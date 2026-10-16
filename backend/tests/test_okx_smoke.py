"""Smoke checks use fakes and never contact OKX."""
import asyncio
from okx_smoke import inspect_demo

class Fake:
    async def balances(self): return []
    async def swap_positions(self): return []
    async def swap_instruments(self): return []
    async def get(self, path, params):
        assert path == "/api/v5/trade/orders-pending"
        assert params["instType"] == "SWAP"
        return []

def test_empty_demo_account_connection_passes():
    result = asyncio.run(inspect_demo(Fake()))
    assert result["ok"]
    assert result["trading_enabled"] is False
    assert result["pending_order_count"] == 0

def test_network_errors_return_no_sensitive_data():
    class Failure(Fake):
        async def balances(self):
            raise RuntimeError("private-secret-stub")
    result = asyncio.run(inspect_demo(Failure()))
    assert result["ok"] is False
    assert result["trading_enabled"] is False
    assert "private-secret-stub" not in str(result)

def test_unexpected_position_response_fails():
    class Malformed(Fake):
        async def swap_positions(self): return {"unexpected": "value"}
    assert asyncio.run(inspect_demo(Malformed()))["ok"] is False
