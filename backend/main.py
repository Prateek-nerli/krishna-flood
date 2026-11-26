import os
import json
import math
import time
import datetime
import pickle
from typing import Optional, Dict, Any, List, Tuple

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.schemas import (
    PredictRequest, PredictResponse, ForecastDay,
    LiveTelemetry, SimulateReleaseRequest
)
from gis_engine.raster_lookup import get_merit_hydro_data, haversine, get_copernicus_dem_patch, _get_wris_network
from ml_engine.model_tft_gat import ST_Attention_GAT, build_basin_adjacency
from ml_engine.hydraulic_cross_section import (
    extract_compound_cross_section,
    compute_divided_channel_conveyance,
    solve_water_depth_and_threshold,
    compute_backwater_afflux,
    compute_tributary_confluence_piling,
    compute_unsteady_wave_attenuation
)
from ml_engine.flood_connectivity_2d import (
    find_critical_entry_height_y,
    extract_connected_flood_footprint,
    find_nearest_safe_high_ground
)
from etl_pipeline.open_meteo import get_live_basin_matrix, get_live_river_discharge

from contextlib import asynccontextmanager
from backend.database import init_db, SessionLocal
from backend.db_models import User, UserLocation
from backend.notifications import send_sms, format_flood_alert
from backend.auth import auth_router
from backend.user_routes import user_router, set_predict_runner
from backend.admin_routes import admin_router, set_admin_predict_runner
from backend.scheduler import start_scheduler, shutdown_scheduler, set_scheduler_predict_runner

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    shutdown_scheduler()

app = FastAPI(
    title="KrishnaFlow AI Backend - IEEE Standard Hydrological Engine",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)

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
scales_vec: np.ndarray = np.ones(6, dtype=np.float32)

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
        
        # Validate exact 27-node ordering and alignment with seed_nodes.json
        expected_node_ids = [n['node_id'] for n in nodes_cache]
        cfg_node_ids = feature_config.get('node_ids', [])
        if cfg_node_ids != expected_node_ids:
            raise RuntimeError(f"Feature config node_ids mismatch! Expected {expected_node_ids}, got {cfg_node_ids}")

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

