# app/routers/patients.py
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional
from app.models import PatientInput, PatientWorkflowResult
from app.services.workflow_service import WorkflowOrchestrator
from app.database import get_db, PatientRecord, WorkflowResult, AppointmentRecord
import json

router = APIRouter(prefix="/patients", tags=["Patients"])


def _serialize_patient(p: PatientRecord, workflow: WorkflowResult = None) -> dict:
    """Serialize a patient record to a frontend-compatible dict"""
    priority_texts = {1: "Emergency", 2: "Urgent", 3: "Routine"}
    # ICU patients are never "pending" - they are "monitoring"
    is_icu = bool(getattr(p, "is_icu", 0))
    location = getattr(p, "location", "waiting") or "waiting"
    if is_icu or location == "icu":
        status = "monitoring"
    elif workflow:
        status = workflow.status or "in_progress"
    elif location == "discharged":
        status = "discharged"
    elif location in ("consultation", "tests"):
        status = "in_progress"
    else:
        status = "in_progress"  # registered and active — never "pending"

    return {
        "id": p.id,
        "patient_id": p.patient_id,
        "name": p.name,
        "age": p.age,
        "gender": getattr(p, "gender", None),
        "symptoms": p.symptoms or [],
        "severity_score": p.severity_score,
        "required_appointments": p.required_appointments or [],
        "medical_history": p.medical_history or "",
        "priority": p.priority,
        # Alias for frontend compatibility
        "priority_level": p.priority,
        "priority_text": priority_texts.get(p.priority, "Routine"),
        "status": status,
        "location": location,
        "is_icu": is_icu,
        "icu_bed_id": getattr(p, "icu_bed_id", ""),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _make_json_safe(obj):
    """Recursively convert non-JSON-serializable objects (like datetime) to strings"""
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_safe(i) for i in obj]
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return obj


@router.post("/process", response_model=PatientWorkflowResult)
async def process_patient(
    patient: PatientInput,
    db: AsyncSession = Depends(get_db)
):
    """
    Process a single patient through the complete workflow:
    1. Priority Classification (Reception Agent)
    2. Optimal Scheduling (Scheduling Agent)
    3. Disruption Handling (Exception Handler Agent)
    4. Staff Notifications (Assistant Agent)
    """
    try:
        # Check for duplicate patient_id
        existing = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient.patient_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Patient ID {patient.patient_id} already exists")

        # Process patient through workflow (with DB for capacity checks)
        result = await WorkflowOrchestrator.process_patient(patient, db=db)

        # Save patient record to database
        patient_record = PatientRecord(
            patient_id=patient.patient_id,
            name=patient.name,
            gender=getattr(patient, 'gender', None),
            symptoms=patient.symptoms,
            severity_score=patient.severity_score,
            required_appointments=patient.required_appointments,
            medical_history=patient.medical_history,
            age=patient.age,
            priority=result.priority_classification.priority_level
        )
        db.add(patient_record)

        # Save workflow result
        workflow_record = WorkflowResult(
            patient_id=patient.patient_id,
            priority_classification=result.priority_classification.model_dump(mode='json'),
            scheduling_result=result.scheduling_result.model_dump(mode='json') if result.scheduling_result else None,
            disruption_handling=_make_json_safe(result.disruption_handling),
            notifications=result.notifications.model_dump(mode='json') if result.notifications else None,
            status=result.status
        )
        db.add(workflow_record)

        # ── Journey Agent: Register patient journey ──────────────────────────
        # RULE:
        #   Emergency (priority=1) → ICU directly (bypass consultation)
        #   Non-emergency           → Consultation first, then SEQUENTIAL tests
        from app.services.patient_journey_service import PatientJourneyAgent as _PJA

        priority_level = result.priority_classification.priority_level

        required_tests_for_journey = [
            t for t in patient.required_appointments
            if t not in ("consultation", "icu")
        ]

        await _PJA.register_patient_journey(
            patient_id=patient.patient_id,
            required_tests=required_tests_for_journey,
            priority=priority_level,
            severity_score=patient.severity_score,
            db=db,
        )

        # Update patient location
        p_rec_res = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient.patient_id)
        )
        p_rec = p_rec_res.scalar_one_or_none()
        if p_rec:
            p_rec.location = "icu" if priority_level == 1 else "consultation"

        # ── Auto-register portal credentials (username=patient_id, password=1234) ──
        try:
            from app.database import PatientAuth, _simple_hash
            existing_auth = await db.execute(
                select(PatientAuth).where(PatientAuth.patient_id == patient.patient_id)
            )
            if not existing_auth.scalar_one_or_none():
                auto_auth = PatientAuth(
                    patient_id=patient.patient_id,
                    username=patient.patient_id,
                    password_hash=_simple_hash("1234"),
                )
                db.add(auto_auth)
        except Exception:
            pass  # Non-critical — credentials can be set manually

        await db.commit()

        # Trigger notification in notification store
        try:
            from app.routers.notifications import add_notification_to_store
            priority_label = {1: "EMERGENCY", 2: "URGENT", 3: "ROUTINE"}.get(
                result.priority_classification.priority_level, "ROUTINE"
            )
            ntype = "emergency" if result.priority_classification.priority_level == 1 else "patient"
            npriority = "critical" if result.priority_classification.priority_level == 1 else "normal"
            add_notification_to_store(
                notif_type=ntype,
                title=f"{priority_label} Patient Registered",
                message=f"{patient.name} (ID: {patient.patient_id}) registered. "
                        f"Priority: {priority_label}. "
                        f"Est. wait: {result.priority_classification.estimated_wait_time_minutes} min.",
                priority=npriority
            )

            # Send overflow notifications if any
            overflow_details = (result.disruption_handling or {}).get("overflow_details", [])
            for overflow in overflow_details:
                tier_label = overflow.get("tier_label", "rerouted")
                add_notification_to_store(
                    notif_type="overflow",
                    title="⚠️ Capacity Overflow — Resource Reallocated",
                    message=(
                        f"Patient {patient.name} (ID: {patient.patient_id}): "
                        f"'{overflow['original_type']}' is fully booked. "
                        f"Redirected to '{overflow['assigned_type']}' "
                        f"via {tier_label}. "
                        f"Reason: {overflow['reason']}"
                    ),
                    priority="warning"
                )
        except Exception:
            pass  # Non-critical

        return result

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error processing patient: {str(e)}")


