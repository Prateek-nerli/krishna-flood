import math
import torch
import torch.nn as nn
import torch.nn.functional as F

def build_basin_adjacency(nodes_list, verbose=False):
    """
    Constructs a physically directed adjacency matrix for the river network.
    Adjacency convention:
      In h_prime = matmul(attention, Wh), row v aggregates from columns u where adj[v, u] > 0.
      Therefore, setting adj[idx[v], idx[u]] = 1.0 ensures node v (downstream)
      strictly receives water and hydrological state from node u (upstream).
    """
    if isinstance(nodes_list[0], dict):
        node_ids = [n["node_id"] for n in nodes_list]
        nodes_dict = {n["node_id"]: n for n in nodes_list}
    else:
        node_ids = list(nodes_list)
        nodes_dict = {}

    N = len(node_ids)
    idx_map = {nid: i for i, nid in enumerate(node_ids)}
    adj = torch.eye(N, dtype=torch.float32)

    edges = []
    for u in node_ids:
        dn = nodes_dict.get(u, {}).get("downstream_node")
        if dn and dn in idx_map:
            u_idx = idx_map[u]
            v_idx = idx_map[dn]
            adj[v_idx, u_idx] = 1.0
            edges.append((u, dn, u_idx, v_idx))

    if verbose:
        print(f"Constructed River DAG Adjacency Matrix ({N}x{N}) with {len(edges)} directed edges:")
        for u, v, ui, vi in edges:
            print(f"  [Edge {ui:2d} -> {vi:2d}] {u:20s} ===>>> {v:20s}")

    return adj

class GatedResidualNetwork(nn.Module):
    """
    TFT-style Gated Residual Network (GRN).
    Non-linear feature processing with ELU activation, GLU gating, skip connection, and LayerNorm.
    """
    def __init__(self, d_in, d_hidden, d_out, dropout=0.1):
        super().__init__()
        self.fc1 = nn.Linear(d_in, d_hidden)
        self.elu = nn.ELU()
        self.fc2 = nn.Linear(d_hidden, d_out)
        self.gate = nn.Linear(d_hidden, d_out)
        self.dropout = nn.Dropout(dropout)
        self.skip = nn.Linear(d_in, d_out) if d_in != d_out else nn.Identity()
        self.norm = nn.LayerNorm(d_out)

    def forward(self, x):
        h = self.elu(self.fc1(x))
        val = self.fc2(h)
        gate = torch.sigmoid(self.gate(h))
        glu = val * gate
        return self.norm(self.skip(x) + self.dropout(glu))

class VectorizedGATLayer(nn.Module):
    """
    Batched, vectorized Multi-Head Graph Attention Layer for spatial river network routing.
    Computes spatial attention over topological upstream-to-downstream adjacency.
    """
    def __init__(self, hidden_dim, num_heads=4, dropout=0.1, alpha=0.2):
        super().__init__()
        assert hidden_dim % num_heads == 0, "hidden_dim must be divisible by num_heads"
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.d_k = hidden_dim // num_heads

        self.W_q = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.W_k = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.W_v = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.W_o = nn.Linear(hidden_dim, hidden_dim)

        self.leaky_relu = nn.LeakyReLU(alpha)
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(self, h, adj):
        B, N, H = h.shape
        q = self.W_q(h).view(B, N, self.num_heads, self.d_k).transpose(1, 2)
        k = self.W_k(h).view(B, N, self.num_heads, self.d_k).transpose(1, 2)
        v = self.W_v(h).view(B, N, self.num_heads, self.d_k).transpose(1, 2)

        scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(self.d_k)
        scores = self.leaky_relu(scores)

        # Mask invalid topological edges (where adj == 0)
        mask = (adj == 0).unsqueeze(0).unsqueeze(0)
        scores = scores.masked_fill(mask, -1e9)

        attn = F.softmax(scores, dim=-1)
        attn_drop = self.dropout(attn)

        out = torch.matmul(attn_drop, v)
        out = out.transpose(1, 2).contiguous().view(B, N, H)
        out = self.W_o(out)

        return self.norm(h + self.dropout(out)), attn

