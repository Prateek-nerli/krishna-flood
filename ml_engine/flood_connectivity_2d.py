"""
ml_engine/flood_connectivity_2d.py

2D Geospatial Physics Engine:
1. find_critical_entry_height_y:
   - 8-neighbor Minimax Dijkstra path solver using heapq seeded from all river pixels.
   - Finds saddle/critical overtopping height Y to target cell (stops immediately when target popped, < 15ms).
   - Direct line-of-sight profile between nearest river pixel and target via Bresenham line algorithm (H_direct).
   - Flanking condition: when Y <= WSE < H_direct, direct ridge blocks line-of-sight but water flanks around.
2. extract_connected_flood_footprint:
   - Hydrologically connected flood cells (below WSE and path-connected to river channel via scipy.ndimage).
   - Outer boundary polygon extraction using OpenCV findContours + approxPolyDP.
   - Converts to [lat, lon] Leaflet coordinate rings and computes inundated area in hectares.
3. find_nearest_safe_high_ground:
   - High-ground evacuation search using DEM elevation clearance (>= WSE + 3m) and gentle slope (< 8 deg).
   - Multi-tier relaxation if terrain is constrained.
   - Returns lat/lon, distance in meters, 8-point compass bearing, elevation, and clearance.
"""

import math
import heapq
from typing import List, Tuple, Dict, Optional, Any, Union
import numpy as np
import scipy.ndimage
import cv2
from rasterio.transform import Affine

# 4-connected cardinal neighborhood offsets (prevents diagonal ridge corner-cutting)
NEIGHBORS_4 = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1)
)

COMPASS_DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _ensure_affine(transform: Any) -> Affine:
    """Ensure the transform is a valid rasterio Affine instance."""
    if transform is None:
        return Affine.identity()
    if isinstance(transform, Affine):
        return transform
    if isinstance(transform, (list, tuple)):
        return Affine(*transform[:6])
    return Affine.identity()


def bresenham_line(r0: int, c0: int, r1: int, c1: int) -> List[Tuple[int, int]]:
    """
    Standard integer Bresenham line algorithm between (r0, c0) and (r1, c1).
    Returns list of (row, col) grid coordinates including start and end points.
    """
    points = []
    dr = abs(r1 - r0)
    dc = abs(c1 - c0)
    sr = 1 if r0 < r1 else -1
    sc = 1 if c0 < c1 else -1
    err = dr - dc
    curr_r, curr_c = r0, c0

    while True:
        points.append((curr_r, curr_c))
        if curr_r == r1 and curr_c == c1:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc
            curr_r += sr
        if e2 < dr:
            err += dr
            curr_c += sc

    return points


