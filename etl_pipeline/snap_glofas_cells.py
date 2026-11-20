import os
import json
import time
import math
import sqlite3
import requests
import pandas as pd
import numpy as np

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

# Dictionary of verified optimal GloFAS mainstem/channel cells
# Identified via 3x3 grid search during August 2019 peak flood
SNAPPED_CELLS = {
    "KOYNA": (17.3511, 73.8022),
    "WARANA": (17.0825, 73.8650),
    "KARAD": (17.2891, 74.1814),              # Already aligned
    "SANGLI": (16.8024, 74.5315),
    "RADHANAGARI": (16.4092, 73.9961),        # Already aligned
    "KURUNDWAD": (16.6850, 74.5892),          # Already aligned
    "HIPPARAGI": (16.5832, 75.2031),
    "ALMATTI": (16.3306, 75.8869),            # Already aligned
    "NARAYANPUR": (16.1781, 76.2231),         # Already aligned
    "UJJANI": (18.1242, 75.0703),
    "YADGIR": (16.7481, 77.0810),
    "JURALA": (16.3394, 77.7053),             # Already aligned
    "TUNGABHADRA": (15.2131, 76.2842),
    "SRISAILAM": (16.1369, 78.8972),
    "NAGARJUNA": (16.5761, 79.3124),          # Already aligned
    "PULICHINTALA": (16.7722, 80.0569),       # Already aligned
    "PRAKASAM": (16.4562, 80.5547),
    "RAKASKOP": (15.8280, 74.4050),           # Already aligned
    "SHIRUR_MARKANDEYA": (16.0333, 74.6417),  # Already aligned
    "HIDKAL": (16.1936, 74.6419),
    "HIRANYAKESHI": (16.2050, 74.5350),
    "GOKAK_CONFLUENCE": (16.1755, 74.8239),   # Already aligned
    "MUDHOL_GHATAPRABHA": (16.2780, 75.3350),
    "KALAMMAWADI": (16.3650, 74.0650),
    "KALLOL_CHIKODI": (16.5820, 74.5240),     # Already aligned
    "MALAPRABHA_DAM": (15.8714, 75.1064),
    "BADAMI_MALAPRABHA": (15.9350, 75.7120)
}

def snap_and_update_all():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_nodes_path = os.path.join(base_dir, "seed_nodes.json")
    pq_path = os.path.join(base_dir, "data", "historical_backfill_1995_2026.parquet")
    db_path = os.path.join(base_dir, "data", "krishna_hydro.db")

    with open(seed_nodes_path, "r", encoding="utf-8") as f:
        nodes = json.load(f)

    df_pq = pd.read_parquet(pq_path)
    df_pq["time"] = pd.to_datetime(df_pq["time"])

    print("=" * 86)
    print("STEP 2A: GLOFAS 0.05° MAINSTEM GRID CELL ALIGNMENT AUDIT & SYNC")
    print("=" * 86)

    # 1. Update seed_nodes.json with glofas_lat and glofas_lon
    changed_nodes = []
    print(f"{'Station ID':20s} | {'Original (Lat, Lon)':22s} | {'GloFAS (Lat, Lon)':22s} | {'Shift (km)':10s} | {'Status':10s}")
    print("-" * 92)

    for node in nodes:
        nid = node["node_id"]
        orig_lat = node["lat"]
        orig_lon = node["lon"]

        g_lat, g_lon = SNAPPED_CELLS.get(nid, (orig_lat, orig_lon))
        node["glofas_lat"] = round(g_lat, 4)
        node["glofas_lon"] = round(g_lon, 4)

        shift = haversine_km(orig_lat, orig_lon, g_lat, g_lon)
        is_changed = (shift > 0.05)
        status = "SNAPPED" if is_changed else "ALIGNED"
        if is_changed:
            changed_nodes.append((nid, orig_lat, orig_lon, g_lat, g_lon, shift))

        print(f"{nid:20s} | {orig_lat:7.4f}, {orig_lon:7.4f}         | {g_lat:7.4f}, {g_lon:7.4f}         | {shift:7.2f} km | {status:10s}")

    with open(seed_nodes_path, "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=2)
    print(f"\nSaved updated seed_nodes.json ({len(changed_nodes)} stations snapped to river cells).")

    # 2. Re-fetch daily historical discharge for all changed stations
    url = "https://flood-api.open-meteo.com/v1/flood"
    before_after_records = []

    print(f"\n[Re-fetching historical daily discharge from Open-Meteo Flood API for {len(changed_nodes)} snapped stations]...")
    for nid, orig_lat, orig_lon, g_lat, g_lon, shift in changed_nodes:
        old_peak = df_pq[df_pq["node_id"] == nid]["discharge_cumecs"].max()

        params = {
            "latitude": g_lat,
            "longitude": g_lon,
            "start_date": "1997-01-01",
            "end_date": "2026-09-25",
            "daily": "river_discharge"
        }

        success = False
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, verify=True, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    dis_arr = data.get("daily", {}).get("river_discharge", [])
                    if len(dis_arr) == 10860:
                        success = True
                        break
                elif r.status_code == 429:
                    time.sleep(3.0)
            except Exception:
                time.sleep(1.5)

        if success:
            new_peak = max(dis_arr)
            # Update in parquet
            mask = df_pq["node_id"] == nid
            df_pq.loc[mask, "discharge_cumecs"] = dis_arr
            before_after_records.append((nid, old_peak, new_peak, shift))
            print(f"  ✓ {nid:20s} -> Old Peak: {old_peak:8.1f} m³/s | New Peak: {new_peak:8.1f} m³/s (Shift: {shift:.2f} km)")
        else:
            print(f"  ✗ Failed to fetch for {nid}")

        time.sleep(0.4)

    # 3. Save updated Parquet
    df_pq.to_parquet(pq_path, index=False)
    print(f"\nUpdated {pq_path} successfully ({len(df_pq)} rows).")

    # 4. Update SQLite database
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print(f"Updating SQLite table daily_hydro in {db_path}...")
    for nid, _, _, _ in before_after_records:
        sub = df_pq[df_pq["node_id"] == nid][["time", "discharge_cumecs"]]
        for _, row in sub.iterrows():
            t_str = row["time"].strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                UPDATE daily_hydro
                SET discharge_cumecs = ?
                WHERE node_id = ? AND time = ?
            """, (float(row["discharge_cumecs"]), nid, t_str))
    conn.commit()
    conn.close()
    print("Updated SQLite database successfully.")

    # 5. Print Before/After Summary Table
    print("\n" + "=" * 78)
    print("GLOFAS GRID SNAPPING BEFORE / AFTER AUDIT TABLE")
    print("=" * 78)
    print(f"{'Station ID':20s} | {'Old Peak (m³/s)':16s} | {'New Peak (m³/s)':16s} | {'Grid Shift (km)':16s}")
    print("-" * 78)
    for nid, old_p, new_p, shift in before_after_records:
        print(f"{nid:20s} | {old_p:16.1f} | {new_p:16.1f} | {shift:13.2f} km")
    print("=" * 78)

if __name__ == "__main__":
    snap_and_update_all()
