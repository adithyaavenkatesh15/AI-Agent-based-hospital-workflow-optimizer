# app/routers/resource_management.py
"""
Resource Management — Staff marks resources as unavailable
  POST /resources/unavailable          — mark a resource/unit as unavailable
  GET  /resources/unavailable          — list active unavailability windows
  DELETE /resources/unavailable/{id}  — cancel an unavailability window
  GET  /resources/availability-check   — check if a resource is free at a given time
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.database import get_db, ResourceUnavailability
from app.config import settings

router = APIRouter(prefix="/resources", tags=["Resource Management"])


class UnavailabilityRequest(BaseModel):
    resource_type: str          # e.g. 'ecg', 'consultation'
    unit_id: Optional[str] = None    # None = all units of this type
    unit_name: Optional[str] = None
    unavailable_from: datetime
    unavailable_until: datetime
    reason: Optional[str] = None
    marked_by: Optional[str] = "staff"


# ── Mark unavailable ──────────────────────────────────────────────────────────
@router.post("/unavailable")
async def mark_unavailable(body: UnavailabilityRequest, db: AsyncSession = Depends(get_db)):
    """Staff marks a resource/unit as unavailable for a time period."""
    if body.resource_type not in settings.resource_pool:
        raise HTTPException(status_code=400, detail=f"Unknown resource type: {body.resource_type}")
    if body.unavailable_until <= body.unavailable_from:
        raise HTTPException(status_code=400, detail="unavailable_until must be after unavailable_from")

    record = ResourceUnavailability(
        resource_type=body.resource_type,
        unit_id=body.unit_id,
        unit_name=body.unit_name or body.unit_id or "All units",
        unavailable_from=body.unavailable_from,
        unavailable_until=body.unavailable_until,
        reason=body.reason,
        marked_by=body.marked_by,
        active=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # Notify staff
    try:
        from app.routers.notifications import add_notification_to_store
        unit_desc = f"Unit: {body.unit_name or body.unit_id}" if body.unit_id else "All units"
        add_notification_to_store(
            notif_type="system",
            title=f"⚠️ Resource Unavailable: {body.resource_type.replace('_',' ').title()}",
            message=(
                f"{unit_desc} of '{body.resource_type}' marked unavailable "
                f"from {body.unavailable_from.strftime('%H:%M')} to {body.unavailable_until.strftime('%H:%M')}. "
                f"Reason: {body.reason or 'Not specified'}. Marked by: {body.marked_by}."
            ),
            priority="warning",
        )
    except Exception:
        pass

    # ── Trigger auto-rescheduling of affected appointments ──────────────────
    reschedule_result = {}
    try:
        from app.services.disruption_handler import handle_resource_unavailability
        reschedule_result = await handle_resource_unavailability(
            db=db,
            resource_type=body.resource_type,
            unit_id=body.unit_id,
            unavailable_from=body.unavailable_from,
            unavailable_until=body.unavailable_until,
            reason=body.reason or "Not specified",
            marked_by=body.marked_by or "staff",
            unit_name=body.unit_name,
        )
    except Exception as _e:
        reschedule_result = {"error": str(_e)}

    return {
        "id": record.id,
        "resource_type": record.resource_type,
        "unit_id": record.unit_id,
        "unit_name": record.unit_name,
        "unavailable_from": record.unavailable_from.isoformat(),
        "unavailable_until": record.unavailable_until.isoformat(),
        "reason": record.reason,
        "marked_by": record.marked_by,
        "message": f"Resource '{body.resource_type}' marked unavailable until {body.unavailable_until.strftime('%H:%M')}.",
        "auto_rescheduled": reschedule_result,
    }


# ── List active unavailability windows ───────────────────────────────────────
@router.get("/unavailable")
async def list_unavailable(db: AsyncSession = Depends(get_db)):
    """Get all currently active unavailability windows."""
    now = datetime.now()
    result = await db.execute(
        select(ResourceUnavailability).where(
            ResourceUnavailability.active == 1,
            ResourceUnavailability.unavailable_until >= now,
        ).order_by(ResourceUnavailability.unavailable_from)
    )
    records = result.scalars().all()

    return [
        {
            "id": r.id,
            "resource_type": r.resource_type,
            "resource_label": r.resource_type.replace("_", " ").title(),
            "unit_id": r.unit_id,
            "unit_name": r.unit_name,
            "unavailable_from": r.unavailable_from.isoformat(),
            "unavailable_until": r.unavailable_until.isoformat(),
            "from_display": r.unavailable_from.strftime("%I:%M %p"),
            "until_display": r.unavailable_until.strftime("%I:%M %p"),
            "reason": r.reason,
            "marked_by": r.marked_by,
            "is_active_now": r.unavailable_from <= now <= r.unavailable_until,
        }
        for r in records
    ]


# ── Cancel unavailability ─────────────────────────────────────────────────────
@router.delete("/unavailable/{record_id}")
async def cancel_unavailability(record_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ResourceUnavailability).where(ResourceUnavailability.id == record_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Unavailability record not found")
    record.active = 0
    await db.commit()
    return {"success": True, "message": "Unavailability window cancelled. Resource is now available."}


# ── Check availability at a specific time ────────────────────────────────────
@router.get("/availability-check")
async def check_availability(
    resource_type: str,
    check_time: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Check if a resource is available at a given time."""
    if resource_type not in settings.resource_pool:
        raise HTTPException(status_code=400, detail=f"Unknown resource type: {resource_type}")

    try:
        t = datetime.fromisoformat(check_time) if check_time else datetime.now()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid check_time format. Use ISO format.")

    pool = settings.resource_pool.get(resource_type, {})
    dur = duration_minutes or pool.get("duration_minutes", 20)
    end_t = t + timedelta(minutes=dur)

    result = await db.execute(
        select(ResourceUnavailability).where(
            ResourceUnavailability.resource_type == resource_type,
            ResourceUnavailability.active == 1,
            ResourceUnavailability.unavailable_from < end_t,
            ResourceUnavailability.unavailable_until > t,
        )
    )
    conflicts = result.scalars().all()

    return {
        "resource_type": resource_type,
        "check_time": t.isoformat(),
        "check_end": end_t.isoformat(),
        "available": len(conflicts) == 0,
        "conflicts": [
            {
                "id": c.id,
                "unit_id": c.unit_id,
                "unit_name": c.unit_name,
                "from": c.unavailable_from.isoformat(),
                "until": c.unavailable_until.isoformat(),
                "reason": c.reason,
            }
            for c in conflicts
        ],
    }


