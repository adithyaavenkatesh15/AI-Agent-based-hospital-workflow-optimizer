# app/models.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import IntEnum


class PriorityLevel(IntEnum):
    """Patient priority levels"""
    EMERGENCY = 1
    URGENT = 2
    ROUTINE = 3


class PatientInput(BaseModel):
    """Input model for patient data"""
    patient_id: str = Field(..., description="Unique patient identifier")
    name: str = Field(..., description="Patient name")
    gender: Optional[str] = Field(None, description="Patient gender (M/F/O)")
    symptoms: List[str] = Field(..., description="List of symptoms")
    severity_score: int = Field(..., ge=1, le=10, description="Severity score (1-10)")
    required_appointments: List[str] = Field(..., description="Required appointments")
    medical_history: str = Field(..., description="Medical history")
    age: int = Field(..., gt=0, description="Patient age")
    priority: Optional[int] = Field(None, description="Priority level (auto-assigned)")


class PriorityClassification(BaseModel):
    """Priority classification result"""
    priority_level: int = Field(..., description="Priority level (1-3)")
    priority_description: str = Field(..., description="Priority description")
    estimated_wait_time_minutes: int = Field(..., description="Estimated wait time")
    triage_notes: str = Field(..., description="Triage notes")


class ResourceSlot(BaseModel):
    """Available resource slot"""
    id: str
    time: datetime
    capacity: int = 1
    current_load: int = 0


class Assignment(BaseModel):
    """Patient assignment result"""
    patient_id: str
    assigned_resource: Dict[str, Any]
    estimated_wait_time: float
    assignment_score: float


class SchedulingResult(BaseModel):
    """Scheduling optimization result"""
    optimal_assignments: List[Assignment]
    total_cost: float
    resource_utilization: Dict[str, float]
    scheduling_conflicts: List[str] = []


class DisruptionInput(BaseModel):
    """Disruption event input"""
    disruption_type: str = Field(..., description="Type of disruption")
    affected_resources: List[str] = Field(..., description="Affected resources")
    current_schedule: Dict[str, Any] = Field(..., description="Current schedule")
    available_alternatives: Dict[str, Any] = Field(..., description="Available alternatives")


class FallbackStrategy(BaseModel):
    """Fallback strategy result"""
    immediate_actions: List[str]
    rescheduled_appointments: List[Dict[str, Any]]
    alternative_assignments: Dict[str, Any]
    estimated_impact: Dict[str, Any]


class RLOptimization(BaseModel):
    """RL optimization result"""
    rl_recommendations: List[str]
    confidence_scores: Dict[str, float]
    learning_updates: Dict[str, Any]
    predicted_outcomes: Dict[str, Any]


class NotificationInput(BaseModel):
    """Notification input"""
    notification_type: str
    recipients: List[str]
    message_content: Dict[str, Any]
    priority_level: str = "normal"


class NotificationResult(BaseModel):
    """Notification delivery result"""
    notifications_sent: int
    delivery_status: List[Dict[str, Any]]
    failed_deliveries: List[Dict[str, Any]]
    notification_id: str


class PatientWorkflowResult(BaseModel):
    """Complete patient workflow result"""
    patient_id: str
    patient_name: str
    priority_classification: PriorityClassification
    scheduling_result: Optional[SchedulingResult] = None
    disruption_handling: Optional[Dict[str, Any]] = None
    notifications: Optional[NotificationResult] = None
    status: str
    processed_at: datetime = Field(default_factory=datetime.now)


class SystemMetrics(BaseModel):
    """System performance metrics"""
    patient_throughput: Dict[str, Any]
    resource_utilization: Dict[str, Any]
    wait_time_statistics: Dict[str, Any]
    staff_workload: Dict[str, Any]
    system_efficiency: float


class OptimizationResult(BaseModel):
    """Genetic algorithm optimization result"""
    pareto_solutions: List[Dict[str, Any]]
    best_solution: Dict[str, Any]
    optimization_metrics: Dict[str, Any]
    convergence_data: List[float]
