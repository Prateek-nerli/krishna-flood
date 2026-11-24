"""
tests/verify_compound_physics.py

Forensic verification suite for First-Principles Hydrological Physics Engine:
1. Copernicus GLO-30 96x96 DEM Patch & Compound Cross-Section Extraction
2. Divided-Channel HEC-RAS Conveyance & Bisection Depth Solver
3. Dynamic AMC-I/II/III Soil Moisture & Sentinel-2 NDVI Roughness
4. Unsteady Wave Attenuation & Empty Channel Storage Deficit (6h vs 12h vs 24h vs 48h)
5. Lateral Floodplain Expansion at Sangli
6. Full FastAPI Endpoints & Diagnostics Verification (including Thogunasi Farmland regression)
"""

import sys
import os
import json
import time
import numpy as np

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.main import app, get_amc_adjusted_cn
from ml_engine.hydraulic_cross_section import (
    extract_compound_cross_section,
    compute_divided_channel_conveyance,
    solve_water_depth_and_threshold,
    compute_backwater_afflux,
    compute_unsteady_wave_attenuation
)
from gis_engine.raster_lookup import get_copernicus_dem_patch

def run_physics_verification():
    print("=" * 80)
    print("FIRST-PRINCIPLES HYDROLOGICAL PHYSICS ENGINE VERIFICATION")
    print("=" * 80)

    # -------------------------------------------------------------
    # 1. COPERNICUS GLO-30 DEM PATCH & COMPOUND TRANSECT TEST
    # -------------------------------------------------------------
    print("\n[Test 1/6] Auditing Copernicus GLO-30 DEM Patch & Valley Cross-Section...")
    # Sangli coordinates
    sangli_lat, sangli_lon = 16.8538, 74.5569
    patch, transform, crs = get_copernicus_dem_patch(sangli_lat, sangli_lon, size_px=96)
    assert patch is not None, "DEM patch returned None"
    assert patch.shape == (96, 96), f"Expected shape (96, 96), got {patch.shape}"
    print(f"  DEM Patch Shape: {patch.shape} (2.88 km x 2.88 km coverage)")
    print(f"  Elevation Min:   {np.nanmin(patch):.1f} m | Max: {np.nanmax(patch):.1f} m MSL")
    assert np.nanmin(patch) > 400.0, f"Unrealistic elevation for Sangli: {np.nanmin(patch)}"

    # Compound Cross-Section Extraction
    profile = extract_compound_cross_section(
        lat=sangli_lat,
        lon=sangli_lon,
        river_lat=sangli_lat,
        river_lon=sangli_lon,
        dist_to_river_m=120.0,
        E_bed_r=538.0,
        W_live=120.0
    )
    offsets = profile["offsets"]
    z_elevs = profile["elevations"]
    print(f"  Transect Points:     {len(offsets)} (Station Span: {offsets[0]:.1f} m to {offsets[-1]:.1f} m)")
    print(f"  Live Channel Width:  {profile['W_live']:.1f} m")
    print(f"  Transect Min Elev:   {np.min(z_elevs):.2f} m | Max Elev: {np.max(z_elevs):.2f} m")
    assert len(offsets) >= 40, f"Expected >= 40 transect points, got {len(offsets)}"
    assert np.min(z_elevs) <= 538.5, f"Bed elevation not anchored properly: {np.min(z_elevs)}"
    print("  [PASS] Copernicus DEM patch and compound cross-section extraction verified.")

    # -------------------------------------------------------------
    # 2. DIVIDED-CHANNEL CONVEYANCE & BISECTION DEPTH SOLVER
    # -------------------------------------------------------------
    print("\n[Test 2/6] Auditing HEC-RAS Divided-Channel Conveyance & Convergence...")
    # Low flow (in-bank): 100 m3/s
    h_low, q_bf_low, top_w_low, v_low, q_ch_bf_low = solve_water_depth_and_threshold(
        Q_target_cumecs=100.0,
        Sf=0.00045,
        profile=profile,
        n_ch=0.032,
        n_fp=0.055,
        hand_m=10.0
    )
    # High flow (overbank): 3,500 m3/s
    h_high, q_bf_high, top_w_high, v_high, q_ch_bf_high = solve_water_depth_and_threshold(
        Q_target_cumecs=3500.0,
        Sf=0.00045,
        profile=profile,
        n_ch=0.032,
        n_fp=0.055,
        hand_m=10.0
    )
    print(f"  Physical Bankfull Capacity (H=HAND=10m): {q_bf_low:,.1f} m³/s ({q_bf_low*35.3147:,.0f} cfs)")
    print(f"  In-Bank Flow (100 m³/s):   Depth={h_low:.2f} m, Top Width={top_w_low:.1f} m, Velocity={v_low:.2f} m/s")
    print(f"  Overbank Flow (3500 m³/s): Depth={h_high:.2f} m, Top Width={top_w_high:.1f} m, Velocity={v_high:.2f} m/s")

    assert h_high > h_low, "Depth did not increase with flow"
    assert top_w_high > top_w_low, "Top width did not expand overbank"
    assert q_bf_low > 500.0, f"Bankfull capacity unreasonably low: {q_bf_low}"
    # Check that depth growth is sub-linear due to lateral expansion
    flow_ratio = 3500.0 / 100.0  # 35x flow increase
    depth_ratio = h_high / h_low # should be much less than 35x (typically 2-4x in compound channels)
    print(f"  Nonlinear Floodplain Relief Ratio: Flow ratio={flow_ratio:.1f}x vs Depth ratio={depth_ratio:.2f}x")
    assert depth_ratio < 8.0, f"Cross-section acted like a narrow pipe rather than a valley: {depth_ratio}"
    print("  [PASS] Divided-channel conveyance and lateral spreading verified.")

    # -------------------------------------------------------------
    # 3. DYNAMIC AMC-I/II/III RUNOFF & NDVI ROUGHNESS AUDIT
    # -------------------------------------------------------------
    print("\n[Test 3/6] Auditing Antecedent Moisture Condition (AMC) Curve Number Physics...")
    cn_dry, amc_dry = get_amc_adjusted_cn(p5_mm=10.0, cn_base=82.0)
    cn_norm, amc_norm = get_amc_adjusted_cn(p5_mm=42.0, cn_base=82.0)
    cn_wet, amc_wet = get_amc_adjusted_cn(p5_mm=75.0, cn_base=82.0)

    print(f"  Dry Antecedent (P5=10mm):   Class={amc_dry}  | CN={cn_dry:.1f} (High Soil Infiltration)")
    print(f"  Normal Antecedent (P5=42mm): Class={amc_norm} | CN={cn_norm:.1f} (Baseline Standard)")
    print(f"  Saturated Antecedent (P5=75mm): Class={amc_wet} | CN={cn_wet:.1f} (Flash Runoff Potential)")

    assert "AMC-I" in amc_dry and cn_dry < 70.0, f"Unexpected AMC-I result: {amc_dry}, {cn_dry}"
    assert "AMC-II" in amc_norm and abs(cn_norm - 82.0) < 0.1, f"Unexpected AMC-II result: {amc_norm}, {cn_norm}"
    assert "AMC-III" in amc_wet and cn_wet > 90.0, f"Unexpected AMC-III result: {amc_wet}, {cn_wet}"
    print("  [PASS] Dynamic AMC soil moisture adjustment verified.")

    # -------------------------------------------------------------
    # 4. UNSTEADY WAVE ATTENUATION & DAM RELEASE DURATION AUDIT
    # -------------------------------------------------------------
    print("\n[Test 4/6] Auditing Unsteady Wave Attenuation & Empty Storage Absorption...")
    # Release 150,000 cfs from Koyna into a 70 km reach
    # Test across 6h, 12h, 24h, 48h
    q_upstream = 150000.0
    reach_km = 70.0
    durations = [6.0, 12.0, 24.0, 48.0]
    outflows = []
    absorbed_pcts = []

    for dur in durations:
        q_out_cumecs, absorbed_pct = compute_unsteady_wave_attenuation(
            Q_pulse_cusecs=q_upstream,
            release_duration_hours=dur,
            dist_km=reach_km,
            Q_t0_cumecs=50.0,
            W_ch=120.0,
            H_bf=4.5,
            Sf=0.0003
        )
        q_out_cfs = q_out_cumecs * 35.3146667
        outflows.append(q_out_cfs)
        absorbed_pcts.append(absorbed_pct)
        print(f"  Release Duration = {dur:4.1f}h | Peak Arrival = {q_out_cfs:8,.0f} cfs | Storage Absorbed = {absorbed_pct:4.1f}%")

    # A short 6h flash wave should be absorbed more than a sustained 48h flood wave
    assert outflows[-1] > outflows[0], f"48h wave ({outflows[-1]}) should transmit more flow than 6h pulse ({outflows[0]})"
    assert absorbed_pcts[0] > absorbed_pcts[-1], f"6h pulse should suffer higher absorption than 48h release"
    print("  [PASS] Hydrograph dispersion and duration-dependent empty-channel absorption verified.")

    # -------------------------------------------------------------
    # 5. FASTAPI CLIENT: LATERAL EXPANSION & DIAGNOSTICS AT SANGLI
    # -------------------------------------------------------------
    print("\n[Test 5/6] Auditing FastAPI Hydraulic Diagnostics at Sangli (16.8538, 74.5569)...")
    client = TestClient(app)

    # 5a. Baseline
    resp_base = client.post("/api/v1/predict/coordinate", json={
        "latitude": sangli_lat, "longitude": sangli_lon,
        "release_duration_hours": 24.0
    })
    assert resp_base.status_code == 200, f"Sangli baseline failed: {resp_base.text}"
    base_data = resp_base.json()
    assert "hydraulic_diagnostics" in base_data, "hydraulic_diagnostics missing from response"
    diag_base = base_data["hydraulic_diagnostics"]
    print(f"  Baseline Diagnostics:")
    print(f"    Wetted Top Width:       {diag_base['wetted_top_width_m']} m")
    print(f"    Channel Velocity:       {diag_base['channel_velocity_ms']} m/s")
    print(f"    Floodplain Manning n:   {diag_base['manning_n_fp']} (NDVI={diag_base['mean_floodplain_ndvi']})")
    print(f"    Dynamic Soil AMC:       {diag_base['amc_class']} (CN={diag_base['cn_effective']})")
    print(f"    Dominant Mechanism:     {diag_base['flood_mechanism']}")
    print(f"    Empty Storage Absorbed: {diag_base['empty_channel_storage_absorbed_pct']}%")

    # 5b. High Dam Release (150,000 cfs from Koyna)
    resp_150 = client.post("/api/v1/predict/coordinate", json={
        "latitude": sangli_lat, "longitude": sangli_lon,
        "custom_dam_outflow_override": {"KOYNA": 150000.0},
        "release_duration_hours": 24.0
    })
    diag_150 = resp_150.json()["hydraulic_diagnostics"]
    f1_150 = resp_150.json()["forecasts"]["day_1_24h"]

    print(f"  Koyna 150,000 cfs Release (24h Duration):")
    print(f"    Predicted Day 1 Flow:   {f1_150['predicted_discharge_cusecs']:,.0f} cfs")
    print(f"    Predicted Depth:        {f1_150['predicted_river_depth_m']:.2f} m")
    print(f"    Wetted Top Width:       {diag_150['wetted_top_width_m']} m")
    print(f"    Flow Velocity:          {diag_150['channel_velocity_ms']} m/s")
    print(f"    Dominant Mechanism:     {diag_150['flood_mechanism']}")

    assert diag_150["wetted_top_width_m"] >= diag_base["wetted_top_width_m"], "Wetted top width should expand with flood"
    assert f1_150["predicted_discharge_cusecs"] > 50000.0, "Dam release not properly propagated to Sangli"
    print("  [PASS] FastAPI compound hydraulic diagnostics and lateral expansion verified.")

    # -------------------------------------------------------------
    # 6. THOGUNASI DRY FARMLAND REGRESSION & BACKWATER AFFLUX
    # -------------------------------------------------------------
    print("\n[Test 6/6] Auditing Thogunasi Dry Farmland Regression & Downstream Backwater Afflux...")
    # Thogunasi farmland
    resp_thog = client.post("/api/v1/predict/coordinate", json={
        "latitude": 16.0468, "longitude": 75.7315
    })
    assert resp_thog.status_code == 200, f"Thogunasi failed: {resp_thog.text}"
    thog_data = resp_thog.json()
    assert thog_data["is_water_body"] == False, "Regression: Thogunasi falsely marked as water body!"
    assert thog_data["forecasts"]["day_1_24h"]["will_submerge"] == False, "Regression: Thogunasi submerge alert triggered!"
    print(f"  Thogunasi Status: is_water_body={thog_data['is_water_body']} | will_submerge={thog_data['forecasts']['day_1_24h']['will_submerge']} | HAND={thog_data['hand_m']:.2f}m")

    # Backwater Afflux unit check
    h_final_conf, h_bw_conf, mech_conf = compute_backwater_afflux(
        E_bed_r=538.0,
        H_manning=2.5,
        WSE_down=545.0, # downstream water level = 7m above bed
        L_m=30000.0,
        alpha=1.0 # at downstream junction
    )
    h_final_up, h_bw_up, mech_up = compute_backwater_afflux(
        E_bed_r=538.0,
        H_manning=2.5,
        WSE_down=545.0,
        L_m=30000.0,
        alpha=0.0 # 30 km upstream
    )
    print(f"  Backwater Afflux at Boundary (alpha=1.0): H_final={h_final_conf:.2f} m, H_bw={h_bw_conf:.2f} m ({mech_conf})")
    print(f"  Backwater Afflux Upstream (alpha=0.0):    H_final={h_final_up:.2f} m, H_bw={h_bw_up:.2f} m ({mech_up})")
    assert h_bw_conf > h_bw_up, "Backwater depth should be higher near confluence than upstream"
    assert h_final_conf >= h_final_up, "Final water depth should reflect backwater control near confluence"
    print("  [PASS] Thogunasi dry farmland and downstream backwater afflux verified.")

    print("\n" + "=" * 80)
    print("ALL FIRST-PRINCIPLES HYDROLOGICAL PHYSICS CHECKS PASSED WITH ZERO ERRORS!")
    print("=" * 80)

if __name__ == "__main__":
    run_physics_verification()
