"""Metadata-only deployment preflight. Never accesses secret payloads or runs jobs."""
import json
import subprocess

PROJECT = "sinka-quant"
REGION = "asia-east1"
REPOSITORY = "sinka-okx-readonly"
JOB = "sinka-okx-readonly-smoke"
SERVICE_ACCOUNT = f"sinka-okx-readonly@{PROJECT}.iam.gserviceaccount.com"
SECRETS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")
APIS = frozenset({"run.googleapis.com", "artifactregistry.googleapis.com",
                  "cloudbuild.googleapis.com", "secretmanager.googleapis.com"})


def metadata(*args):
    """Discard raw stderr and never return malformed responses or exception text."""
    try:
        result = subprocess.run(
            ["gcloud", *args, "--project", PROJECT, "--format=json", "--quiet"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def inspect_cloud():
    checks = {}
    apis = metadata("services", "list", "--enabled")
    if isinstance(apis, list):
        enabled = {row["config"].get("name") for row in apis
                   if isinstance(row, dict) and isinstance(row.get("config"), dict)}
        checks["required_apis"] = "ok" if APIS <= enabled else "missing"
    else:
        checks["required_apis"] = "unavailable_or_denied"

    account = metadata("iam", "service-accounts", "describe", SERVICE_ACCOUNT)
    checks["service_account"] = (
        "ok" if isinstance(account, dict) and account.get("email") == SERVICE_ACCOUNT
        and not account.get("disabled", False) else "missing_disabled_or_denied"
    )
    for secret in SECRETS:
        version = metadata("secrets", "versions", "describe", "latest", "--secret", secret)
        checks[secret + "_version"] = (
            "ok" if isinstance(version, dict) and version.get("state") == "ENABLED"
            else "missing_disabled_or_denied"
        )
    repository = metadata("artifacts", "repositories", "describe", REPOSITORY,
                          "--location", REGION)
    checks["artifact_repository"] = (
        "ok" if isinstance(repository, dict) and repository.get("format") == "DOCKER"
        and repository.get("mode", "STANDARD_REPOSITORY") == "STANDARD_REPOSITORY"
        else "missing_incompatible_or_denied"
    )
    job = metadata("run", "jobs", "describe", JOB, "--region", REGION)
    checks["job_exists"] = (
        "ok" if isinstance(job, dict) and isinstance(job.get("metadata"), dict)
        and job["metadata"].get("name") == JOB else "missing_or_denied"
    )
    return {
        "metadata_ready": all(value == "ok" for value in checks.values()),
        "project": PROJECT, "region": REGION, "checks": checks,
        "runtime_secret_access_verified": False,
        "job_configuration_verified": False,
        "okx_connection_verified": False,
        "job_executed": False, "trading_enabled": False,
    }


def main():
    result = inspect_cloud()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["metadata_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
