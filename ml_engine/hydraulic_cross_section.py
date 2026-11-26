"""
ml_engine/hydraulic_cross_section.py

First-Principles Hydrological Physics Engine:
1. Dynamic Valley Cross-Section extraction from 30m Copernicus GLO-30 DEM.
2. HEC-RAS Divided-Channel Compound Conveyance Q(H) with main-channel and
   vegetated floodplain zones (roughness derived live from Sentinel-2 NDVI).
3. Bisection Solver for water depth H_manning and true physical bankfull capacity.
4. Downstream backwater afflux computation.
5. Unsteady Empty-Channel Storage Deficit & Wave Attenuation for dam release routing.
"""

import math
from typing import Dict, Any, Tuple, Optional, List
import numpy as np
from gis_engine.raster_lookup import get_copernicus_dem_patch, haversine

def extract_compound_cross_section(
    lat: float,
    lon: float,
    river_lat: float,
    river_lon: float,
    dist_to_river_m: float,
    E_bed_r: float,
    W_live: float,
    bank_transect: Optional[List[List[float]]] = None,
    reach_vector: Optional[Tuple[float, float]] = None
) -> Dict[str, Any]:
    """
    Extracts a perpendicular valley cross-section profile centered at (river_lat, river_lon)
    extending max(1200.0, dist_to_river_m + 300.0) meters on both sides at step=30m.
    Anchors the river thalweg at E_bed_r across channel width W_live.
    """
    # 1. Determine unit perpendicular vector (in meters / local space)
    ux, uy = 1.0, 0.0 # default East-West
    if bank_transect and len(bank_transect) == 2:
        # Bank transect from satellite vision: [[latA, lonA], [latB, lonB]]
        dlat = bank_transect[1][0] - bank_transect[0][0]
        dlon = bank_transect[1][1] - bank_transect[0][1]
        # Convert degrees to approx meters
        dy_m = dlat * 111320.0
        dx_m = dlon * 111320.0 * math.cos(math.radians(river_lat))
        norm = math.hypot(dx_m, dy_m)
        if norm > 1e-3:
            ux, uy = dx_m / norm, dy_m / norm
    elif reach_vector:
        # reach_vector = (dlon, dlat) along river flow
        dx_m = reach_vector[0] * 111320.0 * math.cos(math.radians(river_lat))
        dy_m = reach_vector[1] * 111320.0
        norm = math.hypot(dx_m, dy_m)
        if norm > 1e-3:
            # Perpendicular vector: (-dy, dx)
            ux, uy = -dy_m / norm, dx_m / norm

    # 2. Profile sampling bounds (30m step) - capped to fit strictly inside 96x96 DEM
    L_max = min(1380.0, max(1200.0, dist_to_river_m + 300.0))
    step_m = 30.0
    offsets = np.arange(-L_max, L_max + step_m * 0.5, step_m, dtype=np.float32)

    # 3. Fetch 96x96 Copernicus DEM patch
    dem_patch, transform, crs = get_copernicus_dem_patch(river_lat, river_lon, size_px=96)
    
    # 4. Sample elevation along perpendicular profile
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(river_lat))
    inv_transform = ~transform

    elevations = []
    h_patch, w_patch = dem_patch.shape

    for s in offsets:
        # offset s in meters along unit perpendicular vector (ux, uy)
        d_east_m = s * ux
        d_north_m = s * uy
        pt_lon = river_lon + (d_east_m / m_per_deg_lon)
        pt_lat = river_lat + (d_north_m / m_per_deg_lat)

        # Pixel coordinate in DEM patch
        col_f, row_f = inv_transform * (pt_lon, pt_lat)
        row_i = int(round(row_f))
        col_i = int(round(col_f))

        row_clamped = max(0, min(h_patch - 1, row_i))
        col_clamped = max(0, min(w_patch - 1, col_i))
        z_dem = float(dem_patch[row_clamped, col_clamped])

        # Bed anchoring across main channel zone (|s| <= W_live / 2)
        half_w = max(15.0, W_live / 2.0)
        if abs(s) <= half_w:
            # Parabolic bed profile anchored at E_bed_r at center (s=0)
            z_anchor = E_bed_r + 0.35 * ((s / half_w) ** 2)
            z = min(z_dem, z_anchor) if z_dem > E_bed_r else z_anchor
        else:
            # Floodplain zone: use DEM elevation, ensuring it doesn't dip below bed
            z = max(E_bed_r + 0.35, z_dem)

        elevations.append(z)

    elevations = np.array(elevations, dtype=np.float32)

    # Remove urban DSM building spikes on floodplain cells
    import scipy.ndimage
    fp_mask = np.abs(offsets) > half_w
    if np.any(fp_mask):
        smoothed_fp = scipy.ndimage.minimum_filter1d(elevations, size=3)
        elevations[fp_mask] = np.maximum(E_bed_r + 0.35, smoothed_fp[fp_mask])

    # 5. Detect bankfull height (outside carved bed zone where channel banks meet floodplain)
    half_w = max(15.0, W_live / 2.0)
    bank_search_offset = max(half_w + step_m, half_w * 1.25)
    left_bank_idx = np.argmin(np.abs(offsets - (-bank_search_offset)))
    right_bank_idx = np.argmin(np.abs(offsets - (+bank_search_offset)))
    z_bank_left = float(elevations[left_bank_idx])
    z_bank_right = float(elevations[right_bank_idx])
    z_bank_min = min(z_bank_left, z_bank_right)
    H_bankfull = max(3.0, min(8.0, z_bank_min - E_bed_r))

    return {
        "offsets": offsets,
        "elevations": elevations,
        "step_m": step_m,
        "E_bed_r": float(E_bed_r),
        "W_live": float(W_live),
        "H_bankfull": float(H_bankfull),
        "z_bank_left": z_bank_left,
        "z_bank_right": z_bank_right,
        "center_idx": int(len(offsets) // 2)
    }

def compute_divided_channel_conveyance(
    H: float,
    profile: Dict[str, Any],
    n_ch: float = 0.032,
    n_fp: float = 0.045
) -> Tuple[float, float, float, float, float]:
    """
    HEC-RAS divided-channel method:
    Calculates K_ch, K_left_fp, K_right_fp, total K, wetted top width, and channel velocity.
    Only contiguous cells from center river channel are wetted (stops at valley ridges).
    """
    offsets = profile["offsets"]
    elevations = profile["elevations"]
    step_m = profile["step_m"]
    E_bed_r = profile["E_bed_r"]
    W_live = profile["W_live"]
    center_idx = profile["center_idx"]
    half_w = max(15.0, W_live / 2.0)

    WSE = E_bed_r + H

    # 1. Walk left from center to find contiguous wetted cells
    left_wet_idx = center_idx
    for i in range(center_idx, -1, -1):
        if elevations[i] < WSE:
            left_wet_idx = i
        else:
            break # Ridge stops inundation

    # 2. Walk right from center to find contiguous wetted cells
    right_wet_idx = center_idx
    for i in range(center_idx, len(offsets)):
        if elevations[i] < WSE:
            right_wet_idx = i
        else:
            break # Ridge stops inundation

    wetted_indices = list(range(left_wet_idx, right_wet_idx + 1))
    if not wetted_indices:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    # 3. Categorize into Main Channel and Floodplains
    A_ch, P_ch = 0.0, 0.0
    A_L, P_L = 0.0, 0.0
    A_R, P_R = 0.0, 0.0

    for idx in wetted_indices:
        s = offsets[idx]
        z = elevations[idx]
        depth = max(0.0, WSE - z)
        if depth <= 0.0:
            continue

        # Area contribution
        cell_area = depth * step_m

        # Wetted perimeter (bottom surface)
        if idx > 0:
            dz = z - elevations[idx - 1]
            cell_p = math.hypot(step_m, dz)
        else:
            cell_p = step_m

        if abs(s) <= half_w:
            A_ch += cell_area
            P_ch += cell_p
        elif s < -half_w:
            A_L += cell_area
            P_L += cell_p
        else:
            A_R += cell_area
            P_R += cell_p

    # 4. Conveyance calculations (K = 1/n * A * R^(2/3))
    # Main Channel
    if A_ch > 0.0 and P_ch > 0.0:
        R_ch = A_ch / P_ch
        K_ch = (1.0 / n_ch) * A_ch * (R_ch ** (2.0 / 3.0))
    else:
        K_ch = 0.0

    # Left Floodplain
    if A_L > 0.0 and P_L > 0.0:
        R_L = A_L / P_L
        K_L = (1.0 / n_fp) * A_L * (R_L ** (2.0 / 3.0))
    else:
        K_L = 0.0

    # Right Floodplain
    if A_R > 0.0 and P_R > 0.0:
        R_R = A_R / P_R
        K_R = (1.0 / n_fp) * A_R * (R_R ** (2.0 / 3.0))
    else:
        K_R = 0.0

    K_total = K_ch + K_L + K_R
    wetted_top_width = float(offsets[right_wet_idx] - offsets[left_wet_idx] + step_m)
    wetted_top_width = max(W_live, wetted_top_width)

    return K_total, K_ch, A_ch, wetted_top_width, (A_L + A_R)

def solve_water_depth_and_threshold(
    Q_target_cumecs: float,
    Sf: float,
    profile: Dict[str, Any],
    n_ch: float = 0.032,
    n_fp: float = 0.045,
    hand_m: float = 2.0
) -> Tuple[float, float, float, float, float]:
    """
    Solves for water depth H (manning) via bisection (32 steps).
    Computes top wetted width, velocity, site inundation threshold Q(H=hand_m),
    and channel bankfull capacity Q(H=H_bankfull).
    """
    Q_target = max(1.0, float(Q_target_cumecs))
    Sf_safe = max(0.00003, min(0.0050, float(Sf)))
    sqrt_sf = math.sqrt(Sf_safe)

    # 1. Bisection solver for H in [0.05, 35.0]
    low = 0.05
    high = 35.0
    H_sol = low

    for _ in range(32):
        mid = (low + high) / 2.0
        K_tot, _, _, _, _ = compute_divided_channel_conveyance(mid, profile, n_ch, n_fp)
        Q_mid = K_tot * sqrt_sf
        if Q_mid < Q_target:
            low = mid
        else:
            high = mid
        H_sol = mid

    # Evaluate hydraulics at solved depth
    K_tot, K_ch, A_ch, top_w, A_fp = compute_divided_channel_conveyance(H_sol, profile, n_ch, n_fp)
    v_ch = (K_ch * sqrt_sf) / max(1.0, A_ch) if A_ch > 0.0 else 0.5

    # 2. Evaluate Site Inundation Threshold Flow at H = hand_m
    K_bf_site, _, _, _, _ = compute_divided_channel_conveyance(max(0.5, hand_m), profile, n_ch, n_fp)
    Q_bankfull_cumecs = K_bf_site * sqrt_sf

    # 3. Evaluate Physical Channel Bankfull Capacity at H = H_bankfull
    H_bf_ch = float(profile.get("H_bankfull", 4.5))
    K_bf_ch, _, _, _, _ = compute_divided_channel_conveyance(max(0.5, H_bf_ch), profile, n_ch, n_fp)
    Q_channel_bankfull_cumecs = K_bf_ch * sqrt_sf

    return (
        round(float(H_sol), 2),
        round(float(Q_bankfull_cumecs), 1),
        round(float(top_w), 1),
        round(float(v_ch), 2),
        round(float(Q_channel_bankfull_cumecs), 1)
    )

def compute_backwater_afflux(
    E_bed_r: float,
    H_manning: float,
    WSE_down: float,
    L_m: float,
    alpha: float,
    S_0: float = 0.0002
) -> Tuple[float, float, str]:
    """
    Computes downstream backwater afflux using physical reach length L_m and bed slope S_0.
    Returns (H_final, H_backwater, flood_mechanism).
    """
    # Water surface elevation at downstream boundary
    depth_at_down = max(0.0, WSE_down - E_bed_r)
    # Physical backwater decay scale L_b = depth_at_down / S_0
    L_b = max(5000.0, depth_at_down / max(0.0001, float(S_0)))
    d_up = max(0.0, 1.0 - alpha) * max(1000.0, float(L_m))
    decay = math.exp(-d_up / L_b)
    H_backwater = round(float(depth_at_down * decay), 2)

    H_final = max(H_manning, H_backwater)
    mechanism = "Downstream Backwater Afflux" if H_backwater > (H_manning + 0.10) else "Riverine Overbank Flow"
    return round(float(H_final), 2), H_backwater, mechanism

def compute_tributary_confluence_piling(
    E_bed_r: float,
    H_manning: float,
    WSE_confluence: float,
    alpha: float,
    is_tributary_reach: bool = False
) -> Tuple[float, float, str]:
    """
    Computes tributary confluence piling (reverse backwater) when a tributary enters
    a swollen mainstem junction or confluence.
    If WSE_confluence > E_bed_r + H_manning, afflux decays exponentially upstream along reach:
        H_tributary_piling = max(0.0, WSE_confluence - E_bed_r) * exp(-2.2 * (1.0 - alpha))
    """
    if not is_tributary_reach or WSE_confluence <= (E_bed_r + H_manning):
        return round(float(H_manning), 2), 0.0, "Riverine Overbank Flow"

    depth_at_confluence = max(0.0, WSE_confluence - E_bed_r)
    decay = math.exp(-2.2 * max(0.0, 1.0 - alpha))
    H_piling = round(float(depth_at_confluence * decay), 2)

    if H_piling > H_manning:
        return round(float(H_piling), 2), H_piling, "TRIBUTARY_CONFLUENCE_PILING"

    return round(float(H_manning), 2), H_piling, "Riverine Overbank Flow"

def compute_unsteady_wave_attenuation(
    Q_pulse_cusecs: float,
    release_duration_hours: float,
    dist_km: float,
    Q_t0_cumecs: float,
    W_ch: float = 120.0,
    H_bf: float = 4.5,
    Sf: float = 0.0003
) -> Tuple[float, float]:
    """
    Computes physics-based empty-channel storage deficit absorption and hydrograph flattening.
    Returns (Q_arriving_cumecs, storage_absorbed_pct).
    """
    if Q_pulse_cusecs <= 0.0:
        return 0.0, 0.0

    Q_in_cumecs = Q_pulse_cusecs / 35.3146667
    duration_sec = max(3600.0, float(release_duration_hours) * 3600.0)
    V_wave = Q_in_cumecs * duration_sec
    L_m = max(1000.0, float(dist_km) * 1000.0)

    # 1. Current wetted area from baseflow
    Q_base = max(1.0, float(Q_t0_cumecs))
    # Approximate normal depth from Manning equation
    y_base = np.clip((Q_base * 0.035 / (max(20.0, W_ch) * math.sqrt(max(0.00005, Sf)))) ** 0.6, 0.2, H_bf)
    A_curr = W_ch * float(y_base)
    A_bf = W_ch * max(1.5, H_bf)

    # 2. Empty-channel storage volume below bankfull
    V_empty = L_m * max(0.0, A_bf - A_curr)

    # 3. Channel filling + transmission absorption
    V_absorbed = min(0.65 * V_empty, 0.85 * V_wave)
    absorbed_pct = round(float((V_absorbed / max(1.0, V_wave)) * 100.0), 1)

    # 4. Kinematic wave celerity and hydrograph spreading
    c_wave = max(1.5, min(3.5, 1.67 * (Q_in_cumecs / max(10.0, W_ch * max(1.0, float(y_base))))))
    dt_spread = (L_m / c_wave) * 0.35

    # 5. Attenuated peak addition arriving downstream
    V_remaining = max(0.0, V_wave - V_absorbed)
    Q_added_cumecs = V_remaining / (duration_sec + dt_spread)

    return float(Q_added_cumecs), float(absorbed_pct)
