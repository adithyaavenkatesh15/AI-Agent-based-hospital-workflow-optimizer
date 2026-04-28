# Hospital Workflow Optimization System - FastAPI Version

## 🏥 Overview

This is a **complete FastAPI conversion** of the ADK-based Hospital Workflow Optimization System. It implements a multi-agent AI system for intelligent hospital workflow management in cardiology departments.

### Key Features

- ✅ **4 AI Agents** working sequentially
- ✅ **RESTful API** with FastAPI
- ✅ **Async/Await** for high performance
- ✅ **SQLite Database** with SQLAlchemy ORM
- ✅ **Hungarian Algorithm** for optimal scheduling
- ✅ **Genetic Algorithm** for multi-objective optimization
- ✅ **Q-Learning (RL)** for adaptive disruption handling
- ✅ **Interactive API Documentation** (Swagger UI)
- ✅ **Complete CRUD Operations**

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd hospital_fastapi
pip install -r requirements.txt
```

### 2. Set Up Environment

```bash
# Copy example environment file
copy .env.example .env

# Edit .env if needed (optional)
```

### 3. Run the Server

```bash
# Method 1: Using uvicorn directly
uvicorn main:app --reload

# Method 2: Using Python
python main.py
```

### 4. Access the API

- **API Server**: http://localhost:8000
- **Interactive Docs (Swagger)**: http://localhost:8000/docs
- **Alternative Docs (ReDoc)**: http://localhost:8000/redoc

---

## 📚 API Endpoints

### Patient Workflow

#### Process Single Patient
```http
POST /api/v1/patients/process
Content-Type: application/json

{
  "patient_id": "P001",
  "name": "John Smith",
  "symptoms": ["chest pain", "shortness of breath"],
  "severity_score": 8,
  "required_appointments": ["consultation", "echocardiogram"],
  "medical_history": "previous heart condition",
  "age": 65
}
```

#### Process Multiple Patients
```http
POST /api/v1/patients/process-batch
Content-Type: application/json

[
  { patient_data_1 },
  { patient_data_2 }
]
```

#### Get All Patients
```http
GET /api/v1/patients/?skip=0&limit=100
```

#### Get Specific Patient
```http
GET /api/v1/patients/P001
```

#### Get Patient Workflow Results
```http
GET /api/v1/patients/P001/workflow
```

### Scheduling

#### Optimize Scheduling (Hungarian Algorithm)
```http
POST /api/v1/scheduling/optimize
Content-Type: application/json

{
  "patient_requirements": [
    {
      "patient_id": "P001",
      "priority": 1,
      "required_appointments": ["consultation"]
    }
  ]
}
```

#### Get Available Resources
```http
GET /api/v1/scheduling/resources
```

### Disruption Handling

#### Apply Fallback Strategy
```http
POST /api/v1/disruptions/fallback
Content-Type: application/json

{
  "disruption_type": "equipment_failure",
  "affected_resources": ["ECG-1", "ECG-2"],
  "current_schedule": {},
  "available_alternatives": {}
}
```

#### Apply RL Optimization
```http
POST /api/v1/disruptions/rl-optimize
Content-Type: application/json

{
  "historical_disruptions": [],
  "current_situation": {
    "disruption_type": "staff_absence",
    "severity": 1
  },
  "performance_metrics": {}
}
```

#### Get Disruption History
```http
GET /api/v1/disruptions/history
```

### Notifications

#### Send Notification
```http
POST /api/v1/notifications/send
Content-Type: application/json

{
  "notification_type": "patient_scheduled",
  "recipients": ["doctor1", "nurse1"],
  "message_content": {
    "title": "New Patient Alert",
    "patient_id": "P001"
  },
  "priority_level": "high"
}
```

#### Update Dashboard
```http
POST /api/v1/notifications/dashboard/update
Content-Type: application/json

{
  "update_type": "schedule",
  "schedule_data": {},
  "metrics_data": {}
}
```

### Optimization

#### Run Genetic Algorithm
```http
POST /api/v1/optimization/genetic-algorithm?population_size=50&generations=100
```

#### Get System Metrics
```http
GET /api/v1/optimization/metrics?time_period_hours=24
```

### System Information

#### Root Endpoint
```http
GET /
```

#### Health Check
```http
GET /health
```

#### System Info
```http
GET /api/v1/info
```

---

## 🤖 Agent Architecture

### 1. Reception Agent (Priority Classification)
- **Endpoint**: Part of `/patients/process`
- **Function**: Classifies patients into Emergency/Urgent/Routine
- **Algorithm**: Rule-based medical urgency scoring
- **Output**: Priority level (1-3) with estimated wait time

### 2. Scheduling Agent (Hungarian Algorithm)
- **Endpoint**: `/scheduling/optimize`
- **Function**: Optimal patient-to-resource assignment
- **Algorithm**: Hungarian Algorithm (O(n³))
- **Output**: Optimal assignments with utilization rates

### 3. Exception Handling Agent (RL)
- **Endpoints**: `/disruptions/fallback`, `/disruptions/rl-optimize`
- **Function**: Handles disruptions adaptively
- **Algorithms**: Rule-based fallback + Q-Learning
- **Output**: Rescheduling strategies with confidence scores

### 4. Assistant Agent (Notifications)
- **Endpoints**: `/notifications/send`, `/notifications/dashboard/update`
- **Function**: Staff communication and dashboard updates
- **Output**: Notification delivery status

---

## 📊 Database Schema

### Tables

1. **patients** - Patient records
2. **appointments** - Scheduled appointments
3. **disruptions** - Disruption events
4. **metrics** - System performance metrics
5. **workflow_results** - Complete workflow execution results

### Auto-initialization

The database is automatically created on first run with all necessary tables.

---

## 🧪 Testing with Sample Data

### Using cURL

```bash
# Process a single patient
curl -X POST "http://localhost:8000/api/v1/patients/process" \
  -H "Content-Type: application/json" \
  -d @sample_patients.json
