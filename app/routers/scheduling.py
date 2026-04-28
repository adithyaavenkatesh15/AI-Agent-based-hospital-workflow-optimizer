# app/routers/scheduling.py
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, date
from app.models import SchedulingResult
from app.services.scheduling_service import SchedulingService
from app.database import get_db, AppointmentRecord, PatientRecord
from app.config import settings

router = APIRouter(prefix="/scheduling", tags=["Scheduling"])


@router.get("/appointments", response_model=List[dict])
async def get_appointments(
    date_str: Optional[str] = Query(None, alias="date", description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db)
):
    """Get all appointments, optionally filtered by date"""
    try:
        query = select(AppointmentRecord).order_by(AppointmentRecord.scheduled_time)

        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                start = datetime.combine(target_date, datetime.min.time())
                end = start + timedelta(days=1)
                query = query.where(
                    AppointmentRecord.scheduled_time >= start,
                    AppointmentRecord.scheduled_time < end
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        result = await db.execute(query)
        appointments = result.scalars().all()

        # Enrich with patient names
        patient_ids = list({a.patient_id for a in appointments})
        patients_result = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
        )
        patients_map = {p.patient_id: p for p in patients_result.scalars().all()}

        return [
            {
                "id": a.id,
                "patient_id": a.patient_id,
                "patient_name": patients_map.get(a.patient_id, PatientRecord(name="Unknown")).name,
                "patient_priority": patients_map.get(a.patient_id, PatientRecord(priority=3)).priority,
                "appointment_type": a.appointment_type,
                "scheduled_time": a.scheduled_time.isoformat() if a.scheduled_time else None,
                "end_time": getattr(a, "end_time", None) and a.end_time.isoformat() if getattr(a, "end_time", None) else None,
                "duration_minutes": getattr(a, "duration_minutes", None),
                "location_name": getattr(a, "location_name", None) or (a.assigned_resource or {}).get("unit_name", ""),
                "assigned_resource": a.assigned_resource or {},
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in appointments
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching appointments: {str(e)}")


@router.get("/schedule")
async def get_schedule(
    date_str: Optional[str] = Query(None, alias="date", description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db)
):
    """Get structured daily schedule grouped by appointment type"""
    try:
        query = select(AppointmentRecord).order_by(AppointmentRecord.scheduled_time)

        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                start = datetime.combine(target_date, datetime.min.time())
                end = start + timedelta(days=1)
                display_date = target_date.isoformat()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        else:
            target_date = datetime.now().date()
            start = datetime.combine(target_date, datetime.min.time())
            end = start + timedelta(days=1)
            display_date = target_date.isoformat()

        query = query.where(
            AppointmentRecord.scheduled_time >= start,
            AppointmentRecord.scheduled_time < end
        )
        result = await db.execute(query)
        appointments = result.scalars().all()

        # Enrich with patient names
        patient_ids = list({a.patient_id for a in appointments})
        patients_result = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
        )
        patients_map = {p.patient_id: p for p in patients_result.scalars().all()}

        # Group by appointment type — initialize known columns (no bp_monitoring, no stress_test)
        schedule = {}
        always_show_types = [
            "icu", "consultation",
            "ecg", "echocardiogram", "tmt", "angiogram",
            "troponin_test", "cardiac_ct", "blood_test",
        ]
        for appt_type in always_show_types:
            schedule[appt_type] = []

        # ── Compute sequential start/end times per patient ──────────────────
        from collections import defaultdict as _dd
        patient_appt_lists = _dd(list)
        for a in appointments:
            patient_appt_lists[a.patient_id].append(a)

        computed_times = {}  # appt.id -> (start_dt, end_dt)
        for pid, p_appts in patient_appt_lists.items():
            # Sort by sequence_order so tests chain in the correct order
            sorted_appts = sorted(
                p_appts,
                key=lambda x: (x.sequence_order if x.sequence_order is not None else 999, x.id)
            )
            # Anchor: use the scheduled_time of the first non-ICU appointment
            non_icu = [a for a in sorted_appts
                       if not settings.resource_pool.get(a.appointment_type, {}).get("is_icu")]
            anchor = non_icu[0].scheduled_time if non_icu else None
            cursor = anchor

            for a in sorted_appts:
                pool = settings.resource_pool.get(a.appointment_type, {})
                dur = pool.get("duration_minutes", 20)
                if pool.get("is_icu"):
                    computed_times[a.id] = (a.scheduled_time, None)
                    continue
                start_t = cursor or datetime.now()
                end_t   = start_t + timedelta(minutes=dur)
                computed_times[a.id] = (start_t, end_t)
                cursor = end_t   # next test starts exactly when this one ends

        for a in appointments:
            appt_type = a.appointment_type or "consultation"
            if appt_type not in schedule:
                schedule[appt_type] = []
            patient = patients_map.get(a.patient_id)
            pool = settings.resource_pool.get(appt_type, {})
            dur = pool.get("duration_minutes", 20)
            start_dt, end_dt = computed_times.get(a.id, (a.scheduled_time, None))
            if end_dt is None and start_dt and not pool.get("is_icu"):
                end_dt = start_dt + timedelta(minutes=dur)
            schedule[appt_type].append({
                "id": a.id,
                "patient_id": a.patient_id,
                "patient_name": patient.name if patient else "Unknown",
                "patient_priority": patient.priority if patient else 3,
                "severity_score": patient.severity_score if patient else 0,
                "is_icu_patient": bool(getattr(patient, "is_icu", 0)) if patient else False,
                "scheduled_time": start_dt.isoformat() if start_dt else None,
                "end_time": end_dt.isoformat() if end_dt else None,
                "status": a.status,
                "sequence_order": a.sequence_order,
                "assigned_unit": (a.assigned_resource or {}).get("unit_name", ""),
                "rescheduled": bool(getattr(a, "rescheduled_from", None)),
                "reschedule_reason": getattr(a, "reschedule_reason", "") or "",
            })

        return {
            "date": display_date,
            "total_appointments": len(appointments),
            "schedule": schedule,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching schedule: {str(e)}")


@router.post("/optimize", response_model=SchedulingResult)
async def optimize_scheduling(
    patient_requirements: List[Dict[str, Any]],
    available_resources: Dict[str, List[Dict[str, Any]]] = None,
    current_schedules: Dict[str, Any] = None,
    priority_weights: Dict[int, int] = None
):
    """
    Optimize patient scheduling using Hungarian Algorithm (Scheduling Agent).
    """
    try:
        if available_resources is None:
            available_resources = SchedulingService.get_mock_resources()
        if current_schedules is None:
            current_schedules = {}

        result = SchedulingService.optimize_patient_assignment(
            patient_requirements=patient_requirements,
            available_resources=available_resources,
            current_schedules=current_schedules,
            priority_weights=priority_weights
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error optimizing schedule: {str(e)}")


@router.get("/resources", response_model=Dict[str, List[Dict[str, Any]]])
async def get_available_resources():
    """Get currently available time-slotted resources"""
    return SchedulingService.get_mock_resources()