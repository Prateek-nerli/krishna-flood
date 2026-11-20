import os
import json
import math
import time
import datetime
import pickle
from typing import Optional, Dict, Any, List

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.schemas import (
    PredictRequest, PredictResponse, ForecastDay,
    LiveTelemetry, SimulateReleaseRequest
)
from gis_engine.raster_lookup import get_merit_hydro_data, haversine
from ml_engine.model_tft_gat import ST_Attention_GAT, build_basin_adjacency
from etl_pipeline.open_meteo import get_live_basin_matrix, get_live_river_discharge

app = FastAPI(title="KrishnaFlow AI Backend - IEEE Standard Hydrological Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global runtime cache
simulation_state: Dict[str, float] = {}
nodes_cache: List[Dict[str, Any]] = []
node_to_idx: Dict[str, int] = {}
feature_config: Dict[str, Any] = {}
training_metrics: Dict[str, Any] = {}
scaler = None
model: Optional[ST_Attention_GAT] = None
basin_adj: Optional[torch.Tensor] = None

# Scaling vectors (vectorized to eliminate sklearn warnings)
q_mean: float = 2.97
q_scale: float = 2.39
means_vec: np.ndarray = np.zeros(6, dtype=np.float32)
scales_vec: np.ones = np.ones(6, dtype=np.float32)

def load_ml_assets():
    global scaler, model, nodes_cache, node_to_idx, basin_adj
    global feature_config, training_metrics, q_mean, q_scale, means_vec, scales_vec
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 1. Load seed nodes
    seed_nodes_path = os.path.join(base_dir, 'seed_nodes.json')
    with open(seed_nodes_path, 'r', encoding='utf-8') as f:
        nodes_cache = json.load(f)
    node_to_idx = {n['node_id']: i for i, n in enumerate(nodes_cache)}
    num_nodes = len(nodes_cache)
    
    # 2. Build River Directed Adjacency Tensor
    basin_adj = build_basin_adjacency(nodes_cache, verbose=False)
    
    # 3. Load feature configuration & normalization constants
    feat_cfg_path = os.path.join(base_dir, 'models', 'feature_config.json')
    if os.path.exists(feat_cfg_path):
        with open(feat_cfg_path, 'r', encoding='utf-8') as f:
            feature_config = json.load(f)
        means_vec = np.array(feature_config.get('means', [0.0]*6), dtype=np.float32)
        scales_vec = np.array(feature_config.get('scales', [1.0]*6), dtype=np.float32)
        q_mean = float(feature_config.get('q_mean', means_vec[0]))
        q_scale = float(feature_config.get('q_scale', scales_vec[0]))

    # 4. Load Training Metrics
    metrics_path = os.path.join(base_dir, 'models', 'training_metrics.json')
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path, 'r', encoding='utf-8') as f:
                training_metrics = json.load(f)
        except Exception as e:
            print("Warning loading training metrics:", e)

    # 5. Load Scaler & PyTorch ST-Attention-GAT Checkpoint
    scaler_path = os.path.join(base_dir, 'models', 'scaler.pkl')
    if os.path.exists(scaler_path):
        try:
            with open(scaler_path, 'rb') as f:
                scaler = pickle.load(f)
        except Exception as e:
            print("Warning loading scaler.pkl:", e)

    weights_path = os.path.join(base_dir, 'models', 'krishna_tft_gat.pt')
    if os.path.exists(weights_path):
        try:
            model = ST_Attention_GAT(
                num_nodes=num_nodes,
                input_dim=6,
                hidden_dim=32,
                seq_len=14,
                pred_horizon=3,
                use_gat=True,
                dropout=0.0
            )
            state_dict = torch.load(weights_path, weights_only=False)
            if isinstance(state_dict, dict) and "model_state_dict" in state_dict:
                model.load_state_dict(state_dict["model_state_dict"])
            else:
                model.load_state_dict(state_dict)
            model.eval()
            print(f"[Backend] Successfully loaded ST-Attention-GAT model with {num_nodes} nodes and 26 river DAG edges.")
        except Exception as e:
            print("[Backend] Error loading ST-Attention-GAT checkpoint:", e)
            model = None

