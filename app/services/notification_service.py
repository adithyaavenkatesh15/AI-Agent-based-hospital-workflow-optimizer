# app/services/notification_service.py
from typing import List, Dict, Any
from datetime import datetime
from app.models import NotificationResult


class NotificationService:
    """Service for staff notifications (Assistant Agent)"""
    
    @staticmethod
    def send_notifications(
        notification_type: str,
        recipients: List[str],
        message_content: Dict[str, Any],
        priority_level: str = "normal"
    ) -> NotificationResult:
        """
        Send notifications to hospital staff via multiple channels.
        """
        
        delivery_results = []
        failed_deliveries = []
        
        for recipient in recipients:
            # Simulate notification delivery
            result = {
                "recipient": recipient,
                "channel": "mobile_app",
                "status": "delivered",
                "timestamp": datetime.now().isoformat(),
                "success": True
            }
            
            # Simulate occasional failures (5% failure rate)
            import random
            if random.random() < 0.05:
                result["status"] = "failed"
                result["success"] = False
                result["error"] = "Recipient unavailable"
                failed_deliveries.append(result)
            
            delivery_results.append(result)
        
        notification_id = f"NOTIF_{len(delivery_results)}_{notification_type}_{int(datetime.now().timestamp())}"
        
        return NotificationResult(
            notifications_sent=len(delivery_results),
            delivery_status=delivery_results,
            failed_deliveries=failed_deliveries,
            notification_id=notification_id
        )
    
    @staticmethod
    def update_dashboard(
        update_type: str,
        schedule_data: Dict[str, Any],
        metrics_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Update hospital dashboard with real-time information.
        Now includes capacity status and overflow alerts.
        """

        if metrics_data is None:
            metrics_data = {}

        capacity_status = metrics_data.get("capacity_status", {})
        overflow_count = metrics_data.get("overflow_count", 0)

        # Build capacity summary for dashboard panels
        capacity_summary = {}
        for resource, info in capacity_status.items():
            if isinstance(info, dict):
                capacity_summary[resource] = {
                    "capacity": info.get("capacity", 0),
                    "booked": info.get("booked", 0),
                    "available": info.get("available", 0),
                    "is_full": info.get("is_full", False),
                    "utilization_percent": round(
                        info.get("booked", 0) / max(info.get("capacity", 1), 1) * 100, 1
                    )
                }

        affected_views = [
            "patient_queue",
            "resource_schedule",
            "staff_assignments",
            "metrics_dashboard"
        ]
        if overflow_count > 0:
            affected_views.append("capacity_alerts")
            affected_views.append("overflow_log")

        return {
            "dashboard_updated": True,
            "update_timestamp": datetime.now().isoformat(),
            "affected_views": affected_views,
            "overflow_alerts": schedule_data.get("overflow_alerts", []),
            "overflow_count": overflow_count,
            "capacity_status": capacity_summary,
            "real_time_metrics": {
                "current_queue_length": 12,
                "average_wait_time": 23.5,
                "resource_utilization": 0.78,
                "staff_availability": 0.85,
                **metrics_data
            }
        }
    
    @staticmethod
    def generate_notification_message(
        patient_data: Dict[str, Any],
        priority: int,
        schedule: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate notification message content"""
        
        priority_labels = {
            1: "EMERGENCY",
            2: "URGENT",
            3: "ROUTINE"
        }
        
        return {
            "title": f"{priority_labels.get(priority, 'UNKNOWN')} Patient Alert",
            "patient_id": patient_data.get("patient_id"),
            "patient_name": patient_data.get("name"),
            "priority": priority,
            "symptoms": patient_data.get("symptoms", []),
            "scheduled_time": schedule.get("scheduled_time"),
            "assigned_resource": schedule.get("assigned_resource"),
            "action_required": "Immediate attention" if priority == 1 else "Standard protocol",
            "timestamp": datetime.now().isoformat()
        }
