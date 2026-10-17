"""Inventory must never infer durable ledger readiness or expose raw metadata."""
import okx_reconciliation_preflight as preflight


def test_default_plan_performs_no_cloud_calls(monkeypatch):
    def forbidden(*args):
        raise AssertionError("plan queried cloud")
    monkeypatch.setattr(preflight, "metadata", forbidden)
    monkeypatch.setattr(preflight, "inspect_cloud", forbidden)
    result = preflight.inventory()
    assert result["cloud_inspection_attempted"] is False
    assert result["reconciliation_deployment_ready"] is False


def test_inventory_does_not_adopt_paper_disk_or_return_guest_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "inspect_cloud", lambda: {"metadata_ready": True})
    def metadata(*args):
        calls.append(args)
        return {"name": preflight.VM, "status": "TERMINATED", "serviceAccounts": [],
                "disks": [{"boot": True, "autoDelete": False}],
                "scheduling": {"instanceTerminationAction": "STOP", "maxRunDuration": {"seconds": "1800"}},
                "metadata": {"startup-script": "private-startup-content"}}
    monkeypatch.setattr(preflight, "metadata", metadata)
    result = preflight.inventory(True)
    assert calls == [("compute", "instances", "describe", preflight.VM, "--zone", preflight.ZONE)]
    assert result["paper_vm"]["boot_disk_retained"] is True
    assert result["paper_vm"]["no_service_account"] is True
    assert result["paper_vm"]["provider_stop_configured"] is True
    assert result["paper_vm"]["reconciliation_storage_verified"] is False
    assert result["reconciliation_deployment_ready"] is False
    assert "private-startup-content" not in str(result)
    assert not any(result[k] for k in ("resources_changed", "job_executed", "secret_payloads_accessed", "okx_connection_verified", "trading_enabled"))


def test_missing_or_malformed_metadata_cannot_pass(monkeypatch):
    monkeypatch.setattr(preflight, "inspect_cloud", lambda: {"metadata_ready": False})
    for response in (None, {}, {"name": "different"}, {"name": preflight.VM, "status": "private-value", "disks": None, "scheduling": None}):
        monkeypatch.setattr(preflight, "metadata", lambda *args: response)
        result = preflight.inventory(True)
        assert result["paper_vm"]["state"] == "unknown_or_denied"
        assert result["paper_vm"]["no_service_account"] is False
        assert result["paper_vm"]["boot_disk_retained"] is False
        assert "private-value" not in str(result)
