import os
import json
import os
os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
os.environ["GDAL_HTTP_UNSAFESSL"] = "YES"

import math
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import rasterio
from rasterio.windows import Window
import boto3
from botocore.config import Config
import requests
import urllib3
from scipy.spatial import cKDTree

urllib3.disable_warnings()

_nodes = None
def _get_nodes():
    global _nodes
    if _nodes is None:
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(base_dir, 'seed_nodes.json'), 'r') as f:
                _nodes = json.load(f)
        except Exception:
            _nodes = []
    return _nodes

_wris_tree = None
_wris_metadata = None

def _get_wris_network():
    global _wris_tree, _wris_metadata
    if _wris_tree is None:
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            npz_path = os.path.join(base_dir, 'data', 'krishna_dense_points.npz')
            if os.path.exists(npz_path):
                data = np.load(npz_path, allow_pickle=True)
                points = data['points']
                _wris_metadata = data['metadata']
                _wris_tree = cKDTree(points)
        except Exception as e:
            pass
    return _wris_tree, _wris_metadata

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

import scipy.ndimage
def get_copernicus_dem_window(lat, lon, window_size=5):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base_dir, 'data', 'dem_cache')
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f"dem_{lat}_{lon}_{window_size}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            data = json.load(f)
            return data.get('elv'), data.get('peak_3x3'), np.array(data['window'])
            
    lat_deg = int(lat)
    lon_deg = int(lon)
    lat_str = f"N{lat_deg:02d}_00" if lat >= 0 else f"S{abs(lat_deg):02d}_00"
    lon_str = f"E{lon_deg:03d}_00" if lon >= 0 else f"W{abs(lon_deg):03d}_00"
    
    tile_name = f"Copernicus_DSM_COG_10_{lat_str}_{lon_str}_DEM"
    s3_path = f"s3://copernicus-dem-30m/{tile_name}/{tile_name}.tif"
    
    aws_session = rasterio.session.AWSSession(
        boto3.Session(),
        requester_pays=False,
        aws_unsigned=True
    )
    
    try:
        with rasterio.Env(aws_session):
            with rasterio.open(s3_path) as src:
                row, col = src.index(lon, lat)
                offset = window_size // 2
                window = Window(col - offset, row - offset, window_size, window_size)
                dem_data = src.read(1, window=window)
                if dem_data.shape == (window_size, window_size):
                    # Sub-pixel bicubic interpolation
                    col_f, row_f = ~src.transform * (lon, lat)
                    local_col = col_f - (col - offset)
                    local_row = row_f - (row - offset)
                    elv = float(scipy.ndimage.map_coordinates(dem_data, [[local_row], [local_col]], order=3, mode='nearest')[0])
                    
                    # Local 3x3 inner window peak
                    peak_3x3 = float(np.nanmax(dem_data[offset-1:offset+2, offset-1:offset+2]))
                    
                    with open(cache_file, 'w') as f:
                        json.dump({'elv': elv, 'peak_3x3': peak_3x3, 'window': dem_data.tolist()}, f)
                    return elv, peak_3x3, dem_data
                else:
                    return None, None, None
    except Exception as e:
        return None, None, None

import time

def get_true_elevation_batch(lat, lon, nearest_lat, nearest_lon, cop_peak=None, cop_5x5_p10=None):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base_dir, 'data', 'dem_cache')
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f"true_elev_{round(lat, 5)}_{round(lon, 5)}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
                return float(data['ground_elev'])
        except:
            pass

    ground_30m = []
    
    def fetch_srtm():
        try:
            r = requests.get(f"https://api.opentopodata.org/v1/srtm30m?locations={lat},{lon}&interpolation=cubic", verify=False, timeout=2.5)
            if r.status_code == 200:
                res = r.json().get('results', [])
                if len(res) >= 1 and res[0].get('elevation') is not None:
                    return float(res[0]['elevation'])
        except:
            pass
        return None
        
    def fetch_open_meteo():
        try:
            r = requests.get(f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}", verify=False, timeout=2.0)
            if r.status_code == 200:
                res = r.json().get('elevation', [])
                if len(res) == 1:
                    return float(res[0])
        except:
            pass
        return None
        
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(fetch_srtm)
        f2 = executor.submit(fetch_open_meteo)
        v1 = f1.result()
        v2 = f2.result()
        if v1 is not None: ground_30m.append(v1)
        if v2 is not None: ground_30m.append(v2)

    if cop_peak is not None:
        ground_30m.append(cop_peak)
        
    valid_g = [e for e in ground_30m if e is not None and e > 0]
    ground_elev = round(float(max(valid_g)), 2) if valid_g else 500.0
    
    try:
        with open(cache_file, 'w') as f:
            json.dump({'ground_elev': ground_elev}, f)
    except:
        pass
        
    return ground_elev

