# app/routers/patient_portal.py
"""
Patient Portal — Secure login + personal schedule dashboard
  POST /portal/login                — patient login
  POST /portal/logout               — patient logout (session clear)
  GET  /portal/me                   — get logged-in patient info
  GET  /portal/schedule             — patient's personal test schedule (with times)
  GET  /portal/notifications        — patient-specific notifications
  POST /portal/register-credentials — admin creates patient login (used by reception)
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel
import uuid

from app.database import get_db, PatientRecord, AppointmentRecord, PatientAuth, PatientNotification, _simple_hash, _check_hash
from app.config import settings

router = APIRouter(prefix="/portal", tags=["Patient Portal"])

# ── Simple in-memory session store (production: use Redis / JWT) ──────────────
_sessions: dict = {}  # token -> patient_id


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterCredentials(BaseModel):
    patient_id: str
    username: str       # phone number or email
    password: str


def _get_patient_id_from_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    pid = _sessions.get(token)
    if not pid:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return pid


# ── Login ─────────────────────────────────────────────────────────────────────
@router.post("/login")
async def patient_login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Patient login with username + password."""
    result = await db.execute(
        select(PatientAuth).where(PatientAuth.username == body.username)
    )
    auth = result.scalar_one_or_none()
    if not auth or not _check_hash(body.password, auth.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Update last_login
    auth.last_login = datetime.now()
    await db.commit()

    token = str(uuid.uuid4())
    _sessions[token] = auth.patient_id

    # Get patient name for response
    p_res = await db.execute(
        select(PatientRecord).where(PatientRecord.patient_id == auth.patient_id)
    )
    patient = p_res.scalar_one_or_none()

    return {
        "token": token,
        "patient_id": auth.patient_id,
        "patient_name": patient.name if patient else auth.patient_id,
        "message": "Login successful",
    }


# ── Logout ────────────────────────────────────────────────────────────────────
@router.post("/logout")
async def patient_logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        _sessions.pop(token, None)
    return {"message": "Logged out successfully"}


# ── Me ────────────────────────────────────────────────────────────────────────
@router.get("/me")
async def get_me(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    patient_id = _get_patient_id_from_token(authorization)
    result = await db.execute(
        select(PatientRecord).where(PatientRecord.patient_id == patient_id)
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    plabel = {1: "Emergency", 2: "Urgent", 3: "Routine"}
    return {
        "patient_id": patient.patient_id,
        "name": patient.name,
        "age": patient.age,
        "gender": patient.gender,
        "severity_score": patient.severity_score,
        "priority": patient.priority,
        "priority_label": plabel.get(patient.priority, "Routine"),
        "location": patient.location or "waiting",
        "is_icu": bool(getattr(patient, "is_icu", 0)),
        "icu_bed_id": getattr(patient, "icu_bed_id", ""),
        "symptoms": patient.symptoms or [],
        "medical_history": patient.medical_history or "",
    }


# ── Patient Schedule (with start + end times) ─────────────────────────────────
@router.get("/schedule")
async def get_patient_schedule(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Return patient's complete test schedule with start/end times and location."""
    patient_id = _get_patient_id_from_token(authorization)

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)

    p_res = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == patient_id))
    patient = p_res.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    a_res = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.patient_id == patient_id,
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
        ).order_by(
            AppointmentRecord.sequence_order.asc().nullslast(),
            AppointmentRecord.id.asc()
        )
    )
    appts = a_res.scalars().all()

    status_labels = {
        "scheduled": "Upcoming",
        "in_progress": "In Progress",
        "done": "Completed",
        "cancelled": "Cancelled",
        "monitoring": "Under Monitoring",
    }
    type_labels = {
        "consultation":  "Consultation",
        "ecg":           "ECG",
        "echocardiogram":"Echocardiogram",
        "tmt":           "TMT Stress Test",
        "angiogram":     "Coronary Angiogram",
        "troponin_test": "Troponin Blood Test",
        "cardiac_ct":    "Cardiac CT Scan",
        "blood_test":    "Blood Test",
        "icu":           "ICU Monitoring",
    }

    # ── Compute SEQUENTIAL start/end times ──────────────────────────────────
    # ICU runs concurrently (always first, no end time).
    # All other tests run ONE AT A TIME per patient — each starts when
    # the previous one ends. We find the earliest non-ICU anchor time and
    # chain every test after it in sequence_order.

    # Find anchor: the scheduled_time of the first non-ICU appointment
    non_icu_appts = [a for a in appts if not settings.resource_pool.get(a.appointment_type, {}).get("is_icu")]
    anchor_time: Optional[datetime] = non_icu_appts[0].scheduled_time if non_icu_appts else datetime.now()

    # Build a sequential timeline: cursor advances by each test's duration
    sequential_cursor: Optional[datetime] = None

    schedule = []
    for a in appts:
        res = a.assigned_resource or {}
        pool = settings.resource_pool.get(a.appointment_type, {})
        duration = pool.get("duration_minutes", 20)
        is_icu   = pool.get("is_icu", False)

        if is_icu:
            # ICU monitoring: use actual start time, no end time
            start_time = a.scheduled_time
            end_time_val = None
        else:
            # Sequential chaining — every non-ICU test starts after the previous ends
            if sequential_cursor is None:
                start_time = anchor_time or datetime.now()
            else:
                start_time = sequential_cursor
            end_time_val = start_time + timedelta(minutes=duration)
            sequential_cursor = end_time_val  # next test starts here

        locked = bool(res.get("locked"))
        status = a.status
        if is_icu and status == "in_progress":
            status = "monitoring"

        schedule.append({
            "appointment_id":    a.id,
            "test_name":         type_labels.get(a.appointment_type, a.appointment_type.replace("_", " ").title()),
            "test_type":         a.appointment_type,
            "status":            status,
            "status_label":      status_labels.get(status, status.replace("_", " ").title()),
            "scheduled_start":   start_time.strftime("%I:%M %p") if start_time else "TBD",
            "scheduled_end":     end_time_val.strftime("%I:%M %p") if end_time_val else ("Indefinite" if is_icu else "TBD"),
            "scheduled_start_iso": start_time.isoformat() if start_time else None,
            "scheduled_end_iso": end_time_val.isoformat() if end_time_val else None,
            "duration_minutes":  duration,
            "location":          res.get("unit_name", "TBD"),
            "unit_id":           res.get("unit_id", ""),
            "sequence_order":    getattr(a, "sequence_order", None),
            "locked":            locked,
            "lock_reason":       ("Awaiting doctor prescription" if res.get("is_icu") and locked
                                  else "Awaiting consultation completion" if locked else ""),
            "is_icu":            is_icu,
            "rescheduled":       bool(getattr(a, "rescheduled_from", None)),
            "reschedule_reason": getattr(a, "reschedule_reason", ""),
        })

    plabel = {1: "Emergency", 2: "Urgent", 3: "Routine"}
    return {
        "patient_id":      patient_id,
        "patient_name":    patient.name,
        "date":            today_start.strftime("%A, %B %d, %Y"),
        "priority":        patient.priority,
        "priority_label":  plabel.get(patient.priority, "Routine"),
        "location":        patient.location or "waiting",
        "is_icu":          bool(getattr(patient, "is_icu", 0)),
        "total_tests":     len(schedule),
        "completed_tests": sum(1 for s in schedule if s["status"] == "done"),
        "schedule":        schedule,
    }


