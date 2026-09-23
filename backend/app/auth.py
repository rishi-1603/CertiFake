"""JWT authentication backed by the Postgres `users` table.

This replaces two things that existed before consolidation:
  1. The now-removed auth that used to live on the standalone `app/main.py`
     demo backend (retired -- see README "Architecture" section) which
     stored users in a flat JSON file on local disk (`data/users.json`) and
     therefore could not work correctly across multiple API-gateway
     replicas or survive a container restart.
  2. The commit `Remove authentication entirely for public access`, which
     left the distributed Kafka/S3 gateway (`app/api.py`) with NO auth at
     all -- anyone could POST arbitrary files to /analyze and read anyone
     else's /status/{id} or /report/{id} by guessing/enumerating the UUID.

This module is intentionally the single source of truth for auth in the
consolidated architecture: every request that creates or reads an analysis
goes through `require_user`, and analyses are scoped to `owner_id` so one
user cannot read another user's results even with a valid token of their
own (see the ownership check in app/api.py).
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SessionLocal, User

security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def register(db: Session, email: str, password: str) -> tuple[User, str]:
    email = email.strip().lower()
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(id=uuid.uuid4().hex, email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, _create_access_token(user.id)


def login(db: Session, email: str, password: str) -> tuple[User, str]:
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.password_hash):
        # Deliberately identical error for "no such user" and "wrong password"
        # so the API doesn't leak which emails are registered (user
        # enumeration is an OWASP-listed authentication weakness).
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return user, _create_access_token(user.id)


def require_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(_get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists.")
    return user
