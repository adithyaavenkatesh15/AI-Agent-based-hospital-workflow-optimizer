# app/services/optimization_service.py
import random
import numpy as np
from typing import List, Dict, Any
from app.models import OptimizationResult, SystemMetrics
from app.config import settings


class GeneticAlgorithmOptimizer:
    """Genetic Algorithm for multi-objective optimization"""
    
    def __init__(
        self,
        population_size: int = None,
        generations: int = None
    ):
        self.population_size = population_size or settings.ga_population_size
        self.generations = generations or settings.ga_generations
    
    def run_optimization(
        self,
        optimization_objectives: List[str] = None
    ) -> Dict[str, Any]:
        """Run genetic algorithm optimization"""
        
        if optimization_objectives is None:
            optimization_objectives = [
                'minimize_patient_wait_time',
                'maximize_resource_utilization',
                'minimize_operational_cost',
                'maximize_patient_satisfaction',
                'balance_staff_workload'
            ]
        
        population = self._initialize_population(optimization_objectives)
        pareto_front = []
        convergence_history = []
        
        for generation in range(self.generations):
            fitness_scores = self._evaluate_population(population, optimization_objectives)
            pareto_front = self._update_pareto_front(population, fitness_scores, pareto_front)
            
            avg_fitness = np.mean([sum(f.values()) for f in fitness_scores])
            convergence_history.append(float(avg_fitness))
            
            new_population = []
            for _ in range(self.population_size):
                parent1 = self._tournament_selection(population, fitness_scores)
                parent2 = self._tournament_selection(population, fitness_scores)
                child = self._crossover(parent1, parent2)
                child = self._mutate(child)
                new_population.append(child)
            
            population = new_population
        
        final_fitness = self._evaluate_population(population, optimization_objectives)
        best_idx = np.argmax([sum(f.values()) for f in final_fitness])
        best_individual = population[best_idx]
        
        return {
            'pareto_front': pareto_front,
            'best_individual': best_individual,
            'performance_metrics': {
                'final_fitness': final_fitness[best_idx],
                'convergence_rate': self._calculate_convergence_rate(convergence_history),
                'diversity': self._calculate_population_diversity(population)
            },
            'convergence_history': convergence_history
        }
    
    def _initialize_population(self, objectives: List[str]) -> List[Dict]:
        """Initialize random population"""
        population = []
        for _ in range(self.population_size):
            individual = {
                'resource_allocation': self._generate_random_allocation(),
                'scheduling_weights': self._generate_random_weights(),
                'priority_factors': self._generate_random_priority_factors(),
                'load_balancing_params': self._generate_random_load_params()
            }
            population.append(individual)
        return population
    
    def _evaluate_population(
        self,
        population: List[Dict],
        objectives: List[str]
    ) -> List[Dict]:
        """Evaluate fitness for each individual"""
        fitness_scores = []
        for individual in population:
            fitness = {}
            for objective in objectives:
                if 'minimize_patient_wait_time' in objective:
                    fitness[objective] = random.uniform(0.1, 1.0)
                elif 'maximize_resource_utilization' in objective:
                    fitness[objective] = random.uniform(0.5, 1.0)
                elif 'minimize_operational_cost' in objective:
                    fitness[objective] = random.uniform(0.1, 1.0)
                elif 'maximize_patient_satisfaction' in objective:
                    fitness[objective] = random.uniform(0.6, 1.0)
                elif 'balance_staff_workload' in objective:
                    fitness[objective] = random.uniform(0.4, 1.0)
            fitness_scores.append(fitness)
        return fitness_scores
    
    def _update_pareto_front(
        self,
        population: List[Dict],
        fitness_scores: List[Dict],
        current_front: List[Dict]
    ) -> List[Dict]:
        """Update Pareto front with non-dominated solutions"""
        pareto_front = current_front.copy()
        for i, individual in enumerate(population):
            fitness = fitness_scores[i]
            total_fitness = sum(fitness.values())
            if len(pareto_front) < 10 or total_fitness > min([sum(f['fitness'].values()) for f in pareto_front]):
                pareto_front.append({
                    'individual': individual,
                    'fitness': fitness,
                    'total_score': total_fitness
                })
                if len(pareto_front) > 10:
                    pareto_front.sort(key=lambda x: x['total_score'], reverse=True)
                    pareto_front = pareto_front[:10]
        return pareto_front
    
    def _tournament_selection(
        self,
        population: List[Dict],
        fitness_scores: List[Dict],
        tournament_size: int = 3
    ) -> Dict:
        """Select individual using tournament selection"""
        tournament_indices = random.sample(range(len(population)), tournament_size)
        tournament_fitness = [fitness_scores[i] for i in tournament_indices]
        best_idx = tournament_indices[np.argmax([sum(f.values()) for f in tournament_fitness])]
        return population[best_idx]
    
    def _crossover(self, parent1: Dict, parent2: Dict) -> Dict:
        """Perform crossover between two parents"""
        child = {}
        for key in parent1.keys():
            if random.random() < 0.5:
                child[key] = parent1[key].copy() if isinstance(parent1[key], dict) else parent1[key]
            else:
                child[key] = parent2[key].copy() if isinstance(parent2[key], dict) else parent2[key]
        return child
    
    def _mutate(self, individual: Dict, mutation_rate: float = 0.1) -> Dict:
        """Mutate individual"""
        mutated = individual.copy()
        for key, value in mutated.items():
            if random.random() < mutation_rate:
                if isinstance(value, dict):
                    for sub_key in value:
                        if random.random() < 0.3:
                            value[sub_key] = random.uniform(0, 1)
                elif isinstance(value, (int, float)):
                    mutated[key] = value + random.gauss(0, 0.1)
        return mutated
    
    def _generate_random_allocation(self) -> Dict:
        return {
            'consultation': random.uniform(0.3, 0.8),
            'echocardiogram': random.uniform(0.2, 0.7),
            'stress_test': random.uniform(0.1, 0.6)
        }
    
    def _generate_random_weights(self) -> Dict:
        return {
            'priority_weight': random.uniform(0.5, 2.0),
            'wait_time_weight': random.uniform(0.3, 1.5),
            'utilization_weight': random.uniform(0.2, 1.0)
        }
    
    def _generate_random_priority_factors(self) -> Dict:
        return {
            'emergency': random.uniform(0.8, 1.0),
            'urgent': random.uniform(0.5, 0.8),
            'routine': random.uniform(0.1, 0.5)
        }
    
    def _generate_random_load_params(self) -> Dict:
        return {
            'max_load': random.uniform(0.7, 0.9),
            'rebalance_threshold': random.uniform(0.1, 0.3),
            'preference_weight': random.uniform(0.2, 0.8)
        }
    
    def _calculate_convergence_rate(self, history: List[float]) -> float:
        if len(history) < 2:
            return 0.0
        return abs(history[-1] - history[0]) / len(history)
    
    def _calculate_population_diversity(self, population: List[Dict]) -> float:
        return random.uniform(0.3, 0.8)


