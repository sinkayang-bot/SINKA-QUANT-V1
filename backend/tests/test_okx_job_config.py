"""Configuration drift tests use fixtures only and cannot execute a cloud job."""
import copy
import pytest
from okx_job_config import verify_job, IMAGE_PREFIX, ACCOUNT, SECRET_NAMES

IMAGE = IMAGE_PREFIX + "a" * 64


def job():
    return {"metadata": {"name": "sinka-okx-readonly-smoke", "labels": {
        "cloud.googleapis.com/location": "asia-east1"}}, "spec": {"template": {"spec": {
        "taskCount": 1, "parallelism": 1, "template": {"spec": {
            "serviceAccountName": ACCOUNT, "maxRetries": 0, "timeoutSeconds": "120",
            "containers": [{"image": IMAGE, "command": ["python"], "args": ["okx_smoke.py"],
                "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                "env": [{"name": name, "value": "true"} for name in (
                    "SINKA_OKX_DEMO", "SINKA_OKX_READONLY_SMOKE")] + [
                    {"name": name, "valueFrom": {"secretKeyRef": {"name": name, "key": "latest"}}}
                    for name in SECRET_NAMES]}]}}}}}}


def test_reviewed_digest_and_exact_smoke_config_pass():
    assert all(verify_job(job(), IMAGE).values())


@pytest.mark.parametrize("image", [None, IMAGE_PREFIX + "b" * 64,
    IMAGE_PREFIX.replace("sinka-quant/", "other-project/") + "a" * 64,
    IMAGE_PREFIX.split("@")[0] + ":initial"])
def test_unreviewed_or_mutable_image_does_not_pass(image):
    assert not all(verify_job(job(), image).values())


@pytest.mark.parametrize("change", ["live", "duplicate", "extra_env", "plaintext_secret",
    "wrong_secret", "sidecar", "command", "retries", "tasks", "volume", "account", "region"])
def test_drift_is_rejected(change):
    data = job()
    execution = data["spec"]["template"]["spec"]
    task = execution["template"]["spec"]
    container = task["containers"][0]
    if change == "live": container["env"][0]["value"] = "false"
    elif change == "duplicate": container["env"].append(copy.deepcopy(container["env"][0]))
    elif change == "extra_env": container["env"].append({"name": "PYTHONPATH", "value": "/other"})
    elif change == "plaintext_secret": container["env"][2] = {"name": SECRET_NAMES[0], "value": "private-secret"}
    elif change == "wrong_secret": container["env"][2]["valueFrom"]["secretKeyRef"]["name"] = "other"
    elif change == "sidecar": task["containers"].append(copy.deepcopy(container))
    elif change == "command": container["command"] = ["sh"]
    elif change == "retries": task["maxRetries"] = 1
    elif change == "tasks": execution["taskCount"] = 2
    elif change == "volume": task["volumes"] = [{"name": "extra"}]
    elif change == "account": task["serviceAccountName"] = "other"
    elif change == "region": data["metadata"]["labels"]["cloud.googleapis.com/location"] = "other"
    result = verify_job(data, IMAGE)
    assert not all(result.values())
    assert "private-secret" not in str(result)


@pytest.mark.parametrize("data", [None, {}, [], {"metadata": None}, {"spec": {"template": []}}])
def test_malformed_api_response_fails_closed(data):
    assert not all(verify_job(data, IMAGE).values())
