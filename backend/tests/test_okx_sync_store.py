"""Atomic OKX sync diagnostic cache tests."""
from datetime import datetime, timezone
from okx_sync import SyncStatus
from okx_sync_store import save_sync_status, load_sync_status
from okx_sync_gate import assess_sync

def test_roundtrip_and_restart_denial(tmp_path):
    path = tmp_path / "sync.json"
    status = SyncStatus(True, "synced",
                        datetime.now(timezone.utc).isoformat(), True, True)
    save_sync_status(path, status)
    cached = load_sync_status(path)
    assert cached == status
    assert not assess_sync(cached, current_session_verified=False).ready

def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "sync.json"
    path.write_text('{"healthy":true}')
    assert load_sync_status(path) is None

def test_missing_cache_is_ignored(tmp_path):
    assert load_sync_status(tmp_path / "missing.json") is None
