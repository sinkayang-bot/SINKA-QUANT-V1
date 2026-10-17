"""Compare a bounded paper VM's disk and checkpoint evidence across a real restart."""
import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location("vm_verify", Path(__file__).with_name("vm-verify.py"))
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


def disk_identity(instance, disk):
    expected = "/projects/sinka-quant/zones/asia-east1-b/disks/sinka-paper-trial"
    if (not disk.get("id") or disk.get("name") != "sinka-paper-trial" or
            not disk.get("selfLink", "").endswith(expected) or
            str(disk.get("sizeGb")) != "10" or not disk.get("type", "").endswith("/diskTypes/pd-standard") or
            instance["disks"][0].get("source") != disk["selfLink"]):
        raise ValueError("Unexpected persistent disk")
    return {"id": str(disk["id"]), "source": disk["selfLink"], "created": disk.get("creationTimestamp")}


def identity(instance, disk):
    report = vm.verify(instance, "")
    if not report["configuration_ok"] or instance.get("status") not in {"RUNNING", "TERMINATED"} or not instance.get("id"):
        raise ValueError("Existing VM configuration is not the bounded paper trial")
    return {"vm_id": str(instance["id"]), "disk": disk_identity(instance, disk)}


def baseline(instance, disk, serial):
    result = identity(instance, disk)
    events = vm.parse_events(serial)
    if not events or not events[-1].get("revision", 0) >= 2:
        raise ValueError("No persisted worker baseline; do not restart to manufacture acceptance")
    return {**result, "event": events[-1]}


def warmup(before_identity, instance, disk, serial, started_at, now=None):
    now = now or datetime.now(timezone.utc)
    if identity(instance, disk) != before_identity:
        raise ValueError("Existing VM/disk changed during warmup")
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("Missing warmup timezone")
    events = [e for e in vm.parse_events(serial) if datetime.fromisoformat(e["at"]) >= start - timedelta(seconds=5)]
    first = next((e for e in events if e["status"] in {"starting", "recovery_pending"}), None)
    if first and first.get("revision", 0) < 2:
        raise ValueError("Warmup did not reopen a previously persisted checkpoint")
    latest = events[-1] if events else None
    ready = False
    if first and latest:
        fresh = -5 <= (now - datetime.fromisoformat(latest["at"])).total_seconds() <= 90 and latest["quoteFresh"]
        recovered = any(e["status"] == "recovered" and not e["positionOpen"] and e["quoteFresh"] for e in events)
        ready = instance.get("status") == "RUNNING" and fresh and (latest["active"] or recovered)
    # A warmup alone never claims full restart acceptance. Actual counters are captured next.
    return {"warmup_ready": bool(ready), "existing_checkpoint_reopened": bool(first), "restart_acceptance_complete": False}


def compare(before, instance, disk, serial, started_at, now=None):
    now = now or datetime.now(timezone.utc)
    config = vm.verify(instance, serial, now)
    same_vm = str(instance.get("id")) == before["vm_id"]
    same_disk = disk_identity(instance, disk) == before["disk"]
    if not same_vm or not same_disk or not config["configuration_ok"]:
        raise ValueError("VM/disk identity or bounded configuration changed")
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("Restart marker requires timezone")
    # Old serial history cannot satisfy a new boot. Small cross-host clock skew is tolerated.
    events = [event for event in vm.parse_events(serial)
              if datetime.fromisoformat(event["at"]) >= start - timedelta(seconds=5)]
    initial = next((e for e in events if e["status"] in {"starting", "recovery_pending"}), None)
    latest = events[-1] if events else None
    result = {"same_vm": same_vm, "same_persistent_disk": same_disk, "new_boot_observed": bool(initial),
              "checkpoint_not_reset": False, "trade_history_not_reduced": False,
              "fresh_public_quote_after_restart": False, "worker_running": False,
              "recovered_position_flattened": False, "restart_acceptance_complete": False, "trading_enabled": False}
    if initial:
        result["checkpoint_not_reset"] = initial.get("revision", 0) >= before["event"]["revision"]
        result["trade_history_not_reduced"] = initial["closedTrades"] >= before["event"]["closedTrades"]
        if not result["checkpoint_not_reset"] or not result["trade_history_not_reduced"]:
            raise ValueError("Persisted checkpoint or trade history reset")
        if before["event"]["positionOpen"] and not initial["positionOpen"] and initial["closedTrades"] == before["event"]["closedTrades"]:
            raise ValueError("Open position disappeared without a recorded paper close")
    if latest:
        age = (now - datetime.fromisoformat(latest["at"])).total_seconds()
        fresh = -5 <= age <= 90 and latest["quoteFresh"]
        result["fresh_public_quote_after_restart"] = any(e["quoteFresh"] for e in events) and fresh
        result["worker_running"] = instance.get("status") == "RUNNING" and latest["active"] and fresh
        result["recovered_position_flattened"] = any(e["status"] == "recovered" and e["quoteFresh"] and not e["positionOpen"] for e in events) and fresh
    result["restart_acceptance_complete"] = bool(initial and result["checkpoint_not_reset"] and
        result["trade_history_not_reduced"] and result["fresh_public_quote_after_restart"] and
        instance.get("status") == "RUNNING" and (result["worker_running"] or result["recovered_position_flattened"]))
    return result


if __name__ == "__main__":
    def load(path):
        return json.loads(Path(path).read_text())
    try:
        if sys.argv[1] == "identity":
            value = identity(load(sys.argv[2]), load(sys.argv[3]))
            Path(sys.argv[4]).write_text(json.dumps(value))
        elif sys.argv[1] == "warmup":
            value = warmup(load(sys.argv[2]), load(sys.argv[3]), load(sys.argv[4]), Path(sys.argv[5]).read_text(), sys.argv[6])
            print(json.dumps(value))
            sys.exit(0 if value["warmup_ready"] else 3)
        elif sys.argv[1] == "before":
            value = baseline(load(sys.argv[2]), load(sys.argv[3]), Path(sys.argv[4]).read_text())
            Path(sys.argv[5]).write_text(json.dumps(value))
        else:
            value = compare(load(sys.argv[2]), load(sys.argv[3]), load(sys.argv[4]), Path(sys.argv[5]).read_text(), sys.argv[6])
            print(json.dumps(value, indent=2))
            sys.exit(0 if value["restart_acceptance_complete"] else 3)
    except (ValueError, KeyError, TypeError, IndexError):
        print(json.dumps({"restart_acceptance_complete": False, "blocked": "Invalid baseline, changed identity/configuration, or reset checkpoint"}))
        sys.exit(2)
