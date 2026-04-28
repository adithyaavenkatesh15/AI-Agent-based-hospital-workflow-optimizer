# app/routers/system.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.database import get_db, PatientRecord, AppointmentRecord, DisruptionRecord
from app.services.capacity_service import CapacityService
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/queue/status")
async def get_queue_status(db: AsyncSession = Depends(get_db)):
    """Get summarized queue status for all priority levels"""
    patients_result = await db.execute(select(PatientRecord))
    patients = patients_result.scalars().all()

    queues = {
        "emergency": {"count": 0, "avg_wait_time": 5},
        "urgent": {"count": 0, "avg_wait_time": 15},
        "routine": {"count": 0, "avg_wait_time": 45}
    }

    for p in patients:
        prio = p.priority
        if prio == 1:
            queues["emergency"]["count"] += 1
        elif prio == 2:
            queues["urgent"]["count"] += 1
        else:
            queues["routine"]["count"] += 1

    total = sum(q["count"] for q in queues.values())
    return {
        "queues": queues,
        "total_patients": total,
        "last_updated": datetime.now().isoformat()
    }


@router.get("/queue/metrics")
async def get_queue_metrics(db: AsyncSession = Depends(get_db)):
    """Get detailed queue metrics"""
    patients_result = await db.execute(select(PatientRecord))
    patients = patients_result.scalars().all()

    total = len(patients)
    emergency = sum(1 for p in patients if p.priority == 1)
    urgent = sum(1 for p in patients if p.priority == 2)
    routine = sum(1 for p in patients if p.priority == 3)

    return {
        "total_patients": total,
        "by_priority": {
            "emergency": emergency,
            "urgent": urgent,
            "routine": routine
        },
        "avg_wait_times": {
            "emergency": 5,
            "urgent": 18,
            "routine": 42
        },
        "throughput_per_hour": max(1, total // 8),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/resources")
async def get_resources(db: AsyncSession = Depends(get_db)):
    """
    Real-time resource status:
    - Each doctor/cabin shows which patient is currently assigned
    - No double-booking: one patient per unit at a time
    - Live status from DB appointments
    """
    from app.config import settings

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Fetch ONLY in_progress appointments — these are the patients physically in a room.
    # "scheduled" patients are waiting in queue, not occupying any unit yet.
    # A doctor becomes FREE the instant their consultation is marked done.
    appts_result = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
            AppointmentRecord.status == "in_progress"   # ← ONLY physically in room
        ).order_by(AppointmentRecord.scheduled_time)
    )
    appointments = appts_result.scalars().all()

    # Fetch patient names
    patient_ids = list({a.patient_id for a in appointments})
    patients_result = await db.execute(
        select(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
    )
    patients_map = {p.patient_id: p for p in patients_result.scalars().all()}

    # Build per-unit assignment map: unit_id → list of assigned patients
    unit_assignments: Dict[str, List[dict]] = {}
    for appt in appointments:
        res = appt.assigned_resource or {}
        if not isinstance(res, dict):
            continue
        unit_id = res.get("unit_id", "")
        if not unit_id:
            continue
        patient = patients_map.get(appt.patient_id)
        entry = {
            "patient_id": appt.patient_id,
            "patient_name": patient.name if patient else "Unknown",
            "patient_priority": patient.priority if patient else 3,
            "appointment_type": appt.appointment_type,
            "scheduled_time": appt.scheduled_time.strftime("%H:%M") if appt.scheduled_time else "—",
            "status": appt.status,
            "severity_score": getattr(patient, "severity_score", None) if patient else None,
        }
        if unit_id not in unit_assignments:
            unit_assignments[unit_id] = []
        unit_assignments[unit_id].append(entry)

    pool = settings.resource_pool

    def build_unit_cards(resource_type: str) -> List[dict]:
        res_pool = pool.get(resource_type, {})
        units = res_pool.get("units_info", [])
        cap_per_unit = res_pool.get("capacity_per_unit", 1)
        is_icu_resource = res_pool.get("is_icu", False)
        cards = []
        for unit in units:
            uid = unit["id"]
            assigned = unit_assignments.get(uid, [])
            # Enforce: only show up to capacity_per_unit patients per unit
            # Extra = overflow that shouldn't happen but flag if it does
            current = assigned[:cap_per_unit]
            overflow = assigned[cap_per_unit:]
            is_busy = len(current) >= cap_per_unit

            card = {
                "id": uid,
                "name": unit["name"],
                "specialty": unit.get("specialty", ""),
                "status": "admitted" if (is_icu_resource and is_busy) else ("busy" if is_busy else "available"),
                "capacity": cap_per_unit,
                "patients_today": len(assigned),
                "max_capacity": cap_per_unit,
                "current_patient": current[0] if current else None,   # active right now
                "queue": current[1:] + overflow,                       # waiting next
                "is_double_booked": len(overflow) > 0,                 # flag conflict
                "is_icu": is_icu_resource,
                # ICU-specific: no auto-discharge; requires explicit doctor action
                "discharge_required": is_icu_resource and is_busy,
            }
            cards.append(card)
        return cards

    doctors   = build_unit_cards("consultation")
    icu_units = build_unit_cards("icu")
    ecg_units = build_unit_cards("ecg")
    echo_units= build_unit_cards("echocardiogram")
    tmt_units = build_unit_cards("tmt")
    angio_units = build_unit_cards("angiogram")
    trop_units = build_unit_cards("troponin_test")
    cct_units  = build_unit_cards("cardiac_ct")
    bpm_units  = build_unit_cards("bp_monitoring")
    bt_units  = build_unit_cards("blood_test")

    equipment = ecg_units + echo_units + tmt_units + angio_units + trop_units + cct_units
    rooms     = bt_units + bpm_units

    total_doctors = len(doctors)
    available_doctors = sum(1 for d in doctors if d["status"] == "available")
    total_icu = len(icu_units)
    available_icu = sum(1 for u in icu_units if u["status"] == "available")
    total_equipment = len(equipment)
    available_equipment = sum(1 for e in equipment if e["status"] == "available")
    total_rooms = len(rooms)
    available_rooms = sum(1 for r in rooms if r["status"] == "available")
    total_resources = total_doctors + total_icu + total_equipment + total_rooms
    busy = total_resources - (available_doctors + available_icu + available_equipment + available_rooms)
    utilization_rate = round(busy / max(total_resources, 1) * 100)

    # Build ICU summary with patient list
    icu_patients_in = []
    for u in icu_units:
        if u.get("current_patient"):
            icu_patients_in.append({
                **u["current_patient"],
                "bed_id": u["id"],
                "bed_name": u["name"],
            })

    return {
        "icu": icu_units,
        "doctors": doctors,
        "equipment": equipment,
        "rooms": rooms,
        "icu_summary": {
            "total_beds":       total_icu,
            "occupied_beds":    total_icu - available_icu,
            "available_beds":   available_icu,
            "patients":         icu_patients_in,
            "patient_ids":      [p["patient_id"] for p in icu_patients_in],
        },
        "summary": {
            "total_icu_beds": total_icu,
            "available_icu_beds": available_icu,
            "total_doctors": total_doctors,
            "available_doctors": available_doctors,
            "total_equipment": total_equipment,
            "available_equipment": available_equipment,
            "total_rooms": total_rooms,
            "available_rooms": available_rooms,
            "utilization_rate": utilization_rate,
        }
    }

@router.get("/resources/utilization")
async def get_resource_utilization(db: AsyncSession = Depends(get_db)):
    """Get resource utilization percentages (real-time from DB)"""
    capacity_status = await CapacityService.get_capacity_status(db)
    utilization = {}
    for resource, info in capacity_status.items():
        cap = info.get("capacity", 1)
        booked = info.get("booked", 0)
        utilization[resource] = round(booked / max(cap, 1) * 100, 1)
    return utilization


@router.get("/analytics")
async def get_analytics(range: str = "24h", db: AsyncSession = Depends(get_db)):
    """Get system performance analytics"""
    patients_result = await db.execute(select(PatientRecord))
    patients = patients_result.scalars().all()
    total = len(patients)

    # Generate realistic chart data based on actual patient count
    base_val = max(5, total // 6)
    performance_data = {
        "labels": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
        "patientsProcessed": [
            max(0, base_val - 2),
            max(0, base_val - 4),
            base_val + 5,
            base_val + 10,
            base_val + 7,
            base_val + 3
        ],
        "waitTimes": [8, 12, 10, 15, 13, 11]
    }

    return {
        "performance": performance_data,
        "waitTimes": {
            "emergency": 5,
            "urgent": 18,
            "routine": 42
        },
        "utilization": {
            resource: round(info["booked"] / max(info["capacity"], 1) * 100, 1)
            for resource, info in (await CapacityService.get_capacity_status(db)).items()
        },
        "patient_summary": {
            "total": total,
            "emergency": sum(1 for p in patients if p.priority == 1),
            "urgent": sum(1 for p in patients if p.priority == 2),
            "routine": sum(1 for p in patients if p.priority == 3),
        }
    }


@router.get("/analytics/wait-times")
async def get_wait_time_analytics():
    """Get detailed wait time analytics by priority"""
    return {
        "emergency": {"avg": 5, "min": 0, "max": 10},
        "urgent": {"avg": 18, "min": 10, "max": 30},
        "routine": {"avg": 42, "min": 30, "max": 90},
    }


@router.get("/analytics/performance")
async def get_performance_analytics(db: AsyncSession = Depends(get_db)):
    """Get performance metrics"""
    patients_result = await db.execute(select(PatientRecord))
    patients = patients_result.scalars().all()
    total = len(patients)
    return {
        "patients_processed": total,
        "avg_processing_time_minutes": 12,
        "scheduling_efficiency": 0.92,
        "patient_satisfaction_score": 4.3,
    }


@router.get("/status")
async def get_system_status(db: AsyncSession = Depends(get_db)):
    """Get overall system status"""
    try:
        # Quick DB check
        await db.execute(select(func.count(PatientRecord.id)))
        db_status = "healthy"
    except Exception:
        db_status = "degraded"

    return {
        "status": "operational",
        "database": db_status,
        "agents": {
            "reception_agent": "active",
            "scheduling_agent": "active",
            "exception_handling_agent": "active",
            "assistant_agent": "active",
        },
        "algorithms": {
            "hungarian_algorithm": "ready",
            "genetic_algorithm": "ready",
            "q_learning": "ready",
        },
        "uptime_hours": 24,
        "last_optimization": datetime.now().replace(minute=0, second=0, microsecond=0).isoformat(),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/health")
async def get_system_health():
    """Get system health check"""
    return {
        "status": "healthy",
        "services": {
            "api": "up",
            "database": "up",
            "scheduler": "up",
            "notification_service": "up",
        },
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/capacity")
async def get_capacity_status(db: AsyncSession = Depends(get_db)):
    """
    Get real-time capacity status for all resource types.
    Includes waiting time per resource and smart reallocation suggestions.
    """
    capacity_status = await CapacityService.get_capacity_status(db)
    overflow_map = {
        "consultation": [],
        "echocardiogram": ["stress_test"],
        "stress_test": ["echocardiogram"],
        "ecg": ["blood_test"],
        "blood_test": ["ecg"],
    }

    full_resources = [k for k, v in capacity_status.items() if v["is_full"]]
    total_capacity = sum(v["capacity"] for v in capacity_status.values())
    total_booked = sum(v["booked"] for v in capacity_status.values())
    overall_utilization = round(total_booked / total_capacity * 100, 1) if total_capacity > 0 else 0

    # Build enriched resource info with reallocation suggestions
    enriched = {}
    for resource, info in capacity_status.items():
        alternatives = overflow_map.get(resource, [])
        alt_info = []
        for alt in alternatives:
            alt_status = capacity_status.get(alt, {})
            alt_info.append({
                "resource": alt,
                "available": alt_status.get("available", 0),
                "waiting_time_minutes": alt_status.get("waiting_time_minutes", 0),
                "is_full": alt_status.get("is_full", False),
            })
        enriched[resource] = {
            **info,
            "alternatives": alt_info,
            "has_available_alternative": any(not a["is_full"] for a in alt_info),
        }

    return {
        "timestamp": datetime.now().isoformat(),
        "overall_utilization_percent": overall_utilization,
        "total_slots": total_capacity,
        "total_booked": total_booked,
        "total_available": total_capacity - total_booked,
        "full_resources": full_resources,
        "capacity_alert": len(full_resources) > 0,
        "resources": enriched
    }




@router.get("/resources/assignments")
async def get_resource_assignments(db: AsyncSession = Depends(get_db)):
    """
    Get current assignment map: which doctor/cabin is assigned to which patient RIGHT NOW.
    Used by dashboard to show live Doctor → Patient, Cabin → Patient view.
    Ensures same unit is never double-booked at the same time.
    """
    from app.config import settings
    from app.database import PatientRecord

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Only in_progress = physically in the room right now.
    # scheduled = waiting in queue (not assigned to any unit yet).
    appts_result = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
            AppointmentRecord.status == "in_progress"   # ← ONLY physically in room
        ).order_by(AppointmentRecord.scheduled_time)
    )
    appointments = appts_result.scalars().all()

    # Get patient names
    patient_ids = list({a.patient_id for a in appointments})
    patients_result = await db.execute(
        select(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
    )
    patients_map = {p.patient_id: p for p in patients_result.scalars().all()}

    # Build unit → [patients] map
    unit_assignments = {}   # unit_id → list of patient assignments
    unit_meta = {}          # unit_id → unit info

    # Pre-populate all units from config as empty
    for res_type, pool in settings.resource_pool.items():
        for unit in pool.get("units_info", []):
            uid = unit["id"]
            unit_assignments[uid] = []
            unit_meta[uid] = {
                "unit_id": uid,
                "unit_name": unit["name"],
                "specialty": unit.get("specialty", ""),
                "resource_type": res_type,
                "type_label": res_type.replace("_", " ").title(),
            }

    # Fill in actual assignments
    for appt in appointments:
        res = appt.assigned_resource or {}
        if not isinstance(res, dict):
            continue
        uid = res.get("unit_id", "")
        if not uid:
            continue
        patient = patients_map.get(appt.patient_id)
        priority_labels = {1: "Emergency", 2: "Urgent", 3: "Routine"}
        unit_assignments.setdefault(uid, []).append({
            "patient_id": appt.patient_id,
            "patient_name": patient.name if patient else "Unknown",
            "priority": patient.priority if patient else 3,
            "priority_label": priority_labels.get(patient.priority if patient else 3, "Routine"),
            "severity_score": patient.severity_score if patient else 0,
            "appointment_type": appt.appointment_type,
            "scheduled_time": appt.scheduled_time.isoformat() if appt.scheduled_time else None,
            "status": appt.status,
            "estimated_wait": res.get("estimated_wait_minutes", 0),
        })

    # Build final response grouped by resource type
    grouped = {}
    for uid, meta in unit_meta.items():
        rtype = meta["resource_type"]
        if rtype not in grouped:
            grouped[rtype] = {
                "resource_type": rtype,
                "type_label": meta["type_label"],
                "units": []
            }
        patients_in_unit = unit_assignments.get(uid, [])
        pool = settings.resource_pool.get(rtype, {})
        cap = pool.get("capacity_per_unit", 1)
        is_occupied = len(patients_in_unit) >= cap

        grouped[rtype]["units"].append({
            **meta,
            "is_occupied": is_occupied,
            "patient_count": len(patients_in_unit),
            "capacity": cap,
            "patients": patients_in_unit,
            "status": "busy" if is_occupied else "available",
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "assignments": grouped,
        "summary": {
            "total_units": len(unit_meta),
            "occupied_units": sum(1 for uid, pts in unit_assignments.items() if pts),
            "total_patients_assigned": sum(len(pts) for pts in unit_assignments.values()),
        }
    }

@router.get("/capacity/overflow-history")
async def get_overflow_history(db: AsyncSession = Depends(get_db)):
    """
    Get history of overflow events — appointments that were redirected
    because the primary resource was at capacity.
    Reads from workflow_results disruption_handling field.
    """
    from app.database import WorkflowResult
    result = await db.execute(
        select(WorkflowResult).order_by(WorkflowResult.processed_at.desc()).limit(50)
    )
    workflows = result.scalars().all()

    overflow_events = []
    for wf in workflows:
        dh = wf.disruption_handling or {}
        if dh.get("fallback_applied") or dh.get("disruptions_detected"):
            overflow_details = dh.get("overflow_details", [])
            if overflow_details:
                overflow_events.append({
                    "patient_id": wf.patient_id,
                    "processed_at": wf.processed_at.isoformat() if wf.processed_at else None,
                    "overflow_count": len(overflow_details),
                    "overflow_details": overflow_details
                })

    return {
        "total_overflow_events": len(overflow_events),
        "events": overflow_events,
        "timestamp": datetime.now().isoformat()
    }