"""HTTP tests use fake Firebase identities and fake OKX responses only."""
import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import firebase_auth
import okx_status_app as status_api


def setup(monkeypatch):
    monkeypatch.setenv("SINKA_OWNER_UID", "owner-test")
    monkeypatch.setenv("SINKA_OKX_READONLY_API", "true")
    monkeypatch.setenv("SINKA_OKX_DEMO", "true")
    monkeypatch.setattr(firebase_auth, "_firebase_app", lambda: object())
    def verify(token, **kwargs):
        assert kwargs["check_revoked"] is True
        if token == "owner-token": return {"uid": "owner-test"}
        if token == "other-token": return {"uid": "other-test"}
        raise ValueError("private-token-error")
    monkeypatch.setattr(firebase_auth.auth, "verify_id_token", verify)
    return TestClient(status_api.create_app())


def success():
    return {"ok": True, "mode": "demo_readonly", "api_permissions_readonly": True,
            "trading_enabled": False,
            **{stage + "_endpoint": "ok" for stage in status_api.STAGES}}


@pytest.mark.parametrize("token, code", [(None, 401), ("other-token", 403), ("expired", 401)])
def test_unauthorized_request_never_constructs_exchange_client(monkeypatch, token, code):
    client = setup(monkeypatch)
    def forbidden(**kwargs): raise AssertionError("Must not call OKX")
    monkeypatch.setattr(status_api, "OKXReadOnly", forbidden)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer " + token} if token else {})
    assert response.status_code == code
    assert "private-token-error" not in response.text
    assert response.headers["cache-control"] == "no-store, private"


def test_owner_gets_fresh_sanitized_diagnostics(monkeypatch):
    client = setup(monkeypatch)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **kwargs: object())
    async def inspect(reader):
        return {**success(), "uid": "private-uid", "balance": "123456", "key": "private-key"}
    monkeypatch.setattr(status_api, "inspect_demo", inspect)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
    assert response.status_code == 200
    result = response.json()
    assert result["okx_connection_verified"] is True
    assert result["trading_enabled"] is False
    assert all(result["checks"].values())
    assert (datetime.fromisoformat(result["valid_until"]) - datetime.fromisoformat(result["checked_at"])).total_seconds() == 30
    assert all(private not in response.text for private in ("private-uid", "123456", "private-key", "owner-test"))


@pytest.mark.parametrize("flag", ["SINKA_OKX_READONLY_API", "SINKA_OKX_DEMO", "SINKA_OWNER_UID"])
def test_missing_optin_or_owner_stops_before_okx(monkeypatch, flag):
    client = setup(monkeypatch)
    monkeypatch.delenv(flag)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: pytest.fail("Must not contact OKX"))
    assert client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"}).status_code == 503


def test_exchange_failure_has_no_raw_error_or_success_timestamp(monkeypatch):
    client = setup(monkeypatch)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: object())
    async def failed(reader):
        return {"ok": False, "failed_stage": "balances", "secret": "private-secret"}
    monkeypatch.setattr(status_api, "inspect_demo", failed)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
    assert response.status_code == 503
    assert response.json()["okx_connection_verified"] is False
    assert "checked_at" not in response.json()
    assert "private-secret" not in response.text


def test_timeout_fails_closed(monkeypatch):
    client = setup(monkeypatch)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: object())
    async def stalled(reader): await asyncio.sleep(1)
    monkeypatch.setattr(status_api, "inspect_demo", stalled)
    monkeypatch.setattr(status_api, "CHECK_TIMEOUT_SECONDS", 0.001)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
    assert response.status_code == 504
    assert response.json()["okx_connection_verified"] is False


def test_health_does_not_claim_okx_connection(monkeypatch):
    client = setup(monkeypatch)
    assert client.get("/health").json()["okx_connection_verified"] is False


@pytest.mark.parametrize("path", ["/api/open", "/api/close", "/api/initialize", "/api/check-stop", "/docs", "/openapi.json"])
def test_no_order_or_public_documentation_routes(monkeypatch, path):
    client = setup(monkeypatch)
    assert client.post(path).status_code == 404


