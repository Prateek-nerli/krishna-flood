import math
import os
import json
import base64
import requests
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from pyproj import Transformer
from concurrent.futures import ThreadPoolExecutor
import urllib3
import scipy.ndimage as ndimage
import cv2

urllib3.disable_warnings()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_stac_items(lat, lon, collection="sentinel-2-l2a", limit=8):
    stac_url = "https://earth-search.aws.element84.com/v1/search"
    payload = {
        "collections": [collection],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "limit": limit,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}]
    }
    try:
        r = requests.post(stac_url, json=payload, timeout=3.0, verify=False)
        if r.status_code == 200:
            return r.json().get('features', [])
    except:
        pass
    return []

def get_pc_item_id(lat, lon):
    try:
        url = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
        payload = {
            "collections": ["sentinel-2-l2a"],
            "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "limit": 1,
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]
        }
        r = requests.post(url, json=payload, timeout=2.5, verify=False)
        if r.status_code == 200:
            feats = r.json().get('features', [])
            if feats:
                return feats[0]['id'], feats[0]['properties'].get('datetime', '')[:10]
    except:
        pass
    return None, None

def read_band(href, center_lon, center_lat, size_m=2400, out_px=240):
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES"):
        with rasterio.open(href) as src:
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            cx, cy = transformer.transform(center_lon, center_lat)
            
            left = cx - size_m / 2.0
            right = cx + size_m / 2.0
            bottom = cy - size_m / 2.0
            top = cy + size_m / 2.0
            
            win = from_bounds(left, bottom, right, top, src.transform)
            
            data = src.read(
                1, 
                window=win, 
                out_shape=(out_px, out_px),
                resampling=rasterio.enums.Resampling.nearest
            )
            
            # transform for the output window
            win_transform = src.window_transform(win)
            x_scale = win_transform.a * win.width / out_px
            y_scale = win_transform.e * win.height / out_px
            
            out_transform = rasterio.Affine(x_scale, win_transform.b, win_transform.c,
                                            win_transform.d, y_scale, win_transform.f)
                                            
            return data.astype(float), out_transform, src.crs

