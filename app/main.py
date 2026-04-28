# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
from pathlib import Path
from app.config import settings
from app.database import init_db
from app.routers import patients, scheduling, disruptions, notifications, optimization, system, journey
from app.routers import patient_portal, resource_management


async def _journey_background_task():
    """Every 2 minutes: auto-complete expired appointments, free doctors, advance queue."""
    import asyncio
    from app.database import AsyncSessionLocal
    from app.services.patient_journey_service import PatientJourneyAgent
    while True:
        await asyncio.sleep(120)  # 2 minutes
        try:
            async with AsyncSessionLocal() as db:
                result = await PatientJourneyAgent.auto_tick(db)
                completed = result.get("completed_count", 0)
                advanced  = len(result.get("also_advanced", []))
                if completed or advanced:
                    print(f"⏱ Auto-tick: {completed} completed, {advanced} advanced")
        except Exception as e:
            print(f"⚠ Journey auto-tick error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    import asyncio
    print("🏥 Initializing Hospital Workflow Optimization System...")
    await init_db()
    print(f"✅ Database initialized")
    print(f"🏥 {settings.hospital_name} - {settings.department} Department")
    print(f"🚀 Server starting on http://{settings.api_host}:{settings.api_port}")
    print(f"📊 API Docs available at http://{settings.api_host}:{settings.api_port}/docs")
    # Start 2-minute auto-progression background task
    task = asyncio.create_task(_journey_background_task())
    print("⏱ Journey auto-tick started (every 2 minutes)")
    yield
    task.cancel()
    print("👋 Shutting down Hospital Workflow System...")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    description="""
    ## AI-Agent Based Smart Hospital Workflow Optimization System
    
    A comprehensive hospital workflow management system using multiple AI agents:
    
    ### 🤖 Four Agent Pipeline:
    
    1. **Reception Agent** - Patient triage and priority classification
    2. **Task Scheduling Agent** - Optimal resource assignment using Hungarian Algorithm
    3. **Exception Handling Agent** - Disruption management with Reinforcement Learning (Q-Learning)
    4. **Doctor/Nurse Assistant Agent** - Real-time staff notifications and dashboard updates
    5. **Patient Journey Agent** - Consultation→Test routing, emergency bump, queue auto-advance
    
    ### 🔧 Key Features:
    
    - **Priority Queue Classification**: Medical urgency-based patient prioritization (Emergency/Urgent/Routine)
    - **Hungarian Algorithm**: Optimal patient-to-resource assignment (O(n³))
    - **Genetic Algorithm**: Multi-objective system-wide optimization
    - **Q-Learning (RL)**: Adaptive disruption handling
    - **Real-time Notifications**: Staff alerts and dashboard updates
    - **Capacity Overflow Management**: Auto-reallocation when resource slots are full, with dashboard alerts
    
    ### 📊 Optimization Objectives:
    
    - Minimize patient wait time
    - Maximize resource utilization  
    - Minimize operational cost
    - Maximize patient satisfaction
    - Balance staff workload
    
    ### 🏥 Department: Cardiology
    """,
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware - allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# ─── API Routers ──────────────────────────────────────────────────────────────
api_prefix = settings.api_prefix

app.include_router(patients.router, prefix=api_prefix)
app.include_router(scheduling.router, prefix=api_prefix)
app.include_router(disruptions.router, prefix=api_prefix)
app.include_router(notifications.router, prefix=api_prefix)
app.include_router(optimization.router, prefix=api_prefix)
app.include_router(system.router, prefix=api_prefix)
app.include_router(journey.router, prefix=api_prefix)
app.include_router(patient_portal.router, prefix=api_prefix)
app.include_router(resource_management.router, prefix=api_prefix)

# ─── Convenience alias routes ─────────────────────────────────────────────────
# Frontend calls /api/v1/schedule and /api/v1/appointments directly
# Scheduling router provides these under /api/v1/scheduling/schedule and /api/v1/scheduling/appointments
# Add top-level aliases:
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.database import get_db, AppointmentRecord, PatientRecord
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from fastapi import Depends
from datetime import datetime, timedelta
from typing import Optional

alias_router = APIRouter(prefix=api_prefix, tags=["Aliases"])


@alias_router.get("/schedule")
async def schedule_alias(date: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Alias for /scheduling/schedule"""
    from app.routers.scheduling import get_schedule
    return await get_schedule(date_str=date, db=db)


@alias_router.get("/appointments")
async def appointments_alias(date: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Alias for /scheduling/appointments"""
    from app.routers.scheduling import get_appointments
    return await get_appointments(date_str=date, db=db)


app.include_router(alias_router)

# ─── Static files ─────────────────────────────────────────────────────────────
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to the UI dashboard"""
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "hospital": settings.hospital_name,
        "department": settings.department,
        "timestamp": datetime.now().isoformat()
    }


@app.get(f"{api_prefix}/info")
async def system_info():
    """Get detailed system information"""
    return {
        "hospital_name": settings.hospital_name,
        "department": settings.department,
        "optimization_interval_hours": settings.optimization_interval_hours,
        "algorithms": {
            "priority_classification": "Rule-based medical urgency scoring",
            "scheduling": "Hungarian Algorithm (O(n³))",
            "genetic_algorithm": {
                "population_size": settings.ga_population_size,
                "generations": settings.ga_generations
            },
            "reinforcement_learning": {
                "algorithm": "Q-Learning",
                "learning_rate": settings.rl_learning_rate,
                "discount_factor": settings.rl_discount_factor,
                "epsilon": settings.rl_epsilon
            }
        },
        "priority_weights": settings.priority_weights,
        "available_services": [
            "consultation",
            "echocardiogram",
            "stress_test",
            "ecg",
            "blood_test",
            "follow_up"
        ]
    }
