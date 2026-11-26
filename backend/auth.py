import os
import uuid
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from backend.database import get_db
from backend.db_models import User, OTPVerification, UserLocation
from backend.schemas import (
    UserRegisterRequest,
    VerifyOTPRequest,
    ResendOTPRequest,
    LoginRequest,
)
from backend.notifications import send_sms, send_email

auth_router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

import hashlib
raw_jwt_secret = os.getenv("JWT_SECRET_KEY", "7F3N9K2X6M1P8R4W6Q2Z9V4B8_DEFAULT")
# Ensure minimum 32-byte key length for HS256 to eliminate RFC 7518 warnings
JWT_SECRET_KEY = hashlib.sha256(raw_jwt_secret.encode("utf-8")).hexdigest()
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=JWT_EXPIRATION_DAYS)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def normalize_phone(phone: str) -> str:
    p = phone.strip().replace(" ", "").replace("-", "")
    if p.startswith("+91"):
        return p
    if p.startswith("91") and len(p) == 12:
        return "+" + p
    if len(p) == 10 and p.isdigit():
        return "+91" + p
    if not p.startswith("+"):
        return "+91" + p[-10:]
    return p


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token. Please sign in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User account not found")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required for this action.",
        )
    return current_user


@auth_router.post("/register")
def register_user(req: UserRegisterRequest, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    clean_phone = normalize_phone(req.phone)

    # 1. Check duplicate email / phone
    existing = (
        db.query(User)
        .filter((User.email == clean_email) | (User.phone == clean_phone))
        .first()
    )
    if existing:
        if existing.email == clean_email:
            raise HTTPException(status_code=400, detail="Email is already registered.")
        else:
            raise HTTPException(status_code=400, detail="Phone number is already registered.")

    # 2. Hash password & create user
    hashed = pwd_context.hash(req.password)
    new_user = User(
        id=str(uuid.uuid4()),
        full_name=req.full_name.strip(),
        email=clean_email,
        phone=clean_phone,
        password_hash=hashed,
        role="user",
        is_phone_verified=False,
        is_email_verified=False,
        alert_language=req.alert_language or "bilingual",
        sms_enabled=True,
        email_enabled=True,
    )
    db.add(new_user)
    db.flush()

    # 3. Generate TWO separate 6-digit OTPs
    phone_otp = str(secrets.randbelow(900000) + 100000)
    email_otp = str(secrets.randbelow(900000) + 100000)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    otp_phone_rec = OTPVerification(
        id=str(uuid.uuid4()),
        user_id=new_user.id,
        channel="sms",
        otp_code=phone_otp,
        expires_at=expires_at,
        is_used=False,
    )
    otp_email_rec = OTPVerification(
        id=str(uuid.uuid4()),
        user_id=new_user.id,
        channel="email",
        otp_code=email_otp,
        expires_at=expires_at,
        is_used=False,
    )
    db.add(otp_phone_rec)
    db.add(otp_email_rec)
    db.commit()

    # 4. Dispatch OTPs
    sms_msg = (
        f"Your KrishnaFlow  code is: {phone_otp}. Valid for 5 minutes. Do not share."
    )
    send_sms(db, clean_phone, sms_msg, alert_type="OTP", user_id=new_user.id)

    email_html = f"""
    <div style="font-family:sans-serif;background:#0b0f19;color:#fff;padding:24px;border-radius:8px;max-width:500px;">
      <h2 style="color:#38bdf8;margin-top:0;">KrishnaFlow AI Verification</h2>
      <p>Hello {new_user.full_name},</p>
      <p>Your 6-digit email verification code is:</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#34d399;background:#1e293b;padding:12px 20px;display:inline-block;border-radius:6px;margin:12px 0;">{email_otp}</div>
      <p style="color:#94a3b8;font-size:12px;">This code is valid for 5 minutes. If you did not request this, please ignore.</p>
    </div>
    """
    send_email(
        db,
        clean_email,
        "KrishnaFlow Verification Code",
        email_html,
        f"Your KrishnaFlow verification code is: {email_otp} (valid 5 min).",
        alert_type="OTP",
        user_id=new_user.id,
    )

    return {
        "user_id": new_user.id,
        "message": "Registration successful! Two verification codes have been dispatched to your Phone and Email.",
    }


@auth_router.post("/verify-otp")
def verify_otp(req: VerifyOTPRequest, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    now = datetime.now(timezone.utc)

    # Check Phone OTP
    phone_rec = (
        db.query(OTPVerification)
        .filter(
            OTPVerification.user_id == user.id,
            OTPVerification.channel == "sms",
            OTPVerification.otp_code == req.phone_otp.strip(),
            OTPVerification.is_used == False,
            OTPVerification.expires_at > now,
        )
        .order_by(OTPVerification.created_at.desc())
        .first()
    )

    # Check Email OTP
    email_rec = (
        db.query(OTPVerification)
        .filter(
            OTPVerification.user_id == user.id,
            OTPVerification.channel == "email",
            OTPVerification.otp_code == req.email_otp.strip(),
            OTPVerification.is_used == False,
            OTPVerification.expires_at > now,
        )
        .order_by(OTPVerification.created_at.desc())
        .first()
    )

    if not phone_rec and not email_rec:
        raise HTTPException(status_code=400, detail="Invalid or expired Phone and Email OTPs.")
    if not phone_rec:
        raise HTTPException(status_code=400, detail="Invalid or expired Phone OTP.")
    if not email_rec:
        raise HTTPException(status_code=400, detail="Invalid or expired Email OTP.")

    # Mark both used & user verified
    phone_rec.is_used = True
    email_rec.is_used = True
    user.is_phone_verified = True
    user.is_email_verified = True
    db.commit()

    token = create_access_token({"sub": user.id, "role": user.role, "email": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "alert_language": user.alert_language,
            "sms_enabled": user.sms_enabled,
            "email_enabled": user.email_enabled,
        },
    }


@auth_router.post("/resend-otp")
def resend_otp(req: ResendOTPRequest, db: Session = Depends(get_db)):
    clean_email = req.email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    # 60s cooldown check
    now = datetime.now(timezone.utc)
    latest_otp = (
        db.query(OTPVerification)
        .filter(OTPVerification.user_id == user.id)
        .order_by(OTPVerification.created_at.desc())
        .first()
    )
    if latest_otp and latest_otp.created_at:
        # Normalize timezone
        created_tz = latest_otp.created_at
        if created_tz.tzinfo is None:
            created_tz = created_tz.replace(tzinfo=timezone.utc)
        diff = (now - created_tz).total_seconds()
        if diff < 60:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {int(60 - diff)} seconds before requesting new verification codes.",
            )

    expires_at = now + timedelta(minutes=5)
    phone_otp = str(secrets.randbelow(900000) + 100000)
    email_otp = str(secrets.randbelow(900000) + 100000)

    channel_mode = (req.channel or "all").lower()

    if channel_mode in ["all", "sms"]:
        db.add(
            OTPVerification(
                id=str(uuid.uuid4()),
                user_id=user.id,
                channel="sms",
                otp_code=phone_otp,
                expires_at=expires_at,
            )
        )
        send_sms(
            db,
            user.phone,
            f"Your new KrishnaFlow verification code is: {phone_otp}. Valid 5 min.",
            alert_type="OTP",
            user_id=user.id,
        )

    if channel_mode in ["all", "email"]:
        db.add(
            OTPVerification(
                id=str(uuid.uuid4()),
                user_id=user.id,
                channel="email",
                otp_code=email_otp,
                expires_at=expires_at,
            )
        )
        email_html = f"""
        <div style="font-family:sans-serif;background:#0b0f19;color:#fff;padding:24px;border-radius:8px;">
          <h2 style="color:#38bdf8;">KrishnaFlow AI Verification</h2>
          <p>Your new email verification code is:</p>
          <div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#34d399;background:#1e293b;padding:12px;display:inline-block;border-radius:6px;">{email_otp}</div>
        </div>
        """
        send_email(
            db,
            user.email,
            "KrishnaFlow New Verification Code",
            email_html,
            f"Your new verification code is: {email_otp}",
            alert_type="OTP",
            user_id=user.id,
        )

    db.commit()
    return {
        "message": "New verification codes sent successfully.",
    }


@auth_router.post("/login")
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    ident = req.identifier.strip()
    clean_ident_email = ident.lower()
    clean_ident_phone = normalize_phone(ident)

    user = (
        db.query(User)
        .filter((User.email == clean_ident_email) | (User.phone == clean_ident_phone))
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email/phone or password.")

    if not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email/phone or password.")

    # Admin is pre-verified; citizen users must have verified phone and email
    if user.role != "admin" and (not user.is_phone_verified or not user.is_email_verified):
        raise HTTPException(
            status_code=403,
            detail="Verification pending. Please verify your phone and email OTPs to activate your account.",
        )

    token = create_access_token({"sub": user.id, "role": user.role, "email": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "alert_language": user.alert_language,
            "sms_enabled": user.sms_enabled,
            "email_enabled": user.email_enabled,
        },
    }


@auth_router.get("/me")
def get_current_user_profile(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    locations = (
        db.query(UserLocation)
        .filter(UserLocation.user_id == current_user.id)
        .order_by(UserLocation.is_primary.desc(), UserLocation.created_at.desc())
        .all()
    )

    return {
        "user": {
            "id": current_user.id,
            "full_name": current_user.full_name,
            "email": current_user.email,
            "phone": current_user.phone,
            "role": current_user.role,
            "alert_language": current_user.alert_language,
            "sms_enabled": current_user.sms_enabled,
            "email_enabled": current_user.email_enabled,
        },
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
            }
            for loc in locations
        ],
    }