# Initial load on module startup
load_ml_assets()

def propagate_dam_overrides(overrides: Dict[str, float]) -> Dict[str, float]:
    """
    Physically routes dam slider releases downstream strictly along the directed River DAG.
    Decays release flow by 0.92 per reach hop.
    Upstream or non-connected stations receive ZERO added flow (strict spatial isolation).
    """
    flow_additions_cumecs = {n['node_id']: 0.0 for n in nodes_cache}
    
    # Direct additions at specific nodes
    for nid, cusecs in overrides.items():
        if nid in flow_additions_cumecs and nid != "ACTIVE_REACH":
            flow_additions_cumecs[nid] = float(cusecs) / 35.3146667
            
    # Build downstream edges
    node_set = set(nodes_cache_ids())
    edges: Dict[str, List[str]] = {}
    in_degree: Dict[str, int] = {n['node_id']: 0 for n in nodes_cache}
    for n in nodes_cache:
        d = n.get('downstream_node')
        if d and d in node_set:
            if n['node_id'] not in edges:
                edges[n['node_id']] = []
            edges[n['node_id']].append(d)
            in_degree[d] += 1
            
    # Topological sort (Kahn's algorithm)
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    routed_additions = {nid: 0.0 for nid in nodes_cache_ids()}

    # Initialize with direct releases
    for nid, add_q in flow_additions_cumecs.items():
        if nid in routed_additions:
            routed_additions[nid] = add_q

    while queue:
        u = queue.pop(0)
        if u in edges:
            for v in edges[u]:
                if v in routed_additions:
                    routed_additions[v] += routed_additions[u] * 0.92
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
                    
    return routed_additions

def nodes_cache_ids() -> List[str]:
    return [n['node_id'] for n in nodes_cache]

def compute_scs_cn_runoff_depth(daily_precip_mm: float, slope_deg: float, cn: float = 82.0) -> float:
    """
    Computes SCS-CN surface runoff accumulation depth in meters.
    CN=82 for Krishna basin Deccan Black Cotton Soil (Vertisols) under AMC II.
    """
    if daily_precip_mm <= 0.0:
        return 0.0
    S_mm = (25400.0 / cn) - 254.0
    Ia_mm = 0.2 * S_mm
    if daily_precip_mm > Ia_mm:
        q_runoff_mm = ((daily_precip_mm - Ia_mm) ** 2) / (daily_precip_mm - Ia_mm + S_mm)
    else:
        q_runoff_mm = 0.0
    # Slope effect: steep slope causes rapid accumulation
    slope_multiplier = 1.0 + math.sin(math.radians(max(0.0, slope_deg)))
    runoff_m = (q_runoff_mm / 1000.0) * slope_multiplier
    return round(float(runoff_m), 3)

def compute_stage_and_threshold(q_m3s: float, hand_m: float, C_manning: float, is_water_body: bool):
    """
    Calibrated stage-discharge curve and bankfull threshold.
    Unified low-flow threshold at 45.0 m3/s.
    """
    q_safe = max(0.0, float(q_m3s))
    if q_safe <= 45.0:
        stage_m = round(0.85 * ((max(q_safe, 5.0) / 45.0) ** 0.38), 2)
    else:
        stage_m = round(0.85 + ((q_safe / max(C_manning, 1.0)) ** (3.0 / 5.0)), 2)
        
    if is_water_body:
        stage_m += 2.5

    if is_water_body or hand_m <= 0.2:
        bankfull_threshold_cusecs = 0.0
    else:
        h_eff = max(0.0, hand_m - 0.85)
        bankfull_threshold_m3s = C_manning * (h_eff ** (5.0 / 3.0))
        bankfull_threshold_cusecs = round(bankfull_threshold_m3s * 35.3147, 0)

    return stage_m, bankfull_threshold_cusecs

BASIN_POLYGON = [
    [73.35, 19.35], [73.55, 17.95], [73.65, 16.90], [73.85, 15.85], [74.35, 15.10], 
    [75.15, 13.90], [75.60, 13.35], [76.65, 13.65], [77.60, 14.55], [78.95, 15.35], 
    [80.15, 15.65], [81.15, 15.75], [81.20, 16.65], [80.70, 17.65], [79.65, 18.10], 
    [78.35, 18.05], [76.95, 18.45], [75.45, 19.15], [73.35, 19.35]
]