@router.post("/process-batch", response_model=List[PatientWorkflowResult])
async def process_multiple_patients(
    patients: List[PatientInput],
    db: AsyncSession = Depends(get_db)
):
    """Process multiple patients through the workflow in batch."""
    try:
        results = await WorkflowOrchestrator.process_multiple_patients(patients, db=db)

        for i, result in enumerate(results):
            patient = patients[i]

            patient_record = PatientRecord(
                patient_id=patient.patient_id,
                name=patient.name,
                gender=getattr(patient, 'gender', None),
                symptoms=patient.symptoms,
                severity_score=patient.severity_score,
                required_appointments=patient.required_appointments,
                medical_history=patient.medical_history,
                age=patient.age,
                priority=result.priority_classification.priority_level
            )
            db.add(patient_record)

            workflow_record = WorkflowResult(
                patient_id=patient.patient_id,
                priority_classification=result.priority_classification.model_dump(mode='json'),
                scheduling_result=result.scheduling_result.model_dump(mode='json') if result.scheduling_result else None,
                disruption_handling=_make_json_safe(result.disruption_handling),
                notifications=result.notifications.model_dump(mode='json') if result.notifications else None,
                status=result.status
            )
            db.add(workflow_record)

        await db.commit()
        return results

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error processing patients: {str(e)}")


@router.get("/", response_model=List[dict])
async def get_all_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    priority: Optional[int] = Query(None, description="Filter by priority level (1, 2, or 3)"),
    db: AsyncSession = Depends(get_db)
):
    """Get all patients with optional priority filter, newest first"""
    try:
        query = select(PatientRecord).order_by(desc(PatientRecord.created_at))
        if priority is not None:
            query = query.where(PatientRecord.priority == priority)
        query = query.offset(skip).limit(limit)

        result = await db.execute(query)
        patients = result.scalars().all()

        # Fetch workflow results for status
        patient_ids = [p.patient_id for p in patients]
        workflows_result = await db.execute(
            select(WorkflowResult).where(WorkflowResult.patient_id.in_(patient_ids))
        )
        workflows = {w.patient_id: w for w in workflows_result.scalars().all()}

        return [_serialize_patient(p, workflows.get(p.patient_id)) for p in patients]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching patients: {str(e)}")


@router.get("/stats")
async def get_patient_stats(db: AsyncSession = Depends(get_db)):
    """Get patient statistics by priority"""
    try:
        result = await db.execute(select(PatientRecord))
        patients = result.scalars().all()
        total = len(patients)
        emergency = sum(1 for p in patients if p.priority == 1)
        urgent = sum(1 for p in patients if p.priority == 2)
        routine = sum(1 for p in patients if p.priority == 3)
        return {
            "total": total,
            "emergency": emergency,
            "urgent": urgent,
            "routine": routine,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")


@router.get("/{patient_id}", response_model=dict)
async def get_patient(
    patient_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific patient by ID"""
    try:
        result = await db.execute(
            select(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        patient = result.scalar_one_or_none()

        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        wf_result = await db.execute(
            select(WorkflowResult).where(WorkflowResult.patient_id == patient_id)
        )
        workflow = wf_result.scalar_one_or_none()

        return _serialize_patient(patient, workflow)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching patient: {str(e)}")


@router.get("/{patient_id}/workflow", response_model=dict)
async def get_patient_workflow_results(
    patient_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get complete workflow results for a specific patient"""
    try:
        result = await db.execute(
            select(WorkflowResult).where(WorkflowResult.patient_id == patient_id)
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow results not found")

        return {
            "id": workflow.id,
            "patient_id": workflow.patient_id,
            "priority_classification": workflow.priority_classification,
            "scheduling_result": workflow.scheduling_result,
            "disruption_handling": workflow.disruption_handling,
            "notifications": workflow.notifications,
            "status": workflow.status,
            "processed_at": workflow.processed_at.isoformat() if workflow.processed_at else None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workflow results: {str(e)}")