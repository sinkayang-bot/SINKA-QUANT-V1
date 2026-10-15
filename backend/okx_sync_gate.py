"""Fail-closed freshness and restart gate for read-only OKX sync results.

Persist a status snapshot only after a successful sync. On process restart,
do not trust persisted success until this process performs a fresh sync.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from okx_sync import SyncStatus

MAX_SYNC_AGE_SECONDS = 30

@dataclass(frozen=True)
class SyncGate:
    ready: bool
    reason: str

def assess_sync(status: SyncStatus | None, *, current_session_verified=False,
                now=None, max_age_seconds=MAX_SYNC_AGE_SECONDS):
    if not current_session_verified:
        return SyncGate(False, "restart_requires_fresh_sync")
    if status is None or not isinstance(status, SyncStatus):
        return SyncGate(False, "sync_missing")
    if not status.healthy or not status.positions_matched or not status.orders_matched:
        return SyncGate(False, "sync_unhealthy")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= MAX_SYNC_AGE_SECONDS:
        return SyncGate(False, "invalid_freshness_limit")
    try:
        checked = datetime.fromisoformat(status.checked_at)
        current = now or datetime.now(timezone.utc)
        if checked.tzinfo is None or current.tzinfo is None:
            raise ValueError("Timezone required")
        age = (current - checked).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return SyncGate(False, "sync_timestamp_invalid")
    if age < 0 or age > max_age_seconds:
        return SyncGate(False, "sync_stale")
    return SyncGate(True, "fresh_sync_verified")
