import json
import os
import sqlite3
import numpy as np
import math
from scipy.spatial import cKDTree

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

DAM_COORDS = {
    "RAKASKOP": (15.8109451, 74.3706113),
    "KAKATI_BEND": (15.89910, 74.49998),
    "SHIRUR_MARKANDEYA": (16.0158421, 74.6383748),
    "HIDKAL": (16.1424, 74.6447),
    "DHUPADAL_WEIR": (16.2112, 74.7598),
    "MALAPRABHA_DAM": (15.8214, 75.1064),
    "KALAMMAWADI": (16.321389, 73.965278),
    "RADHANAGARI": (16.4055, 73.9600),
    "HIPPARAGI": (16.552778, 75.165833),
    "ALMATTI": (16.32795, 75.88591),
    "NARAYANPUR": (16.1781, 76.2231),
    "JURALA": (16.33261, 77.70407),
    "SRISAILAM": (16.0869, 78.8972),
    "NAGARJUNA": (16.5761, 79.3124),
    "PULICHINTALA": (16.770556, 80.059167),
    "PRAKASAM": (16.5062, 80.6047)
}

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
nodes_file = os.path.join(base_dir, 'seed_nodes.json')
db_file = os.path.join(base_dir, 'data', 'krishna_hydro.db')
npz_file = os.path.join(base_dir, 'data', 'krishna_dense_points.npz')
paths_file = os.path.join(base_dir, 'data', 'wris_krishna_all_rivers_paths.json')

with open(nodes_file, 'r') as f:
    nodes = json.load(f)

print("Loading KDTree...")
data = np.load(npz_file, allow_pickle=True)
points = data['points']
metadata = data['metadata']
tree = cKDTree(points)

print("Loading WRIS Paths...")
with open(paths_file, 'r', encoding='utf-8') as f:
    raw_rivers = json.load(f)

confluence_map = {}
features = raw_rivers
if isinstance(raw_rivers, dict):
    features = raw_rivers.get("features", raw_rivers.get("rivers", raw_rivers))
if not isinstance(features, list):
    features = [features]

for f in features:
    attr = f.get("attributes", {})
    name = attr.get("rivname", "").upper()
    if not name: continue
    
    paths = f.get("geometry", {}).get("paths", [])
    if paths and paths[0]:
        last_pt = paths[0][-1] 
        confluence_map[name] = (last_pt[1], last_pt[0])

verification = []

print("Snapping nodes...")
for n in nodes:
    old_lat, old_lon = n['lat'], n['lon']
    
    is_overridden = False
    for d_name, d_coords in DAM_COORDS.items():
        if d_name in n['node_id']:
            n['lat'], n['lon'] = d_coords
            is_overridden = True
            break
    
    new_lat, new_lon = n['lat'], n['lon']
    
    if n['node_role'] == 'CONFLUENCE' and not is_overridden:
        found_conf = False
        for trib in ["KOYNA", "WARANA", "PANCHAGANGA", "DUDHGANGA", "HIRANYAKESHI", "MARKANDEYA", 
                     "GHATAPRABHA", "MALAPRABHA", "BENNIHALLA", "BHIMA", "SINA", "NIRA", "TUNGABHADRA", 
                     "VEDAVATI", "DINDI", "MUSI", "PALLERU", "MUNNERU"]:
            if trib in n['node_id'] and trib in confluence_map:
                n['lat'], n['lon'] = confluence_map[trib]
                new_lat, new_lon = n['lat'], n['lon']
                found_conf = True
                break
        
        if not found_conf:
            dist, idx = tree.query([new_lat, new_lon], k=1)
            new_lat, new_lon = points[idx]
            n['lat'], n['lon'] = new_lat, new_lon
    
    elif not is_overridden:
        dist, idx = tree.query([new_lat, new_lon], k=1)
        new_lat, new_lon = points[idx]
        n['lat'], n['lon'] = new_lat, new_lon
        
    dist, idx = tree.query([new_lat, new_lon], k=1)
    meta = metadata[idx]
    
    n['wris_river_name'] = meta[1]
    n['wris_confluence_point'] = meta[2]
    n['wris_length_km'] = float(meta[3] or 0)
    n['wris_st_tehsil'] = meta[4]
    n['wris_en_tehsil'] = meta[5]
    
    dist_m = haversine(new_lat, new_lon, points[idx][0], points[idx][1])
    verification.append({
        "node": n['node_id'],
        "old_lat": old_lat,
        "old_lon": old_lon,
        "new_lat": new_lat,
        "new_lon": new_lon,
        "dist_m": dist_m
    })

with open(nodes_file, 'w') as f:
    json.dump(nodes, f, indent=2)

print("Updating DB...")
conn = sqlite3.connect(db_file)
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, lat REAL, lon REAL, node_role TEXT, station_name TEXT)")
for n in nodes:
    c.execute("UPDATE nodes SET lat = ?, lon = ? WHERE node_id = ?", (n['lat'], n['lon'], n['node_id']))
    if c.rowcount == 0:
        c.execute("INSERT INTO nodes (node_id, lat, lon, node_role, station_name) VALUES (?, ?, ?, ?, ?)",
                  (n['node_id'], n['lat'], n['lon'], n['node_role'], n['station_name']))
conn.commit()
conn.close()

print("\n--- VERIFICATION AUDIT (15 Nodes) ---")
for v in verification[:15]:
    print(f"{v['node'][:20]:<20} | Old: {v['old_lat']:.5f}, {v['old_lon']:.5f} | New: {v['new_lat']:.5f}, {v['new_lon']:.5f} | Dist to WRIS: {v['dist_m']:.2f}m")

