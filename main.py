# main.py (root level entry point)
"""
Hospital Workflow Optimization System - FastAPI Entry Point

Run with:
    uvicorn main:app --reload
    
Or:
    python main.py
"""
import uvicorn
from app.main import app
from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_level="info"
    )

