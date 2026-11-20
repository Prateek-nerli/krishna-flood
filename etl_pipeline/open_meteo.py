import os
import time
import requests
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _create_robust_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

_SESSION = _create_robust_session()

# In-memory cache for live basin telemetry (15-minute TTL)
_BASIN_CACHE = {
    "timestamp": 0.0,
    "data": None
}

def _safe_get(url: str, params: dict, timeout: int = 25) -> requests.Response:
    for attempt in range(3):
        try:
            resp = _SESSION.get(url, params=params, verify=True, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))

def get_live_river_discharge(lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetch live and near-term GloFAS discharge for a single station.
    """
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge,river_discharge_mean,river_discharge_median,river_discharge_max,river_discharge_p25,river_discharge_p75",
        "past_days": 14,
        "forecast_days": 7
    }
    return _safe_get(url, params, timeout=20).json()

def get_historical_river_discharge(lat: float, lon: float, start_date: str = "1995-01-01", end_date: str = "2026-09-25") -> Dict[str, Any]:
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "river_discharge"
    }
    return _safe_get(url, params, timeout=60).json()

def get_live_catchment_data(lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetch live catchment meteorological variables (precip, soil moisture, et0).
    Note: et0_fao_evapotranspiration is queried under daily to prevent HTTP 400.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum,rain_sum,et0_fao_evapotranspiration",
        "hourly": "precipitation,rain,soil_moisture_0_to_7cm,soil_moisture_7_to_28cm",
        "past_days": 14,
        "forecast_days": 3,
        "timezone": "Asia/Kolkata"
    }
    return _safe_get(url, params, timeout=20).json()

def get_historical_catchment_data(lat: float, lon: float, start_date: str = "1995-01-01", end_date: str = "2026-09-25") -> Dict[str, Any]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum,rain_sum,et0_fao_evapotranspiration"
    }
    return _safe_get(url, params, timeout=60).json()

def _fetch_flood_batch(coords: List[tuple]) -> List[Dict[str, Any]]:
    """Fetch flood data for a batch of (lat, lon) coordinates."""
    lats = ",".join(f"{c[0]:.4f}" for c in coords)
    lons = ",".join(f"{c[1]:.4f}" for c in coords)
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lats,
        "longitude": lons,
        "daily": "river_discharge,river_discharge_mean,river_discharge_max",
        "past_days": 14,
        "forecast_days": 7
    }
    resp = _safe_get(url, params, timeout=25)
    data = resp.json()
    if isinstance(data, list):
        return data
    return [data]

def _fetch_weather_batch(coords: List[tuple]) -> List[Dict[str, Any]]:
    """Fetch catchment weather data for a batch of (lat, lon) coordinates."""
    lats = ",".join(f"{c[0]:.4f}" for c in coords)
    lons = ",".join(f"{c[1]:.4f}" for c in coords)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "daily": "precipitation_sum,rain_sum,et0_fao_evapotranspiration",
        "past_days": 14,
        "forecast_days": 3,
        "timezone": "Asia/Kolkata"
    }
    resp = _safe_get(url, params, timeout=25)
    data = resp.json()
    if isinstance(data, list):
        return data
    return [data]

def get_live_basin_matrix(nodes_list: List[Dict[str, Any]], force_refresh: bool = False) -> Dict[str, Any]:
    """
    Fetches real-time GloFAS discharge and Open-Meteo catchment meteorology
    for all 27 basin nodes in parallel with ThreadPoolExecutor and 15-minute TTL caching.
    Uses glofas_lat/glofas_lon for flood discharge, and lat/lon for weather.
    """
    global _BASIN_CACHE
    now = time.time()
    if not force_refresh and _BASIN_CACHE["data"] is not None and (now - _BASIN_CACHE["timestamp"] < 900):
        return _BASIN_CACHE["data"]

    num_nodes = len(nodes_list)
    half = (num_nodes + 1) // 2

    # GloFAS coordinates (snapped mainstem)
    flood_coords = [(n.get('glofas_lat', n['lat']), n.get('glofas_lon', n['lon'])) for n in nodes_list]
    # Physical station coordinates (weather/catchment)
    weather_coords = [(n['lat'], n['lon']) for n in nodes_list]

    batch1_flood = flood_coords[:half]
    batch2_flood = flood_coords[half:]
    batch1_weather = weather_coords[:half]
    batch2_weather = weather_coords[half:]

    with ThreadPoolExecutor(max_workers=4) as executor:
        f1_future = executor.submit(_fetch_flood_batch, batch1_flood)
        f2_future = executor.submit(_fetch_flood_batch, batch2_flood)
        w1_future = executor.submit(_fetch_weather_batch, batch1_weather)
        w2_future = executor.submit(_fetch_weather_batch, batch2_weather)

        flood_res = f1_future.result() + f2_future.result()
        weather_res = w1_future.result() + w2_future.result()

    basin_data = {
        "timestamp": now,
        "nodes": {}
    }

    for i, node in enumerate(nodes_list):
        nid = node['node_id']
        f_entry = flood_res[i] if i < len(flood_res) else {}
        w_entry = weather_res[i] if i < len(weather_res) else {}

        f_daily = f_entry.get("daily", {})
        w_daily = w_entry.get("daily", {})

        basin_data["nodes"][nid] = {
            "node_id": nid,
            "station_name": node.get("station_name", nid),
            "dates": f_daily.get("time", []),
            "discharge": f_daily.get("river_discharge", []),
            "discharge_mean": f_daily.get("river_discharge_mean", []),
            "discharge_max": f_daily.get("river_discharge_max", []),
            "weather_dates": w_daily.get("time", []),
            "precip_sum": w_daily.get("precipitation_sum", []),
            "et0": w_daily.get("et0_fao_evapotranspiration", [])
        }

    _BASIN_CACHE["timestamp"] = now
    _BASIN_CACHE["data"] = basin_data
    return basin_data

if __name__ == "__main__":
    import json
    with open("seed_nodes.json", "r") as f:
        nodes = json.load(f)
    print(f"Testing live basin matrix for {len(nodes)} nodes (multi-threaded batched fetch)...")
    t0 = time.time()
    matrix = get_live_basin_matrix(nodes, force_refresh=True)
    t1 = time.time()
    print(f"Fetch completed in {t1 - t0:.2f}s! Node count: {len(matrix['nodes'])}")
    for test_id in ["SANGLI", "ALMATTI", "SRISAILAM", "PRAKASAM"]:
        item = matrix['nodes'].get(test_id, {})
        dis = item.get('discharge', [])
        p = item.get('precip_sum', [])
        print(f"  [{test_id}] discharge len={len(dis)}, latest_3={dis[-3:] if len(dis)>=3 else dis}, precip len={len(p)}")
