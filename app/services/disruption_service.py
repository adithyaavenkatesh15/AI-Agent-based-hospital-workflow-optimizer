# app/services/disruption_service.py
import random
import numpy as np
from typing import List, Dict, Any
from app.models import FallbackStrategy, RLOptimization
from app.config import settings


class QLearningAgent:
    """Q-Learning Reinforcement Learning Agent"""
    
    def __init__(
        self,
        state_size: int = 10,
        action_size: int = 5,
        learning_rate: float = None,
        discount_factor: float = None
    ):
        self.state_size = state_size
        self.action_size = action_size
        self.learning_rate = learning_rate or settings.rl_learning_rate
        self.discount_factor = discount_factor or settings.rl_discount_factor
        self.epsilon = settings.rl_epsilon
        self.q_table = np.zeros((state_size, action_size))
    
    def get_state(self, situation: Dict) -> int:
        """Convert situation to state index"""
        disruption_type = situation.get('disruption_type', 'normal')
        severity = situation.get('severity', 0)
        
        state_mapping = {
            'equipment_failure': 0,
            'staff_absence': 1,
            'emergency': 2,
            'overload': 3,
            'normal': 4
        }
        
        base_state = state_mapping.get(disruption_type, 4)
        severity_offset = min(severity, 1)
        return min(base_state + severity_offset, self.state_size - 1)
    
    def get_action(self, state: int) -> int:
        """Select action using epsilon-greedy policy"""
        if random.random() < self.epsilon:
            return random.randint(0, self.action_size - 1)
        return int(np.argmax(self.q_table[state]))
    
    def update_q_value(self, state: int, action: int, reward: float, next_state: int):
        """Update Q-value using Q-learning formula"""
        current_q = self.q_table[state, action]
        max_next_q = np.max(self.q_table[next_state])
        new_q = current_q + self.learning_rate * (
            reward + self.discount_factor * max_next_q - current_q
        )
        self.q_table[state, action] = new_q
    
    def decay_epsilon(self, decay_rate: float = 0.995):
        """Decay exploration rate"""
        self.epsilon = max(0.01, self.epsilon * decay_rate)


# Global RL agent instance
rl_agent = QLearningAgent()


