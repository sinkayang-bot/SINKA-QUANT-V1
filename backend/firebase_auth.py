"""Owner-only Firebase ID token validation for the future Firestore API.

Not wired into the legacy SQLite API yet. No credentials are bundled here.
"""
import os

from fastapi import Header, HTTPException
from firebase_admin import auth, initialize_app, get_app

def _firebase_app():
    try:
        return get_app()
    except ValueError:
        return initialize_app()

def verify_owner_token(authorization: str | None = Header(default=None)) -> str:
    owner = os.environ.get("SINKA_OWNER_UID", "").strip()
    if not owner:
        raise HTTPException(status_code=503, detail="Owner UID is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Firebase login required")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Firebase login required")
    try:
        decoded = auth.verify_id_token(token, app=_firebase_app(), check_revoked=True)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase token")
    uid = decoded.get("uid")
    if not uid or uid != owner:
        raise HTTPException(status_code=403, detail="Not authorized for this account")
    return uid
