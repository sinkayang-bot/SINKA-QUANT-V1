"""Tests for OKX read-only transport and explicit trade lock."""
import asyncio
import pytest
from okx_readonly import OKXReadOnly

def client():
    return OKXReadOnly(key="test-key", secret="test-secret", passphrase="test-pass", demo=True)

def test_demo_header_and_signature():
    headers = client()._headers("/api/v5/account/balance")
    assert headers["x-simulated-trading"] == "1"
    assert headers["OK-ACCESS-SIGN"]
    assert headers["OK-ACCESS-KEY"] == "test-key"

def test_live_mode_does_not_claim_demo():
    obj = OKXReadOnly(key="test-key", secret="test-secret", passphrase="test-pass", demo=False)
    assert "x-simulated-trading" not in obj._headers("/api/v5/account/balance")

def test_trade_endpoint_is_rejected_before_network():
    with pytest.raises(ValueError, match="Order lookup requires"):
        asyncio.run(client().get("/api/v5/trade/order"))

def test_only_swap_positions_allowed():
    with pytest.raises(ValueError, match="swap positions"):
        asyncio.run(client().get("/api/v5/account/positions", {"instType": "SPOT"}))


def test_trade_submission_remains_blocked():
    with pytest.raises(ValueError, match="not permitted"):
        asyncio.run(client().get("/api/v5/trade/cancel-order"))