def is_point_in_polygon(lat: float, lon: float, polygon: List[List[float]]) -> bool:
    x, y = lon, lat
    inside = False
    for i in range(len(polygon)):
        j = i - 1 if i != 0 else len(polygon) - 1
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        if intersect:
            inside = not inside
    return inside

# ====================================================================
# REST API ENDPOINTS
# ====================================================================

@app.get("/api/v1/model/metrics")
def get_model_metrics():
    """
    Returns the real IEEE ablation study and model performance benchmarks.
    """
    if not training_metrics:
        load_ml_assets()
    return training_metrics

@app.get("/api/v1/basin/wris-stats")
def get_wris_stats():
    """
    Returns macro hydrological summary for Krishna Basin.
    """
    total_len = sum(n.get('dist_km', 0.0) for n in nodes_cache)
    reservoirs = [n['station_name'] for n in nodes_cache if n.get('node_type') == 'RESERVOIR']
    return {
        "basin_name": "Krishna River Basin",
        "total_monitored_stations": len(nodes_cache),
        "total_mainstem_reach_km": round(total_len, 1),
        "monitored_major_reservoirs": reservoirs,
        "telemetry_providers": ["ECMWF GloFAS v4", "Open-Meteo API", "CWC WRIS"]
    }

@app.get("/api/v1/basin/nodes")
def get_basin_nodes():
    return {"nodes": nodes_cache}

@app.post("/api/v1/simulate/release")
def simulate_release(req: SimulateReleaseRequest):
    simulation_state[req.node_id] = req.outflow_cusecs
    return {"status": "success", "message": f"Overrode outflow for {req.node_id} to {req.outflow_cusecs} cusecs"}