```

### Using Python

```python
import requests
import json

# Load sample patients
with open('sample_patients.json', 'r') as f:
    patients = json.load(f)

# Process first patient
response = requests.post(
    'http://localhost:8000/api/v1/patients/process',
    json=patients[0]
)

print(response.json())
```

### Using Swagger UI

1. Go to http://localhost:8000/docs
2. Click on any endpoint
3. Click "Try it out"
4. Fill in the request body
5. Click "Execute"

---

## 🔧 Configuration

Edit `.env` file to customize:

```env
HOSPITAL_NAME=City General Hospital
DEPARTMENT=Cardiology
DATABASE_URL=sqlite+aiosqlite:///./hospital_workflow.db
OPTIMIZATION_INTERVAL_HOURS=4
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=True
```

---

## 📁 Project Structure

```
hospital_fastapi/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── config.py            # Configuration settings
│   ├── models.py            # Pydantic models
│   ├── database.py          # SQLAlchemy models & DB setup
│   ├── routers/             # API endpoints
│   │   ├── patients.py      # Patient workflow endpoints
│   │   ├── scheduling.py    # Scheduling endpoints
│   │   ├── disruptions.py   # Disruption handling endpoints
│   │   ├── notifications.py # Notification endpoints
│   │   └── optimization.py  # Optimization endpoints
│   └── services/            # Business logic (Agents)
│       ├── priority_service.py      # Reception Agent
│       ├── scheduling_service.py    # Scheduling Agent
│       ├── disruption_service.py    # Exception Handler Agent
│       ├── notification_service.py  # Assistant Agent
│       ├── optimization_service.py  # Genetic Algorithm
│       └── workflow_service.py      # Workflow Orchestrator
├── main.py                  # Entry point
├── requirements.txt         # Dependencies
├── .env.example            # Environment template
├── .gitignore              # Git ignore rules
├── sample_patients.json    # Sample data
└── README.md               # This file
```

---

## 🎯 Key Algorithms

### Hungarian Algorithm
- **Purpose**: Optimal patient-resource assignment
- **Complexity**: O(n³)
- **Implementation**: `app/services/scheduling_service.py`

### Genetic Algorithm
- **Purpose**: Multi-objective system optimization
- **Parameters**: Population=50, Generations=100
- **Implementation**: `app/services/optimization_service.py`

### Q-Learning (RL)
- **Purpose**: Adaptive disruption handling
- **Parameters**: α=0.1, γ=0.9, ε=0.1
- **Implementation**: `app/services/disruption_service.py`

---

## 🔄 Workflow Example

```
1. POST /api/v1/patients/process
   ↓
2. Reception Agent: Priority Classification
   ↓
3. Scheduling Agent: Hungarian Algorithm
   ↓
4. Exception Handler: Check Disruptions
   ↓
5. Assistant Agent: Send Notifications
   ↓
6. Return Complete Workflow Result
```

---

## 📈 Performance

- **Async/Await**: Non-blocking I/O for high concurrency
- **Database**: SQLite with async support (aiosqlite)
- **Optimization**: Efficient algorithms (Hungarian, GA, Q-Learning)
- **Scalability**: Can process multiple patients concurrently

---

## 🆚 Comparison with ADK Version

| Feature | ADK Version | FastAPI Version |
|---------|-------------|-----------------|
| Framework | Google ADK | FastAPI |
| API Type | Agent-based | RESTful API |
| Documentation | Manual | Auto-generated (Swagger) |
| Database | SQLite (sync) | SQLite (async) |
| Performance | Good | Excellent (async) |
| Deployment | ADK server | Standard web server |
| Testing | Manual | API testing tools |
| Scalability | Limited | High (async) |

---

## 🚀 Deployment

### Local Development
```bash
uvicorn main:app --reload
```

### Production
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker (Optional)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 📝 License

This project is for educational and demonstration purposes.

---

## 🤝 Contributing

This is a complete, production-ready FastAPI conversion of the hospital workflow system. All features from the original ADK version have been implemented with additional improvements:

- ✅ RESTful API design
- ✅ Interactive documentation
- ✅ Async database operations
- ✅ Comprehensive error handling
- ✅ CRUD operations for all entities
- ✅ Sample data included
- ✅ Easy deployment

---

## 📞 Support

For issues or questions:
1. Check the interactive docs at `/docs`
2. Review the API examples above
3. Examine the sample data in `sample_patients.json`

---

**Built with FastAPI, SQLAlchemy, NumPy, and SciPy** 🚀
