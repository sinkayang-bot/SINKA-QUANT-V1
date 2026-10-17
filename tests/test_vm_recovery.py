import importlib.util
import json
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vm_recovery', ROOT / 'worker/vm-recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
T = '2026-10-17T09:00:00+00:00'
NOW = datetime(2026, 10, 17, 9, 1, tzinfo=timezone.utc)
SOURCE = 'https://www.googleapis.com/compute/v1/projects/sinka-quant/zones/asia-east1-b/disks/sinka-paper-trial'


def instance():
    return {'id': '42', 'name': 'sinka-paper-trial', 'status': 'RUNNING',
            'machineType': 'zones/asia-east1-b/machineTypes/e2-micro',
            'scheduling': {'maxRunDuration': {'seconds': '1800'}, 'instanceTerminationAction': 'STOP', 'automaticRestart': False},
            'disks': [{'boot': True, 'autoDelete': False, 'source': SOURCE}], 'serviceAccounts': [],
            'networkInterfaces': [{'network': 'projects/sinka-quant/global/networks/sinka-paper-trial-net'}],
            'labels': {'sinka-purpose': 'paper-trial'}}


def disk():
    return {'id': '88', 'name': 'sinka-paper-trial', 'selfLink': SOURCE, 'sizeGb': '10',
            'type': 'zones/asia-east1-b/diskTypes/pd-standard', 'creationTimestamp': '2026-10-17T08:58:00Z'}


def event(status='waiting_signal', revision=6, count=2, at=T, position=False, active=True):
    return json.dumps({'mode': 'local_paper', 'strategy': 'SNR-5M-1', 'status': status, 'active': active,
                       'positionOpen': position, 'closedTrades': count, 'quoteFresh': True, 'revision': revision, 'at': at}, separators=(',', ':'))


def before(position=False):
    return module.baseline(instance(), disk(), event(position=position))


class RecoveryTests(unittest.TestCase):
    def test_same_disk_and_checkpoint_survive_restart(self):
        logs = event('starting') + '\n' + event(at='2026-10-17T09:01:00Z')
        report = module.compare(before(), instance(), disk(), logs, T, NOW)
        self.assertTrue(report['restart_acceptance_complete'])
        self.assertTrue(report['same_persistent_disk'])
        self.assertFalse(report['trading_enabled'])

    def test_old_serial_cannot_prove_new_boot(self):
        report = module.compare(before(), instance(), disk(), event(at='2026-10-17T08:59:00Z'), T, NOW)
        self.assertFalse(report['restart_acceptance_complete'])
        self.assertFalse(report['new_boot_observed'])

    def test_changed_disk_vm_or_runtime_policy_blocks(self):
        for mutate in [lambda i, d: d.update(id='new-disk'), lambda i, d: i.update(id='new-vm'), lambda i, d: i['scheduling'].update(instanceTerminationAction='DELETE')]:
            i, d = instance(), disk()
            mutate(i, d)
            with self.assertRaises(ValueError):
                module.compare(before(), i, d, event('starting'), T, NOW)

    def test_checkpoint_or_trade_history_reset_blocks(self):
        for log in [event('starting', revision=1), event('starting', count=0)]:
            with self.assertRaises(ValueError):
                module.compare(before(), instance(), disk(), log, T, NOW)

    def test_disappearing_position_without_recorded_close_blocks(self):
        with self.assertRaises(ValueError):
            module.compare(before(True), instance(), disk(), event('starting'), T, NOW)

    def test_recovered_position_can_flatten_safely_without_new_entry(self):
        logs = '\n'.join([event('recovery_pending', position=True, active=False),
                           event('recovered', count=3, revision=7, active=False, at='2026-10-17T09:01:00Z'),
                           event('closed', count=3, revision=7, active=False, at='2026-10-17T09:01:00Z')])
        report = module.compare(before(True), instance(), disk(), logs, T, NOW)
        self.assertTrue(report['restart_acceptance_complete'])
        self.assertTrue(report['recovered_position_flattened'])
        self.assertFalse(report['worker_running'])

    def test_missing_revision_cannot_be_used_as_baseline(self):
        row = json.loads(event())
        row.pop('revision')
        with self.assertRaises(ValueError):
            module.baseline(instance(), disk(), json.dumps(row, separators=(',', ':')))

    def test_stopped_vm_or_stale_worker_does_not_pass(self):
        i = instance()
        i['status'] = 'TERMINATED'
        logs = event('starting') + '\n' + event(at='2026-10-17T09:01:00Z')
        self.assertFalse(module.compare(before(), i, disk(), logs, T, NOW)['restart_acceptance_complete'])
        self.assertFalse(module.compare(before(), instance(), disk(), logs, T, datetime(2026, 10, 17, 10, tzinfo=timezone.utc))['restart_acceptance_complete'])

class WarmupTests(unittest.TestCase):
    def test_stopped_identity_can_be_saved_without_serial(self):
        value = instance()
        value['status'] = 'TERMINATED'
        saved = module.identity(value, disk())
        self.assertEqual(saved['vm_id'], '42')
        self.assertNotIn('event', saved)

    def test_new_boot_with_existing_ledger_is_warmup_only(self):
        logs = event('starting') + '\n' + event(at='2026-10-17T09:01:00Z')
        report = module.warmup(module.identity(instance(), disk()), instance(), disk(), logs, T, NOW)
        self.assertTrue(report['warmup_ready'])
        self.assertFalse(report['restart_acceptance_complete'])

    def test_fresh_account_or_changed_disk_is_rejected(self):
        saved = module.identity(instance(), disk())
        with self.assertRaises(ValueError):
            module.warmup(saved, instance(), disk(), event('starting', revision=1), T, NOW)
        changed = disk()
        changed['id'] = 'replacement'
        with self.assertRaises(ValueError):
            module.warmup(saved, instance(), changed, '', T, NOW)

    def test_old_events_do_not_finish_warmup(self):
        report = module.warmup(module.identity(instance(), disk()), instance(), disk(), event('starting', at='2026-10-17T08:58:00Z'), T, NOW)
        self.assertFalse(report['warmup_ready'])


if __name__ == '__main__':
    unittest.main()
