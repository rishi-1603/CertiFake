"""Upload validation for /analyze.

Defense in depth against malicious uploads, applied to the canonical
`app/api.py`, which (after the "Remove authentication entirely for public
access" commit) had no validation at all beyond a bare size check:

  1. declared Content-Type is checked against an allow-list
  2. the file extension (from the basename only, ignoring any path
     component -- defends against path traversal like '../../x.png') must
     match that Content-Type
  3. the file's actual magic bytes are sniffed with libmagic and must match
     a real image/PDF signature -- this is what stops someone renaming a
     script or executable to "certificate.png" and relying on the
     (client-controlled, trivially spoofable) Content-Type header alone
  4. size limit + empty-file rejection

Any single one of 1-3 failing is a 400; a real production system would also
run a malware scan (e.g. ClamAV) on the bytes before they reach storage --
that is explicitly listed as unimplemented in the README rather than
pretended to be covered here.
"""
import os

import magic
from fastapi import HTTPException

from app.config import settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# What libmagic is allowed to report for each declared Content-Type. A file
# that claims to be a PNG but whose actual bytes are, say, an ELF executable
# or a shell script will not appear in this set and gets rejected.
_MAGIC_ALLOWLIST: dict[str, set[str]] = {
    "image/jpeg": {"image/jpeg"},
    "image/png": {"image/png"},
    "image/webp": {"image/webp"},
    "application/pdf": {"application/pdf"},
}


def _safe_extension(filename: str | None) -> str:
    """Return the lowercase extension of a filename, defending against
    path-traversal attempts (e.g. '../../etc/passwd.png') by only ever
    looking at the basename, never the full client-supplied path."""
    base = os.path.basename(filename or "")
    return os.path.splitext(base)[1].lower()


def validate_upload(filename: str | None, data: bytes, declared_content_type: str | None) -> str:
    """Validate an uploaded file. Returns the declared content type on success.

    Raises HTTPException(400/413) on any validation failure.
    """
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (max {settings.max_upload_mb}MB).")

    if declared_content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    ext = _safe_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file extension.")

    if declared_content_type == "application/pdf" and ext != ".pdf":
        raise HTTPException(status_code=400, detail="File extension does not match declared PDF content type.")
    if declared_content_type.startswith("image/") and ext == ".pdf":
        raise HTTPException(status_code=400, detail="File extension does not match declared image content type.")

    try:
        sniffed = magic.from_buffer(data, mime=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not verify file contents.")

    if sniffed not in _MAGIC_ALLOWLIST.get(declared_content_type, set()):
        raise HTTPException(
            status_code=400,
            detail=f"File contents do not match the declared type (detected: {sniffed}).",
        )

    return declared_content_type
