"""Fail-closed recent OKX fills audit for the read-only monitor.

Checkpoint overlap is diagnostic evidence only. It never proves a complete
trading history and never authorizes demo or live order submission.
"""
import asyncio
from okx_fills import recent_swap_fills, reconcile_fill_quantities
from okx_fill_checkpoint import reconcile_fill_window

async def audit_fill_window(*, reader, store, worker_id, tracked_orders):
    if store is None:
        return False, "fill_store_not_configured"
    try:
        checkpoint = await asyncio.to_thread(store.read_checkpoint)
        if checkpoint is None:
            return False, "initial_baseline_required"
        recent = await recent_swap_fills(reader)
        # OKX returns newest first; mapping retains page insertion order.
        window = tuple(recent.values())
        overlap = reconcile_fill_window(checkpoint, window)
        if not overlap.safe_to_append:
            return False, overlap.reason
        if not reconcile_fill_quantities(recent, tracked_orders):
            return False, "fill_order_quantity_mismatch"
        ok, reason = await asyncio.to_thread(
            store.append_verified_window, worker_id, window)
        if not ok:
            return False, reason
        return True, "fill_overlap_audited"
    except Exception:
        # Avoid exposing response bodies, credentials or Firestore metadata.
        return False, "fill_audit_failed"
