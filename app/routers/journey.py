# app/routers/journey.py
"""
Patient Journey Router
  POST /journey/complete/{appointment_id}  — mark done, auto-route next
  POST /journey/prescribe/{patient_id}     — doctor prescribes tests by patient ID
  POST /journey/emergency-bump             — bump non-emergency for ICU patient
  GET  /journey/queue                      — live queue per resource type
  POST /journey/advance-queue              — manually trigger auto-advance
  GET  /journey/patient/{patient_id}       — full journey for one patient
  POST /journey/assign-time/{id}           — manually assign time slot
  POST /journey/tick                       — 2-min auto-tick
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime, timedelta

from app.database import get_db, AppointmentRecord, PatientRecord
from app.services.patient_journey_service import (
    PatientJourneyAgent,
    STATUS_SCHEDULED, STATUS_IN_PROGRESS, STATUS_DONE,
)

router = APIRouter(prefix="/journey", tags=["Patient Journey"])


# ── Complete an appointment ───────────────────────────────────────────────────
@router.post("/complete/{appointment_id}")
async def complete_appointment(
    appointment_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Mark an appointment done. ICU bed appointments are BLOCKED here.
    Use POST /journey/discharge/{patient_id} to discharge from ICU.
    """
    try:
        from sqlalchemy import select as _sel
        chk = await db.execute(_sel(AppointmentRecord).where(AppointmentRecord.id == appointment_id))
        appt = chk.scalar_one_or_none()
        if appt and appt.appointment_type == "icu":
            raise HTTPException(
                status_code=400,
                detail="ICU admission cannot be completed here. Use /journey/discharge/{patient_id}."
            )
        result = await PatientJourneyAgent.complete_appointment(appointment_id, db)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




