# app/services/capacity_service.py
"""
Capacity Service — Smart Unit-Level Allocation with Dynamic Overflow
- Tracks per-unit (doctor/cabin) occupancy from DB
- Allocates based on: severity score × test complexity
- High severity → fastest available unit first
- Multiple units → parallel allocation (10 doctors = 10 simultaneous patients)

OVERFLOW STRATEGY (3-tier):
  Tier 1 — Primary resource has a free unit → assign directly
  Tier 2 — Primary full → try configured overflow_fallback list (similar tests)
  Tier 3 — All configured fallbacks full → scan ALL non-ICU, non-consultation
            resources in the pool and pick the one with the SHORTEST wait time
            (Hungarian-style greedy: lowest est_wait wins).
            This ensures a patient NEVER waits if ANY resource slot is free.
"""
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import AppointmentRecord
from app.config import settings


class CapacityService:

    @staticmethod
    async def get_today_unit_counts(db: AsyncSession) -> Dict[str, int]:
        """
        Returns how many patients are PHYSICALLY IN each resource type right now.
        Only counts in_progress — scheduled patients are in the waiting queue, not in a room.
        This makes is_full reflect real room occupancy: a doctor is free as soon as
        their last patient's consultation is marked done.
        """
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        result = await db.execute(
            select(
                AppointmentRecord.appointment_type,
                func.count(AppointmentRecord.id).label("count")
            )
            .where(
                AppointmentRecord.scheduled_time >= today_start,
                AppointmentRecord.scheduled_time < today_end,
                AppointmentRecord.status == "in_progress"   # ← ONLY physically in room
            )
            .group_by(AppointmentRecord.appointment_type)
        )
        return {row.appointment_type: row.count for row in result.all()}

    @staticmethod
    async def get_capacity_status(db: AsyncSession) -> Dict[str, dict]:
        """
        Returns per-resource-type capacity status.
        Units = parallel slots (5 doctors = 5 simultaneous patients possible).
        """
        counts = await CapacityService.get_today_unit_counts(db)
        status = {}

        for resource_type, pool in settings.resource_pool.items():
            total_units = pool["units"]
            duration = pool["duration_minutes"]
            booked = counts.get(resource_type, 0)
            is_icu = pool.get("is_icu", False)

            # How many units are currently free (parallel capacity)
            free_units = max(0, total_units - booked)
            is_full = free_units == 0

            # ICU has NO wait time concept — patients stay until doctor discharge
            if is_icu:
                waiting_time = 0  # ICU waiting-for-bed patients are just queued, no ETA
            elif free_units > 0:
                waiting_time = 0  # immediate slot available
            else:
                # How many patients are waiting beyond capacity
                overflow_count = booked - total_units
                waiting_time = (overflow_count + 1) * duration

            status[resource_type] = {
                "total_units": total_units,
                "capacity": total_units,
                "booked": booked,
                "free_units": free_units,
                "available": free_units,
                "is_full": is_full,
                "is_icu": is_icu,
                "waiting_time_minutes": waiting_time,
                "duration_per_appointment": duration,
                "units_info": pool.get("units_info", []),
            }

        return status

    @staticmethod
    def get_severity_priority(severity_score: int) -> int:
        """Returns cost multiplier: low number = higher priority."""
        return settings.severity_priority_map.get(severity_score, 5)

    @staticmethod
    async def allocate_unit_for_patient(
        resource_type: str,
        severity_score: int,
        capacity_status: Dict,
        db: AsyncSession
    ) -> Optional[Dict]:
        """
        Allocates a specific unit (doctor/cabin) for a patient.
        High severity → assigned to unit with shortest queue.
        Returns assigned unit info or None if all full.
        """
        pool = settings.resource_pool.get(resource_type)
        if not pool:
            return None

        res_status = capacity_status.get(resource_type, {})
        if res_status.get("is_full"):
            return None

        # Get per-unit booking counts from DB (fetch all, group in Python — JSON field)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        result = await db.execute(
            select(AppointmentRecord.assigned_resource)
            .where(
                AppointmentRecord.appointment_type == resource_type,
                AppointmentRecord.scheduled_time >= today_start,
                AppointmentRecord.scheduled_time < today_end,
                AppointmentRecord.status == "in_progress"   # ← only physically in room
            )
        )
        unit_load = {}
        for row in result.all():
            resource_dict = row.assigned_resource or {}
            if isinstance(resource_dict, dict):
                uid = resource_dict.get("unit_id", "")
                if uid:
                    unit_load[uid] = unit_load.get(uid, 0) + 1

        # Find least loaded unit — SKIP units at full capacity (double-booking prevention)
        units = pool.get("units_info", [])
        best_unit = None
        best_load = float('inf')
        cap_per_unit = pool.get("capacity_per_unit", 1)

        for unit in units:
            load = unit_load.get(unit["id"], 0)
            # Skip if this unit is already at capacity (prevents double-booking)
            if load >= cap_per_unit:
                continue
            if load < best_load:
                best_load = load
                best_unit = unit

        if best_unit:
            duration = pool["duration_minutes"]
            estimated_wait = best_load * duration
            return {
                "unit_id": best_unit["id"],
                "unit_name": best_unit["name"],
                "specialty": best_unit.get("specialty", ""),
                "resource_type": resource_type,
                "current_load": best_load,
                "estimated_wait_minutes": estimated_wait,
            }
        return None

    # ─── Tier-3 helper ────────────────────────────────────────────────────────
    @staticmethod
    async def _find_any_available_resource(
        original_type: str,
        excluded_types: List[str],
        capacity_status: Dict,
        severity_score: int,
        db: AsyncSession,
    ) -> Tuple[Optional[str], Optional[Dict]]:
        """
        Tier-3 dynamic fallback: scan ALL non-ICU, non-consultation resources
        in the pool and pick the one with the SHORTEST estimated wait that has
        at least one free unit.  Returns (resource_type, unit_info) or (None, None).

        This guarantees a patient is NEVER left waiting if ANY slot is free
        anywhere in the hospital — regardless of whether it's in the configured
        overflow_fallback list.
        """
        # Resources that are never valid generic substitutes
        SKIP_TYPES = {"icu", "consultation", "stress_test"}
        SKIP_TYPES.add(original_type)
        SKIP_TYPES.update(excluded_types)

        best_type: Optional[str] = None
        best_unit: Optional[Dict] = None
        best_wait: float = float("inf")

        for res_type, res_status in capacity_status.items():
            if res_type in SKIP_TYPES:
                continue
            if res_status.get("is_icu", False):
                continue
            if res_status.get("is_full", True):
                continue
            if res_status.get("total_units", 0) == 0:
                continue

            unit = await CapacityService.allocate_unit_for_patient(
                res_type, severity_score, capacity_status, db
            )
            if unit is None:
                continue

            wait = unit.get("estimated_wait_minutes", 0)
            if wait < best_wait:
                best_wait = wait
                best_type = res_type
                best_unit = unit

        return best_type, best_unit

    @staticmethod
    async def resolve_appointments_with_overflow(
        required_appointments: List[str],
        severity_score: int,
        db: AsyncSession
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Main allocation function — 3-tier overflow strategy.

        Tier 1: Primary resource has a free unit → assign directly.
        Tier 2: Primary full → try configured overflow_fallback list (similar tests).
        Tier 3: All configured fallbacks also full → scan EVERY resource in pool
                and pick the one with the shortest wait time (greedy Hungarian-style).
                Patient is NEVER left waiting if ANY slot exists anywhere.

        Only falls to the "queue at primary" path when the ENTIRE hospital is at
        capacity for that test category.
        """
        capacity_status = await CapacityService.get_capacity_status(db)
        resolved = []
        overflow_alerts = []

        # Keep original order — Journey Agent handles sequencing.
        # DO NOT reorder here: consultation must stay first, tests in request order.
        appt_list = [a.lower().replace(" ", "_") for a in required_appointments]

        for appt_type in appt_list:
            # ICU is never a requested appointment — it's assigned at triage, not here
            if appt_type == "icu":
                continue

            res_status = capacity_status.get(appt_type, {})
            is_full = res_status.get("is_full", True)

            # ── TIER 1: Primary resource has a free slot ──────────────────────
            if not is_full:
                unit = await CapacityService.allocate_unit_for_patient(
                    appt_type, severity_score, capacity_status, db
                )
                wait = unit["estimated_wait_minutes"] if unit else 0
                resolved.append({
                    "original": appt_type,
                    "assigned": appt_type,
                    "overflowed": False,
                    "overflow_tier": None,
                    "assigned_unit": unit,
                    "waiting_time_minutes": wait,
                    "allocation_reason": (
                        f"Assigned to {unit['unit_name']} "
                        f"(load: {unit['current_load']} patients, "
                        f"severity {severity_score} prioritized)"
                        if unit else "Assigned to available slot"
                    )
                })
                # Update virtual count so subsequent appts in this batch see real state
                if appt_type in capacity_status:
                    capacity_status[appt_type]["booked"] += 1
                    capacity_status[appt_type]["free_units"] = max(0, capacity_status[appt_type]["free_units"] - 1)
                    if capacity_status[appt_type]["free_units"] == 0:
                        capacity_status[appt_type]["is_full"] = True
                continue  # ← done for this appointment

            # ── Primary is full: build list of already-tried types ────────────
            configured_fallbacks = settings.overflow_fallback.get(appt_type, [])
            assigned_alt: Optional[str] = None
            assigned_unit: Optional[Dict] = None
            assigned_tier: int = 0

            # ── TIER 2: Configured overflow_fallback list ─────────────────────
            for alt_type in configured_fallbacks:
                alt_status = capacity_status.get(alt_type, {})
                if alt_status.get("is_full", True):
                    continue
                unit = await CapacityService.allocate_unit_for_patient(
                    alt_type, severity_score, capacity_status, db
                )
                if unit is None:
                    continue  # unit-level double-booking guard
                assigned_alt  = alt_type
                assigned_unit = unit
                assigned_tier = 2
                break

            # ── TIER 3: Dynamic scan of ALL available resources ───────────────
            if not assigned_alt:
                dyn_type, dyn_unit = await CapacityService._find_any_available_resource(
                    original_type=appt_type,
                    excluded_types=configured_fallbacks,
                    capacity_status=capacity_status,
                    severity_score=severity_score,
                    db=db,
                )
                if dyn_type and dyn_unit:
                    assigned_alt  = dyn_type
                    assigned_unit = dyn_unit
                    assigned_tier = 3

            # ── Successfully rerouted (Tier 2 or 3) ──────────────────────────
            if assigned_alt and assigned_unit:
                alt_wait        = assigned_unit["estimated_wait_minutes"]
                tier_label      = (
                    "configured fallback"   if assigned_tier == 2
                    else "dynamic scan (any available resource)"
                )
                overflow_alerts.append({
                    "original_type":        appt_type,
                    "assigned_type":        assigned_alt,
                    "overflow_tier":        assigned_tier,
                    "tier_label":           tier_label,
                    "reason": (
                        f"All {res_status.get('total_units', '?')} units of "
                        f"'{appt_type}' are occupied. "
                        f"Rerouted via {tier_label} to '{assigned_alt}'."
                    ),
                    "original_wait_minutes": res_status.get("waiting_time_minutes", 0),
                    "new_wait_minutes":      alt_wait,
                    "time_saved_minutes":    max(0, res_status.get("waiting_time_minutes", 0) - alt_wait),
                    "assigned_unit":         assigned_unit,
                })
                resolved.append({
                    "original":             appt_type,
                    "assigned":             assigned_alt,
                    "overflowed":           True,
                    "overflow_tier":        assigned_tier,
                    "tier_label":           tier_label,
                    "assigned_unit":        assigned_unit,
                    "waiting_time_minutes": alt_wait,
                    "overflow_reason": (
                        f"'{appt_type}' fully occupied "
                        f"({res_status.get('total_units','?')} units busy). "
                        f"Moved to '{assigned_alt}' via {tier_label}."
                    )
                })
                # Update virtual count for the chosen alternative
                if assigned_alt in capacity_status:
                    capacity_status[assigned_alt]["booked"] += 1
                    capacity_status[assigned_alt]["free_units"] = max(
                        0, capacity_status[assigned_alt]["free_units"] - 1
                    )
                    if capacity_status[assigned_alt]["free_units"] == 0:
                        capacity_status[assigned_alt]["is_full"] = True

            else:
                # ── All 3 tiers exhausted — entire hospital is at capacity ────
                # Queue at primary with estimated wait time
                queue_pos = res_status.get("booked", 0) - res_status.get("total_units", 1) + 1
                wait      = max(queue_pos, 1) * res_status.get("duration_per_appointment", 20)
                overflow_alerts.append({
                    "original_type":        appt_type,
                    "assigned_type":        appt_type,
                    "overflow_tier":        None,
                    "tier_label":           "queued — all resources full",
                    "reason": (
                        f"All units of '{appt_type}' and every available alternative "
                        f"are currently at full capacity. Patient queued at primary resource."
                    ),
                    "original_wait_minutes": wait,
                    "new_wait_minutes":      wait,
                    "time_saved_minutes":    0,
                    "assigned_unit":         None,
                })
                resolved.append({
                    "original":             appt_type,
                    "assigned":             appt_type,
                    "overflowed":           True,
                    "overflow_tier":        None,
                    "tier_label":           "queued — all resources full",
                    "assigned_unit":        None,
                    "waiting_time_minutes": wait,
                    "overflow_reason":      f"Entire resource pool at capacity. Est. wait: ~{wait} min.",
                })

        total_wait = sum(r.get("waiting_time_minutes", 0) for r in resolved)
        for r in resolved:
            r["total_patient_wait_minutes"] = total_wait

        return resolved, overflow_alerts