# app/services/patient_journey_service.py
"""
Patient Journey Agent — ICU-First for Emergencies, Consultation-First for Others

WORKFLOW RULES:
  R0. TRIAGE:
      Emergency (priority=1)  → ICU immediately (bypass consultation)
      Non-emergency            → Consultation first, then tests

  R1. ICU FLOW (Emergency):
      Patient admitted to ICU bed (based on severity + availability).
      ICU doctors prescribe tests via /journey/prescribe/{patient_id}.
      Tests run SEQUENTIALLY: one at a time per patient.

  R2. CONSULTATION FIRST (non-emergency):
      Every non-emergency starts with consultation ONLY.
      Tests UNLOCKED only after consultation is marked done.

  R3. SEQUENTIAL TEST EXECUTION (all patients):
      Multiple tests run ONE AT A TIME per patient.
      After test N completes → system automatically starts test N+1.
      While patient waits, the freed resource goes to the next waiting patient.

  R4. PRIORITY QUEUE:
      When a room frees → highest-priority waiting patient gets it.
      Order: Emergency(1) > Urgent(2) > Routine(3), then severity_score desc.

  R5. EMERGENCY BUMP:
      New emergency can displace the lowest-priority non-emergency from a test room.

AUTO-PROGRESSION (background task every 2 minutes):
  - Any in_progress appointment older than AUTO_COMPLETE_AFTER_MINUTES → auto-complete
  - Freed room → next waiting patient routed in
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AppointmentRecord, PatientRecord
from app.config import settings

import numpy as np

STATUS_SCHEDULED   = "scheduled"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE        = "done"
STATUS_CANCELLED   = "cancelled"
STATUS_MONITORING  = "monitoring"   # ICU patients awaiting doctor instructions

# Simulated appointment duration for auto-complete (2 minutes demo speed)
AUTO_COMPLETE_AFTER_MINUTES = 2
# Buffer between consecutive tests (travel time between rooms)
TEST_BUFFER_MINUTES = 5
# Severity threshold above which patients go directly to ICU
ICU_SEVERITY_THRESHOLD = 6


def _today_bounds() -> Tuple[datetime, datetime]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def _unit_load(db: AsyncSession, resource_type: str) -> Dict[str, int]:
    """Count of in_progress patients per unit_id for resource_type today."""
    start, end = _today_bounds()
    rows = await db.execute(
        select(AppointmentRecord.assigned_resource).where(
            AppointmentRecord.appointment_type == resource_type,
            AppointmentRecord.scheduled_time >= start,
            AppointmentRecord.scheduled_time < end,
            AppointmentRecord.status == STATUS_IN_PROGRESS,
        )
    )
    load: Dict[str, int] = {}
    for row in rows.all():
        uid = (row.assigned_resource or {}).get("unit_id", "")
        if uid:
            load[uid] = load.get(uid, 0) + 1
    return load


async def _patient_active(db: AsyncSession, patient_id: str) -> List[str]:
    """Appointment types currently IN_PROGRESS for this patient today (excluding ICU).
    ICU is a permanent room — it must NOT block sequential test routing."""
    start, end = _today_bounds()
    rows = await db.execute(
        select(AppointmentRecord.appointment_type).where(
            AppointmentRecord.patient_id == patient_id,
            AppointmentRecord.status == STATUS_IN_PROGRESS,
            AppointmentRecord.appointment_type != "icu",
            AppointmentRecord.scheduled_time >= start,
            AppointmentRecord.scheduled_time < end,
        )
    )
    return [r.appointment_type for r in rows.all()]


async def _patient_all_today(db: AsyncSession, patient_id: str) -> List[AppointmentRecord]:
    """Today's appointments ordered by sequence_order then id."""
    start, end = _today_bounds()
    rows = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.patient_id == patient_id,
            AppointmentRecord.scheduled_time >= start,
            AppointmentRecord.scheduled_time < end,
        ).order_by(
            AppointmentRecord.sequence_order.asc().nullslast(),
            AppointmentRecord.id.asc()
        )
    )
    return rows.scalars().all()


def _find_best_unit(resource_type: str, unit_load: Dict[str, int]) -> Tuple[Optional[Dict], int]:
    """Least-loaded unit with free capacity, or (None, 0)."""
    pool = settings.resource_pool.get(resource_type, {})
    cap  = pool.get("capacity_per_unit", 1)
    best, best_load = None, float("inf")
    for unit in pool.get("units_info", []):
        load = unit_load.get(unit["id"], 0)
        if load < cap and load < best_load:
            best_load = load
            best      = unit
    return (best, int(best_load)) if best else (None, 0)


def _mark_done(appt: AppointmentRecord) -> None:
    """Mark appointment done and clear unit_id so room shows FREE immediately."""
    appt.status = STATUS_DONE
    prev = dict(appt.assigned_resource or {})
    appt.assigned_resource = {
        **prev,
        "unit_id":      "",
        "completed_at": datetime.now().isoformat(),
    }
    appt.updated_at = datetime.now()


def _compute_slot(start_time: datetime, resource_type: str) -> tuple:
    """
    Returns (start_time, end_time, duration_minutes) for a test appointment.
    end_time = start_time + test_duration + TEST_BUFFER_MINUTES (travel buffer).
    ICU has no end_time (indefinite stay).
    """
    pool = settings.resource_pool.get(resource_type, {})
    duration = pool.get("duration_minutes", 20)
    if pool.get("is_icu"):
        return start_time, None, duration
    end_time = start_time + timedelta(minutes=duration + TEST_BUFFER_MINUTES)
    return start_time, end_time, duration


