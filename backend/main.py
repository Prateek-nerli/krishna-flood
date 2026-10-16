from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from backend.schemas import PredictRequest, PredictResponse, ForecastDay, SimulateReleaseRequest
from gis_engine.raster_lookup import get_merit_hydro_data, haversine
from ml_engine.routing_hydraulics import routing_and_submersion, get_channel_params
from etl_pipeline.open_meteo import get_live_river_discharge
from ml_engine.model_tft_gat import TFT_GAT_Model
import json
import os
import math
import torch
import pickle
import numpy as np

app = FastAPI(title="KrishnaFlow AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

simulation_state = {}
scaler = None
model = None
nodes_cache = []

def load_ml_assets():
    global scaler, model, nodes_cache
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    with open(os.path.join(base_dir, 'seed_nodes.json'), 'r') as f:
        nodes_cache = json.load(f)
        
    num_nodes = len(nodes_cache)
    try:
        with open(os.path.join(base_dir, 'models', 'scaler.pkl'), 'rb') as f:
            scaler = pickle.load(f)
            
        model = TFT_GAT_Model(num_nodes=num_nodes, input_dim=4, hidden_dim=32, seq_len=14, pred_horizon=3)
        model.load_state_dict(torch.load(os.path.join(base_dir, 'models', 'krishna_tft_gat.pt'), weights_only=True))
        model.eval()
    except Exception as e:
        print("ML assets not found or error loading:", e)

load_ml_assets()

def get_node_coords(node_id):
    for n in nodes_cache:
        if n['node_id'] == node_id:
            return n['lat'], n['lon']
    return None, None
    
def get_node_index(node_id):
    for i, n in enumerate(nodes_cache):
        if n['node_id'] == node_id:
            return i
    return 0

def propagate_overrides(overrides):
    flow_state = {k: v / 35.3146667 for k,v in overrides.items()} 
    edges = {}
    in_degree = {n['node_id']: 0 for n in nodes_cache}
    for n in nodes_cache:
        d = n.get('downstream_node')
        if d:
            if n['node_id'] not in edges:
                edges[n['node_id']] = []
            edges[n['node_id']].append(d)
            if d not in in_degree:
                in_degree[d] = 0
            in_degree[d] += 1
            
    queue = [n for n in in_degree if in_degree[n] == 0]
    
    while queue:
        u = queue.pop(0)
        if u in edges:
            for v in edges[u]:
                if u in flow_state:
                    if v not in flow_state:
                        flow_state[v] = 0.0
                    flow_state[v] += flow_state[u] * 0.92 
                
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
                    
    return flow_state

BASIN_POLYGON = [
    [73.35, 19.35], [73.55, 17.95], [73.65, 16.90], [73.85, 15.85], [74.35, 15.10], 
    [75.15, 13.90], [75.60, 13.35], [76.65, 13.65], [77.60, 14.55], [78.95, 15.35], 
    [80.15, 15.65], [81.15, 15.75], [81.20, 16.65], [80.70, 17.65], [79.65, 18.10], 
    [78.35, 18.05], [76.95, 18.45], [75.45, 19.15], [73.35, 19.35]
]

def is_point_in_polygon(lat, lon, polygon):
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

@app.get("/api/v1/basin/nodes")
def get_basin_nodes():
    return {"nodes": nodes_cache}

@app.post("/api/v1/simulate/release")
def simulate_release(req: SimulateReleaseRequest):
    simulation_state[req.node_id] = req.outflow_cusecs / 35.3146667
    return {"status": "success", "message": f"Overrode outflow for {req.node_id} to {req.outflow_cusecs} cusecs"}

@app.post("/api/v1/predict/coordinate", response_model=PredictResponse)
def predict_coordinate(req: PredictRequest):
    lat, lon = req.latitude, req.longitude
    
    if not is_point_in_polygon(lat, lon, BASIN_POLYGON):
        raise HTTPException(status_code=400, detail="NOT UNDER KRISHNA BASIN")
        
    terrain = get_merit_hydro_data(lat, lon)
    
    if terrain is None or terrain['distance_to_river_m'] > 95000:
        raise HTTPException(status_code=400, detail="NOT UNDER KRISHNA BASIN")
        
    slope_deg = math.degrees(math.atan(terrain['tan_beta']))
    
    # 2. Water Body / Reservoir Detection
    is_water_body = False
    water_body_name = "Active River Channel"
    reservoir_node = None
    
    for n in nodes_cache:
        if n.get('node_type') == 'RESERVOIR':
            d = haversine(lat, lon, n['lat'], n['lon'])
            if d < 15000.0:
                reservoir_node = n
                break
                
    def check_osm_water(lat, lon):
        try:
            import requests
            url = "https://overpass-api.de/api/interpreter"
            query = f"""
            [out:json][timeout:4];
            is_in({lat},{lon})->.a;
            area.a["natural"="water"];
            out tags;
            """
            resp = requests.post(url, data={'data': query}, timeout=3)
            if resp.status_code == 200 and len(resp.json().get('elements', [])) > 0:
                return True
        except:
            pass
        return False
        
    is_upstream = False
    if reservoir_node:
        if reservoir_node['node_id'] == 'HIDKAL':
            is_upstream = lon < 74.6415
        else:
            is_upstream = True # Default for others
            
    if terrain.get('is_water_body_raster', False) or check_osm_water(lat, lon) or (slope_deg < 0.08 and reservoir_node and is_upstream):
        is_water_body = True
        if reservoir_node:
            water_body_name = reservoir_node['station_name']
        
    # Apply override states
    if is_water_body and reservoir_node:
        terrain['E_bed_r'] = reservoir_node['bed_elev_m']
        terrain['hnd'] = 0.0

    up_id = terrain['upstream_node_id']
    down_id = terrain['downstream_node_id']
    
    up_lat, up_lon = get_node_coords(up_id)
    down_lat, down_lon = get_node_coords(down_id)
    
    if not up_lat: up_lat, up_lon = lat, lon
    if not down_lat: down_lat, down_lon = lat, lon
        
    up_q_live = get_live_river_discharge(up_lat, up_lon).get('daily', {}).get('river_discharge', [0]*21)
    down_q_live = get_live_river_discharge(down_lat, down_lon).get('daily', {}).get('river_discharge', [0]*21)
    
    num_nodes = len(nodes_cache)
    x_input = torch.zeros(1, num_nodes, 14, 4)
    up_idx = get_node_index(up_id)
    down_idx = get_node_index(down_id)
    
    for i in range(14):
        q_u = up_q_live[i] if i < len(up_q_live) else 0.0
        q_d = down_q_live[i] if i < len(down_q_live) else 0.0
        
        feat_u = np.array([[q_u, 0, 0, 0]])
        feat_d = np.array([[q_d, 0, 0, 0]])
        
        if scaler:
            feat_u = scaler.transform(feat_u)
            feat_d = scaler.transform(feat_d)
            
        x_input[0, up_idx, i, :] = torch.tensor(feat_u[0])
        x_input[0, down_idx, i, :] = torch.tensor(feat_d[0])

    adj = torch.eye(num_nodes)
    for i in range(num_nodes - 1): adj[i, i+1] = 1.0
    
    with torch.no_grad():
        if model:
            preds = model(x_input, adj)
        else:
            preds = torch.zeros(1, num_nodes, 6)
            
    forecasts = {}
    horizons = ["day_1_24h", "day_2_48h", "day_3_72h"]
    
    override_flows = {}
    if req.custom_dam_outflow_override:
        override_flows = propagate_overrides(req.custom_dam_outflow_override)
    
    W_r = terrain['wth']
    hand_m = terrain['hnd']
    
    # User Simulator Maximum Override Fix
    override_cusecs = 0.0
    if req.custom_dam_outflow_override:
        valid_vals = [float(v) for v in req.custom_dam_outflow_override.values() if v is not None]
        if valid_vals:
            override_cusecs = max(valid_vals)

    # CWC Calibrated Rating Curve for Base Flow
    tributaries = ["RAKASKOP", "SHIRUR_MARKANDEYA", "HIDKAL", "HIRANYAKESHI", "GOKAK_CONFLUENCE", "MUDHOL_GHATAPRABHA", "KALAMMAWADI", "KALLOL_CHIKODI", "MALAPRABHA_DAM", "BADAMI_MALAPRABHA", "WARANA", "RADHANAGARI"]
    main_rivers = ["KOYNA", "KARAD", "SANGLI", "KURUNDWAD", "HIPPARAGI", "ALMATTI", "NARAYANPUR", "UJJANI", "YADGIR", "JURALA", "TUNGABHADRA", "SRISAILAM", "NAGARJUNA", "PULICHINTALA", "PRAKASAM"]
    
    if up_id in main_rivers or down_id in main_rivers:
        q_base_cusecs = 45000.0
    else:
        q_base_cusecs = 15000.0

    r_len = float(terrain.get('wris_data', {}).get('river_length_km', 250.0) or 250.0)
    C_channel = 275.0 if r_len >= 250 else (190.0 if r_len >= 80 else 120.0)
    dist_shore = float(terrain.get('dist_r', 50.0) or 50.0)
    overbank_factor = 1.0 + 0.00085 * min(dist_shore, 2500.0)
    C_manning = C_channel * overbank_factor
    
    if is_water_body or hand_m <= 0.2:
        bankfull_threshold_cusecs = 0.0
    else:
        h_eff = max(0.0, hand_m - 0.85)
        bankfull_threshold_m3s = C_manning * (h_eff ** (5.0 / 3.0))
        bankfull_threshold_cusecs = round(bankfull_threshold_m3s * 35.3147, 0)
        
    slope_runoff_m = 0.0
    
    for h_idx, name in enumerate(horizons):
        q_glofas_up = up_q_live[14 + h_idx] if len(up_q_live) > 14 + h_idx else 0.0
        q_glofas_down = down_q_live[14 + h_idx] if len(down_q_live) > 14 + h_idx else 0.0
        
        ml_residual_up = max(-0.15, min(0.15, float(preds[0, up_idx, h_idx*2].item())))
        ml_residual_down = max(-0.15, min(0.15, float(preds[0, down_idx, h_idx*2].item())))
        
        if up_id in override_flows:
            q_up_pred = override_flows[up_id]
        else:
            q_up_pred = q_glofas_up * (1.0 + ml_residual_up)
            
        if down_id in override_flows:
            q_down_pred = override_flows[down_id]
        else:
            q_down_pred = q_glofas_down * (1.0 + ml_residual_down)
            
        q_up_pred = max(0.0, q_up_pred)
        q_down_pred = max(0.0, q_down_pred)
        
        q_local_cumecs = q_up_pred + terrain['reach_fraction_alpha'] * (q_down_pred - q_up_pred)
        # Note: override_cumecs is already handled via propagate_overrides, so we just use q_local_cumecs

        # Override Flow
        q_total_cusecs = q_local_cumecs * 35.3147
        if override_cusecs > 0.0:
            q_total_cusecs += override_cusecs
            
        # Physics
        q_m3s = q_total_cusecs / 35.3147
        if q_m3s <= 45.0:
            predicted_river_depth_m = round(0.85 * ((max(q_m3s, 5.0) / 45.0) ** 0.38), 2)
        else:
            predicted_river_depth_m = round(0.85 + ((q_m3s / C_manning) ** (3.0 / 5.0)), 2)
            
        if is_water_body:
            predicted_river_depth_m += 2.5
            
        wse_local = terrain['E_bed_r'] + predicted_river_depth_m

        if is_water_body or q_total_cusecs >= bankfull_threshold_cusecs or predicted_river_depth_m >= hand_m:
            will_submerge = True
            submersion_depth_m = round(max(0.25, predicted_river_depth_m - hand_m + slope_runoff_m), 2)
            safety_margin_m = 0.0
            risk_level = "CRITICAL"
        else:
            will_submerge = False
            submersion_depth_m = 0.0
            safety_margin_m = round(max(0.05, hand_m - predicted_river_depth_m), 2)
            risk_level = "WARNING" if safety_margin_m < 1.2 else "SAFE"
            
        forecasts[name] = ForecastDay(
            predicted_river_depth_m=predicted_river_depth_m,
            predicted_wse_m=wse_local,
            slope_runoff_accumulation_m=slope_runoff_m,
            will_submerge=will_submerge,
            submersion_depth_m=submersion_depth_m,
            safety_margin_m=safety_margin_m,
            risk_level=risk_level,
            predicted_discharge_cusecs=round(q_total_cusecs, 0),
            predicted_discharge_cumecs=round(q_total_cusecs / 35.3147, 1),
            upstream_inflow_cusecs=round(q_up_pred * 35.3147, 0),
            upstream_outflow_cusecs=round(q_down_pred * 35.3147, 0),
            bankfull_capacity_cusecs=bankfull_threshold_cusecs,
            wse_feet=round(wse_local * 3.28084, 2),
            ground_elevation_feet=round(terrain['elv'] * 3.28084, 2)
        )
        
    import datetime
    q_up_t0 = up_q_live[14] if len(up_q_live) > 14 else 0.0
    q_down_t0 = down_q_live[14] if len(down_q_live) > 14 else 0.0
    q_local_cumecs_t0 = max(0.0, q_up_t0 + terrain['reach_fraction_alpha'] * (q_down_t0 - q_up_t0))
    
    # Telemetry includes slider simulated flows as requested
    q_t0_cusecs = q_local_cumecs_t0 * 35.3147
    if override_cusecs > 0.0:
        q_t0_cusecs += override_cusecs
        
    q_t0_m3s = q_t0_cusecs / 35.3147
    if q_t0_m3s <= 50.0:
        current_river_depth_m = round(0.85 * ((max(q_t0_m3s, 5.0) / 50.0) ** 0.38), 2)
    else:
        current_river_depth_m = round(0.85 + ((q_t0_m3s / C_manning) ** (3.0 / 5.0)), 2)
        
    if is_water_body:
        current_river_depth_m += 2.5
        
    current_wse_m = terrain['E_bed_r'] + current_river_depth_m
    is_bankfull = True if is_water_body or q_t0_cusecs >= bankfull_threshold_cusecs or current_river_depth_m >= hand_m else False
    
    from backend.schemas import LiveTelemetry
    telemetry = LiveTelemetry(
        fetched_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
        data_provider="ECMWF GloFAS v4 / Open-Meteo Real-Time Telemetry",
        upstream_station=nodes_cache[up_idx]['station_name'],
        downstream_station=nodes_cache[down_idx]['station_name'],
        current_flow_cusecs=round(q_t0_cusecs, 0),
        current_flow_cumecs=round(q_t0_cusecs / 35.3147, 1),
        current_river_depth_m=round(current_river_depth_m, 2),
        current_river_depth_ft=round(current_river_depth_m * 3.28084, 2),
        current_wse_msl_m=round(current_wse_m, 2),
        current_wse_msl_ft=round(current_wse_m * 3.28084, 2),
        ground_clearance_m=round(terrain['hnd'] - current_river_depth_m, 2),
        is_bankfull=is_bankfull,
        bankfull_threshold_cusecs=bankfull_threshold_cusecs,
        raw_telemetry_source_url=f"https://flood-api.open-meteo.com/v1/flood?latitude={up_lat}&longitude={up_lon}&daily=river_discharge&forecast_days=3"
    )

    return PredictResponse(
        ground_elevation_m=terrain['elv'],
        nearest_river_point=f"{terrain['nearest_river_lat']},{terrain['nearest_river_lon']}",
        distance_to_river_m=terrain['distance_to_river_m'],
        river_bed_elevation_m=terrain['E_bed_r'],
        hand_m=terrain['hnd'],
        reach_fraction_alpha=terrain['reach_fraction_alpha'],
        local_slope_degrees=slope_deg,
        current_state="ACTIVE_RESERVOIR_WATER_BODY" if is_water_body else ("NORMAL" if not forecasts["day_1_24h"].will_submerge else forecasts["day_1_24h"].risk_level),
        forecasts=forecasts,
        is_water_body=is_water_body,
        water_body_name=water_body_name if is_water_body else None,
        live_telemetry=telemetry
    )

@app.get("/api/v1/benchmarks/100-cases")
def get_benchmarks():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bench_path = os.path.join(base_dir, 'data', 'benchmark_100_results.json')
    if not os.path.exists(bench_path):
        return []
    import json
    with open(bench_path, 'r', encoding='utf-8') as f:
        return json.load(f)
