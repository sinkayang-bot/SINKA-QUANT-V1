"""Mock-only fill audit tests: baseline, overlap, duplicates and failures."""
import asyncio
from unittest.mock import AsyncMock
from okx_fills import parse_fill
from okx_fill_checkpoint import FillCheckpoint
from okx_fill_audit import audit_fill_window
from okx_orders import parse_order

def fill(i):
    return parse_fill({"tradeId": str(i), "billId": str(2000-i),
                       "ordId": "123", "instId": "BTC-USDT-SWAP",
                       "side": "buy", "fillSz": "1", "fillPx": "100000"})

class Store:
    def __init__(self, checkpoint, accepted=True):
        self.checkpoint = checkpoint
        self.accepted = accepted
        self.saved = []
    def read_checkpoint(self):
        return self.checkpoint
    def append_verified_window(self, worker_id, window):
        self.saved.append((worker_id, tuple(window)))
        return (self.accepted, "overlap_appended" if self.accepted else "lease_denied")

class Reader:
    def __init__(self, rows):
        self.get = AsyncMock(return_value=rows)

def run(store, rows, orders=()):
    return asyncio.run(audit_fill_window(
        reader=Reader(rows), store=store, worker_id="worker1",
        tracked_orders=orders))

def test_missing_baseline_rejected_before_exchange_call():
    store = Store(None)
    assert run(store, []) == (False, "initial_baseline_required")
    assert not store.saved

def test_unconfigured_store_fails_closed():
    assert run(None, []) == (False, "fill_store_not_configured")

def test_overlapping_fills_are_committed_once():
    prior = fill(10)
    store = Store(FillCheckpoint(prior.trade_id, prior))
    order = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "partially_filled",
                         "sz": "10", "accFillSz": "2"})
    rows = [
        {"tradeId": "11", "billId": "1989", "ordId": "123",
         "instId": "BTC-USDT-SWAP", "side": "buy",
         "fillSz": "1", "fillPx": "100000"},
        {"tradeId": "10", "billId": "1990", "ordId": "123",
         "instId": "BTC-USDT-SWAP", "side": "buy",
         "fillSz": "1", "fillPx": "100000"},
    ]
    assert run(store, rows, [order]) == (False, "new_fills_require_fresh_poll")
    assert [f.trade_id for f in store.saved[0][1]] == ["11", "10"]

def test_missing_anchor_never_writes():
    prior = fill(10)
    store = Store(FillCheckpoint(prior.trade_id, prior))
    assert run(store, [{"tradeId": "12", "billId": "1988", "ordId": "123",
                        "instId": "BTC-USDT-SWAP", "side": "buy",
                        "fillSz": "1", "fillPx": "100000"}])[0] is False
    assert not store.saved

def test_store_denial_keeps_unhealthy():
    prior = fill(10)
    store = Store(FillCheckpoint(prior.trade_id, prior), accepted=False)
    order = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "partially_filled",
                         "sz": "10", "accFillSz": "1"})
    rows = [{"tradeId": "10", "billId": "1990", "ordId": "123",
             "instId": "BTC-USDT-SWAP", "side": "buy",
             "fillSz": "1", "fillPx": "100000"}]
    assert run(store, rows, [order]) == (False, "lease_denied")

def test_replay_of_same_anchor_is_diagnostic_only():
    prior = fill(10)
    store = Store(FillCheckpoint(prior.trade_id, prior))
    order = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "filled",
                         "sz": "1", "accFillSz": "1"})
    rows = [{"tradeId": "10", "billId": "1990", "ordId": "123",
             "instId": "BTC-USDT-SWAP", "side": "buy",
             "fillSz": "1", "fillPx": "100000"}]
    assert run(store, rows, [order]) == (True, "fill_overlap_audited")
