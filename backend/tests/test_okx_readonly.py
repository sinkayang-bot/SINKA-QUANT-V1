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


def test_transport_uses_only_get_with_demo_header(monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"code": "0", "data": []}
    class Transport:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://www.okx.com"
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, path, headers):
            assert headers["x-simulated-trading"] == "1"
            calls.append(path)
            return Response()
    monkeypatch.setattr("okx_readonly.httpx.AsyncClient", Transport)
    assert asyncio.run(client().balances()) == []
    assert calls == ["/api/v5/account/balance"]
