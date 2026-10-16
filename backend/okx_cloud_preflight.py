"""Metadata-only deployment preflight. Never accesses secret payloads or runs jobs."""
import argparse
import json
import subprocess

from okx_job_config import verify_job

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


def inspect_cloud(expected_image=None):
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
    build_keys = ["required_apis", "service_account", *[name + "_version" for name in SECRETS]]
    before_build_ready = all(checks[key] == "ok" for key in build_keys)
    job_checks = verify_job(job, expected_image)
    return {
        "job_configuration_checks": job_checks,
        "before_build_ready": before_build_ready,
        "metadata_ready": all(value == "ok" for value in checks.values()),
        "project": PROJECT, "region": REGION, "checks": checks,
        "runtime_secret_access_verified": False,
        "job_configuration_verified": all(job_checks.values()),
        "okx_connection_verified": False,
        "job_executed": False, "trading_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image", help="Reviewed smoke image reference pinned by SHA256 digest")
    parser.add_argument("--before-build", action="store_true",
                        help="Check build prerequisites; repository and Job may not exist yet")
    args = parser.parse_args()
    result = inspect_cloud(args.expected_image)
    print(json.dumps(result, sort_keys=True))
    ready = (result["before_build_ready"] if args.before_build else
             result["metadata_ready"] and result["job_configuration_verified"])
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
