import json
import os
import sqlite3

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
seed_nodes_path = os.path.join(base_dir, 'seed_nodes.json')
db_path = os.path.join(base_dir, 'data', 'krishna_hydro.db')

with open(seed_nodes_path, 'r', encoding='utf-8') as f:
    nodes = json.load(f)

cwc_benchmarks = {
    "KARAD": {"cwc_warning_level_m": 564.5, "cwc_danger_level_m": 566.0, "cwc_hfl_m": 569.1},
    "SANGLI": {"cwc_warning_level_m": 536.5, "cwc_danger_level_m": 537.8, "cwc_hfl_m": 541.45},
    "KURUNDWAD": {"cwc_warning_level_m": 532.5, "cwc_danger_level_m": 534.0, "cwc_hfl_m": 538.12},
    "HIPPARAGI": {"frl_m": 524.87, "max_spillway_cusecs": 450000},
    "HIDKAL": {"frl_m": 662.94, "mwl_m": 663.55, "max_spillway_cusecs": 118000},
    "DHUPADAL_WEIR": {"frl_m": 631.65, "max_spillway_cusecs": 110000},
    "Lolasura": {"cwc_warning_level_m": 551.5, "cwc_danger_level_m": 553.0, "cwc_hfl_m": 556.8},
    "MALAPRABHA_DAM": {"frl_m": 633.83, "max_spillway_cusecs": 185000},
    "ALMATTI": {"frl_m": 519.60, "mwl_m": 521.0, "max_spillway_cusecs": 1095000},
    "NARAYANPUR": {"frl_m": 492.25, "mwl_m": 493.5, "max_spillway_cusecs": 1340000},
    "KOYNA": {"frl_m": 657.90, "max_spillway_cusecs": 250000},
    "WARANA": {"frl_m": 626.90, "max_spillway_cusecs": 120000},
    "UJJANI": {"frl_m": 496.83, "mwl_m": 497.58, "max_spillway_cusecs": 550000},
    "YADGIR": {"cwc_warning_level_m": 364.5, "cwc_danger_level_m": 366.0, "cwc_hfl_m": 369.2},
    "BHADRA_DAM": {"frl_m": 657.76, "max_spillway_cusecs": 135000},
    "TUNGABHADRA": {"frl_m": 497.74, "max_spillway_cusecs": 650000},
    "SUNKESULA_BARRAGE": {"frl_m": 292.0, "max_spillway_cusecs": 600000},
    "JURALA": {"frl_m": 318.52, "max_spillway_cusecs": 1250000},
    "SRISAILAM": {"frl_m": 269.75, "mwl_m": 271.88, "max_spillway_cusecs": 1320000},
    "NAGARJUNA": {"frl_m": 179.83, "mwl_m": 181.05, "max_spillway_cusecs": 1250000},
    "PULICHINTALA": {"frl_m": 53.34, "max_spillway_cusecs": 1400000},
    "PRAKASAM": {"frl_m": 17.39, "cwc_warning_level_m": 16.0, "cwc_danger_level_m": 17.39, "cwc_hfl_m": 20.65, "max_spillway_cusecs": 1190000}
}

for n in nodes:
    if n['node_id'] == 'INGALI_CONFLUENCE':
        n['lat'] = 16.17725
        n['lon'] = 74.68866
        
    # Match by ID or Name
    b = cwc_benchmarks.get(n['node_id'])
    if not b:
        b = cwc_benchmarks.get(n.get('station_name'))
    
    if b:
        for k, v in b.items():
            n[k] = v

with open(seed_nodes_path, 'w', encoding='utf-8') as f:
    json.dump(nodes, f, indent=2)

try:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE routing_nodes SET lat = ?, lon = ? WHERE node_id = ?", (16.17725, 74.68866, 'INGALI_CONFLUENCE'))
    
    # Optional: add columns if they don't exist
    for col in ['cwc_warning_level_m', 'cwc_danger_level_m', 'cwc_hfl_m', 'mwl_m', 'max_spillway_cusecs']:
        try:
            c.execute(f"ALTER TABLE routing_nodes ADD COLUMN {col} REAL")
        except:
            pass
            
    for n in nodes:
        b = cwc_benchmarks.get(n['node_id']) or cwc_benchmarks.get(n.get('station_name'))
        if b:
            set_clauses = []
            vals = []
            for k, v in b.items():
                set_clauses.append(f"{k} = ?")
                vals.append(v)
            if set_clauses:
                vals.append(n['node_id'])
                c.execute(f"UPDATE routing_nodes SET {', '.join(set_clauses)} WHERE node_id = ?", vals)
                
    conn.commit()
    conn.close()
    print("Database updated!")
except Exception as e:
    print("DB error:", e)

print("seed_nodes.json updated!")