def _hungarian_assign_tests(
    pending_appts: List[AppointmentRecord],
    unit_loads: Dict[str, Dict[str, int]],
) -> List[Tuple[AppointmentRecord, Optional[Dict], int]]:
    """
    Use Hungarian Algorithm to optimally assign a list of pending test
    appointments to available units, minimising total estimated wait time.
    Returns list of (appointment, best_unit_or_None, est_wait_minutes).
    """
    if not pending_appts:
        return []

    all_units = []
    for appt in pending_appts:
        res_type = appt.appointment_type
        pool     = settings.resource_pool.get(res_type, {})
        cap      = pool.get("capacity_per_unit", 1)
        duration = pool.get("duration_minutes", 20)
        for unit in pool.get("units_info", []):
            load = unit_loads.get(res_type, {}).get(unit["id"], 0)
            if load < cap:
                all_units.append({
                    "resource_type": res_type,
                    "unit":          unit,
                    "load":          load,
                    "duration":      duration,
                    "est_wait":      load * duration,
                })

    if not all_units:
        return [(a, None, 0) for a in pending_appts]

    n_patients = len(pending_appts)
    n_slots    = len(all_units)
    INF        = 1e9

    cost_matrix = np.full((n_patients, n_slots), INF)
    for i, appt in enumerate(pending_appts):
        res_type = appt.appointment_type
        for j, slot in enumerate(all_units):
            if slot["resource_type"] == res_type:
                cost_matrix[i, j] = float(slot["est_wait"])

    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
    except Exception:
        row_ind = list(range(n_patients))
        col_ind = [int(np.argmin(cost_matrix[i])) for i in row_ind]

    results       = []
    assigned_cols = set()

    for i, appt in enumerate(pending_appts):
        matched = [(r, c) for r, c in zip(row_ind, col_ind) if r == i]
        if matched and matched[0][1] not in assigned_cols:
            _, c = matched[0]
            slot = all_units[c]
            if cost_matrix[i, c] < INF:
                assigned_cols.add(c)
                results.append((appt, slot["unit"], int(slot["est_wait"])))
                slot["load"]    += 1
                slot["est_wait"] = slot["load"] * slot["duration"]
                cost_matrix[:, c] = slot["est_wait"]
                continue
        results.append((appt, None, 0))

    return results


