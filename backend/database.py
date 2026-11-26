import os
import uuid
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from passlib.context import CryptContext

# Load .env from both project root and backend/.env
env_paths = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
]
for p in env_paths:
    if p.exists():
        load_dotenv(p, override=True)

raw_db_url = os.getenv("DATABASE_URL", "postgresql://postgres:2004@localhost:5432/krishnaflow")
# Normalize postgres:// and postgresql:// to postgresql+psycopg2:// for SQLAlchemy 2.0 with psycopg2
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif raw_db_url.startswith("postgresql://") and not raw_db_url.startswith("postgresql+"):
    raw_db_url = raw_db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

DATABASE_URL = raw_db_url

# Production-grade connection pooling suitable for NeonDB / PostgreSQL
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10,
    connect_args={"connect_timeout": 10} if "postgresql" in DATABASE_URL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Auto-creates all tables and seeds the initial Admin account from ADMIN_* environment variables.
    """
    from backend.db_models import User
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    # Seed Admin Account
    admin_name = os.getenv("ADMIN_NAME", "KrishnaAdmin")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@krishnaflow.in").strip().lower()
    admin_phone = os.getenv("ADMIN_PHONE", "+919876543210").strip()
    admin_pass = os.getenv("ADMIN_PASSWORD", "Admin@123")
    
    # Normalize phone: ensure +91 prefix
    if not admin_phone.startswith("+"):
        admin_phone = "+91" + admin_phone[-10:]
    
    db = SessionLocal()
    try:
        existing_admin = db.query(User).filter(
            (User.email == admin_email) | (User.role == "admin")
        ).first()
        
        if not existing_admin:
            hashed = pwd_context.hash(admin_pass)
            admin_user = User(
                id=str(uuid.uuid4()),
                full_name=admin_name,
                email=admin_email,
                phone=admin_phone,
                password_hash=hashed,
                role="admin",
                is_phone_verified=True,
                is_email_verified=True,
                alert_language="bilingual",
                sms_enabled=True,
                email_enabled=True,
            )
            db.add(admin_user)
            db.commit()
            print(f"[Database] Seeded initial Admin account: {admin_email} (Role: admin)")
        else:
            # Ensure admin is marked verified and role is admin
            if existing_admin.role != "admin" or not existing_admin.is_email_verified or not existing_admin.is_phone_verified:
                existing_admin.role = "admin"
                existing_admin.is_email_verified = True
                existing_admin.is_phone_verified = True
                db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Database] Notice during admin seeding: {e}")
    finally:
        db.close()
