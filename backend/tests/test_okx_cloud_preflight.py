"""All cloud responses are mocked; no credentials, cloud mutations or OKX calls."""
import json
import subprocess
from types import SimpleNamespace
import okx_cloud_preflight as cloud


def test_preflight_only_uses_metadata_commands(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        action = command[1:4]
        if action[:2] == ["services", "list"]:
            data = [{"config": {"name": api}} for api in cloud.APIS]
        elif action == ["iam", "service-accounts", "describe"]:
            data = {"email": cloud.SERVICE_ACCOUNT}
        elif action == ["secrets", "versions", "describe"]:
            data = {"state": "ENABLED"}
        elif action == ["artifacts", "repositories", "describe"]:
            data = {"format": "DOCKER", "mode": "STANDARD_REPOSITORY"}
        elif action == ["run", "jobs", "describe"]:
            data = {"metadata": {"name": cloud.JOB}}
        else:
            raise AssertionError("Unexpected cloud operation")
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr(cloud.subprocess, "run", run)
    result = cloud.inspect_cloud()
    assert result["metadata_ready"] is True
    assert result["okx_connection_verified"] is False
    assert result["runtime_secret_access_verified"] is False
    assert result["job_executed"] is False
    for command in commands:
        assert command[-4:] == ["--project", "sinka-quant", "--format=json", "--quiet"]
        assert not {"access", "execute", "create", "deploy", "enable", "add-iam-policy-binding"} & set(command)


def test_denied_metadata_does_not_leak_stderr(monkeypatch):
    monkeypatch.setattr(cloud.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout="", stderr="private-value"))
    result = cloud.inspect_cloud()
    assert result["metadata_ready"] is False
    assert "private-value" not in str(result)


def test_disabled_latest_secret_version_blocks_readiness(monkeypatch):
    monkeypatch.setattr(cloud, "metadata", lambda *a: {"state": "DISABLED"})
    result = cloud.inspect_cloud()
    assert result["metadata_ready"] is False
    assert all(result["checks"][name + "_version"] != "ok" for name in cloud.SECRETS)


def test_missing_sdk_and_timeout_are_sanitized(monkeypatch):
    for error in (FileNotFoundError("private-path"), subprocess.TimeoutExpired("gcloud", 30)):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(cloud.subprocess, "run", fail)
        assert cloud.metadata("services", "list") is None


def test_before_build_can_pass_without_repository_or_job(monkeypatch, capsys):
    def metadata(*args):
        if args[:2] == ("services", "list"):
            return [{"config": {"name": name}} for name in cloud.APIS]
        if args[:3] == ("iam", "service-accounts", "describe"):
            return {"email": cloud.SERVICE_ACCOUNT}
        if args[:3] == ("secrets", "versions", "describe"):
            return {"state": "ENABLED"}
        return None
    monkeypatch.setattr(cloud, "metadata", metadata)
    monkeypatch.setattr("sys.argv", ["okx_cloud_preflight.py", "--before-build"])
    assert cloud.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["before_build_ready"] is True
    assert result["metadata_ready"] is False
    assert result["job_configuration_verified"] is False
    assert result["okx_connection_verified"] is False
    monkeypatch.setattr("sys.argv", ["okx_cloud_preflight.py"])
    assert cloud.main() == 1


def test_disabled_account_blocks_build_readiness(monkeypatch):
    def metadata(*args):
        if args[:2] == ("services", "list"):
            return [{"config": {"name": name}} for name in cloud.APIS]
        if args[:3] == ("iam", "service-accounts", "describe"):
            return {"email": cloud.SERVICE_ACCOUNT, "disabled": True}
        if args[:3] == ("secrets", "versions", "describe"):
            return {"state": "ENABLED"}
        return None
    monkeypatch.setattr(cloud, "metadata", metadata)
    assert cloud.inspect_cloud()["before_build_ready"] is False
