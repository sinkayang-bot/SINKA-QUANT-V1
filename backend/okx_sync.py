"""One-shot read-only OKX synchronization with fail-closed readiness.

Does not submit orders or enable trading. Caller owns persistence and scheduling.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from okx_reconcile import reconcile_positions
from okx_order_reader import OKXOrderReader

@dataclass(frozen=True)
class SyncStatus:
    healthy: bool
    reason: str
    checked_at: str
    positions_matched: bool
    orders_matched: bool

async def sync_once(*, reader: OKXOrderReader, local_position,
                    tracked_orders=(), orders_inventory_verified=False):
    now = datetime.now(timezone.utc).isoformat()
    try:
        positions = await reader.swap_positions()
        if not isinstance(positions, list):
            raise ValueError("Malformed positions response")
        position_status = reconcile_positions(local_position, positions)
        if not position_status["ready"]:
            return SyncStatus(False, position_status["reason"], now, False, False)
        for prior in tracked_orders:
            if prior.state not in ("filled", "canceled", "mmp_canceled"):
                await reader.refresh(prior)
        if not orders_inventory_verified:
            return SyncStatus(False, "order_inventory_unverified", now, True, False)
        return SyncStatus(True, "synced", now, True, True)
    except Exception:
        # No credentials or response bodies in errors returned to callers.
        return SyncStatus(False, "exchange_sync_failed", now, False, False)
