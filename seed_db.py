import asyncio
import json
from datetime import datetime, timedelta
from app.database import engine, Base, AsyncSessionLocal, PatientRecord, AppointmentRecord, MetricsRecord

async def industry_seed():
    print("🏥 Starting Industrial Grade Data Seeding...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Clear existing
        from sqlalchemy import delete
        await session.execute(delete(PatientRecord))
        await session.execute(delete(AppointmentRecord))
        await session.execute(delete(MetricsRecord))

        # 1. Patients with varied conditions
        patients = [
            PatientRecord(
                patient_id="P-001", name="John 'Emergency' Wilson", age=68,
                symptoms=json.dumps(["Acute Chest Pain", "Left Arm Numbness"]),
                severity_score=10, priority=1, medical_history="Chronic Hypertension",
                required_appointments=json.dumps(["consultation", "ecg", "emergency_surgery"])
            ),
            PatientRecord(
                patient_id="P-002", name="Sarah 'Urgent' Miller", age=45,
                symptoms=json.dumps(["Persistent Arrhythmia", "Dizziness"]),
                severity_score=7, priority=2, medical_history="None",
                required_appointments=json.dumps(["consultation", "echocardiogram"])
            ),
            PatientRecord(
                patient_id="P-003", name="Michael 'Routine' Scott", age=52,
                symptoms=json.dumps(["Annual Physical", "Blood Pressure Check"]),
                severity_score=3, priority=3, medical_history="Diabetes Type 2",
                required_appointments=json.dumps(["consultation", "blood_test"])
            ),
            PatientRecord(
                patient_id="P-004", name="Elena Rodriguez", age=31,
                symptoms=json.dumps(["Mild Palpitations"]),
                severity_score=4, priority=2, medical_history="Anxiety",
                required_appointments=json.dumps(["consultation"])
            ),
            PatientRecord(
                patient_id="P-005", name="Gregory House", age=55,
                symptoms=json.dumps(["Leg Pain", "Limping"]),
                severity_score=6, priority=1, medical_history="Vicodin Addiction",
                required_appointments=json.dumps(["diagnostic_scan", "pain_management"])
            )
        ]
        session.add_all(patients)

        # 2. Key Appointments
        appts = [
            AppointmentRecord(
                patient_id="P-001", appointment_type="Emergency ECG",
                scheduled_time=datetime.now(), assigned_resource=json.dumps({"doctor": "Dr. Sarah Chen", "room": "ER-01"}),
                status="in-progress"
            ),
            AppointmentRecord(
                patient_id="P-002", appointment_type="Echocardiogram",
                scheduled_time=datetime.now() + timedelta(hours=1), assigned_resource=json.dumps({"machine": "Siemens S2000"}),
                status="scheduled"
            ),
            AppointmentRecord(
                patient_id="P-003", appointment_type="Lab Consultation",
                scheduled_time=datetime.now() + timedelta(hours=4), assigned_resource=json.dumps({"nurse": "Nurse Jones"}),
                status="scheduled"
            )
        ]
        session.add_all(appts)

        # 3. System Metrics for Graphics
        metrics = [
            MetricsRecord(metric_type="avg_wait_time", metric_data=json.dumps({"value": 11.2, "unit": "min"})),
            MetricsRecord(metric_type="resource_utilization", metric_data=json.dumps({"value": 84, "unit": "%"})),
            MetricsRecord(metric_type="triage_accuracy", metric_data=json.dumps({"value": 98.5, "unit": "%"}))
        ]
        session.add_all(metrics)

        await session.commit()
    
    print("✅ Industrial Grade Data Seeded Successfully!")

if __name__ == "__main__":
    asyncio.run(industry_seed())
