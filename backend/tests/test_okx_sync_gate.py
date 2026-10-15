"""Tests for OKX sync freshness and process-restart safety."""
from datetime import datetime, timedelta, timezone
from okx_sync import SyncStatus
from okx_sync_gate import assess_sync

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)

def status(age=0, healthy=True):
    return SyncStatus(healthy, "synced",
                      (NOW - timedelta(seconds=age)).isoformat(),
                      True, True)

def test_recent_verified_sync_ready():
    assert assess_sync(status(29), current_session_verified=True, now=NOW).ready

def test_stale_sync_rejected():
    result = assess_sync(status(31), current_session_verified=True, now=NOW)
    assert not result.ready and result.reason == "sync_stale"

def test_restart_requires_new_sync():
    result = assess_sync(status(), current_session_verified=False, now=NOW)
    assert not result.ready and result.reason == "restart_requires_fresh_sync"

def test_failed_sync_rejected():
    assert not assess_sync(status(healthy=False), current_session_verified=True, now=NOW).ready

def test_future_timestamp_rejected():
    assert not assess_sync(status(-5), current_session_verified=True, now=NOW).ready

def test_naive_timestamp_rejected():
    bad = SyncStatus(True, "synced", "2026-10-16T12:00:00", True, True)
    assert not assess_sync(bad, current_session_verified=True, now=NOW).ready

def test_age_limit_cannot_be_relaxed():
    assert not assess_sync(status(50), current_session_verified=True,
                           now=NOW, max_age_seconds=60).ready
