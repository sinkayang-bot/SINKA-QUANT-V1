"""Firestore emulator tests for OKX diagnostic sync lease and fencing."""
import os
import uuid
from datetime import datetime, timezone
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured",
)

def test_only_current_worker_can_write_diagnostics():
    from google.cloud import firestore
    from okx_firestore_sync import OKXSyncLedger
    from okx_sync_lease import OKXSyncLease
    from okx_sync import SyncStatus

    db = firestore.Client(project="sinka-emulator")
    ledger = OKXSyncLedger(db, "test-" + uuid.uuid4().hex)
    first = OKXSyncLease(ledger, "worker-1")
    second = OKXSyncLease(ledger, "worker-2")
    status = SyncStatus(True, "synced", datetime.now(timezone.utc).isoformat(),
                        True, True)

    assert first.acquire()
    assert not second.acquire()
    assert not ledger.write(second.worker_id, status)
    assert ledger.write(first.worker_id, status)
    assert ledger.read_diagnostic()["sequence"] == 1
    assert first.release()
    assert second.acquire()
    assert not ledger.write(first.worker_id, status)
    assert ledger.write(second.worker_id, status)
    assert ledger.read_diagnostic()["sequence"] == 2

def test_expired_worker_cannot_overwrite_new_owner():
    from google.cloud import firestore
    from okx_firestore_sync import OKXSyncLedger
    from okx_sync_lease import OKXSyncLease
    from okx_sync import SyncStatus

    db = firestore.Client(project="sinka-emulator")
    ledger = OKXSyncLedger(db, "test-" + uuid.uuid4().hex)
    first = OKXSyncLease(ledger, "stale")
    second = OKXSyncLease(ledger, "successor")
    status = SyncStatus(False, "exchange_sync_failed",
                        datetime.now(timezone.utc).isoformat(), False, False)
    assert first.acquire()
    ledger.lease_ref.update({"expires_at": 0})
    assert second.acquire()
    assert not ledger.write(first.worker_id, status)
    assert ledger.write(second.worker_id, status)
    assert ledger.read_diagnostic()["reason"] == "exchange_sync_failed"
