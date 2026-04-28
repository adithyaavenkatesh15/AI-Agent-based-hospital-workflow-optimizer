# test_api.py
"""
Test script for Hospital Workflow Optimization API

Run this after starting the server to test all endpoints.
"""
import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/v1"


def print_section(title):
    """Print a formatted section header"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def test_root():
    """Test root endpoint"""
    print_section("Testing Root Endpoint")
    response = requests.get(f"{BASE_URL}/")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.status_code == 200


def test_health():
    """Test health check"""
    print_section("Testing Health Check")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.status_code == 200


def test_system_info():
    """Test system info"""
    print_section("Testing System Info")
    response = requests.get(f"{API_URL}/info")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.status_code == 200


def test_process_patient():
    """Test processing a single patient"""
    print_section("Testing Patient Processing (Complete Workflow)")
    
    patient_data = {
        "patient_id": "TEST001",
        "name": "Test Patient",
        "symptoms": ["chest pain", "shortness of breath"],
        "severity_score": 8,
        "required_appointments": ["consultation", "echocardiogram"],
        "medical_history": "previous heart condition",
        "age": 65
    }
    
    response = requests.post(
        f"{API_URL}/patients/process",
        json=patient_data
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print("\n✅ Patient Processed Successfully!")
        print(f"\nPatient: {result['patient_name']} ({result['patient_id']})")
        print(f"Priority: {result['priority_classification']['priority_level']} - {result['priority_classification']['priority_description']}")
        print(f"Estimated Wait: {result['priority_classification']['estimated_wait_time_minutes']} minutes")
        
        if result.get('scheduling_result'):
            print(f"\nScheduling:")
            print(f"  Total Cost: {result['scheduling_result']['total_cost']:.2f}")
            print(f"  Assignments: {len(result['scheduling_result']['optimal_assignments'])}")
            
        if result.get('notifications'):
            print(f"\nNotifications:")
            print(f"  Sent: {result['notifications']['notifications_sent']}")
            print(f"  Failed: {len(result['notifications']['failed_deliveries'])}")
        
        print(f"\nStatus: {result['status']}")
        print(f"Processed At: {result['processed_at']}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_process_batch():
    """Test processing multiple patients"""
    print_section("Testing Batch Patient Processing")
    
    # Load sample patients
    try:
        with open('sample_patients.json', 'r') as f:
            patients = json.load(f)
    except FileNotFoundError:
        print("⚠️  sample_patients.json not found, using inline data")
        patients = [
            {
                "patient_id": "BATCH001",
                "name": "Batch Patient 1",
                "symptoms": ["palpitations"],
                "severity_score": 5,
                "required_appointments": ["consultation"],
                "medical_history": "no significant history",
                "age": 45
            },
            {
                "patient_id": "BATCH002",
                "name": "Batch Patient 2",
                "symptoms": ["chest pain"],
                "severity_score": 7,
                "required_appointments": ["consultation", "ecg"],
                "medical_history": "hypertension",
                "age": 60
            }
        ]
    
    response = requests.post(
        f"{API_URL}/patients/process-batch",
        json=patients[:2]  # Process first 2 patients
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        results = response.json()
        print(f"\n✅ Processed {len(results)} patients successfully!")
        for i, result in enumerate(results, 1):
            print(f"\n{i}. {result['patient_name']} - Priority {result['priority_classification']['priority_level']}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_get_patients():
    """Test getting all patients"""
    print_section("Testing Get All Patients")
    
    response = requests.get(f"{API_URL}/patients/")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        patients = response.json()
        print(f"\n✅ Found {len(patients)} patients in database")
        for patient in patients[:5]:  # Show first 5
            print(f"  - {patient['name']} ({patient['patient_id']}) - Priority {patient['priority']}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_scheduling():
    """Test scheduling optimization"""
    print_section("Testing Scheduling Optimization (Hungarian Algorithm)")
    
    patient_requirements = [
        {
            "patient_id": "SCHED001",
            "priority": 1,
            "required_appointments": ["consultation"]
        }
    ]
    
    response = requests.post(
        f"{API_URL}/scheduling/optimize",
        json={"patient_requirements": patient_requirements}
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ Scheduling Optimized!")
        print(f"Total Cost: {result['total_cost']:.2f}")
        print(f"Assignments: {len(result['optimal_assignments'])}")
        print(f"Resource Utilization: {json.dumps(result['resource_utilization'], indent=2)}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_disruption_handling():
    """Test disruption handling"""
    print_section("Testing Disruption Handling (Fallback Strategy)")
    
    disruption_data = {
        "disruption_type": "equipment_failure",
        "affected_resources": ["ECG-1", "ECG-2"],
        "current_schedule": {},
        "available_alternatives": {}
    }
    
    response = requests.post(
        f"{API_URL}/disruptions/fallback",
        json=disruption_data
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ Fallback Strategy Applied!")
        print(f"\nImmediate Actions:")
        for action in result['immediate_actions']:
            print(f"  - {action}")
        print(f"\nEstimated Impact:")
        print(json.dumps(result['estimated_impact'], indent=2))
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_rl_optimization():
    """Test RL optimization"""
    print_section("Testing RL Optimization (Q-Learning)")
    
    rl_data = {
        "historical_disruptions": [],
        "current_situation": {
            "disruption_type": "staff_absence",
            "severity": 1
        },
        "performance_metrics": {}
    }
    
    response = requests.post(
        f"{API_URL}/disruptions/rl-optimize",
        json=rl_data
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ RL Optimization Complete!")
        print(f"\nRecommendations:")
        for rec in result['rl_recommendations']:
            print(f"  - {rec}")
        print(f"\nConfidence: {result['confidence_scores']}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_genetic_algorithm():
    """Test genetic algorithm optimization"""
    print_section("Testing Genetic Algorithm (Multi-Objective Optimization)")
    
    response = requests.post(
        f"{API_URL}/optimization/genetic-algorithm",
        params={
            "population_size": 20,
            "generations": 10
        }
    )
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ Genetic Algorithm Complete!")
        print(f"Pareto Solutions: {len(result['pareto_solutions'])}")
        print(f"Best Solution: {json.dumps(result['best_solution'], indent=2)}")
        print(f"Optimization Metrics: {json.dumps(result['optimization_metrics'], indent=2)}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def test_system_metrics():
    """Test system metrics collection"""
    print_section("Testing System Metrics Collection")
    
    response = requests.get(f"{API_URL}/optimization/metrics")
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ Metrics Collected!")
        print(f"\nPatient Throughput:")
        print(json.dumps(result['patient_throughput'], indent=2))
        print(f"\nResource Utilization:")
        print(json.dumps(result['resource_utilization'], indent=2))
        print(f"\nSystem Efficiency: {result['system_efficiency']:.2%}")
    else:
        print(f"Error: {response.text}")
    
    return response.status_code == 200


def run_all_tests():
    """Run all tests"""
    print("\n" + "🏥" * 40)
    print("  HOSPITAL WORKFLOW OPTIMIZATION API - TEST SUITE")
    print("🏥" * 40)
    
    tests = [
        ("Root Endpoint", test_root),
        ("Health Check", test_health),
        ("System Info", test_system_info),
        ("Process Single Patient", test_process_patient),
        ("Process Batch Patients", test_process_batch),
        ("Get All Patients", test_get_patients),
        ("Scheduling Optimization", test_scheduling),
        ("Disruption Handling", test_disruption_handling),
        ("RL Optimization", test_rl_optimization),
        ("Genetic Algorithm", test_genetic_algorithm),
        ("System Metrics", test_system_metrics)
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"\n❌ Test failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    print_section("TEST SUMMARY")
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n{'='*80}")
    print(f"  Results: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    print("\n⚠️  Make sure the server is running on http://localhost:8000")
    print("   Start it with: python main.py\n")
    
    input("Press Enter to start tests...")
    
    run_all_tests()
