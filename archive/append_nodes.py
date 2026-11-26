import json

new_nodes = [
   {"node_id": "RAKASKOP", "station_name": "Rakaskop Dam (Markandeya River)", "node_type": "RESERVOIR", "lat": 15.8280, "lon": 74.4050, "bed_elev_m": 742.0, "danger_frl_m": 754.38, "gross_cap_tmc": 0.62, "downstream_node": "SHIRUR_MARKANDEYA"},
   {"node_id": "SHIRUR_MARKANDEYA", "station_name": "Markandeya Reservoir", "node_type": "RESERVOIR", "lat": 16.0333, "lon": 74.6417, "bed_elev_m": 642.0, "danger_frl_m": 668.00, "gross_cap_tmc": 3.69, "downstream_node": "GOKAK_CONFLUENCE"},
   {"node_id": "HIDKAL", "station_name": "Hidkal Dam", "node_type": "RESERVOIR", "lat": 16.1436, "lon": 74.6419, "bed_elev_m": 622.0, "danger_frl_m": 662.94, "gross_cap_tmc": 51.16, "downstream_node": "GOKAK_CONFLUENCE"},
   {"node_id": "HIRANYAKESHI", "station_name": "Hiranyakeshi River Gauge", "node_type": "GAUGE", "lat": 16.2550, "lon": 74.4850, "bed_elev_m": 605.0, "danger_frl_m": 616.50, "gross_cap_tmc": 0.0, "downstream_node": "GOKAK_CONFLUENCE"},
   {"node_id": "GOKAK_CONFLUENCE", "station_name": "Gokak Falls Confluence", "node_type": "CONFLUENCE", "lat": 16.1755, "lon": 74.8239, "bed_elev_m": 548.0, "danger_frl_m": 561.50, "gross_cap_tmc": 1.20, "downstream_node": "MUDHOL_GHATAPRABHA"},
   {"node_id": "MUDHOL_GHATAPRABHA", "station_name": "Mudhol Ghataprabha Gauge", "node_type": "GAUGE", "lat": 16.3280, "lon": 75.2850, "bed_elev_m": 512.0, "danger_frl_m": 523.50, "gross_cap_tmc": 0.0, "downstream_node": "ALMATTI"},
   {"node_id": "KALAMMAWADI", "station_name": "Doodhganga Dam (Kalammawadi)", "node_type": "RESERVOIR", "lat": 16.3650, "lon": 74.0150, "bed_elev_m": 595.0, "danger_frl_m": 646.10, "gross_cap_tmc": 25.40, "downstream_node": "KALLOL_CHIKODI"},
   {"node_id": "KALLOL_CHIKODI", "station_name": "Kallol-Yadur Confluence", "node_type": "CONFLUENCE", "lat": 16.5820, "lon": 74.5240, "bed_elev_m": 521.0, "danger_frl_m": 534.50, "gross_cap_tmc": 0.0, "downstream_node": "HIPPARAGI"},
   {"node_id": "MALAPRABHA_DAM", "station_name": "Malaprabha Dam", "node_type": "RESERVOIR", "lat": 15.8214, "lon": 75.1064, "bed_elev_m": 610.0, "danger_frl_m": 633.83, "gross_cap_tmc": 37.73, "downstream_node": "BADAMI_MALAPRABHA"},
   {"node_id": "BADAMI_MALAPRABHA", "station_name": "Malaprabha Gauge", "node_type": "GAUGE", "lat": 15.9850, "lon": 75.6620, "bed_elev_m": 515.0, "danger_frl_m": 526.00, "gross_cap_tmc": 0.0, "downstream_node": "NARAYANPUR"}
]

with open('seed_nodes.json', 'r') as f:
    data = json.load(f)

# add only if not exists
existing_ids = {d['node_id'] for d in data}
for n in new_nodes:
    if n['node_id'] not in existing_ids:
        data.append(n)

with open('seed_nodes.json', 'w') as f:
    json.dump(data, f, indent=2)

print("Added 10 new nodes.")
