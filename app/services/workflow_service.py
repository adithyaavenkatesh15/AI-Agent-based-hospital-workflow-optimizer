# app/services/workflow_service.py
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import PatientInput, PatientWorkflowResult
from app.services.priority_service import PriorityService
from app.services.scheduling_service import SchedulingService
from app.services.disruption_service import DisruptionService
from app.services.notification_service import NotificationService
from app.services.capacity_service import CapacityService


class WorkflowOrchestrator:
    """Orchestrates the complete patient workflow through all agents"""

    @staticmethod
    async def process_patient(
        patient_data: PatientInput,
        db: Optional[AsyncSession] = None
    ) -> PatientWorkflowResult:
        """
        Process a patient through the complete 4-agent workflow:
        1. Reception Agent   - Priority Classification
        2. Scheduling Agent  - Capacity Check + Optimal Assignment (with overflow)
        3. Exception Agent   - Disruption Management
        4. Assistant Agent   - Notifications (including overflow alerts)
        """

        # AGENT 1: Reception Agent - Priority Classification
        priority_classification = PriorityService.get_priority_classification(
            symptoms=patient_data.symptoms,
            severity_score=patient_data.severity_score,
            medical_history=patient_data.medical_history,
            patient_age=patient_data.age
        )

        patient_dict = patient_data.model_dump()
        patient_dict['priority'] = priority_classification.priority_level

        # AGENT 2: Scheduling Agent with Capacity Overflow logic
        # NOTE: Consultation is ALWAYS the first step for non-emergency patients.
        # The Journey Agent enforces the consultation-first rule; here we just
        # ensure the appointments list is properly formed.
        overflow_alerts = []
        resolved_appointments = []

        # Ensure consultation is always in required_appointments (non-emergency)
        is_emergency = (priority_classification.priority_level == 1)
        appts_with_consult = list(patient_data.required_appointments)
        if not is_emergency and "consultation" not in appts_with_consult:
            appts_with_consult = ["consultation"] + appts_with_consult

        if db is not None:
            resolved_appointments, overflow_alerts = (
                await CapacityService.resolve_appointments_with_overflow(
                    required_appointments=appts_with_consult,
                    severity_score=patient_data.severity_score,
                    db=db
                )
            )
            patient_dict['required_appointments'] = [r['assigned'] for r in resolved_appointments]
        else:
            patient_dict['required_appointments'] = appts_with_consult

        available_resources = SchedulingService.get_mock_resources()

        scheduling_result = SchedulingService.optimize_patient_assignment(
            patient_requirements=[patient_dict],
            available_resources=available_resources,
            current_schedules={},
            priority_weights=None
        )

        # AGENT 3: Exception Handling Agent
        disruption_handling = {
            "disruptions_detected": len(overflow_alerts) > 0,
            "fallback_applied": any(a.get("overflowed") for a in resolved_appointments),
            "overflow_details": overflow_alerts,
            "resolved_appointments": resolved_appointments,
            "rl_optimization": None
        }

        # AGENT 4: Assistant Agent - Notifications
        notifications = None
        dashboard_update = None

        if scheduling_result.optimal_assignments:
            assignment = scheduling_result.optimal_assignments[0]

            message_content = NotificationService.generate_notification_message(
                patient_data=patient_dict,
                priority=priority_classification.priority_level,
                schedule={
                    "scheduled_time": assignment.assigned_resource.get('slot', {}).get('time'),
                    "assigned_resource": assignment.assigned_resource
                }
            )

            if overflow_alerts:
                message_content["overflow_alerts"] = overflow_alerts
                message_content["overflow_occurred"] = True
                overflow_summary = "; ".join(
                    f"{a['original_type']} → {a['assigned_type']} "
                    f"[{a.get('tier_label', 'rerouted')}]"
                    for a in overflow_alerts
                )
                message_content["overflow_summary"] = overflow_summary

            recipients = ["duty_doctor", "triage_nurse"]
            if priority_classification.priority_level == 1:
                recipients.extend(["emergency_team", "department_head"])
            if overflow_alerts:
                recipients.append("resource_coordinator")

            notifications = NotificationService.send_notifications(
                notification_type="patient_scheduled",
                recipients=recipients,
                message_content=message_content,
                priority_level="critical" if priority_classification.priority_level == 1 else "normal"
            )

            capacity_status = {}
            if db is not None:
                capacity_status = await CapacityService.get_capacity_status(db)

            dashboard_update = NotificationService.update_dashboard(
                update_type="schedule",
                schedule_data={
                    "patient_id": patient_data.patient_id,
                    "assignment": assignment.model_dump(),
                    "overflow_alerts": overflow_alerts,
                    "resolved_appointments": resolved_appointments,
                    "capacity_status": capacity_status
                },
                metrics_data={
                    "overflow_count": len(overflow_alerts),
                    "capacity_status": capacity_status
                }
            )

        return PatientWorkflowResult(
            patient_id=patient_data.patient_id,
            patient_name=patient_data.name,
            priority_classification=priority_classification,
            scheduling_result=scheduling_result,
            disruption_handling=disruption_handling,
            notifications=notifications,
            status="completed",
            processed_at=datetime.now()
        )

    @staticmethod
    async def process_multiple_patients(
        patients: list[PatientInput],
        db: Optional[AsyncSession] = None
    ) -> list[PatientWorkflowResult]:
        """Process multiple patients through the workflow"""
        results = []
        for patient in patients:
            result = await WorkflowOrchestrator.process_patient(patient, db=db)
            results.append(result)
        return results