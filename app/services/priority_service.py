# app/services/priority_service.py
from typing import List
from app.models import PriorityClassification

# All valid cardiology test types
VALID_CARDIOLOGY_TESTS = {
    "ecg", "echocardiogram", "tmt", "angiogram",
    "troponin_test", "cardiac_ct", "bp_monitoring",
    "blood_test", "consultation", "icu",
    "stress_test",
}

# Normalise alternate spellings / old names to canonical keys
TEST_ALIASES = {
    "stress_test": "tmt",
    "echocardiagram": "echocardiogram",
    "echo": "echocardiogram",
    "ekg": "ecg",
    "troponin": "troponin_test",
    "ct": "cardiac_ct",
    "cardiac_ct_scan": "cardiac_ct",
    "bp": "bp_monitoring",
    "blood_pressure": "bp_monitoring",
    "angio": "angiogram",
}


def normalise_test(test: str) -> str:
    """Lower-case, underscore, then resolve alias."""
    key = test.lower().replace(" ", "_").strip()
    return TEST_ALIASES.get(key, key)


class PriorityService:
    """Service for patient priority classification (Reception Agent)"""

    @staticmethod
    def classify_priority_level(
        symptoms: List[str],
        severity_score: int,
        medical_history: str,
        patient_age: int = 50
    ) -> int:
        """
        Returns:
            1=Emergency → ICU, 2=Urgent, 3=Routine
        """
        emergency_symptoms = [
            "chest pain", "heart attack", "cardiac arrest", "severe shortness of breath",
            "unconscious", "severe bleeding", "stroke symptoms", "severe arrhythmia",
            "ventricular fibrillation", "vf", "vt", "ventricular tachycardia",
            "acute mi", "stemi", "nstemi", "complete heart block",
            "cardiac tamponade", "aortic dissection", "pulmonary embolism"
        ]
        urgent_symptoms = [
            "moderate chest pain", "palpitations", "mild shortness of breath",
            "dizziness", "syncope", "irregular heartbeat", "high blood pressure",
            "hypertension", "atrial fibrillation", "af", "flutter",
            "unstable angina", "decompensated heart failure"
        ]
        for symptom in symptoms:
            if any(em in symptom.lower() for em in emergency_symptoms):
                return 1
        if severity_score >= 8:
            return 1
        for symptom in symptoms:
            if any(ur in symptom.lower() for ur in urgent_symptoms):
                return 2
        if severity_score >= 5:
            return 2
        risk_factors = 0
        if patient_age > 65:
            risk_factors += 1
        if "heart" in medical_history.lower() or "cardiac" in medical_history.lower():
            risk_factors += 1
        if "diabetes" in medical_history.lower():
            risk_factors += 1
        if "hypertension" in medical_history.lower():
            risk_factors += 1
        if risk_factors >= 2 and severity_score >= 4:
            return 2
        return 3

    @staticmethod
    def get_priority_classification(
        symptoms: List[str],
        severity_score: int,
        medical_history: str,
        patient_age: int = 50
    ) -> PriorityClassification:
        priority_level = PriorityService.classify_priority_level(
            symptoms, severity_score, medical_history, patient_age
        )
        priority_descriptions = {
            1: "EMERGENCY — Transferred to ICU immediately. Life-threatening cardiac condition.",
            2: "URGENT — Serious condition, directed to consultation for prompt care.",
            3: "ROUTINE — Regular care, scheduled through consultation queue.",
        }
        wait_times = {1: 0, 2: 15, 3: 60}
        routing = {1: "ICU", 2: "Consultation", 3: "Consultation"}
        return PriorityClassification(
            priority_level=priority_level,
            priority_description=priority_descriptions.get(priority_level, "UNKNOWN"),
            estimated_wait_time_minutes=wait_times.get(priority_level, 30),
            triage_notes=(
                f"Classified as {routing.get(priority_level,'Consultation')} based on symptoms: "
                f"{', '.join(symptoms)} with severity {severity_score}/10"
            ),
        )