def test_only_expected_origin_and_get_preflight_allowed(monkeypatch):
    client = setup(monkeypatch)
    response = client.options("/api/okx/status", headers={"Origin": "https://sinkayang-bot.github.io", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://sinkayang-bot.github.io"
    response = client.options("/api/okx/status", headers={"Origin": "https://other.example", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_missing_secret_configuration_is_sanitized(monkeypatch):
    client = setup(monkeypatch)
    for name in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Read-only credentials are not configured"


@pytest.mark.parametrize("field, value", [
    ("mode", "live"), ("api_permissions_readonly", False),
    ("trading_enabled", True), ("account_config_endpoint", "unknown"), ("ok", "true")])
def test_partial_or_conflicting_check_cannot_claim_verified(monkeypatch, field, value):
    client = setup(monkeypatch)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: object())
    async def partial(reader): return {**success(), field: value}
    monkeypatch.setattr(status_api, "inspect_demo", partial)
    response = client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
    assert response.status_code == 503
    assert response.json()["okx_connection_verified"] is False


def test_concurrent_requests_do_not_duplicate_exchange_scan(monkeypatch):
    import httpx
    setup(monkeypatch)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: object())
    calls = []
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def inspect(reader):
            calls.append(reader)
            entered.set()
            await release.wait()
            return success()
        monkeypatch.setattr(status_api, "inspect_demo", inspect)
        transport = httpx.ASGITransport(app=status_api.create_app())
        async with httpx.AsyncClient(transport=transport, base_url="https://test.local") as client:
            first = asyncio.create_task(client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"}))
            await asyncio.wait_for(entered.wait(), 2)
            second = await client.get("/api/okx/status", headers={"Authorization": "Bearer owner-token"})
            assert second.status_code == 429
            assert second.headers["retry-after"] == "5"
            release.set()
            assert (await first).status_code == 200
    asyncio.run(exercise())
    assert len(calls) == 1


@pytest.mark.parametrize("token,code", [(None,401),("other-token",403),("expired",401)])
def test_assets_are_owner_only_before_exchange_access(monkeypatch, token, code):
    client = setup(monkeypatch)
    monkeypatch.setenv("SINKA_OKX_ASSETS_API", "true")
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: pytest.fail("Must not call OKX"))
    response = client.get("/api/okx/assets",headers={"Authorization":"Bearer "+token} if token else {})
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store, private"


def test_assets_optin_is_required_and_does_not_change_status_probe(monkeypatch):
    client = setup(monkeypatch)
    monkeypatch.delenv("SINKA_OKX_ASSETS_API",raising=False)
    monkeypatch.setattr(status_api, "OKXReadOnly", lambda **k: pytest.fail("Must not call OKX"))
    assert client.get("/api/okx/assets",headers={"Authorization":"Bearer owner-token"}).status_code == 503
    assert client.get("/health").json()["assets_readonly_enabled"] is False


def test_assets_success_is_non_atomic_demo_snapshot_with_no_public_asset_response(monkeypatch):
    client = setup(monkeypatch)
    monkeypatch.setenv("SINKA_OKX_ASSETS_API", "true")
    monkeypatch.setattr(status_api,"OKXReadOnly",lambda **k: object())
    async def snapshot(reader): return {"summary":{"total_equity_usd":"123.45"},"currencies":[],"positions":[],"pending_orders":[]}
    monkeypatch.setattr(status_api,"inspect_assets",snapshot)
    response=client.get("/api/okx/assets",headers={"Authorization":"Bearer owner-token"})
    assert response.status_code == 200
    result=response.json()
    assert result["schema_version"] == 1 and result["atomic"] is False and result["trading_enabled"] is False
    assert result["summary"]["total_equity_usd"] == "123.45"
    assert "123.45" not in client.get("/health").text
    assert (datetime.fromisoformat(result["valid_until"])-datetime.fromisoformat(result["checked_at"])).total_seconds()==30


def test_asset_failure_never_exposes_raw_payload_or_success_timestamp(monkeypatch):
    client=setup(monkeypatch);monkeypatch.setenv("SINKA_OKX_ASSETS_API","true")
    monkeypatch.setattr(status_api,"OKXReadOnly",lambda **k: object())
    async def failed(reader): raise ValueError("PRIVATE_BALANCE PRIVATE_KEY")
    monkeypatch.setattr(status_api,"inspect_assets",failed)
    response=client.get("/api/okx/assets",headers={"Authorization":"Bearer owner-token"})
    assert response.status_code==503 and "PRIVATE" not in response.text and "checked_at" not in response.text