# ── Full resource pool status (combined: capacity + unavailability) ───────────
@router.get("/status")
async def get_full_resource_status(db: AsyncSession = Depends(get_db)):
    """Get all resources with their capacity info and current unavailability."""
    now = datetime.now()
    unav_res = await db.execute(
        select(ResourceUnavailability).where(
            ResourceUnavailability.active == 1,
            ResourceUnavailability.unavailable_from <= now,
            ResourceUnavailability.unavailable_until >= now,
        )
    )
    active_unavail = unav_res.scalars().all()
    unavail_by_type: dict = {}
    for u in active_unavail:
        unavail_by_type.setdefault(u.resource_type, []).append({
            "unit_id": u.unit_id,
            "unit_name": u.unit_name,
            "reason": u.reason,
            "until": u.unavailable_until.strftime("%I:%M %p"),
        })

    result = {}
    for res_type, pool in settings.resource_pool.items():
        if pool.get("units", 0) == 0:
            continue
        result[res_type] = {
            "resource_type": res_type,
            "label": res_type.replace("_", " ").title(),
            "total_units": pool.get("units", 0),
            "duration_minutes": pool.get("duration_minutes", 20),
            "is_icu": pool.get("is_icu", False),
            "units_info": pool.get("units_info", []),
            "unavailable_now": unavail_by_type.get(res_type, []),
            "has_unavailability": res_type in unavail_by_type,
        }
    return result