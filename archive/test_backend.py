print("Importing TestClient...")
from fastapi.testclient import TestClient
print("Importing app...")
from backend.main import app
import json

print("Initializing TestClient...")
client = TestClient(app)
print("TestClient initialized.")

def run_tests():
    coords = [
        {"name": "Sangli riverbank", "lat": 16.8524, "lon": 74.5815},
        {"name": "Intermediate floodplain (Sangli-Kurundwad)", "lat": 16.7650, "lon": 74.5850},
        {"name": "Vijayawada", "lat": 16.5062, "lon": 80.6047}
    ]
    
    for c in coords:
        print(f"\n--- Testing POST /api/v1/predict/coordinate for {c['name']} ---")
        response = client.post("/api/v1/predict/coordinate", json={
            "latitude": c['lat'],
            "longitude": c['lon']
        })
        print(json.dumps(response.json(), indent=2))
        
    print("\n--- Testing POST /api/v1/simulate/release ---")
    res2 = client.post("/api/v1/simulate/release", json={
        "node_id": "KOYNA",
        "outflow_cusecs": 150000.0
    })
    print(json.dumps(res2.json(), indent=2))
    
    print("\n--- Testing POST /api/v1/predict/coordinate (EMERGENCY OVERRIDE at Sangli) ---")
    res3 = client.post("/api/v1/predict/coordinate", json={
        "latitude": 16.8524, 
        "longitude": 74.5815,
        "custom_dam_outflow_override": {"KOYNA": 150000.0, "WARANA": 60000.0}
    })
    print(json.dumps(res3.json(), indent=2))

if __name__ == "__main__":
    run_tests()
    print("\n--- Testing Benakanaholi ---")
    res_b = client.post("/api/v1/predict/coordinate", json={"latitude": 16.07752, "longitude": 74.52053})
    print(json.dumps(res_b.json(), indent=2))
    print("\n--- Testing Nirvanatti ---")
    res_n = client.post("/api/v1/predict/coordinate", json={"latitude": 16.14863, "longitude": 74.65154})
    print(json.dumps(res_n.json(), indent=2))
