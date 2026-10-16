"""Emulator tests for lease-fenced position diagnostics."""
import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator not configured",
)

def test_only_lease_owner_may_write_position_snapshot():
    from google.cloud import firestore
    from okx_firestore_sync import OKXSyncLedger
    from okx_sync_lease import OKXSyncLease
    from okx_position_journal import OKXPositionJournal

    db = firestore.Client(project="sinka-emulator")
    ledger = OKXSyncLedger(db, "positions-" + uuid.uuid4().hex)
    position = OKXPositionJournal(ledger)
    first = OKXSyncLease(ledger, "worker-one")
    next_worker = OKXSyncLease(ledger, "worker-two")
    rows = [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
             "posSide": "net", "pos": "-1"}]

    assert first.acquire()
    assert not position.write_snapshot(next_worker.worker_id, rows)
    assert position.write_snapshot(first.worker_id, rows)
    snap = position.read_diagnostic()
    assert snap["positions"][0]["contracts"] == "-1"
    assert snap["trading_authorized"] is False
    assert snap["sequence"] == 1

    ledger.lease_ref.update({"expires_at": 0})
    assert next_worker.acquire()
    assert not position.write_snapshot(first.worker_id, [])
    assert position.write_snapshot(next_worker.worker_id, [])
    snap = position.read_diagnostic()
    assert snap["positions"] == []
    assert snap["sequence"] == 2
    assert snap["trading_authorized"] is False
