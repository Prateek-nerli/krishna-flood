import os
import json
import numpy as np
import pandas as pd
import torch
from ml_engine.model_tft_gat import ST_Attention_GAT, build_basin_adjacency

def generate_replays():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seed_nodes_path = os.path.join(base_dir, "seed_nodes.json")
    with open(seed_nodes_path, "r", encoding="utf-8") as f:
        nodes = json.load(f)
        
    node_ids = [n["node_id"] for n in nodes]
    N = len(node_ids)
    node_to_idx = {nid: i for i, nid in enumerate(node_ids)}
    
    # Adjacency
    adj = build_basin_adjacency(nodes, verbose=False)
    
    # Feature config
    feat_cfg_path = os.path.join(base_dir, "models", "feature_config.json")
    with open(feat_cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    means = np.array(cfg["means"], dtype=np.float32)
    scales = np.array(cfg["scales"], dtype=np.float32)
    q_mean = float(cfg.get("q_mean", means[0]))
    q_scale = float(cfg.get("q_scale", scales[0]))
    
    # Load model
    model = ST_Attention_GAT(num_nodes=N, input_dim=6, hidden_dim=32, seq_len=14, pred_horizon=3, use_gat=True, dropout=0.0)
    weights_path = os.path.join(base_dir, "models", "krishna_tft_gat.pt")
    state_dict = torch.load(weights_path, weights_only=False)
    if isinstance(state_dict, dict) and "model_state_dict" in state_dict:
        model.load_state_dict(state_dict["model_state_dict"])
    else:
        model.load_state_dict(state_dict)
    model.eval()
    
    # Load backfill
    pq_path = os.path.join(base_dir, "data", "historical_backfill_1995_2026.parquet")
    df = pd.read_parquet(pq_path)
    df["time"] = pd.to_datetime(df["time"])
    
    dates = df[df["node_id"] == "KOYNA"]["time"].sort_values().reset_index(drop=True)
    T_total = len(dates)
    
    feat_3d = np.zeros((T_total, N, 6), dtype=np.float32)
    raw_discharge = np.zeros((T_total, N), dtype=np.float32)
    doy = dates.dt.dayofyear.values
    sin_doy = np.sin(2.0 * np.pi * doy / 365.25).astype(np.float32)
    cos_doy = np.cos(2.0 * np.pi * doy / 365.25).astype(np.float32)
    
    for nid in node_ids:
        idx = node_to_idx[nid]
        sub = df[df["node_id"] == nid].sort_values("time")
        q = np.clip(sub["discharge_cumecs"].values, 0.0, None).astype(np.float32)
        raw_discharge[:, idx] = q
        
        log_q = np.log1p(q)
        p = sub["precipitation_sum"].values.astype(np.float32)
        p3d = pd.Series(p).rolling(window=3, min_periods=1).sum().values.astype(np.float32)
        et0 = sub["et0"].values.astype(np.float32)
        
        feat_3d[:, idx, 0] = log_q
        feat_3d[:, idx, 1] = p
        feat_3d[:, idx, 2] = p3d
        feat_3d[:, idx, 3] = et0
        feat_3d[:, idx, 4] = sin_doy
        feat_3d[:, idx, 5] = cos_doy

    # Focus on the peak 2024 monsoon: 2024-07-01 to 2024-09-30 (unseen test set!)
    monsoon_mask = (dates >= "2024-07-01") & (dates <= "2024-09-30")
    monsoon_indices = np.where(monsoon_mask)[0]
    
    target_stations = ["ALMATTI", "SANGLI", "KURUNDWAD", "NARAYANPUR", "SRISAILAM", "PRAKASAM", "GOKAK_CONFLUENCE", "KOYNA"]
    
    replays = {
        "metadata": {
            "window": "2024 Monsoon Flood (Unseen Test Set)",
            "start_date": "2024-07-01",
            "end_date": "2024-09-30",
            "models_compared": ["Observed (GloFAS Ground Truth)", "ST-Attention-GAT (T+1 / 24h)", "ST-Attention-GAT (T+3 / 72h)"]
        },
        "stations": {}
    }
    
    for st in target_stations:
        replays["stations"][st] = []

    # Run inference across monsoon window with true horizon-lag alignment:
    # Day t observation is paired with:
    # - pred_t1 (24h ahead): forecast made at window ending t-1 (horizon 0)
    # - pred_t3 (72h ahead): forecast made at window ending t-3 (horizon 2)
    pred_dict = {}
    with torch.no_grad():
        for t_end in range(monsoon_indices[0] - 3, monsoon_indices[-1] + 1):
            x_seq = feat_3d[t_end - 13 : t_end + 1] # [14, N, 6]
            x_scaled = (x_seq - means.reshape(1, 1, 6)) / scales.reshape(1, 1, 6)
            x_tensor = torch.tensor(x_scaled, dtype=torch.float32).permute(1, 0, 2).unsqueeze(0)
            preds_scaled = model(x_tensor, adj) # [1, N, 3]
            preds_log = preds_scaled.cpu().numpy()[0] * q_scale + q_mean
            pred_dict[t_end] = np.expm1(np.clip(preds_log, 0.0, 11.5))

    for t in monsoon_indices:
        d_str = dates.iloc[t].strftime("%Y-%m-%d")
        p_t1_arr = pred_dict[t - 1]
        p_t3_arr = pred_dict[t - 3]
        
        for st in target_stations:
            idx = node_to_idx[st]
            obs = float(raw_discharge[t, idx])
            p_t1 = float(p_t1_arr[idx, 0])
            p_t3 = float(p_t3_arr[idx, 2])
            
            replays["stations"][st].append({
                "date": d_str,
                "observed_discharge_cumecs": round(obs, 1),
                "pred_t1_cumecs": round(p_t1, 1),
                "pred_t3_cumecs": round(p_t3, 1),
                "observed_cusecs": round(obs * 35.3147, 0),
                "pred_t1_cusecs": round(p_t1 * 35.3147, 0),
                "pred_t3_cusecs": round(p_t3 * 35.3147, 0)
            })

    out_path = os.path.join(base_dir, "models", "test_hydrograph_replays.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(replays, f, indent=2)
    print(f"[Done] Generated test hydrograph replays for {len(target_stations)} stations to {out_path}.")

if __name__ == "__main__":
    generate_replays()
