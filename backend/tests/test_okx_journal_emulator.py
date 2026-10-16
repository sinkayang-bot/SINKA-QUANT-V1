"""Firestore emulator tests for a lease-fenced OKX order journal."""
import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator not configured",
)

def setup():
    from google.cloud import firestore
    from okx_firestore_sync import OKXSyncLedger
    from okx_sync_lease import OKXSyncLease
    from okx_order_journal import OKXOrderJournal
    db = firestore.Client(project="sinka-emulator")
    ledger = OKXSyncLedger(db, "journal-" + uuid.uuid4().hex)
    first = OKXSyncLease(ledger, "current")
    stale = OKXSyncLease(ledger, "stale")
    journal = OKXOrderJournal(ledger)
    return first, stale, journal

def snapshot(state="live", filled="0"):
    from okx_orders import parse_order
    return parse_order({
        "ordId": "1234567", "clOrdId": "sinka-recovery-1",
        "instId": "BTC-USDT-SWAP", "state": state,
        "sz": "10", "accFillSz": filled,
    })

def test_explicit_registration_and_recovery():
    first, stale, journal = setup()
    assert first.acquire()
    assert not journal.register_existing(stale.worker_id, snapshot())
    assert first.worker_id != stale.worker_id
    assert journal.register_existing(first.worker_id, snapshot())
    with pytest.raises(ValueError, match="already registered"):
        journal.register_existing(first.worker_id, snapshot())
    assert journal.list_active() == [snapshot()]
    assert journal.refresh(first.worker_id, snapshot("partially_filled", "4"))
    assert journal.list_active() == [snapshot("partially_filled", "4")]
    with pytest.raises(ValueError, match="backwards"):
        journal.refresh(first.worker_id, snapshot())
    assert journal.refresh(first.worker_id, snapshot("filled", "10"))
    assert journal.list_active() == []
    assert journal.list_tracked() == [snapshot("filled", "10")]

def test_missing_order_cannot_be_auto_adopted():
    first, _stale, journal = setup()
    assert first.acquire()
    with pytest.raises(ValueError, match="Untracked"):
        journal.refresh(first.worker_id, snapshot())

def test_stale_worker_cannot_mutate_order():
    first, stale, journal = setup()
    assert first.acquire()
    assert journal.register_existing(first.worker_id, snapshot())
    assert not journal.refresh(stale.worker_id, snapshot("filled", "10"))
    journal.lease_ref.update({"expires_at": 0})
    assert stale.acquire()
    assert not journal.refresh(first.worker_id, snapshot("filled", "10"))
    assert journal.list_active() == [snapshot()]
