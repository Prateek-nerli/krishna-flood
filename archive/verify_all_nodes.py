import json
import os
import csv
import math
import time
import requests
import sqlite3
import numpy as np
import networkx as nx
from raster_lookup import get_true_elevation, project_point_on_segment, haversine
import urllib3
urllib3.disable_warnings()

GROUND_TRUTH = {
    'RAKASKOP': (15.8109451, 74.3706113),
    'KAKATI_BEND': (15.8991000, 74.4999800),
    'SHIRUR_MARKANDEYA': (16.0158421, 74.6383748),
    'HIDKAL': (16.1424000, 74.6447000),
    'DHUPADAL_WEIR': (16.2112000, 74.7598000),
    'GOKAK_CONFLUENCE': (16.1755000, 74.8239000),
    'LOLASURA_BRIDGE': (16.1801000, 74.8328000),
    'MALAPRABHA_DAM': (15.8214000, 75.1064000),
    'KALAMMAWADI': (16.3213890, 73.9652780),
    'RADHANAGARI': (16.4055000, 73.9600000),
    'HIPPARAGI': (16.5527780, 75.1658330),
    'ALMATTI': (16.3279500, 75.8859100),
    'NARAYANPUR': (16.1781000, 76.2231000),
    'JURALA': (16.3326100, 77.7040700),
    'SRISAILAM': (16.0869000, 78.8972000),
    'NAGARJUNA': (16.5761000, 79.3124000),
    'PULICHINTALA': (16.7705560, 80.0591670),
    'PRAKASAM': (16.5062000, 80.6047000),
}

