import torch
import torch.nn as nn
import torch.nn.functional as F

class SimpleGATLayer(nn.Module):
    """
    A simplified Graph Attention Layer in pure PyTorch.
    """
    def __init__(self, in_features, out_features, dropout=0.1, alpha=0.2):
        super(SimpleGATLayer, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha

        self.W = nn.Linear(in_features, out_features, bias=False)
        self.a = nn.Linear(2 * out_features, 1, bias=False)
        self.leakyrelu = nn.LeakyReLU(self.alpha)

    def forward(self, h, adj):
        # h: [N, in_features]
        # adj: [N, N] adjacency matrix
        Wh = self.W(h) # [N, out_features]
        
        N = Wh.size(0)
        
        # We compute attention manually
        # [N, N, 2 * out_features]
        a_input = torch.cat([Wh.repeat(1, N).view(N * N, -1), Wh.repeat(N, 1)], dim=1).view(N, -1, 2 * self.out_features)
        
        e = self.leakyrelu(self.a(a_input).squeeze(2))
        
        # Masked attention
        zero_vec = -9e15 * torch.ones_like(e)
        attention = torch.where(adj > 0, e, zero_vec)
        attention = F.softmax(attention, dim=1)
        attention = F.dropout(attention, self.dropout, training=self.training)
        
        h_prime = torch.matmul(attention, Wh)
        return F.elu(h_prime)

class TFT_GAT_Model(nn.Module):
    """
    Temporal Graph Attention Network mimicking TFT-GAT.
    """
    def __init__(self, num_nodes, input_dim, hidden_dim, seq_len, pred_horizon):
        super(TFT_GAT_Model, self).__init__()
        self.num_nodes = num_nodes
        self.hidden_dim = hidden_dim
        self.seq_len = seq_len
        self.pred_horizon = pred_horizon
        
        # Temporal encoder (LSTM as proxy for TFT)
        self.lstm = nn.LSTM(input_size=input_dim, hidden_size=hidden_dim, batch_first=True)
        
        # Spatial Graph Attention
        self.gat = SimpleGATLayer(hidden_dim, hidden_dim)
        
        # Predictor (discharge & stage at t+1, t+2, t+3 etc)
        self.fc = nn.Linear(hidden_dim, pred_horizon * 2) # * 2 for discharge and stage

    def forward(self, x, adj):
        # x: [Batch, Nodes, SeqLen, Features]
        # adj: [Nodes, Nodes]
        batch_size = x.size(0)
        
        # Reshape for LSTM: [Batch * Nodes, SeqLen, Features]
        x_lstm = x.view(batch_size * self.num_nodes, self.seq_len, -1)
        
        lstm_out, (hn, cn) = self.lstm(x_lstm)
        # Take last hidden state
        h_last = hn[-1] # [Batch * Nodes, Hidden]
        
        # Reshape for GAT: [Batch, Nodes, Hidden]
        h_spat = h_last.view(batch_size, self.num_nodes, self.hidden_dim)
        
        # Apply GAT per batch
        out_list = []
        for b in range(batch_size):
            h_g = self.gat(h_spat[b], adj)
            out_list.append(h_g.unsqueeze(0))
            
        h_final = torch.cat(out_list, dim=0) # [Batch, Nodes, Hidden]
        
        # Predict
        preds = self.fc(h_final) # [Batch, Nodes, pred_horizon * 2]
        
        return preds
