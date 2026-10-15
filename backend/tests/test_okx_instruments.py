"""Tests for OKX linear USDT swap instrument metadata validation."""
import pytest
from okx_instruments import instrument_spec, validate_with_instrument

INSTRUMENT = {
    "instId": "BTC-USDT-SWAP", "instType": "SWAP",
    "state": "live", "ctType": "linear", "settleCcy": "USDT",
    "ctValCcy": "BTC", "ctVal": "0.01", "lotSz": "0.01",
    "minSz": "0.01", "tickSz": "0.1",
}

def test_instrument_accepted():
    assert instrument_spec(INSTRUMENT)["symbol"] == "BTC-USDT-SWAP"

@pytest.mark.parametrize("change", [
    {"state": "suspend"},
    {"ctType": "inverse"},
    {"settleCcy": "BTC"},
    {"ctValCcy": "USDT"},
    {"instType": "SPOT"},
    {"lotSz": "0"},
    {"tickSz": "NaN"},
])
def test_invalid_instrument_rejected(change):
    with pytest.raises(ValueError):
        instrument_spec(INSTRUMENT | change)

def test_risk_uses_contract_metadata():
    result = validate_with_instrument(
        instrument=INSTRUMENT, side="buy", contracts="0.01",
        price="100000.0", stop="99500.0", equity_usdt="10000",
        leverage=2)
    assert result["tdMode"] == "isolated"
    assert result["live_submission_allowed"] is False

def test_reject_price_off_tick():
    with pytest.raises(ValueError, match="tick"):
        validate_with_instrument(
            instrument=INSTRUMENT, side="buy", contracts="0.01",
            price="100000.05", stop="99500.0", equity_usdt="10000",
            leverage=2)
