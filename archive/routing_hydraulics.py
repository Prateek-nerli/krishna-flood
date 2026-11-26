import math

def calculate_scs_runoff(p_cum_mm, a_acc_km2, tan_beta, cn=82):
    s_retention_mm = (25400.0 / cn) - 254.0
    if p_cum_mm > 0.2 * s_retention_mm:
        q_surf_mm = ((p_cum_mm - 0.2 * s_retention_mm) ** 2) / (p_cum_mm + 0.8 * s_retention_mm)
    else:
        q_surf_mm = 0.0
        
    twi = math.log((a_acc_km2 * 1e6) / max(0.001, tan_beta))
    d_runoff_m = (q_surf_mm / 1000.0) * min(3.0, twi / 8.0)
    return d_runoff_m

def get_channel_params(node_id):
    tributaries = ["RAKASKOP", "SHIRUR_MARKANDEYA", "HIRANYAKESHI", "KALAMMAWADI", "KALLOL_CHIKODI"]
    major_tributaries = ["HIDKAL", "GOKAK_CONFLUENCE", "MUDHOL_GHATAPRABHA", "MALAPRABHA_DAM", "BADAMI_MALAPRABHA", "WARANA", "RADHANAGARI"]
    upper_krishna = ["KOYNA", "KARAD", "SANGLI", "KURUNDWAD", "HIPPARAGI"]
    middle_lower_krishna = ["ALMATTI", "NARAYANPUR", "UJJANI", "YADGIR", "JURALA", "TUNGABHADRA", "SRISAILAM", "NAGARJUNA", "PULICHINTALA", "PRAKASAM"]
    
    if node_id in tributaries: return 45.0, 0.00025
    if node_id in major_tributaries: return 75.0, 0.00022
    if node_id in upper_krishna: return 140.0, 0.00018
    if node_id in middle_lower_krishna: return 260.0, 0.00015
    return 75.0, 0.00022

def routing_and_submersion(
    q_upstream,
    q_downstream,
    wse_down_t_k,
    dist_to_down_m,
    e_bed_r,
    w_r_fallback,
    alpha,
    hand_coord,
    p_cum_mm,
    a_acc_km2,
    tan_beta,
    node_id="SANGLI",
    h_r_prev=2.0,
    manning_n=0.035
):
    q_r_t_k = q_upstream + alpha * (q_downstream - q_upstream)
    
    W_r, S_0 = get_channel_params(node_id)
        
    h_manning = ((q_r_t_k * manning_n) / (W_r * math.sqrt(S_0))) ** 0.6
    h_backwater = max(0.0, wse_down_t_k - e_bed_r) * math.exp(-2.5 * (1.0 - alpha))
    
    h_r_t_k = max(h_manning, h_backwater)
    wse_local = e_bed_r + h_r_t_k
    
    overbank_flood_depth_m = max(0.0, h_r_t_k - hand_coord)
    d_runoff_m = calculate_scs_runoff(p_cum_mm, a_acc_km2, tan_beta, cn=82)
    total_submersion_depth_m = overbank_flood_depth_m + d_runoff_m
    
    if total_submersion_depth_m >= 0.15:
        submerged = True
        risk = "CRITICAL"
        safety_margin = 0.0
    elif hand_coord - h_r_t_k < 1.0:
        submerged = False
        risk = "HIGH_WARNING_BUFFER"
        safety_margin = hand_coord - h_r_t_k
    else:
        submerged = False
        risk = "SAFE"
        safety_margin = hand_coord - h_r_t_k
        
    return {
        "submerged": submerged,
        "risk": risk,
        "total_submersion_depth_m": total_submersion_depth_m,
        "overbank_flood_depth_m": overbank_flood_depth_m,
        "runoff_depth_m": d_runoff_m,
        "river_stage_h_r": h_r_t_k,
        "safety_margin_m": safety_margin,
        "wse_local": wse_local
    }
