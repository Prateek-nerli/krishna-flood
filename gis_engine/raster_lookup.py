import os
import json
import math
import numpy as np
import rasterio
from rasterio.windows import Window
import boto3
from botocore.config import Config

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

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # radius of Earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def project_point_on_segment(lat, lon, lat_A, lon_A, lat_B, lon_B):
    AB_lat = lat_B - lat_A
    AB_lon = lon_B - lon_A
    AP_lat = lat - lat_A
    AP_lon = lon - lon_A
    
    dot_AP_AB = AP_lat * AB_lat + AP_lon * AB_lon
    dot_AB_AB = AB_lat * AB_lat + AB_lon * AB_lon
    
    if dot_AB_AB == 0:
        alpha = 0.0
    else:
        alpha = max(0.0, min(1.0, dot_AP_AB / dot_AB_AB))
        
    P_river_lat = lat_A + alpha * AB_lat
    P_river_lon = lon_A + alpha * AB_lon
    return alpha, P_river_lat, P_river_lon

def get_copernicus_dem_window(lat, lon, window_size=9):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base_dir, 'data', 'dem_cache')
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f"dem_{lat}_{lon}_{window_size}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            data = json.load(f)
            return data['elv'], np.array(data['window'])
            
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
                row, col = src.index(lon, lat)  # rasterio index takes (lon, lat)
                offset = window_size // 2
                window = Window(col - offset, row - offset, window_size, window_size)
                dem_data = src.read(1, window=window)
                if dem_data.shape == (window_size, window_size):
                    elv = float(dem_data[offset, offset])
                    with open(cache_file, 'w') as f:
                        json.dump({'elv': elv, 'window': dem_data.tolist()}, f)
                    return elv, dem_data
                else:
                    return None, None
    except Exception as e:
        return None, None

