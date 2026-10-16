"""Fail-closed checks for the v1 Cloud Run demo smoke Job description.

An expected digest must come from a separately reviewed build; this checks
configuration equality, not image provenance or OKX credential permissions.
"""
import re

IMAGE_PREFIX = "asia-east1-docker.pkg.dev/sinka-quant/sinka-okx-readonly/okx-smoke@sha256:"
ACCOUNT = "sinka-okx-readonly@sinka-quant.iam.gserviceaccount.com"
SECRET_NAMES = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")


def verify_job(job, expected_image):
    checks = dict.fromkeys(("identity", "image", "entrypoint", "demo_environment",
                            "secret_references", "execution_limits", "isolation"), False)
    try:
        if not isinstance(job, dict):
            return checks
        metadata = job["metadata"]
        execution = job["spec"]["template"]["spec"]
        task = execution["template"]["spec"]
        checks["identity"] = (
            metadata["name"] == "sinka-okx-readonly-smoke"
            and metadata.get("labels", {}).get("cloud.googleapis.com/location") == "asia-east1"
            and task["serviceAccountName"] == ACCOUNT
        )
        containers = task["containers"]
        if not isinstance(containers, list) or len(containers) != 1:
            return checks
        container = containers[0]
        checks["image"] = (
            isinstance(expected_image, str)
            and re.fullmatch(re.escape(IMAGE_PREFIX) + r"[0-9a-f]{64}", expected_image) is not None
            and container["image"] == expected_image
        )
        checks["entrypoint"] = (container.get("command") == ["python"]
                                and container.get("args") == ["okx_smoke.py"]
                                and container.get("workingDir", "/app") == "/app")
        rows = container.get("env", [])
        env = {row["name"]: row for row in rows}
        expected_names = set(SECRET_NAMES) | {"SINKA_OKX_DEMO", "SINKA_OKX_READONLY_SMOKE"}
        exact_env = len(rows) == len(env) and set(env) == expected_names
        checks["demo_environment"] = exact_env and all(
            env[name] == {"name": name, "value": "true"}
            for name in ("SINKA_OKX_DEMO", "SINKA_OKX_READONLY_SMOKE")
        )
        # Accept only direct same-project names. Cross-project annotation aliases
        # need a separate review, so fail closed even if access was granted.
        annotations = job["spec"]["template"].get("metadata", {}).get("annotations", {})
        checks["secret_references"] = exact_env and not annotations.get("run.googleapis.com/secrets") and all(
            env[name] == {"name": name, "valueFrom": {
                "secretKeyRef": {"name": name, "key": "latest"}}}
            for name in SECRET_NAMES
        )
        checks["execution_limits"] = (
            str(execution["taskCount"]) == "1" and str(execution["parallelism"]) == "1"
            and str(task["maxRetries"]) == "0" and str(task["timeoutSeconds"]) == "120"
            and container["resources"]["limits"] == {"cpu": "1", "memory": "512Mi"}
        )
        checks["isolation"] = not task.get("volumes") and not container.get("volumeMounts")
    except (KeyError, TypeError, AttributeError, ValueError):
        # Never print descriptions, env values, secret names from unknown jobs,
        # or raw API responses. The result contains only fixed checks.
        pass
    return checks
