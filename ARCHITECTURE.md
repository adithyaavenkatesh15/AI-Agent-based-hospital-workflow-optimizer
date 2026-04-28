# API Architecture Documentation

## 🏗️ System Architecture

### Overview
The Hospital Workflow Optimization System is built using a **microservices-inspired architecture** with FastAPI, implementing a **4-agent pipeline** for intelligent patient workflow management.

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │   Routers     │  │   Services    │  │   Database    │   │
│  │  (Endpoints)  │→ │   (Agents)    │→ │  (SQLAlchemy) │   │
│  └───────────────┘  └───────────────┘  └───────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 🤖 Agent Pipeline

### Sequential Processing Flow

```
Patient Input
     ↓
┌────────────────────────────────────────┐
│  Agent 1: Reception Agent              │
│  - Priority Classification             │
│  - Medical Urgency Scoring             │
│  Output: Priority Level (1-3)          │
└────────────────────────────────────────┘
     ↓
┌────────────────────────────────────────┐
│  Agent 2: Scheduling Agent             │
│  - Hungarian Algorithm                 │
│  - Resource Optimization               │
│  Output: Optimal Assignments           │
└────────────────────────────────────────┘
     ↓
┌────────────────────────────────────────┐
│  Agent 3: Exception Handling Agent     │
│  - Disruption Detection                │
│  - RL-based Adaptation                 │
│  Output: Rescheduling Strategy         │
└────────────────────────────────────────┘
     ↓
┌────────────────────────────────────────┐
│  Agent 4: Assistant Agent              │
│  - Staff Notifications                 │
│  - Dashboard Updates                   │
│  Output: Notification Status           │
└────────────────────────────────────────┘
     ↓
Complete Workflow Result
```

---

## 📦 Module Structure

### 1. Routers (API Layer)
**Location**: `app/routers/`

- **patients.py**: Patient workflow endpoints
  - Process single/batch patients
  - CRUD operations for patients
  - Workflow result retrieval

- **scheduling.py**: Scheduling optimization endpoints
  - Hungarian Algorithm execution
  - Resource availability queries

- **disruptions.py**: Disruption handling endpoints
  - Fallback strategy application
  - RL optimization
  - Disruption history

- **notifications.py**: Communication endpoints
  - Staff notifications
  - Dashboard updates

- **optimization.py**: System optimization endpoints
  - Genetic Algorithm execution
  - System metrics collection

### 2. Services (Business Logic Layer)
**Location**: `app/services/`

- **priority_service.py**: Reception Agent
  - `classify_priority_level()`: Rule-based classification
  - `get_priority_classification()`: Complete triage

- **scheduling_service.py**: Scheduling Agent
  - `HungarianScheduler`: Hungarian Algorithm implementation
  - `optimize_patient_assignment()`: Main scheduling function

- **disruption_service.py**: Exception Handling Agent
  - `QLearningAgent`: Q-Learning implementation
  - `execute_fallback_strategy()`: Rule-based fallback
  - `apply_reinforcement_learning()`: RL optimization

- **notification_service.py**: Assistant Agent
  - `send_notifications()`: Multi-channel notifications
  - `update_dashboard()`: Real-time dashboard updates

- **optimization_service.py**: System Optimizer
  - `GeneticAlgorithmOptimizer`: GA implementation
  - `collect_system_metrics()`: Metrics aggregation

- **workflow_service.py**: Workflow Orchestrator
  - `WorkflowOrchestrator`: Coordinates all agents
  - `process_patient()`: Main workflow function

### 3. Models (Data Layer)
**Location**: `app/models.py`

Pydantic models for:
- Request validation
- Response serialization
- Type safety
- Auto-documentation

### 4. Database (Persistence Layer)
**Location**: `app/database.py`

SQLAlchemy models:
- `PatientRecord`
- `AppointmentRecord`
- `DisruptionRecord`
- `MetricsRecord`
- `WorkflowResult`

---

## 🔄 Request Flow

### Example: Processing a Patient

```
1. HTTP POST /api/v1/patients/process
   ↓
2. Router: patients.py → process_patient()
   ↓
3. Validation: Pydantic model validates input
   ↓
4. Service: WorkflowOrchestrator.process_patient()
   ├─ PriorityService.get_priority_classification()
   ├─ SchedulingService.optimize_patient_assignment()
   ├─ DisruptionService (if needed)
   └─ NotificationService.send_notifications()
   ↓
5. Database: Save patient & workflow results
   ↓
6. Response: PatientWorkflowResult (JSON)
```

---

## 🧮 Algorithm Implementations

### Hungarian Algorithm
**File**: `app/services/scheduling_service.py`

```python
class HungarianScheduler:
    def create_cost_matrix(patients, resources):
        # Cost = wait_time × priority_factor × load_factor
        
    def solve_assignment():
        # Uses scipy.optimize.linear_sum_assignment
        # Returns optimal patient-resource pairs
```

**Complexity**: O(n³)
**Library**: SciPy