def get_merit_hydro_data(lat, lon):
    elv, dem_window = get_copernicus_dem_window(lat, lon, window_size=5)
    
    if elv is None or dem_window is None:
        elv = 500.0
        dem_window = np.full((5, 5), 500.0)

    # 1. Compute tan_beta (slope) and D8 dir
    dz_dy, dz_dx = np.gradient(dem_window.astype(float), 30.0, 30.0)
    center_y, center_x = 2, 2
    tan_beta = float(np.sqrt(dz_dx[center_y, center_x]**2 + dz_dy[center_y, center_x]**2))
    tan_beta = float(np.clip(tan_beta, 0.0005, 1.5))
    
    angle = math.degrees(math.atan2(-dz_dy[center_y, center_x], dz_dx[center_y, center_x]))
    if angle < 0: angle += 360
    dirs = [1, 128, 64, 32, 16, 8, 4, 2]
    idx = int(round(angle / 45.0)) % 8
    d8_dir = dirs[idx]

    # 2. Continuous River-Reach Projection
    nodes = _get_nodes()
    node_map = {n['node_id']: n for n in nodes}
    
    best_reach = None
    min_dist = float('inf')
    
    for A in nodes:
        down_id = A.get('downstream_node')
        if not down_id or down_id not in node_map:
            continue
            
        B = node_map[down_id]
        
        alpha, P_r_lat, P_r_lon = project_point_on_segment(
            lat, lon, A['lat'], A['lon'], B['lat'], B['lon']
        )
        
        dist = haversine(lat, lon, P_r_lat, P_r_lon)
        if dist < min_dist:
            min_dist = dist
            E_bed_interp = A['bed_elev_m'] + alpha * (B['bed_elev_m'] - A['bed_elev_m'])
            
            w_A = 180.0
            w_B = 300.0
            if "wth" in A: w_A = A['wth']
            if "wth" in B: w_B = B['wth']
            
            wth_interp = w_A + alpha * (w_B - w_A)
            
            best_reach = {
                "upstream_node_id": A['node_id'],
                "downstream_node_id": B['node_id'],
                "reach_fraction_alpha": alpha,
                "nearest_river_lat": P_r_lat,
                "nearest_river_lon": P_r_lon,
                "distance_to_river_m": dist,
                "E_bed_interp": E_bed_interp,
                "upa": 1000.0,
                "wth": wth_interp
            }
            
    if best_reach:
        dist_r = best_reach['distance_to_river_m']
        nearest_lat = best_reach['nearest_river_lat']
        nearest_lon = best_reach['nearest_river_lon']
        is_settlement = False
        
        try:
            import requests
            # Use Nominatim for settlement check
            nom_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
            nom_resp = requests.get(nom_url, headers={'User-Agent': 'FloodApp/1.0'}, timeout=2)
            if nom_resp.status_code == 200:
                add = nom_resp.json().get('address', {})
                if any(k in add for k in ['village', 'town', 'city', 'hamlet', 'residential']):
                    is_settlement = True

            overpass_url = "https://overpass-api.de/api/interpreter"
            query = f"""[out:json][timeout:5];(way["waterway"~"river|canal|stream"](around:3500,{lat},{lon});way["natural"="water"](around:3500,{lat},{lon});relation["natural"="water"](around:3500,{lat},{lon}););out geom;"""
            resp = requests.post(overpass_url, data={'data': query}, timeout=6)
            
            is_inside_water_poly = False
            
            if resp.status_code == 200:
                data = resp.json()
                min_osm_dist = float('inf')
                best_osm_lat, best_osm_lon = None, None
                for element in data.get('elements', []):
                    # For ways and relations
                    tags = element.get('tags', {})
                    is_water_poly = tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank'
                    
                    if element.get('type') == 'way' and element.get('geometry'):
                        for pt in element.get('geometry', []):
                            d = haversine(lat, lon, pt['lat'], pt['lon'])
                            if d < min_osm_dist:
                                min_osm_dist = d
                                best_osm_lat, best_osm_lon = pt['lat'], pt['lon']
                            if is_water_poly and d < 50.0:
                                is_inside_water_poly = True
                
                if best_osm_lat is not None and min_osm_dist < dist_r:
                    dist_r = min_osm_dist
                    nearest_lat = best_osm_lat
                    nearest_lon = best_osm_lon
        except Exception:
            pass

        _, local_dem_11x11 = get_copernicus_dem_window(lat, lon, window_size=11)
        
        # Check c) DEM valley check
        is_dem_valley = False
        if local_dem_11x11 is not None:
            p12 = float(np.nanpercentile(local_dem_11x11, 12))
            local_slope = 0.5
            try:
                dz_dy, dz_dx = np.gradient(local_dem_11x11.astype(float), 30.0, 30.0)
                tb = float(math.sqrt(dz_dx[5, 5]**2 + dz_dy[5, 5]**2))
                local_slope = math.degrees(math.atan(tb))
            except:
                pass
            if (elv - p12 <= 0.75) and (local_slope <= 1.2):
                is_dem_valley = True

        if is_inside_water_poly or dist_r <= 55.0 or is_dem_valley:
            dist_r = 0.0
            hand_m = 0.0
            E_bed_r = round(elv - 2.5, 2)
            is_water_body_raster = True
        else:
            is_water_body_raster = False
            if local_dem_11x11 is not None:
                local_relief_m = max(0.5, elv - float(np.nanpercentile(local_dem_11x11, 10)))
            else:
                local_relief_m = 0.5
                
            hand_m = float(np.clip(1.2 + 0.65 * local_relief_m + (min(dist_r, 1500.0) / 220.0), 1.5, 12.0))
            E_bed_r = round(elv - hand_m, 2)
            
        hnd = float(hand_m)
            
        return {
            "upstream_node_id": best_reach['upstream_node_id'],
            "downstream_node_id": best_reach['downstream_node_id'],
            "reach_fraction_alpha": best_reach['reach_fraction_alpha'],
            "nearest_river_lat": nearest_lat,
            "nearest_river_lon": nearest_lon,
            "distance_to_river_m": dist_r,
            "E_bed_r": E_bed_r,
            "elv": elv,
            "hnd": hnd,
            "is_water_body_raster": locals().get("is_water_body_raster", False),
            "tan_beta": tan_beta,
            "dir": d8_dir,
            "upa": best_reach['upa'],
            "wth": best_reach['wth']
        }
    else:
        return {
            "upstream_node_id": "UNKNOWN",
            "downstream_node_id": "UNKNOWN",
            "reach_fraction_alpha": 0.0,
            "nearest_river_lat": lat,
            "nearest_river_lon": lon,
            "distance_to_river_m": 0.0,
            "E_bed_r": elv - 5.0,
            "elv": elv,
            "hnd": 5.0,
            "tan_beta": tan_beta,
            "dir": d8_dir,
            "upa": 1000.0,
            "wth": 250.0
        }
