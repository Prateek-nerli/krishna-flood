import os
import json
import time
import math
import pickle
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler

from ml_engine.model_tft_gat import ST_Attention_GAT, build_basin_adjacency

class HydroDataset(Dataset):
    """
    Lazy 14-day sliding window dataset.
    Holds [T_total, N, F] array in memory (~7 MB) and slices windows dynamically in __getitem__.
    """
    def __init__(self, data_scaled, targets_scaled, window_indices, seq_len=14, pred_horizon=3):
        self.data_scaled = torch.tensor(data_scaled, dtype=torch.float32)
        self.targets_scaled = torch.tensor(targets_scaled, dtype=torch.float32)
        self.window_indices = window_indices
        self.seq_len = seq_len
        self.pred_horizon = pred_horizon

    def __len__(self):
        return len(self.window_indices)

    def __getitem__(self, idx):
        t = self.window_indices[idx]
        # x shape: [N, seq_len, F]
        x = self.data_scaled[t - self.seq_len + 1 : t + 1].permute(1, 0, 2)
        # target shape: [N, pred_horizon]
        y = self.targets_scaled[t + 1 : t + self.pred_horizon + 1].permute(1, 0)
        return x, y

def compute_hydrological_metrics(y_true, y_pred, flood_thresholds):
    """
    Computes standard IEEE hydrological forecasting metrics:
    NSE, KGE, R², RMSE, MAE, and Flood-Event Classification Metrics (Precision, Recall, F1, CSI).
    y_true: [Samples, N] in physical units (cumecs)
    y_pred: [Samples, N] in physical units (cumecs)
    flood_thresholds: [N] 95th percentile training thresholds
    """
    y_true_flat = y_true.flatten()
    y_pred_flat = y_pred.flatten()

    # NSE (Nash-Sutcliffe Efficiency)
    ss_res = np.sum((y_true_flat - y_pred_flat) ** 2)
    ss_tot = np.sum((y_true_flat - np.mean(y_true_flat)) ** 2)
    nse = 1.0 - (ss_res / (ss_tot + 1e-8))

    # Pearson r and R²
    r_mat = np.corrcoef(y_true_flat, y_pred_flat)
    r = r_mat[0, 1] if not np.isnan(r_mat[0, 1]) else 0.0
    r2 = r ** 2

    # KGE (Kling-Gupta Efficiency)
    mean_true = np.mean(y_true_flat)
    mean_pred = np.mean(y_pred_flat)
    std_true = np.std(y_true_flat)
    std_pred = np.std(y_pred_flat)
    alpha = (std_pred + 1e-8) / (std_true + 1e-8)
    beta = (mean_pred + 1e-8) / (mean_true + 1e-8)
    kge = 1.0 - np.sqrt((r - 1.0)**2 + (alpha - 1.0)**2 + (beta - 1.0)**2)

    # RMSE and MAE
    rmse = np.sqrt(np.mean((y_true_flat - y_pred_flat) ** 2))
    mae = np.mean(np.abs(y_true_flat - y_pred_flat))

    # Flood-Event High-Flow Metrics (Threshold = P95 of training discharge per station)
    # y_true shape is [Samples, N]
    thresh_broadcast = flood_thresholds.reshape(1, -1)
    actual_flood = (y_true >= thresh_broadcast)
    pred_flood = (y_pred >= thresh_broadcast)

    tp = np.sum(actual_flood & pred_flood)
    fp = np.sum(~actual_flood & pred_flood)
    fn = np.sum(actual_flood & ~actual_flood.T if False else actual_flood & ~pred_flood)

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = float(2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    csi = float(tp / (tp + fp + fn)) if (tp + fp + fn) > 0 else 0.0

    return {
        "NSE": round(float(nse), 4),
        "KGE": round(float(kge), 4),
        "R2": round(float(r2), 4),
        "RMSE_cumecs": round(float(rmse), 2),
        "MAE_cumecs": round(float(mae), 2),
        "Flood_Precision": round(precision, 4),
        "Flood_Recall": round(recall, 4),
        "Flood_F1": round(f1, 4),
        "Flood_CSI": round(csi, 4)
    }

def train_epoch(model, dataloader, optimizer, adj, q_mean, q_scale, device):
    model.train()
    total_loss = 0.0
    for x, y in dataloader:
        x, y = x.to(device), y.to(device)
        optimizer.zero_grad()

        pred = model(x, adj)
        loss_log = F.smooth_l1_loss(pred, y)

        # Physics-informed physical scale MSE component
        pred_log = pred * q_scale + q_mean
        true_log = y * q_scale + q_mean
        pred_q = torch.expm1(torch.clamp(pred_log, min=0.0, max=11.5))
        true_q = torch.expm1(torch.clamp(true_log, min=0.0, max=11.5))

        loss_phys = F.mse_loss(pred_q / 1000.0, true_q / 1000.0)
        loss = loss_log + 0.25 * loss_phys

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        total_loss += loss.item() * len(x)

    return total_loss / len(dataloader.dataset)

def evaluate_loss(model, dataloader, adj, q_mean, q_scale, device):
    model.eval()
    total_loss = 0.0
    with torch.no_grad():
        for x, y in dataloader:
            x, y = x.to(device), y.to(device)
            pred = model(x, adj)
            loss_log = F.smooth_l1_loss(pred, y)

            pred_log = pred * q_scale + q_mean
            true_log = y * q_scale + q_mean
            pred_q = torch.expm1(torch.clamp(pred_log, min=0.0, max=11.5))
            true_q = torch.expm1(torch.clamp(true_log, min=0.0, max=11.5))

            loss_phys = F.mse_loss(pred_q / 1000.0, true_q / 1000.0)
            loss = loss_log + 0.25 * loss_phys
            total_loss += loss.item() * len(x)

    return total_loss / len(dataloader.dataset)

def predict_test_set(model, dataloader, adj, q_mean, q_scale, device):
    model.eval()
    preds_phys = []
    trues_phys = []
    with torch.no_grad():
        for x, y in dataloader:
            x = x.to(device)
            pred = model(x, adj)

            pred_log = pred.cpu().numpy() * q_scale + q_mean
            true_log = y.numpy() * q_scale + q_mean

            pred_q = np.expm1(np.clip(pred_log, 0.0, 11.5))
            true_q = np.expm1(np.clip(true_log, 0.0, 11.5))

            preds_phys.append(pred_q)
            trues_phys.append(true_q)

    # Concatenate along sample axis -> [Total_Test_Samples, N, 3]
    return np.concatenate(preds_phys, axis=0), np.concatenate(trues_phys, axis=0)

def main():
    # Set seeds and multi-threading for reproducibility and speed
    torch.manual_seed(42)
    np.random.seed(42)
    torch.set_num_threads(8)
    device = torch.device("cpu")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_dir = os.path.join(base_dir, "models")
    os.makedirs(model_dir, exist_ok=True)

    print("=" * 78)
    print("IEEE KRISHNAFLOW ML ARCHITECTURE, TOPOLOGY & ABLATION PIPELINE")
    print("=" * 78)

    # 1. Load seed nodes
    seed_nodes_path = os.path.join(base_dir, "seed_nodes.json")
    with open(seed_nodes_path, "r", encoding="utf-8") as f:
        nodes = json.load(f)

    node_ids = [n["node_id"] for n in nodes]
    N = len(node_ids)
    node_to_idx = {nid: i for i, nid in enumerate(node_ids)}
    print(f"\n[Step 1] Loaded {N} stations from seed_nodes.json.")

    # 2. Build River Directed Adjacency Matrix
    print("\n[Step 2] Building physically directed River DAG Adjacency Matrix...")
    adj = build_basin_adjacency(nodes, verbose=True)

    # 3. Load Parquet Dataset & Engineer 6 Features
    pq_path = os.path.join(base_dir, "data", "historical_backfill_1995_2026.parquet")
    print(f"\n[Step 3] Loading backfill dataset from {pq_path}...")
    t0_data = time.time()
    df = pd.read_parquet(pq_path)
    df["time"] = pd.to_datetime(df["time"])

    dates = df[df["node_id"] == "KOYNA"]["time"].sort_values().reset_index(drop=True)
    T_total = len(dates)
    print(f"Loaded {len(df)} rows across {T_total} consecutive calendar days in {time.time()-t0_data:.2f}s.")

    # Build 3D array [T_total, N, 6]
    feat_3d = np.zeros((T_total, N, 6), dtype=np.float32)
    doy = dates.dt.dayofyear.values
    sin_doy = np.sin(2.0 * np.pi * doy / 365.25).astype(np.float32)
    cos_doy = np.cos(2.0 * np.pi * doy / 365.25).astype(np.float32)

    raw_discharge_matrix = np.zeros((T_total, N), dtype=np.float32)

    for nid in node_ids:
        idx = node_to_idx[nid]
        sub = df[df["node_id"] == nid].sort_values("time")
        q = np.clip(sub["discharge_cumecs"].values, 0.0, None).astype(np.float32)
        raw_discharge_matrix[:, idx] = q

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

    feature_names = ["log1p_discharge", "precipitation_sum", "precip_3d_sum", "et0", "sin_doy", "cos_doy"]
    print(f"Engineered 6 Non-Redundant Features: {feature_names}")

    # 4. Strict Chronological Splits (ZERO Leakage)
    train_mask = dates <= "2020-12-31"
    val_mask = (dates >= "2021-01-01") & (dates <= "2023-12-31")
    test_mask = dates >= "2024-01-01"

    train_days_idx = np.where(train_mask)[0]
    val_days_idx = np.where(val_mask)[0]
    test_days_idx = np.where(test_mask)[0]

    n_train_days = len(train_days_idx)
    n_val_days = len(val_days_idx)
    n_test_days = len(test_days_idx)

    # Fit StandardScaler STRICTLY on Training Split
    print("\n[Step 4] Fitting StandardScaler STRICTLY on Training Split (1997-01-01 to 2020-12-31)...")
    train_slice = feat_3d[:n_train_days, :, :]
    train_flat = train_slice.reshape(-1, 6)

    scaler = StandardScaler()
    scaler.fit(train_flat)

    q_mean = float(scaler.mean_[0])
    q_scale = float(scaler.scale_[0])
    print(f"Scaler fitted on {len(train_flat)} station-days. Log-discharge: mean={q_mean:.4f}, scale={q_scale:.4f}")

    # Transform all data using train scaler
    feat_3d_flat = feat_3d.reshape(-1, 6)
    feat_3d_scaled = scaler.transform(feat_3d_flat).reshape(T_total, N, 6)
    target_scaled = feat_3d_scaled[:, :, 0] # scaled log-discharge target

    # Window slicing with seq_len=14, pred_horizon=3
    seq_len = 14
    pred_horizon = 3

    train_window_indices = list(range(seq_len - 1, n_train_days - pred_horizon))
    val_window_indices = list(range(n_train_days, n_train_days + n_val_days - pred_horizon))
    test_window_indices = list(range(n_train_days + n_val_days, T_total - pred_horizon))

    print(f"Chronological Dataset Windows:")
    print(f"  • Train: {dates.iloc[train_days_idx[0]].date()} to {dates.iloc[train_days_idx[-1]].date()} ({len(train_window_indices)} sequences)")
    print(f"  • Val:   {dates.iloc[val_days_idx[0]].date()} to {dates.iloc[val_days_idx[-1]].date()} ({len(val_window_indices)} sequences)")
    print(f"  • Test:  {dates.iloc[test_days_idx[0]].date()} to {dates.iloc[test_days_idx[-1]].date()} ({len(test_window_indices)} sequences)")

    # Save feature configuration and scaler
    with open(os.path.join(model_dir, "scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)

    feature_config = {
        "node_ids": node_ids,
        "feature_names": feature_names,
        "q_mean": q_mean,
        "q_scale": q_scale,
        "means": scaler.mean_.tolist(),
        "scales": scaler.scale_.tolist(),
        "seq_len": seq_len,
        "pred_horizon": pred_horizon,
        "split_dates": {
            "train": [str(dates.iloc[train_days_idx[0]].date()), str(dates.iloc[train_days_idx[-1]].date())],
            "val": [str(dates.iloc[val_days_idx[0]].date()), str(dates.iloc[val_days_idx[-1]].date())],
            "test": [str(dates.iloc[test_days_idx[0]].date()), str(dates.iloc[test_days_idx[-1]].date())]
        }
    }
    with open(os.path.join(model_dir, "feature_config.json"), "w", encoding="utf-8") as f:
        json.dump(feature_config, f, indent=2)

    # 5. Compute P95 Flood Thresholds per Station on Training Data
    train_raw_q = raw_discharge_matrix[:n_train_days, :]
    flood_p95_thresholds = np.percentile(train_raw_q, 95, axis=0)

    # 6. Create DataLoaders
    train_ds = HydroDataset(feat_3d_scaled, target_scaled, train_window_indices, seq_len, pred_horizon)
    val_ds = HydroDataset(feat_3d_scaled, target_scaled, val_window_indices, seq_len, pred_horizon)
    test_ds = HydroDataset(feat_3d_scaled, target_scaled, test_window_indices, seq_len, pred_horizon)

    batch_size = 64
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    # -------------------------------------------------------------
    # 7. MODEL 1: PERSISTENCE BASELINE EVALUATION
    # -------------------------------------------------------------
    print("\n" + "=" * 78)
    print("[MODEL 1/3] PERSISTENCE BASELINE EVALUATION")
    print("=" * 78)
    # Persistence takes Day-0 physical discharge and predicts Q(t+h) = Q(t) for h=1,2,3
    test_pers_preds = []
    test_trues = []
    for t in test_window_indices:
        q_t = raw_discharge_matrix[t, :] # [N]
        pred_3d = np.tile(q_t[:, None], (1, pred_horizon)) # [N, 3]
        true_3d = raw_discharge_matrix[t + 1 : t + pred_horizon + 1, :].T # [N, 3]
        test_pers_preds.append(pred_3d)
        test_trues.append(true_3d)

    test_pers_preds = np.stack(test_pers_preds, axis=0) # [Samples, N, 3]
    test_trues = np.stack(test_trues, axis=0)           # [Samples, N, 3]

    pers_metrics = {}
    for h in range(pred_horizon):
        name = f"day_{h+1}_{24*(h+1)}h"
        m = compute_hydrological_metrics(test_trues[:, :, h], test_pers_preds[:, :, h], flood_p95_thresholds)
        pers_metrics[name] = m
        print(f"  Persistence {name}: NSE={m['NSE']:.4f} | KGE={m['KGE']:.4f} | R²={m['R2']:.4f} | RMSE={m['RMSE_cumecs']} m³/s | F1={m['Flood_F1']:.4f}")

    # -------------------------------------------------------------
    # 8. MODEL 2: TEMPORAL-ONLY ABLATION (use_gat=False)
    # -------------------------------------------------------------
    print("\n" + "=" * 78)
    print("[MODEL 2/3] TEMPORAL-ONLY ABLATION TRAINING (LSTM + Temporal Attention, use_gat=False)")
    print("=" * 78)
    model_temp = ST_Attention_GAT(num_nodes=N, input_dim=6, hidden_dim=32, seq_len=14, pred_horizon=3, use_gat=False)
    opt_temp = torch.optim.AdamW(model_temp.parameters(), lr=1.5e-3, weight_decay=1e-4)
    sched_temp = torch.optim.lr_scheduler.ReduceLROnPlateau(opt_temp, mode="min", factor=0.5, patience=3, min_lr=1e-5)

    best_val_loss_temp = float("inf")
    patience_cnt = 0
    max_epochs = 30
    best_weights_temp = None

    t0_tr = time.time()
    for epoch in range(max_epochs):
        tr_loss = train_epoch(model_temp, train_loader, opt_temp, adj, q_mean, q_scale, device)
        v_loss = evaluate_loss(model_temp, val_loader, adj, q_mean, q_scale, device)
        sched_temp.step(v_loss)

        if v_loss < best_val_loss_temp:
            best_val_loss_temp = v_loss
            patience_cnt = 0
            best_weights_temp = {k: v.cpu().clone() for k, v in model_temp.state_dict().items()}
        else:
            patience_cnt += 1

        if (epoch + 1) % 5 == 0 or epoch == 0 or patience_cnt >= 6:
            print(f"  Epoch {epoch+1:2d}/{max_epochs:2d} | Train Loss: {tr_loss:.4f} | Val Loss: {v_loss:.4f} | Best: {best_val_loss_temp:.4f}")

        if patience_cnt >= 6:
            print(f"  Early stopping triggered at epoch {epoch+1} (patience=6).")
            break

    print(f"Temporal-Only model trained in {time.time()-t0_tr:.1f}s.")
    model_temp.load_state_dict(best_weights_temp)
    torch.save(model_temp.state_dict(), os.path.join(model_dir, "ablation_no_gat.pt"))

    temp_preds, temp_trues = predict_test_set(model_temp, test_loader, adj, q_mean, q_scale, device)
    temp_metrics = {}
    for h in range(pred_horizon):
        name = f"day_{h+1}_{24*(h+1)}h"
        m = compute_hydrological_metrics(temp_trues[:, :, h], temp_preds[:, :, h], flood_p95_thresholds)
        temp_metrics[name] = m
        print(f"  Temporal-Only {name}: NSE={m['NSE']:.4f} | KGE={m['KGE']:.4f} | R²={m['R2']:.4f} | RMSE={m['RMSE_cumecs']} m³/s | F1={m['Flood_F1']:.4f}")

    # -------------------------------------------------------------
    # 9. MODEL 3: PROPOSED ST-ATTENTION-GAT (use_gat=True, River DAG)
    # -------------------------------------------------------------
    print("\n" + "=" * 78)
    print("[MODEL 3/3] PROPOSED ST-ATTENTION-GAT TRAINING (use_gat=True, Physical River DAG)")
    print("=" * 78)
    model_stgat = ST_Attention_GAT(num_nodes=N, input_dim=6, hidden_dim=32, seq_len=14, pred_horizon=3, use_gat=True)
    total_params = sum(p.numel() for p in model_stgat.parameters())
    print(f"Proposed Model Parameter Count: {total_params:,}")

    opt_stgat = torch.optim.AdamW(model_stgat.parameters(), lr=1.5e-3, weight_decay=1e-4)
    sched_stgat = torch.optim.lr_scheduler.ReduceLROnPlateau(opt_stgat, mode="min", factor=0.5, patience=3, min_lr=1e-5)

    best_val_loss_stgat = float("inf")
    patience_cnt = 0
    best_weights_stgat = None
    epoch_history = []

    t0_stgat = time.time()
    for epoch in range(max_epochs):
        tr_loss = train_epoch(model_stgat, train_loader, opt_stgat, adj, q_mean, q_scale, device)
        v_loss = evaluate_loss(model_stgat, val_loader, adj, q_mean, q_scale, device)
        sched_stgat.step(v_loss)

        epoch_history.append((epoch + 1, tr_loss, v_loss))

        if v_loss < best_val_loss_stgat:
            best_val_loss_stgat = v_loss
            patience_cnt = 0
            best_weights_stgat = {k: v.cpu().clone() for k, v in model_stgat.state_dict().items()}
            best_epoch = epoch + 1
        else:
            patience_cnt += 1

        if (epoch + 1) % 5 == 0 or epoch == 0 or patience_cnt >= 6:
            print(f"  Epoch {epoch+1:2d}/{max_epochs:2d} | Train Loss: {tr_loss:.4f} | Val Loss: {v_loss:.4f} | Best: {best_val_loss_stgat:.4f}")

        if patience_cnt >= 6:
            print(f"  Early stopping triggered at epoch {epoch+1} (patience=6).")
            break

    training_duration = time.time() - t0_stgat
    print(f"ST-Attention-GAT trained in {training_duration:.1f}s (Best Epoch: {best_epoch}).")

    model_stgat.load_state_dict(best_weights_stgat)
    save_path = os.path.join(model_dir, "krishna_tft_gat.pt")
    torch.save(model_stgat.state_dict(), save_path)
    print(f"Saved optimal ST-Attention-GAT weights to {save_path}.")

    stgat_preds, stgat_trues = predict_test_set(model_stgat, test_loader, adj, q_mean, q_scale, device)
    stgat_metrics = {}
    for h in range(pred_horizon):
        name = f"day_{h+1}_{24*(h+1)}h"
        m = compute_hydrological_metrics(stgat_trues[:, :, h], stgat_preds[:, :, h], flood_p95_thresholds)
        stgat_metrics[name] = m
        print(f"  Proposed ST-GAT {name}: NSE={m['NSE']:.4f} | KGE={m['KGE']:.4f} | R²={m['R2']:.4f} | RMSE={m['RMSE_cumecs']} m³/s | F1={m['Flood_F1']:.4f}")

    # -------------------------------------------------------------
    # 10. PER-STATION TEST NSE & RMSE TABLE
    # -------------------------------------------------------------
    key_stations = [
        "KOYNA", "KARAD", "SANGLI", "KURUNDWAD", "HIPPARAGI", "ALMATTI",
        "NARAYANPUR", "HIDKAL", "GOKAK_CONFLUENCE", "SRISAILAM", "PRAKASAM"
    ]
    per_station_results = {}
    print("\n" + "=" * 78)
    print("PER-STATION TEST EVALUATION (Day 1 / 24h Forecast)")
    print("=" * 78)
    print(f"{'Station':20s} | {'Obs Mean':10s} | {'Obs Peak':10s} | {'NSE':8s} | {'KGE':8s} | {'R²':8s} | {'RMSE (m³/s)':12s}")
    print("-" * 78)
    for st in key_stations:
        idx = node_to_idx[st]
        st_true = stgat_trues[:, idx, 0]
        st_pred = stgat_preds[:, idx, 0]

        ss_res = np.sum((st_true - st_pred) ** 2)
        ss_tot = np.sum((st_true - np.mean(st_true)) ** 2)
        st_nse = 1.0 - (ss_res / (ss_tot + 1e-8))

        r_mat = np.corrcoef(st_true, st_pred)
        r = r_mat[0, 1] if not np.isnan(r_mat[0, 1]) else 0.0
        r2 = r ** 2

        alpha = (np.std(st_pred) + 1e-8) / (np.std(st_true) + 1e-8)
        beta = (np.mean(st_pred) + 1e-8) / (np.mean(st_true) + 1e-8)
        kge = 1.0 - np.sqrt((r - 1.0)**2 + (alpha - 1.0)**2 + (beta - 1.0)**2)
        st_rmse = np.sqrt(np.mean((st_true - st_pred)**2))

        per_station_results[st] = {
            "obs_mean": round(float(np.mean(st_true)), 2),
            "obs_max": round(float(np.max(st_true)), 2),
            "NSE_24h": round(float(st_nse), 4),
            "KGE_24h": round(float(kge), 4),
            "R2_24h": round(float(r2), 4),
            "RMSE_cumecs_24h": round(float(st_rmse), 2)
        }
        print(f"{st:20s} | {np.mean(st_true):10.1f} | {np.max(st_true):10.1f} | {st_nse:8.4f} | {kge:8.4f} | {r2:8.4f} | {st_rmse:12.2f}")

    # -------------------------------------------------------------
    # 11. SAVE TRAINING METRICS & SUMMARY
    # -------------------------------------------------------------
    full_audit_metrics = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_parameters": total_params,
            "architecture": "Spatio-Temporal Attention Graph Neural Network (ST-Attention-GAT)",
            "features": feature_names,
            "stations_count": N,
            "training_duration_seconds": round(training_duration, 2),
            "best_epoch": best_epoch,
            "loss_function": "Peak-Aware Composite Smooth-L1 + Scaled Physical MSE"
        },
        "splits": {
            "train_days": n_train_days,
            "val_days": n_val_days,
            "test_days": n_test_days,
            "train_windows": len(train_window_indices),
            "val_windows": len(val_window_indices),
            "test_windows": len(test_window_indices)
        },
        "ablation_study": {
            "persistence_baseline": pers_metrics,
            "temporal_only_no_gat": temp_metrics,
            "proposed_st_attention_gat": stgat_metrics
        },
        "per_station_test_metrics": per_station_results
    }

    metrics_save_path = os.path.join(model_dir, "training_metrics.json")
    with open(metrics_save_path, "w", encoding="utf-8") as f:
        json.dump(full_audit_metrics, f, indent=2)

    print(f"\n[Done] Saved full IEEE benchmark metrics to {metrics_save_path}.")
    print("=" * 78)

if __name__ == "__main__":
    main()
