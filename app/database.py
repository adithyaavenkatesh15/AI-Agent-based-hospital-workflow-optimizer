# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Text, Boolean
from datetime import datetime
from app.config import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for models
Base = declarative_base()


class PatientRecord(Base):
    """Patient database model"""
    __tablename__ = "patients"
    
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, unique=True, index=True)
    name = Column(String)
    gender = Column(String, nullable=True)
    symptoms = Column(JSON)
    severity_score = Column(Integer)
    required_appointments = Column(JSON)
    medical_history = Column(Text)
    age = Column(Integer)
    priority = Column(Integer)
    location = Column(String, nullable=True, default="waiting")  # waiting/consultation/tests/icu/discharged
    is_icu = Column(Integer, nullable=True, default=0)   # 1 = patient is in ICU
    icu_bed_id = Column(String, nullable=True, default="")  # ICU bed assigned
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AppointmentRecord(Base):
    """Appointment database model"""
    __tablename__ = "appointments"
    
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, index=True)
    appointment_type = Column(String)
    scheduled_time = Column(DateTime)
    end_time = Column(DateTime, nullable=True)       # start_time + duration + 5min buffer
    duration_minutes = Column(Integer, nullable=True) # actual test duration
    location_name = Column(String, nullable=True)    # human-readable room/unit name
    assigned_resource = Column(JSON)
    status = Column(String, default="scheduled")
    sequence_order = Column(Integer, nullable=True)  # 0=consultation,1=first_test,2=second_test…
    rescheduled_from = Column(DateTime, nullable=True)  # original time if rescheduled
    reschedule_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ResourceUnavailability(Base):
    """Track when resources are marked unavailable by staff"""
    __tablename__ = "resource_unavailability"

    id = Column(Integer, primary_key=True, index=True)
    resource_type = Column(String, index=True)       # e.g. 'ecg', 'consultation'
    unit_id = Column(String, nullable=True)          # specific unit or None = all units
    unit_name = Column(String, nullable=True)
    unavailable_from = Column(DateTime)
    unavailable_until = Column(DateTime)
    reason = Column(String, nullable=True)
    marked_by = Column(String, nullable=True)        # staff name/id
    active = Column(Integer, default=1)              # 1=active, 0=cancelled
    created_at = Column(DateTime, default=datetime.now)


class PatientNotification(Base):
    """Per-patient notifications for portal display"""    
    __tablename__ = "patient_notifications"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, index=True)
    notif_type = Column(String, default="info")   # info | warning | rescheduled | bumped
    title = Column(String)
    message = Column(String)
    read = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


class PatientAuth(Base):
    """Patient login credentials"""
    __tablename__ = "patient_auth"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)   # phone or email
    password_hash = Column(String)                        # bcrypt hash
    created_at = Column(DateTime, default=datetime.now)
    last_login = Column(DateTime, nullable=True)


class DisruptionRecord(Base):
    """Disruption event database model"""
    __tablename__ = "disruptions"
    
    id = Column(Integer, primary_key=True, index=True)
    disruption_type = Column(String)
    affected_resources = Column(JSON)
    severity = Column(Integer)
    resolution_strategy = Column(JSON)
    resolved = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime, nullable=True)


class MetricsRecord(Base):
    """System metrics database model"""
    __tablename__ = "metrics"
    
    id = Column(Integer, primary_key=True, index=True)
    metric_type = Column(String)
    metric_data = Column(JSON)
    timestamp = Column(DateTime, default=datetime.now)


class WorkflowResult(Base):
    """Workflow execution results"""
    __tablename__ = "workflow_results"
    
    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(String, index=True)
    priority_classification = Column(JSON)
    scheduling_result = Column(JSON)
    disruption_handling = Column(JSON, nullable=True)
    notifications = Column(JSON, nullable=True)
    status = Column(String)
    processed_at = Column(DateTime, default=datetime.now)


def _simple_hash(password: str) -> str:
    """Simple hash for patient passwords (SHA256 for demo; use bcrypt in production)"""
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def _check_hash(password: str, hashed: str) -> bool:
    return _simple_hash(password) == hashed


async def init_db():
    """Initialize database tables, run migrations for new columns"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migration: add sequence_order column if it doesn't exist (for existing DBs)
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE appointments ADD COLUMN sequence_order INTEGER"
                )
            )
        except Exception:
            pass  # Column already exists — ignore
        # Migration: add location column to patients if it doesn't exist
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE patients ADD COLUMN location VARCHAR DEFAULT 'waiting'"
                )
            )
        except Exception:
            pass  # Column already exists — ignore
        # Migration: add is_icu column
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE patients ADD COLUMN is_icu INTEGER DEFAULT 0"
                )
            )
        except Exception:
            pass
        # Migration: add icu_bed_id column
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE patients ADD COLUMN icu_bed_id VARCHAR DEFAULT ''"
                )
            )
        except Exception:
            pass
        # Migration: new appointment columns
        for col_sql in [
            "ALTER TABLE appointments ADD COLUMN end_time DATETIME",
            "ALTER TABLE appointments ADD COLUMN duration_minutes INTEGER",
            "ALTER TABLE appointments ADD COLUMN location_name VARCHAR",
            "ALTER TABLE appointments ADD COLUMN rescheduled_from DATETIME",
            "ALTER TABLE appointments ADD COLUMN reschedule_reason VARCHAR",
        ]:
            try:
                await conn.execute(__import__("sqlalchemy").text(col_sql))
            except Exception:
                pass
        # Migration: patient_notifications table (auto-created by create_all above)
        # No manual migration needed — SQLAlchemy handles new tables automatically


async def get_db():
    """Dependency for getting database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()