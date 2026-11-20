"""
tests/verify_spatial_upgrade.py

Verification script for the India-WRIS Spatial & Hydraulic Precision Upgrade:
1. Thogunasi (16.0468, 75.7315) - Dry farmland near seasonal stream
2. Hipparagi Barrage - Snapped directly to Krishna River
3. Sangli (Irwin Bridge) - Snapped directly to Krishna River
4. Prakasam Barrage - River confluence and upstream DAG GAT attention
"""

import sys
import os
import json

# Add root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.main import app
import numpy as np

def run_checks():
    client = TestClient(app)
    
    print("=" * 80)
    print("WRIS SPATIAL & HYDRAULIC PRECISION VERIFICATION")
    print("=" * 80)

    # -------------------------------------------------------------
    # 1. THOGUNASI DRY FARMLAND AUDIT (16.0468, 75.7315)
    # -------------------------------------------------------------
    print("\n[Test 1/4] Auditing Thogunasi Dry Farmland (16.0468, 75.7315)...")
    resp_thog = client.post("/api/v1/predict/coordinate", json={"latitude": 16.0468, "longitude": 75.7315})
    assert resp_thog.status_code == 200, f"Thogunasi failed: {resp_thog.text}"
    thog = resp_thog.json()
    
    print(f"  Current State:       {thog['current_state']}")
    print(f"  Is Water Body:       {thog['is_water_body']}")
    print(f"  Water Body Name:     {thog['water_body_name']}")
    print(f"  HAND Vertical Diff:  {thog['hand_m']:.2f} m")
    print(f"  Distance to River:   {thog['distance_to_river_m']:.0f} m")
    print(f"  WRIS River Reach:    {thog['wris_river_name']}")
    print(f"  Local Drainage:      {thog.get('wris_data', {}).get('nearest_local_stream')}")
    print(f"  Ground Clearance:    {thog['live_telemetry']['ground_clearance_m']} m")
    print(f"  Day 1 Submerge:      {thog['forecasts']['day_1_24h']['will_submerge']}")
    print(f"  Day 1 Risk Level:    {thog['forecasts']['day_1_24h']['risk_level']}")
    
    # Assertions
    assert thog['is_water_body'] == False, "False Positive: Thogunasi dry farmland marked as water body!"
    assert thog['current_state'] == "NORMAL", f"Expected NORMAL, got {thog['current_state']}"
    assert thog['hand_m'] > 5.0, f"Expected HAND > 5m, got {thog['hand_m']}"
    assert thog['forecasts']['day_1_24h']['will_submerge'] == False, "Thogunasi should not submerge"
    assert thog['wris_data'].get('nearest_local_stream') is not None, "Missing seasonal stream identification"
    print("  [PASS] Thogunasi dry farmland correctly classified with zero false reservoir alert.")

    # -------------------------------------------------------------
    # 2. HIPPARAGI BARRAGE SNAP AUDIT
    # -------------------------------------------------------------
    print("\n[Test 2/4] Auditing Hipparagi Barrage Pin & Coordinates...")
    with open("seed_nodes.json", "r", encoding="utf-8") as f:
        nodes = json.load(f)
    node_map = {n['node_id']: n for n in nodes}
    hipp = node_map['HIPPARAGI']
    print(f"  Station:             {hipp['station_name']}")
    print(f"  Snapped Coordinates: ({hipp['lat']}, {hipp['lon']})")
    print(f"  GloFAS Coordinates:  ({hipp['glofas_lat']}, {hipp['glofas_lon']})")
    print(f"  WRIS River:          {hipp['wris_river_name']}")
    
    # Check that Hipparagi coordinates are within 10 meters of Krishna River
    from scipy.spatial import cKDTree
    data = np.load('data/krishna_dense_points.npz', allow_pickle=True)
    points = data['points']
    metadata = data['metadata']
    k_mask = metadata[:, 1] == 'Krishna River'
    k_tree = cKDTree(points[k_mask])
    d_deg, _ = k_tree.query([hipp['lat'], hipp['lon']])
    d_m = d_deg * 111320.0
    print(f"  Distance to Krishna River Polyline: {d_m:.2f} m")
    assert d_m < 10.0, f"Hipparagi not on Krishna River polyline: {d_m} m"
    print("  [PASS] Hipparagi Barrage correctly seated on Krishna River.")

    # -------------------------------------------------------------
    # 3. SANGLI IRWIN BRIDGE SNAP AUDIT
    # -------------------------------------------------------------
    print("\n[Test 3/4] Auditing Sangli Irwin Bridge Pin & Coordinates...")
    sang = node_map['SANGLI']
    print(f"  Station:             {sang['station_name']}")
    print(f"  Snapped Coordinates: ({sang['lat']}, {sang['lon']})")
    print(f"  GloFAS Coordinates:  ({sang['glofas_lat']}, {sang['glofas_lon']})")
    print(f"  WRIS River:          {sang['wris_river_name']}")
    d_deg, _ = k_tree.query([sang['lat'], sang['lon']])
    d_m = d_deg * 111320.0
    print(f"  Distance to Krishna River Polyline: {d_m:.2f} m")
    assert d_m < 10.0, f"Sangli not on Krishna River polyline: {d_m} m"
    print("  [PASS] Sangli Irwin Bridge correctly seated on Krishna River.")

    # -------------------------------------------------------------
    # 4. PRAKASAM BARRAGE CONFLUENCE & XAI AUDIT
    # -------------------------------------------------------------
    print("\n[Test 4/4] Auditing Prakasam Barrage Coordinate & XAI Upstream Contributors...")
    prak = node_map['PRAKASAM']
    resp_prak = client.post("/api/v1/predict/coordinate", json={"latitude": prak['lat'], "longitude": prak['lon']})
    assert resp_prak.status_code == 200, f"Prakasam failed: {resp_prak.text}"
    prak_pred = resp_prak.json()
    xai = prak_pred.get("xai_attribution", {})
    print(f"  Target XAI Station: {xai.get('target_xai_station')}")
    contribs = xai.get("spatial_attention", [])
    print(f"  Spatial Contributors Count: {len(contribs)}")
    for c in contribs:
        print(f"    - {c['station_name']} ({c['station_id']}): {c['attention_weight']*100:.2f}% | self_loop={c['is_self_loop']}")
    
    contrib_ids = [c['station_id'] for c in contribs]
    assert "PRAKASAM" in contrib_ids, "Prakasam self-loop missing"
    assert "PULICHINTALA" in contrib_ids, "Upstream Pulichintala tributary missing from Prakasam attention"
    print("  [PASS] Prakasam Barrage XAI correctly routes from Pulichintala and self-loop.")

    print("\n" + "=" * 80)
    print("ALL SPATIAL & HYDRAULIC VERIFICATION CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 80)

if __name__ == '__main__':
    run_checks()
