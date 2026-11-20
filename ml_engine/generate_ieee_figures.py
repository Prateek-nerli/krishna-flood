import os
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch

from ml_engine.model_tft_gat import ST_Attention_GAT, build_basin_adjacency

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fig_dir = os.path.join(base_dir, "ieee_figures")
    os.makedirs(fig_dir, exist_ok=True)
    
    # 1. Load metrics
    metrics_path = os.path.join(base_dir, "models", "training_metrics.json")
    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)
        
    ablation = metrics["ablation_study"]
    per_station = metrics["per_station_test_metrics"]
    
    # Load replays
    replays_path = os.path.join(base_dir, "models", "test_hydrograph_replays.json")
    with open(replays_path, "r", encoding="utf-8") as f:
        replays = json.load(f)
        
    # Set publication styling
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.titlesize": 14,
        "figure.dpi": 300
    })

    # =========================================================================
    # FIGURE 1: 3-Way Ablation Comparison (NSE & RMSE)
    # =========================================================================
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    
    horizons = ["Day 1 (+24h)", "Day 2 (+48h)", "Day 3 (+72h)"]
    h_keys = ["day_1_24h", "day_2_48h", "day_3_72h"]
    x = np.arange(len(horizons))
    width = 0.26
    
    pers_nse = [ablation["persistence_baseline"][k]["NSE"] for k in h_keys]
    temp_nse = [ablation["temporal_only_no_gat"][k]["NSE"] for k in h_keys]
    stgat_nse = [ablation["proposed_st_attention_gat"][k]["NSE"] for k in h_keys]
    
    pers_rmse = [ablation["persistence_baseline"][k]["RMSE_cumecs"] for k in h_keys]
    temp_rmse = [ablation["temporal_only_no_gat"][k]["RMSE_cumecs"] for k in h_keys]
    stgat_rmse = [ablation["proposed_st_attention_gat"][k]["RMSE_cumecs"] for k in h_keys]
    
    # Subplot 1: NSE
    ax1.bar(x - width, pers_nse, width, label="Persistence Baseline", color="#94a3b8", edgecolor="#64748b")
    ax1.bar(x, temp_nse, width, label="Temporal-Only (No GAT)", color="#38bdf8", edgecolor="#0284c7")
    ax1.bar(x + width, stgat_nse, width, label="Proposed ST-Attention-GAT", color="#2563eb", edgecolor="#1e40af")
    ax1.set_ylabel("Nash-Sutcliffe Efficiency (NSE)")
    ax1.set_title("(a) Hydrological Forecast Accuracy (NSE)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(horizons)
    ax1.set_ylim(0.70, 1.0)
    ax1.legend(loc="lower left")
    for i in range(len(horizons)):
        ax1.text(x[i] + width, stgat_nse[i] + 0.008, f"{stgat_nse[i]:.4f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1e40af")

    # Subplot 2: RMSE
    ax2.bar(x - width, pers_rmse, width, label="Persistence Baseline", color="#94a3b8", edgecolor="#64748b")
    ax2.bar(x, temp_rmse, width, label="Temporal-Only (No GAT)", color="#38bdf8", edgecolor="#0284c7")
    ax2.bar(x + width, stgat_rmse, width, label="Proposed ST-Attention-GAT", color="#2563eb", edgecolor="#1e40af")
    ax2.set_ylabel(r"RMSE ($m^3/s$)")
    ax2.set_title(r"(b) Forecasting Error Reduction (RMSE in $m^3/s$)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(horizons)
    ax2.legend(loc="upper left")
    for i in range(len(horizons)):
        ax2.text(x[i] + width, stgat_rmse[i] + 15, f"{stgat_rmse[i]:.1f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color="#1e40af")

    plt.tight_layout()
    f1_path = os.path.join(fig_dir, "fig1_ablation_comparison.png")
    plt.savefig(f1_path, dpi=300)
    plt.close()
    print(f"Saved: {f1_path}")

    # =========================================================================
    # FIGURE 2: 2x2 Grid of Unseen 2024 Monsoon Peak Hydrographs
    # =========================================================================
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    stations_to_plot = [
        ("SANGLI", "Sangli Reach (Upper Krishna)", axes[0, 0]),
        ("ALMATTI", "Almatti Dam (Middle Krishna)", axes[0, 1]),
        ("SRISAILAM", "Srisailam Reservoir (Lower Krishna)", axes[1, 0]),
        ("PRAKASAM", "Prakasam Barrage (Delta Outlet)", axes[1, 1])
    ]
    
    for st_id, st_name, ax in stations_to_plot:
        data = replays["stations"][st_id]
        dates = [d["date"][5:] for d in data] # MM-DD
        obs = [d["observed_discharge_cumecs"] for d in data]
        pred_t1 = [d["pred_t1_cumecs"] for d in data]
        pred_t3 = [d["pred_t3_cumecs"] for d in data]
        
        ax.plot(dates, obs, label="Observed River Discharge", color="#0f172a", linewidth=2.0)
        ax.plot(dates, pred_t1, label="ST-Attention-GAT (T+1 / 24h)", color="#2563eb", linestyle="--", linewidth=1.8)
        ax.plot(dates, pred_t3, label="ST-Attention-GAT (T+3 / 72h)", color="#dc2626", linestyle=":", linewidth=1.6)
        
        ax.set_title(st_name, fontweight="bold")
        ax.set_ylabel(r"Discharge ($m^3/s$)")
        ax.legend(loc="upper left", frameon=True)
        # Sample every 10th date tick
        ax.set_xticks(range(0, len(dates), 10))
        ax.set_xticklabels([dates[i] for i in range(0, len(dates), 10)], rotation=30)
        ax.grid(True, linestyle="--", alpha=0.6)

    plt.suptitle("Unseen Test-Set 2024 Monsoon Peak Hydrograph Replays (July - Sept 2024)", fontsize=15, y=0.99)
    plt.tight_layout()
    f2_path = os.path.join(fig_dir, "fig2_monsoon_hydrographs.png")
    plt.savefig(f2_path, dpi=300)
    plt.close()
    print(f"Saved: {f2_path}")

    # =========================================================================
    # FIGURE 3: Directed GAT Spatial Attention Heatmap
    # =========================================================================
    with open(os.path.join(base_dir, "seed_nodes.json"), "r") as f:
        nodes = json.load(f)
    node_ids = [n["node_id"] for n in nodes]
    N = len(node_ids)
    adj = build_basin_adjacency(nodes, verbose=False)
    
    model = ST_Attention_GAT(num_nodes=N, input_dim=6, hidden_dim=32, seq_len=14, pred_horizon=3, use_gat=True, dropout=0.0)
    weights_path = os.path.join(base_dir, "models", "krishna_tft_gat.pt")
    state_dict = torch.load(weights_path, weights_only=False)
    if isinstance(state_dict, dict) and "model_state_dict" in state_dict:
        model.load_state_dict(state_dict["model_state_dict"])
    else:
        model.load_state_dict(state_dict)
    model.eval()

    # Pass dummy batch to extract learned spatial weights
    dummy_x = torch.zeros((1, N, 14, 6), dtype=torch.float32)
    with torch.no_grad():
        _, attn_info = model(dummy_x, adj, return_attention=True)
        spat_attn = attn_info["spatial"][0].cpu().numpy() # [N, N]
        
    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(spat_attn, cmap="Blues", interpolation="nearest")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Learned Directed GAT Spatial Attention Weight")
    
    ax.set_xticks(np.arange(N))
    ax.set_yticks(np.arange(N))
    ax.set_xticklabels(node_ids, rotation=90, fontsize=8)
    ax.set_yticklabels(node_ids, fontsize=8)
    ax.set_xlabel("Key / Source Station (Upstream Inflow Contributor)")
    ax.set_ylabel("Query / Target Station (Monitored Reach Junction)")
    ax.set_title("Learned 2-Hop Spatio-Temporal Graph Attention Matrix (27 Stations)", fontsize=13)
    
    plt.tight_layout()
    f3_path = os.path.join(fig_dir, "fig3_gat_spatial_attention.png")
    plt.savefig(f3_path, dpi=300)
    plt.close()
    print(f"Saved: {f3_path}")

    # =========================================================================
    # FIGURE 4: Per-Station Test NSE Bar Chart
    # =========================================================================
    fig, ax = plt.subplots(figsize=(10, 6))
    st_names = list(per_station.keys())
    nse_vals = [per_station[s]["NSE_24h"] for s in st_names]
    kge_vals = [per_station[s]["KGE_24h"] for s in st_names]
    
    y = np.arange(len(st_names))
    height = 0.35
    
    ax.barh(y - height/2, nse_vals, height, label="Nash-Sutcliffe Efficiency (NSE)", color="#2563eb")
    ax.barh(y + height/2, kge_vals, height, label="Kling-Gupta Efficiency (KGE)", color="#06b6d4")
    
    ax.set_yticks(y)
    ax.set_yticklabels(st_names, fontsize=9.5)
    ax.set_xlim(0.85, 1.0)
    ax.set_xlabel("Efficiency Metric Value (Test Set 2024-2026)")
    ax.set_title("Per-Station 24-Hour Forecast Accuracy Across Key Monitored Reaches", fontsize=12)
    ax.legend(loc="lower left")
    ax.grid(True, linestyle="--", alpha=0.5)

    for i in range(len(st_names)):
        ax.text(nse_vals[i] + 0.002, y[i] - height/2, f"{nse_vals[i]:.3f}", va="center", fontsize=8.5, fontweight="bold", color="#1e40af")

    plt.tight_layout()
    f4_path = os.path.join(fig_dir, "fig4_per_station_nse.png")
    plt.savefig(f4_path, dpi=300)
    plt.close()
    print(f"Saved: {f4_path}")
    print("\n[Done] All 4 publication-ready IEEE figures generated successfully.")

if __name__ == "__main__":
    main()
