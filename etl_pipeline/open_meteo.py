import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_live_river_discharge(lat, lon):
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge,river_discharge_mean,river_discharge_median,river_discharge_max,river_discharge_p25,river_discharge_p75",
        "past_days": 14,
        "forecast_days": 7
    }
    response = requests.get(url, params=params, verify=False)
    response.raise_for_status()
    return response.json()

def get_historical_river_discharge(lat, lon, start_date="1995-01-01", end_date="2026-09-25"):
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "river_discharge"
    }
    response = requests.get(url, params=params, verify=False)
    response.raise_for_status()
    return response.json()

def get_live_catchment_data(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation,rain,surface_runoff,soil_moisture_0_to_7cm,soil_moisture_7_to_28cm,et0_fao_evapotranspiration",
        "past_days": 7,
        "forecast_days": 3,
        "timezone": "Asia/Kolkata"
    }
    response = requests.get(url, params=params, verify=False)
    response.raise_for_status()
    return response.json()

def get_historical_catchment_data(lat, lon, start_date="1995-01-01", end_date="2026-09-25"):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum,rain_sum,et0_fao_evapotranspiration"
    }
    response = requests.get(url, params=params, verify=False)
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    # Test for Almatti Dam coords
    lat, lon = 16.3306, 75.8869
    print("Testing Open-Meteo Live River Discharge API...")
    data = get_live_river_discharge(lat, lon)
    print(f"Data received: {list(data.keys())}")
    if "daily" in data:
        print(f"Available daily variables: {list(data['daily'].keys())}")
