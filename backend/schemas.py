from pydantic import BaseModel
from typing import Optional, Dict

class PredictRequest(BaseModel):
    latitude: float
    longitude: float
    custom_dam_outflow_override: Optional[Dict[str, float]] = None

class ForecastDay(BaseModel):
    predicted_river_depth_m: float
    predicted_wse_m: float
    slope_runoff_accumulation_m: float
    will_submerge: bool
    submersion_depth_m: float
    safety_margin_m: float
    risk_level: str
    
    predicted_discharge_cusecs: float = 0.0
    predicted_discharge_cumecs: float = 0.0
    upstream_inflow_cusecs: float = 0.0
    upstream_outflow_cusecs: float = 0.0
    bankfull_capacity_cusecs: float = 0.0
    wse_feet: float = 0.0
    ground_elevation_feet: float = 0.0

class LiveTelemetry(BaseModel):
    fetched_at_utc: str
    data_provider: str
    upstream_station: str
    downstream_station: str
    current_flow_cusecs: float
    current_flow_cumecs: float
    current_river_depth_m: float
    current_river_depth_ft: float
    current_wse_msl_m: float
    current_wse_msl_ft: float
    ground_clearance_m: float
    is_bankfull: bool
    bankfull_threshold_cusecs: float
    raw_telemetry_source_url: str

class PredictResponse(BaseModel):
    ground_elevation_m: float
    nearest_river_point: str
    distance_to_river_m: float
    river_bed_elevation_m: float
    hand_m: float
    reach_fraction_alpha: float
    local_slope_degrees: float
    current_state: str
    forecasts: Dict[str, ForecastDay]
    is_water_body: bool = False
    water_body_name: Optional[str] = None
    live_telemetry: Optional[LiveTelemetry] = None

class SimulateReleaseRequest(BaseModel):
    node_id: str
    outflow_cusecs: float
