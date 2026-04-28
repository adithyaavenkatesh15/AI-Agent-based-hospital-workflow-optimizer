# app/routers/notifications.py
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Optional
from app.models import NotificationInput, NotificationResult
from app.services.notification_service import NotificationService
from datetime import datetime
import uuid

router = APIRouter(prefix="/notifications", tags=["Notifications"])

# In-memory notification store (persists for session lifetime)
_notification_store: List[Dict[str, Any]] = []

def _seed_notifications():
    """Seed some initial notifications if store is empty"""
    if _notification_store:
        return
    now = datetime.now()
    seeds = [
        {
            "id": str(uuid.uuid4()),
            "type": "emergency",
            "title": "Emergency Patient Alert",
            "message": "Patient with severe chest pain admitted - Priority 1 triage required",
            "priority": "critical",
            "read": False,
            "timestamp": (now.replace(hour=now.hour, minute=max(now.minute - 5, 0))).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "type": "schedule",
            "title": "Appointment Scheduled",
            "message": "Echocardiogram scheduled for Patient P-0042 at 14:30",
            "priority": "normal",
            "read": False,
            "timestamp": (now.replace(minute=max(now.minute - 12, 0))).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "type": "system",
            "title": "System Optimization Complete",
            "message": "Genetic algorithm completed — resource utilization improved by 12%",
            "priority": "low",
            "read": True,
            "timestamp": (now.replace(hour=max(now.hour - 1, 0))).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "type": "patient",
            "title": "Patient Discharged",
            "message": "Patient ID P-0031 has been successfully discharged from Ward C",
            "priority": "normal",
            "read": True,
            "timestamp": (now.replace(hour=max(now.hour - 2, 0))).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "type": "emergency",
            "title": "Disruption Detected",
            "message": "ECG Machine #2 is offline. Patients rerouted to ECG Machine #1",
            "priority": "high",
            "read": False,
            "timestamp": (now.replace(minute=max(now.minute - 20, 0))).isoformat(),
        },
    ]
    _notification_store.extend(seeds)


@router.get("/", response_model=List[Dict[str, Any]])
async def get_notifications():
    """Get all notifications for the dashboard"""
    _seed_notifications()
    # Return newest first
    return sorted(_notification_store, key=lambda n: n["timestamp"], reverse=True)


@router.get("/unread-count")
async def get_unread_count():
    """Get count of unread notifications"""
    _seed_notifications()
    count = sum(1 for n in _notification_store if not n["read"])
    return {"unread_count": count}


@router.put("/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    """Mark a specific notification as read"""
    _seed_notifications()
    for notif in _notification_store:
        if notif["id"] == notification_id:
            notif["read"] = True
            return {"status": "success", "notification_id": notification_id}
    raise HTTPException(status_code=404, detail="Notification not found")


@router.put("/mark-all-read")
async def mark_all_notifications_read():
    """Mark all notifications as read"""
    _seed_notifications()
    for notif in _notification_store:
        notif["read"] = True
    return {"status": "success", "message": "All notifications marked as read"}


@router.post("/send", response_model=NotificationResult)
async def send_notification(notification: NotificationInput):
    """
    Send notifications to hospital staff (Assistant Agent).
    Also stores notification in the in-memory store.
    """
    try:
        result = NotificationService.send_notifications(
            notification_type=notification.notification_type,
            recipients=notification.recipients,
            message_content=notification.message_content,
            priority_level=notification.priority_level
        )

        # Store notification for display
        msg = notification.message_content
        _notification_store.append({
            "id": result.notification_id,
            "type": notification.notification_type,
            "title": msg.get("title", "Hospital Alert"),
            "message": msg.get("body", str(msg)),
            "priority": notification.priority_level,
            "read": False,
            "timestamp": datetime.now().isoformat(),
        })

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending notifications: {str(e)}")


@router.post("/dashboard/update")
async def update_dashboard(
    update_type: str,
    schedule_data: Dict[str, Any],
    metrics_data: Optional[Dict[str, Any]] = None
):
    """Update hospital dashboard with real-time information (Assistant Agent)."""
    try:
        result = NotificationService.update_dashboard(
            update_type=update_type,
            schedule_data=schedule_data,
            metrics_data=metrics_data
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating dashboard: {str(e)}")


def add_notification_to_store(notif_type: str, title: str, message: str, priority: str = "normal"):
    """Helper to add a notification from other services"""
    _notification_store.append({
        "id": str(uuid.uuid4()),
        "type": notif_type,
        "title": title,
        "message": message,
        "priority": priority,
        "read": False,
        "timestamp": datetime.now().isoformat(),
    })
