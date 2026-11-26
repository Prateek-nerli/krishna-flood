import uuid
from sqlalchemy import Column, String, Boolean, Float, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user", nullable=False)  # "user" | "admin"
    is_phone_verified = Column(Boolean, default=False, nullable=False)
    is_email_verified = Column(Boolean, default=False, nullable=False)
    alert_language = Column(String(20), default="bilingual", nullable=False)  # "bilingual" | "en" | "kn"
    sms_enabled = Column(Boolean, default=True, nullable=False)
    email_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    locations = relationship("UserLocation", back_populates="user", cascade="all, delete-orphan")
    otps = relationship("OTPVerification", back_populates="user", cascade="all, delete-orphan")
    alert_logs = relationship("AlertLog", back_populates="user", cascade="all, delete-orphan")


class OTPVerification(Base):
    __tablename__ = "otp_verifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(20), nullable=False)  # "sms" | "email"
    otp_code = Column(String(10), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="otps")


class UserLocation(Base):
    __tablename__ = "user_locations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(80), nullable=False, default="Home")  # e.g., "Home", "Farm"
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    place_name = Column(String(200), nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_risk_level = Column(String(20), default="SAFE", nullable=False)  # "SAFE" | "WARNING" | "CRITICAL"
    last_flood_days_json = Column(Text, nullable=True)  # JSON string e.g. ["Day 1 (+24h)", "Day 2 (+48h)"]
    last_snapshot_json = Column(Text, nullable=True)  # JSON string of summary telemetry
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="locations")
    alert_logs = relationship("AlertLog", back_populates="location")


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    location_id = Column(String(36), ForeignKey("user_locations.id", ondelete="SET NULL"), nullable=True)
    alert_type = Column(String(20), nullable=False)  # "REAL" | "SIMULATION" | "BROADCAST" | "TEST" | "OTP"
    channel = Column(String(10), nullable=False)  # "SMS" | "EMAIL"
    language = Column(String(20), default="bilingual", nullable=False)  # "en" | "kn" | "bilingual"
    recipient = Column(String(120), nullable=False)
    flood_days = Column(String(120), nullable=True)
    message_body = Column(Text, nullable=False)
    status = Column(String(30), nullable=False)  # "SENT" | "SIMULATED_OFFLINE" | "QUOTA_SKIPPED" | "FAILED"
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="alert_logs")
    location = relationship("UserLocation", back_populates="alert_logs")