# ── Doctor discharges patient from ICU ───────────────────────────────────────
@router.post("/discharge/{patient_id}")
async def discharge_from_icu(
    patient_id: str,
    discharged_by: str = "doctor",
    db: AsyncSession = Depends(get_db)
):
    """
    ONLY way to complete an ICU stay. Must be called explicitly by a doctor.
    - Marks the ICU appointment as done
    - Updates patient location to 'discharged'
    - Frees the ICU bed for the next patient
    """
    try:
        from sqlalchemy import select as _sel
        from app.database import PatientRecord
        # Verify patient is actually in ICU
        p_res = await db.execute(_sel(PatientRecord).where(PatientRecord.patient_id == patient_id))
        patient = p_res.scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        if not getattr(patient, 'is_icu', 0):
            raise HTTPException(status_code=400, detail=f"Patient {patient_id} is not currently in ICU")

        # Find their active ICU appointment
        start = __import__('datetime').datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + __import__('datetime').timedelta(days=1)
        icu_res = await db.execute(
            _sel(AppointmentRecord).where(
                AppointmentRecord.patient_id == patient_id,
                AppointmentRecord.appointment_type == "icu",
                AppointmentRecord.status == "in_progress",
                AppointmentRecord.scheduled_time >= start,
                AppointmentRecord.scheduled_time < end,
            )
        )
        icu_appt = icu_res.scalar_one_or_none()
        if not icu_appt:
            raise HTTPException(status_code=404, detail="No active ICU appointment found for this patient")

        # Mark ICU appointment done
        from app.services.patient_journey_service import _mark_done, PatientJourneyAgent
        _mark_done(icu_appt)
        icu_appt.assigned_resource = {
            **(icu_appt.assigned_resource or {}),
            "discharged_by": discharged_by,
            "discharged_at": __import__('datetime').datetime.now().isoformat(),
        }

        # Update patient status
        patient.location = "discharged"
        patient.is_icu = 0
        patient.icu_bed_id = ""
        await db.commit()

        # Advance ICU queue — give bed to next waiting emergency patient
        freed = await PatientJourneyAgent._advance_resource_queue("icu", db, exclude_patient=patient_id)

        return {
            "success": True,
            "patient_id": patient_id,
            "patient_name": patient.name,
            "discharged_by": discharged_by,
            "icu_bed_freed": (icu_appt.assigned_resource or {}).get("unit_name", ""),
            "next_icu_patient": freed,
            "message": f"Patient {patient.name} discharged from ICU by {discharged_by}. Bed is now free."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ICU discharge error: {str(e)}")

# ── Doctor prescribes tests by patient ID ────────────────────────────────────
@router.post("/prescribe/{patient_id}")
async def prescribe_tests_for_patient(
    patient_id: str,
    tests: List[str],
    prescribed_by: str = "doctor",
    db: AsyncSession = Depends(get_db)
):
    """
    Doctor selects patient by ID and prescribes tests.
    Works for both ICU and post-consultation patients.
    Allocation: Hungarian Algorithm weighted by severity score.
    """
    try:
        result = await PatientJourneyAgent.prescribe_tests(
            patient_id=patient_id,
            tests=tests,
            prescribed_by=prescribed_by,
            db=db,
        )
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])

        # Notification
        try:
            from app.routers.notifications import add_notification_to_store
            add_notification_to_store(
                notif_type="patient",
                title=f"Tests Prescribed — {result.get('patient_name', patient_id)}",
                message=(
                    f"{prescribed_by.replace('_',' ').title()} prescribed "
                    f"{len(tests)} test(s): {', '.join(tests)} "
                    f"(Severity {result.get('severity_score', '?')}/10)"
                ),
                priority="critical" if result.get("severity_score", 0) >= 8 else "normal",
            )
        except Exception:
            pass

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Emergency bump ────────────────────────────────────────────────────────────
@router.post("/emergency-bump")
async def emergency_bump(
    patient_id: str,
    required_tests: List[str],
    db: AsyncSession = Depends(get_db)
):
    try:
        result = await PatientJourneyAgent.handle_emergency_bump(
            new_patient_id=patient_id,
            required_tests=required_tests,
            db=db
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Priority bump (auto-triggered when resource full + higher-priority arrives) ──
@router.post("/priority-bump")
async def priority_bump(
    patient_id: str,
    resource_type: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Attempt to displace the lowest-priority in_progress patient from resource_type
    and assign it to patient_id (only if patient_id has strictly higher priority).
    """
    try:
        from app.services.disruption_handler import handle_priority_bump
        result = await handle_priority_bump(db=db, new_patient_id=patient_id, resource_type=resource_type)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Live queue ────────────────────────────────────────────────────────────────
@router.get("/queue")
async def get_queue(db: AsyncSession = Depends(get_db)):
    try:
        return await PatientJourneyAgent.get_queue_status(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Auto-advance queue ────────────────────────────────────────────────────────
@router.post("/advance-queue")
async def advance_queue(db: AsyncSession = Depends(get_db)):
    try:
        return await PatientJourneyAgent.auto_advance_queue(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Auto-allocate slot using available resources (Hungarian Algorithm) ────────
@router.post("/assign-time/{appointment_id}")
async def assign_appointment_time(
    appointment_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Auto-allocates the best available unit for this appointment using the
    Hungarian Algorithm — no manual time input needed.
    Finds the least-loaded free unit, sets scheduled_time = now + wait, marks in_progress.
    """
    try:
        res  = await db.execute(
            select(AppointmentRecord).where(AppointmentRecord.id == appointment_id)
        )
        appt = res.scalar_one_or_none()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        if appt.status == STATUS_DONE:
            raise HTTPException(status_code=400, detail="Appointment is already completed")

        res_type = appt.appointment_type

        from app.services.patient_journey_service import _unit_load, _find_best_unit
        from app.config import settings

        unit_load      = await _unit_load(db, res_type)
        unit, load     = _find_best_unit(res_type, unit_load)
        pool           = settings.resource_pool.get(res_type, {})
        duration       = pool.get("duration_minutes", 20)
        est_wait       = load * duration
        scheduled_time = datetime.now() + timedelta(minutes=est_wait)

        if unit is None:
            raise HTTPException(
                status_code=409,
                detail=f"All units for '{res_type}' are currently busy. Patient will be queued automatically."
            )

        appt.status         = STATUS_IN_PROGRESS
        appt.scheduled_time = scheduled_time
        prev                = dict(appt.assigned_resource or {})
        appt.assigned_resource = {
            **prev,
            "unit_id":                unit["id"],
            "unit_name":              unit["name"],
            "specialty":              unit.get("specialty", ""),
            "estimated_wait_minutes": est_wait,
            "started_at":             datetime.now().isoformat(),
            "auto_allocated":         True,
        }
        appt.updated_at = datetime.now()
        await db.commit()

        p_res   = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == appt.patient_id))
        patient = p_res.scalar_one_or_none()

        wait_str = f"~{est_wait} min wait" if est_wait > 0 else "immediate"
        return {
            "success":          True,
            "appointment_id":   appointment_id,
            "patient_id":       appt.patient_id,
            "patient_name":     patient.name if patient else appt.patient_id,
            "appointment_type": res_type,
            "assigned_unit":    unit["name"],
            "estimated_wait":   est_wait,
            "scheduled_time":   scheduled_time.isoformat(),
            "message":          f"Auto-allocated to {unit['name']} ({wait_str}) for {res_type.replace('_',' ').title()}",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 2-minute auto-tick ────────────────────────────────────────────────────────
@router.post("/tick")
async def journey_tick(db: AsyncSession = Depends(get_db)):
    """
    Auto-completes expired in_progress appointments, frees rooms,
    routes patients to next step, fills freed rooms from priority queue.
    """
    try:
        return await PatientJourneyAgent.auto_tick(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Full journey status for one patient ──────────────────────────────────────
@router.get("/patient/{patient_id}")
async def get_patient_journey(
    patient_id: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end   = today_start + timedelta(days=1)

        p_result = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        patient = p_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        a_result = await db.execute(
            select(AppointmentRecord).where(
                AppointmentRecord.patient_id == patient_id,
                AppointmentRecord.scheduled_time >= today_start,
                AppointmentRecord.scheduled_time < today_end,
            ).order_by(
                AppointmentRecord.sequence_order.asc().nullslast(),
                AppointmentRecord.id.asc()
            )
        )
        appts = a_result.scalars().all()

        # Build sequential start/end times (same logic as patient portal)
        from app.config import settings as _cfg
        _non_icu_j = [a for a in appts if not _cfg.resource_pool.get(a.appointment_type, {}).get("is_icu")]
        _anchor_j = _non_icu_j[0].scheduled_time if _non_icu_j else datetime.now()
        _cursor_j = None
        _seq_times = {}  # appt.id -> (start_dt, end_dt)
        for _a in appts:
            _pool = _cfg.resource_pool.get(_a.appointment_type, {})
            _dur  = _pool.get("duration_minutes", 20)
            if _pool.get("is_icu"):
                _seq_times[_a.id] = (_a.scheduled_time, None)
            else:
                _st = _cursor_j if _cursor_j is not None else (_anchor_j or datetime.now())
                _et = _st + timedelta(minutes=_dur)
                _seq_times[_a.id] = (_st, _et)
                _cursor_j = _et

        def fmt(a: AppointmentRecord):
            res = a.assigned_resource or {}
            _pool = _cfg.resource_pool.get(a.appointment_type, {})
            _dur  = _pool.get("duration_minutes", 20)
            _is_icu = _pool.get("is_icu", False)
            st, et = _seq_times.get(a.id, (a.scheduled_time, None))
            return {
                "appointment_id":   a.id,
                "type":             a.appointment_type,
                "status":           a.status,
                "unit":             res.get("unit_name", ""),
                "scheduled_time":   st.isoformat() if st else None,
                "end_time":         et.isoformat() if et else None,
                "start_display":    st.strftime("%I:%M %p") if st else "TBD",
                "end_display":      et.strftime("%I:%M %p") if et else ("Ongoing" if _is_icu else "TBD"),
                "duration_minutes": _dur,
                "estimated_wait":   res.get("estimated_wait_minutes", 0),
                "prescribed_by":    getattr(a, "prescribed_by", None),
                "sequence_order":   getattr(a, "sequence_order", None),
            }

        done        = [fmt(a) for a in appts if a.status == STATUS_DONE]
        in_progress = [fmt(a) for a in appts if a.status == STATUS_IN_PROGRESS]
        waiting     = [fmt(a) for a in appts if a.status == STATUS_SCHEDULED]

        if in_progress:
            stage = f"In {in_progress[0]['type'].replace('_',' ').title()}"
        elif waiting:
            stage = f"Waiting for {waiting[0]['type'].replace('_',' ').title()}"
        elif done:
            stage = "journey_complete"
        else:
            stage = "no_appointments"

        location_labels = {
            "icu":          "🚨 ICU",
            "consultation": "🩺 Consultation",
            "tests":        "🔬 Tests",
            "waiting":      "⏳ Waiting",
            "discharged":   "✅ Discharged",
        }
        plabel = {1: "Emergency", 2: "Urgent", 3: "Routine"}

        return {
            "patient_id":        patient_id,
            "patient_name":      patient.name,
            "priority":          patient.priority,
            "priority_label":    plabel.get(patient.priority, "Routine"),
            "severity_score":    patient.severity_score,
            "location":          patient.location or "waiting",
            "location_label":    location_labels.get(patient.location or "waiting", patient.location),
            "current_stage":     stage,
            "done":              done,
            "in_progress":       in_progress,
            "waiting":           waiting,
            "total_appointments": len(appts),
            "completed_count":   len(done),
            "remaining_count":   len(waiting) + len(in_progress),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))