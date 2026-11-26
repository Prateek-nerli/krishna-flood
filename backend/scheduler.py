import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.db_models import User, UserLocation, AlertLog
from backend.schemas import PredictRequest
from backend.notifications import send_sms, send_email, format_flood_alert

scheduler = BackgroundScheduler()
_predict_runner_fn = None


def set_scheduler_predict_runner(runner_fn):
    global _predict_runner_fn
    _predict_runner_fn = runner_fn


def run_scheduled_basin_scan(db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Automated 6-Hour Basin-Wide Scan:
    Evaluates every verified citizen's saved location.
    If flood is detected (will_submerge == True on Day 1, 2, or 3),
    dispatches bilingual SMS and Email alert with exact flood days.
    Enforces a 6-hour duplicate cooldown for REAL alerts.
    If all 3 days are False, updates status to SAFE and sends ZERO alerts.
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    summary = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_locations_checked": 0,
        "flooded_locations_count": 0,
        "alerts_dispatched": 0,
        "skipped_cooldown": 0,
        "details": [],
    }

    try:
        # Query all verified users with saved locations
        verified_users = (
            db.query(User)
            .filter(User.is_phone_verified == True, User.is_email_verified == True)
            .all()
        )

        user_map = {u.id: u for u in verified_users}
        user_ids = list(user_map.keys())

        if not user_ids:
            return summary

        locations = (
            db.query(UserLocation)
            .filter(UserLocation.user_id.in_(user_ids))
            .all()
        )

        now = datetime.now(timezone.utc)
        cooldown_window = now - timedelta(hours=6)

        for loc in locations:
            user = user_map.get(loc.user_id)
            if not user or not _predict_runner_fn:
                continue

            summary["total_locations_checked"] += 1

            # 1. Direct in-process coordinate prediction
            try:
                pred = _predict_runner_fn(PredictRequest(latitude=loc.latitude, longitude=loc.longitude))
            except Exception as e:
                print(f"[Scheduler] Error predicting for loc {loc.id}: {e}")
                continue

            # 2. Check flood days
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

            loc.last_checked_at = now

            if flooded_days:
                summary["flooded_locations_count"] += 1
                loc.last_risk_level = "CRITICAL"
                loc.last_flood_days_json = json.dumps(flooded_days)

                # Check duplicate cooldown in alert_logs
                recent_alert = (
                    db.query(AlertLog)
                    .filter(
                        AlertLog.location_id == loc.id,
                        AlertLog.alert_type == "REAL",
                        AlertLog.created_at >= cooldown_window,
                    )
                    .first()
                )

                if recent_alert:
                    summary["skipped_cooldown"] += 1
                    summary["details"].append({
                        "user": user.full_name,
                        "location": loc.place_name,
                        "status": "FLOODED_SKIPPED_COOLDOWN",
                        "days": flooded_days,
                    })
                    continue

                # Format bilingual alert
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
                    is_simulation=False,
                    language=user.alert_language or "bilingual",
                )

                days_str = ", ".join(flooded_days)

                if user.sms_enabled:
                    send_sms(
                        db,
                        user.phone,
                        alert_bundle["sms_body"],
                        alert_type="REAL",
                        user_id=user.id,
                        location_id=loc.id,
                        language=user.alert_language or "bilingual",
                        flood_days=days_str,
                    )
                    summary["alerts_dispatched"] += 1

                if user.email_enabled:
                    send_email(
                        db,
                        user.email,
                        alert_bundle["email_subject"],
                        alert_bundle["email_html"],
                        alert_bundle["sms_body"],
                        alert_type="REAL",
                        user_id=user.id,
                        location_id=loc.id,
                        language=user.alert_language or "bilingual",
                        flood_days=days_str,
                    )
                    summary["alerts_dispatched"] += 1

                summary["details"].append({
                    "user": user.full_name,
                    "location": loc.place_name,
                    "status": "ALERT_SENT",
                    "days": flooded_days,
                })
            else:
                # 3. All days are safe: update status, send ZERO alerts
                loc.last_risk_level = "SAFE"
                loc.last_flood_days_json = "[]"
                summary["details"].append({
                    "user": user.full_name,
                    "location": loc.place_name,
                    "status": "SAFE_NO_ALERT",
                    "days": [],
                })

        db.commit()
    finally:
        if should_close:
            db.close()

    return summary


def get_next_scan_time() -> Optional[str]:
    job = scheduler.get_job("basin_6h_scan")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            run_scheduled_basin_scan,
            IntervalTrigger(hours=6),
            id="basin_6h_scan",
            replace_existing=True,
        )
        scheduler.start()
        print("[Scheduler] Automated 6-Hour Flood Scanner started.")


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
