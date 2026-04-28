# app/services/scheduling_service.py
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import Dict, List, Any
from datetime import datetime, timedelta
from app.models import SchedulingResult, Assignment
from app.config import settings


class HungarianScheduler:
    """Hungarian Algorithm implementation for optimal scheduling"""
    
    def __init__(self):
        self.cost_matrix = None
        self.assignment_results = None
    
    def create_cost_matrix(
        self,
        patients: List[Dict],
        resources: Dict[str, List[Dict]],
        current_schedules: Dict,
        priority_weights: Dict = None
    ) -> np.ndarray:
        """Create cost matrix for Hungarian Algorithm optimization."""
        
        if priority_weights is None:
            priority_weights = settings.priority_weights
        
        num_patients = len(patients)
        
        # Flatten all available resource slots
        all_slots = []
        for resource_type, slots in resources.items():
            for slot in slots:
                all_slots.append({
                    'resource_type': resource_type,
                    'resource_id': slot.get('id'),
                    'available_time': slot.get('time'),
                    'capacity': slot.get('capacity', 1),
                    'current_load': slot.get('current_load', 0)
                })
        
        num_slots = len(all_slots)
        
        # Initialize cost matrix
        cost_matrix = np.full((num_patients, num_slots), float('inf'))
        
        for i, patient in enumerate(patients):
            patient_priority = patient.get('priority', 3)
            required_services = patient.get('required_appointments', [])
            
            for j, slot in enumerate(all_slots):
                # Check if slot can serve patient's requirements
                if slot['resource_type'] in required_services:
                    # Calculate base cost (waiting time)
                    current_time = datetime.now()
                    slot_time = slot['available_time']
                    if isinstance(slot_time, str):
                        slot_time = datetime.fromisoformat(slot_time)
                    
                    wait_time_minutes = max(0, (slot_time - current_time).total_seconds() / 60)
                    
                    # Apply priority weighting (higher priority = lower cost)
                    priority_factor = 1.0 / priority_weights.get(patient_priority, 1)
                    
                    # Apply load balancing (prefer less loaded resources)
                    load_factor = 1 + (slot['current_load'] / max(slot['capacity'], 1))
                    
                    # Calculate final cost
                    cost = wait_time_minutes * priority_factor * load_factor
                    cost_matrix[i, j] = cost
        
        self.cost_matrix = cost_matrix
        return cost_matrix
    
    def solve_assignment(self):
        """Solve the assignment problem using Hungarian Algorithm."""
        if self.cost_matrix is None:
            raise ValueError("Cost matrix not created. Call create_cost_matrix first.")
        
        # Use scipy's implementation of Hungarian Algorithm
        patient_indices, resource_indices = linear_sum_assignment(self.cost_matrix)
        
        self.assignment_results = (patient_indices, resource_indices)
        return patient_indices, resource_indices
    
    def get_assignment_cost(self) -> float:
        """Get total cost of current assignment"""
        if self.assignment_results is None:
            return float('inf')
        
        patient_indices, resource_indices = self.assignment_results
        total_cost = self.cost_matrix[patient_indices, resource_indices].sum()
        return total_cost


class SchedulingService:
    """Service for task scheduling (Scheduling Agent)"""
    
    @staticmethod
    def optimize_patient_assignment(
        patient_requirements: List[Dict],
        available_resources: Dict[str, List[Dict]],
        current_schedules: Dict,
        priority_weights: Dict = None
    ) -> SchedulingResult:
        """
        Optimize patient assignment using Hungarian Algorithm.
        """
        scheduler = HungarianScheduler()
        
        # Create and solve optimization problem
        cost_matrix = scheduler.create_cost_matrix(
            patient_requirements,
            available_resources,
            current_schedules,
            priority_weights
        )
        
        patient_indices, resource_indices = scheduler.solve_assignment()
        total_cost = scheduler.get_assignment_cost()
        
        # Format results
        assignments = []
        for i, (patient_idx, resource_idx) in enumerate(zip(patient_indices, resource_indices)):
            if cost_matrix[patient_idx, resource_idx] < float('inf'):
                patient = patient_requirements[patient_idx]
                
                # Find corresponding resource
                resource_counter = 0
                selected_resource = None
                for resource_type, slots in available_resources.items():
                    for slot in slots:
                        if resource_counter == resource_idx:
                            selected_resource = {
                                'type': resource_type,
                                'slot': slot
                            }
                            break
                        resource_counter += 1
                    if selected_resource:
                        break
                
                assignments.append(Assignment(
                    patient_id=patient.get('patient_id'),
                    assigned_resource=selected_resource,
                    estimated_wait_time=float(cost_matrix[patient_idx, resource_idx]),
                    assignment_score=1.0 / (1.0 + cost_matrix[patient_idx, resource_idx])
                ))
        
        # Calculate utilization rates
        utilization_rates = {}
        for resource_type, slots in available_resources.items():
            assigned_slots = sum(1 for a in assignments if a.assigned_resource['type'] == resource_type)
            total_slots = len(slots)
            utilization_rates[resource_type] = assigned_slots / max(total_slots, 1)
        
        return SchedulingResult(
            optimal_assignments=assignments,
            total_cost=float(total_cost),
            resource_utilization=utilization_rates,
            scheduling_conflicts=[]
        )
    
    @staticmethod
    def get_mock_resources() -> Dict[str, List[Dict]]:
        """Get available resources from resource pool config (5 doctors, 6 blood cabins etc.)"""
        from app.config import settings
        base_time = datetime.now()
        resources = {}
        interval_map = {
            "consultation":    2,   # 2-minute slots (matches config duration)
            "blood_test":      5,
            "ecg":            10,
            "echocardiogram": 20,
            "stress_test":    25,
        }
        for resource_type, pool in settings.resource_pool.items():
            interval = interval_map.get(resource_type, 15)
            slots = []
            for i, unit in enumerate(pool["units_info"]):
                slots.append({
                    "id": unit["id"],
                    "name": unit["name"],
                    "specialty": unit.get("specialty", ""),
                    "time": base_time + timedelta(minutes=interval * (i + 1)),
                    "capacity": pool["capacity_per_unit"],
                    "current_load": 0,
                    "duration_minutes": pool["duration_minutes"],
                })
            resources[resource_type] = slots
        return resources