@app.post("/api/v1/predict/coordinate", response_model=PredictResponse)
def predict_coordinate(req: PredictRequest):
    lat, lon = req.latitude, req.longitude
    
    # 1. Basin boundary check
    if not is_point_in_polygon(lat, lon, BASIN_POLYGON):
        raise HTTPException(status_code=400, detail="NOT UNDER KRISHNA BASIN")
        
    terrain = get_merit_hydro_data(lat, lon)
    if terrain is None or terrain['distance_to_river_m'] > 95000:
        raise HTTPException(status_code=400, detail="NOT UNDER KRISHNA BASIN")
        
    slope_deg = math.degrees(math.atan(terrain['tan_beta']))
    
    # 2. Water Body / Reservoir Detection
    is_water_body = bool(terrain.get('is_water_body_raster', False))
    water_body_name = "Active River Channel" if is_water_body else None
    reservoir_node = None
    
    if is_water_body:
        for n in nodes_cache:
            if n.get('node_type') == 'RESERVOIR':
                d = haversine(lat, lon, n['lat'], n['lon'])
                if d < 3000.0 and n['bed_elev_m'] < terrain['elv']:
                    reservoir_node = n
                    water_body_name = n['station_name']
                    terrain['E_bed_r'] = n['bed_elev_m']
                    terrain['hnd'] = 0.0
                    break

    up_id = terrain['upstream_node_id']
    down_id = terrain['downstream_node_id']
    reach_alpha = float(terrain.get('reach_fraction_alpha', 0.5))
    
    up_idx = node_to_idx.get(up_id, 0)
    down_idx = node_to_idx.get(down_id, min(1, len(nodes_cache)-1))

    # 3. Live 27-Node Basin Telemetry
    basin_data = get_live_basin_matrix(nodes_cache)
    up_data = basin_data['nodes'].get(up_id, {})
    down_data = basin_data['nodes'].get(down_id, {})

    up_dis = up_data.get('discharge', [0.0]*21)
    down_dis = down_data.get('discharge', [0.0]*21)
    up_precip = up_data.get('precip_sum', [0.0]*17)
    down_precip = down_data.get('precip_sum', [0.0]*17)

    # Dynamic today_idx (past 14 days are indices 0..13; index 14 is T+0)
    today_idx = 14
    dates = up_data.get('dates', [])
    today_str = datetime.date.today().isoformat()
    if today_str in dates:
        today_idx = dates.index(today_str)
    elif len(up_dis) > 14:
        today_idx = 14

    # 4. Spatially Isolated Dam Slider Overrides
    active_overrides = dict(simulation_state)
    if req.custom_dam_outflow_override:
        for k, v in req.custom_dam_outflow_override.items():
            if v is not None:
                active_overrides[k] = float(v)

    # Routed dam flow addition strictly via DAG (0.92 per hop)
    routed_dam_cumecs = propagate_dam_overrides(active_overrides)
    
    # Check for direct ACTIVE_REACH slider override
    active_reach_cusecs = float(active_overrides.get("ACTIVE_REACH", 0.0))

    # 5. Assemble ST-Attention-GAT Input Tensor [1, N, 14, 6]
    num_nodes = len(nodes_cache)
    x_input = np.zeros((1, num_nodes, 14, 6), dtype=np.float32)
    
    # Calculate Day of Year cyclical features
    doy = datetime.date.today().timetuple().tm_yday
    sin_doy = math.sin(2.0 * math.pi * doy / 365.25)
    cos_doy = math.cos(2.0 * math.pi * doy / 365.25)

    for i, node in enumerate(nodes_cache):
        nid = node['node_id']
        nd = basin_data['nodes'].get(nid, {})
        dis_arr = nd.get('discharge', [])
        p_arr = nd.get('precip_sum', [])
        et_arr = nd.get('et0', [])

        # Slice 14 lag days ending at today_idx
        start_t = max(0, today_idx - 13)
        q_slice = dis_arr[start_t : today_idx + 1] if len(dis_arr) >= today_idx + 1 else [0.0]*14
        while len(q_slice) < 14:
            q_slice.insert(0, q_slice[0] if q_slice else 0.0)

        p_slice = p_arr[:14] if len(p_arr) >= 14 else [0.0]*14
        while len(p_slice) < 14:
            p_slice.append(0.0)

        et_slice = et_arr[:14] if len(et_arr) >= 14 else [0.0]*14
        while len(et_slice) < 14:
            et_slice.append(4.0)

        for t in range(14):
            q_val = float(q_slice[t])
            p_val = float(p_slice[t])
            # Rolling 3-day precip
            p3d_val = float(sum(p_slice[max(0, t-2):t+1]))
            et_val = float(et_slice[t])

            x_input[0, i, t, 0] = math.log1p(max(0.0, q_val))
            x_input[0, i, t, 1] = p_val
            x_input[0, i, t, 2] = p3d_val
            x_input[0, i, t, 3] = et_val
            x_input[0, i, t, 4] = sin_doy
            x_input[0, i, t, 5] = cos_doy

    # Vectorized normalization: (X - means) / scales (ZERO sklearn warnings)
    x_scaled = (x_input - means_vec.reshape(1, 1, 1, 6)) / np.where(scales_vec.reshape(1, 1, 1, 6) == 0, 1.0, scales_vec.reshape(1, 1, 1, 6))
    x_tensor = torch.tensor(x_scaled, dtype=torch.float32)

    # 6. Model Forward Pass with Attention Extraction
    spat_attn_matrix = None
    temp_attn_vector = None
    preds_phys = np.zeros((num_nodes, 3), dtype=np.float32)

    if model is not None and basin_adj is not None:
        with torch.no_grad():
            preds_out, attn_info = model(x_tensor, basin_adj, return_attention=True)
            # unscale predictions: pred_scaled * q_scale + q_mean
            preds_scaled_np = preds_out.cpu().numpy()[0] # [N, 3]
            pred_log = preds_scaled_np * q_scale + q_mean
            preds_phys = np.expm1(np.clip(pred_log, 0.0, 11.5))

            if attn_info:
                spat_attn_matrix = attn_info.get("spatial")
                if spat_attn_matrix is not None:
                    spat_attn_matrix = spat_attn_matrix[0].cpu().numpy() # [N, N]
                temp_attn_all = attn_info.get("temporal")
                if temp_attn_all is not None:
                    temp_attn_vector = temp_attn_all[0, up_idx].cpu().numpy().tolist() # [14]

    # Channel hydraulics parameters (Dynamic First-Principles Manning formulation)
    hand_m = float(terrain['hnd'])
    r_len = float(terrain.get('wris_data', {}).get('river_length_km', 250.0) or 250.0)
    
    # Hydraulic energy slope S_0 based on upstream to downstream bed drop
    E_bed_up = float(nodes_cache[up_idx].get('bed_elev_m', 500.0))
    E_bed_down = float(nodes_cache[down_idx].get('bed_elev_m', 480.0))
    reach_dist_km = float(nodes_cache[up_idx].get('dist_km', 50.0) or 50.0)
    bed_drop_m = max(1.0, E_bed_up - E_bed_down)
    reach_len_m = max(1000.0, reach_dist_km * 1000.0)
    S_0 = max(0.00010, min(0.00150, bed_drop_m / reach_len_m))
    
    # Active channel width W_live
    W_live = float(terrain.get('wth', 150.0) or 150.0)
    if W_live <= 5.0:
        W_live = 450.0 if r_len >= 800 else (280.0 if r_len >= 200 else (160.0 if r_len >= 60 else 70.0))
        
    # Manning n = 0.035 alluvial natural bed conveyance: C_channel = (W_live * sqrt(S_0)) / n
    C_channel = max(40.0, (W_live * math.sqrt(S_0)) / 0.035)
    dist_shore = float(terrain.get('dist_r', 50.0) or 50.0)
    overbank_factor = 1.0 + 0.00085 * min(dist_shore, 2500.0)
    C_manning = C_channel * overbank_factor

    # 7. Generate Forecasts for T+1, T+2, T+3
    forecasts = {}
    horizons = ["day_1_24h", "day_2_48h", "day_3_72h"]

    for h_idx, name in enumerate(horizons):
        # GloFAS baseline for horizon h
        glo_up_idx = today_idx + 1 + h_idx
        glo_down_idx = today_idx + 1 + h_idx

        q_up_glo = up_dis[glo_up_idx] if glo_up_idx < len(up_dis) else up_dis[-1]
        q_down_glo = down_dis[glo_down_idx] if glo_down_idx < len(down_dis) else down_dis[-1]
        q_local_glofas_cumecs = max(0.0, q_up_glo + reach_alpha * (q_down_glo - q_up_glo))

        # ML Forecast for horizon h
        q_up_ml = preds_phys[up_idx, h_idx] if preds_phys is not None else q_up_glo
        q_down_ml = preds_phys[down_idx, h_idx] if preds_phys is not None else q_down_glo
        q_local_ml_cumecs = max(0.0, q_up_ml + reach_alpha * (q_down_ml - q_up_ml))

        # Routed Dam Additions for Upstream and Downstream nodes
        dam_add_up = routed_dam_cumecs.get(up_id, 0.0)
        dam_add_down = routed_dam_cumecs.get(down_id, 0.0)
        dam_reach_addition = dam_add_up + reach_alpha * (dam_add_down - dam_add_up)

        # Total combined flow
        q_final_cumecs = q_local_ml_cumecs + dam_reach_addition
        if active_reach_cusecs > 0.0:
            q_final_cumecs += (active_reach_cusecs / 35.3146667)

        q_final_cusecs = q_final_cumecs * 35.3146667

        # SCS-CN Slope Runoff for Horizon Day (CN=82 Deccan Black Cotton Soil)
        precip_day_mm = float(up_precip[min(h_idx, len(up_precip)-1)]) if up_precip else 0.0
        slope_runoff_m = compute_scs_cn_runoff_depth(precip_day_mm, slope_deg, cn=82.0)

        # Stage and submersion
        pred_depth_m, bankfull_cusecs = compute_stage_and_threshold(q_final_cumecs, hand_m, C_manning, is_water_body)
        wse_local = terrain['E_bed_r'] + pred_depth_m

        if is_water_body or q_final_cusecs >= bankfull_cusecs or pred_depth_m >= hand_m:
            will_submerge = True
            submersion_depth_m = round(max(0.25, pred_depth_m - hand_m + slope_runoff_m), 2)
            safety_margin_m = 0.0
            risk_level = "CRITICAL"
        else:
            will_submerge = False
            submersion_depth_m = 0.0
            safety_margin_m = round(max(0.05, hand_m - pred_depth_m), 2)
            risk_level = "WARNING" if safety_margin_m < 1.2 else "SAFE"

        forecasts[name] = ForecastDay(
            predicted_river_depth_m=pred_depth_m,
            predicted_wse_m=wse_local,
            slope_runoff_accumulation_m=slope_runoff_m,
            will_submerge=will_submerge,
            submersion_depth_m=submersion_depth_m,
            safety_margin_m=safety_margin_m,
            risk_level=risk_level,
            predicted_discharge_cusecs=round(q_final_cusecs, 0),
            predicted_discharge_cumecs=round(q_final_cumecs, 1),
            glofas_baseline_cusecs=round(q_local_glofas_cumecs * 35.3146667, 0),
            ml_predicted_cusecs=round(q_local_ml_cumecs * 35.3146667, 0),
            upstream_inflow_cusecs=round(q_up_ml * 35.3146667, 0),
            upstream_outflow_cusecs=round(q_down_ml * 35.3146667, 0),
            bankfull_capacity_cusecs=bankfull_cusecs,
            wse_feet=round(wse_local * 3.28084, 2),
            ground_elevation_feet=round(terrain['elv'] * 3.28084, 2)
        )

    # 8. Live Telemetry for T+0
    q_up_t0 = up_dis[today_idx] if today_idx < len(up_dis) else up_dis[-1]
    q_down_t0 = down_dis[today_idx] if today_idx < len(down_dis) else down_dis[-1]
    q_local_t0_cumecs = max(0.0, q_up_t0 + reach_alpha * (q_down_t0 - q_up_t0))
    
    # Add dam additions to T+0 telemetry as well
    q_local_t0_cumecs += dam_reach_addition
    if active_reach_cusecs > 0.0:
        q_local_t0_cumecs += (active_reach_cusecs / 35.3146667)
    q_t0_cusecs = q_local_t0_cumecs * 35.3146667

    curr_depth_m, t0_bankfull_cusecs = compute_stage_and_threshold(q_local_t0_cumecs, hand_m, C_manning, is_water_body)
    curr_wse_m = terrain['E_bed_r'] + curr_depth_m
    is_bankfull = True if is_water_body or q_t0_cusecs >= t0_bankfull_cusecs or curr_depth_m >= hand_m else False

    telemetry = LiveTelemetry(
        fetched_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
        data_provider="ECMWF GloFAS v4 / Open-Meteo Real-Time Telemetry",
        upstream_station=nodes_cache[up_idx]['station_name'],
        downstream_station=nodes_cache[down_idx]['station_name'],
        current_flow_cusecs=round(q_t0_cusecs, 0),
        current_flow_cumecs=round(q_local_t0_cumecs, 1),
        current_river_depth_m=round(curr_depth_m, 2),
        current_river_depth_ft=round(curr_depth_m * 3.28084, 2),
        current_wse_msl_m=round(curr_wse_m, 2),
        current_wse_msl_ft=round(curr_wse_m * 3.28084, 2),
        ground_clearance_m=round(hand_m - curr_depth_m, 2),
        is_bankfull=is_bankfull,
        bankfull_threshold_cusecs=t0_bankfull_cusecs,
        raw_telemetry_source_url=f"https://flood-api.open-meteo.com/v1/flood?latitude={nodes_cache[up_idx].get('glofas_lat', nodes_cache[up_idx]['lat'])}&longitude={nodes_cache[up_idx].get('glofas_lon', nodes_cache[up_idx]['lon'])}&daily=river_discharge&forecast_days=3"
    )

    # 9. Explainable AI (XAI) Attribution Construction
    xai_attribution = None
    if spat_attn_matrix is not None:
        # Target node selection: choose down_idx if down_id has incoming DAG edges, otherwise up_idx
        down_has_incoming = False
        if basin_adj is not None:
            # Check row of down_idx (incoming edges into down_idx in build_basin_adjacency)
            down_in_deg = float(basin_adj[down_idx, :].sum().item())
            down_has_incoming = (down_in_deg > 1.0) # > 1 accounts for self-loop + tributary

        target_idx = down_idx if down_has_incoming else up_idx
        target_station = nodes_cache[target_idx]
        target_xai_station = target_station['station_name']
        
        target_station_attn = spat_attn_matrix[target_idx] # [N]
        sorted_indices = np.argsort(target_station_attn)[::-1]
        
        spatial_contrib = []
        for idx in sorted_indices:
            w = float(target_station_attn[idx])
            if w > 0.001:
                c_node = nodes_cache[int(idx)]
                spatial_contrib.append({
                    "station_id": c_node['node_id'],
                    "station_name": c_node['station_name'],
                    "node_type": c_node.get('node_type', 'GAUGE'),
                    "is_self_loop": bool(int(idx) == target_idx),
                    "attention_weight": round(w, 4)
                })

        # Format 14-day antecedent temporal lag profile
        lag_labels = [f"T-{13-t}" for t in range(14)]
        temporal_profile = [
            {"lag": f"T-{13-t}", "day_index": t, "weight": round(float(temp_attn_vector[t]), 4)}
            for t in range(14)
        ] if temp_attn_vector else []
        
        xai_attribution = {
            "target_xai_station": target_xai_station,
            "target_station_id": target_station['node_id'],
            "spatial_attention": spatial_contrib,
            "temporal_attention": [round(float(w), 4) for w in temp_attn_vector] if temp_attn_vector else [],
            "temporal_lag_days": lag_labels,
            "temporal_profile": temporal_profile,
            "physical_factors": {
                "direct_precipitation_mm": round(float(up_precip[0]), 1) if up_precip else 0.0,
                "local_slope_deg": round(slope_deg, 2),
                "channel_conveyance": round(C_manning, 1),
                "upstream_distance_km": round(float(terrain.get('wris_data', {}).get('river_length_km', 50.0) or 50.0), 1),
                "curve_number": 82
            }
        }

    return PredictResponse(
        ground_elevation_m=terrain['elv'],
        nearest_river_point=f"{terrain['nearest_river_lat']},{terrain['nearest_river_lon']}",
        distance_to_river_m=terrain['distance_to_river_m'],
        river_bed_elevation_m=terrain['E_bed_r'],
        hand_m=terrain['hnd'],
        reach_fraction_alpha=reach_alpha,
        local_slope_degrees=slope_deg,
        current_state="ACTIVE_RESERVOIR_WATER_BODY" if is_water_body else ("NORMAL" if not forecasts["day_1_24h"].will_submerge else forecasts["day_1_24h"].risk_level),
        forecasts=forecasts,
        is_water_body=is_water_body,
        water_body_name=water_body_name if is_water_body else None,
        live_telemetry=telemetry,
        uparea=terrain.get('uparea', 0.0),
        wris_river_name=terrain.get('wris_river_name', 'Krishna River'),
        wris_data=terrain.get('wris_data'),
        xai_attribution=xai_attribution,
        model_info={
            "architecture": "ST-Attention-GAT (27 Stations)",
            "horizons": ["T+1 (24h)", "T+2 (48h)", "T+3 (72h)"],
            "features_in": 6
        }
    )

@app.get("/api/v1/model/hydrograph-replay")
def get_hydrograph_replay(node_id: Optional[str] = "ALMATTI"):
    """
    Serves real unseen test-set 2024 monsoon hydrograph replay for validation.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    replays_path = os.path.join(base_dir, 'models', 'test_hydrograph_replays.json')
    if not os.path.exists(replays_path):
        raise HTTPException(status_code=404, detail="Hydrograph replays not found")
        
    with open(replays_path, 'r', encoding='utf-8') as f:
        replays = json.load(f)
        
    target = node_id.upper() if node_id else "ALMATTI"
    if target not in replays.get("stations", {}):
        target = "ALMATTI"
        
    station_meta = next((n for n in nodes_cache if n['node_id'] == target), None)
    st_name = station_meta['station_name'] if station_meta else target
    
    return {
        "node_id": target,
        "station_name": st_name,
        "metadata": replays.get("metadata", {}),
        "available_stations": list(replays.get("stations", {}).keys()),
        "hydrograph": replays.get("stations", {}).get(target, [])
    }
