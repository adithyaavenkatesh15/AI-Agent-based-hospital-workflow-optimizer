# app/services/disruption_handler.py
"""
Disruption Handler — two main workflows:

A. RESOURCE UNAVAILABILITY (machine fault, room closed, etc.)
   When a unit is marked unavailable:
   1. Find all SCHEDULED/IN_PROGRESS appointments on that unit during the window.
   2. Try to reassign each to an alternative free unit of the same type.
   3. If no alternative exists → mark appointment as "rescheduled" (bumped to next
      available slot) and set reschedule_reason.
   4. Notify affected patients via PatientNotification.
   5. Add a staff-level system notification.

B. EMERGENCY PRIORITY BUMP
   When a new high-priority patient needs a resource that is fully occupied:
   1. Find the LOWEST-priority in_progress patient in that resource.
   2. If new patient has strictly higher priority → re-queue the low-priority one.
   3. Assign the freed slot to the emergency patient.
   4. Notify bumped patient + emergency patient.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AppointmentRecord, PatientRecord, ResourceUnavailability, PatientNotification
from app.config import settings

STATUS_SCHEDULED   = "scheduled"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE        = "done"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()


async def _get_patient(db: AsyncSession, patient_id: str) -> Optional[PatientRecord]:
    r = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == patient_id))
    return r.scalar_one_or_none()


async def _add_patient_notification(
    db: AsyncSession,
    patient_id: str,
    notif_type: str,
    title: str,
    message: str,
) -> None:
    """Store a per-patient notification visible in their portal."""
    db.add(PatientNotification(
        patient_id=patient_id,
        notif_type=notif_type,
        title=title,
        message=message,
    ))


async def _find_free_unit(
    db: AsyncSession,
    resource_type: str,
    exclude_unit_id: str,
    from_dt: datetime,
    until_dt: datetime,
) -> Optional[Dict]:
    """
    Find an alternative unit of resource_type that is:
    - not the excluded unit
    - not marked unavailable during from_dt → until_dt
    - not already at capacity during that window
    Returns unit dict from settings or None.
    """
    pool = settings.resource_pool.get(resource_type, {})
    cap  = pool.get("capacity_per_unit", 1)

    # Get units currently marked unavailable
    unav_res = await db.execute(
        select(ResourceUnavailability).where(
            ResourceUnavailability.resource_type == resource_type,
            ResourceUnavailability.active == 1,
            ResourceUnavailability.unavailable_from < until_dt,
            ResourceUnavailability.unavailable_until > from_dt,
        )
    )
    unavail_unit_ids = {u.unit_id for u in unav_res.scalars().all() if u.unit_id}

    # Count in-progress load per unit during that window
    busy_res = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.appointment_type == resource_type,
            AppointmentRecord.status == STATUS_IN_PROGRESS,
            AppointmentRecord.scheduled_time < until_dt,
        )
    )
    unit_load: Dict[str, int] = {}
    for a in busy_res.scalars().all():
        uid = (a.assigned_resource or {}).get("unit_id", "")
        if uid:
            unit_load[uid] = unit_load.get(uid, 0) + 1

    for unit in pool.get("units_info", []):
        uid = unit["id"]
        if uid == exclude_unit_id:
            continue
        if uid in unavail_unit_ids:
            continue
        if unit_load.get(uid, 0) < cap:
            return unit
    return None


# ── A. Resource Unavailability Handler ────────────────────────────────────────

async def handle_resource_unavailability(
    db: AsyncSession,
    resource_type: str,
    unit_id: Optional[str],       # config ID e.g. "ANGIO-002"
    unavailable_from: datetime,
    unavailable_until: datetime,
    reason: str,
    marked_by: str,
    unit_name: Optional[str] = None,  # display name e.g. "Cath Lab 2"
) -> Dict:
    """
    Called immediately after a ResourceUnavailability record is saved.
    Finds all affected appointments and reassigns / reschedules them.
    """
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)

    # Find affected appointments: scheduled OR in_progress on this unit during the window
    q = select(AppointmentRecord).where(
        AppointmentRecord.appointment_type == resource_type,
        AppointmentRecord.status.in_([STATUS_SCHEDULED, STATUS_IN_PROGRESS]),
        AppointmentRecord.scheduled_time >= today_start,
        AppointmentRecord.scheduled_time < today_end,
    )
    result = await db.execute(q)
    all_appts = result.scalars().all()

    pool_dur = settings.resource_pool.get(resource_type, {}).get("duration_minutes", 20)
    affected = []
    for a in all_appts:
        res = a.assigned_resource or {}
        appt_unit_id   = res.get("unit_id", "")
        appt_unit_name = res.get("unit_name", "")
        # Match: specific unit (by ID or name) OR all units (unit_id is None/empty)
        if unit_id:
            # Accept match by config ID or by display name
            unit_name_for_match = unit_name or ""
            if appt_unit_id != unit_id and appt_unit_name != unit_name_for_match and appt_unit_id != unit_name_for_match:
                continue
        # All scheduled/in_progress appointments on this unit are affected
        # (time overlap check: appointment runs within unavailability window)
        # Note: DB scheduled_time = registration time for pending tests; we treat ALL
        # pending (STATUS_SCHEDULED) appointments as potentially overlapping, since
        # sequential computed times may place them inside the window.
        if a.status == STATUS_SCHEDULED or (
            a.status == STATUS_IN_PROGRESS and a.scheduled_time < unavailable_until
        ):
            affected.append(a)

    reassigned = []
    rescheduled = []

    for appt in affected:
        patient = await _get_patient(db, appt.patient_id)
        patient_name = patient.name if patient else appt.patient_id
        old_unit_name = (appt.assigned_resource or {}).get("unit_name", unit_id or resource_type)

        # Try to find an alternative free unit
        appt_uid = (appt.assigned_resource or {}).get("unit_id", "") or unit_id or ""
        alt_unit = await _find_free_unit(
            db, resource_type,
            exclude_unit_id=appt_uid,
            from_dt=appt.scheduled_time,
            until_dt=appt.scheduled_time + timedelta(minutes=settings.resource_pool.get(resource_type, {}).get("duration_minutes", 20)),
        )

        prev = dict(appt.assigned_resource or {})
        prev["original_unit_id"]   = prev.get("unit_id", "")
        prev["original_unit_name"] = prev.get("unit_name", "")
        prev["rescheduled_due_to"] = reason

        if alt_unit:
            # Reassign to alternative unit — keep same time slot
            appt.assigned_resource = {
                **prev,
                "unit_id":   alt_unit["id"],
                "unit_name": alt_unit["name"],
                "specialty": alt_unit.get("specialty", ""),
                "reassigned_from": old_unit_name,
                "reassigned_reason": reason,
                "reassigned_at": _now().isoformat(),
            }
            appt.rescheduled_from  = appt.scheduled_time
            appt.reschedule_reason = f"Unit unavailable ({reason}) → moved to {alt_unit['name']}"
            appt.updated_at        = _now()

            await _add_patient_notification(
                db, appt.patient_id,
                notif_type="rescheduled",
                title=f"📍 Room Change: {resource_type.replace('_',' ').title()}",
                message=(
                    f"Your {resource_type.replace('_',' ')} appointment has been moved from "
                    f"{old_unit_name} to {alt_unit['name']} due to: {reason}. "
                    f"Your time slot remains the same."
                ),
            )
            reassigned.append({
                "appointment_id": appt.id,
                "patient_id":     appt.patient_id,
                "patient_name":   patient_name,
                "old_unit":       old_unit_name,
                "new_unit":       alt_unit["name"],
                "time_unchanged": True,
            })

        else:
            # No free alternative — mark as needing reschedule, keep scheduled status
            # Find the next available slot after unavailability ends
            pool = settings.resource_pool.get(resource_type, {})
            dur  = pool.get("duration_minutes", 20)
            new_time = unavailable_until  # earliest possible slot after window

            appt.rescheduled_from  = appt.scheduled_time
            appt.reschedule_reason = f"Unit unavailable ({reason}) — rescheduled to after {unavailable_until.strftime('%I:%M %p')}"
            appt.scheduled_time    = new_time
            appt.assigned_resource = {
                **prev,
                "unit_id":   "",
                "unit_name": f"Pending reassignment (after {unavailable_until.strftime('%I:%M %p')})",
                "rescheduled_reason": reason,
            }
            appt.updated_at = _now()

            await _add_patient_notification(
                db, appt.patient_id,
                notif_type="warning",
                title=f"⚠️ Appointment Delayed: {resource_type.replace('_',' ').title()}",
                message=(
                    f"Your {resource_type.replace('_',' ')} at {old_unit_name} has been delayed "
                    f"due to: {reason}. No alternative unit is currently available. "
                    f"Earliest new slot: after {unavailable_until.strftime('%I:%M %p')}. "
                    f"Staff will update you shortly."
                ),
            )
            rescheduled.append({
                "appointment_id": appt.id,
                "patient_id":     appt.patient_id,
                "patient_name":   patient_name,
                "old_unit":       old_unit_name,
                "new_time":       new_time.strftime("%I:%M %p"),
                "reason":         "no_alternative_unit",
            })

    await db.commit()

    # Staff-level notification
    try:
        from app.routers.notifications import add_notification_to_store
        total = len(reassigned) + len(rescheduled)
        add_notification_to_store(
            notif_type="system",
            title=f"🔄 Auto-Rescheduled: {resource_type.replace('_',' ').title()} Disruption",
            message=(
                f"{total} appointment(s) affected by {unit_id or 'unit'} unavailability. "
                f"{len(reassigned)} reassigned to alternative units. "
                f"{len(rescheduled)} delayed (no alternative available). "
                f"Reason: {reason}. All patients notified."
            ),
            priority="warning",
        )
    except Exception:
        pass

    return {
        "resource_type":    resource_type,
        "unit_id":          unit_id,
        "affected_count":   len(affected),
        "reassigned":       reassigned,
        "rescheduled":      rescheduled,
        "patients_notified": [a["patient_id"] for a in reassigned + rescheduled],
    }


# ── B. Emergency Priority Bump ────────────────────────────────────────────────

async def handle_priority_bump(
    db: AsyncSession,
    new_patient_id: str,
    resource_type: str,
) -> Dict:
    """
    A high-priority patient needs `resource_type` but it's full.
    Find the lowest-priority currently in_progress patient.
    If new patient has strictly higher priority → bump them out, route new patient in.
    """
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)

    new_patient = await _get_patient(db, new_patient_id)
    if not new_patient:
        return {"bumped": False, "reason": "new_patient_not_found"}

    # Find the new patient's pending appointment for this resource
    np_res = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.patient_id == new_patient_id,
            AppointmentRecord.appointment_type == resource_type,
            AppointmentRecord.status == STATUS_SCHEDULED,
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
        )
    )
    new_appt = np_res.scalar_one_or_none()
    if not new_appt:
        return {"bumped": False, "reason": "no_pending_appointment_for_new_patient"}

    # Find current in_progress patients for this resource, sorted lowest priority first
    ip_res = await db.execute(
        select(AppointmentRecord, PatientRecord)
        .join(PatientRecord, AppointmentRecord.patient_id == PatientRecord.patient_id)
        .where(
            AppointmentRecord.appointment_type == resource_type,
            AppointmentRecord.status == STATUS_IN_PROGRESS,
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
        )
        .order_by(
            PatientRecord.priority.desc(),       # highest number = lowest priority
            PatientRecord.severity_score.asc(),  # lowest severity
        )
    )
    candidates = ip_res.all()
    if not candidates:
        return {"bumped": False, "reason": "resource_not_full"}

    bump_appt, bump_patient = candidates[0]

    # Only bump if new patient has strictly HIGHER priority (lower number)
    if new_patient.priority >= bump_patient.priority:
        return {
            "bumped": False,
            "reason": "new_patient_not_higher_priority",
            "new_priority": new_patient.priority,
            "lowest_current_priority": bump_patient.priority,
        }

    # Get the unit being freed
    freed_unit_id   = (bump_appt.assigned_resource or {}).get("unit_id", "")
    freed_unit_name = (bump_appt.assigned_resource or {}).get("unit_name", resource_type)

    # Re-queue the bumped patient — back to scheduled
    prev_bump = dict(bump_appt.assigned_resource or {})
    bump_appt.status = STATUS_SCHEDULED
    bump_appt.assigned_resource = {
        **prev_bump,
        "unit_id":   "",
        "unit_name": "Re-queued — displaced by higher-priority patient",
        "bumped":    True,
        "bumped_at": _now().isoformat(),
    }
    bump_appt.rescheduled_from  = bump_appt.scheduled_time
    bump_appt.reschedule_reason = f"Displaced by emergency patient {new_patient_id} (Priority {new_patient.priority})"
    bump_appt.updated_at        = _now()

    # Assign the new patient to the freed slot
    pool = settings.resource_pool.get(resource_type, {})
    unit_info = next(
        (u for u in pool.get("units_info", []) if u["id"] == freed_unit_id),
        {"id": freed_unit_id, "name": freed_unit_name}
    )
    new_appt.status = STATUS_IN_PROGRESS
    new_appt.assigned_resource = {
        "type":        resource_type,
        "unit_id":     freed_unit_id,
        "unit_name":   freed_unit_name,
        "specialty":   unit_info.get("specialty", ""),
        "started_at":  _now().isoformat(),
        "priority_bumped": True,
    }
    new_appt.scheduled_time = _now()
    new_appt.updated_at     = _now()

    # Notify bumped patient
    await _add_patient_notification(
        db, bump_patient.patient_id,
        notif_type="bumped",
        title="⚠️ Your Appointment Has Been Re-queued",
        message=(
            f"Your {resource_type.replace('_',' ')} at {freed_unit_name} has been temporarily "
            f"re-queued to accommodate a higher-priority emergency patient. "
            f"You will be called again as soon as a unit is free. We apologise for the inconvenience."
        ),
    )

    # Notify new (emergency) patient
    await _add_patient_notification(
        db, new_patient_id,
        notif_type="info",
        title=f"✅ {resource_type.replace('_',' ').title()} Ready",
        message=(
            f"You have been assigned to {freed_unit_name} for your "
            f"{resource_type.replace('_',' ')} immediately due to your emergency priority."
        ),
    )

    await db.commit()

    # Staff notification
    try:
        from app.routers.notifications import add_notification_to_store
        plabel = {1: "Emergency", 2: "Urgent", 3: "Routine"}
        add_notification_to_store(
            notif_type="emergency",
            title=f"🚨 Priority Bump: {resource_type.replace('_',' ').title()}",
            message=(
                f"{new_patient.name} ({plabel.get(new_patient.priority,'?')} P{new_patient.priority}) "
                f"assigned to {freed_unit_name}. "
                f"{bump_patient.name} ({plabel.get(bump_patient.priority,'?')} P{bump_patient.priority}) "
                f"re-queued. All patients notified."
            ),
            priority="critical",
        )
    except Exception:
        pass

    return {
        "bumped":            True,
        "resource_type":     resource_type,
        "freed_unit":        freed_unit_name,
        "bumped_patient": {
            "patient_id":   bump_patient.patient_id,
            "patient_name": bump_patient.name,
            "priority":     bump_patient.priority,
        },
        "assigned_patient": {
            "patient_id":   new_patient_id,
            "patient_name": new_patient.name,
            "priority":     new_patient.priority,
        },
    }