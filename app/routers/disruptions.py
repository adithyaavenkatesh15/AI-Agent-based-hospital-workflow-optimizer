# app/routers/disruptions.py
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from app.models import DisruptionInput, FallbackStrategy, RLOptimization
from app.services.disruption_service import DisruptionService
from app.database import get_db, DisruptionRecord

router = APIRouter(prefix="/disruptions", tags=["Disruptions"])


@router.post("/fallback", response_model=FallbackStrategy)
async def apply_fallback_strategy(
    disruption: DisruptionInput,
    db: AsyncSession = Depends(get_db)
):
    """
    Apply rule-based fallback strategy for disruptions.
    
    This endpoint implements the Exception Handling Agent's fallback logic.
    """
    try:
        result = DisruptionService.execute_fallback_strategy(
            disruption_type=disruption.disruption_type,
            affected_resources=disruption.affected_resources,
            current_schedule=disruption.current_schedule,
            available_alternatives=disruption.available_alternatives
        )
        
        # Save disruption to database
        disruption_record = DisruptionRecord(
            disruption_type=disruption.disruption_type,
            affected_resources=disruption.affected_resources,
            severity=len(disruption.affected_resources),
            resolution_strategy=result.model_dump(),
            resolved=0
        )
        db.add(disruption_record)
        await db.commit()
        
        return result
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error applying fallback: {str(e)}")


@router.post("/rl-optimize", response_model=RLOptimization)
async def apply_rl_optimization(
    historical_disruptions: List[Dict[str, Any]],
    current_situation: Dict[str, Any],
    performance_metrics: Dict[str, Any]
):
    """
    Apply Reinforcement Learning optimization for disruption handling.
    
    This endpoint implements the Exception Handling Agent's RL logic.
    """
    try:
        result = DisruptionService.apply_reinforcement_learning(
            historical_disruptions=historical_disruptions,
            current_situation=current_situation,
            performance_metrics=performance_metrics
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in RL optimization: {str(e)}")


@router.post("/update-rl-model")
async def update_rl_model(
    situation: Dict[str, Any],
    strategy_result: Dict[str, Any]
):
    """
    Update the RL model with new experience.
    """
    try:
        DisruptionService.update_rl_model(situation, strategy_result)
        return {"status": "success", "message": "RL model updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating RL model: {str(e)}")


@router.get("/history", response_model=List[dict])
async def get_disruption_history(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """Get disruption history from database"""
    try:
        from sqlalchemy import select
        result = await db.execute(
            select(DisruptionRecord).offset(skip).limit(limit)
        )
        disruptions = result.scalars().all()
        
        return [
            {
                "id": d.id,
                "disruption_type": d.disruption_type,
                "affected_resources": d.affected_resources,
                "severity": d.severity,
                "resolved": bool(d.resolved),
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None
            }
            for d in disruptions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching disruptions: {str(e)}")