def get_merit_hydro_data(lat, lon):
    cop_ground_elv, cop_peak, dem_ground_5x5 = get_copernicus_dem_window(lat, lon, window_size=5)
    if dem_ground_5x5 is None:
        dem_ground_5x5 = np.full((5, 5), 500.0)
        cop_ground_elv = 500.0
        cop_peak = 500.0

    dz_dy, dz_dx = np.gradient(dem_ground_5x5.astype(float), 30.0, 30.0)
    center_y, center_x = 2, 2
    local_slope_rad = float(np.sqrt(dz_dx[center_y, center_x]**2 + dz_dy[center_y, center_x]**2))
    local_slope_deg = math.degrees(math.atan(local_slope_rad))
    tan_beta = float(np.clip(local_slope_rad, 0.0005, 1.5))
    
    angle = math.degrees(math.atan2(-dz_dy[center_y, center_x], dz_dx[center_y, center_x]))
    if angle < 0: angle += 360
    dirs = [1, 128, 64, 32, 16, 8, 4, 2]
    idx = int(round(angle / 45.0)) % 8
    d8_dir = dirs[idx]
    
    tree, metadata = _get_wris_network()
    
    wris_data = None
    dist_to_centerline = 0.0
    nearest_lat, nearest_lon = lat, lon
    river_length_km = 0.0
    
    if tree is not None:
        dist_deg, point_idx = tree.query([lat, lon], k=1)
        nearest_lat = tree.data[point_idx][0]
        nearest_lon = tree.data[point_idx][1]
        dist_to_centerline = haversine(lat, lon, nearest_lat, nearest_lon)
        
        meta = metadata[point_idx]
        river_length_km = float(meta[3])
        wris_data = {
            "wris_river_name": meta[1],
            "wris_confluence_point": meta[2],
            "river_length_km": river_length_km,
            "start_tehsil": meta[4],
            "end_tehsil": meta[5]
        }
        
    cop_river_elv, cop_peak_river, dem_river_5x5 = get_copernicus_dem_window(nearest_lat, nearest_lon, window_size=5)
    
    if dem_river_5x5 is not None:
        river_water_surface_cop = float(np.nanpercentile(dem_river_5x5, 15))
    else:
        river_water_surface_cop = cop_ground_elv - 5.0
        
    rel_height_above_water = max(0.0, cop_ground_elv - river_water_surface_cop)
    
    elv = get_true_elevation_batch(lat, lon, nearest_lat, nearest_lon, cop_peak, None)
    
    # Exact datum offset applied equally
    river_surface_elv = elv - rel_height_above_water
    
    # Physical river width model
    if river_length_km >= 800:
        half_width_m = 225.0
    elif river_length_km >= 200:
        half_width_m = 140.0
    elif river_length_km >= 60:
        half_width_m = 80.0
    else:
        half_width_m = 35.0
        
    # Detect water body
    is_water_body_raster = False
    if dist_to_centerline <= half_width_m and rel_height_above_water <= 2.2 and local_slope_deg <= 2.5:
        is_water_body_raster = True
    elif dist_to_centerline <= 600.0 and rel_height_above_water <= 1.2 and local_slope_deg <= 1.5:
        is_water_body_raster = True
        
    nodes = _get_nodes()
    
    if not is_water_body_raster:
        for n in nodes:
            if n.get('node_role') in ['RESERVOIR', 'BARRAGE']:
                n_dist = haversine(lat, lon, n['lat'], n['lon'])
                if n_dist <= 2500.0 and rel_height_above_water <= 2.5:
                    is_water_body_raster = True
                    break

    if is_water_body_raster:
        dist_r = 0.0
        hand_m = 0.0
        E_bed_r = round(elv - 2.5, 2)
    else:
        dist_r = round(max(5.0, dist_to_centerline - half_width_m), 2)
        E_bed_r = round(river_surface_elv - 1.5, 2)
        hand_m = round(max(1.0, elv - E_bed_r), 2)

    nodes = _get_nodes()
    upstream_id = "UNKNOWN"
    downstream_id = "UNKNOWN"
    min_dist_edge = float('inf')
    alpha = 0.0
    
    node_map = {n['node_id']: n for n in nodes}
    for n in nodes:
        if n.get('downstream_node') and n['downstream_node'] in node_map:
            B = node_map[n['downstream_node']]
            A_lat, A_lon = n['lat'], n['lon']
            B_lat, B_lon = B['lat'], B['lon']
            
            AB_lat = B_lat - A_lat
            AB_lon = B_lon - A_lon
            AP_lat = nearest_lat - A_lat
            AP_lon = nearest_lon - A_lon
            dot = AP_lat * AB_lat + AP_lon * AB_lon
            ab2 = AB_lat**2 + AB_lon**2
            t = max(0.0, min(1.0, dot / ab2)) if ab2 > 0 else 0.0
            p_lat = A_lat + t * AB_lat
            p_lon = A_lon + t * AB_lon
            d = math.hypot(nearest_lat - p_lat, nearest_lon - p_lon)
            if d < min_dist_edge:
                min_dist_edge = d
                upstream_id = n['node_id']
                downstream_id = B['node_id']
                alpha = t

    hnd = float(hand_m)
    wth = half_width_m * 2.0
    upa = round(max(150.0, (river_length_km ** 1.65) * 3.2), 1)
        
    try:
        from gis_engine.satellite_water_vision import analyze_satellite_water_boundary
        vision = analyze_satellite_water_boundary(lat, lon, nearest_lat, nearest_lon)
        dist_r = vision['exact_shoreline_dist_m']
        nearest_lat = vision['shoreline_lat']
        nearest_lon = vision['shoreline_lon']
        wth = vision['measured_width_m']
        if vision['is_water_body_image']:
            is_water_body_raster = True
        if wris_data is None:
            wris_data = {}
        wris_data.update(vision['satellite_pass_info'])
    except Exception as e:
        print("VISION ERROR:", e)

    return {
        "elv": float(elv),
        "E_bed_r": float(E_bed_r),
        "river_bed_elevation": float(E_bed_r),
        "hnd": float(hnd),
        "dist_r": float(dist_r),
        "distance_to_river_m": float(dist_r),
        "nearest_river_lat": float(nearest_lat),
        "nearest_river_lon": float(nearest_lon),
        "upa": float(upa),
        "uparea": float(upa),
        "wth": float(wth),
        "d8_dir": int(d8_dir),
        "dir": int(d8_dir),
        "tan_beta": float(tan_beta),
        "wris_data": wris_data,
        "upstream_node_id": str(upstream_id),
        "downstream_node_id": str(downstream_id),
        "reach_fraction_alpha": float(alpha),
        "is_water_body_raster": bool(is_water_body_raster),
    }
