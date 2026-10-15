"""Persist non-sensitive OKX sync status atomically.

This is a diagnostic cache, NEVER proof of current-session verification.
No API credentials, balances, positions or order IDs are stored here.
"""
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from okx_sync import SyncStatus

FIELDS = frozenset(SyncStatus.__dataclass_fields__)

def save_sync_status(path, status):
    if not isinstance(status, SyncStatus):
        raise ValueError("Invalid sync status")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(status), separators=(",", ":"))
    fd, temp = tempfile.mkstemp(prefix=".okx-sync-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def load_sync_status(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != FIELDS:
            return None
        if not (type(data["healthy"]) is bool and
                type(data["positions_matched"]) is bool and
                type(data["orders_matched"]) is bool and
                type(data["reason"]) is str and
                type(data["checked_at"]) is str):
            return None
        return SyncStatus(**data)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
