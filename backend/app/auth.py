import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from app.config import settings

security = HTTPBearer(auto_error=False)
USERS_FILE = Path("data/users.json")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def load_users():
    if not USERS_FILE.exists():
        # Initialize with admin user if file doesn't exist
        hashed_pw = pwd_context.hash(settings.admin_password)
        users = {settings.admin_username: hashed_pw}
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_users(users)
        return users
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        hashed_pw = pwd_context.hash(settings.admin_password)
        return {settings.admin_username: hashed_pw}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def signup(username: str, password: str) -> str:
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    users = load_users()
    if username in users:
        raise HTTPException(status_code=400, detail="Username already exists")
    users[username] = pwd_context.hash(password)
    save_users(users)
    return generate_token(username)

def login(username: str, password: str) -> str:
    users = load_users()
    hashed_password = users.get(username)
    if not hashed_password or not pwd_context.verify(password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return generate_token(username)
    
def generate_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

def require_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing token")
    token = credentials.credentials
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return data.get("sub", "admin")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
