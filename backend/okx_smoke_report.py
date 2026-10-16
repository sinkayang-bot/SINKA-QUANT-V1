"""Read-only verification of one existing Cloud Run smoke execution and its logs."""
import argparse
import json
import re

from okx_cloud_preflight import JOB, PROJECT, REGION, metadata
from okx_job_config import verify_job

ENDPOINTS = ("account_config_endpoint", "balances_endpoint", "positions_endpoint", "instruments_endpoint", "pending_orders_endpoint")
EXECUTION_PATTERN = re.compile(re.escape(JOB) + r"-[a-z0-9]{1,32}")
LOG_LIMIT = 100


def valid_execution_name(name):
    return isinstance(name, str) and EXECUTION_PATTERN.fullmatch(name) is not None


def execution_identity(labels):
    """Support the documented label and the legacy namespaced Cloud Run label."""
    names = [labels[key] for key in ("execution_name", "run.googleapis.com/execution_name") if key in labels]
    return names[0] if names and all(name == names[0] for name in names) else None


def successful_smoke(payload):
    expected_keys = {"ok", "mode", "trading_enabled", "api_permissions_readonly", "pending_order_count", *ENDPOINTS}
    return (isinstance(payload, dict) and set(payload) == expected_keys
            and payload["ok"] is True and payload["mode"] == "demo_readonly"
            and payload["trading_enabled"] is False
            and payload["api_permissions_readonly"] is True
            and type(payload["pending_order_count"]) is int and payload["pending_order_count"] >= 0
            and all(payload[key] == "ok" for key in ENDPOINTS))


def verify_report(execution, logs, name, expected_image):
    checks = dict.fromkeys(("execution_identity", "execution_configuration", "execution_completed",
                            "matching_smoke_result"), False)
    try:
        if not valid_execution_name(name):
            raise ValueError("Invalid execution name")
        info = execution["metadata"]
        labels = info["labels"]
        job_name = labels.get("run.googleapis.com/job", labels.get("job_name"))
        checks["execution_identity"] = info["name"] == name and job_name == JOB
        # Inspect this execution's immutable template, not the job's current template.
        job_shape = {"metadata": {"name": job_name, "labels": labels},
                     "spec": {"template": {"metadata": {"annotations": info.get("annotations", {})},
                                           "spec": execution["spec"]}}}
        checks["execution_configuration"] = all(verify_job(job_shape, expected_image).values())
        status = execution["status"]
        completed = [row for row in status["conditions"] if row.get("type") == "Completed"]
        checks["execution_completed"] = (
            len(completed) == 1 and completed[0].get("status") == "True"
            and status.get("succeededCount") == 1 and type(status.get("succeededCount")) is int
            and all(status.get(key, 0) == 0 for key in
                    ("failedCount", "cancelledCount", "runningCount", "retriedCount"))
            and isinstance(status.get("completionTime"), str) and bool(status["completionTime"])
        )
        if not isinstance(logs, list) or len(logs) >= LOG_LIMIT:
            raise ValueError("Logs unavailable or truncated")
        reports = []
        for entry in logs:
            resource = entry.get("resource", {})
            log_labels = resource.get("labels", {})
            matching = (resource.get("type") == "cloud_run_job"
                        and log_labels.get("project_id") == PROJECT
                        and log_labels.get("location") == REGION
                        and log_labels.get("job_name") == JOB
                        and execution_identity(entry.get("labels", {})) == name)
            if not matching:
                continue
            payload = entry.get("jsonPayload")
            if payload is None:
                try:
                    payload = json.loads(entry.get("textPayload", ""))
                except (ValueError, TypeError):
                    continue
            if isinstance(payload, dict) and payload.get("mode") == "demo_readonly":
                reports.append(payload)
        checks["matching_smoke_result"] = len(reports) == 1 and successful_smoke(reports[0])
    except (KeyError, TypeError, AttributeError, ValueError):
        pass
    return {"okx_connection_verified": all(checks.values()), "checks": checks,
            "mode": "demo_readonly", "job_executed_by_checker": False, "trading_enabled": False}


def inspect_execution(name, expected_image):
    if not valid_execution_name(name):
        return verify_report(None, None, name, expected_image)
    execution = metadata("run", "jobs", "executions", "describe", name, "--region", REGION)
    query = (f'resource.type="cloud_run_job" AND resource.labels.project_id="{PROJECT}" '
             f'AND resource.labels.location="{REGION}" AND resource.labels.job_name="{JOB}" '
             f'AND (labels.execution_name="{name}" OR labels."run.googleapis.com/execution_name"="{name}")')
    logs = metadata("logging", "read", query, "--limit", str(LOG_LIMIT), "--freshness", "30d")
    return verify_report(execution, logs, name, expected_image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", required=True)
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()
    result = inspect_execution(args.execution, args.expected_image)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["okx_connection_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
