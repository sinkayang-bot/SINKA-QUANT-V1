import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vm_verify", ROOT / "worker/vm-verify.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 17, 6, tzinfo=timezone.utc)


def instance():
    return {"name": "sinka-paper-trial", "status": "RUNNING", "machineType": "zones/asia-east1-b/machineTypes/e2-micro",
            "scheduling": {"maxRunDuration": {"seconds": "1800"}, "instanceTerminationAction": "STOP", "automaticRestart": False},
            "disks": [{"boot": True, "autoDelete": False}], "serviceAccounts": [],
            "networkInterfaces": [{"network": "projects/sinka-quant/global/networks/sinka-paper-trial-net"}],
            "labels": {"sinka-purpose": "paper-trial"}}


EVENT = '{"mode":"local_paper","strategy":"SNR-5M-1","status":"waiting_signal","active":true,"positionOpen":false,"closedTrades":0,"quoteFresh":true,"at":"2026-10-17T06:00:00Z"}'


class VerifyTests(unittest.TestCase):
    def test_instance_creation_is_not_accepted_as_worker_execution(self):
        report = module.verify(instance(), "", NOW)
        self.assertTrue(report["configuration_ok"])
        self.assertFalse(report["cloud_acceptance_complete"])

    def test_current_public_quote_heartbeat_proves_running_paper_worker(self):
        report = module.verify(instance(), "console prefix " + EVENT, NOW)
        self.assertTrue(report["cloud_acceptance_complete"])
        self.assertFalse(report["trading_enabled"])

    def test_missing_runtime_limit_or_service_account_rejected(self):
        for change in [lambda x: x["scheduling"].pop("maxRunDuration"), lambda x: x.update(serviceAccounts=[{"email": "not-allowed"}]), lambda x: x["disks"][0].update(autoDelete=True)]:
            value = instance()
            change(value)
            self.assertFalse(module.verify(value, EVENT, NOW)["configuration_ok"])

    def test_stale_or_closed_heartbeat_is_not_active_acceptance(self):
        for event in [EVENT.replace("06:00:00", "05:00:00"), EVENT.replace('"active":true', '"active":false'), EVENT.replace('"quoteFresh":true', '"quoteFresh":false')]:
            self.assertFalse(module.verify(instance(), event, NOW)["cloud_acceptance_complete"])

    def test_stopped_vm_cannot_be_accepted_from_a_recent_log(self):
        value = instance()
        value['status'] = 'TERMINATED'
        self.assertFalse(module.verify(value, EVENT, NOW)['cloud_acceptance_complete'])

    def test_unknown_status_is_not_copied(self):
        self.assertIsNone(module.verify(instance(), EVENT.replace('waiting_signal', 'PRIVATE_STATUS'), NOW)['latest_worker_event'])

    def test_report_does_not_repeat_extra_payload(self):
        import json
        event = EVENT[:-1] + ',"token":"PRIVATE_TOKEN","amount":"PRIVATE_AMOUNT"}'
        report = module.verify(instance(), event, NOW)
        self.assertNotIn("PRIVATE_", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
