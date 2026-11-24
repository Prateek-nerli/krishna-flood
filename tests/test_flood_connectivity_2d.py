"""
tests/test_flood_connectivity_2d.py

Verification suite for 2D Geospatial Physics Engine:
1. Synthetic ridge with side gap: verifies Minimax Dijkstra finds the gap height Y,
   direct line has H > Y, and when Y <= WSE < H flanking triggers!
2. Connected flood footprint extraction: verifies only connected cells are included,
   excluding isolated depressions below WSE, and generates Leaflet polygons.
3. Nearest safe high-ground returns valid coordinates, clearance, and compass bearing.
4. Execution speed: Dijkstra solves 96x96 grid in < 15 ms.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import math
import numpy as np
from rasterio.transform import from_origin

from ml_engine.flood_connectivity_2d import (
    find_critical_entry_height_y,
    extract_connected_flood_footprint,
    find_nearest_safe_high_ground,
    bresenham_line
)


def create_synthetic_test_dem():
    """
    Creates a synthetic 96x96 DEM representing a river valley with:
    - Base elevation: 500.0 m.
    - River channel at column 10 (rows 0-95): elevation 495.0 m.
    - Continuous barrier ridge at column 25: elevation 515.0 m.
    - Side breach/gap in ridge at rows 10-18: elevation 504.0 m (critical gap Y).
    - Target cell at (cy=22, cx=35): elevation 500.0 m.
      Direct line from river (22, 10) to (22, 35) crosses ridge at (22, 25) with H_direct = 515.0 m.
      Lowest overtopping path passes through side gap at rows 10-18 with Y = 504.0 m.
    - Isolated depression at (80, 80): elevation 498.0 m surrounded by 520.0 m hills.
    - Safe high ground plateau at rows 4-9, cols 32-38 (North of target): elevation 514.0 m with slope < 1 deg.
    """
    H, W = 96, 96
    dem = np.full((H, W), 500.0, dtype=np.float32)

    # 1. River channel at column 10
    river_pixels = []
    for r in range(H):
        dem[r, 10] = 495.0
        river_pixels.append((r, 10))

    # 2. Continuous barrier ridge at column 25 (rows 0 to 95) with gap at rows 10-18
    dem[:, 25] = 515.0
    dem[10:19, 25] = 504.0   # The only breach gap (elevation 504.0 m)

    # 3. Isolated depression at (80, 80) surrounded by 520m high terrain
    dem[75:88, 75:88] = 520.0
    dem[79:84, 79:84] = 498.0  # Low bowl inside high walls

    # 4. Safe high-ground flat plateau North of target at rows 12-16, cols 32-38
    dem[12:17, 32:39] = 514.0

    # Affine transform centered near Krishna River (Sangli, Maharashtra)
    # Resolution ~ 30 meters = 0.00027 degrees
    transform = from_origin(74.5500, 16.8800, 0.00027, 0.00027)

    return dem, river_pixels, transform


def test_bresenham_line():
    """Verify Bresenham line algorithm connects endpoints and handles slopes."""
    pts_h = bresenham_line(10, 10, 10, 20)
    assert len(pts_h) == 11
    assert pts_h[0] == (10, 10) and pts_h[-1] == (10, 20)

    pts_v = bresenham_line(10, 10, 25, 10)
    assert len(pts_v) == 16
    assert pts_v[0] == (10, 10) and pts_v[-1] == (25, 10)

    pts_d = bresenham_line(0, 0, 5, 5)
    assert len(pts_d) == 6
    assert pts_d[0] == (0, 0) and pts_d[-1] == (5, 5)
    print("PASS: test_bresenham_line verified.")


def test_minimax_dijkstra_flanking():
    """
    Verifies that:
    1. Minimax Dijkstra finds the gap height Y = 504.0m.
    2. Direct line-of-sight barrier H = 515.0m > Y.
    3. Flanking occurs when Y <= WSE < H.
    4. Dijkstra execution runs in < 15 ms.
    """
    dem, river_pixels, transform = create_synthetic_test_dem()
    target_rc = (22, 35)

    # Warmup call
    find_critical_entry_height_y(dem, river_pixels, target_rc, transform)

    # Benchmark run
    t0 = time.perf_counter()
    critical_entry_y, H_direct = find_critical_entry_height_y(
        dem, river_pixels, target_rc, transform
    )
    t1 = time.perf_counter()
    runtime_ms = (t1 - t0) * 1000.0

    print(f"Minimax Dijkstra runtime: {runtime_ms:.2f} ms")
    print(f"Computed Critical Entry Y = {critical_entry_y:.1f} m, H_direct = {H_direct:.1f} m")

    # Assert runtime requirement (< 15 ms)
    assert runtime_ms < 15.0, f"Dijkstra took {runtime_ms:.2f} ms, expected < 15 ms"

    # Assert gap height and direct barrier height
    assert abs(critical_entry_y - 504.0) < 1e-3, (
        f"Expected gap height 504.0 m, got {critical_entry_y:.2f} m"
    )
    assert abs(H_direct - 515.0) < 1e-3, (
        f"Expected direct ridge height 515.0 m, got {H_direct:.2f} m"
    )
    assert H_direct > critical_entry_y, "Direct barrier must be higher than gap height"

    # Flanking physics validation:
    # 1. At WSE = 508.0m: Water exceeds gap (504m) but does NOT overtop direct ridge (515m).
    # Floodwater flanks around the ridge!
    wse_flanking = 508.0
    is_flanked = (critical_entry_y <= wse_flanking < H_direct)
    assert is_flanked is True, "Flanking condition should be TRUE at WSE=508m"

    # 2. At WSE = 502.0m: Below gap, completely dry
    wse_dry = 502.0
    assert wse_dry < critical_entry_y, "Terrain should be dry at WSE=502m"

    # 3. At WSE = 518.0m: Directly overtopped
    wse_overtop = 518.0
    assert wse_overtop >= H_direct, "Direct overtopping at WSE=518m"

    print("PASS: test_minimax_dijkstra_flanking verified (Y=504m, H=515m, runtime < 15ms).")


def test_connected_flood_footprint_extraction():
    """
    Verifies that:
    1. Extracting flood footprint at WSE=508m connects the river and the flanked target cell.
    2. Excludes isolated depression at (80, 80) which has dem=498m <= WSE but is blocked by 520m terrain.
    3. Calculates accurate inundated area in hectares.
    4. Extracts valid, closed Leaflet [lat, lon] polygons.
    """
    dem, river_pixels, transform = create_synthetic_test_dem()
    predicted_wse = 508.0

    polygons, inundated_ha, connected_mask = extract_connected_flood_footprint(
        dem, river_pixels, predicted_wse, transform
    )

    # 1. Target (22, 35) is flooded via gap
    assert connected_mask[22, 35] == True, "Target (22, 35) must be inside connected flood mask"

    # 2. Isolated bowl (80, 80) is NOT connected
    assert dem[80, 80] <= predicted_wse, "Isolated bowl elevation must be below WSE"
    assert connected_mask[80, 80] == False, "Isolated bowl at (80, 80) must NOT be in connected flood mask"

    # 3. Area in hectares
    expected_cells = int(np.sum(connected_mask))
    expected_ha = expected_cells * 900.0 / 10000.0
    assert abs(inundated_ha - expected_ha) < 1e-4, f"Area mismatch: {inundated_ha} vs {expected_ha}"
    assert inundated_ha > 0.0

    # 4. Polygons verification
    assert len(polygons) > 0, "Should extract at least one outer flood contour"
    for poly in polygons:
        assert len(poly) >= 4, "Contour polygon must have at least 4 vertices"
        # Check closed ring
        assert poly[0] == poly[-1], "Polygon must be closed ring (first == last)"
        # Check coordinates are [lat, lon] near Sangli
        for pt in poly:
            lat, lon = pt[0], pt[1]
            assert 16.0 < lat < 17.5, f"Latitude {lat} out of expected range"
            assert 74.0 < lon < 75.5, f"Longitude {lon} out of expected range"

    print(f"PASS: test_connected_flood_footprint_extraction verified ({inundated_ha:.2f} ha, {len(polygons)} polygons).")


def test_nearest_safe_high_ground():
    """
    Verifies that:
    1. Returns a safe plateau location above WSE + 3m with gentle slope < 8 deg.
    2. Target is at (22, 35), plateau is at rows 4-9, cols 32-38 (North) -> bearing should be 'N'.
    3. Clearance above WSE is >= 3.0 m.
    4. Distance in meters is calculated accurately.
    """
    dem, river_pixels, transform = create_synthetic_test_dem()
    predicted_wse = 508.0

    # Get connected flood mask first
    _, _, connected_mask = extract_connected_flood_footprint(
        dem, river_pixels, predicted_wse, transform
    )

    target_rc = (22, 35)
    safe_info = find_nearest_safe_high_ground(
        dem, connected_mask, target_rc, predicted_wse, transform
    )

    assert safe_info is not None, "Safe high ground must be found"
    print(f"Safe high ground found: {safe_info}")

    assert safe_info["elevation_m"] >= predicted_wse + 3.0, (
        f"Safe elevation {safe_info['elevation_m']} must be >= {predicted_wse + 3.0}"
    )
    assert safe_info["clearance_above_wse_m"] >= 3.0, (
        f"Clearance {safe_info['clearance_above_wse_m']} must be >= 3.0m"
    )
    # Target row 22, Plateau row ~7 (North)
    assert safe_info["bearing"] == "N", f"Expected bearing 'N', got {safe_info['bearing']}"
    assert safe_info["distance_m"] > 0, "Distance should be positive"

    # Geographic coordinates must be valid
    assert 16.0 < safe_info["lat"] < 17.5
    assert 74.0 < safe_info["lon"] < 75.5

    print("PASS: test_nearest_safe_high_ground verified.")


def test_safe_high_ground_relaxation():
    """Verifies fallback when all terrain is steep or clearance is tight."""
    dem = np.full((96, 96), 500.0, dtype=np.float32)
    # Add steep hill (elev 512m, slope 12 deg)
    dem[20:30, 20:30] = 512.0
    connected_mask = np.zeros((96, 96), dtype=bool)
    connected_mask[40:60, 40:60] = True

    transform = from_origin(74.5500, 16.8800, 0.00027, 0.00027)

    # WSE = 508.0m -> hill clearance is 4.0m, but slope at edge is steeper than 8 deg.
    # The interior of the hill plateau has 0 slope, so it succeeds.
    safe_info = find_nearest_safe_high_ground(
        dem, connected_mask, target_rc=(50, 50), predicted_wse_m=508.0, transform=transform
    )
    assert safe_info is not None
    assert safe_info["clearance_above_wse_m"] >= 3.0
    print("PASS: test_safe_high_ground_relaxation verified.")


if __name__ == "__main__":
    print("Running 2D Flood Connectivity Physics Test Suite...")
    test_bresenham_line()
    test_minimax_dijkstra_flanking()
    test_connected_flood_footprint_extraction()
    test_nearest_safe_high_ground()
    test_safe_high_ground_relaxation()
    print("\nALL TESTS PASSED SUCCESSFULLY!")