def analyze_satellite_water_boundary(lat, lon, nearest_river_lat, nearest_river_lon):
    out_px = 240
    size_m = 2400
    
    dist_to_river = haversine(lat, lon, nearest_river_lat, nearest_river_lon)
    if dist_to_river <= (size_m/2.0 - 150):
        center_lat = (lat + nearest_river_lat) / 2.0
        center_lon = (lon + nearest_river_lon) / 2.0
    else:
        center_lat = nearest_river_lat
        center_lon = nearest_river_lon
        
    s2_feats = get_stac_items(center_lat, center_lon, "sentinel-2-l2a", 8)
    if not s2_feats:
        raise Exception("No STAC Sentinel-2 features found")
        
    feat_primary = s2_feats[0]
    
    pc_item_id, pc_date = get_pc_item_id(center_lat, center_lon)
    if not pc_item_id:
        pc_item_id = feat_primary.get('id', 'S2A_MSIL2A_20261002T053241_R105_T43PDT_20261002T101803')
        pc_date = feat_primary['properties'].get('datetime', '2026-10-02')[:10]
        
    assets = feat_primary.get('assets', {})
    if 'green' not in assets or 'nir' not in assets or 'scl' not in assets or 'red' not in assets:
        raise Exception("Missing S2 bands in STAC asset")
        
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_green = executor.submit(read_band, assets['green']['href'], center_lon, center_lat, size_m, out_px)
        f_red = executor.submit(read_band, assets['red']['href'], center_lon, center_lat, size_m, out_px)
        f_nir = executor.submit(read_band, assets['nir']['href'], center_lon, center_lat, size_m, out_px)
        f_scl = executor.submit(read_band, assets['scl']['href'], center_lon, center_lat, size_m, out_px)
        green, transform, crs = f_green.result()
        red, _, _ = f_red.result()
        nir, _, _ = f_nir.result()
        scl, _, _ = f_scl.result()
        
    ndwi = (green - nir) / (green + nir + 1e-6)
    
    # Evaluate local cloud % on central 100x100
    mid = out_px // 2
    local_cloud_pct = float(np.mean(np.isin(scl[mid-50:mid+50, mid-50:mid+50], [8, 9])) * 100.0)
    
    # 8=Cloud Medium, 9=Cloud High, 10=Thin Cirrus, 3=Cloud Shadow, 6=Water
    clouds = np.isin(scl, [8, 9, 10])
    s2_water = ((ndwi > 0.02) | (scl == 6)) & (~np.isin(scl, [3, 8, 9, 10]))
    
    live_water_mask = s2_water
    detection_mode = "Sentinel-2 Optical + NIR (Clear Sky)"
    
    # Fallback logic
    if local_cloud_pct > 25.0:
        detection_mode = "Sentinel-1 SAR Radar + Multi-Pass S2 (Cloud-Penetrating)"
        
        feat_secondary = None
        for f in s2_feats[1:]:
            if f['properties'].get('eo:cloud_cover', 100) <= 25:
                feat_secondary = f
                break
                
        s1_feats = get_stac_items(center_lat, center_lon, "sentinel-1-grd", 2)
        feat_s1 = s1_feats[0] if s1_feats else None
        
        def fetch_extra_bands():
            res = {}
            try:
                if feat_secondary:
                    s_assets = feat_secondary['assets']
                    res['s2_sec_green'] = read_band(s_assets['green']['href'], center_lon, center_lat, size_m, out_px)
                    res['s2_sec_nir'] = read_band(s_assets['nir']['href'], center_lon, center_lat, size_m, out_px)
                    res['s2_sec_scl'] = read_band(s_assets['scl']['href'], center_lon, center_lat, size_m, out_px)
                if feat_s1:
                    s1_assets = feat_s1['assets']
                    vv_href = None
                    if 'vv' in s1_assets: vv_href = s1_assets['vv']['href']
                    elif 'gamma0_vv' in s1_assets: vv_href = s1_assets['gamma0_vv']['href']
                    if vv_href:
                        res['s1_vv'] = read_band(vv_href, center_lon, center_lat, size_m, out_px)
            except:
                pass
            return res
            
        extra_bands = fetch_extra_bands()
        
        if 's2_sec_green' in extra_bands and 's2_sec_nir' in extra_bands and 's2_sec_scl' in extra_bands:
            s2g, _, _ = extra_bands['s2_sec_green']
            s2n, _, _ = extra_bands['s2_sec_nir']
            s2s, _, _ = extra_bands['s2_sec_scl']
            
            s2_ndwi = (s2g - s2n) / (s2g + s2n + 1e-6)
            sec_water = ((s2_ndwi > 0.02) | (s2s == 6)) & (~np.isin(s2s, [3, 8, 9, 10]))
            
            live_water_mask[clouds] = sec_water[clouds]
            
        if 's1_vv' in extra_bands:
            vv_data, _, _ = extra_bands['s1_vv']
            vv_db = 10 * np.log10(vv_data + 1e-6)
            s1_water = vv_db < -16.0
            live_water_mask = live_water_mask | (clouds & s1_water)
            
    # Projections
    proj_to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    ll_to_proj = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    
    def ll_to_px(l_lat, l_lon):
        x, y = ll_to_proj.transform(l_lon, l_lat)
        col, row = ~transform * (x, y)
        return int(round(row)), int(round(col))
        
    def px_to_ll(row, col):
        x, y = transform * (col, row)
        l_lon, l_lat = proj_to_ll.transform(x, y)
        return l_lat, l_lon
        
    py_click, px_click = ll_to_px(lat, lon)
    py_river, px_river = ll_to_px(nearest_river_lat, nearest_river_lon)
    
    h, w = live_water_mask.shape
    live_water_mask = ndimage.binary_opening(live_water_mask, structure=np.ones((3,3)))
    labeled, num_features = ndimage.label(live_water_mask)
    
    final_water_mask = np.zeros_like(live_water_mask)
    if num_features > 0 and 0 <= py_river < h and 0 <= px_river < w:
        river_label = labeled[py_river, px_river]
        if river_label == 0:
            dist_to_labels, inds = ndimage.distance_transform_edt(labeled == 0, return_indices=True)
            closest_py, closest_px = inds[0, py_river, px_river], inds[1, py_river, px_river]
            river_label = labeled[closest_py, closest_px]
        
        if river_label > 0:
            final_water_mask = (labeled == river_label)
            final_water_mask = ndimage.binary_fill_holes(final_water_mask)
    else:
        final_water_mask = live_water_mask
        
    live_water_mask = final_water_mask
        
    is_water_body_image = False
    shoreline_lat, shoreline_lon = lat, lon
    exact_shoreline_dist_m = 0.0
    py_shore, px_shore = py_click, px_click
    
    if 0 <= py_click < h and 0 <= px_click < w and live_water_mask[py_click, px_click]:
        is_water_body_image = True
    else:
        if np.any(live_water_mask):
            dist_mat, inds = ndimage.distance_transform_edt(~live_water_mask, return_indices=True)
            if 0 <= py_click < h and 0 <= px_click < w:
                py_shore, px_shore = inds[0, py_click, px_click], inds[1, py_click, px_click]
                shoreline_lat, shoreline_lon = px_to_ll(py_shore, px_shore)
                exact_shoreline_dist_m = round(haversine(lat, lon, shoreline_lat, shoreline_lon), 2)
            else:
                wm_y, wm_x = np.where(live_water_mask)
                if len(wm_y) > 0:
                    y_diff = wm_y - py_click
                    x_diff = wm_x - px_click
                    dists = y_diff**2 + x_diff**2
                    min_idx = np.argmin(dists)
                    py_shore, px_shore = wm_y[min_idx], wm_x[min_idx]
                    shoreline_lat, shoreline_lon = px_to_ll(py_shore, px_shore)
                    exact_shoreline_dist_m = round(haversine(lat, lon, shoreline_lat, shoreline_lon), 2)
                else:
                    exact_shoreline_dist_m = round(dist_to_river, 2)
                    shoreline_lat, shoreline_lon = nearest_river_lat, nearest_river_lon
                    
    bankA_lat, bankA_lon = nearest_river_lat, nearest_river_lon
    bankB_lat, bankB_lon = nearest_river_lat, nearest_river_lon
    measured_width_m = 0.0
    pyA, pxA = py_river, px_river
    pyB, pxB = py_river, px_river
    
    y_coords, x_coords = np.nonzero(live_water_mask)
    if 0 <= py_river < h and 0 <= px_river < w:
        mask_local = (np.abs(y_coords - py_river) < 70) & (np.abs(x_coords - px_river) < 70)
        y_loc, x_loc = y_coords[mask_local], x_coords[mask_local]
        
        if len(y_loc) > 10:
            pts = np.vstack((x_loc, y_loc)).T
            mean = np.mean(pts, axis=0)
            cov = np.cov(pts - mean, rowvar=False)
            evals, evecs = np.linalg.eigh(cov)
            v = evecs[:, 1]
            nx, ny = -v[1], v[0]
            
            def cast_ray(dy, dx):
                py, px = float(py_river), float(px_river)
                gap_allowance = 2
                gap_count = 0
                for step in range(200):
                    py += dy
                    px += dx
                    ipy, ipx = int(round(py)), int(round(px))
                    if not (0 <= ipy < h and 0 <= ipx < w):
                        break
                    if not live_water_mask[ipy, ipx]:
                        gap_count += 1
                        if gap_count > gap_allowance:
                            py -= dy * gap_count
                            px -= dx * gap_count
                            break
                    else:
                        gap_count = 0
                return int(round(py)), int(round(px))
                
            pyA, pxA = cast_ray(ny, nx)
            pyB, pxB = cast_ray(-ny, -nx)
            
            bankA_lat, bankA_lon = px_to_ll(pyA, pxA)
            bankB_lat, bankB_lon = px_to_ll(pyB, pxB)
            measured_width_m = round(haversine(bankA_lat, bankA_lon, bankB_lat, bankB_lon), 1)

    water_polygon = []
    if np.any(live_water_mask):
        contours, _ = cv2.findContours(live_water_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest_contour = max(contours, key=cv2.contourArea)
            epsilon = 0.005 * cv2.arcLength(largest_contour, True)
            approx = cv2.approxPolyDP(largest_contour, epsilon, True)
            for pt in approx:
                px, py = pt[0]
                plat, plon = px_to_ll(py, px)
                water_polygon.append([float(plat), float(plon)])
                
    # --- Base64 Image Generation ---
    # Create False Color Composite (NIR, Red, Green)
    def normalize_band(b, max_val=4000):
        return np.clip(b / max_val * 255.0, 0, 255).astype(np.uint8)
        
    img_bgr = np.zeros((h, w, 3), dtype=np.uint8)
    img_bgr[:, :, 0] = normalize_band(green) # B -> Green
    img_bgr[:, :, 1] = normalize_band(red)   # G -> Red
    img_bgr[:, :, 2] = normalize_band(nir)   # R -> NIR (so vegetation is red)
    
    # Overlay Water Boundary in Cyan (0, 245, 212 in BGR -> 212, 245, 0)
    if np.any(live_water_mask):
        contours, _ = cv2.findContours(live_water_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img_bgr, contours, -1, (212, 245, 0), 2)
        
    # Overlay Width Transect in Yellow (0, 215, 255 in BGR)
    if measured_width_m > 0:
        cv2.line(img_bgr, (int(pxA), int(pyA)), (int(pxB), int(pyB)), (0, 215, 255), 2)
        
    # Overlay Shoreline vector in Magenta/Pink (94, 63, 244 in BGR)
    if 0 <= py_click < h and 0 <= px_click < w:
        cv2.circle(img_bgr, (int(px_click), int(py_click)), 4, (94, 63, 244), -1)
        if exact_shoreline_dist_m > 0 and 0 <= py_shore < h and 0 <= px_shore < w:
            cv2.line(img_bgr, (int(px_click), int(py_click)), (int(px_shore), int(py_shore)), (94, 63, 244), 2)
            cv2.circle(img_bgr, (int(px_shore), int(py_shore)), 4, (94, 63, 244), -1)
            
    success, encoded_img = cv2.imencode('.png', img_bgr)
    vision_preview_b64 = f"data:image/png;base64,{base64.b64encode(encoded_img).decode('utf-8')}" if success else None

    # Guarantee PC Tile URLs
    url_base = f"https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}@1x?collection=sentinel-2-l2a&item={pc_item_id}"
    true_color_url = f"{url_base}&assets=visual"
    false_color_url = f"{url_base}&assets=B08&assets=B04&assets=B03&color_formula=Gamma+RGB+3.2+Saturation+1.5+Sigmoidal+RGB+15+0.35"
    
    stac_data = {
        "satellite_sensor": "Sentinel-2 L2A / Sentinel-1 SAR",
        "latest_scene_date": pc_date,
        "cloud_cover_pct": round(local_cloud_pct, 1),
        "detection_mode": detection_mode,
        "measured_river_width_m": float(measured_width_m),
        "segmentation_resolution_m": 10.0,
        "bank_transect": [[float(bankA_lat), float(bankA_lon)], [float(bankB_lat), float(bankB_lon)]],
        "shoreline_vector": [[float(lat), float(lon)], [float(shoreline_lat), float(shoreline_lon)]],
        "water_polygon": water_polygon,
        "live_true_color_url": true_color_url,
        "live_false_color_url": false_color_url,
        "vision_preview_b64": vision_preview_b64
    }
    
    return {
        "is_water_body_image": bool(is_water_body_image),
        "exact_shoreline_dist_m": float(exact_shoreline_dist_m),
        "shoreline_lat": float(shoreline_lat),
        "shoreline_lon": float(shoreline_lon),
        "measured_width_m": float(measured_width_m),
        "satellite_pass_info": stac_data
    }
