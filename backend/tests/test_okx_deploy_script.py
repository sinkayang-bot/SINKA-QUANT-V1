"""Run deployment shell against a fake SDK. No cloud calls or OKX credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / "firebase" / "deploy-okx-smoke-job.sh"


def run_deploy(tmp_path, monkeypatch, digest):
    log = tmp_path / "commands.jsonl"
    sdk = tmp_path / "gcloud"
    sdk.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["SDK_TEST_LOG"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["builds", "submit"]:
    print(os.environ["SDK_TEST_DIGEST"])
''')
    sdk.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SDK_TEST_LOG", str(log))
    monkeypatch.setenv("SDK_TEST_DIGEST", digest)
    for variable in ("PROJECT_ID", "REGION", "REPOSITORY", "JOB", "SERVICE_ACCOUNT"):
        monkeypatch.delenv(variable, raising=False)
    result = subprocess.run(["bash", str(SCRIPT)], input="DEPLOY\n",
                            text=True, capture_output=True, timeout=10)
    calls = [json.loads(row) for row in log.read_text().splitlines()]
    return result, calls


def test_deploy_pins_build_digest_and_never_executes(tmp_path, monkeypatch):
    digest = "sha256:" + "a" * 64
    result, calls = run_deploy(tmp_path, monkeypatch, digest)
    assert result.returncode == 0, result.stderr
    deploy = next(call for call in calls if call[:3] == ["run", "jobs", "deploy"])
    assert deploy[deploy.index("--image") + 1].endswith("okx-smoke@" + digest)
    assert "--command=python" in deploy
    assert "--args=okx_smoke.py" in deploy
    assert not any("execute" in call or "--execute-now" in call for call in calls)
    assert not any(call[:3] == ["secrets", "versions", "access"] for call in calls)


def test_missing_build_digest_blocks_job_deployment(tmp_path, monkeypatch):
    result, calls = run_deploy(tmp_path, monkeypatch, "")
    assert result.returncode != 0
    assert "missing build image digest" in result.stderr
    assert not any(call[:3] == ["run", "jobs", "deploy"] for call in calls)
