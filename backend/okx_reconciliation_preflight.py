"""Deployment inventory only: no secret payloads, VM changes, or Job execution."""
import argparse
import json

from okx_cloud_preflight import inspect_cloud, metadata

VM = "sinka-paper-trial"
ZONE = "asia-east1-b"


def inventory(inspect=False):
    result = {
        "project": "sinka-quant", "region": "asia-east1",
        "mode": "metadata_only" if inspect else "plan",
        "cloud_inspection_attempted": False, "existing_smoke": None, "paper_vm": None,
        "reconciliation_deployment_ready": False,
        "blockers": ["dedicated_durable_ledger_not_verified",
                     "expected_okx_account_binding_not_verified",
                     "reconciliation_runtime_not_deployed_or_verified"],
        "resources_changed": False, "job_executed": False,
        "secret_payloads_accessed": False, "okx_connection_verified": False,
        "trading_enabled": False,
    }
    if not inspect:
        return result
    result["cloud_inspection_attempted"] = True  # attempted; individual results remain explicit
    result["existing_smoke"] = inspect_cloud()
    vm = metadata("compute", "instances", "describe", VM, "--zone", ZONE)
    known = isinstance(vm, dict) and vm.get("name") == VM
    state = vm.get("status") if known else None
    if state not in {"PROVISIONING", "STAGING", "RUNNING", "STOPPING", "SUSPENDING",
                     "SUSPENDED", "REPAIRING", "TERMINATED"}:
        state = "unknown_or_denied"
    disks = vm.get("disks") if known else None
    boot = [d for d in disks if isinstance(d, dict) and d.get("boot") is True] if isinstance(disks, list) else []
    scheduling = vm.get("scheduling") if known else None
    scheduling = scheduling if isinstance(scheduling, dict) else {}
    result["paper_vm"] = {
        "name": VM, "zone": ZONE, "state": state,
        "metadata_available": known,
        "no_service_account": known and vm.get("serviceAccounts") == [],
        "boot_disk_retained": len(boot) == 1 and boot[0].get("autoDelete") is False,
        "provider_stop_configured": scheduling.get("instanceTerminationAction") == "STOP",
        "duration_bound_configured": isinstance(scheduling.get("maxRunDuration"), dict)
        and bool(scheduling["maxRunDuration"].get("seconds")),
        "reconciliation_storage_verified": False,
        "guest_ledger_inspected": False,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Read fixed GCP resource metadata using an authenticated gcloud session")
    args = parser.parse_args()
    result = inventory(args.inspect)
    print(json.dumps(result, sort_keys=True))
    if not args.inspect:
        return 0
    # Inventory success is distinct from deployment readiness or account acceptance.
    return 0 if result["existing_smoke"]["metadata_ready"] and result["paper_vm"]["metadata_available"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
