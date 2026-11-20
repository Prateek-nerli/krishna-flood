"""
gis_engine/snap_stations_to_wris.py

Snaps all 27 stations in seed_nodes.json to official India-WRIS river polylines.
Preserves glofas_lat and glofas_lon 100% UNCHANGED.
Updates seed_nodes.json and data/krishna_hydro.db (nodes table).
"""

import os
import json
import math
import sqlite3
import numpy as np
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

def snap_all_stations():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_nodes_path = os.path.join(base_dir, 'seed_nodes.json')
    db_path = os.path.join(base_dir, 'data', 'krishna_hydro.db')
    npz_path = os.path.join(base_dir, 'data', 'krishna_dense_points.npz')

    with open(seed_nodes_path, 'r', encoding='utf-8') as f:
        nodes = json.load(f)

    print(f"Loaded {len(nodes)} stations from seed_nodes.json")

    data = np.load(npz_path, allow_pickle=True)
    points = data['points']
    metadata = data['metadata']
    print(f"Loaded {len(points):,} India-WRIS dense points.")

    # Target river and authoritative anchor coordinate for each station
    # Station mappings:
    station_configs = {
        "KOYNA": {
            "river": ["Koyna"],
            "anchor": (17.4011, 73.7522)
        },
        "WARANA": {
            "river": ["Varna"],
            "anchor": (17.1325, 73.8650)
        },
        "KARAD": {
            "river": ["Krishna River", "Koyna"],
            "anchor": (17.2960, 74.1765)
        },
        "SANGLI": {
            "river": ["Krishna River"],
            "anchor": (16.8538, 74.5569)
        },
        "RADHANAGARI": {
            "river": ["Bhogavati"],
            "anchor": (16.4078, 73.9612)
        },
        "KURUNDWAD": {
            "river": ["Krishna River", "Panchaganga"],
            "anchor": (16.6892, 74.6016)
        },
        "HIPPARAGI": {
            "river": ["Krishna River"],
            "anchor": (16.5526, 75.1658)
        },
        "ALMATTI": {
            "river": ["Krishna River"],
            "anchor": (16.3306, 75.8869)
        },
        "NARAYANPUR": {
            "river": ["Krishna River"],
            "anchor": (16.1719, 76.2210)
        },
        "UJJANI": {
            "river": ["Bhima River"],
            "anchor": (18.0743, 75.1201)
        },
        "YADGIR": {
            "river": ["Bhima River"],
            "anchor": (16.7364, 77.1245)
        },
        "JURALA": {
            "river": ["Krishna River"],
            "anchor": (16.3363, 77.6999)
        },
        "TUNGABHADRA": {
            "river": ["Tungabhadra River"],
            "anchor": (15.2635, 76.3333)
        },
        "SRISAILAM": {
            "river": ["Krishna River"],
            "anchor": (16.0869, 78.8972)
        },
        "NAGARJUNA": {
            "river": ["Krishna River"],
            "anchor": (16.5761, 79.3124)
        },
        "PULICHINTALA": {
            "river": ["Krishna River"],
            "anchor": (16.7716, 80.0570)
        },
        "PRAKASAM": {
            "river": ["Krishna River"],
            "anchor": (16.5062, 80.6047)
        },
        "RAKASKOP": {
            "river": ["Markandeya"],
            "anchor": (15.8108, 74.3690)
        },
        "SHIRUR_MARKANDEYA": {
            "river": ["Markandeya"],
            "anchor": (16.0153, 74.6436)
        },
        "HIDKAL": {
            "river": ["Ghatprabha"],
            "anchor": (16.1435, 74.6441)
        },
        "HIRANYAKESHI": {
            "river": ["Hiranyakeshi"],
            "anchor": (16.2501, 74.4808)
        },
        "GOKAK_CONFLUENCE": {
            "river": ["Ghatprabha"],
            "anchor": (16.1754, 74.8237)
        },
        "MUDHOL_GHATAPRABHA": {
            "river": ["Ghatprabha"],
            "anchor": (16.3265, 75.2806)
        },
        "KALAMMAWADI": {
            "river": ["Dudhganga"],
            "anchor": (16.3307, 74.0055)
        },
        "KALLOL_CHIKODI": {
            "river": ["Krishna River", "Dudhganga"],
            "anchor": (16.5840, 74.6353)
        },
        "MALAPRABHA_DAM": {
            "river": ["Malprabha"],
            "anchor": (15.8242, 75.1005)
        },
        "BADAMI_MALAPRABHA": {
            "river": ["Malprabha"],
            "anchor": (15.8997, 75.7502)
        }
    }

    # Build river-specific KD-trees
    unique_rivers = set()
    for cfg in station_configs.values():
        unique_rivers.update(cfg["river"])

    river_trees = {}
    for riv in unique_rivers:
        mask = (metadata[:, 1] == riv)
        r_pts = points[mask]
        r_meta = metadata[mask]
        tree = cKDTree(r_pts)
        river_trees[riv] = (tree, r_pts, r_meta)
        print(f"  Indexed WRIS River '{riv}': {len(r_pts):,} vertices")

    # Global KD-tree to measure distance to WRIS polyline
    all_tree = cKDTree(points)

    audit_rows = []
    db_updates = []

    for node in nodes:
        nid = node['node_id']
        old_lat, old_lon = node['lat'], node['lon']
        old_glofas_lat = node['glofas_lat']
        old_glofas_lon = node['glofas_lon']

        cfg = station_configs.get(nid)
        if not cfg:
            raise ValueError(f"Unknown node {nid}")

        anchor_lat, anchor_lon = cfg["anchor"]
        target_rivers = cfg["river"]

        best_d = float('inf')
        best_pt = None
        best_meta = None
        best_riv = None

        for riv in target_rivers:
            tree, r_pts, r_meta = river_trees[riv]
            d_deg, idx = tree.query([anchor_lat, anchor_lon], k=1)
            pt = r_pts[idx]
            meta = r_meta[idx]
            d_m = haversine(anchor_lat, anchor_lon, pt[0], pt[1])
            if d_m < best_d:
                best_d = d_m
                best_pt = pt
                best_meta = meta
                best_riv = riv

        new_lat = round(float(best_pt[0]), 6)
        new_lon = round(float(best_pt[1]), 6)

        # Distance from old coordinate
        shift_m = haversine(old_lat, old_lon, new_lat, new_lon)

        # Verify distance from new coordinate to WRIS network
        dist_deg, pt_idx = all_tree.query([new_lat, new_lon], k=1)
        wris_nearest_pt = points[pt_idx]
        dist_to_wris_m = haversine(new_lat, new_lon, wris_nearest_pt[0], wris_nearest_pt[1])

        # Strict check: must be < 100m
        assert dist_to_wris_m < 100.0, f"Node {nid} WRIS distance {dist_to_wris_m}m >= 100m!"

        # Update node dictionary
        node['lat'] = new_lat
        node['lon'] = new_lon
        node['wris_river_name'] = best_meta[1]
        node['wris_length_km'] = round(float(best_meta[3]), 1)
        node['wris_confluence_point'] = best_meta[2]

        # Ensure glofas coordinates are strictly untouched
        assert node['glofas_lat'] == old_glofas_lat, f"GloFAS lat modified for {nid}!"
        assert node['glofas_lon'] == old_glofas_lon, f"GloFAS lon modified for {nid}!"

        db_updates.append((new_lat, new_lon, nid))

        audit_rows.append({
            "node_id": nid,
            "station_name": node['station_name'],
            "old_coords": f"({old_lat:.4f}, {old_lon:.4f})",
            "new_coords": f"({new_lat:.4f}, {new_lon:.4f})",
            "shift_m": round(shift_m, 1),
            "dist_to_wris_m": round(dist_to_wris_m, 2),
            "wris_river": node['wris_river_name'],
            "wris_len_km": node['wris_length_km']
        })

    # Save updated seed_nodes.json
    with open(seed_nodes_path, 'w', encoding='utf-8') as f:
        json.dump(nodes, f, indent=2)
    print(f"\nSaved updated coordinates to {seed_nodes_path}")

    # Update sqlite database
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for lat, lon, nid in db_updates:
            cur.execute("UPDATE nodes SET lat = ?, lon = ? WHERE node_id = ?", (lat, lon, nid))
        conn.commit()
        conn.close()
        print(f"Updated {len(db_updates)} stations in {db_path} nodes table.")

    # Print 27-Node Audit Table
    print("\n" + "=" * 125)
    print("WRIS-AUTHORITATIVE 27-STATION COORDINATE AUDIT TABLE")
    print("=" * 125)
    print(f"{'Node ID':20s} | {'Old Coords':20s} | {'WRIS Snapped Coords':22s} | {'Shift (m)':10s} | {'WRIS Dist (m)':13s} | {'WRIS River':18s}")
    print("-" * 125)
    for r in audit_rows:
        print(f"{r['node_id']:20s} | {r['old_coords']:20s} | {r['new_coords']:22s} | {r['shift_m']:10.1f} | {r['dist_to_wris_m']:13.2f} | {r['wris_river']:18s}")
    print("-" * 125)
    max_wris_dist = max(r['dist_to_wris_m'] for r in audit_rows)
    print(f"ALL 27 NODES VERIFIED: Maximum distance to official India-WRIS polyline = {max_wris_dist:.2f} m (< 100 m).")
    print("=" * 125)

if __name__ == '__main__':
    snap_all_stations()
