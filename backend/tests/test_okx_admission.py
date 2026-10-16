"""Admission policy tests; no OKX connectivity or trading."""
import pytest
from okx_admission import evaluate
from okx_sync import SyncStatus
from datetime import datetime, timezone

SPEC = {"instId": "BTC-USDT-SWAP", "instType": "SWAP",
        "state": "live", "ctType": "linear", "settleCcy": "USDT",
        "ctValCcy": "BTC", "ctVal": "0.01",
        "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1"}
SYNC = SyncStatus(True, 'synced', datetime.now(timezone.utc).isoformat(), True, True)
BASE = dict(local_position=None, exchange_positions=[],
            instrument=SPEC, side="buy", contracts="0.01",
            price="100000.0", stop="99500.0",
            equity_usdt="10000", leverage=2,
            monitor_healthy=True, orders_synced=True,
            kill_switch=False, demo_verified=True,
            sync_status=SYNC, current_session_verified=True,
            pending_order_count=0)

def check(**changes):
    return evaluate(**(BASE | changes))

def test_demo_admission_only():
    result = check()
    assert result.allowed
    assert result.order["live_submission_allowed"] is False

@pytest.mark.parametrize("change,reason", [
    ({"live_requested": True}, "live_trading_locked"),
    ({"kill_switch": True}, "kill_switch_active"),
    ({"monitor_healthy": False}, "monitor_unhealthy"),
    ({"orders_synced": False}, "orders_not_reconciled"),
    ({"demo_verified": False}, "demo_not_verified"),
    ({"exchange_positions": [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated", "posSide": "long", "pos": "1"}]}, "exchange_position_not_tracked"),
    ({"leverage": 5}, "risk_check_failed"),
    ({"pending_order_count": 1}, "pending_orders_exist"),
    ({"pending_order_count": None}, "pending_order_count_unverified"),
    ({"pending_order_count": True}, "pending_order_count_unverified"),
])
def test_gate_denies_unsafe_requests(change, reason):
    assert check(**change).reason == reason
    assert not check(**change).allowed

def test_open_local_position_denied():
    local = {"symbol": "BTC-USDT-SWAP", "side": 1, "contracts": "1"}
    remote = [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated", "posSide": "long", "pos": "1"}]
    assert check(local_position=local, exchange_positions=remote).reason == "position_already_open"


def test_missing_fresh_sync_denied():
    assert check(sync_status=None).reason == "sync_missing"

def test_restart_denied_even_with_cached_sync():
    assert check(current_session_verified=False).reason == "restart_requires_fresh_sync"