def find_critical_entry_height_y(
    dem_96x96: np.ndarray,
    river_pixels: Union[List[Tuple[int, int]], np.ndarray],
    target_rc: Tuple[int, int],
    transform: Optional[Any] = None
) -> Tuple[float, float]:
    """
    8-neighbor Minimax Dijkstra path solver and direct line-of-sight barrier finder.

    Args:
        dem_96x96: 2D numpy array of DEM elevations (meters).
        river_pixels: Iterable/array of (row, col) coordinates belonging to river channel.
        target_rc: (row, col) of the point of interest (e.g. user-clicked coordinate).
        transform: Optional rasterio Affine transform (not strictly needed for elevation, but passed for API symmetry).

    Returns:
        (critical_entry_y, H_direct):
            critical_entry_y: Minimum barrier height on the lowest overtopping path from river to target.
            H_direct: Maximum elevation along straight direct line between nearest river cell and target.
    """
    H, W = dem_96x96.shape
    cy, cx = int(target_rc[0]), int(target_rc[1])

    # Convert river_pixels to list of (r, c)
    if isinstance(river_pixels, np.ndarray) and river_pixels.ndim == 2 and river_pixels.shape == (H, W) and river_pixels.dtype == bool:
        river_coords = [tuple(p) for p in np.argwhere(river_pixels)]
    else:
        river_coords = [(int(p[0]), int(p[1])) for p in river_pixels]

    # Validate target coordinates
    if not (0 <= cy < H and 0 <= cx < W):
        raise ValueError(f"Target coordinates ({cy}, {cx}) outside DEM shape ({H}, {W})")

    # 1. Direct line-of-sight profile
    # Find nearest river pixel to target_rc (Euclidean distance)
    nearest_river_pt = None
    min_dist_sq = float("inf")
    for r, c in river_coords:
        if 0 <= r < H and 0 <= c < W:
            d2 = (r - cy) ** 2 + (c - cx) ** 2
            if d2 < min_dist_sq:
                min_dist_sq = d2
                nearest_river_pt = (r, c)

    if nearest_river_pt is None:
        # Fallback if no valid river pixels in grid
        H_direct = float(dem_96x96[cy, cx])
    else:
        # Trace Bresenham line from nearest river pixel to target
        line_pts = bresenham_line(nearest_river_pt[0], nearest_river_pt[1], cy, cx)
        H_direct = float(max(dem_96x96[r, c] for r, c in line_pts if 0 <= r < H and 0 <= c < W))

    # 2. Minimax Dijkstra path solver
    # Cost to reach a cell is max(cost_to_reach_parent, elev[child])
    # Minimax seeks the path that minimizes this maximum elevation.
    dist = np.full((H, W), np.inf, dtype=np.float32)
    visited = np.zeros((H, W), dtype=bool)
    pq: List[Tuple[float, int, int]] = []
    push = heapq.heappush
    pop = heapq.heappop

    for r, c in river_coords:
        if 0 <= r < H and 0 <= c < W:
            elev = float(dem_96x96[r, c])
            if elev < dist[r, c]:
                dist[r, c] = elev
                pq.append((elev, r, c))

    heapq.heapify(pq)

    critical_entry_y = float("inf")

    while pq:
        d, r, c = pop(pq)

        if r == cy and c == cx:
            critical_entry_y = d
            break

        if visited[r, c]:
            continue
        visited[r, c] = True

        for dr, dc in NEIGHBORS_4:
            nr = r + dr
            nc = c + dc
            if 0 <= nr < H and 0 <= nc < W:
                if not visited[nr, nc]:
                    elev = float(dem_96x96[nr, nc])
                    cost = d if d > elev else elev
                    if cost < dist[nr, nc]:
                        dist[nr, nc] = cost
                        push(pq, (cost, nr, nc))

    if math.isinf(critical_entry_y):
        # Fallback if unreachable
        critical_entry_y = float(dem_96x96[cy, cx])

    return float(critical_entry_y), float(H_direct)


