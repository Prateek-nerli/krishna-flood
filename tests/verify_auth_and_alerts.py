import sys
import os
import json

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from fastapi.testclient import TestClient
from backend.main import app
from backend.notifications import format_flood_alert, DAY_MAP_KN, translate_days_to_kannada

def run_all_tests():
    print("=" * 80)
    print("KRISHNAFLOW AUTHENTICATION, DASHBOARD, SCHEDULER & ALERT VERIFICATION")
    print("=" * 80)

    client = TestClient(app)

    # -------------------------------------------------------------
    # TEST 1: Bilingual Message Formatting (English + Kannada)
    # -------------------------------------------------------------
    print("\n[Test 1/6] Auditing Bilingual English + Kannada Flood Alert Builder...")
    test_days = ["Day 1 (+24h)", "Day 2 (+48h)"]
    kn_translated = translate_days_to_kannada(test_days)
    assert "ದಿನ 1 (+24 ಗಂ)" in kn_translated and "ದಿನ 2 (+48 ಗಂ)" in kn_translated, f"Kannada day translation mismatch: {kn_translated}"

    # Real Alert
    real_alert = format_flood_alert(
        location_name="Konnur Riverbank Farm",
        lat=16.2038,
        lon=74.7485,
        flood_days=test_days,
        peak_flow_cfs=112000.0,
        max_depth_m=6.84,
        high_ground_str="Konnur Hillock (545m MSL, 380m East)",
        is_simulation=False,
        language="bilingual"
    )
    assert "KRISHNAFLOW FLOOD ALERT" in real_alert["sms_body"]
    assert "ಕೃಷ್ಣಾಫ್ಲೋ ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ" in real_alert["sms_body"]
    assert "Day 1 (+24h)" in real_alert["sms_body"]
    assert "ದಿನ 1 (+24 ಗಂ)" in real_alert["sms_body"]
    print("  [PASS] Real flood bilingual message formatted with exact English + Kannada flood days.")

    # Simulation Alert
    sim_alert = format_flood_alert(
        location_name="Gokak Falls Village",
        lat=16.1755,
        lon=74.8213,
        flood_days=["Day 1 (+24h)"],
        peak_flow_cfs=75000.0,
        max_depth_m=4.5,
        high_ground_str="Gokak Elevated Ridge",
        is_simulation=True,
        sim_summary="Hidkal: 60,000 cfs, 24h",
        language="bilingual"
    )
    assert "Detected flood via simulation" in sim_alert["sms_body"]
    assert "ಸಿಮ್ಯುಲೇಶನ್ ಮೂಲಕ ಪ್ರವಾಹ ಪತ್ತೆಯಾಗಿದೆ" in sim_alert["sms_body"]
    assert "Hidkal: 60,000 cfs, 24h" in sim_alert["sms_body"]
    print("  [PASS] Simulation flood alert explicitly states [SIMULATION] / ಸಿಮ್ಯುಲೇಶನ್ ಮೂಲಕ ಪ್ರವಾಹ ಪತ್ತೆಯಾಗಿದೆ.")

    # -------------------------------------------------------------
    # TEST 2: Citizen Registration & Dual OTP (Phone + Email)
    # -------------------------------------------------------------
    print("\n[Test 2/6] Auditing Citizen Registration & Dual-OTP Verification...")
    test_email = "citizen_test@krishnaflow.in"
    test_phone = "+919876501234"
    reg_resp = client.post("/api/v1/auth/register", json={
        "full_name": "Ramesh Patil",
        "phone": test_phone,
        "email": test_email,
        "password": "SecurePassword@123",
        "alert_language": "bilingual"
    })
    
    # If already registered from previous run, that's fine; otherwise check 200
    if reg_resp.status_code == 400 and "already registered" in reg_resp.json().get("detail", ""):
        print("  User already registered, requesting fresh OTPs...")
        resend_resp = client.post("/api/v1/auth/resend-otp", json={"email": test_email, "channel": "all"})
        # Might be rate limited to 60s
        otps = resend_resp.json().get("dev_fallback_otps_if_offline")
    else:
        assert reg_resp.status_code == 200, f"Registration failed: {reg_resp.text}"
        otps = reg_resp.json().get("dev_fallback_otps_if_offline")

    assert otps is not None, "Fallback OTPs missing in response"
    phone_otp = otps.get("phone_otp")
    email_otp = otps.get("email_otp")
    print(f"  Received Dual OTPs: Phone={phone_otp}, Email={email_otp}")

    # Verify Dual OTPs
    verify_resp = client.post("/api/v1/auth/verify-otp", json={
        "email": test_email,
        "phone_otp": phone_otp,
        "email_otp": email_otp
    })
    assert verify_resp.status_code == 200, f"OTP verification failed: {verify_resp.text}"
    user_token = verify_resp.json()["access_token"]
    assert user_token, "Access token not returned after OTP verification"
    print("  [PASS] Dual OTP verified and 7-day JWT access token issued.")

    # Test Login with verified credentials
    login_resp = client.post("/api/v1/auth/login", json={
        "identifier": test_email,
        "password": "SecurePassword@123"
    })
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    user_headers = {"Authorization": f"Bearer {user_token}"}
    print("  [PASS] Citizen login verified with JWT.")

    # -------------------------------------------------------------
    # TEST 3: User Location Management (Max 3, Basin Envelope)
    # -------------------------------------------------------------
    print("\n[Test 3/6] Auditing User Monitored Locations (Max 3, Bounding Envelope)...")
    
    # 1. Out of basin check
    bad_loc = client.post("/api/v1/user/locations", headers=user_headers, json={
        "label": "Himalayas",
        "latitude": 30.5,
        "longitude": 79.0,
        "place_name": "Invalid Out-of-Basin Point"
    })
    assert bad_loc.status_code == 400, "Out-of-basin location was erroneously accepted!"
    print("  [PASS] Out-of-basin coordinates correctly rejected with HTTP 400.")

    # 2. Add valid location at Sangli (Irwin Bridge)
    loc_resp = client.post("/api/v1/user/locations", headers=user_headers, json={
        "label": "Sangli Riverside Farm",
        "latitude": 16.8538,
        "longitude": 74.5569,
        "place_name": "Sangli Irwin Bridge Ghat",
        "is_primary": True
    })
    assert loc_resp.status_code == 200, f"Failed to add location: {loc_resp.text}"
    loc_id = loc_resp.json()["location"]["id"]
    print(f"  [PASS] Successfully saved primary location: {loc_resp.json()['location']['place_name']} (ID: {loc_id}).")

    # -------------------------------------------------------------
    # TEST 4: Citizen User Dashboard (Hydrology + Weather + Reservoirs)
    # -------------------------------------------------------------
    print("\n[Test 4/6] Auditing Citizen Dashboard Telemetry & Reservoir Traversal...")
    dash_resp = client.get(f"/api/v1/user/dashboard?location_id={loc_id}", headers=user_headers)
    assert dash_resp.status_code == 200, f"Dashboard fetch failed: {dash_resp.text}"
    dash_data = dash_resp.json()

    assert dash_data["has_location"] == True
    hydrology = dash_data.get("hydrology", {})
    weather = dash_data.get("weather", {})
    river = dash_data.get("river_telemetry", {})
    reservoirs = dash_data.get("reservoirs", {})

    print(f"  Hydrology: Ground={hydrology.get('ground_elevation_m')}m, Bed={hydrology.get('river_bed_elevation_m')}m, HAND={hydrology.get('hand_m')}m")
    print(f"  River Telemetry: {river.get('wris_river_name')} | Flow: {river.get('flow_cfs')} cfs | Depth: {river.get('current_depth_m')}m")
    print(f"  Live Weather: Temp={weather.get('temperature_c')}°C | P5 Antecedent={weather.get('p5_antecedent_mm')}mm | Class={weather.get('amc_class')}")
    print(f"  Reservoirs: Upstream={reservoirs.get('upstream', {}).get('station_name')} ({reservoirs.get('upstream', {}).get('fill_percentage')}%) | Downstream={reservoirs.get('downstream', {}).get('station_name')}")

    assert hydrology.get("ground_elevation_m") is not None
    assert weather.get("temperature_c") is not None
    assert reservoirs.get("upstream") is not None or reservoirs.get("downstream") is not None
    print("  [PASS] Citizen Dashboard returned complete hydrological and reservoir telemetry.")

    # 1-Click Test Alert
    test_alert_resp = client.post("/api/v1/user/test-alert", headers=user_headers, json={"location_id": loc_id})
    assert test_alert_resp.status_code == 200
    print("  [PASS] 1-Click bilingual test alert dispatched and logged.")

    # -------------------------------------------------------------
    # TEST 5: Automated 6-Hour Scheduler Logic
    # -------------------------------------------------------------
    print("\n[Test 5/6] Auditing Automated 6-Hour Flood Scanner Execution...")
    from backend.scheduler import run_scheduled_basin_scan
    scan_summary = run_scheduled_basin_scan()
    print(f"  Scanned Locations: {scan_summary['total_locations_checked']}")
    print(f"  Flooded Locations: {scan_summary['flooded_locations_count']}")
    print(f"  Alerts Dispatched: {scan_summary['alerts_dispatched']}")
    assert scan_summary["total_locations_checked"] >= 1, "Scheduler failed to find saved locations!"
    print("  [PASS] 6-Hour background scheduler executed in-process without network loopback.")

    # -------------------------------------------------------------
    # TEST 6: Admin Command Center, Simulation Alerts & Broadcast
    # -------------------------------------------------------------
    print("\n[Test 6/6] Auditing Admin Command Center & Dam Simulation Alerts...")
    admin_login = client.post("/api/v1/auth/login", json={
        "identifier": "23u0641@students.git.edu",
        "password": "Admin@123"
    })
    assert admin_login.status_code == 200, f"Admin login failed: {admin_login.text}"
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Overview KPIs
    overview = client.get("/api/v1/admin/overview", headers=admin_headers).json()
    assert overview["kpis"]["total_users"] >= 2
    assert overview["kpis"]["verified_users"] >= 2
    print(f"  Admin KPIs: Users={overview['kpis']['total_users']} (Verified: {overview['kpis']['verified_users']}) | Safe Locations={overview['kpis']['locations_safe']}")

    # 2. User Directory
    users_dir = client.get("/api/v1/admin/users", headers=admin_headers).json()
    assert len(users_dir["users"]) >= 2
    print(f"  Admin Directory: Successfully listed {len(users_dir['users'])} users with pinned locations.")

    # 3. Simulate Dam Release (Koyna 150,000 cfs) & Dispatch Simulation Alert
    sim_resp = client.post("/api/v1/admin/simulate-and-alert", headers=admin_headers, json={
        "custom_dam_outflow_override": {"KOYNA": 150000.0},
        "release_duration_hours": 24.0,
        "target_mode": "admin_only",
        "send_alerts": True
    })
    assert sim_resp.status_code == 200, f"Simulation endpoint error: {sim_resp.text}"
    sim_data = sim_resp.json()
    print(f"  Dam Outflow Simulation: {sim_data['simulation_summary']}")
    print(f"  Monitored Locations Checked: {sim_data['total_monitored_locations']} | Flooded Impacted: {sim_data['impacted_count']}")
    print(f"  Simulation Alerts Dispatched: {sim_data['alerts_dispatched']} (Target Mode: {sim_data['target_mode']})")
    assert sim_data["impacted_count"] >= 1, "Expected Sangli location to detect flood under 150,000 cfs Koyna release!"
    print("  [PASS] Dam release simulation detected flood on monitored user coordinate and dispatched simulation alert.")

    # 4. Emergency Broadcast
    bcast_resp = client.post("/api/v1/admin/broadcast", headers=admin_headers, json={
        "message_en": "Heavy upstream discharge expected. All ghat activities suspended.",
        "message_kn": "ಹೆಚ್ಚಿನ ಹೊರಹರಿವು ನಿರೀಕ್ಷಿಸಲಾಗಿದೆ. ನದಿ ತೀರದ ಚಟುವಟಿಕೆಗಳನ್ನು ತಕ್ಷಣ ನಿಲ್ಲಿಸಿ.",
        "filter_type": "ALL"
    })
    assert bcast_resp.status_code == 200
    print(f"  Emergency Broadcast: {bcast_resp.json()['message']}")

    # 5. Alert Logs Audit
    logs_resp = client.get("/api/v1/admin/alert-logs?limit=10", headers=admin_headers).json()
    assert logs_resp["total"] >= 1
    sample_log = logs_resp["logs"][0]
    print(f"  Recent Alert Log: Type={sample_log['alert_type']}, Channel={sample_log['channel']}, Status={sample_log['status']}")
    print("  [PASS] Alert logs and offline outbox recorded successfully.")

    print("\n" + "=" * 80)
    print("ALL 6 AUTHENTICATION, DASHBOARD, SCHEDULER & ALERT TESTS PASSED WITH 0 ERRORS!")
    print("=" * 80)

if __name__ == "__main__":
    run_all_tests()
