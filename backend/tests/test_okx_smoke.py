"""Smoke checks use fakes and never contact OKX."""
import asyncio
from okx_smoke import inspect_demo

class Fake:
    async def account_config(self): return [{"perm": "read_only"}]
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


import pytest


@pytest.mark.parametrize("config", [
    [{"perm": "read_only,trade"}], [{"perm": "read_only,withdraw"}],
    [{"perm": "read_only,withdraw,trade"}], [{"perm": "trade"}],
    [{"perm": "unknown"}], [{"perm": ""}], [{"perm": None}], [{}], [],
    [{"perm": "read_only"}, {"perm": "trade"}], {"perm": "read_only"},
    [{"perm": ["read_only"]}], [None],
])
def test_unsafe_or_unknown_api_permissions_stop_before_balance_query(config):
    class Permissions(Fake):
        async def account_config(self): return config
        async def balances(self):
            raise AssertionError("No account reads after permission rejection")
    result = asyncio.run(inspect_demo(Permissions()))
    assert result["ok"] is False
    assert result["failed_stage"] == "account_config"
    assert result["trading_enabled"] is False
    assert "perm" not in result


def test_config_error_is_sanitized_and_stops_followup_reads():
    class Failure(Fake):
        async def account_config(self):
            raise RuntimeError("private-api-key-permissions")
        async def balances(self):
            raise AssertionError("No followup request allowed")
    result = asyncio.run(inspect_demo(Failure()))
    assert result["failed_stage"] == "account_config"
    assert "private-api-key-permissions" not in str(result)


def test_readonly_permission_success_has_fixed_evidence_without_account_details():
    class Config(Fake):
        async def account_config(self):
            return [{"perm": "read_only", "uid": "private-uid", "label": "private-label"}]
    result = asyncio.run(inspect_demo(Config()))
    assert result["ok"] is True
    assert result["account_config_endpoint"] == "ok"
    assert result["api_permissions_readonly"] is True
    assert "private-uid" not in str(result)
    assert "private-label" not in str(result)


@pytest.mark.parametrize("permissions, expected_requests, success", [
    ("read_only", 5, True), ("read_only,trade", 1, False),
])
def test_full_smoke_transport_is_demo_get_only(monkeypatch, permissions, expected_requests, success):
    import httpx
    from okx_readonly import OKXReadOnly
    calls = []
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "www.okx.com"
        assert request.headers["x-simulated-trading"] == "1"
        calls.append(request.url.path)
        data = [{"perm": permissions}] if request.url.path == "/api/v5/account/config" else []
        return httpx.Response(200, json={"code": "0", "data": data})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:
                        client_type(**kwargs, transport=httpx.MockTransport(handler)))
    reader = OKXReadOnly(key="test-key", secret="test-secret", passphrase="test-pass", demo=True)
    result = asyncio.run(inspect_demo(reader))
    assert result["ok"] is success
    assert len(calls) == expected_requests
    assert calls[0] == "/api/v5/account/config"
    assert not any(secret in str(result) for secret in ("test-key", "test-secret", "test-pass"))
