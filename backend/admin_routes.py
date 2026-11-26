import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.db_models import User, UserLocation, AlertLog
from backend.schemas import (
    SimulateAndAlertRequest,
    AdminBroadcastRequest,
    PredictRequest,
)
from backend.auth import require_admin
from backend.notifications import (
    send_sms,
    send_email,
    format_flood_alert,
    get_gateway_health,
)
from backend.scheduler import run_scheduled_basin_scan, get_next_scan_time

admin_router = APIRouter(prefix="/api/v1/admin", tags=["Admin Command Center"])

_admin_predict_runner = None
_admin_nodes_cache = None


def set_admin_predict_runner(runner_fn, nodes_list):
    global _admin_predict_runner, _admin_nodes_cache
    _admin_predict_runner = runner_fn
    _admin_nodes_cache = nodes_list


@admin_router.get("/overview")
def get_admin_overview(
    current_admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    total_users = db.query(User).count()
    verified_users = (
        db.query(User)
        .filter(User.is_phone_verified == True, User.is_email_verified == True)
        .count()
    )

    # Risk counts
    crit_count = db.query(UserLocation).filter(UserLocation.last_risk_level == "CRITICAL").count()
    warn_count = db.query(UserLocation).filter(UserLocation.last_risk_level == "WARNING").count()
    safe_count = db.query(UserLocation).filter(UserLocation.last_risk_level == "SAFE").count()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sms_today = (
        db.query(func.count(AlertLog.id))
        .filter(AlertLog.channel == "SMS", AlertLog.status == "SENT", AlertLog.created_at >= today_start)
        .scalar()
        or 0
    )
    emails_today = (
        db.query(func.count(AlertLog.id))
        .filter(AlertLog.channel == "EMAIL", AlertLog.status == "SENT", AlertLog.created_at >= today_start)
        .scalar()
        or 0
    )

    gateway_health = get_gateway_health(db)
    next_scan = get_next_scan_time()

    return {
        "kpis": {
            "total_users": total_users,
            "verified_users": verified_users,
            "locations_critical": crit_count,
            "locations_warning": warn_count,
            "locations_safe": safe_count,
            "sms_sent_today": sms_today,
            "max_daily_sms": gateway_health["sms_gateway"]["daily_quota"],
            "emails_sent_today": emails_today,
            "next_scheduled_scan": next_scan,
        },
        "gateway_health": gateway_health,
    }


@admin_router.get("/users")
def get_all_users_directory(
    current_admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    results = []

    for u in users:
        locs = (
            db.query(UserLocation)
            .filter(UserLocation.user_id == u.id)
            .order_by(UserLocation.is_primary.desc())
            .all()
        )
        primary_loc = next((l for l in locs if l.is_primary), (locs[0] if locs else None))

        results.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "role": u.role,
            "is_phone_verified": u.is_phone_verified,
            "is_email_verified": u.is_email_verified,
            "alert_language": u.alert_language,
            "sms_enabled": u.sms_enabled,
            "email_enabled": u.email_enabled,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "locations_count": len(locs),
            "primary_location": {
                "id": primary_loc.id,
                "label": primary_loc.label,
                "place_name": primary_loc.place_name,
                "latitude": primary_loc.latitude,
                "longitude": primary_loc.longitude,
                "risk_level": primary_loc.last_risk_level,
                "last_checked_at": primary_loc.last_checked_at.isoformat() if primary_loc.last_checked_at else None,
            } if primary_loc else None,
            "all_locations": [
                {
                    "id": l.id,
                    "label": l.label,
                    "place_name": l.place_name,
                    "latitude": l.latitude,
                    "longitude": l.longitude,
                    "risk_level": l.last_risk_level,
                }
                for l in locs
            ],
        })

    return {"users": results, "total": len(results)}


