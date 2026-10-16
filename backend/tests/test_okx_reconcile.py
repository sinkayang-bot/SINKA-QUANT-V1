"""Unit tests for fail-closed OKX position reconciliation."""
import pytest
from okx_reconcile import reconcile_positions

LOCAL = {"symbol": "BTC-USDT-SWAP", "side": 1, "contracts": "2"}
REMOTE = {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
          "posSide": "long", "pos": "2"}

def test_flat_account_ready():
    assert reconcile_positions(None, [])["ready"] is True

def test_matching_isolated_position():
    assert reconcile_positions(LOCAL, [REMOTE])["ready"] is True

@pytest.mark.parametrize("local,remote,reason", [
    (None, [REMOTE], "exchange_position_not_tracked"),
    (LOCAL, [], "local_position_missing_on_exchange"),
    (LOCAL, [REMOTE | {"pos": "3"}], "position_quantity_mismatch"),
    (LOCAL, [REMOTE | {"posSide": "short"}], "position_side_mismatch"),
    (LOCAL, [REMOTE | {"mgnMode": "cross"}], "unexpected_exchange_exposure"),
    (LOCAL, [REMOTE, REMOTE], "multiple_exchange_positions"),
    (LOCAL, [REMOTE | {"instId": "ETH-USDT-SWAP"}], "instrument_mismatch"),
])
def test_mismatches_lock_new_orders(local, remote, reason):
    result = reconcile_positions(local, remote)
    assert result == {"ready": False, "reason": reason}

def test_invalid_quantity_rejected():
    with pytest.raises(ValueError):
        reconcile_positions(None, [REMOTE | {"pos": "NaN"}])

def test_net_mode_negative_position_is_valid_short():
    local = {"symbol": "BTC-USDT-SWAP", "side": -1, "contracts": "2"}
    row = {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
           "posSide": "net", "pos": "-2"}
    assert reconcile_positions(local, [row]) == {"ready": True, "reason": "matched"}

def test_untracked_negative_position_blocks_new_orders():
    row = {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
           "posSide": "net", "pos": "-1"}
    assert reconcile_positions(None, [row])["reason"] == "exchange_position_not_tracked"

def test_negative_local_quantity_is_invalid():
    row = {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
           "posSide": "net", "pos": "-2"}
    import pytest
    with pytest.raises(ValueError):
        reconcile_positions({"symbol": "BTC-USDT-SWAP", "side": -1,
                             "contracts": "-2"}, [row])
