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


def test_failure_stage_is_fixed_and_later_calls_are_skipped():
    class Failure(Fake):
        async def swap_positions(self):
            raise RuntimeError("private-position-value")
        async def swap_instruments(self):
            raise AssertionError("must not call the next endpoint")
    result = asyncio.run(inspect_demo(Failure()))
    assert result["failed_stage"] == "positions"
    assert "private-position-value" not in str(result)


def test_malformed_balances_stop_before_other_requests():
    class Malformed(Fake):
        async def balances(self): return {"private-balance": 123}
        async def swap_positions(self):
            raise AssertionError("must not call the next endpoint")
    result = asyncio.run(inspect_demo(Malformed()))
    assert result["failed_stage"] == "balances"
    assert "private-balance" not in str(result)


def test_missing_credentials_print_sanitized_json(monkeypatch, capsys):
    import json
    import pytest
    from okx_smoke import main
    monkeypatch.setenv("SINKA_OKX_DEMO", "true")
    monkeypatch.setenv("SINKA_OKX_READONLY_SMOKE", "true")
    for name in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "credentials_not_configured"
    assert result["trading_enabled"] is False


def test_startup_guards_prevent_client_creation(monkeypatch):
    import pytest
    import okx_smoke
    def blocked(*args, **kwargs):
        raise AssertionError("client must not be created")
    monkeypatch.setattr(okx_smoke, "OKXReadOnly", blocked)
    monkeypatch.delenv("SINKA_OKX_DEMO", raising=False)
    with pytest.raises(SystemExit, match="Demo mode must"):
        okx_smoke.main()
    monkeypatch.setenv("SINKA_OKX_DEMO", "true")
    monkeypatch.delenv("SINKA_OKX_READONLY_SMOKE", raising=False)
    with pytest.raises(SystemExit, match="disabled by default"):
        okx_smoke.main()