@admin_router.post("/run-scan-now")
def run_scan_now(
    current_admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    summary = run_scheduled_basin_scan(db)
    return {"message": "Basin-wide scan completed.", "summary": summary}


@admin_router.post("/simulate-and-alert")
def simulate_dam_release_and_alert(
    req: SimulateAndAlertRequest,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not _admin_predict_runner:
        raise HTTPException(status_code=503, detail="Prediction engine offline")

    # Format simulation summary string
    sim_parts = [f"{k}: {v:,.0f} cfs" for k, v in req.custom_dam_outflow_override.items()]
    sim_summary = f"{', '.join(sim_parts)} ({req.release_duration_hours:.0f}h duration)"

    # Get all verified users
    verified_users = (
        db.query(User)
        .filter(User.is_phone_verified == True, User.is_email_verified == True)
        .all()
    )
    user_map = {u.id: u for u in verified_users}
    locations = (
        db.query(UserLocation)
        .filter(UserLocation.user_id.in_(list(user_map.keys())))
        .all()
    )

    impacted = []
    alerts_dispatched = 0

    for loc in locations:
        user = user_map.get(loc.user_id)
        if not user:
            continue

        try:
            pred = _admin_predict_runner(
                PredictRequest(
                    latitude=loc.latitude,
                    longitude=loc.longitude,
                    custom_dam_outflow_override=req.custom_dam_outflow_override,
                    release_duration_hours=req.release_duration_hours,
                )
            )
        except Exception as e:
            print(f"[AdminSimulate] Error for loc {loc.id}: {e}")
            continue

        flooded_days = []
        max_depth = 0.0
        peak_flow = 0.0
        for h_name, f_obj in pred.forecasts.items():
            if f_obj.will_submerge:
                day_label = (
                    "Day 1 (+24h)" if h_name.startswith("day_1")
                    else ("Day 2 (+48h)" if h_name.startswith("day_2") else "Day 3 (+72h)")
                )
                flooded_days.append(day_label)
                max_depth = max(max_depth, f_obj.submersion_depth_m)
                peak_flow = max(peak_flow, f_obj.predicted_discharge_cusecs)

        if flooded_days:
            impacted_record = {
                "user_id": user.id,
                "user_name": user.full_name,
                "user_email": user.email,
                "user_phone": user.phone,
                "location_id": loc.id,
                "place_name": loc.place_name,
                "latitude": loc.latitude,
                "longitude": loc.longitude,
                "flooded_days": flooded_days,
                "peak_discharge_cfs": round(peak_flow, 0),
                "max_submersion_depth_m": round(max_depth, 2),
            }
            impacted.append(impacted_record)

            # Check if alerts should be sent
            if req.send_alerts:
                should_alert = False
                target_user = user

                if req.target_mode == "admin_only":
                    should_alert = True
                    target_user = current_admin
                elif req.target_mode == "selected_user" and req.target_user_id == user.id:
                    should_alert = True
                    target_user = user
                elif req.target_mode == "all_impacted":
                    should_alert = True
                    target_user = user

                if should_alert:
                    hg_str = ""
                    if pred.nearest_safe_high_ground:
                        hg = pred.nearest_safe_high_ground
                        hg_str = f"{hg.get('bearing', 'Nearby')} ({hg.get('elevation_m', 500)}m, {hg.get('distance_m', 300)}m)"

                    alert_bundle = format_flood_alert(
                        location_name=loc.place_name,
                        lat=loc.latitude,
                        lon=loc.longitude,
                        flood_days=flooded_days,
                        peak_flow_cfs=peak_flow,
                        max_depth_m=max_depth,
                        high_ground_str=hg_str,
                        is_simulation=True,
                        sim_summary=sim_summary,
                        language=target_user.alert_language or "bilingual",
                    )

                    days_str = ", ".join(flooded_days)

                    if target_user.sms_enabled:
                        send_sms(
                            db,
                            target_user.phone,
                            alert_bundle["sms_body"],
                            alert_type="SIMULATION",
                            user_id=target_user.id,
                            location_id=loc.id,
                            language=target_user.alert_language or "bilingual",
                            flood_days=days_str,
                        )
                        alerts_dispatched += 1

                    if target_user.email_enabled:
                        send_email(
                            db,
                            target_user.email,
                            alert_bundle["email_subject"],
                            alert_bundle["email_html"],
                            alert_bundle["sms_body"],
                            alert_type="SIMULATION",
                            user_id=target_user.id,
                            location_id=loc.id,
                            language=target_user.alert_language or "bilingual",
                            flood_days=days_str,
                        )
                        alerts_dispatched += 1

    return {
        "simulation_summary": sim_summary,
        "total_monitored_locations": len(locations),
        "impacted_count": len(impacted),
        "impacted_users": impacted,
        "send_alerts_active": req.send_alerts,
        "target_mode": req.target_mode,
        "alerts_dispatched": alerts_dispatched,
    }


@admin_router.post("/broadcast")
def send_emergency_broadcast(
    req: AdminBroadcastRequest,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = (
        db.query(User)
        .filter(User.is_phone_verified == True, User.is_email_verified == True)
        .all()
    )

    sms_count = 0
    email_count = 0

    for u in users:
        # Determine language text
        lang = u.alert_language or "bilingual"
        if lang == "en":
            body = f"[KRISHNAFLOW EMERGENCY ADVISORY]\n{req.message_en}"
        elif lang == "kn":
            body = f"[ಕೃಷ್ಣಾಫ್ಲೋ ತುರ್ತು ಪ್ರಕಟಣೆ]\n{req.message_kn}"
        else:
            body = f"[KRISHNAFLOW ADVISORY / ತುರ್ತು ಪ್ರಕಟಣೆ]\nEN: {req.message_en}\nKN: {req.message_kn}"

        email_html = f"""
        <div style="font-family:sans-serif;background:#0b0f19;color:#fff;padding:24px;border-radius:10px;border-left:5px solid #f59e0b;">
          <h2 style="color:#f59e0b;margin-top:0;">⚠️ Emergency Flood Advisory / ತುರ್ತು ಪ್ರವಾಹ ಪ್ರಕಟಣೆ</h2>
          <div style="background:#1e293b;padding:16px;border-radius:8px;margin-bottom:12px;">
            <strong style="color:#38bdf8;">English:</strong>
            <p style="margin:6px 0 0 0;font-size:14px;color:#e2e8f0;">{req.message_en}</p>
          </div>
          <div style="background:#1a2234;padding:16px;border-radius:8px;">
            <strong style="color:#10b981;">ಕನ್ನಡ:</strong>
            <p style="margin:6px 0 0 0;font-size:14px;color:#e2e8f0;">{req.message_kn}</p>
          </div>
        </div>
        """

        if u.sms_enabled:
            send_sms(
                db,
                u.phone,
                body,
                alert_type="BROADCAST",
                user_id=u.id,
                language=lang,
            )
            sms_count += 1

        if u.email_enabled:
            send_email(
                db,
                u.email,
                "⚠️ KrishnaFlow Emergency Flood Advisory / ತುರ್ತು ಪ್ರವಾಹ ಪ್ರಕಟಣೆ",
                email_html,
                body,
                alert_type="BROADCAST",
                user_id=u.id,
                language=lang,
            )
            email_count += 1

    return {
        "message": f"Broadcast sent to {len(users)} verified citizen users.",
        "recipients_count": len(users),
        "sms_sent": sms_count,
        "email_sent": email_count,
    }


@admin_router.get("/alert-logs")
def get_alert_logs(
    alert_type: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(AlertLog)

    if alert_type:
        q = q.filter(AlertLog.alert_type == alert_type.upper())
    if channel:
        q = q.filter(AlertLog.channel == channel.upper())
    if status:
        q = q.filter(AlertLog.status == status.upper())

    logs = q.order_by(AlertLog.created_at.desc()).limit(limit).all()

    return {
        "total": len(logs),
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "location_id": log.location_id,
                "alert_type": log.alert_type,
                "channel": log.channel,
                "language": log.language,
                "recipient": log.recipient,
                "flood_days": log.flood_days,
                "message_body": log.message_body,
                "status": log.status,
                "error_detail": log.error_detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