def get_osm_snap(lat, lon, node_type):
    if node_type in ['RESERVOIR', 'BARRAGE']:
        query = f'[out:json][timeout:12];(way["waterway"~"dam|weir"](around:4500,{lat},{lon});way["natural"="water"](around:4500,{lat},{lon}););out geom;'
    else:
        query = f'[out:json][timeout:12];(way["waterway"~"^(river|riverbank)$"](around:4500,{lat},{lon}););out geom;'
    
    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.post(url, data={'data': query}, verify=False, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            min_dist = float('inf')
            best_lat, best_lon = lat, lon
            for el in data.get('elements', []):
                if 'geometry' in el:
                    geom = el['geometry']
                    for i in range(len(geom)-1):
                        _, p_lat, p_lon = project_point_on_segment(lat, lon, geom[i]['lat'], geom[i]['lon'], geom[i+1]['lat'], geom[i+1]['lon'])
                        d = haversine(lat, lon, p_lat, p_lon)
                        if d < min_dist:
                            min_dist = d
                            best_lat, best_lon = p_lat, p_lon
            if min_dist < 4500:
                return best_lat, best_lon
    except Exception as e:
        pass
    return lat, lon

def build_dense_points(nodes):
    print("Rebuilding data/krishna_dense_points.npz...")
    node_map = {n['node_id']: n for n in nodes}
    dense_pts = []
    dense_data = []
    
    for i, n in enumerate(nodes):
        dn_id = n.get('downstream_node')
        if not dn_id or dn_id not in node_map:
            continue
        A = n
        B = node_map[dn_id]
        
        min_lat, max_lat = min(A['lat'], B['lat']) - 0.02, max(A['lat'], B['lat']) + 0.02
        min_lon, max_lon = min(A['lon'], B['lon']) - 0.02, max(A['lon'], B['lon']) + 0.02
        
        query = f'[out:json][timeout:10];way["waterway"~"^(river|stream|canal|riverbank)$"]({min_lat},{min_lon},{max_lat},{max_lon});out geom;'
        
        path_coords = []
        try:
            resp = requests.post("https://overpass-api.de/api/interpreter", data={'data': query}, verify=False, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                G = nx.Graph()
                for el in data.get('elements', []):
                    if 'geometry' in el:
                        geom = el['geometry']
                        for j in range(len(geom)-1):
                            p1 = (geom[j]['lat'], geom[j]['lon'])
                            p2 = (geom[j+1]['lat'], geom[j+1]['lon'])
                            d = haversine(p1[0], p1[1], p2[0], p2[1])
                            G.add_edge(p1, p2, weight=d)
                            
                if len(G.nodes) > 0:
                    nodes_list = list(G.nodes)
                    a_dists = [haversine(A['lat'], A['lon'], p[0], p[1]) for p in nodes_list]
                    b_dists = [haversine(B['lat'], B['lon'], p[0], p[1]) for p in nodes_list]
                    best_a = nodes_list[np.argmin(a_dists)]
                    best_b = nodes_list[np.argmin(b_dists)]
                    
                    if np.min(a_dists) < 5000 and np.min(b_dists) < 5000:
                        try:
                            path_coords = nx.shortest_path(G, best_a, best_b, weight='weight')
                        except nx.NetworkXNoPath:
                            pass
        except Exception:
            pass
            
        if len(path_coords) < 2:
            path_coords = [(A['lat'], A['lon']), (B['lat'], B['lon'])]
            
        total_dist = 0
        seg_dists = []
        for j in range(len(path_coords)-1):
            d = haversine(path_coords[j][0], path_coords[j][1], path_coords[j+1][0], path_coords[j+1][1])
            seg_dists.append(d)
            total_dist += d
            
        if total_dist == 0:
            continue
            
        steps = max(2, int(total_dist / 40.0))
        for step in range(steps):
            target_dist = (step / float(steps)) * total_dist
            cur_dist = 0
            for j, d in enumerate(seg_dists):
                if cur_dist + d >= target_dist or j == len(seg_dists)-1:
                    rem = target_dist - cur_dist
                    alpha = rem / d if d > 0 else 0
                    lat_i = path_coords[j][0] * (1-alpha) + path_coords[j+1][0] * alpha
                    lon_i = path_coords[j][1] * (1-alpha) + path_coords[j+1][1] * alpha
                    global_alpha = target_dist / total_dist if total_dist > 0 else 0
                    elev_i = A['bed_elev_m'] * (1-global_alpha) + B['bed_elev_m'] * global_alpha
                    
                    dense_pts.append([lat_i, lon_i])
                    dense_data.append([A['node_id'], B['node_id'], global_alpha, elev_i])
                    break
                cur_dist += d

    dense_pts = np.array(dense_pts)
    dense_data = np.array(dense_data)
    np.savez('data/krishna_dense_points.npz', points=dense_pts, data=dense_data)
    print(f"Saved {len(dense_pts)} dense points.")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(base_dir)
    
    with open('seed_nodes.json', 'r') as f:
        nodes = json.load(f)
    
    audit_rows = []
    
    try:
        db_conn = sqlite3.connect('data/krishna_hydro.db')
        cur = db_conn.cursor()
    except:
        cur = None
        db_conn = None
        
    for i, node in enumerate(nodes):
        nid = node['node_id']
        old_lat = node['lat']
        old_lon = node['lon']
        
        if nid in GROUND_TRUTH:
            v_lat, v_lon = GROUND_TRUTH[nid]
            print(f"[{i+1}/{len(nodes)}] {nid}: Using GROUND TRUTH -> {v_lat}, {v_lon}")
        else:
            v_lat, v_lon = get_osm_snap(old_lat, old_lon, node.get('node_type', 'GAUGE'))
            print(f"[{i+1}/{len(nodes)}] {nid}: Snapped OSM -> {v_lat}, {v_lon}")
            
        try:
            river_srtm_elv = get_true_elevation(v_lat, v_lon)
            if river_srtm_elv is None or river_srtm_elv == -9999:
                river_srtm_elv = node.get('bed_elev_m', 500) + 1.5
        except:
            river_srtm_elv = node.get('bed_elev_m', 500) + 1.5
            
        new_elev = round(river_srtm_elv - 1.5, 2)
        shift_m = haversine(old_lat, old_lon, v_lat, v_lon)
        
        node['lat'] = v_lat
        node['lon'] = v_lon
        node['bed_elev_m'] = new_elev
        
        audit_rows.append({
            'node_id': nid,
            'station_name': node.get('station_name', ''),
            'node_role': node.get('node_role', ''),
            'old_lat': old_lat,
            'old_lon': old_lon,
            'verified_lat': v_lat,
            'verified_lon': v_lon,
            'shift_meters': round(shift_m, 2),
            'bed_elev_m': new_elev,
            'downstream_node': node.get('downstream_node', ''),
            'google_maps_url': f"https://maps.google.com/?q={v_lat},{v_lon}"
        })
        
        if cur:
            try:
                cur.execute("UPDATE hydro_nodes SET lat=?, lon=?, bed_elev_m=?, geom=ST_SetSRID(ST_MakePoint(?, ?), 4326) WHERE node_id=?", 
                            (v_lat, v_lon, new_elev, v_lon, v_lat, nid))
            except:
                try:
                    cur.execute("UPDATE nodes SET lat=?, lon=?, bed_elev_m=? WHERE node_id=?", 
                                (v_lat, v_lon, new_elev, nid))
                except:
                    pass

    if db_conn:
        db_conn.commit()
        db_conn.close()
    
    with open('seed_nodes.json', 'w') as f:
        json.dump(nodes, f, indent=2)
        
    csv_file = 'data/krishna_nodes_verified_audit.csv'
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=audit_rows[0].keys())
        writer.writeheader()
        writer.writerows(audit_rows)
        
    build_dense_points(nodes)
    
    print("\n--- START CSV AUDIT TABLE ---")
    with open(csv_file, 'r') as f:
        print(f.read())
    print("--- END CSV AUDIT TABLE ---\n")

if __name__ == '__main__':
    main()
