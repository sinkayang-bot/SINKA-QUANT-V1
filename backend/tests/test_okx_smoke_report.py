"""Fixtures only: cloud success and OKX success must refer to the same execution."""
import json
import pytest
import okx_smoke_report as report
from test_okx_job_config import job, IMAGE

NAME = report.JOB + "-abc12"


def execution():
    fixture = job()
    return {"metadata": {"name": NAME, "labels": {
        "run.googleapis.com/job": report.JOB, "cloud.googleapis.com/location": report.REGION}},
        "spec": fixture["spec"]["template"]["spec"],
        "status": {"conditions": [{"type": "Completed", "status": "True"}],
                   "succeededCount": 1, "completionTime": "2026-10-16T14:00:00Z"}}


def payload():
    return {"ok": True, "mode": "demo_readonly", "trading_enabled": False,
            "pending_order_count": 0, "api_permissions_readonly": True, **{key: "ok" for key in report.ENDPOINTS}}


def log():
    return {"resource": {"type": "cloud_run_job", "labels": {
        "project_id": report.PROJECT, "location": report.REGION, "job_name": report.JOB}},
        "labels": {"execution_name": NAME}, "jsonPayload": payload()}


def test_completed_execution_and_matching_result_pass():
    result = report.verify_report(execution(), [log()], NAME, IMAGE)
    assert result["okx_connection_verified"] is True
    assert result["job_executed_by_checker"] is False
    assert result["trading_enabled"] is False


def test_text_payload_and_namespaced_label_are_supported():
    entry = log()
    entry["textPayload"] = json.dumps(entry.pop("jsonPayload"))
    entry["labels"] = {"run.googleapis.com/execution_name": NAME}
    assert report.verify_report(execution(), [entry], NAME, IMAGE)["okx_connection_verified"]


@pytest.mark.parametrize("change", ["no_logs", "wrong_execution", "wrong_project", "failed",
    "running", "wrong_image", "live_mode", "extra_payload", "duplicates", "truncated", "malformed"])
def test_incomplete_or_conflicting_evidence_never_passes(change):
    run, logs, image = execution(), [log()], IMAGE
    if change == "no_logs": logs = []
    elif change == "wrong_execution": logs[0]["labels"]["execution_name"] = report.JOB + "-other"
    elif change == "wrong_project": logs[0]["resource"]["labels"]["project_id"] = "other"
    elif change == "failed": run["status"]["failedCount"] = 1
    elif change == "running": run["status"]["conditions"][0]["status"] = "Unknown"
    elif change == "wrong_image": image = IMAGE.replace("a" * 64, "b" * 64)
    elif change == "live_mode": logs[0]["jsonPayload"]["trading_enabled"] = True
    elif change == "extra_payload": logs[0]["jsonPayload"]["secret"] = "private-key"
    elif change == "duplicates": logs.append(log())
    elif change == "truncated": logs = [log()] * report.LOG_LIMIT
    elif change == "malformed": run = None
    result = report.verify_report(run, logs, NAME, image)
    assert result["okx_connection_verified"] is False
    assert "private-key" not in str(result)


def test_invalid_execution_does_not_call_sdk(monkeypatch):
    def forbidden(*args): raise AssertionError("SDK must not be called")
    monkeypatch.setattr(report, "metadata", forbidden)
    assert not report.inspect_execution('bad" OR anything', IMAGE)["okx_connection_verified"]


def test_checker_only_describes_execution_and_reads_filtered_logs(monkeypatch):
    calls = []
    def metadata(*args):
        calls.append(args)
        return execution() if args[:4] == ("run", "jobs", "executions", "describe") else [log()]
    monkeypatch.setattr(report, "metadata", metadata)
    assert report.inspect_execution(NAME, IMAGE)["okx_connection_verified"]
    assert calls[0] == ("run", "jobs", "executions", "describe", NAME, "--region", report.REGION)
    assert calls[1][:2] == ("logging", "read")
    assert NAME in calls[1][2]
    assert all("execute" not in call for call in calls)


def test_unavailable_sdk_or_logs_cannot_claim_success(monkeypatch):
    monkeypatch.setattr(report, "metadata", lambda *args: None)
    result = report.inspect_execution(NAME, IMAGE)
    assert result["okx_connection_verified"] is False
    assert result["job_executed_by_checker"] is False


@pytest.mark.parametrize("logs", [None, {}, [None], [{"jsonPayload": "private-secret"}]])
def test_malformed_logs_are_sanitized(logs):
    result = report.verify_report(execution(), logs, NAME, IMAGE)
    assert result["okx_connection_verified"] is False
    assert "private-secret" not in str(result)


def test_old_result_without_permission_evidence_does_not_pass():
    entry = log()
    del entry["jsonPayload"]["api_permissions_readonly"]
    del entry["jsonPayload"]["account_config_endpoint"]
    assert not report.verify_report(execution(), [entry], NAME, IMAGE)["okx_connection_verified"]


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_permission_flag_must_be_explicit_boolean(value):
    entry = log()
    entry["jsonPayload"]["api_permissions_readonly"] = value
    assert not report.verify_report(execution(), [entry], NAME, IMAGE)["okx_connection_verified"]
