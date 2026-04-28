# app/routers/optimization.py
from fastapi import APIRouter, HTTPException
from typing import List
from app.models import OptimizationResult, SystemMetrics
from app.services.optimization_service import OptimizationService

router = APIRouter(prefix="/optimization", tags=["Optimization"])


@router.post("/genetic-algorithm", response_model=OptimizationResult)
async def run_genetic_algorithm(
    population_size: int = None,
    generations: int = None,
    optimization_objectives: List[str] = None
):
    """
    Execute Genetic Algorithm for system-wide multi-objective optimization.
    
    Optimizes:
    - Patient wait time
    - Resource utilization
    - Operational cost
    - Patient satisfaction
    - Staff workload balance
    """
    try:
        result = OptimizationService.run_genetic_optimization(
            population_size=population_size,
            generations=generations,
            optimization_objectives=optimization_objectives
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in genetic algorithm: {str(e)}")


@router.get("/metrics", response_model=SystemMetrics)
async def get_system_metrics(time_period_hours: int = 24):
    """
    Collect comprehensive system performance metrics.
    """
    try:
        metrics = OptimizationService.collect_system_metrics(
            time_period_hours=time_period_hours
        )
        
        return metrics
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error collecting metrics: {str(e)}")
