import os
import json
import time
from datetime import datetime
import sqlite3
import pandas as pd
from etl_pipeline.open_meteo import get_historical_river_discharge, get_historical_catchment_data

def setup_sqlite(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_hydro (
            node_id TEXT,
            time TEXT,
            discharge_cumecs REAL,
            precipitation_sum REAL,
            rain_sum REAL,
            et0_fao_evapotranspiration REAL,
            PRIMARY KEY (node_id, time)
        )
    """)
    conn.commit()
    return conn, cur

def run_historical_backfill():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, 'krishna_hydro.db')
    parquet_path = os.path.join(data_dir, 'historical_backfill_1995_2026.parquet')

    conn, cur = setup_sqlite(db_path)

    try:
        with open(os.path.join(base_dir, 'seed_nodes.json'), 'r') as f:
            nodes = json.load(f)
    except FileNotFoundError:
        print("Error: seed_nodes.json not found.")
        return
        
    existing_df = None
    existing_nodes = set()
    if os.path.exists(parquet_path):
        existing_df = pd.read_parquet(parquet_path)
        existing_nodes = set(existing_df['node_id'].unique())
        print(f"Loaded existing Parquet with {len(existing_nodes)} nodes, {len(existing_df)} rows.")

    print(f"Loaded {len(nodes)} nodes from seed_nodes.json. Starting missing backfill...")

    all_data = []

    for node in nodes:
        node_id = node['node_id']
        lat = node['lat']
        lon = node['lon']
        
        if node_id in existing_nodes:
            print(f"Skipping {node_id}, already in parquet.")
            continue
            
        print(f"Fetching missing data for {node_id} ({lat}, {lon})...")
        max_retries = 5
        success = False
        
        for attempt in range(max_retries):
            try:
                # 1. Fetch Discharge
                discharge_data = get_historical_river_discharge(lat, lon, start_date="1995-01-01", end_date="2026-09-25")
                
                # 2. Fetch Catchment
                catchment_data = get_historical_catchment_data(lat, lon, start_date="1995-01-01", end_date="2026-09-25")
                
                if 'daily' not in discharge_data or 'daily' not in catchment_data:
                    print(f"  -> Skipping {node_id}: Missing daily variables.")
                    break
                    
                d_daily = discharge_data['daily']
                c_daily = catchment_data['daily']
                
                times = d_daily.get('time', [])
                discharges = d_daily.get('river_discharge', [])
                precip = c_daily.get('precipitation_sum', [])
                rain = c_daily.get('rain_sum', [])
                et0 = c_daily.get('et0_fao_evapotranspiration', [])
                
                records = []
                for i, t_str in enumerate(times):
                    q = discharges[i] if i < len(discharges) else None
                    p = precip[i] if i < len(precip) else None
                    r = rain[i] if i < len(rain) else None
                    e = et0[i] if i < len(et0) else None
                    
                    if q is not None:
                        dt = datetime.strptime(t_str, "%Y-%m-%d").strftime("%Y-%m-%d")
                        records.append((node_id, dt, q, p, r, e))
                        all_data.append({
                            'node_id': node_id,
                            'time': dt,
                            'discharge_cumecs': q,
                            'precipitation_sum': p,
                            'rain_sum': r,
                            'et0': e
                        })
                
                # Insert into SQLite
                insert_query = """
                    INSERT INTO daily_hydro (node_id, time, discharge_cumecs, precipitation_sum, rain_sum, et0_fao_evapotranspiration)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id, time) DO UPDATE SET
                    discharge_cumecs=excluded.discharge_cumecs,
                    precipitation_sum=excluded.precipitation_sum,
                    rain_sum=excluded.rain_sum,
                    et0_fao_evapotranspiration=excluded.et0_fao_evapotranspiration;
                """
                cur.executemany(insert_query, records)
                conn.commit()
                
                print(f"  -> Saved {len(records)} records for {node_id}.")
                time.sleep(5) # Pacing to avoid 429
                success = True
                break

            except Exception as e:
                print(f"  -> Failed attempt {attempt + 1} for {node_id}: {e}")
                if "429" in str(e) and attempt < max_retries - 1:
                    print("  -> Rate limit hit, sleeping 15s...")
                    time.sleep(15)
                else:
                    break
                    
        if not success:
            print(f"  -> Giving up on {node_id} after {max_retries} attempts.")

    # Save to Parquet
    new_df = pd.DataFrame(all_data)
    if not new_df.empty:
        new_df['time'] = pd.to_datetime(new_df['time'])
        if existing_df is not None:
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            final_df = new_df
        final_df.to_parquet(parquet_path, index=False)
        print(f"\nSuccessfully updated Parquet! Total nodes: {len(final_df['node_id'].unique())}, rows: {len(final_df)}")
    else:
        print("\nNo new data fetched. Parquet is up to date.")
        if existing_df is not None:
             print(f"Total nodes: {len(existing_nodes)}, rows: {len(existing_df)}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    run_historical_backfill()
