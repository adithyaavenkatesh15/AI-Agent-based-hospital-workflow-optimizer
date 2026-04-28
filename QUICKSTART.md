# Quick Start Guide

## 🚀 Get Started in 3 Steps

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Run the Server
```bash
python main.py
```

### Step 3: Open Your Browser
Go to: **http://localhost:8000/docs**

---

## 📝 Try Your First Request

### Using Swagger UI (Easiest)
1. Go to http://localhost:8000/docs
2. Click on `POST /api/v1/patients/process`
3. Click "Try it out"
4. Paste this JSON:
```json
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
5. Click "Execute"
6. See the complete workflow result!

### Using cURL
```bash
curl -X POST "http://localhost:8000/api/v1/patients/process" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P001",
    "name": "John Smith",
    "symptoms": ["chest pain"],
    "severity_score": 8,
    "required_appointments": ["consultation"],
    "medical_history": "heart condition",
    "age": 65
  }'
```

### Using Python
```python
import requests

patient = {
    "patient_id": "P001",
    "name": "John Smith",
    "symptoms": ["chest pain"],
    "severity_score": 8,
    "required_appointments": ["consultation"],
    "medical_history": "heart condition",
    "age": 65
}

response = requests.post(
    "http://localhost:8000/api/v1/patients/process",
    json=patient
)

print(response.json())
```

---

## 🧪 Run All Tests
```bash
python test_api.py
```

---

## 📚 What Happens When You Process a Patient?

1. **Reception Agent** → Classifies priority (Emergency/Urgent/Routine)
2. **Scheduling Agent** → Finds optimal time slot using Hungarian Algorithm
3. **Exception Handler** → Checks for disruptions
4. **Assistant Agent** → Sends notifications to staff

All in one API call! 🎉

---

## 🎯 Key Endpoints

| Endpoint | What It Does |
|----------|--------------|
| `POST /api/v1/patients/process` | Process a patient through complete workflow |
| `POST /api/v1/patients/process-batch` | Process multiple patients |
| `GET /api/v1/patients/` | Get all patients |
| `POST /api/v1/scheduling/optimize` | Run Hungarian Algorithm |
| `POST /api/v1/optimization/genetic-algorithm` | Run GA optimization |
| `GET /api/v1/optimization/metrics` | Get system metrics |

---

## 📖 Full Documentation
See **README.md** for complete API documentation.

---

## ❓ Troubleshooting

**Server won't start?**
- Make sure port 8000 is not in use
- Check that all dependencies are installed

**Database errors?**
- The database is created automatically
- Delete `hospital_workflow.db` to reset

**Import errors?**
- Make sure you're in the `hospital_fastapi` directory
- Reinstall dependencies: `pip install -r requirements.txt`

---

**Happy Coding! 🏥✨**
