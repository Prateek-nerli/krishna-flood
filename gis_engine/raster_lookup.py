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

_wris_all_tree = None
_wris_all_metadata = None
_wris_major_tree = None
_wris_major_metadata = None

def _get_wris_network():
    global _wris_all_tree, _wris_all_metadata, _wris_major_tree, _wris_major_metadata
    if _wris_all_tree is None:
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            npz_path = os.path.join(base_dir, 'data', 'krishna_dense_points.npz')
            if os.path.exists(npz_path):
                data = np.load(npz_path, allow_pickle=True)
                points = data['points']
                metadata = data['metadata']
                _wris_all_metadata = metadata
                _wris_all_tree = cKDTree(points)
                
                # Major perennial rivers (length >= 50.0 km)
                major_mask = metadata[:, 3].astype(float) >= 50.0
                _wris_major_metadata = metadata[major_mask]
                _wris_major_tree = cKDTree(points[major_mask])
        except Exception as e:
            pass
    return _wris_all_tree, _wris_all_metadata, _wris_major_tree, _wris_major_metadata

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

def get_copernicus_dem_patch(lat: float, lon: float, size_px: int = 96):
    """
    Returns (dem_array, transform, crs) for a ~2.88 km x 2.88 km window centered at (lat, lon)
    using Copernicus GLO-30 DEM, cached in data/dem_cache/.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(base_dir, 'data', 'dem_cache')
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f"dem_patch_{round(lat, 4)}_{round(lon, 4)}_{size_px}.npz")
    if os.path.exists(cache_file):
        try:
            data = np.load(cache_file, allow_pickle=True)
            dem_arr = data['dem_data']
            transform_vals = data['transform']
            from rasterio.transform import Affine
            transform = Affine(transform_vals[0], transform_vals[1], transform_vals[2],
                               transform_vals[3], transform_vals[4], transform_vals[5])
            crs = str(data['crs'])
            return dem_arr, transform, crs
        except Exception:
            pass

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
                offset = size_px // 2
                window = Window(col - offset, row - offset, size_px, size_px)
                dem_data = src.read(1, window=window).astype(np.float32)
                win_transform = src.window_transform(window)
                crs = str(src.crs)
                
                try:
                    np.savez_compressed(
                        cache_file,
                        dem_data=dem_data,
                        transform=np.array([win_transform.a, win_transform.b, win_transform.c,
                                            win_transform.d, win_transform.e, win_transform.f], dtype=np.float64),
                        crs=crs
                    )
                except Exception:
                    pass
                return dem_data, win_transform, crs
    except Exception as e:
        raise RuntimeError("COPERNICUS_DEM_UNAVAILABLE") from e

import time

def get_true_elevation_batch(lat, lon, cop_center_raw):
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
            r = requests.get(f"https://api.opentopodata.org/v1/srtm30m?locations={lat},{lon}&interpolation=cubic", verify=True, timeout=2.5)
            if r.status_code == 200:
                res = r.json().get('results', [])
                if len(res) >= 1 and res[0].get('elevation') is not None:
                    return float(res[0]['elevation'])
        except:
            pass
        return None
        
    def fetch_open_meteo():
        try:
            r = requests.get(f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}", verify=True, timeout=2.0)
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

    if cop_center_raw is not None and cop_center_raw > 0:
        ground_30m.append(cop_center_raw)
        
    valid_g = [e for e in ground_30m if e is not None and e > 0]
    ground_elev = round(float(np.median(valid_g)), 2) if valid_g else 500.0
    
    try:
        with open(cache_file, 'w') as f:
            json.dump({'ground_elev': ground_elev}, f)
    except:
        pass
        
    return ground_elev

def get_merit_hydro_data(lat, lon):
    import math
    cop_ground_elv, cop_peak, dem_ground_5x5 = get_copernicus_dem_window(lat, lon, window_size=5)
    if dem_ground_5x5 is None:
        dem_ground_5x5 = np.full((5, 5), 500.0)
        bare_ground_cop = 500.0
        cop_center_raw = 500.0
    else:
        bare_ground_cop = float(np.nanpercentile(dem_ground_5x5, 20))
        cop_center_raw = float(dem_ground_5x5[2, 2])

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
    
    all_tree, all_meta, major_tree, major_meta = _get_wris_network()
    
    local_dist_m = 0.0
    local_lat, local_lon = lat, lon
    local_rivname = "Local Drainage"
    local_len_km = 10.0
    local_meta_item = None
    
    if all_tree is not None:
        d_deg, p_idx = all_tree.query([lat, lon], k=1)
        local_lat = float(all_tree.data[p_idx][0])
        local_lon = float(all_tree.data[p_idx][1])
        local_dist_m = haversine(lat, lon, local_lat, local_lon)
        local_meta_item = all_meta[p_idx]
        local_rivname = str(local_meta_item[1])
        local_len_km = float(local_meta_item[3])

    maj_dist_m = 0.0
    maj_lat, maj_lon = lat, lon
    maj_rivname = "Krishna River"
    maj_len_km = 250.0
    maj_meta_item = None

    if major_tree is not None:
        d_deg, p_idx = major_tree.query([lat, lon], k=1)
        maj_lat = float(major_tree.data[p_idx][0])
        maj_lon = float(major_tree.data[p_idx][1])
        maj_dist_m = haversine(lat, lon, maj_lat, maj_lon)
        maj_meta_item = major_meta[p_idx]
        maj_rivname = str(maj_meta_item[1])
        maj_len_km = float(maj_meta_item[3])

    # Run satellite water vision at (lat, lon) and local channel
    is_water_body_image = False
    measured_wth = 0.0
    exact_shore_dist = None
    vision_extra = {}
    try:
        from gis_engine.satellite_water_vision import analyze_satellite_water_boundary
        vision = analyze_satellite_water_boundary(lat, lon, local_lat, local_lon)
        if vision.get('exact_shoreline_dist_m') is not None and vision['exact_shoreline_dist_m'] > 0.0:
            exact_shore_dist = vision['exact_shoreline_dist_m']
        measured_wth = float(vision.get('measured_width_m', 0.0) or 0.0)
        is_water_body_image = bool(vision.get('is_water_body_image', False))
        vision_extra = vision.get('satellite_pass_info', {})
    except Exception as e:
        print("VISION ERROR:", e)

    # Seasonal stream routing logic
    if local_len_km < 50.0 and (not is_water_body_image and measured_wth == 0.0):
        # Ephemeral seasonal halla without active surface water -> route to major perennial river
        river_length_km = maj_len_km
        nearest_lat = maj_lat
        nearest_lon = maj_lon
        dist_to_centerline = maj_dist_m
        wris_data = {
            "wris_river_name": maj_rivname,
            "wris_confluence_point": maj_meta_item[2] if maj_meta_item is not None else "Krishna River",
            "river_length_km": maj_len_km,
            "start_tehsil": maj_meta_item[4] if maj_meta_item is not None else "",
            "end_tehsil": maj_meta_item[5] if maj_meta_item is not None else "",
            "nearest_local_stream": f"{local_rivname} ({local_len_km:.1f} km, seasonal/dry)"
        }
    else:
        river_length_km = local_len_km
        nearest_lat = local_lat
        nearest_lon = local_lon
        dist_to_centerline = local_dist_m
        wris_data = {
            "wris_river_name": local_rivname,
            "wris_confluence_point": local_meta_item[2] if local_meta_item is not None else "",
            "river_length_km": local_len_km,
            "start_tehsil": local_meta_item[4] if local_meta_item is not None else "",
            "end_tehsil": local_meta_item[5] if local_meta_item is not None else ""
        }

    if vision_extra:
        wris_data.update(vision_extra)

    # Physical river width model
    if river_length_km >= 800:
        half_width_m = 225.0
    elif river_length_km >= 200:
        half_width_m = 140.0
    elif river_length_km >= 60:
        half_width_m = 80.0
    else:
        half_width_m = 35.0

    wth = measured_wth if measured_wth > 0 else half_width_m * 2.0
    if exact_shore_dist is not None and exact_shore_dist > 0.0:
        dist_r = exact_shore_dist
    else:
        dist_r = max(5.0, dist_to_centerline - half_width_m)

    cop_river_elv, cop_peak_river, dem_river_5x5 = get_copernicus_dem_window(nearest_lat, nearest_lon, window_size=5)
    if dem_river_5x5 is not None:
        cop_river_surface = float(np.nanpercentile(dem_river_5x5, 20))
    else:
        cop_river_surface = bare_ground_cop - 5.0

    raw_bare_diff = max(0.5, bare_ground_cop - cop_river_surface)
    canopy_filtered_diff = 2.25 * math.log1p(raw_bare_diff / 1.8) + 0.0012 * min(dist_r, 2500.0)
    rel_height_above_water = round(min(raw_bare_diff, canopy_filtered_diff) if raw_bare_diff > 2.2 else raw_bare_diff + 0.0010 * min(dist_r, 2500.0), 2)

    elv = round(float(cop_center_raw), 2)
    river_surface_elv = round(elv - rel_height_above_water, 2)

    # Strict water body detection: active satellite water OR inside bank channel with minimal slope/elevation diff
    is_water_body_raster = False
    if is_water_body_image:
        is_water_body_raster = True
    elif dist_to_centerline <= half_width_m and rel_height_above_water <= 1.5 and local_slope_deg <= 2.0:
        is_water_body_raster = True

    if is_water_body_raster:
        dist_r = 0.0
        hand_m = 0.0
        E_bed_r = round(elv - 2.5, 2)
    else:
        E_bed_r = round(river_surface_elv - 1.0, 2)
        hand_m = round(max(1.0, elv - E_bed_r), 2)

    # Node projection for DAG edge fraction
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
    upa = round(max(150.0, (river_length_km ** 1.65) * 3.2), 1)

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
        "wris_river_name": wris_data.get("wris_river_name", "Krishna River"),
        "wris_data": wris_data,
        "upstream_node_id": str(upstream_id),
        "downstream_node_id": str(downstream_id),
        "reach_fraction_alpha": float(alpha),
        "is_water_body_raster": bool(is_water_body_raster),
        "manning_n_fp": float(wris_data.get('manning_n_fp', 0.045) or 0.045),
        "mean_floodplain_ndvi": float(wris_data.get('mean_floodplain_ndvi', 0.25) or 0.25),
        "bank_transect": wris_data.get('bank_transect'),
    }