class ST_Attention_GAT(nn.Module):
    """
    Spatio-Temporal Attention Graph Neural Network (ST-Attention-GAT)
    for Krishna River Basin Hydro-Discharge Forecasting.

    Inputs:
      x:   [Batch, N, T=14, F=6] tensor of 6 non-redundant hydro features
      adj: [N, N] directed river topology adjacency matrix
    Outputs:
      pred: [Batch, N, pred_horizon=3] scaled log1p-discharge forecasts
    """
    def __init__(self, num_nodes=27, input_dim=6, hidden_dim=32, seq_len=14, pred_horizon=3, use_gat=True, dropout=0.1):
        super().__init__()
        self.num_nodes = num_nodes
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.seq_len = seq_len
        self.pred_horizon = pred_horizon
        self.use_gat = use_gat

        # Learnable Station Embedding (num_nodes x 8 dims)
        self.station_emb = nn.Embedding(num_nodes, 8)
        self.input_grn = GatedResidualNetwork(d_in=input_dim + 8, d_hidden=hidden_dim, d_out=hidden_dim, dropout=dropout)

        # Temporal Sequence Modeling
        self.lstm = nn.LSTM(input_size=hidden_dim, hidden_size=hidden_dim, num_layers=1, batch_first=True)
        self.temporal_attn = nn.MultiheadAttention(embed_dim=hidden_dim, num_heads=4, dropout=dropout, batch_first=True)
        self.temp_grn = GatedResidualNetwork(d_in=hidden_dim, d_hidden=hidden_dim, d_out=hidden_dim, dropout=dropout)

        # Spatial Graph Attention (2-hop upstream routing)
        if self.use_gat:
            self.gat1 = VectorizedGATLayer(hidden_dim=hidden_dim, num_heads=4, dropout=dropout)
            self.gat2 = VectorizedGATLayer(hidden_dim=hidden_dim, num_heads=4, dropout=dropout)

        # Residual Forecasting Head
        self.fc = nn.Linear(hidden_dim, pred_horizon)

    def forward(self, x, adj, return_attention=False):
        B, N, T, F_dim = x.shape
        device = x.device

        # Concatenate station embeddings [B, N, T, 8]
        node_indices = torch.arange(N, device=device).unsqueeze(0).unsqueeze(2).expand(B, N, T)
        emb = self.station_emb(node_indices)

        x_feat = torch.cat([x, emb], dim=-1)
        x_proj = self.input_grn(x_feat)

        # Temporal sequence modeling via LSTM
        x_reshaped = x_proj.view(B * N, T, self.hidden_dim)
        lstm_out, _ = self.lstm(x_reshaped)

        # Multi-head temporal attention: query current day t against all 14 antecedent lag days
        query = lstm_out[:, -1:, :]
        attn_out, temp_weights = self.temporal_attn(query, lstm_out, lstm_out, need_weights=True)
        h_temp = self.temp_grn(attn_out.squeeze(1) + lstm_out[:, -1, :])
        h_spatial = h_temp.view(B, N, self.hidden_dim)

        spatial_weights = None
        if self.use_gat:
            h_spatial, sp_attn1 = self.gat1(h_spatial, adj)
            h_spatial, sp_attn2 = self.gat2(h_spatial, adj)
            if return_attention:
                spatial_weights = 0.5 * (sp_attn1.mean(dim=1) + sp_attn2.mean(dim=1))

        # Residual forecast: pred = last observed scaled log-discharge + residual delta
        last_q = x[:, :, -1, 0:1] # [B, N, 1]
        delta = self.fc(h_spatial)
        pred = last_q + delta

        if return_attention:
            temp_weights_reshaped = temp_weights.squeeze(1).view(B, N, T)
            return pred, {"temporal": temp_weights_reshaped, "spatial": spatial_weights}

        return pred

# Primary Alias for backward compatibility
TFT_GAT_Model = ST_Attention_GAT
