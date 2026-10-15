"""Firebase owner authorization tests; do not require live Firebase credentials."""
import importlib.util
import os
from pathlib import Path

from fastapi import HTTPException
import pytest

spec = importlib.util.spec_from_file_location("sinka_firebase_auth", Path(__file__).resolve().parents[1] / "firebase_auth.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unconfigured_owner_fails_closed(monkeypatch):
    monkeypatch.delenv("SINKA_OWNER_UID", raising=False)
    with pytest.raises(HTTPException) as exc:
        module.verify_owner_token("Bearer example")
    assert exc.value.status_code == 503


def test_missing_token_rejected(monkeypatch):
    monkeypatch.setenv("SINKA_OWNER_UID", "owner-uid")
    with pytest.raises(HTTPException) as exc:
        module.verify_owner_token(None)
    assert exc.value.status_code == 401


def test_other_user_rejected(monkeypatch):
    monkeypatch.setenv("SINKA_OWNER_UID", "owner-uid")
    monkeypatch.setattr(module, "_firebase_app", lambda: object())
    monkeypatch.setattr(module.auth, "verify_id_token", lambda *args, **kwargs: {"uid": "other-uid"})
    with pytest.raises(HTTPException) as exc:
        module.verify_owner_token("Bearer fake-token")
    assert exc.value.status_code == 403


def test_owner_accepted(monkeypatch):
    monkeypatch.setenv("SINKA_OWNER_UID", "owner-uid")
    monkeypatch.setattr(module, "_firebase_app", lambda: object())
    monkeypatch.setattr(module.auth, "verify_id_token", lambda *args, **kwargs: {"uid": "owner-uid"})
    assert module.verify_owner_token("Bearer fake-token") == "owner-uid"


def test_invalid_token_rejected(monkeypatch):
    monkeypatch.setenv("SINKA_OWNER_UID", "owner-uid")
    monkeypatch.setattr(module, "_firebase_app", lambda: object())
    def invalid(*args, **kwargs):
        raise ValueError("invalid token")
    monkeypatch.setattr(module.auth, "verify_id_token", invalid)
    with pytest.raises(HTTPException) as exc:
        module.verify_owner_token("Bearer fake-token")
    assert exc.value.status_code == 401
