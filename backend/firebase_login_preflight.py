"""Read-only Firebase login prerequisites, with sanitized output only.

Uses the operator's existing gcloud session in memory. No secret payload access,
user listing, login, IAM changes, provider enablement or cloud deployment.
"""
import json
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT = "sinka-quant"
DOMAIN = "sinkayang-bot.github.io"
ENDPOINTS = {
    "auth_config": f"https://identitytoolkit.googleapis.com/admin/v2/projects/{PROJECT}/config?fields=authorizedDomains",
    "google_provider": f"https://identitytoolkit.googleapis.com/admin/v2/projects/{PROJECT}/defaultSupportedIdpConfigs/google.com?fields=enabled",
    "web_apps": f"https://firebase.googleapis.com/v1beta1/projects/{PROJECT}/webApps?pageSize=100&fields=apps(appId,state)",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the bearer token to a redirect destination.
        return None


def access_token():
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token", "--project", PROJECT, "--quiet"],
            capture_output=True, text=True, timeout=30, check=False)
        token = result.stdout.strip()
        return token if result.returncode == 0 and token and not any(c.isspace() for c in token) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_metadata(name, token):
    request = Request(ENDPOINTS[name], headers={"Authorization": "Bearer " + token}, method="GET")
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return None, "response_too_large"
            data = json.loads(raw)
            return (data, "ok") if isinstance(data, dict) else (None, "invalid_response")
    except HTTPError as error:
        return None, {401: "login_required", 403: "api_disabled_or_permission_denied",
                      404: "not_configured"}.get(error.code, "http_error")
    except (URLError, OSError, ValueError):
        return None, "unavailable_or_invalid_response"


def inspect_login():
    token = access_token()
    checks = dict.fromkeys(ENDPOINTS, "login_required")
    data = {}
    if token:
        for name in ENDPOINTS:
            data[name], checks[name] = get_metadata(name, token)
    config, provider, apps = (data.get(name) or {} for name in ENDPOINTS)
    domains = config.get("authorizedDomains", [])
    web_apps = apps.get("apps", [])
    pages_ready = isinstance(domains, list) and DOMAIN in domains
    google_ready = provider.get("enabled") is True
    app_ready = isinstance(web_apps, list) and any(
        isinstance(app, dict) and app.get("state", "ACTIVE") == "ACTIVE" and app.get("appId")
        for app in web_apps)
    return {"project": PROJECT, "checks": checks,
            "google_signin_enabled": google_ready,
            "pages_domain_authorized": pages_ready,
            "web_app_available": app_ready,
            "login_prerequisites_ready": all(value == "ok" for value in checks.values())
                                         and pages_ready and google_ready and app_ready,
            "owner_identity_resolved": False, "settings_changed": False,
            "trading_enabled": False}


def main():
    result = inspect_login()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["login_prerequisites_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