class PatientJourneyAgent:

    # ──────────────────────────────────────────────────────────────────────────
    # 1. REGISTER: setup journey for a newly registered patient
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def register_patient_journey(
        patient_id: str,
        required_tests: List[str],
        priority: int,
        severity_score: int,
        db: AsyncSession,
    ) -> Dict:
        """
        Entry point: set up the journey for a newly registered patient.

        Emergency (priority=1) OR severity_score > ICU_SEVERITY_THRESHOLD:
          - Allocated directly to an ICU bed based on severity score.
          - Status = "monitoring" — no "pending" in ICU.
          - No consultation initially; ICU doctors prescribe tests later.

        Non-emergency + severity ≤ threshold:
          - Consultation first (locked tests unlock after consult done).
          - Tests run SEQUENTIALLY one at a time.
        """
        is_icu_patient = (priority == 1) or (severity_score > ICU_SEVERITY_THRESHOLD)

        if is_icu_patient:
            return await PatientJourneyAgent._register_icu_patient(
                patient_id, required_tests, severity_score, db
            )
        else:
            return await PatientJourneyAgent._register_consultation_patient(
                patient_id, required_tests, priority, severity_score, db
            )

    @staticmethod
    async def _register_icu_patient(
        patient_id: str,
        required_tests: List[str],
        severity_score: int,
        db: AsyncSession,
    ) -> Dict:
        """Admit emergency patient to ICU bed. Patient stays until explicit doctor discharge."""
        icu_load         = await _unit_load(db, "icu")
        icu_unit, _      = _find_best_unit("icu", icu_load)

        if icu_unit:
            icu_status   = STATUS_IN_PROGRESS
            icu_resource = {
                "type":        "icu",
                "unit_id":     icu_unit["id"],
                "unit_name":   icu_unit["name"],
                "specialty":   icu_unit.get("specialty", "Cardiac ICU"),
                "admitted_at": datetime.now().isoformat(),
                "started_at":  datetime.now().isoformat(),
                "is_icu":      True,
                # No estimated_wait — ICU has no time limit; discharge is by doctor only
            }
            # Update patient record with ICU bed assignment
            p_res = await db.execute(
                select(PatientRecord).where(PatientRecord.patient_id == patient_id)
            )
            patient = p_res.scalar_one_or_none()
            if patient:
                patient.location   = "icu"
                patient.is_icu     = 1
                patient.icu_bed_id = icu_unit["id"]
        else:
            icu_status   = STATUS_SCHEDULED
            icu_resource = {
                "type":      "icu",
                "unit_id":   "",
                "unit_name": "Waiting for ICU bed",
                "specialty": "Cardiac ICU",
                "is_icu":    True,
                # Patient is waiting for a bed — will be assigned when one is freed by discharge
            }

        icu_appt = AppointmentRecord(
            patient_id=patient_id,
            appointment_type="icu",
            scheduled_time=datetime.now(),
            assigned_resource=icu_resource,
            status=icu_status,
            sequence_order=0,
        )
        db.add(icu_appt)

        # Lock any pre-specified tests (doctor will prescribe formally)
        test_appts_info = []
        clean_tests = [t for t in required_tests if t not in ("consultation", "icu")]
        for seq, t in enumerate(clean_tests, start=1):
            locked_appt = AppointmentRecord(
                patient_id=patient_id,
                appointment_type=t,
                scheduled_time=datetime.now(),
                assigned_resource={
                    "type":    t,
                    "unit_id": "",
                    "unit_name": "Locked — awaiting ICU doctor prescription",
                    "locked":  True,
                    "is_icu":  True,
                },
                status=STATUS_SCHEDULED,
                sequence_order=seq,
            )
            db.add(locked_appt)
            test_appts_info.append({"type": t, "status": "locked", "unit": "awaiting_prescription"})

        await db.commit()

        return {
            "patient_id":     patient_id,
            "is_emergency":   True,
            "icu_status":     icu_status,
            "icu_unit":       icu_unit["name"] if icu_unit else "no_bed_available",
            "tests":          test_appts_info,
            "tests_locked":   True,
            "message":        "Emergency patient admitted to ICU. Tests will be prescribed by ICU doctor.",
        }

    @staticmethod
    async def _register_consultation_patient(
        patient_id: str,
        required_tests: List[str],
        priority: int,
        severity_score: int,
        db: AsyncSession,
    ) -> Dict:
        """Non-emergency: consultation first, then sequential tests."""
        pool_consult     = settings.resource_pool.get("consultation", {})
        duration_consult = pool_consult.get("duration_minutes", 2)

        consult_load         = await _unit_load(db, "consultation")
        consult_unit, c_load = _find_best_unit("consultation", consult_load)

        if consult_unit:
            consult_status   = STATUS_IN_PROGRESS
            consult_resource = {
                "type":                   "consultation",
                "unit_id":                consult_unit["id"],
                "unit_name":              consult_unit["name"],
                "specialty":              consult_unit.get("specialty", ""),
                "estimated_wait_minutes": c_load * duration_consult,
                "started_at":             datetime.now().isoformat(),
            }
        else:
            consult_status   = STATUS_SCHEDULED
            consult_resource = {
                "type":                   "consultation",
                "unit_id":                "",
                "unit_name":              "Waiting for available doctor",
                "specialty":              "",
                "estimated_wait_minutes": 0,
            }

        now_time = datetime.now()
        start_t, end_t, dur = _compute_slot(now_time, "consultation")
        consult_appt = AppointmentRecord(
            patient_id=patient_id,
            appointment_type="consultation",
            scheduled_time=start_t,
            end_time=end_t,
            duration_minutes=dur,
            location_name=consult_unit["name"] if consult_unit else "Waiting",
            assigned_resource=consult_resource,
            status=consult_status,
            sequence_order=0,
        )
        db.add(consult_appt)

        # Lock all tests — sequential execution enforced
        test_types      = [t for t in required_tests if t not in ("consultation", "icu")]
        test_appts_info = []
        for seq, t in enumerate(test_types, start=1):
            locked_appt = AppointmentRecord(
                patient_id=patient_id,
                appointment_type=t,
                scheduled_time=datetime.now(),
                assigned_resource={
                    "type":    t,
                    "unit_id": "",
                    "unit_name": "Locked — awaiting consultation",
                    "locked":  True,
                    "estimated_wait_minutes": 0,
                },
                status=STATUS_SCHEDULED,
                sequence_order=seq,
            )
            db.add(locked_appt)
            test_appts_info.append({"type": t, "status": "locked", "unit": "pending_consultation"})

        await db.commit()

        return {
            "patient_id":          patient_id,
            "is_emergency":        False,
            "consultation_status": consult_status,
            "consultation_unit":   consult_unit["name"] if consult_unit else "queued",
            "tests":               test_appts_info,
            "tests_locked":        True,
            "message":             "Consultation started. Tests locked and will run SEQUENTIALLY after consultation.",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 2. POST-CONSULTATION: unlock + allocate tests SEQUENTIALLY
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def unlock_and_allocate_tests(patient_id: str, db: AsyncSession) -> Dict:
        """
        Called when consultation is marked done for a non-emergency patient.
        1. Load all locked test appointments (ordered by sequence_order).
        2. Run Hungarian Algorithm → assign FIRST test only (sequential enforcement).
        3. Remaining tests stay scheduled until previous completes.
        """
        appts = await _patient_all_today(db, patient_id)
        test_appts = [
            a for a in appts
            if a.appointment_type not in ("consultation", "icu")
            and a.status == STATUS_SCHEDULED
        ]

        if not test_appts:
            return {"patient_id": patient_id, "allocated": 0, "message": "no_pending_tests"}

        needed_types = list({a.appointment_type for a in test_appts})
        unit_loads: Dict[str, Dict[str, int]] = {}
        for t in needed_types:
            unit_loads[t] = await _unit_load(db, t)

        # Only allocate FIRST test immediately — enforce sequential execution
        first_test = test_appts[0]
        assignments = _hungarian_assign_tests([first_test], unit_loads)

        allocated = []
        for idx, (appt, unit, est_wait) in enumerate(assignments):
            # Unlock all tests (remove locked flag)
            prev = dict(appt.assigned_resource or {})
            prev.pop("locked", None)
            if unit:
                appt.assigned_resource = {
                    **prev,
                    "type":                    appt.appointment_type,
                    "unit_id":                 unit["id"],
                    "unit_name":               unit["name"],
                    "specialty":               unit.get("specialty", ""),
                    "estimated_wait_minutes":  est_wait,
                    "allocated_after_consult": True,
                    "started_at":              datetime.now().isoformat(),
                }
                appt.status        = STATUS_IN_PROGRESS
                appt.scheduled_time = datetime.now()
            else:
                appt.assigned_resource = {
                    **prev,
                    "type":                    appt.appointment_type,
                    "unit_id":                 "",
                    "unit_name":               "Waiting for available unit",
                    "locked":                  False,
                    "allocated_after_consult": True,
                    "estimated_wait_minutes":  0,
                }
                appt.status = STATUS_SCHEDULED
            appt.updated_at = datetime.now()
            allocated.append({
                "appointment_id": appt.id,
                "type":           appt.appointment_type,
                "unit":           unit["name"] if unit else "queued",
                "status":         appt.status,
                "est_wait":       est_wait,
                "sequence":       0,
            })

        # Unlock remaining tests (remove locked flag, keep scheduled)
        for remaining in test_appts[1:]:
            prev = dict(remaining.assigned_resource or {})
            prev.pop("locked", None)
            remaining.assigned_resource = {
                **prev,
                "locked": False,
                "unit_name": "Waiting — previous test must complete first",
                "allocated_after_consult": True,
            }
            remaining.updated_at = datetime.now()

        await db.commit()

        return {
            "patient_id":   patient_id,
            "allocated":    len([a for a in allocated if a["unit"] != "queued"]),
            "total_tests":  len(test_appts),
            "tests":        allocated,
            "message":      f"Tests unlocked. Sequential execution: first test started, {len(test_appts)-1} remaining queued.",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 3. ROUTE NEXT TEST (sequential enforcement)
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def _route_next_test(patient_id: str, db: AsyncSession) -> Dict:
        """
        Find and activate this patient's next pending test.
        Enforces: one room at a time per patient (sequential).
        """
        appts = await _patient_all_today(db, patient_id)

        # ICU is a permanent room — it must NOT block sequential tests
        active = [a for a in appts if a.status == STATUS_IN_PROGRESS
                  and a.appointment_type != "icu"]
        if active:
            return {
                "routed":      False,
                "reason":      "already_in_room",
                "active_type": active[0].appointment_type,
            }

        pending_tests = [
            a for a in appts
            if a.status == STATUS_SCHEDULED
            and a.appointment_type not in ("consultation", "icu")
            and not (a.assigned_resource or {}).get("locked")
        ]
        if not pending_tests:
            return {"routed": False, "reason": "no_pending_tests"}

        next_appt = pending_tests[0]
        res_type  = next_appt.appointment_type

        pre_uid   = (next_appt.assigned_resource or {}).get("unit_id", "")
        unit_load = await _unit_load(db, res_type)
        pool      = settings.resource_pool.get(res_type, {})
        cap       = pool.get("capacity_per_unit", 1)
        duration  = pool.get("duration_minutes", 20)

        chosen_unit = None
        est_wait    = 0

        if pre_uid and unit_load.get(pre_uid, 0) < cap:
            chosen_unit = next(
                (u for u in pool.get("units_info", []) if u["id"] == pre_uid), None
            )
            est_wait = unit_load.get(pre_uid, 0) * duration
        else:
            assignments = _hungarian_assign_tests([next_appt], {res_type: unit_load})
            if assignments and assignments[0][1] is not None:
                _, chosen_unit, est_wait = assignments[0]
            else:
                # Resource full — attempt priority bump if this patient is high priority
                try:
                    p_rec = await db.execute(
                        select(PatientRecord).where(PatientRecord.patient_id == patient_id)
                    )
                    p_obj = p_rec.scalar_one_or_none()
                    if p_obj and p_obj.priority == 1:  # Only auto-bump for Emergency
                        from app.services.disruption_handler import handle_priority_bump
                        bump_result = await handle_priority_bump(db, patient_id, res_type)
                        if bump_result.get("bumped"):
                            return {
                                "routed":    True,
                                "patient_id": patient_id,
                                "routed_to": res_type,
                                "unit_name": bump_result["freed_unit"],
                                "via":       "priority_bump",
                            }
                except Exception:
                    pass
                return {
                    "routed":      False,
                    "reason":      "resource_full",
                    "waiting_for": res_type,
                    "patient_id":  patient_id,
                }

        if not chosen_unit:
            return {"routed": False, "reason": "resource_full", "waiting_for": res_type}

        next_appt.status         = STATUS_IN_PROGRESS
        next_appt.scheduled_time = datetime.now() + timedelta(minutes=est_wait)
        next_appt.assigned_resource = {
            "type":                   res_type,
            "unit_id":                chosen_unit["id"],
            "unit_name":              chosen_unit["name"],
            "specialty":              chosen_unit.get("specialty", ""),
            "estimated_wait_minutes": est_wait,
            "started_at":             datetime.now().isoformat(),
        }
        next_appt.updated_at = datetime.now()
        await db.commit()

        p_res   = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        patient = p_res.scalar_one_or_none()

        return {
            "routed":         True,
            "patient_id":     patient_id,
            "patient_name":   patient.name if patient else patient_id,
            "routed_to":      res_type,
            "unit_id":        chosen_unit["id"],
            "unit_name":      chosen_unit["name"],
            "est_wait":       est_wait,
            "appointment_id": next_appt.id,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 4. ADVANCE QUEUE
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def _advance_resource_queue(
        resource_type: str,
        db: AsyncSession,
        exclude_patient: str = "",
    ) -> Dict:
        """A room just freed. Give it to the highest-priority eligible waiting patient."""
        start, end = _today_bounds()

        rows = await db.execute(
            select(AppointmentRecord, PatientRecord)
            .join(PatientRecord, AppointmentRecord.patient_id == PatientRecord.patient_id)
            .where(
                AppointmentRecord.appointment_type == resource_type,
                AppointmentRecord.status == STATUS_SCHEDULED,
                AppointmentRecord.scheduled_time >= start,
                AppointmentRecord.scheduled_time < end,
            )
            .order_by(
                PatientRecord.priority.asc(),
                PatientRecord.severity_score.desc(),
            )
        )

        for appt, patient in rows.all():
            pid = appt.patient_id
            if pid == exclude_patient:
                continue
            if await _patient_active(db, pid):
                continue
            if (appt.assigned_resource or {}).get("locked"):
                continue
            if resource_type not in ("consultation", "icu") and patient.priority > 1:
                all_a      = await _patient_all_today(db, pid)
                done_types = {a.appointment_type for a in all_a if a.status == STATUS_DONE}
                has_consult = any(a.appointment_type == "consultation" for a in all_a)
                if has_consult and "consultation" not in done_types:
                    continue

            unit_load  = await _unit_load(db, resource_type)
            unit, load = _find_best_unit(resource_type, unit_load)
            if unit is None:
                break

            pool     = settings.resource_pool.get(resource_type, {})

            # ── ICU: admit patient to bed — no timer, no wait estimate ──
            if pool.get("is_icu"):
                appt.status         = STATUS_IN_PROGRESS
                appt.scheduled_time = datetime.now()
                appt.assigned_resource = {
                    "type":        resource_type,
                    "unit_id":     unit["id"],
                    "unit_name":   unit["name"],
                    "specialty":   unit.get("specialty", "Cardiac ICU"),
                    "admitted_at": datetime.now().isoformat(),
                    "started_at":  datetime.now().isoformat(),
                    "is_icu":      True,
                }
                appt.updated_at = datetime.now()
                # Update patient record
                p_res = await db.execute(
                    select(PatientRecord).where(PatientRecord.patient_id == pid)
                )
                p = p_res.scalar_one_or_none()
                if p:
                    p.location   = "icu"
                    p.is_icu     = 1
                    p.icu_bed_id = unit["id"]
                await db.commit()
                return {
                    "advanced":     True,
                    "patient_id":   pid,
                    "patient_name": patient.name,
                    "priority":     patient.priority,
                    "admitted_to":  unit["name"],
                    "icu_bed_id":   unit["id"],
                }

            # ── Normal resource: schedule with wait time ──
            duration = pool.get("duration_minutes", 20)
            est_wait = load * duration
            start_t = datetime.now() + timedelta(minutes=est_wait)
            end_t   = start_t + timedelta(minutes=duration + TEST_BUFFER_MINUTES)

            appt.status          = STATUS_IN_PROGRESS
            appt.scheduled_time  = start_t
            appt.end_time        = end_t
            appt.duration_minutes = duration
            appt.location_name   = unit["name"]
            appt.assigned_resource = {
                "type":                   resource_type,
                "unit_id":                unit["id"],
                "unit_name":              unit["name"],
                "specialty":              unit.get("specialty", ""),
                "estimated_wait_minutes": est_wait,
                "started_at":             datetime.now().isoformat(),
            }
            appt.updated_at = datetime.now()
            await db.commit()

            return {
                "advanced":     True,
                "patient_id":   pid,
                "patient_name": patient.name,
                "priority":     patient.priority,
                "routed_to":    resource_type,
                "unit":         unit["name"],
            }

        return {"advanced": False, "resource_type": resource_type}

    # ──────────────────────────────────────────────────────────────────────────
    # 5. COMPLETE APPOINTMENT
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def complete_appointment(appointment_id: int, db: AsyncSession) -> Dict:
        return await PatientJourneyAgent._do_complete(appointment_id, db, source="manual")

    @staticmethod
    async def _do_complete(
        appointment_id: int, db: AsyncSession, source: str = "auto"
    ) -> Dict:
        res  = await db.execute(
            select(AppointmentRecord).where(AppointmentRecord.id == appointment_id)
        )
        appt = res.scalar_one_or_none()
        if not appt:
            return {"error": "Appointment not found"}
        if appt.status == STATUS_DONE:
            return {"already_done": True, "patient_id": appt.patient_id}
        # ICU appointments can ONLY be completed via the discharge endpoint, never auto
        if appt.appointment_type == "icu" and source != "icu_discharge":
            return {"blocked": True, "reason": "ICU admission can only be ended by doctor discharge", "patient_id": appt.patient_id}

        patient_id    = appt.patient_id
        finished_type = appt.appointment_type

        _mark_done(appt)
        await db.commit()

        p_res   = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        patient      = p_res.scalar_one_or_none()
        is_emergency = (patient.priority == 1) if patient else False

        routing       = {}
        unlock_result = {}

        if finished_type == "consultation" and not is_emergency:
            # Consultation done → unlock + allocate first test sequentially
            unlock_result = await PatientJourneyAgent.unlock_and_allocate_tests(patient_id, db)
            routing = {"action": "tests_unlocked_sequential", "details": unlock_result}
        elif finished_type == "icu":
            # ICU discharge — patient leaves, bed is freed for next waiting emergency
            if patient:
                patient.location = "discharged"
                patient.is_icu   = 0
                patient.icu_bed_id = ""
                await db.commit()
            # Give freed ICU bed to next emergency patient waiting for a bed
            freed_gave_to = await PatientJourneyAgent._advance_resource_queue(
                "icu", db, exclude_patient=patient_id
            )
            all_appts    = await _patient_all_today(db, patient_id)
            done_count   = sum(1 for a in all_appts if a.status == STATUS_DONE)
            journey_done = (done_count == len(all_appts) and len(all_appts) > 0)
            return {
                "patient_id":        patient_id,
                "patient_name":      patient.name if patient else patient_id,
                "finished_type":     "icu",
                "source":            source,
                "next_step":         "discharged_from_icu",
                "routing":           {"action": "icu_discharged"},
                "unlock_result":     {},
                "journey_complete":  journey_done,
                "freed_bed_gave_to": freed_gave_to,
            }
        else:
            # Test done → route to NEXT sequential test automatically
            routing = await PatientJourneyAgent._route_next_test(patient_id, db)

        # Give freed room to next waiting patient (non-ICU resources only)
        freed_gave_to = await PatientJourneyAgent._advance_resource_queue(
            finished_type, db, exclude_patient=patient_id
        )

        all_appts    = await _patient_all_today(db, patient_id)
        done_count   = sum(1 for a in all_appts if a.status == STATUS_DONE)
        journey_done = (done_count == len(all_appts) and len(all_appts) > 0)

        return {
            "patient_id":         patient_id,
            "patient_name":       patient.name if patient else patient_id,
            "finished_type":      finished_type,
            "source":             source,
            "next_step": (
                "journey_complete" if journey_done
                else routing.get("routed_to") or routing.get("action")
            ),
            "routing":            routing,
            "unlock_result":      unlock_result,
            "journey_complete":   journey_done,
            "freed_room_gave_to": freed_gave_to,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 6. AUTO-TICK
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def auto_tick(db: AsyncSession) -> Dict:
        start, end = _today_bounds()
        cutoff = datetime.now() - timedelta(minutes=AUTO_COMPLETE_AFTER_MINUTES)

        rows = await db.execute(
            select(AppointmentRecord).where(
                AppointmentRecord.status == STATUS_IN_PROGRESS,
                AppointmentRecord.scheduled_time >= start,
                AppointmentRecord.scheduled_time < end,
                AppointmentRecord.scheduled_time <= cutoff,
                # Don't auto-complete ICU stays
                AppointmentRecord.appointment_type != "icu",
            ).order_by(AppointmentRecord.scheduled_time.asc())
        )
        expired = rows.scalars().all()

        completed_log = []
        for appt in expired:
            result = await PatientJourneyAgent._do_complete(appt.id, db, source="auto_tick")
            if not result.get("already_done"):
                completed_log.append({
                    "appointment_id":  appt.id,
                    "patient_id":      result.get("patient_id"),
                    "patient_name":    result.get("patient_name"),
                    "finished_type":   result.get("finished_type"),
                    "next_step":       result.get("next_step"),
                    "journey_complete": result.get("journey_complete", False),
                    "tests_unlocked":  bool(result.get("unlock_result", {}).get("allocated", 0)),
                })

        advance_result = await PatientJourneyAgent.auto_advance_queue(db)

        return {
            "timestamp":       datetime.now().isoformat(),
            "auto_completed":  completed_log,
            "completed_count": len(completed_log),
            "also_advanced":   advance_result.get("advanced", []),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 7. AUTO-ADVANCE QUEUE
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def auto_advance_queue(db: AsyncSession) -> Dict:
        start, end = _today_bounds()

        rows = await db.execute(
            select(AppointmentRecord, PatientRecord)
            .join(PatientRecord, AppointmentRecord.patient_id == PatientRecord.patient_id)
            .where(
                AppointmentRecord.status == STATUS_SCHEDULED,
                AppointmentRecord.scheduled_time >= start,
                AppointmentRecord.scheduled_time < end,
            )
            .order_by(
                PatientRecord.priority.asc(),
                PatientRecord.severity_score.desc(),
            )
        )

        advanced  = []
        seen_pids = set()

        for appt, patient in rows.all():
            pid = patient.patient_id
            if pid in seen_pids:
                continue
            if (appt.assigned_resource or {}).get("locked"):
                continue
            seen_pids.add(pid)
            if await _patient_active(db, pid):
                continue

            if appt.appointment_type == "icu":
                # ICU beds are NEVER auto-advanced — only a doctor can discharge a patient from ICU
                continue
            elif appt.appointment_type == "consultation":
                result = await PatientJourneyAgent._advance_resource_queue("consultation", db)
            else:
                if patient.priority > 1:
                    all_a       = await _patient_all_today(db, pid)
                    done_types  = {a.appointment_type for a in all_a if a.status == STATUS_DONE}
                    has_consult = any(a.appointment_type == "consultation" for a in all_a)
                    if has_consult and "consultation" not in done_types:
                        continue
                result = await PatientJourneyAgent._route_next_test(pid, db)

            if result.get("routed") or result.get("advanced"):
                advanced.append(result)

        return {"timestamp": datetime.now().isoformat(), "advanced_count": len(advanced), "advanced": advanced}

    # ──────────────────────────────────────────────────────────────────────────
    # 8. PRESCRIBE TESTS (doctor → ICU or post-consultation)
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def prescribe_tests(
        patient_id: str,
        tests: List[str],
        prescribed_by: str,
        db: AsyncSession,
    ) -> Dict:
        """
        Doctor prescribes specific tests. Works for ICU and post-consultation patients.
        Tests are queued SEQUENTIALLY — only first is started immediately.
        """
        p_res   = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        patient = p_res.scalar_one_or_none()
        if not patient:
            return {"error": f"Patient '{patient_id}' not found"}

        clean_tests = [t.lower().replace(" ", "_") for t in tests if t and t.strip()]
        # Resolve aliases
        from app.services.priority_service import TEST_ALIASES
        clean_tests = [TEST_ALIASES.get(t, t) for t in clean_tests]

        if not clean_tests:
            return {"error": "No valid tests provided"}

        existing_appts = await _patient_all_today(db, patient_id)
        max_seq = max(
            (getattr(a, "sequence_order") or 0 for a in existing_appts),
            default=0,
        )

        placeholder_appts: List[AppointmentRecord] = []
        for i, test_type in enumerate(clean_tests):
            seq = max_seq + i + 1
            appt = AppointmentRecord(
                patient_id=patient_id,
                appointment_type=test_type,
                scheduled_time=datetime.now(),
                assigned_resource={
                    "type":          test_type,
                    "unit_id":       "",
                    "unit_name":     f"Prescribed by {prescribed_by}",
                    "locked":        False,
                    "prescribed_by": prescribed_by,
                    "is_icu_test":   bool(patient.is_icu),
                },
                status=STATUS_SCHEDULED,
                sequence_order=seq,
            )
            placeholder_appts.append(appt)

        needed_types = list({t for t in clean_tests})
        unit_loads: Dict[str, Dict[str, int]] = {}
        for t in needed_types:
            unit_loads[t] = await _unit_load(db, t)

        # Hungarian assignment
        assignments = _hungarian_assign_tests(placeholder_appts, unit_loads)

        active_now = await _patient_active(db, patient_id)
        # Don't count ICU stay as blocking test start
        active_non_icu = [a for a in active_now if a != "icu"]

        allocated = []
        for idx, (appt, unit, est_wait) in enumerate(assignments):
            if unit:
                appt.assigned_resource = {
                    "type":                   appt.appointment_type,
                    "unit_id":                unit["id"],
                    "unit_name":              unit["name"],
                    "specialty":              unit.get("specialty", ""),
                    "estimated_wait_minutes": est_wait,
                    "prescribed_by":          prescribed_by,
                    "prescribed_at":          datetime.now().isoformat(),
                    "is_icu_test":            bool(patient.is_icu),
                }
                # Only start first test if patient is free (ICU patients can run tests from ICU)
                if idx == 0 and not active_non_icu:
                    appt.status = STATUS_IN_PROGRESS
                    appt.assigned_resource["started_at"] = datetime.now().isoformat()
                    appt.scheduled_time = datetime.now()
                else:
                    appt.status = STATUS_SCHEDULED
            else:
                appt.assigned_resource = {
                    "type":          appt.appointment_type,
                    "unit_id":       "",
                    "unit_name":     "Queued — all units busy",
                    "prescribed_by": prescribed_by,
                    "prescribed_at": datetime.now().isoformat(),
                    "is_icu_test":   bool(patient.is_icu),
                }
                appt.status = STATUS_SCHEDULED

            db.add(appt)
            allocated.append({
                "appointment_id": appt.id,
                "type":           appt.appointment_type,
                "unit":           unit["name"] if unit else "queued",
                "status":         appt.status,
                "est_wait":       est_wait,
                "sequence":       appt.sequence_order,
            })

        await db.commit()
        for i, (appt, _, _) in enumerate(assignments):
            allocated[i]["appointment_id"] = appt.id

        return {
            "patient_id":       patient_id,
            "patient_name":     patient.name,
            "severity_score":   patient.severity_score,
            "is_icu_patient":   bool(patient.is_icu),
            "prescribed_by":    prescribed_by,
            "tests_prescribed": len(clean_tests),
            "tests_allocated":  len([a for a in allocated if a["unit"] != "queued"]),
            "allocations":      allocated,
            "sequential_note":  "Tests will execute ONE AT A TIME. Next test starts automatically when previous completes.",
            "message": (
                f"{len(clean_tests)} test(s) prescribed by {prescribed_by}. "
                f"{len([a for a in allocated if a['unit'] != 'queued'])} assigned immediately. "
                f"Sequential execution enforced."
            ),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 9. EMERGENCY BUMP
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def handle_emergency_bump(
        new_patient_id: str, required_tests: List[str], db: AsyncSession
    ) -> Dict:
        start, end = _today_bounds()
        bumped     = []

        for test_type in required_tests:
            if test_type in ("consultation", "icu"):
                continue

            pool      = settings.resource_pool.get(test_type, {})
            cap       = pool.get("capacity_per_unit", 1)
            total     = pool.get("units", 0)
            unit_load = await _unit_load(db, test_type)

            if sum(unit_load.values()) < total * cap:
                continue

            rows = await db.execute(
                select(AppointmentRecord, PatientRecord)
                .join(PatientRecord, AppointmentRecord.patient_id == PatientRecord.patient_id)
                .where(
                    AppointmentRecord.appointment_type == test_type,
                    AppointmentRecord.status == STATUS_IN_PROGRESS,
                    AppointmentRecord.scheduled_time >= start,
                    AppointmentRecord.scheduled_time < end,
                    PatientRecord.priority > 1,
                )
                .order_by(
                    PatientRecord.priority.desc(),
                    PatientRecord.severity_score.asc(),
                )
                .limit(1)
            )
            row = rows.first()
            if not row:
                continue

            bump_appt, bump_patient = row
            prev_unit               = (bump_appt.assigned_resource or {}).get("unit_name", "")
            prev                    = dict(bump_appt.assigned_resource or {})
            bump_appt.status        = STATUS_SCHEDULED
            bump_appt.updated_at    = datetime.now()
            bump_appt.assigned_resource = {
                **prev,
                "unit_id":   "",
                "unit_name": "Re-queued (bumped by emergency)",
                "bumped":    True,
            }
            await db.commit()

            bumped.append({
                "patient_id":   bump_patient.patient_id,
                "patient_name": bump_patient.name,
                "priority":     bump_patient.priority,
                "bumped_from":  test_type,
                "unit":         prev_unit,
            })

        return {
            "emergency_patient_id": new_patient_id,
            "bumped_count":         len(bumped),
            "bumped_patients":      bumped,
            "message": (
                f"{len(bumped)} patient(s) re-queued to make room for emergency."
                if bumped else "Sufficient capacity — no bump needed."
            ),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 10. LIVE QUEUE STATUS (includes ICU)
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    async def get_queue_status(db: AsyncSession) -> Dict:
        start, end = _today_bounds()

        rows = await db.execute(
            select(AppointmentRecord).where(
                AppointmentRecord.scheduled_time >= start,
                AppointmentRecord.scheduled_time < end,
                AppointmentRecord.status.notin_([STATUS_DONE, STATUS_CANCELLED]),
            )
        )
        appts = rows.scalars().all()

        pid_set = {a.patient_id for a in appts}
        p_rows  = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id.in_(pid_set))
        )
        pmap    = {p.patient_id: p for p in p_rows.scalars().all()}
        plabel  = {1: "Emergency", 2: "Urgent", 3: "Routine"}
        result  = {}

        for res_type, pool in settings.resource_pool.items():
            if pool.get("units", 0) == 0:
                continue  # skip empty pools (stress_test legacy)

            is_icu     = pool.get("is_icu", False)
            total_units = pool.get("units", 0)

            # ── ICU is NOT a timed resource — patients stay until doctor discharges ──
            if is_icu:
                admitted = []
                waiting_for_bed = []
                for a in appts:
                    if a.appointment_type != "icu":
                        continue
                    pat = pmap.get(a.patient_id)
                    res = a.assigned_resource or {}
                    admitted_at_str = res.get("started_at", "")
                    hours_admitted  = 0
                    if admitted_at_str and a.status == STATUS_IN_PROGRESS:
                        try:
                            admitted_at    = datetime.fromisoformat(admitted_at_str)
                            hours_admitted = round((datetime.now() - admitted_at).total_seconds() / 3600, 1)
                        except Exception:
                            pass

                    entry = {
                        "appointment_id": a.id,
                        "patient_id":     a.patient_id,
                        "patient_name":   pat.name if pat else "Unknown",
                        "priority":       pat.priority if pat else 1,
                        "priority_label": "Emergency",
                        "severity_score": pat.severity_score if pat else 0,
                        "status":         "admitted" if a.status == STATUS_IN_PROGRESS else "waiting_for_bed",
                        "icu_bed_id":     res.get("unit_id", ""),
                        "icu_bed_name":   res.get("unit_name", ""),
                        "admitted_at":    admitted_at_str,
                        "hours_in_icu":   hours_admitted,
                        "discharge_required": True,   # must be manually discharged by doctor
                        "is_icu_patient": True,
                    }
                    if a.status == STATUS_IN_PROGRESS:
                        admitted.append(entry)
                    else:
                        waiting_for_bed.append(entry)

                result["icu"] = {
                    "resource_type":      "icu",
                    "label":              "ICU",
                    "is_icu":             True,
                    "total_beds":         total_units,
                    "admitted":           admitted,
                    "admitted_count":     len(admitted),
                    "waiting_for_bed":    waiting_for_bed,
                    "waiting_count":      len(waiting_for_bed),
                    "occupied_beds":      len(admitted),
                    "available_beds":     max(0, total_units - len(admitted)),
                    "is_full":            len(admitted) >= total_units,
                    "discharge_note":     "ICU patients are discharged only by explicit doctor action. No auto-complete.",
                }
                continue  # skip the normal resource logic below

            # ── Normal timed resources (consultation, blood_test, ecg, etc.) ──
            in_prog, waiting = [], []

            for a in appts:
                if a.appointment_type != res_type:
                    continue

                pat = pmap.get(a.patient_id)
                pri = pat.priority if pat else 3
                sev = pat.severity_score if pat else 0

                started_at_str  = (a.assigned_resource or {}).get("started_at", "")
                minutes_in_room = 0
                if started_at_str and a.status == STATUS_IN_PROGRESS:
                    try:
                        started_at      = datetime.fromisoformat(started_at_str)
                        minutes_in_room = int(
                            (datetime.now() - started_at).total_seconds() / 60
                        )
                    except Exception:
                        pass

                pool_dur     = pool.get("duration_minutes", 20)
                progress_pct = min(100, int(minutes_in_room / max(pool_dur, 1) * 100))
                is_locked    = bool((a.assigned_resource or {}).get("locked"))
                is_icu_test  = bool((a.assigned_resource or {}).get("is_icu_test"))

                entry = {
                    "appointment_id":  a.id,
                    "patient_id":      a.patient_id,
                    "patient_name":    pat.name if pat else "Unknown",
                    "priority":        pri,
                    "priority_label":  plabel.get(pri, "Routine"),
                    "severity_score":  sev,
                    "status":          a.status,
                    "assigned_unit":   (a.assigned_resource or {}).get("unit_name", ""),
                    "unit_id":         (a.assigned_resource or {}).get("unit_id", ""),
                    "est_wait":        (a.assigned_resource or {}).get("estimated_wait_minutes", 0),
                    "sequence_order":  getattr(a, "sequence_order", None),
                    "minutes_in_room": minutes_in_room,
                    "progress_pct":    progress_pct,
                    "auto_done_in":    max(0, AUTO_COMPLETE_AFTER_MINUTES - minutes_in_room),
                    "locked":          is_locked,
                    "lock_reason":     ("ICU prescription pending" if is_icu_test and is_locked
                                       else "Awaiting consultation" if is_locked else ""),
                    "is_icu_test":     is_icu_test,
                    "is_icu_patient":  bool(pat.is_icu) if pat and hasattr(pat, "is_icu") else False,
                    "icu_bed":         (pat.icu_bed_id or "") if pat and hasattr(pat, "icu_bed_id") else "",
                }

                if a.status == STATUS_IN_PROGRESS:
                    in_prog.append(entry)
                elif a.status == STATUS_SCHEDULED:
                    waiting.append(entry)

            waiting.sort(key=lambda x: (x["priority"], -x["severity_score"]))
            in_prog.sort(key=lambda x: x["priority"])

            result[res_type] = {
                "resource_type":         res_type,
                "label":                 res_type.replace("_", " ").title(),
                "is_icu":                False,
                "total_units":           total_units,
                "in_progress":           in_prog,
                "in_progress_count":     len(in_prog),
                "waiting":               waiting,
                "waiting_count":         len(waiting),
                "is_full":               len(in_prog) >= total_units,
                "free_units":            max(0, total_units - len(in_prog)),
                "occupied_beds":         len(in_prog),
                "available_beds":        max(0, total_units - len(in_prog)),
                "auto_complete_minutes": AUTO_COMPLETE_AFTER_MINUTES,
            }

        return {"timestamp": datetime.now().isoformat(), "queues": result}