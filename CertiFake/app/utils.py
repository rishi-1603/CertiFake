from pathlib import Path
from uuid import uuid4
import os

ALLOWED_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}

def allowed_mime(content_type: str) -> bool:
    return content_type in ALLOWED_MIME

def ensure_dirs():
    Path("data/uploads").mkdir(parents=True, exist_ok=True)
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("static").mkdir(parents=True, exist_ok=True)
    Path("templates").mkdir(parents=True, exist_ok=True)

def save_path(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".pdf"]:
        ext = ".bin"
    return str(Path("data/uploads") / f"{uuid4().hex}{ext}")