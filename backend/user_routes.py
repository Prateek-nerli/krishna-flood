import os
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.db_models import User, UserLocation, AlertLog
from backend.schemas import (
    UserLocationCreate,
    UserLocationUpdate,
    UserPreferencesUpdate,
    TestAlertRequest,
    PredictRequest,
)
from backend.auth import get_current_user
from backend.notifications import send_sms, send_email, format_flood_alert
from gis_engine.raster_lookup import haversine
from etl_pipeline.open_meteo import get_live_basin_matrix

user_router = APIRouter(prefix="/api/v1/user", tags=["Citizen User Dashboard"])

# Reference to prediction runner
_predict_runner = None
_nodes_cache = None


def set_predict_runner(runner_fn, nodes_list):
    global _predict_runner, _nodes_cache
    _predict_runner = runner_fn
    _nodes_cache = nodes_list


def is_in_basin_bbox(lat: float, lon: float) -> bool:
    """Bounding envelope for the entire Krishna Basin."""
    return 13.0 <= lat <= 19.8 and 73.0 <= lon <= 81.8


def fetch_live_weather(lat: float, lon: float) -> Dict[str, Any]:
    """
    Fetches real-time temperature, current rainfall, 5-day antecedent P5,
    AMC Class, and 3-day daily rainfall forecast from Open-Meteo.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat:.4f}&longitude={lon:.4f}&"
        f"current=temperature_2m,precipitation&"
        f"daily=precipitation_sum&past_days=5&forecast_days=3"
    )
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            curr = data.get("current", {})
            daily = data.get("daily", {})
            precip_list = daily.get("precipitation_sum", [])
            # indices 0..4 are past 5 days; index 5 is today; indices 6..8 are Day 1, 2, 3 forecast
            p5 = sum(precip_list[:5]) if len(precip_list) >= 5 else 12.0
            forecast_3d = precip_list[5:8] if len(precip_list) >= 8 else [0.0, 0.0, 0.0]

            if p5 < 15.0:
                amc = "AMC-I (Dry Soil / High Infiltration)"
            elif p5 <= 40.0:
                amc = "AMC-II (Normal Moisture / Standard Runoff)"
            else:
                amc = "AMC-III (Saturated Soil / Flash Flood Risk)"

            return {
                "temperature_c": round(float(curr.get("temperature_2m", 28.5)), 1),
                "current_rain_mm": round(float(curr.get("precipitation", 0.0)), 1),
                "p5_antecedent_mm": round(float(p5), 1),
                "amc_class": amc,
                "daily_forecast_rain_mm": [round(float(x), 1) for x in forecast_3d],
            }
    except Exception as e:
        print(f"[UserDashboard] Open-Meteo Weather fallback: {e}")

    return {
        "temperature_c": 28.0,
        "current_rain_mm": 0.0,
        "p5_antecedent_mm": 15.0,
        "amc_class": "AMC-II (Normal)",
        "daily_forecast_rain_mm": [0.0, 0.0, 0.0],
    }


def find_nearby_reservoirs(lat: float, lon: float, up_node_id: str, down_node_id: str) -> Dict[str, Any]:
    """
    Finds nearest upstream and downstream reservoirs along the Krishna Basin network.
    """
    if not _nodes_cache:
        return {"upstream": None, "downstream": None}

    # Upstream candidates
    reservoirs = [n for n in _nodes_cache if n.get("node_type") == "RESERVOIR"]

    # Match upstream node or closest upstream reservoir
    up_res = next((r for r in reservoirs if r["node_id"] == up_node_id), None)
    if not up_res:
        # Find closest reservoir by geographic distance that is upstream in latitude/longitude
        candidates = sorted(reservoirs, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))
        up_res = candidates[0] if candidates else None

    # Downstream candidate: find nearest downstream reservoir or barrage
    down_candidates = [
        n for n in _nodes_cache
        if n.get("node_type") in ["RESERVOIR", "BARRAGE", "GAUGE"] and n["node_id"] != (up_res["node_id"] if up_res else "")
    ]
    down_res = next((r for r in down_candidates if r["node_id"] == down_node_id), None)
    if not down_res:
        down_res = next((r for r in down_candidates if r.get("node_type") == "RESERVOIR"), None)

    # Fetch live discharge/inflow
    basin_data = get_live_basin_matrix(_nodes_cache)
    today_idx = 14

    def format_res_card(r):
        if not r:
            return None
        nid = r["node_id"]
        node_data = basin_data.get("nodes", {}).get(nid, {})
        dis_list = node_data.get("discharge", [120.0] * 21)
        curr_flow_cfs = round(float(dis_list[min(today_idx, len(dis_list) - 1)]), 0)

        frl = float(r.get("danger_frl_m", 500.0) or 500.0)
        bed = float(r.get("bed_elev_m", 450.0) or 450.0)
        # Approximate live water level & percentage
        fill_pct = min(98.0, max(35.0, round(50.0 + (curr_flow_cfs / 50000.0) * 40.0, 1)))
        est_level_m = round(bed + (frl - bed) * (fill_pct / 100.0), 2)

        return {
            "node_id": nid,
            "station_name": r.get("station_name", nid),
            "node_type": r.get("node_type", "RESERVOIR"),
            "gross_cap_tmc": float(r.get("gross_cap_tmc", 0.0) or 0.0),
            "danger_frl_m": frl,
            "bed_elev_m": bed,
            "current_flow_cfs": curr_flow_cfs,
            "estimated_level_m": est_level_m,
            "fill_percentage": fill_pct,
            "distance_km": round(haversine(lat, lon, r["lat"], r["lon"]) / 1000.0, 1),
        }

    return {
        "upstream": format_res_card(up_res),
        "downstream": format_res_card(down_res),
    }


@user_router.get("/locations")
def get_user_locations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    locs = (
        db.query(UserLocation)
        .filter(UserLocation.user_id == current_user.id)
        .order_by(UserLocation.is_primary.desc(), UserLocation.created_at.asc())
        .all()
    )
    return {
        "locations": [
            {
                "id": loc.id,
                "label": loc.label,
                "latitude": loc.latitude,
                "longitude": loc.longitude,
                "place_name": loc.place_name,
                "is_primary": loc.is_primary,
                "last_risk_level": loc.last_risk_level,
                "last_checked_at": loc.last_checked_at.isoformat() if loc.last_checked_at else None,
                "last_flood_days": json.loads(loc.last_flood_days_json) if loc.last_flood_days_json else [],
            }
            for loc in locs
        ]
    }


@user_router.post("/locations")
def add_user_location(
    req: UserLocationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. Limit to max 3 locations
    count = db.query(UserLocation).filter(UserLocation.user_id == current_user.id).count()
    if count >= 3:
        raise HTTPException(
            status_code=400,
            detail="Maximum of 3 locations allowed per account. Please remove an existing location first.",
        )

    # 2. Check Krishna Basin envelope
    if not is_in_basin_bbox(req.latitude, req.longitude):
        raise HTTPException(
            status_code=400,
            detail="Coordinate is outside the monitored Krishna River Basin (Karnataka, Maharashtra, Andhra Pradesh/Telangana).",
        )

    # 3. Handle primary status
    is_primary = req.is_primary or (count == 0)
    if is_primary:
        db.query(UserLocation).filter(UserLocation.user_id == current_user.id).update(
            {"is_primary": False}
        )

    # 4. Initial quick flood evaluation
    initial_risk = "SAFE"
    flood_days = []
    if _predict_runner:
        try:
            pred = _predict_runner(PredictRequest(latitude=req.latitude, longitude=req.longitude))
            for h_name, f_obj in pred.forecasts.items():
                if f_obj.will_submerge:
                    day_label = "Day 1 (+24h)" if h_name.startswith("day_1") else ("Day 2 (+48h)" if h_name.startswith("day_2") else "Day 3 (+72h)")
                    flood_days.append(day_label)
            if flood_days:
                initial_risk = "CRITICAL"
        except Exception as e:
            print(f"[UserDashboard] Location initial assessment notice: {e}")

    new_loc = UserLocation(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        label=req.label.strip() or "Home",
        latitude=round(req.latitude, 6),
        longitude=round(req.longitude, 6),
        place_name=req.place_name.strip() or f"Pin at {req.latitude:.4f}, {req.longitude:.4f}",
        is_primary=is_primary,
        last_checked_at=datetime.now(timezone.utc),
        last_risk_level=initial_risk,
        last_flood_days_json=json.dumps(flood_days),
    )
    db.add(new_loc)
    db.commit()

    return {
        "message": "Location saved successfully.",
        "location": {
            "id": new_loc.id,
            "label": new_loc.label,
            "latitude": new_loc.latitude,
            "longitude": new_loc.longitude,
            "place_name": new_loc.place_name,
            "is_primary": new_loc.is_primary,
            "last_risk_level": new_loc.last_risk_level,
            "last_flood_days": flood_days,
        },
    }


@user_router.delete("/locations/{location_id}")
def delete_user_location(
    location_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loc = (
        db.query(UserLocation)
        .filter(UserLocation.id == location_id, UserLocation.user_id == current_user.id)
        .first()
    )
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found.")

    was_primary = loc.is_primary
    db.delete(loc)
    db.commit()

    # If deleted location was primary, promote remaining location
    if was_primary:
        remaining = (
            db.query(UserLocation)
            .filter(UserLocation.user_id == current_user.id)
            .first()
        )
        if remaining:
            remaining.is_primary = True
            db.commit()

    return {"message": "Location removed successfully."}


@user_router.put("/locations/{location_id}")
@user_router.patch("/locations/{location_id}")
def update_user_location(
    location_id: str,
    req: UserLocationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loc = (
        db.query(UserLocation)
        .filter(UserLocation.id == location_id, UserLocation.user_id == current_user.id)
        .first()
    )
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found.")

    if req.latitude is not None and req.longitude is not None:
        if not is_in_basin_bbox(req.latitude, req.longitude):
            raise HTTPException(
                status_code=400,
                detail="Coordinate is outside the monitored Krishna River Basin.",
            )
        loc.latitude = round(req.latitude, 6)
        loc.longitude = round(req.longitude, 6)

        # Re-evaluate flood risk on coordinate update
        if _predict_runner:
            try:
                pred = _predict_runner(PredictRequest(latitude=req.latitude, longitude=req.longitude))
                flood_days = []
                for h_name, f_obj in pred.forecasts.items():
                    if f_obj.will_submerge:
                        day_label = "Day 1 (+24h)" if h_name.startswith("day_1") else ("Day 2 (+48h)" if h_name.startswith("day_2") else "Day 3 (+72h)")
                        flood_days.append(day_label)
                loc.last_risk_level = "CRITICAL" if flood_days else "SAFE"
                loc.last_flood_days_json = json.dumps(flood_days)
            except Exception as e:
                print(f"[UserLocation] Re-evaluation notice: {e}")

    if req.label is not None:
        loc.label = req.label.strip() or loc.label
    if req.place_name is not None:
        loc.place_name = req.place_name.strip() or loc.place_name

    if req.is_primary is not None and req.is_primary:
        db.query(UserLocation).filter(UserLocation.user_id == current_user.id).update(
            {"is_primary": False}
        )
        loc.is_primary = True

    loc.last_checked_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "message": "Location updated successfully.",
        "location": {
            "id": loc.id,
            "label": loc.label,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "place_name": loc.place_name,
            "is_primary": loc.is_primary,
            "last_risk_level": loc.last_risk_level,
        },
    }


@user_router.patch("/preferences")
def update_user_preferences(
    req: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.alert_language is not None:
        if req.alert_language not in ["bilingual", "en", "kn"]:
            raise HTTPException(status_code=400, detail="Invalid language. Choose bilingual, en, or kn.")
        current_user.alert_language = req.alert_language

    if req.sms_enabled is not None:
        current_user.sms_enabled = req.sms_enabled

    if req.email_enabled is not None:
        current_user.email_enabled = req.email_enabled

    db.commit()
    return {
        "message": "Notification preferences updated successfully.",
        "preferences": {
            "alert_language": current_user.alert_language,
            "sms_enabled": current_user.sms_enabled,
            "email_enabled": current_user.email_enabled,
        },
    }


@user_router.get("/dashboard")
def get_user_dashboard(
    location_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. Fetch user locations
    locations = (
        db.query(UserLocation)
        .filter(UserLocation.user_id == current_user.id)
        .order_by(UserLocation.is_primary.desc(), UserLocation.created_at.asc())
        .all()
    )

    if not locations:
        return {
            "has_location": False,
            "message": "No monitored locations configured yet. Please add your home or farm location to begin live monitoring.",
            "user": {
                "full_name": current_user.full_name,
                "email": current_user.email,
                "phone": current_user.phone,
                "alert_language": current_user.alert_language,
                "sms_enabled": current_user.sms_enabled,
                "email_enabled": current_user.email_enabled,
            },
        }

    # 2. Pick target location
    active_loc = None
    if location_id:
        active_loc = next((l for l in locations if l.id == location_id), None)
    if not active_loc:
        active_loc = locations[0]

    lat = active_loc.latitude
    lon = active_loc.longitude

    # 3. Run live prediction
    if not _predict_runner:
        raise HTTPException(status_code=503, detail="Prediction engine offline")

    try:
        pred = _predict_runner(PredictRequest(latitude=lat, longitude=lon))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hydrological engine analysis error: {str(e)}")

    # 4. Evaluate flood days & update location status in DB
    flooded_days = []
    max_risk = "SAFE"
    for h_name, f_obj in pred.forecasts.items():
        if f_obj.will_submerge:
            max_risk = "CRITICAL"
            day_label = "Day 1 (+24h)" if "1" in h_name else ("Day 2 (+48h)" if "2" in h_name else "Day 3 (+72h)")
            flooded_days.append(day_label)
        elif f_obj.risk_level == "WARNING" and max_risk != "CRITICAL":
            max_risk = "WARNING"

    active_loc.last_checked_at = datetime.now(timezone.utc)
    active_loc.last_risk_level = max_risk
    active_loc.last_flood_days_json = json.dumps(flooded_days)
    db.commit()

    # 5. Live Weather & Soil AMC
    weather = fetch_live_weather(lat, lon)

    # 6. Upstream & Downstream Reservoirs
    up_id = pred.live_telemetry.upstream_station if pred.live_telemetry else "KOYNA"
    down_id = pred.live_telemetry.downstream_station if pred.live_telemetry else "ALMATTI"
    reservoirs = find_nearby_reservoirs(lat, lon, up_id, down_id)

    # 7. Recent alert logs for this user
    recent_alerts = (
        db.query(AlertLog)
        .filter(AlertLog.user_id == current_user.id)
        .order_by(AlertLog.created_at.desc())
        .limit(20)
        .all()
    )

    return {
        "has_location": True,
        "active_location": {
            "id": active_loc.id,
            "label": active_loc.label,
            "latitude": active_loc.latitude,
            "longitude": active_loc.longitude,
            "place_name": active_loc.place_name,
            "is_primary": active_loc.is_primary,
            "risk_level": max_risk,
            "flooded_days": flooded_days,
            "last_checked_at": active_loc.last_checked_at.isoformat(),
        },
        "all_locations": [
            {
                "id": l.id,
                "label": l.label,
                "place_name": l.place_name,
                "latitude": l.latitude,
                "longitude": l.longitude,
                "is_primary": l.is_primary,
                "risk_level": l.last_risk_level,
            }
            for l in locations
        ],
        "hydrology": {
            "ground_elevation_m": pred.ground_elevation_m,
            "river_bed_elevation_m": pred.river_bed_elevation_m,
            "hand_m": pred.hand_m,
            "distance_to_river_m": pred.distance_to_river_m,
            "current_state": pred.current_state,
            "critical_entry_height_y_m": pred.critical_entry_height_y_m,
            "direct_ridge_height_h_m": pred.direct_ridge_height_h_m,
            "indirect_flanking": pred.indirect_flanking,
            "barrier_protected": pred.barrier_protected,
            "drainage_lock_active": pred.drainage_lock_active,
            "nearest_safe_high_ground": pred.nearest_safe_high_ground,
            "forecasts": {
                k: {
                    "predicted_river_depth_m": v.predicted_river_depth_m,
                    "predicted_wse_m": v.predicted_wse_m,
                    "will_submerge": v.will_submerge,
                    "submersion_depth_m": v.submersion_depth_m,
                    "safety_margin_m": v.safety_margin_m,
                    "risk_level": v.risk_level,
                    "predicted_discharge_cusecs": v.predicted_discharge_cusecs,
                    "predicted_discharge_cumecs": v.predicted_discharge_cumecs,
                    "channel_bankfull_cusecs": v.channel_bankfull_cusecs,
                    "bankfull_capacity_cusecs": v.bankfull_capacity_cusecs,
                }
                for k, v in pred.forecasts.items()
            },
        },
        "river_telemetry": {
            "wris_river_name": pred.wris_river_name,
            "flow_cfs": pred.live_telemetry.current_flow_cusecs if pred.live_telemetry else 0.0,
            "flow_cumecs": pred.live_telemetry.current_flow_cumecs if pred.live_telemetry else 0.0,
            "current_depth_m": pred.live_telemetry.current_river_depth_m if pred.live_telemetry else 0.0,
            "is_bankfull": pred.live_telemetry.is_bankfull if pred.live_telemetry else False,
            "measured_width_m": pred.hydraulic_diagnostics.get("wetted_top_width_m", 120.0) if pred.hydraulic_diagnostics else 120.0,
            "channel_velocity_ms": pred.hydraulic_diagnostics.get("channel_velocity_ms", 0.5) if pred.hydraulic_diagnostics else 0.5,
        },
        "weather": weather,
        "reservoirs": reservoirs,
        "recent_alerts": [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "channel": a.channel,
                "language": a.language,
                "status": a.status,
                "message_body": a.message_body,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_alerts
        ],
        "user_preferences": {
            "alert_language": current_user.alert_language,
            "sms_enabled": current_user.sms_enabled,
            "email_enabled": current_user.email_enabled,
            "phone": current_user.phone,
            "email": current_user.email,
        },
    }


@user_router.post("/test-alert")
def send_user_test_alert(
    req: TestAlertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Dispatches a 1-click test alert via SMS & Email according to user preferences.
    """
    target_loc = None
    if req.location_id:
        target_loc = db.query(UserLocation).filter(
            UserLocation.id == req.location_id, UserLocation.user_id == current_user.id
        ).first()

    if not target_loc:
        target_loc = (
            db.query(UserLocation)
            .filter(UserLocation.user_id == current_user.id)
            .order_by(UserLocation.is_primary.desc())
            .first()
        )

    loc_name = target_loc.place_name if target_loc else "Krishna Basin Site"
    lat = target_loc.latitude if target_loc else 16.8538
    lon = target_loc.longitude if target_loc else 74.5569

    alert_bundle = format_flood_alert(
        location_name=loc_name,
        lat=lat,
        lon=lon,
        flood_days=["Day 1 (+24h)"],
        peak_flow_cfs=45000.0,
        max_depth_m=3.85,
        high_ground_str="Elevated Ridge (520m MSL, 250m North)",
        is_simulation=True,
        sim_summary="User Dashboard Test Alert",
        language=current_user.alert_language or "bilingual",
    )

    sms_res = {"status": "DISABLED_BY_USER"}
    if current_user.sms_enabled:
        sms_res = send_sms(
            db,
            current_user.phone,
            alert_bundle["sms_body"],
            alert_type="TEST",
            user_id=current_user.id,
            location_id=target_loc.id if target_loc else None,
            language=current_user.alert_language or "bilingual",
            flood_days="Day 1 (+24h)",
        )

    email_res = {"status": "DISABLED_BY_USER"}
    if current_user.email_enabled:
        email_res = send_email(
            db,
            current_user.email,
            f"[TEST ALERT] KrishnaFlow: {alert_bundle['email_subject']}",
            alert_bundle["email_html"],
            alert_bundle["sms_body"],
            alert_type="TEST",
            user_id=current_user.id,
            location_id=target_loc.id if target_loc else None,
            language=current_user.alert_language or "bilingual",
            flood_days="Day 1 (+24h)",
        )

    return {
        "message": "Test alert dispatched.",
        "sms_result": sms_res,
        "email_result": email_res,
        "preview": {
            "sms_text": alert_bundle["sms_body"],
            "recipient_phone": current_user.phone,
            "recipient_email": current_user.email,
        },
    }
