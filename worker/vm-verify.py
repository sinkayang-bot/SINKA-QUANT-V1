"""Read-only verification of a bounded, isolated paper VM and sanitized serial events."""
import json
import sys
from datetime import datetime, timezone


def parse_events(serial):
    statuses = {"idle", "starting", "waiting_signal", "position_open", "daily_halt", "paused", "closing", "interrupted", "market_error", "missing_bars", "storage_error", "recovery_pending", "recovered", "closed"}
    events = []
    for line in serial.splitlines():
        # systemd/docker serial prefixes can precede a JSON event.
        start = line.find('{"mode":"local_paper"')
        if start < 0:
            continue
        try:
            row = json.loads(line[start:])
            if row.get("strategy") != "SNR-5M-1" or not isinstance(row.get("active"), bool):
                continue
            if type(row.get("closedTrades")) is not int or not 0 <= row["closedTrades"] <= 100000 or row.get("status") not in statuses:
                continue
            if not isinstance(row.get("positionOpen"), bool) or not isinstance(row.get("quoteFresh"), bool):
                continue
            at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                continue
            # No amounts, instruments, account identifiers, raw errors or extra fields.
            event = {k: row[k] for k in ("status", "active", "positionOpen", "closedTrades", "quoteFresh")}
            event["at"] = at.isoformat()
            if type(row.get("revision")) is int and row["revision"] > 0:
                event["revision"] = row["revision"]
            events.append(event)
        except (ValueError, TypeError, KeyError):
            continue
    return events


def verify(instance, serial, now=None):
    now = now or datetime.now(timezone.utc)
    expected_network = "/global/networks/sinka-paper-trial-net"
    scheduling = instance.get("scheduling", {})
    disks = instance.get("disks", [])
    duration = scheduling.get("maxRunDuration", {}).get("seconds")
    checks = {
        "expected_name": instance.get("name") == "sinka-paper-trial",
        "expected_machine": instance.get("machineType", "").endswith("/machineTypes/e2-micro"),
        "stop_after_30_minutes": str(duration) == "1800" and scheduling.get("instanceTerminationAction") == "STOP",
        "automatic_restart_disabled": scheduling.get("automaticRestart") is False,
        "no_service_account": not instance.get("serviceAccounts"),
        "persistent_boot_disk": len(disks) == 1 and disks[0].get("boot") is True and disks[0].get("autoDelete") is False,
        "isolated_network": len(instance.get("networkInterfaces", [])) == 1 and instance["networkInterfaces"][0].get("network", "").endswith(expected_network),
        "paper_trial_label": instance.get("labels", {}).get("sinka-purpose") == "paper-trial",
    }
    events = parse_events(serial)
    latest = events[-1] if events else None
    fresh = False
    if latest:
        at = datetime.fromisoformat(latest["at"].replace("Z", "+00:00"))
        fresh = -5 <= (now - at).total_seconds() <= 90
    return {"configuration_ok": all(checks.values()), "checks": checks, "vm_status": instance.get("status"),
            "worker_observed_with_fresh_quote": any(e["quoteFresh"] for e in events),
            "latest_worker_event_fresh": fresh, "latest_worker_event": latest,
            "trading_enabled": False, "cloud_acceptance_complete": instance.get("status") == "RUNNING" and all(checks.values()) and fresh and bool(latest and latest["active"] and latest["quoteFresh"])}


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as file:
        instance = json.load(file)
    with open(sys.argv[2], encoding="utf-8") as file:
        serial = file.read()
    report = verify(instance, serial)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["configuration_ok"] else 2)
