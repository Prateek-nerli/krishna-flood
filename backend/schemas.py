from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class PredictRequest(BaseModel):
    latitude: float
    longitude: float
    custom_dam_outflow_override: Optional[Dict[str, float]] = None
    release_duration_hours: Optional[float] = 24.0
    trigger_registered_alerts: bool = False

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
    glofas_baseline_cusecs: float = 0.0
    ml_predicted_cusecs: float = 0.0
    upstream_inflow_cusecs: float = 0.0
    upstream_outflow_cusecs: float = 0.0
    bankfull_capacity_cusecs: float = 0.0
    channel_bankfull_cusecs: float = 0.0
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
    uparea: Optional[float] = None
    wris_river_name: Optional[str] = None
    wris_data: Optional[Dict[str, Any]] = None
    xai_attribution: Optional[Dict[str, Any]] = None
    hydraulic_diagnostics: Optional[Dict[str, Any]] = None
    flood_inundation_polygons: Optional[List[List[List[float]]]] = None
    inundated_area_hectares: float = 0.0
    critical_entry_height_y_m: Optional[float] = None
    direct_ridge_height_h_m: Optional[float] = None
    indirect_flanking: bool = False
    barrier_protected: bool = False
    drainage_lock_active: bool = False
    nearest_safe_high_ground: Optional[Dict[str, Any]] = None
    auto_alert_summary: Optional[Dict[str, Any]] = None
    model_info: Optional[Dict[str, Any]] = None

class SimulateReleaseRequest(BaseModel):
    node_id: str
    outflow_cusecs: float

# ==========================================
# AUTH, CITIZEN DASHBOARD & ADMIN SCHEMAS
# ==========================================

class UserRegisterRequest(BaseModel):
    full_name: str
    phone: str
    email: str
    password: str
    alert_language: Optional[str] = "bilingual"

class VerifyOTPRequest(BaseModel):
    email: str
    phone_otp: str
    email_otp: str

class ResendOTPRequest(BaseModel):
    email: str
    channel: Optional[str] = "all"  # "sms", "email", or "all"

class LoginRequest(BaseModel):
    identifier: str  # email or phone
    password: str

class UserLocationCreate(BaseModel):
    label: str = "Home"
    latitude: float
    longitude: float
    place_name: str
    is_primary: bool = False

class UserLocationUpdate(BaseModel):
    label: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_name: Optional[str] = None
    is_primary: Optional[bool] = None

class UserPreferencesUpdate(BaseModel):
    alert_language: Optional[str] = None
    sms_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None

class TestAlertRequest(BaseModel):
    location_id: Optional[str] = None

class SimulateAndAlertRequest(BaseModel):
    custom_dam_outflow_override: Dict[str, float]
    release_duration_hours: float = 24.0
    target_mode: str = "admin_only"  # "admin_only", "selected_user", "all_impacted"
    target_user_id: Optional[str] = None
    send_alerts: bool = False

class AdminBroadcastRequest(BaseModel):
    message_en: str
    message_kn: str
    filter_type: str = "ALL"  # "ALL", "RIVER", "DAM"
    filter_value: Optional[str] = None