# ── Patient Notifications ─────────────────────────────────────────────────────
@router.get("/notifications")
async def get_patient_notifications(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Patient-specific notifications about their schedule."""
    patient_id = _get_patient_id_from_token(authorization)

    p_res = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == patient_id))
    patient = p_res.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)

    a_res = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.patient_id == patient_id,
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
        ).order_by(AppointmentRecord.scheduled_time)
    )
    appts = a_res.scalars().all()

    notifications = []
    for a in appts:
        res = a.assigned_resource or {}
        if a.status == "in_progress":
            notifications.append({
                "type": "now",
                "icon": "🟢",
                "title": f"Now: {a.appointment_type.replace('_',' ').title()}",
                "message": f"You are currently at {res.get('unit_name', 'assigned unit')}.",
                "time": a.scheduled_time.strftime("%I:%M %p") if a.scheduled_time else "",
            })
        elif a.status == "scheduled" and not res.get("locked"):
            pool = settings.resource_pool.get(a.appointment_type, {})
            dur  = pool.get("duration_minutes", 20)
            notifications.append({
                "type": "upcoming",
                "icon": "🕐",
                "title": f"Upcoming: {a.appointment_type.replace('_',' ').title()}",
                "message": f"Scheduled at {res.get('unit_name', 'TBD')}. Duration ~{dur} min.",
                "time": a.scheduled_time.strftime("%I:%M %p") if a.scheduled_time else "TBD",
            })
        elif getattr(a, "rescheduled_from", None):
            notifications.append({
                "type": "rescheduled",
                "icon": "🔄",
                "title": f"Rescheduled: {a.appointment_type.replace('_',' ').title()}",
                "message": f"Your appointment was rescheduled. Reason: {getattr(a, 'reschedule_reason', 'emergency priority')}.",
                "time": a.scheduled_time.strftime("%I:%M %p") if a.scheduled_time else "TBD",
            })
        elif a.status == "done":
            notifications.append({
                "type": "done",
                "icon": "✅",
                "title": f"Completed: {a.appointment_type.replace('_',' ').title()}",
                "message": "This test has been completed.",
                "time": (res.get("completed_at", "")[:16].replace("T", " ") if res.get("completed_at") else ""),
            })

    return {
        "patient_id":   patient_id,
        "patient_name": patient.name,
        "notifications": notifications,
        "count": len(notifications),
    }


# ── Print Schedule (PDF-ready JSON for reception) ────────────────────────────
@router.get("/print-schedule/{patient_id}")
async def print_schedule(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Reception-facing endpoint: returns full printable schedule for a patient.
    No auth required (used by reception desk staff).
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)

    p_res = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == patient_id))
    patient = p_res.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    a_res = await db.execute(
        select(AppointmentRecord).where(
            AppointmentRecord.patient_id == patient_id,
            AppointmentRecord.scheduled_time >= today_start,
            AppointmentRecord.scheduled_time < today_end,
        ).order_by(
            AppointmentRecord.sequence_order.asc().nullslast(),
            AppointmentRecord.id.asc()
        )
    )
    appts = a_res.scalars().all()

    type_labels = {
        "consultation":  "Consultation", "ecg": "ECG",
        "echocardiogram": "Echocardiogram", "tmt": "TMT Stress Test",
        "angiogram": "Coronary Angiogram", "troponin_test": "Troponin Blood Test",
        "cardiac_ct": "Cardiac CT Scan", "blood_test": "Blood Test",
        "icu": "ICU Monitoring",
    }
    plabel = {1: "Emergency", 2: "Urgent", 3: "Routine"}

    # Sequential time computation (same logic as /portal/schedule)
    _non_icu_p = [a for a in appts if not settings.resource_pool.get(a.appointment_type, {}).get("is_icu")]
    _anchor_p: Optional[datetime] = _non_icu_p[0].scheduled_time if _non_icu_p else datetime.now()
    _cursor_p: Optional[datetime] = None

    tests = []
    for a in appts:
        res = a.assigned_resource or {}
        pool = settings.resource_pool.get(a.appointment_type, {})
        duration = pool.get("duration_minutes", 20)
        is_icu = pool.get("is_icu", False)

        if is_icu:
            start_time   = a.scheduled_time
            end_time_val = None
        else:
            start_time   = _cursor_p if _cursor_p is not None else (_anchor_p or datetime.now())
            end_time_val = start_time + timedelta(minutes=duration)
            _cursor_p    = end_time_val

        tests.append({
            "seq":          (a.sequence_order or 0),
            "test_name":    type_labels.get(a.appointment_type, a.appointment_type.replace("_", " ").title()),
            "location":     res.get("unit_name", "TBD"),
            "start_time":   start_time.strftime("%I:%M %p") if start_time else "TBD",
            "end_time":     end_time_val.strftime("%I:%M %p") if end_time_val else ("Ongoing" if is_icu else "TBD"),
            "duration_min": duration,
            "status":       a.status,
            "rescheduled":  bool(getattr(a, "rescheduled_from", None)),
        })

    return {
        "hospital_name":   settings.hospital_name,
        "department":      settings.department,
        "print_date":      datetime.now().strftime("%B %d, %Y %I:%M %p"),
        "patient_id":      patient.patient_id,
        "patient_name":    patient.name,
        "patient_age":     patient.age,
        "patient_gender":  patient.gender or "—",
        "priority_label":  plabel.get(patient.priority, "Routine"),
        "severity_score":  patient.severity_score,
        "date":            today_start.strftime("%A, %B %d, %Y"),
        "tests":           tests,
        "total_tests":     len(tests),
        "instructions":    [
            "Please arrive 5 minutes before your scheduled appointment time.",
            "Carry this schedule and your hospital ID card at all times.",
            "If you have any concerns, please approach the nearest nurse station.",
            "In case of an emergency, press the call button in any room.",
        ],
    }


# ── Patient Notifications (portal-facing) ────────────────────────────────────
@router.get("/my-notifications")
async def get_my_notifications(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Return unread + recent notifications for the logged-in patient."""
    patient_id = _get_patient_id_from_token(authorization)

    result = await db.execute(
        select(PatientNotification)
        .where(PatientNotification.patient_id == patient_id)
        .order_by(PatientNotification.created_at.desc())
        .limit(30)
    )
    notifs = result.scalars().all()

    return {
        "patient_id": patient_id,
        "notifications": [
            {
                "id":         n.id,
                "type":       n.notif_type,
                "title":      n.title,
                "message":    n.message,
                "read":       bool(n.read),
                "created_at": n.created_at.strftime("%I:%M %p, %b %d") if n.created_at else "",
            }
            for n in notifs
        ],
        "unread_count": sum(1 for n in notifs if not n.read),
    }


@router.put("/my-notifications/{notif_id}/read")
async def mark_my_notification_read(
    notif_id: int,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    patient_id = _get_patient_id_from_token(authorization)
    result = await db.execute(
        select(PatientNotification).where(
            PatientNotification.id == notif_id,
            PatientNotification.patient_id == patient_id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif:
        notif.read = 1
        await db.commit()
    return {"success": True}


# ── Register credentials (used by reception staff) ───────────────────────────
@router.post("/register-credentials")
async def register_patient_credentials(
    body: RegisterCredentials,
    db: AsyncSession = Depends(get_db),
):
    """
    Reception creates login for a patient (called after patient registration).
    Default password = patient DOB or phone; patient can change later.
    """
    # Check patient exists
    p_res = await db.execute(select(PatientRecord).where(PatientRecord.patient_id == body.patient_id))
    if not p_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Patient not found")

    # Check username not taken
    u_res = await db.execute(select(PatientAuth).where(PatientAuth.username == body.username))
    if u_res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    auth = PatientAuth(
        patient_id=body.patient_id,
        username=body.username,
        password_hash=_simple_hash(body.password),
    )
    db.add(auth)
    await db.commit()
    return {"success": True, "patient_id": body.patient_id, "username": body.username}