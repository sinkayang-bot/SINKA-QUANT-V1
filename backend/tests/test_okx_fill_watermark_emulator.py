"""Firestore emulator tests for lease-fenced OKX fill watermarks."""
import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator not configured",
)

def sample(i):
    from okx_fills import parse_fill
    return parse_fill({"tradeId": str(i), "ordId": "12345",
                       "instId": "BTC-USDT-SWAP", "side": "buy",
                       "fillSz": "1", "fillPx": "100000"})

def test_fenced_watermark_append_and_replay():
    from google.cloud import firestore
    from okx_firestore_sync import OKXSyncLedger
    from okx_sync_lease import OKXSyncLease
    from okx_fill_watermark_store import OKXFillWatermarkStore, encode_fill

    db = firestore.Client(project="sinka-emulator")
    ledger = OKXSyncLedger(db, "fill-test-" + uuid.uuid4().hex)
    store = OKXFillWatermarkStore(ledger)
    first = OKXSyncLease(ledger, "worker-1")
    next_worker = OKXSyncLease(ledger, "worker-2")
    assert first.acquire()
    assert store.append_verified_window(first.worker_id, [sample(1)]) == (
        False, "initial_baseline_required")
    # Test-only fixture: independent verified baseline established before tracking.
    store.checkpoint_ref.create({"anchor_id": "1", "anchor": encode_fill(sample(1)),
                                 "baseline_reviewed": True})
    assert store.append_verified_window(next_worker.worker_id, [sample(2), sample(1)])[0] is False
    assert store.append_verified_window(first.worker_id, [sample(3), sample(2), sample(1)])[0]
    assert store.read_checkpoint().anchor_id == "3"
    assert store.fills_ref.document("2").get().exists
    assert store.fills_ref.document("3").get().exists
    assert store.append_verified_window(first.worker_id, [sample(3), sample(2)])[0]
    assert not store.append_verified_window(first.worker_id, [sample(5)])[0]
    ledger.lease_ref.update({"expires_at": 0})
    assert next_worker.acquire()
    assert store.append_verified_window(first.worker_id, [sample(4), sample(3)])[0] is False
    assert store.append_verified_window(next_worker.worker_id, [sample(4), sample(3)])[0]
    assert store.read_checkpoint().anchor_id == "4"