class DisruptionService:
    """Service for disruption handling (Exception Handling Agent)"""
    
    @staticmethod
    def execute_fallback_strategy(
        disruption_type: str,
        affected_resources: List[str],
        current_schedule: Dict[str, Any],
        available_alternatives: Dict[str, Any]
    ) -> FallbackStrategy:
        """
        Apply rule-based fallback strategies for system disruptions.
        """
        
        immediate_actions = []
        rescheduled_appointments = []
        alternative_assignments = {}
        
        if disruption_type == "equipment_failure":
            immediate_actions = [
                f"Immediately mark {', '.join(affected_resources)} as unavailable",
                "Activate backup equipment if available",
                "Notify maintenance team",
                "Reschedule affected appointments to alternative equipment"
            ]
            
            # Simulate rescheduling
            for resource in affected_resources:
                rescheduled_appointments.append({
                    "original_resource": resource,
                    "new_resource": f"BACKUP-{resource}",
                    "status": "rescheduled"
                })
        
        elif disruption_type == "staff_absence":
            immediate_actions = [
                f"Redistribute workload from {', '.join(affected_resources)}",
                "Contact on-call staff",
                "Prioritize emergency cases",
                "Delay non-urgent appointments"
            ]
        
        elif disruption_type == "emergency":
            immediate_actions = [
                "Clear emergency bay immediately",
                "Activate emergency protocol",
                "Notify all emergency staff",
                "Postpone routine appointments"
            ]
        
        else:
            immediate_actions = [
                "Monitor situation",
                "Prepare contingency plans",
                "Alert supervisors"
            ]
        
        estimated_impact = {
            "affected_patients": len(affected_resources) * 3,
            "estimated_delay_minutes": 30 if disruption_type == "emergency" else 15,
            "severity": "high" if disruption_type in ["equipment_failure", "emergency"] else "medium"
        }
        
        return FallbackStrategy(
            immediate_actions=immediate_actions,
            rescheduled_appointments=rescheduled_appointments,
            alternative_assignments=alternative_assignments,
            estimated_impact=estimated_impact
        )
    
    @staticmethod
    def apply_reinforcement_learning(
        historical_disruptions: List[Dict],
        current_situation: Dict,
        performance_metrics: Dict
    ) -> RLOptimization:
        """
        Apply Reinforcement Learning for adaptive disruption handling.
        """
        
        current_state = rl_agent.get_state(current_situation)
        action = rl_agent.get_action(current_state)
        
        action_strategies = {
            0: 'immediate_reallocation',
            1: 'gradual_redistribution',
            2: 'emergency_protocol',
            3: 'load_balancing',
            4: 'wait_and_observe'
        }
        
        chosen_strategy = action_strategies.get(action, 'immediate_reallocation')
        recommendations = DisruptionService._generate_rl_recommendations(
            chosen_strategy, current_situation, performance_metrics
        )
        
        confidence = DisruptionService._calculate_confidence(current_state, action)
        
        return RLOptimization(
            rl_recommendations=recommendations,
            confidence_scores={'strategy': confidence, 'action': float(action)},
            learning_updates={
                'state': int(current_state),
                'action': int(action),
                'strategy': chosen_strategy
            },
            predicted_outcomes=DisruptionService._predict_outcomes(
                recommendations, current_situation
            )
        )
    
    @staticmethod
    def _generate_rl_recommendations(
        strategy: str,
        situation: Dict,
        metrics: Dict
    ) -> List[str]:
        """Generate recommendations based on RL strategy"""
        
        recommendations_map = {
            'immediate_reallocation': [
                "Immediately reassign affected patients to alternative resources",
                "Activate backup equipment and staff",
                "Notify all stakeholders of changes within 5 minutes"
            ],
            'gradual_redistribution': [
                "Gradually shift workload over next 30 minutes",
                "Prioritize high-priority patients for immediate reallocation",
                "Monitor system stability during transition"
            ],
            'emergency_protocol': [
                "Activate emergency response procedures",
                "Clear all non-urgent appointments",
                "Deploy all available resources immediately"
            ],
            'load_balancing': [
                "Analyze current load distribution",
                "Identify least loaded resources",
                "Implement balanced redistribution"
            ],
            'wait_and_observe': [
                "Monitor situation for 15 minutes",
                "Prepare contingency plans",
                "Gather more information before acting"
            ]
        }
        
        return recommendations_map.get(strategy, ["Monitor and assess"])
    
    @staticmethod
    def _calculate_confidence(state: int, action: int) -> float:
        """Calculate confidence score for action"""
        q_value = rl_agent.q_table[state, action]
        max_q = np.max(rl_agent.q_table[state])
        return float(min(1.0, max(0.0, q_value / max_q)) if max_q > 0 else 0.5)
    
    @staticmethod
    def _predict_outcomes(recommendations: List[str], situation: Dict) -> Dict[str, Any]:
        """Predict outcomes of recommendations"""
        return {
            'expected_wait_time_reduction': random.uniform(10, 30),
            'resource_utilization_improvement': random.uniform(0.1, 0.3),
            'patient_satisfaction_impact': random.uniform(0.05, 0.15),
            'implementation_time_minutes': random.uniform(5, 20),
            'success_probability': random.uniform(0.7, 0.95)
        }
    
    @staticmethod
    def update_rl_model(situation: Dict, strategy_result: Dict):
        """Update RL model with new experience"""
        reward = DisruptionService._calculate_reward(strategy_result, situation)
        current_state = rl_agent.get_state(situation)
        next_state = rl_agent.get_state(situation)
        action = strategy_result.get('learning_updates', {}).get('action', 0)
        
        rl_agent.update_q_value(current_state, action, reward, next_state)
        rl_agent.decay_epsilon()
    
    @staticmethod
    def _calculate_reward(strategy_result: Dict, situation: Dict) -> float:
        """Calculate reward for RL update"""
        base_reward = 0.0
        
        if strategy_result.get('success', True):
            base_reward += 1.0
        
        wait_time = strategy_result.get('wait_time_minutes', 0)
        if wait_time > 60:
            base_reward -= 0.5
        elif wait_time < 30:
            base_reward += 0.3
        
        utilization = strategy_result.get('resource_utilization', 0.5)
        if utilization > 0.8:
            base_reward += 0.2
        elif utilization < 0.3:
            base_reward -= 0.3
        
        return base_reward