def propagate_dam_overrides(
    overrides: Dict[str, float],
    release_duration_hours: float = 24.0,
    basin_t0_flows: Optional[Dict[str, float]] = None
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Physically routes dam slider releases downstream strictly along the directed River DAG
    using Unsteady Empty-Channel Storage Deficit & Wave Attenuation.
    Returns (routed_additions_cumecs, storage_absorbed_pcts).
    """
    node_set = set(nodes_cache_ids())
    edges: Dict[str, List[str]] = {}
    in_degree: Dict[str, int] = {n['node_id']: 0 for n in nodes_cache}
    node_map = {n['node_id']: n for n in nodes_cache}

    for n in nodes_cache:
        d = n.get('downstream_node')
        if d and d in node_set:
            if n['node_id'] not in edges:
                edges[n['node_id']] = []
            edges[n['node_id']].append(d)
            in_degree[d] += 1

    flow_additions_cumecs = {n['node_id']: 0.0 for n in nodes_cache}
    storage_absorbed_pcts = {n['node_id']: 0.0 for n in nodes_cache}

    # Direct additions at specific nodes
    for nid, cusecs in overrides.items():
        if nid in flow_additions_cumecs and nid != "ACTIVE_REACH":
            flow_additions_cumecs[nid] = float(cusecs) / 35.3146667

    # Topological sort (Kahn's algorithm)
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    routed_additions = dict(flow_additions_cumecs)

    while queue:
        u = queue.pop(0)
        u_added_flow = routed_additions[u]
        if u in edges:
            for v in edges[u]:
                if u_added_flow > 0.0:
                    dist_km = float(node_map[u].get('dist_km', 50.0) or 50.0)
                    q_t0 = basin_t0_flows.get(u, 15.0) if basin_t0_flows else 15.0
                    w_ch = 180.0 if dist_km >= 80.0 else 100.0
                    q_pulse_cfs = u_added_flow * 35.3146667

                    q_arr, abs_pct = compute_unsteady_wave_attenuation(
                        Q_pulse_cusecs=q_pulse_cfs,
                        release_duration_hours=release_duration_hours,
                        dist_km=dist_km,
                        Q_t0_cumecs=q_t0,
                        W_ch=w_ch,
                        H_bf=4.5
                    )
                    routed_additions[v] += q_arr
                    storage_absorbed_pcts[v] = abs_pct
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)

    return routed_additions, storage_absorbed_pcts

def get_amc_adjusted_cn(p5_mm: float, cn_base: float = 82.0) -> Tuple[float, str]:
    """
    Dynamically adjusts CN based on 5-day Antecedent Moisture Condition (AMC):
    - P5 < 35 mm: AMC-I Dry: CN_I = (4.2 * CN_II) / (10 - 0.058 * CN_II)
    - 35 <= P5 <= 53 mm: AMC-II Normal: CN_II = 82.0
    - P5 > 53 mm: AMC-III Saturated: CN_III = (23 * CN_II) / (10 + 0.13 * CN_II)
    """
    if p5_mm < 35.0:
        cn = (4.2 * cn_base) / (10.0 - 0.058 * cn_base)
        amc = "AMC-I (Dry)"
    elif p5_mm <= 53.0:
        cn = cn_base
        amc = "AMC-II (Normal)"
    else:
        cn = (23.0 * cn_base) / (10.0 + 0.13 * cn_base)
        amc = "AMC-III (Saturated)"
    return round(float(np.clip(cn, 30.0, 98.0)), 1), amc

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

    # 4. Spatially Isolated Dam Slider Overrides & Unsteady Wave Routing
    # Strictly isolated per-request overrides (never reads or mutates global simulation_state)
    active_overrides: Dict[str, float] = {}
    if req.custom_dam_outflow_override:
        for k, v in req.custom_dam_outflow_override.items():
            if v is not None:
                active_overrides[k] = float(v)

    release_dur = float(req.release_duration_hours or 24.0)
    basin_t0_flows = {nid: float(basin_data['nodes'].get(nid, {}).get('discharge', [15.0]*21)[min(today_idx, len(basin_data['nodes'].get(nid, {}).get('discharge', [15.0]*21))-1)]) for nid in node_to_idx}
    routed_dam_cumecs, storage_absorbed_pcts = propagate_dam_overrides(
        active_overrides,
        release_duration_hours=release_dur,
        basin_t0_flows=basin_t0_flows
    )
    
    # Check for direct ACTIVE_REACH slider override
    active_reach_cusecs = float(active_overrides.get("ACTIVE_REACH", 0.0))

    # 5. Assemble ST-Attention-GAT Input Tensor [1, N, 14, 6]
    num_nodes = len(nodes_cache)
    x_input = np.zeros((1, num_nodes, 14, 6), dtype=np.float32)
    
    today_dt = datetime.date.today()

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

        p_slice = p_arr[start_t : today_idx + 1] if len(p_arr) >= today_idx + 1 else [0.0]*14
        while len(p_slice) < 14:
            p_slice.insert(0, p_slice[0] if p_slice else 0.0)

        et_slice = et_arr[start_t : today_idx + 1] if len(et_arr) >= today_idx + 1 else [4.0]*14
        while len(et_slice) < 14:
            et_slice.insert(0, et_slice[0] if et_slice else 4.0)

        for t in range(14):
            q_val = float(q_slice[t])
            p_val = float(p_slice[t])
            # Rolling 3-day precip
            p3d_val = float(sum(p_slice[max(0, t-2):t+1]))
            et_val = float(et_slice[t])

            lag_date = today_dt - datetime.timedelta(days=13 - t)
            lag_doy = lag_date.timetuple().tm_yday
            sin_doy_t = math.sin(2.0 * math.pi * lag_doy / 365.25)
            cos_doy_t = math.cos(2.0 * math.pi * lag_doy / 365.25)

            x_input[0, i, t, 0] = math.log1p(max(0.0, q_val))
            x_input[0, i, t, 1] = p_val
            x_input[0, i, t, 2] = p3d_val
            x_input[0, i, t, 3] = et_val
            x_input[0, i, t, 4] = sin_doy_t
            x_input[0, i, t, 5] = cos_doy_t

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

    # Channel hydraulics parameters (Dynamic Compound 30m DEM Cross-Section Solver)
    hand_m = float(terrain['hnd'])
    r_len = float(terrain.get('wris_data', {}).get('river_length_km', 250.0) or 250.0)
    
    # Hydraulic energy slope S_0 based on upstream to downstream bed drop, refined for alluvial vs headwater
    E_bed_up = float(nodes_cache[up_idx].get('bed_elev_m', 500.0))
    E_bed_down = float(nodes_cache[down_idx].get('bed_elev_m', 480.0))
    reach_dist_km = float(nodes_cache[up_idx].get('dist_km', 50.0) or 50.0)
    bed_drop_m = max(1.0, E_bed_up - E_bed_down)
    reach_len_m = max(1000.0, reach_dist_km * 1000.0)
    S_macro = bed_drop_m / reach_len_m

    # Headwater reaches vs. alluvial plains/valleys
    # Headwaters in Western Ghats dams can sustain S_0 in [0.00045, 0.00080]
    # Plains/valley reaches and reaches with waterfalls/drops are capped to [0.00010, 0.00045]
    is_headwater = (up_id in ["KOYNA", "RADHANAGARI", "RAKASKOP", "KALAMMAWADI"] and reach_alpha < 0.3)
    if is_headwater:
        S_0 = max(0.00045, min(0.00080, S_macro))
    else:
        S_0 = max(0.00010, min(0.00045, S_macro))
    
    # Active channel width W_live
    W_live = float(terrain.get('wth', 150.0) or 150.0)
    if W_live <= 5.0:
        W_live = 450.0 if r_len >= 800 else (280.0 if r_len >= 200 else (160.0 if r_len >= 60 else 70.0))

    # Dynamic Compound Cross-Section from 30m DEM
    reach_vec = (
        nodes_cache[down_idx]['lon'] - nodes_cache[up_idx]['lon'],
        nodes_cache[down_idx]['lat'] - nodes_cache[up_idx]['lat']
    )
    try:
        profile = extract_compound_cross_section(
            lat=lat,
            lon=lon,
            river_lat=float(terrain['nearest_river_lat']),
            river_lon=float(terrain['nearest_river_lon']),
            dist_to_river_m=float(terrain['distance_to_river_m']),
            E_bed_r=float(terrain['E_bed_r']),
            W_live=W_live,
            bank_transect=terrain.get('bank_transect'),
            reach_vector=reach_vec
        )
    except RuntimeError as e:
        if "COPERNICUS_DEM_UNAVAILABLE" in str(e):
            raise HTTPException(status_code=503, detail="Copernicus 30m DEM terrain data unavailable for this coordinate")
        raise

    n_ch = 0.032
    n_fp = float(terrain.get('manning_n_fp', 0.045) or 0.045)

    # Dynamic AMC-I/II/III SCS-CN Calculation from 5-day antecedent precipitation (ending today)
    p_w_idx = min(today_idx, len(up_precip) - 1) if up_precip else 0
    p5_mm = float(sum(up_precip[max(0, p_w_idx - 4) : p_w_idx + 1])) if up_precip else 0.0
    cn_dynamic, amc_class = get_amc_adjusted_cn(p5_mm, cn_base=82.0)

    # 7. Generate Forecasts for T+1, T+2, T+3
    forecasts = {}
    horizons = ["day_1_24h", "day_2_48h", "day_3_72h"]

    # Dam addition for reach
    dam_add_up = routed_dam_cumecs.get(up_id, 0.0)
    dam_add_down = routed_dam_cumecs.get(down_id, 0.0)
    dam_reach_addition = dam_add_up + reach_alpha * (dam_add_down - dam_add_up)

    # Detect if current reach is a tributary entering a junction
    down_node_obj = nodes_cache[down_idx]
    up_node_obj = nodes_cache[up_idx]
    is_tributary_reach = False
    if down_node_obj.get('node_type') == 'CONFLUENCE' or up_node_obj.get('node_type') in ['TRIBUTARY', 'RESERVOIR'] or down_node_obj.get('node_id') in ['SANGLI', 'KURUNDWAD', 'KALLOL_CHIKODI', 'GOKAK_CONFLUENCE', 'ALMATTI', 'NARAYANPUR', 'SRISAILAM', 'KARAD']:
        if up_node_obj.get('wris_river_name') != down_node_obj.get('wris_river_name') or down_node_obj.get('node_type') == 'CONFLUENCE':
            is_tributary_reach = True

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

        # Total combined flow
        q_final_cumecs = q_local_ml_cumecs + dam_reach_addition
        if active_reach_cusecs > 0.0:
            q_final_cumecs += (active_reach_cusecs / 35.3146667)

        q_final_cusecs = q_final_cumecs * 35.3146667

        # Compound Cross-Section Manning Solver
        pred_depth_manning, bankfull_cumecs, top_w, v_ch, ch_bankfull_cumecs = solve_water_depth_and_threshold(
            q_final_cumecs, S_0, profile, n_ch, n_fp, hand_m
        )
        bankfull_cusecs = round(bankfull_cumecs * 35.3146667, 0)
        channel_bankfull_cusecs = round(ch_bankfull_cumecs * 35.3146667, 0)

        # Downstream predicted WSE & Backwater / Confluence Piling Afflux
        down_q_pred = preds_phys[down_idx, h_idx] if preds_phys is not None else q_down_glo
        down_wse_pred = E_bed_down + max(0.8, (max(1.0, float(down_q_pred)) / 120.0)**0.4)
        
        if is_tributary_reach and down_wse_pred > (terrain['E_bed_r'] + pred_depth_manning):
            pred_depth_final, h_backwater, mechanism = compute_tributary_confluence_piling(
                terrain['E_bed_r'], pred_depth_manning, down_wse_pred, reach_alpha, is_tributary_reach=True
            )
        else:
            pred_depth_final, h_backwater, mechanism = compute_backwater_afflux(
                terrain['E_bed_r'], pred_depth_manning, down_wse_pred, reach_len_m, reach_alpha, S_0
            )

        if is_water_body:
            pred_depth_final += 2.5

        pred_depth_final = round(float(pred_depth_final), 2)
        wse_local = round(float(terrain['E_bed_r'] + pred_depth_final), 2)

        # SCS-CN Slope Runoff for Horizon Day (Dynamic AMC-adjusted CN)
        precip_day_mm = float(up_precip[min(p_w_idx + 1 + h_idx, len(up_precip) - 1)]) if up_precip else 0.0
        slope_runoff_m = compute_scs_cn_runoff_depth(precip_day_mm, slope_deg, cn=cn_dynamic)

        if is_water_body or q_final_cusecs >= bankfull_cusecs or pred_depth_final >= hand_m:
            will_submerge = True
            submersion_depth_m = round(max(0.25, pred_depth_final - hand_m + slope_runoff_m), 2)
            safety_margin_m = 0.0
            risk_level = "CRITICAL"
        else:
            will_submerge = False
            submersion_depth_m = 0.0
            safety_margin_m = round(max(0.05, hand_m - pred_depth_final), 2)
            risk_level = "WARNING" if safety_margin_m < 1.2 else "SAFE"

        forecasts[name] = ForecastDay(
            predicted_river_depth_m=pred_depth_final,
            predicted_wse_m=wse_local,
            slope_runoff_accumulation_m=slope_runoff_m,
            will_submerge=will_submerge,
            submersion_depth_m=submersion_depth_m,
            safety_margin_m=safety_margin_m,
            risk_level=risk_level,
            predicted_discharge_cusecs=round(q_final_cusecs, 0),
            predicted_discharge_cumecs=round(float(q_final_cumecs), 1),
            glofas_baseline_cusecs=round(q_local_glofas_cumecs * 35.3146667, 0),
            ml_predicted_cusecs=round(q_local_ml_cumecs * 35.3146667, 0),
            upstream_inflow_cusecs=round(q_up_ml * 35.3146667, 0),
            upstream_outflow_cusecs=round(q_down_ml * 35.3146667, 0),
            bankfull_capacity_cusecs=bankfull_cusecs,
            channel_bankfull_cusecs=channel_bankfull_cusecs,
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

    # Solve T+0 stage and bankfull capacity on compound section
    t0_depth_manning, t0_bankfull_cumecs, t0_top_w, t0_v_ch, t0_ch_bankfull_cumecs = solve_water_depth_and_threshold(
        q_local_t0_cumecs, S_0, profile, n_ch, n_fp, hand_m
    )
    t0_bankfull_cusecs = round(t0_bankfull_cumecs * 35.3146667, 0)
    t0_channel_bankfull_cusecs = round(t0_ch_bankfull_cumecs * 35.3146667, 0)
    
    down_wse_t0 = E_bed_down + max(0.8, (max(1.0, float(q_down_t0)) / 120.0)**0.4)
    if is_tributary_reach and down_wse_t0 > (terrain['E_bed_r'] + t0_depth_manning):
        t0_depth_final, t0_h_backwater, t0_mechanism = compute_tributary_confluence_piling(
            terrain['E_bed_r'], t0_depth_manning, down_wse_t0, reach_alpha, is_tributary_reach=True
        )
    else:
        t0_depth_final, t0_h_backwater, t0_mechanism = compute_backwater_afflux(
            terrain['E_bed_r'], t0_depth_manning, down_wse_t0, reach_len_m, reach_alpha, S_0
        )
    if is_water_body:
        t0_depth_final += 2.5
    curr_depth_m = round(float(t0_depth_final), 2)
    curr_wse_m = round(float(terrain['E_bed_r'] + curr_depth_m), 2)
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
                "direct_precipitation_mm": round(float(up_precip[p_w_idx]), 1) if up_precip else 0.0,
                "local_slope_deg": round(slope_deg, 2),
                "channel_conveyance": round(t0_top_w, 1),
                "upstream_distance_km": round(float(terrain.get('wris_data', {}).get('river_length_km', 50.0) or 50.0), 1),
                "curve_number": round(cn_dynamic, 1)
            }
        }

    # 10. 2D Geospatial Flood Connectivity, Flanking, and Evacuation Pin
    try:
        dem_patch, dem_transform, _ = get_copernicus_dem_patch(lat, lon, size_px=96)
    except RuntimeError as e:
        if "COPERNICUS_DEM_UNAVAILABLE" in str(e):
            raise HTTPException(status_code=503, detail="Copernicus 30m DEM terrain data unavailable for this coordinate")
        raise
    inv_dem = ~dem_transform
    c_t, r_t = inv_dem * (lon, lat)
    target_rc = (int(np.clip(round(r_t), 0, 95)), int(np.clip(round(c_t), 0, 95)))

    riv_lat = float(terrain['nearest_river_lat'])
    riv_lon = float(terrain['nearest_river_lon'])
    c_riv, r_riv = inv_dem * (riv_lon, riv_lat)
    dist_to_riv = float(terrain['distance_to_river_m'])
    true_site_threshold_cusecs = t0_bankfull_cusecs

    all_tree, _, _, _ = _get_wris_network()
    river_pixels_set = set()

    if all_tree is not None:
        # Query true curved WRIS river vertices inside the DEM patch (~2.88 km x 2.88 km)
        idxs = all_tree.query_ball_point([lat, lon], r=0.020)
        if len(idxs) > 0:
            patch_pts = all_tree.data[idxs]
            for pt in patch_pts:
                col_f, row_f = inv_dem * (pt[1], pt[0])
                rr = int(round(row_f))
                cc = int(round(col_f))
                if 0 <= rr < 96 and 0 <= cc < 96:
                    river_pixels_set.add((rr, cc))
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < 96 and 0 <= nc < 96:
                            river_pixels_set.add((nr, nc))

    # If no WRIS points in patch, check Sentinel-2 water_polygon pixels as fallback
    if not river_pixels_set:
        water_poly = terrain.get('wris_data', {}).get('water_polygon') or terrain.get('water_polygon')
        if water_poly and isinstance(water_poly, list):
            for wp in water_poly:
                if len(wp) >= 2:
                    c_w, r_w = inv_dem * (float(wp[1]), float(wp[0]))
                    rw, cw = int(round(r_w)), int(round(c_w))
                    if 0 <= rw < 96 and 0 <= cw < 96:
                        river_pixels_set.add((rw, cw))
                        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                            nr, nc = rw + dr, cw + dc
                            if 0 <= nr < 96 and 0 <= nc < 96:
                                river_pixels_set.add((nr, nc))

    if not river_pixels_set:
        critical_entry_y = float("inf")
        H_direct = float("inf")
        flood_polys = []
        inun_ha = 0.0
        conn_mask = np.zeros((96, 96), dtype=bool)
        safe_high_ground = None
        indirect_flanking = False
        barrier_protected = False
        drainage_lock_active = False
        connectivity_2d_status = "RIVER_OUTSIDE_TERRAIN_WINDOW"
    else:
        connectivity_2d_status = "OK"
        river_pixels_list = list(river_pixels_set)
        critical_entry_y, H_direct = find_critical_entry_height_y(
            dem_patch, river_pixels_list, target_rc, dem_transform
        )

        # Synchronize Site Flood Threshold & T+0 Bankfull Telemetry with 2D Critical Entry Height Y
        if not math.isinf(critical_entry_y):
            h_site_critical = max(0.5, critical_entry_y - float(terrain['E_bed_r']))
            K_site_crit, _, _, _, _ = compute_divided_channel_conveyance(h_site_critical, profile, n_ch, n_fp)
            true_site_threshold_cusecs = round(float(K_site_crit * math.sqrt(max(0.00003, S_0)) * 35.3147), 0)

            for h_name in forecasts:
                forecasts[h_name].bankfull_capacity_cusecs = true_site_threshold_cusecs

            if telemetry is not None:
                telemetry.bankfull_threshold_cusecs = true_site_threshold_cusecs
                t0_exceeded = (
                    is_water_body
                    or q_t0_cusecs >= true_site_threshold_cusecs
                    or curr_wse_m >= critical_entry_y
                )
                telemetry.is_bankfull = bool(t0_exceeded)
                telemetry.ground_clearance_m = (
                    0.0 if t0_exceeded else round(critical_entry_y - curr_wse_m, 2)
                )

        # Evaluate 2D threshold/level consistency, Indirect Flanking (4A) & Drainage Lock (4B) BEFORE 2D footprint extraction
        clicked_dem = float(dem_patch[target_rc[0], target_rc[1]])
        horizon_flanking = {}
        horizon_barrier = {}
        horizon_drainage_lock = {}

        for h_name, f_obj in forecasts.items():
            wse_h = f_obj.predicted_wse_m
            q_h = f_obj.predicted_discharge_cusecs
            crit_q = f_obj.bankfull_capacity_cusecs
            h_flanking = False
            h_barrier = False
            h_drainage_lock = False

            if not math.isinf(critical_entry_y) and (is_water_body or wse_h >= critical_entry_y or q_h >= crit_q):
                if (critical_entry_y < (H_direct - 0.5)) and (wse_h >= critical_entry_y) and (wse_h < H_direct):
                    h_flanking = True
                f_obj.will_submerge = True
                f_obj.risk_level = "CRITICAL"
                subm_base = (wse_h - clicked_dem) if h_flanking else max(wse_h - critical_entry_y, wse_h - clicked_dem)
                f_obj.submersion_depth_m = round(
                    max(0.25 if h_flanking else 0.15, subm_base + (0.0 if h_flanking else float(f_obj.slope_runoff_accumulation_m))),
                    2,
                )
                f_obj.safety_margin_m = 0.0
            elif not math.isinf(critical_entry_y):
                h_barrier = bool(clicked_dem < wse_h)
                f_obj.will_submerge = False
                f_obj.submersion_depth_m = 0.0
                clearance_y = max(0.05, critical_entry_y - wse_h)
                f_obj.safety_margin_m = round(max(0.5, clearance_y) if h_barrier else clearance_y, 2)
                f_obj.risk_level = "SAFE" if h_barrier else ("WARNING" if f_obj.safety_margin_m < 1.2 else "SAFE")

                if h_barrier and (p5_mm > 15.0):
                    h_drainage_lock = True
                    f_obj.slope_runoff_accumulation_m = round(f_obj.slope_runoff_accumulation_m * 1.65, 3)
                    if f_obj.slope_runoff_accumulation_m > f_obj.safety_margin_m:
                        f_obj.will_submerge = True
                        f_obj.risk_level = "WARNING"
                        f_obj.submersion_depth_m = round(f_obj.slope_runoff_accumulation_m - f_obj.safety_margin_m, 2)

            horizon_flanking[h_name] = h_flanking
            horizon_barrier[h_name] = h_barrier
            horizon_drainage_lock[h_name] = h_drainage_lock

        indirect_flanking = horizon_flanking.get("day_1_24h", False)
        barrier_protected = horizon_barrier.get("day_1_24h", False)
        drainage_lock_active = horizon_drainage_lock.get("day_1_24h", False)

        day1_wse = forecasts["day_1_24h"].predicted_wse_m
        day1_depth = forecasts["day_1_24h"].predicted_river_depth_m
        h_bankfull = float(profile.get("H_bankfull", 4.5))
        is_overbank_flood = (day1_depth > h_bankfull) or bool(forecasts["day_1_24h"].will_submerge)

        if is_overbank_flood:
            flood_polys, inun_ha, conn_mask = extract_connected_flood_footprint(
                dem_patch,
                river_pixels_list,
                day1_wse,
                dem_transform,
                river_bed_elev_m=float(terrain['E_bed_r']),
            )
        else:
            flood_polys = []
            inun_ha = 0.0
            conn_mask = np.zeros((96, 96), dtype=bool)

        if is_overbank_flood or forecasts["day_1_24h"].safety_margin_m < 1.5:
            safe_high_ground = find_nearest_safe_high_ground(
                dem_patch, conn_mask, target_rc, day1_wse, dem_transform
            )
        else:
            safe_high_ground = None

    if indirect_flanking:
        t0_mechanism = "INDIRECT_RIDGE_FLANKING"
    elif drainage_lock_active:
        t0_mechanism = "DRAINAGE_LOCK_COMPOUND"

    # 11. Compound Hydraulic Diagnostics
    active_absorbed_pct = 0.0
    if up_id in storage_absorbed_pcts and storage_absorbed_pcts[up_id] > 0.0:
        active_absorbed_pct = storage_absorbed_pcts[up_id]
    elif down_id in storage_absorbed_pcts and storage_absorbed_pcts[down_id] > 0.0:
        active_absorbed_pct = storage_absorbed_pcts[down_id]

    hydraulic_diagnostics = {
        "solver": "Divided-Channel 30m DEM Compound Manning + Unsteady Storage + 2D Flanking",
        "wetted_top_width_m": round(float(t0_top_w), 1),
        "channel_velocity_ms": round(float(t0_v_ch), 2),
        "manning_n_ch": n_ch,
        "manning_n_fp": round(float(n_fp), 4),
        "mean_floodplain_ndvi": round(float(terrain.get('mean_floodplain_ndvi', 0.25) or 0.25), 3),
        "energy_slope_sf": round(float(S_0), 6),
        "h_manning_m": round(float(t0_depth_manning), 2),
        "h_backwater_m": round(float(t0_h_backwater), 2),
        "flood_mechanism": t0_mechanism,
        "amc_class": amc_class,
        "cn_effective": round(float(cn_dynamic), 1),
        "p5_precipitation_mm": round(float(p5_mm), 1),
        "profile_points": len(profile["offsets"]),
        "empty_channel_storage_absorbed_pct": round(float(active_absorbed_pct), 1),
        "release_duration_hours": float(release_dur),
        "connectivity_2d_status": connectivity_2d_status,
        "solver_saturated": bool(t0_depth_manning >= 34.5),
        "channel_bankfull_cusecs": t0_channel_bankfull_cusecs,
        "bankfull_capacity_cusecs": true_site_threshold_cusecs,
        "h_bankfull_m": round(float(profile.get("H_bankfull", 4.5)), 2),
        "critical_entry_height_y_m": round(float(critical_entry_y), 2) if not math.isinf(critical_entry_y) else None,
        "direct_ridge_height_h_m": round(float(H_direct), 2) if not math.isinf(H_direct) else None,
        "indirect_flanking": indirect_flanking,
        "barrier_protected": barrier_protected,
        "drainage_lock_active": drainage_lock_active,
        "inundated_area_hectares": round(float(inun_ha), 1)
    }

    # 12. On explicit "Route Waves" action, check if coordinates match any stored DB user location
    #     and immediately trigger SMS alerts when flood is detected/predicted.
    auto_alert_summary = None
    if req.trigger_registered_alerts:
        flooded_days: List[str] = []
        max_subm_depth = 0.0
        peak_flood_flow = 0.0
        for h_name, f_obj in forecasts.items():
            if f_obj.will_submerge:
                day_label = (
                    "Day 1 (+24h)" if h_name.startswith("day_1")
                    else ("Day 2 (+48h)" if h_name.startswith("day_2") else "Day 3 (+72h)")
                )
                flooded_days.append(day_label)
                max_subm_depth = max(max_subm_depth, float(f_obj.submersion_depth_m))
                peak_flood_flow = max(peak_flood_flow, float(f_obj.predicted_discharge_cusecs))

        matched_pairs: List[Tuple[User, UserLocation]] = []
        sent_recipients: List[Dict[str, Any]] = []
        seen_user_ids = set()
        db_session = SessionLocal()
        try:
            rows = (
                db_session.query(User, UserLocation)
                .join(UserLocation, UserLocation.user_id == User.id)
                .filter(User.is_phone_verified == True)
                .all()
            )
            for db_user, db_loc in rows:
                if haversine(lat, lon, db_loc.latitude, db_loc.longitude) <= 300.0:
                    matched_pairs.append((db_user, db_loc))

            if flooded_days and matched_pairs:
                is_sim = bool(active_overrides)
                sim_summary = (
                    ", ".join(f"{k}: {v:,.0f} cfs" for k, v in active_overrides.items())
                    + f" ({release_dur:.0f}h duration)"
                    if is_sim
                    else ""
                )
                hg_str = ""
                if safe_high_ground:
                    hg_str = (
                        f"{safe_high_ground.get('bearing', 'Nearby')} "
                        f"({safe_high_ground.get('elevation_m', 500)}m, "
                        f"{safe_high_ground.get('distance_m', 300)}m)"
                    )
                days_str = ", ".join(flooded_days)
                now_utc = datetime.datetime.now(datetime.timezone.utc)

                for db_user, db_loc in matched_pairs:
                    if not is_sim:
                        db_loc.last_checked_at = now_utc
                        db_loc.last_risk_level = "CRITICAL"
                        db_loc.last_flood_days_json = json.dumps(flooded_days)

                    if db_user.id in seen_user_ids or not db_user.sms_enabled:
                        continue
                    seen_user_ids.add(db_user.id)

                    alert_bundle = format_flood_alert(
                        location_name=db_loc.place_name,
                        lat=db_loc.latitude,
                        lon=db_loc.longitude,
                        flood_days=flooded_days,
                        peak_flow_cfs=peak_flood_flow,
                        max_depth_m=max_subm_depth,
                        high_ground_str=hg_str,
                        is_simulation=is_sim,
                        sim_summary=sim_summary,
                        language=db_user.alert_language or "bilingual",
                    )
                    sms_res = send_sms(
                        db_session,
                        db_user.phone,
                        alert_bundle["sms_body"],
                        alert_type="SIMULATION" if is_sim else "REAL",
                        user_id=db_user.id,
                        location_id=db_loc.id,
                        language=db_user.alert_language or "bilingual",
                        flood_days=days_str,
                    )
                    phone_tail = db_user.phone[-4:] if db_user.phone and len(db_user.phone) >= 4 else "****"
                    sent_recipients.append({
                        "user_name": db_user.full_name,
                        "place_name": db_loc.place_name,
                        "phone_masked": f"***{phone_tail}",
                        "delivery_status": sms_res.get("status", "UNKNOWN"),
                    })
                if not is_sim:
                    db_session.commit()
            elif not flooded_days and matched_pairs and not active_overrides:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                for _, db_loc in matched_pairs:
                    db_loc.last_checked_at = now_utc
                    db_loc.last_risk_level = "SAFE"
                    db_loc.last_flood_days_json = "[]"
                db_session.commit()

            auto_alert_summary = {
                "checked": True,
                "matched_locations": len(matched_pairs),
                "flood_detected": bool(flooded_days),
                "alerts_sent": len(sent_recipients),
                "flood_days": flooded_days,
                "recipients": sent_recipients,
            }
        except Exception as exc:
            print(f"[RouteWavesAlert] Failed checking DB user locations: {exc}")
            auto_alert_summary = {
                "checked": True,
                "matched_locations": 0,
                "flood_detected": bool(flooded_days),
                "alerts_sent": 0,
                "flood_days": flooded_days,
                "recipients": [],
                "error": str(exc),
            }
        finally:
            db_session.close()

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
        hydraulic_diagnostics=hydraulic_diagnostics,
        flood_inundation_polygons=flood_polys,
        inundated_area_hectares=round(float(inun_ha), 1),
        critical_entry_height_y_m=round(float(critical_entry_y), 2) if not math.isinf(critical_entry_y) else None,
        direct_ridge_height_h_m=round(float(H_direct), 2) if not math.isinf(H_direct) else None,
        indirect_flanking=indirect_flanking,
        barrier_protected=barrier_protected,
        drainage_lock_active=drainage_lock_active,
        nearest_safe_high_ground=safe_high_ground,
        auto_alert_summary=auto_alert_summary,
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

# Wire up in-process prediction runners for User Dashboard, Admin simulation, and 6-Hour Scheduler
set_predict_runner(predict_coordinate, nodes_cache)
set_admin_predict_runner(predict_coordinate, nodes_cache)
set_scheduler_predict_runner(predict_coordinate)

# Ensure DB tables & seeded admin exist on import (for TestClient / scripts)
try:
    init_db()
except Exception as e:
    print(f"[Backend] Notice on startup init_db: {e}")