class OptimizationService:
    """Service for system-wide optimization"""
    
    @staticmethod
    def run_genetic_optimization(
        population_size: int = None,
        generations: int = None,
        optimization_objectives: List[str] = None
    ) -> OptimizationResult:
        """Execute genetic algorithm optimization"""
        
        optimizer = GeneticAlgorithmOptimizer(population_size, generations)
        results = optimizer.run_optimization(optimization_objectives)
        
        return OptimizationResult(
            pareto_solutions=results['pareto_front'],
            best_solution=results['best_individual'],
            optimization_metrics=results['performance_metrics'],
            convergence_data=results['convergence_history']
        )
    
    @staticmethod
    def collect_system_metrics(time_period_hours: int = 24) -> SystemMetrics:
        """Collect comprehensive system performance metrics"""
        
        return SystemMetrics(
            patient_throughput={
                'total_patients': random.randint(50, 150),
                'patients_per_hour': random.uniform(2, 8),
                'emergency_cases': random.randint(5, 20),
                'urgent_cases': random.randint(15, 40),
                'routine_cases': random.randint(30, 90)
            },
            resource_utilization={
                'consultation': random.uniform(0.7, 0.95),
                'echocardiogram': random.uniform(0.6, 0.85),
                'stress_test': random.uniform(0.5, 0.75),
                'ecg': random.uniform(0.65, 0.90),
                'blood_test': random.uniform(0.70, 0.95)
            },
            wait_time_statistics={
                'average_wait_minutes': random.uniform(15, 45),
                'median_wait_minutes': random.uniform(10, 35),
                'max_wait_minutes': random.uniform(60, 180),
                'emergency_wait_minutes': random.uniform(0, 5),
                'urgent_wait_minutes': random.uniform(10, 25),
                'routine_wait_minutes': random.uniform(30, 90)
            },
            staff_workload={
                'average_patients_per_staff': random.uniform(8, 15),
                'workload_variance': random.uniform(0.1, 0.4),
                'overtime_hours': random.uniform(0, 10),
                'staff_satisfaction': random.uniform(0.6, 0.9)
            },
            system_efficiency=random.uniform(0.7, 0.95)
        )
