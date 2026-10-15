"""Central trade admission policy. All failures deny new orders.

Pure checks only. No API credentials, network access, or order submission.
"""
from dataclasses import dataclass
from okx_reconcile import reconcile_positions
from okx_instruments import validate_with_instrument
from okx_sync_gate import assess_sync

@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    order: dict | None = None

def evaluate(*, local_position, exchange_positions, instrument,
             side, contracts, price, stop, equity_usdt, leverage,
             monitor_healthy, orders_synced, kill_switch,
             demo_verified, live_requested=False, sync_status=None,
             current_session_verified=False):
    if live_requested:
        return Admission(False, "live_trading_locked")
    freshness = assess_sync(sync_status, current_session_verified=current_session_verified)
    if not freshness.ready:
        return Admission(False, freshness.reason)
    if kill_switch:
        return Admission(False, "kill_switch_active")
    if not monitor_healthy:
        return Admission(False, "monitor_unhealthy")
    if not orders_synced:
        return Admission(False, "orders_not_reconciled")
    if not demo_verified:
        return Admission(False, "demo_not_verified")
    try:
        match = reconcile_positions(local_position, exchange_positions)
    except (ValueError, TypeError):
        return Admission(False, "position_data_invalid")
    if not match["ready"]:
        return Admission(False, match["reason"])
    if local_position is not None:
        return Admission(False, "position_already_open")
    try:
        order = validate_with_instrument(
            instrument=instrument, side=side, contracts=contracts,
            price=price, stop=stop, equity_usdt=equity_usdt,
            leverage=leverage)
    except (ValueError, TypeError, ArithmeticError):
        return Admission(False, "risk_check_failed")
    return Admission(True, "demo_preflight_passed", order)