def extract_connected_flood_footprint(
    dem_96x96: np.ndarray,
    river_pixels: Union[List[Tuple[int, int]], np.ndarray],
    predicted_wse_m: float,
    transform: Any
) -> Tuple[List[List[List[float]]], float, np.ndarray]:
    """
    Extracts the hydrologically connected flood footprint for a predicted WSE.

    Args:
        dem_96x96: 2D numpy array of DEM elevations (meters).
        river_pixels: Iterable/array of (row, col) coordinates belonging to river channel.
        predicted_wse_m: Predicted Water Surface Elevation (meters).
        transform: Rasterio Affine transform for pixel-to-geographic coordinate mapping.

    Returns:
        (flood_inundation_polygons, inundated_area_hectares, connected_mask):
            flood_inundation_polygons: List of polygons, each a list of [lat, lon] points for Leaflet.
            inundated_area_hectares: Total inundated area in hectares (30m cell = 0.09 ha).
            connected_mask: 2D boolean mask of shape (H, W) indicating connected inundated cells.
    """
    H, W = dem_96x96.shape
    aff = _ensure_affine(transform)

    # 1. Cells with elevation below or equal to predicted WSE
    below_wse = (dem_96x96 <= predicted_wse_m)

    # 2. 4-connected component labeling (cardinal connectivity)
    structure_4 = np.array([[0, 1, 0],
                            [1, 1, 1],
                            [0, 1, 0]], dtype=int)
    labeled_array, num_features = scipy.ndimage.label(below_wse, structure=structure_4)

    # 3. Identify labels that intersect with river pixels
    if isinstance(river_pixels, np.ndarray) and river_pixels.ndim == 2 and river_pixels.shape == (H, W) and river_pixels.dtype == bool:
        river_coords = [tuple(p) for p in np.argwhere(river_pixels)]
    else:
        river_coords = [(int(p[0]), int(p[1])) for p in river_pixels]

    valid_river_rows = [p[0] for p in river_coords if 0 <= p[0] < H and 0 <= p[1] < W]
    valid_river_cols = [p[1] for p in river_coords if 0 <= p[0] < H and 0 <= p[1] < W]

    if valid_river_rows:
        river_labels = np.unique(labeled_array[valid_river_rows, valid_river_cols])
        river_labels = river_labels[river_labels > 0]  # Exclude background 0
    else:
        river_labels = np.array([], dtype=int)

    # 4. Connected mask: only features connected to the river
    connected_mask = (labeled_array > 0) & np.isin(labeled_array, river_labels)

    # Inundated area in hectares: 1 pixel (30m x 30m) = 900 m^2 = 0.09 hectares
    inundated_area_hectares = float(np.sum(connected_mask) * 900.0 / 10000.0)

    # 5. Extract outer boundary contours via OpenCV
    mask_u8 = (connected_mask.astype(np.uint8) * 255)
    res = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = res[0] if len(res) == 2 else res[1]

    flood_inundation_polygons: List[List[List[float]]] = []

    for cnt in contours:
        # Filter out tiny contours (< 4 pixels or < 4 area)
        if len(cnt) < 4 or cv2.contourArea(cnt) < 4.0:
            continue

        approx = cv2.approxPolyDP(cnt, epsilon=1.0, closed=True)
        if len(approx) < 3:
            continue

        polygon_ring: List[List[float]] = []
        for pt in approx:
            col_px, row_px = float(pt[0][0]), float(pt[0][1])
            # In rasterio: aff * (col + 0.5, row + 0.5) -> (x_lon, y_lat) at pixel center
            x_lon, y_lat = aff * (col_px + 0.5, row_px + 0.5)
            polygon_ring.append([round(float(y_lat), 6), round(float(x_lon), 6)])

        # Ensure ring is closed
        if polygon_ring and polygon_ring[0] != polygon_ring[-1]:
            polygon_ring.append(polygon_ring[0])

        if len(polygon_ring) >= 4:
            flood_inundation_polygons.append(polygon_ring)

    return flood_inundation_polygons, inundated_area_hectares, connected_mask


