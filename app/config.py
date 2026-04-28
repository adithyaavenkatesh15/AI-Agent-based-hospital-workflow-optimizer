# app/config.py
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings and configuration"""

    # Application
    app_name: str = "Hospital Workflow Optimization System"
    hospital_name: str = "City General Hospital"
    department: str = "Cardiology"
    debug: bool = True

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./hospital_workflow.db"

    # Optimization
    optimization_interval_hours: int = 4

    # Hungarian Algorithm — priority weights (higher = lower cost = assigned first)
    priority_weights: dict = {1: 10, 2: 5, 3: 1}

    # Genetic Algorithm
    ga_population_size: int = 50
    ga_generations: int = 100

    # ── Resource Pool ─────────────────────────────────────────────────────────
    # Each resource = one parallel unit (doctor/cabin/machine)
    # capacity = how many patients that unit can handle simultaneously (usually 1)
    # units = number of parallel units available
    #
    # WORKFLOW RULE:
    #   - ALL non-emergency patients MUST complete consultation before any tests.
    #   - Emergency patients → directly to ICU; ICU doctors prescribe tests.
    #   - ICU patients bypass consultation; tests ordered by ICU doctor.
    #   - consultation duration = 2 minutes (simulation speed)
    #   - After consultation is done, tests are allocated optimally via Hungarian Algo.
    #   - Tests run SEQUENTIALLY per patient (one at a time).
    resource_pool: dict = {
        "icu": {
            "units": 10,          # 10 ICU beds
            "capacity_per_unit": 1,
            "duration_minutes": 60,   # ICU stay (simulation)
            "complexity": 5,
            "is_icu": True,
            "units_info": [
                {"id": "ICU-001", "name": "ICU Bed 1",  "specialty": "Cardiac ICU"},
                {"id": "ICU-002", "name": "ICU Bed 2",  "specialty": "Cardiac ICU"},
                {"id": "ICU-003", "name": "ICU Bed 3",  "specialty": "Cardiac ICU"},
                {"id": "ICU-004", "name": "ICU Bed 4",  "specialty": "Cardiac ICU"},
                {"id": "ICU-005", "name": "ICU Bed 5",  "specialty": "Cardiac ICU"},
                {"id": "ICU-006", "name": "ICU Bed 6",  "specialty": "Cardiac ICU"},
                {"id": "ICU-007", "name": "ICU Bed 7",  "specialty": "Cardiac ICU"},
                {"id": "ICU-008", "name": "ICU Bed 8",  "specialty": "Cardiac ICU"},
                {"id": "ICU-009", "name": "ICU Bed 9",  "specialty": "Cardiac ICU"},
                {"id": "ICU-010", "name": "ICU Bed 10", "specialty": "Cardiac ICU"},
            ]
        },
        "consultation": {
            "units": 10,          # 10 doctors → 10 patients can be seen simultaneously
            "capacity_per_unit": 1,
            "duration_minutes": 2,   # 2 minutes per consultation (simulation)
            "complexity": 3,
            "units_info": [
                {"id": "DOC-001", "name": "Dr. Sarah Chen",        "specialty": "General Cardiology"},
                {"id": "DOC-002", "name": "Dr. Marcus Thorne",     "specialty": "Interventional Cardiology"},
                {"id": "DOC-003", "name": "Dr. Priya Patel",       "specialty": "Echocardiography"},
                {"id": "DOC-004", "name": "Dr. Arjun Mehta",       "specialty": "Cardiac Electrophysiology"},
                {"id": "DOC-005", "name": "Dr. Emily Watson",      "specialty": "Heart Failure & Transplant"},
                {"id": "DOC-006", "name": "Dr. Ravi Kumar",        "specialty": "Preventive Cardiology"},
                {"id": "DOC-007", "name": "Dr. Aisha Noor",        "specialty": "Cardiac Imaging"},
                {"id": "DOC-008", "name": "Dr. James O'Brien",     "specialty": "Interventional Cardiology"},
                {"id": "DOC-009", "name": "Dr. Lin Wei",           "specialty": "Cardiac Rehabilitation"},
                {"id": "DOC-010", "name": "Dr. Sofia Fernandez",   "specialty": "General Cardiology"},
            ]
        },
        "blood_test": {
            "units": 6,           # 6 cabins → 6 patients simultaneously
            "capacity_per_unit": 1,
            "duration_minutes": 10,
            "complexity": 1,
            "units_info": [
                {"id": "BT-001", "name": "Blood Test Cabin 1", "specialty": "General Blood Work"},
                {"id": "BT-002", "name": "Blood Test Cabin 2", "specialty": "Cardiac Enzymes"},
                {"id": "BT-003", "name": "Blood Test Cabin 3", "specialty": "Lipid Profile"},
                {"id": "BT-004", "name": "Blood Test Cabin 4", "specialty": "CBC & Metabolic"},
                {"id": "BT-005", "name": "Blood Test Cabin 5", "specialty": "Coagulation"},
                {"id": "BT-006", "name": "Blood Test Cabin 6", "specialty": "Troponin / BNP"},
            ]
        },
        "ecg": {
            "units": 5,
            "capacity_per_unit": 1,
            "duration_minutes": 15,
            "complexity": 2,
            "units_info": [
                {"id": "ECG-001", "name": "ECG Machine 1", "specialty": "12-Lead ECG"},
                {"id": "ECG-002", "name": "ECG Machine 2", "specialty": "Holter / Stress ECG"},
                {"id": "ECG-003", "name": "ECG Machine 3", "specialty": "12-Lead ECG"},
                {"id": "ECG-004", "name": "ECG Machine 4", "specialty": "Holter / Stress ECG"},
                {"id": "ECG-005", "name": "ECG Machine 5", "specialty": "12-Lead ECG"},
            ]
        },
        "echocardiogram": {
            "units": 7,
            "capacity_per_unit": 1,
            "duration_minutes": 30,
            "complexity": 4,
            "units_info": [
                {"id": "ECHO-001", "name": "Echo Lab A", "specialty": "2D/3D Echo"},
                {"id": "ECHO-002", "name": "Echo Lab B", "specialty": "Stress Echo"},
                {"id": "ECHO-003", "name": "Echo Lab C", "specialty": "2D/3D Echo"},
                {"id": "ECHO-004", "name": "Echo Lab D", "specialty": "Stress Echo"},
                {"id": "ECHO-005", "name": "Echo Lab E", "specialty": "2D/3D Echo"},
                {"id": "ECHO-006", "name": "Echo Lab F", "specialty": "Stress Echo"},
                {"id": "ECHO-007", "name": "Echo Lab G", "specialty": "2D/3D Echo"},
            ]
        },
        "tmt": {
            "units": 5,
            "capacity_per_unit": 1,
            "duration_minutes": 15,
            "complexity": 4,
            "units_info": [
                {"id": "TMT-001", "name": "TMT Room 1", "specialty": "Treadmill Stress Test"},
                {"id": "TMT-002", "name": "TMT Room 2", "specialty": "Pharmacological Stress"},
            ]
        },
        "angiogram": {
            "units": 3,
            "capacity_per_unit": 1,
            "duration_minutes": 60,
            "complexity": 5,
            "units_info": [
                {"id": "ANGIO-001", "name": "Cath Lab 1", "specialty": "Coronary Angiogram"},
                {"id": "ANGIO-002", "name": "Cath Lab 2", "specialty": "Peripheral Angiogram"},
                {"id": "ANGIO-003", "name": "Cath Lab 3", "specialty": "Coronary Angiogram"},
            ]
        },
        "troponin_test": {
            "units": 4,
            "capacity_per_unit": 1,
            "duration_minutes": 20,
            "complexity": 2,
            "units_info": [
                {"id": "TROP-001", "name": "Troponin Lab 1", "specialty": "High-Sensitivity Troponin"},
                {"id": "TROP-002", "name": "Troponin Lab 2", "specialty": "Troponin I/T"},
                {"id": "TROP-003", "name": "Troponin Lab 3", "specialty": "Serial Troponin"},
                {"id": "TROP-004", "name": "Troponin Lab 4", "specialty": "Point-of-Care Troponin"},
            ]
        },
        "cardiac_ct": {
            "units": 4,
            "capacity_per_unit": 1,
            "duration_minutes": 45,
            "complexity": 5,
            "units_info": [
                {"id": "CCT-001", "name": "Cardiac CT Scanner", "specialty": "Coronary CT Angiography"},
                {"id": "CCT-002", "name": "Cardiac CT Scanner 2", "specialty": "Coronary CT Angiography"},
                {"id": "CCT-003", "name": "Cardiac CT Scanner 3", "specialty": "Coronary CT Angiography"},
                {"id": "CCT-004", "name": "Cardiac CT Scanner 4", "specialty": "Coronary CT Angiography"},
            ]
        },
        # Legacy alias kept for backward compatibility
        "stress_test": {
            "units": 0,
            "capacity_per_unit": 1,
            "duration_minutes": 45,
            "complexity": 4,
            "units_info": []
        },
    }

    # Overflow fallback: if resource full, try alternatives
    overflow_fallback: dict = {
        "consultation": [],
        "icu": [],                                 # no alternative for ICU
        "echocardiogram": ["cardiac_ct", "tmt"],
        "tmt": ["echocardiogram"],
        "stress_test": ["tmt", "echocardiogram"],  # legacy alias
        "ecg": [],
        "blood_test": ["troponin_test"],
        "troponin_test": ["blood_test"],
        "angiogram": [],
        "cardiac_ct": ["echocardiogram"],
    }

    # Severity → allocation priority multiplier
    # Higher severity patients get assigned to earliest/fastest available unit
    severity_priority_map: dict = {
        10: 1,   # Critical → absolute priority (cost factor 1 = cheapest = assigned first)
        9:  1,
        8:  2,
        7:  2,
        6:  3,
        5:  4,
        4:  5,
        3:  6,
        2:  7,
        1:  8,   # Minimal → lowest priority
    }

    # Reinforcement Learning
    rl_learning_rate: float = 0.1
    rl_discount_factor: float = 0.9
    rl_epsilon: float = 0.1

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()