"""Fake Google responses only; never obtains real credentials or modifies Firebase."""
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import firebase_login_preflight as login


def fake_metadata(name, token):
    assert token == "private-access-token"
    return {"auth_config": {"authorizedDomains": [login.DOMAIN]},
            "google_provider": {"enabled": True, "clientSecret": "private-client-secret"},
            "web_apps": {"apps": [{"appId": "private-app-id", "state": "ACTIVE"}]}}[name], "ok"


def test_ready_output_excludes_tokens_keys_and_app_identifiers(monkeypatch):
    monkeypatch.setattr(login, "access_token", lambda: "private-access-token")
    monkeypatch.setattr(login, "get_metadata", fake_metadata)
    result = login.inspect_login()
    assert result["login_prerequisites_ready"] is True
    assert result["settings_changed"] is False
    assert result["owner_identity_resolved"] is False
    assert "private-" not in str(result)


def test_missing_login_never_calls_cloud(monkeypatch):
    monkeypatch.setattr(login, "access_token", lambda: None)
    monkeypatch.setattr(login, "get_metadata", lambda *args: pytest.fail("must not call Google"))
    assert login.inspect_login()["login_prerequisites_ready"] is False


@pytest.mark.parametrize("name, payload", [
    ("auth_config", {"authorizedDomains": []}),
    ("google_provider", {"enabled": False}),
    ("google_provider", {"enabled": "true"}),
    ("web_apps", {"apps": []}),
    ("web_apps", {"apps": [{"appId": "deleted", "state": "DELETED"}]}),
])
def test_missing_prerequisite_is_reported_without_mutation(monkeypatch, name, payload):
    monkeypatch.setattr(login, "access_token", lambda: "private-access-token")
    monkeypatch.setattr(login, "get_metadata", lambda key, token:
                        (payload, "ok") if key == name else fake_metadata(key, token))
    assert not login.inspect_login()["login_prerequisites_ready"]


def test_sdk_output_is_captured_not_printed(monkeypatch, capsys):
    def run(command, **kwargs):
        assert command == ["gcloud", "auth", "print-access-token", "--project", "sinka-quant", "--quiet"]
        assert kwargs["capture_output"] is True
        return SimpleNamespace(returncode=0, stdout="private-token\n")
    monkeypatch.setattr(login.subprocess, "run", run)
    assert login.access_token() == "private-token"
    assert capsys.readouterr().out == ""


def test_get_requests_are_fixed_google_urls_with_redirects_blocked(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"enabled":true,"clientSecret":"private-value"}'
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            assert request.get_method() == "GET"
            assert request.get_header("Authorization") == "Bearer private-token"
            return Response()
    def build(handler):
        assert handler.redirect_request(None, None, 302, "", {}, "https://other.example") is None
        return Opener()
    monkeypatch.setattr(login, "build_opener", build)
    assert login.get_metadata("google_provider", "private-token")[1] == "ok"
    assert calls == [login.ENDPOINTS["google_provider"]]


def test_raw_google_error_is_not_returned(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError("https://fixed.example", 403, "private-error", {}, None)
    monkeypatch.setattr(login, "build_opener", lambda *a: Opener())
    data, status = login.get_metadata("auth_config", "private-token")
    assert data is None and status == "api_disabled_or_permission_denied"
    assert "private-error" not in status
