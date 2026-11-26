import os
import json
import smtplib
from datetime import datetime, timezone, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict, Any
import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.db_models import AlertLog

# Kannada Day mappings
DAY_MAP_KN = {
    "Day 1 (+24h)": "ದಿನ 1 (+24 ಗಂ)",
    "Day 2 (+48h)": "ದಿನ 2 (+48 ಗಂ)",
    "Day 3 (+72h)": "ದಿನ 3 (+72 ಗಂ)",
    "Day 1": "ದಿನ 1 (+24 ಗಂ)",
    "Day 2": "ದಿನ 2 (+48 ಗಂ)",
    "Day 3": "ದಿನ 3 (+72 ಗಂ)",
}


def translate_days_to_kannada(flood_days: List[str]) -> str:
    kn_days = [DAY_MAP_KN.get(d.strip(), d.strip()) for d in flood_days]
    return ", ".join(kn_days)


def format_flood_alert(
    location_name: str,
    lat: float,
    lon: float,
    flood_days: List[str],
    peak_flow_cfs: float,
    max_depth_m: float,
    high_ground_str: str,
    is_simulation: bool = False,
    sim_summary: str = "",
    language: str = "bilingual",
) -> Dict[str, str]:
    """
    Constructs English, Kannada, and Bilingual flood alert texts (plain text for SMS, HTML for Email).
    """
    days_en = ", ".join(flood_days) if flood_days else "Next 24h-72h"
    days_kn = translate_days_to_kannada(flood_days) if flood_days else "ಮುಂದಿನ 24-72 ಗಂಟೆಗಳಲ್ಲಿ"
    flow_str = f"{peak_flow_cfs:,.0f}"
    depth_str = f"{max_depth_m:.2f}"
    hg = high_ground_str if high_ground_str else "Nearby high elevation / ಸಮೀಪದ ಎತ್ತರದ ಪ್ರದೇಶ"

    if is_simulation:
        sim_tag = f" [{sim_summary}]" if sim_summary else ""
        text_en = (
            f"[SIMULATION FLOOD ALERT] KrishnaFlow: Detected flood via simulation at {location_name} "
            f"on {days_en}{sim_tag}. Peak: {flow_str} cfs, Depth: {depth_str}m. Safe High Ground: {hg}. "
            f"This is a DEMO SIMULATION test alert."
        )
        text_kn = (
            f"[ಸಿಮ್ಯುಲೇಶನ್ ಎಚ್ಚರಿಕೆ] ಕೃಷ್ಣಾಫ್ಲೋ: {sim_summary or 'ಅಣೆಕಟ್ಟು ಹೊರಹರಿವು'} ಕಾರಣದಿಂದ {location_name} ನಲ್ಲಿ "
            f"{days_kn} ರಂದು ಸಿಮ್ಯುಲೇಶನ್ ಮೂಲಕ ಪ್ರವಾಹ ಪತ್ತೆಯಾಗಿದೆ. ಗರಿಷ್ಠ ಹರಿವು: {flow_str} ಕ್ಯೂಸೆಕ್ಸ್, ಆಳ: {depth_str}m. "
            f"ಸುರಕ್ಷಿತ ಸ್ಥಳ: {hg}. ಇದು ಕೇವಲ ಪ್ರಾಯೋಗಿಕ ಪರೀಕ್ಷಾ ಎಚ್ಚರಿಕೆ."
        )
        text_bilingual = (
            f"🚨 [SIMULATION ALERT / ಸಿಮ್ಯುಲೇಶನ್ ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ]\n"
            f"EN: Detected flood via simulation at {location_name} on {days_en}{sim_tag}. "
            f"Flow: {flow_str} cfs, Depth: {depth_str}m. High Ground: {hg}. [Demo test alert]\n"
            f"KN: {sim_summary or 'ಅಣೆಕಟ್ಟು ಹೊರಹರಿವು'} ಕಾರಣದಿಂದ {location_name} ನಲ್ಲಿ {days_kn} ರಂದು ಸಿಮ್ಯುಲೇಶನ್ ಮೂಲಕ ಪ್ರವಾಹ ಪತ್ತೆಯಾಗಿದೆ [ಪರೀಕ್ಷಾ ಎಚ್ಚರಿಕೆ]. "
            f"ಹರಿವು: {flow_str} cfs, ಆಳ: {depth_str}m. ಸುರಕ್ಷಿತ ಸ್ಥಳ: {hg}."
        )
        email_subject = f"⚠️ [SIMULATION ALERT] KrishnaFlow Flood Detected at {location_name} ({days_en})"
    else:
        text_en = (
            f"KRISHNAFLOW FLOOD ALERT: Flood predicted at {location_name} on {days_en}. "
            f"Peak: {flow_str} cfs, Depth: {depth_str}m. Safe High Ground: {hg}. Move to safety."
        )
        text_kn = (
            f"ಕೃಷ್ಣಾಫ್ಲೋ ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ: {location_name} ನಲ್ಲಿ {days_kn} ರಂದು ಪ್ರವಾಹ ಸಾಧ್ಯತೆ. "
            f"ಗರಿಷ್ಠ ಹರಿವು: {flow_str} ಕ್ಯೂಸೆಕ್ಸ್, ಆಳ: {depth_str}m. ಸುರಕ್ಷಿತ ಸ್ಥಳ: {hg}. ತಕ್ಷಣ ಎಚ್ಚರವಹಿಸಿ."
        )
        text_bilingual = (
            f"🚨 [KRISHNAFLOW FLOOD ALERT / ಕೃಷ್ಣಾಫ್ಲೋ ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ]\n"
            f"EN: Flood predicted at {location_name} on {days_en}. Peak: {flow_str} cfs, Depth: {depth_str}m. "
            f"Safe High Ground: {hg}.\n"
            f"KN: {location_name} ನಲ್ಲಿ {days_kn} ರಂದು ಪ್ರವಾಹ ಸಂಭವಿಸುವ ಸಾಧ್ಯತೆ. "
            f"ಗರಿಷ್ಠ ಹರಿವು: {flow_str} ಕ್ಯೂಸೆಕ್ಸ್, ಆಳ: {depth_str}m. ಸುರಕ್ಷಿತ ಸ್ಥಳ: {hg}."
        )
        email_subject = f"🚨 URGENT: KrishnaFlow Flood Alert for {location_name} ({days_en})"

    # Select SMS plain text based on user's language preference
    if language == "en":
        sms_text = text_en
    elif language == "kn":
        sms_text = text_kn
    else:
        sms_text = text_bilingual

    # HTML Email Template (Responsive & Bilingual)
    status_badge_color = "#f59e0b" if is_simulation else "#ef4444"
    status_label = "DEMO SIMULATION FLOOD ALERT / ಪ್ರಾಯೋಗಿಕ ಪರೀಕ್ಷಾ ಎಚ್ಚರಿಕೆ" if is_simulation else "OFFICIAL RIVERINE FLOOD WARNING / ಅಧಿಕೃತ ಪ್ರವಾಹ ಮುನ್ನೆಚ್ಚರಿಕೆ"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 12px; overflow: hidden; }}
        .header {{ background: linear-gradient(135deg, #1e293b, #0f172a); padding: 24px; border-bottom: 2px solid {status_badge_color}; }}
        .badge {{ display: inline-block; background-color: {status_badge_color}; color: #000; font-weight: bold; font-size: 11px; padding: 4px 10px; border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.05em; }}
        .title {{ font-size: 22px; font-weight: 800; color: #ffffff; margin: 12px 0 4px 0; }}
        .subtitle {{ font-size: 14px; color: #94a3b8; margin: 0; }}
        .content {{ padding: 24px; }}
        .card {{ background-color: #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 16px; border-left: 4px solid #06b6d4; }}
        .card-kn {{ background-color: #1a2234; border-radius: 8px; padding: 16px; margin-bottom: 16px; border-left: 4px solid #10b981; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 20px; }}
        .stat-box {{ background-color: #0f172a; padding: 12px; border-radius: 8px; border: 1px solid #1e293b; }}
        .stat-label {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; margin-bottom: 4px; }}
        .stat-val {{ font-size: 18px; font-weight: 700; color: #38bdf8; }}
        .footer {{ padding: 20px 24px; background-color: #0b0f19; font-size: 12px; color: #64748b; text-align: center; border-top: 1px solid #1f2937; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <span class="badge">{status_label}</span>
          <div class="title">KrishnaFlow AI Flood Alert</div>
          <div class="subtitle">Location: <strong>{location_name}</strong> ({lat:.4f}°N, {lon:.4f}°E)</div>
        </div>
        <div class="content">
          <div class="stat-grid">
            <div class="stat-box">
              <div class="stat-label">Inundation Timeline / ಪ್ರವಾಹ ದಿನಗಳು</div>
              <div class="stat-val" style="font-size: 15px; color: #f87171;">{days_en}<br><span style="font-size: 13px; color: #a7f3d0;">{days_kn}</span></div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Peak Discharge / ಗರಿಷ್ಠ ಹರಿವು</div>
              <div class="stat-val">{flow_str} <span style="font-size: 12px; color: #94a3b8;">cfs</span></div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Predicted River Depth / ನದಿ ಆಳ</div>
              <div class="stat-val">{depth_str} <span style="font-size: 12px; color: #94a3b8;">m</span></div>
            </div>
            <div class="stat-box">
              <div class="stat-label">Recommended Evacuation / ಸುರಕ್ಷಿತ ಸ್ಥಳ</div>
              <div class="stat-val" style="font-size: 13px; color: #34d399;">{hg}</div>
            </div>
          </div>

          <div class="card">
            <div style="font-weight: 700; color: #38bdf8; margin-bottom: 6px;">English Advisory:</div>
            <div style="font-size: 14px; line-height: 1.5; color: #e2e8f0;">{text_en}</div>
          </div>

          <div class="card-kn">
            <div style="font-weight: 700; color: #10b981; margin-bottom: 6px;">ಕನ್ನಡ ಪ್ರಕಟಣೆ:</div>
            <div style="font-size: 14px; line-height: 1.5; color: #e2e8f0;">{text_kn}</div>
          </div>
        </div>
        <div class="footer">
          KrishnaFlow AI Hydrological Early Warning System • Krishna River Basin, Karnataka & Maharashtra.<br>
          Automated Sentinel-2 & GLO-30 2D Physics Analysis.
        </div>
      </div>
    </body>
    </html>
    """

    return {
        "sms_body": sms_text,
        "text_en": text_en,
        "text_kn": text_kn,
        "text_bilingual": text_bilingual,
        "email_subject": email_subject,
        "email_html": html_body,
    }


def send_sms(
    db: Session,
    recipient_phone: str,
    message: str,
    alert_type: str = "REAL",
    user_id: Optional[str] = None,
    location_id: Optional[str] = None,
    language: str = "bilingual",
    flood_days: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends SMS via Android SMS Gateway (HTTP Basic Auth) with Daily Quota check and Offline Simulation fallback.
    """
    # 1. Normalize phone to E.164 (+91XXXXXXXXXX)
    clean_phone = recipient_phone.strip()
    if not clean_phone.startswith("+"):
        clean_phone = "+91" + clean_phone[-10:]

    max_daily = int(os.getenv("MAX_DAILY_SMS", "90"))

    # 2. Daily Quota Check in alert_logs
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        db.query(func.count(AlertLog.id))
        .filter(
            AlertLog.channel == "SMS",
            AlertLog.status == "SENT",
            AlertLog.created_at >= today_start,
        )
        .scalar()
        or 0
    )

    if sent_today >= max_daily:
        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="SMS",
            language=language,
            recipient=clean_phone,
            flood_days=flood_days,
            message_body=message,
            status="QUOTA_SKIPPED",
            error_detail=f"Daily SMS limit of {max_daily} reached ({sent_today} sent today).",
        )
        db.add(log)
        db.commit()
        return {"status": "QUOTA_SKIPPED", "detail": f"Daily SMS quota reached ({max_daily})"}

    # 3. Android SMS Gateway URL & Credentials
    gateway_url = os.getenv("SMS_GATEWAY_URL", "").strip()
    gateway_user = os.getenv("SMS_GATEWAY_USER", "").strip()
    gateway_pass = os.getenv("SMS_GATEWAY_PASS", "").strip()

    # Check if gateway is configured
    if not gateway_url or "x.x" in gateway_url or "localhost" in gateway_url and "8080" not in gateway_url:
        # Fallback to SIMULATED_OFFLINE so the system never crashes
        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="SMS",
            language=language,
            recipient=clean_phone,
            flood_days=flood_days,
            message_body=message,
            status="SIMULATED_OFFLINE",
            error_detail="SMS Gateway URL unset or demo placeholder. Stored in Outbox.",
        )
        db.add(log)
        db.commit()
        return {"status": "SIMULATED_OFFLINE", "detail": "Stored in offline outbox (gateway unconfigured)"}

    payload = {
        "textMessage": {"text": message},
        "phoneNumbers": [clean_phone],
    }

    try:
        # capcom6 Android SMS Gateway API
        auth = HTTPBasicAuth(gateway_user, gateway_pass) if gateway_user else None
        target_endpoint = gateway_url.rstrip("/")
        if not target_endpoint.endswith("/messages") and not target_endpoint.endswith("/message"):
            target_endpoint += "/messages"

        resp = requests.post(
            target_endpoint,
            json=payload,
            auth=auth,
            timeout=5.0,
            headers={"Content-Type": "application/json"},
        )

        # Fallback check if 404
        if resp.status_code == 404 and target_endpoint.endswith("/messages"):
            resp = requests.post(
                target_endpoint[:-1],  # /message
                json=payload,
                auth=auth,
                timeout=5.0,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code in [200, 201, 202]:
            log = AlertLog(
                user_id=user_id,
                location_id=location_id,
                alert_type=alert_type,
                channel="SMS",
                language=language,
                recipient=clean_phone,
                flood_days=flood_days,
                message_body=message,
                status="SENT",
                error_detail=f"HTTP {resp.status_code}: {resp.text[:100]}",
            )
            db.add(log)
            db.commit()
            return {"status": "SENT", "detail": "SMS dispatched via gateway"}
        else:
            log = AlertLog(
                user_id=user_id,
                location_id=location_id,
                alert_type=alert_type,
                channel="SMS",
                language=language,
                recipient=clean_phone,
                flood_days=flood_days,
                message_body=message,
                status="SIMULATED_OFFLINE",
                error_detail=f"Gateway responded HTTP {resp.status_code}: {resp.text[:100]}",
            )
            db.add(log)
            db.commit()
            return {"status": "SIMULATED_OFFLINE", "detail": f"Gateway error {resp.status_code}"}

    except Exception as exc:
        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="SMS",
            language=language,
            recipient=clean_phone,
            flood_days=flood_days,
            message_body=message,
            status="SIMULATED_OFFLINE",
            error_detail=f"Connection failure: {str(exc)[:150]}",
        )
        db.add(log)
        db.commit()
        return {"status": "SIMULATED_OFFLINE", "detail": f"Gateway offline: {str(exc)[:60]}"}


def send_email(
    db: Session,
    recipient_email: str,
    subject: str,
    html_body: str,
    plain_text: str,
    alert_type: str = "REAL",
    user_id: Optional[str] = None,
    location_id: Optional[str] = None,
    language: str = "bilingual",
    flood_days: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends UTF-8 multipart HTML/plain-text email via Gmail SMTP with offline demo fallback.
    """
    clean_email = recipient_email.strip().lower()
    email_user = os.getenv("EMAIL_USER", "").strip()
    email_pass = os.getenv("EMAIL_APP_PASSWORD", "").strip()

    if not email_user or not email_pass or "your_email" in email_user:
        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="EMAIL",
            language=language,
            recipient=clean_email,
            flood_days=flood_days,
            message_body=plain_text,
            status="SIMULATED_OFFLINE",
            error_detail="Gmail SMTP credentials unconfigured. Stored in Outbox.",
        )
        db.add(log)
        db.commit()
        return {"status": "SIMULATED_OFFLINE", "detail": "Stored in offline outbox (SMTP unconfigured)"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"KrishnaFlow AI <{email_user}>"
        msg["To"] = clean_email

        part1 = MIMEText(plain_text, "plain", "utf-8")
        part2 = MIMEText(html_body, "html", "utf-8")
        msg.attach(part1)
        msg.attach(part2)

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10.0) as server:
            server.ehlo()
            server.starttls()
            server.login(email_user, email_pass)
            server.sendmail(email_user, [clean_email], msg.as_string())

        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="EMAIL",
            language=language,
            recipient=clean_email,
            flood_days=flood_days,
            message_body=plain_text,
            status="SENT",
            error_detail="Dispatched via smtp.gmail.com:587",
        )
        db.add(log)
        db.commit()
        return {"status": "SENT", "detail": "Email dispatched via Gmail SMTP"}

    except Exception as exc:
        log = AlertLog(
            user_id=user_id,
            location_id=location_id,
            alert_type=alert_type,
            channel="EMAIL",
            language=language,
            recipient=clean_email,
            flood_days=flood_days,
            message_body=plain_text,
            status="SIMULATED_OFFLINE",
            error_detail=f"SMTP Error: {str(exc)[:150]}",
        )
        db.add(log)
        db.commit()
        return {"status": "SIMULATED_OFFLINE", "detail": f"SMTP failure: {str(exc)[:60]}"}


def get_gateway_health(db: Session) -> Dict[str, Any]:
    """
    Returns live health and daily quota metrics for the Admin dashboard.
    """
    max_daily = int(os.getenv("MAX_DAILY_SMS", "90"))
    gateway_url = os.getenv("SMS_GATEWAY_URL", "").strip()
    email_user = os.getenv("EMAIL_USER", "").strip()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        db.query(func.count(AlertLog.id))
        .filter(
            AlertLog.channel == "SMS",
            AlertLog.status == "SENT",
            AlertLog.created_at >= today_start,
        )
        .scalar()
        or 0
    )

    # Test SMS Gateway reachability
    sms_online = False
    if gateway_url and "x.x" not in gateway_url:
        try:
            r = requests.get(gateway_url.rstrip("/"), timeout=2.0)
            if r.status_code in [200, 401, 403]:
                sms_online = True
        except Exception:
            sms_online = False

    email_configured = bool(email_user and "your_email" not in email_user)

    return {
        "sms_gateway": {
            "configured": bool(gateway_url and "x.x" not in gateway_url),
            "url": gateway_url,
            "status": "ONLINE" if sms_online else "OFFLINE_SIMULATED",
            "daily_quota": max_daily,
            "used_today": sent_today,
            "remaining_today": max(0, max_daily - sent_today),
        },
        "email_smtp": {
            "configured": email_configured,
            "user": email_user,
            "status": "ONLINE_CONFIGURED" if email_configured else "OFFLINE_SIMULATED",
        },
    }
