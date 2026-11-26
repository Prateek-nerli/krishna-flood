import json
import psycopg2

def load_data():
    conn = psycopg2.connect(
        dbname="krishnaflow",
        user="postgres",
        password="password",
        host="localhost",
        port="5432"
    )
    cur = conn.cursor()

    with open('seed_nodes.json', 'r') as f:
        nodes = json.load(f)

    # Insert hydro_nodes
    for node in nodes:
        cur.execute("""
            INSERT INTO hydro_nodes (
                node_id, station_name, state, node_type, lat, lon, bed_elev_m, danger_frl_m, gross_cap_tmc, geom
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            ) ON CONFLICT (node_id) DO NOTHING;
        """, (
            node['node_id'], node['station_name'], node['state'], node['node_type'],
            node['lat'], node['lon'], node['bed_elev_m'], node['danger_frl_m'],
            node['gross_cap_tmc'], node['lon'], node['lat']
        ))

    # Insert river_reaches
    for node in nodes:
        if 'downstream_node' in node and node['downstream_node']:
            # Ensure downstream node exists (for BAY_BENGAL which isn't in nodes list but is downstream for PRAKASAM)
            cur.execute("""
                INSERT INTO hydro_nodes (node_id, station_name, geom)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(81.0, 16.0), 4326))
                ON CONFLICT (node_id) DO NOTHING;
            """, (node['downstream_node'], node['downstream_node'] + " (Auto-created)"))

            cur.execute("""
                INSERT INTO river_reaches (upstream_node, downstream_node, dist_km, base_lag_hr)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (upstream_node, downstream_node) DO NOTHING;
            """, (
                node['node_id'], node['downstream_node'], node['dist_km'], node['base_lag_hr']
            ))

    conn.commit()
    cur.close()
    conn.close()
    print("Seed data loaded successfully.")

if __name__ == "__main__":
    load_data()
