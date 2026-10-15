"""Unit tests for isolated USDT swap risk checks."""
import pytest
from okx_risk import validate_isolated_order

BASE = dict(symbol="BTC-USDT-SWAP", side="buy", contracts="0.01",
            price="100000", stop="99500", equity_usdt="10000",
            ct_val="0.01", lot_sz="0.01", min_sz="0.01", leverage=2)

def check(**changes):
    return validate_isolated_order(**(BASE | changes))

def test_valid_isolated_order_is_not_executable():
    result = check()
    assert result["tdMode"] == "isolated"
    assert result["live_submission_allowed"] is False

@pytest.mark.parametrize("change", [
    {"symbol": "BTC-USDT"},
    {"side": "hold"},
    {"leverage": 4},
    {"contracts": "0.015"},
    {"contracts": "0"},
    {"stop": "100000"},
    {"stop": "101000"},
    {"price": "NaN"},
    {"equity_usdt": "0"},
    {"contracts": "2"},
])
def test_invalid_orders_rejected(change):
    with pytest.raises(ValueError):
        check(**change)

def test_short_stop_must_be_above_entry():
    with pytest.raises(ValueError):
        check(side="sell", stop="99000")

def test_short_with_valid_stop():
    assert check(side="sell", stop="100500")["side"] == "sell"