### Genetic Algorithm
**File**: `app/services/optimization_service.py`

```python
class GeneticAlgorithmOptimizer:
    def run_optimization():
        # 1. Initialize population
        # 2. Evaluate fitness
        # 3. Selection (tournament)
        # 4. Crossover
        # 5. Mutation
        # 6. Repeat for N generations
```

**Parameters**:
- Population: 50
- Generations: 100
- Mutation Rate: 0.1

### Q-Learning (RL)
**File**: `app/services/disruption_service.py`

```python
class QLearningAgent:
    def update_q_value(state, action, reward, next_state):
        # Q(s,a) = Q(s,a) + α[r + γ max Q(s',a') - Q(s,a)]
```

**Parameters**:
- Learning Rate (α): 0.1
- Discount Factor (γ): 0.9
- Epsilon (ε): 0.1

---

## 🗄️ Database Schema

### Entity Relationship Diagram

```
┌─────────────────┐
│  PatientRecord  │
├─────────────────┤
│ id (PK)         │
│ patient_id      │◄────┐
│ name            │     │
│ symptoms        │     │
│ severity_score  │     │
│ priority        │     │
└─────────────────┘     │
                        │
┌─────────────────┐     │
│ WorkflowResult  │     │
├─────────────────┤     │
│ id (PK)         │     │
│ patient_id (FK) │─────┘
│ priority_class  │
│ scheduling_res  │
│ notifications   │
└─────────────────┘

┌─────────────────┐
│ AppointmentRec  │
├─────────────────┤
│ id (PK)         │
│ patient_id (FK) │
│ appointment_type│
│ scheduled_time  │
│ status          │
└─────────────────┘

┌─────────────────┐
│ DisruptionRec   │
├─────────────────┤
│ id (PK)         │
│ disruption_type │
│ affected_res    │
│ resolution      │
└─────────────────┘
```

---

## 🔐 Security Considerations

### Current Implementation
- **CORS**: Enabled for all origins (development)
- **Validation**: Pydantic models validate all inputs
- **SQL Injection**: Protected by SQLAlchemy ORM

### Production Recommendations
1. **Authentication**: Add JWT tokens
2. **Authorization**: Role-based access control
3. **CORS**: Restrict to specific origins
4. **Rate Limiting**: Prevent API abuse
5. **HTTPS**: Use SSL/TLS in production
6. **Input Sanitization**: Additional validation layers

---

## 📊 Performance Optimization

### Async/Await
All database operations use async/await for non-blocking I/O:
```python
async def process_patient(patient: PatientInput, db: AsyncSession):
    # Non-blocking database operations
    await db.commit()
```

### Database Indexing
Indexes on frequently queried fields:
- `patient_id` (unique index)
- `created_at` (for time-based queries)

### Caching Opportunities
Future improvements:
- Redis for session caching
- Memoization for repeated calculations
- Resource availability caching

---

## 🧪 Testing Strategy

### Unit Tests
Test individual services:
```python
def test_priority_classification():
    result = PriorityService.classify_priority_level(
        symptoms=["chest pain"],
        severity_score=8,
        medical_history="heart condition",
        patient_age=65
    )
    assert result == 1  # Emergency
```

### Integration Tests
Test complete workflows:
```python
async def test_patient_workflow():
    result = await WorkflowOrchestrator.process_patient(patient_data)
    assert result.status == "completed"
```

### API Tests
Test endpoints:
```bash
python test_api.py
```

---

## 🚀 Deployment Architecture

### Development
```
uvicorn main:app --reload
```

### Production
```
┌─────────────────────────────────────┐
│         Load Balancer (Nginx)       │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   Uvicorn Workers (4 processes)     │
│   ├─ Worker 1                       │
│   ├─ Worker 2                       │
│   ├─ Worker 3                       │
│   └─ Worker 4                       │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│         SQLite Database             │
│   (or PostgreSQL for production)    │
└─────────────────────────────────────┘
```

### Docker Deployment
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

---

## 📈 Monitoring & Logging

### Metrics to Track
- Request latency
- Error rates
- Patient throughput
- Resource utilization
- Algorithm performance

### Logging Strategy
```python
import logging

logger = logging.getLogger(__name__)
logger.info(f"Processing patient {patient_id}")
logger.error(f"Error in workflow: {error}")
```

---

## 🔄 API Versioning

Current version: **v1**
Prefix: `/api/v1`

Future versions will maintain backward compatibility:
- `/api/v1` - Current stable version
- `/api/v2` - Future enhancements

---

## 📚 Additional Resources

- **FastAPI Docs**: https://fastapi.tiangolo.com
- **SQLAlchemy**: https://www.sqlalchemy.org
- **Pydantic**: https://docs.pydantic.dev
- **Hungarian Algorithm**: https://en.wikipedia.org/wiki/Hungarian_algorithm
- **Q-Learning**: https://en.wikipedia.org/wiki/Q-learning

---

**Last Updated**: 2026-02-11
**Version**: 1.0.0
