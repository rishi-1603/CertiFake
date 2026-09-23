"""Convenience entrypoint: `uvicorn main:app` from the backend/ directory
runs the same canonical app as `uvicorn app.api:app` (and as docker-compose's
api-gateway service). There is exactly one backend app in this repository;
see app/api.py for why.
"""
from app.api import app

__all__ = ["app"]
