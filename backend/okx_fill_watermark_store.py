"""Firestore checkpoint persistence for VERIFIED overlapping recent OKX fills.

This ledger cannot bootstrap a missing baseline. A separate reviewed bootstrap
is required; no code here creates a trading authorization or exchange order.
"""
import time
from google.cloud import firestore
from okx_fill_checkpoint import FillCheckpoint, reconcile_fill_window
from okx_fills import Fill
from decimal import Decimal

MAX_APPEND = 100

def encode_fill(fill):
    if not isinstance(fill, Fill):
        raise ValueError("Invalid fill")
    return {
        "tradeId": fill.trade_id, "ordId": fill.order_id,
        "instId": fill.instrument, "side": fill.side,
        "fillSz": str(fill.contracts), "fillPx": str(fill.price),
    }

def decode_fill(row):
    from okx_fills import parse_fill
    return parse_fill(row)

class OKXFillWatermarkStore:
    def __init__(self, sync_ledger):
        self.db = sync_ledger.db
        self.lease_ref = sync_ledger.lease_ref
        account = self.lease_ref.parent.parent
        self.checkpoint_ref = account.collection("runtime").document("fill_watermark")
        self.fills_ref = account.collection("tracked_fills")

    def read_checkpoint(self):
        snap = self.checkpoint_ref.get()
        if not snap.exists:
            return None
        payload = snap.to_dict()
        if payload.get("baseline_reviewed") is not True:
            raise ValueError("Unreviewed fill baseline")
        anchor = decode_fill(payload["anchor"])
        if anchor.trade_id != payload["anchor_id"]:
            raise ValueError("Corrupted fill watermark")
        return FillCheckpoint(anchor.trade_id, anchor)

    @firestore.transactional
    def _append(self, tx, worker_id, window, now):
        lease_snap = self.lease_ref.get(transaction=tx)
        lease = lease_snap.to_dict() if lease_snap.exists else {}
        if not worker_id or lease.get("worker_id") != worker_id or float(lease.get("expires_at", 0)) <= now:
            return False, "lease_denied"
        checkpoint_snap = self.checkpoint_ref.get(transaction=tx)
        if not checkpoint_snap.exists:
            return False, "initial_baseline_required"
        existing = checkpoint_snap.to_dict()
        if existing.get("baseline_reviewed") is not True:
            return False, "baseline_review_required"
        anchor = decode_fill(existing["anchor"])
        previous = FillCheckpoint(existing["anchor_id"], anchor)
        if previous.anchor_id != anchor.trade_id:
            raise ValueError("Corrupted fill watermark")
        result = reconcile_fill_window(previous, window)
        if not result.safe_to_append:
            return False, result.reason
        if len(result.unseen) > MAX_APPEND:
            return False, "batch_too_large"
        refs = [self.fills_ref.document(fill.trade_id) for fill in result.unseen]
        existing_docs = [ref.get(transaction=tx) for ref in refs]
        if any(snap.exists for snap in existing_docs):
            return False, "duplicate_persisted_fill"
        for ref, fill in zip(refs, result.unseen):
            tx.create(ref, {"fill": encode_fill(fill),
                            "created_at": firestore.SERVER_TIMESTAMP})
        if result.next_checkpoint != previous:
            tx.update(self.checkpoint_ref, {
                "anchor_id": result.next_checkpoint.anchor_id,
                "anchor": encode_fill(result.next_checkpoint.anchor_fill),
                "updated_at": firestore.SERVER_TIMESTAMP,
                "writer": worker_id,
            })
        return True, "overlap_appended"

    def append_verified_window(self, worker_id, window):
        return self._append(self.db.transaction(), worker_id, tuple(window), time.time())