def find_nearest_safe_high_ground(
    dem_96x96: np.ndarray,
    connected_flood_mask: np.ndarray,
    target_rc: Tuple[int, int],
    predicted_wse_m: float,
    transform: Any
) -> Optional[Dict[str, Any]]:
    """
    Finds the nearest accessible, safe high ground for flood evacuation.

    Criteria:
      - Elevation >= predicted_wse_m + 3.0 m (substantial dry clearance).
      - Gentle slope < 8.0 degrees (walkable, safe from landslides/scour).
      - Outside connected flood footprint (~connected_flood_mask).
      - Tiered relaxation if strict criteria cannot be met.

    Args:
        dem_96x96: 2D numpy array of DEM elevations (meters).
        connected_flood_mask: 2D boolean mask of flooded cells.
        target_rc: (row, col) of the reference point (e.g. user location).
        predicted_wse_m: Predicted Water Surface Elevation (meters).
        transform: Rasterio Affine transform for pixel-to-geographic mapping.

    Returns:
        Dict with keys:
            - lat: float (rounded to 6 decimals)
            - lon: float (rounded to 6 decimals)
            - distance_m: float (meters to safe point, rounded to 0 decimals)
            - bearing: str (8-point compass bearing, e.g. "NE")
            - elevation_m: float (ground elevation, rounded to 1 decimal)
            - clearance_above_wse_m: float (dry elevation above WSE, rounded to 1 decimal)
        Or None if no safe ground is found.
    """
    H, W = dem_96x96.shape
    aff = _ensure_affine(transform)
    cy, cx = int(target_rc[0]), int(target_rc[1])

    # 1. Slope computation in degrees
    # 30m cell resolution: dy, dx gradient with 30m spacing
    dy, dx = np.gradient(dem_96x96, 30.0, 30.0)
    slope_deg = np.degrees(np.arctan(np.hypot(dx, dy)))

    not_flooded = ~connected_flood_mask

    # Restrict candidate safe high ground to the same dry connected component as target_rc (if target_rc is dry)
    # This prevents suggesting high ground on the opposite side of a flooded river reach
    structure_4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int)
    if 0 <= cy < H and 0 <= cx < W and not_flooded[cy, cx]:
        dry_labeled, _ = scipy.ndimage.label(not_flooded, structure=structure_4)
        target_dry_label = dry_labeled[cy, cx]
        dry_candidate_mask = (dry_labeled == target_dry_label) if target_dry_label > 0 else not_flooded
    else:
        dry_candidate_mask = not_flooded

    # 2. Tiered search for safe ground within dry-reachable candidates
    # Tier 1: Optimal safe high ground (>= +3.0m clearance, slope < 8 deg)
    safe_mask = (dem_96x96 >= predicted_wse_m + 3.0) & (slope_deg < 8.0) & dry_candidate_mask

    # Tier 2: Relaxed slope (< 15 deg)
    if not np.any(safe_mask):
        safe_mask = (dem_96x96 >= predicted_wse_m + 3.0) & (slope_deg < 15.0) & dry_candidate_mask

    # Tier 3: Relaxed clearance (>= +1.5m clearance, slope < 15 deg)
    if not np.any(safe_mask):
        safe_mask = (dem_96x96 >= predicted_wse_m + 1.5) & (slope_deg < 15.0) & dry_candidate_mask

    # Tier 4: Minimal clearance (>= +0.5m clearance, slope < 25 deg)
    if not np.any(safe_mask):
        safe_mask = (dem_96x96 >= predicted_wse_m + 0.5) & (slope_deg < 25.0) & dry_candidate_mask

    # If no safe pixels found in any tier
    if not np.any(safe_mask):
        return None

    # 3. Find closest safe pixel to target_rc by Euclidean distance
    safe_indices = np.argwhere(safe_mask)
    dist_sq = (safe_indices[:, 0] - cy) ** 2 + (safe_indices[:, 1] - cx) ** 2
    best_idx = int(np.argmin(dist_sq))
    r_safe = int(safe_indices[best_idx, 0])
    c_safe = int(safe_indices[best_idx, 1])

    dist_m = float(math.hypot(r_safe - cy, c_safe - cx) * 30.0)

    # 4. Transform to geographic coordinates at pixel centers
    safe_lon, safe_lat = aff * (c_safe + 0.5, r_safe + 0.5)
    target_lon, target_lat = aff * (cx + 0.5, cy + 0.5)

    # 5. Compute 8-point compass bearing from target to safe point
    d_lon = safe_lon - target_lon
    d_lat = safe_lat - target_lat

    # Convert geographic offsets to approx meters
    dx_m = d_lon * 111320.0 * math.cos(math.radians(target_lat))
    dy_m = d_lat * 111320.0

    if abs(dx_m) < 1e-3 and abs(dy_m) < 1e-3:
        bearing = "N"
    else:
        angle_deg = (math.degrees(math.atan2(dx_m, dy_m)) + 360.0) % 360.0
        bearing_idx = int((angle_deg + 22.5) // 45) % 8
        bearing = COMPASS_DIRECTIONS[bearing_idx]

    safe_elev = float(dem_96x96[r_safe, c_safe])
    clearance = float(safe_elev - predicted_wse_m)

    return {
        "lat": round(safe_lat, 6),
        "lon": round(safe_lon, 6),
        "distance_m": round(dist_m, 0),
        "bearing": bearing,
        "elevation_m": round(safe_elev, 1),
        "clearance_above_wse_m": round(clearance, 1)
    }
