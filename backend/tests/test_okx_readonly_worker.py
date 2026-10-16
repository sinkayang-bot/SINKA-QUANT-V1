"""Mock-only tests for disabled-by-default OKX read-only worker."""
import asyncio
from unittest.mock import AsyncMock
from okx_readonly_worker import poll_once

class Lease:
    worker_id = "worker-1"
    def __init__(self, owns=True):
        self.acquire = lambda: owns
        self.release = lambda: True

class Ledger:
    def __init__(self, accepts=True):
        self.accepts = accepts
        self.written = None
    def write(self, worker_id, status):
        self.written = status
        return self.accepts

class PositionJournal:
    def __init__(self, accepts=True):
        self.accepts = accepts
        self.saved = []
    def write_snapshot(self, worker_id, positions):
        self.saved.append((worker_id, positions))
        return self.accepts

def test_worker_records_verified_empty_pending_inventory():
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[])
    ledger = Ledger()
    position_journal = PositionJournal()
    success = asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease(),
                                    position_journal=position_journal))
    assert position_journal.saved == [("worker-1", [])]
    assert success
    assert ledger.written.positions_matched
    assert not ledger.written.healthy
    assert ledger.written.reason == "fill_store_not_configured"
    assert not ledger.written.orders_matched

def test_worker_skips_when_lease_owned_elsewhere():
    class Reader:
        swap_positions = AsyncMock()
    obj = Reader()
    ledger = Ledger()
    success = asyncio.run(poll_once(reader=obj, ledger=ledger, lease=Lease(False)))
    assert not success
    assert ledger.written is None
    obj.swap_positions.assert_not_awaited()

def test_fencing_rejects_stale_worker_write():
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[])
    ledger = Ledger(accepts=False)
    assert not asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease()))

def test_worker_persists_verified_terminal_fill():
    from okx_orders import parse_order
    prior = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "2", "accFillSz": "0"})
    filled = parse_order({"ordId": "123", "clOrdId": "sinka123",
                          "instId": "BTC-USDT-SWAP", "state": "filled",
                          "sz": "2", "accFillSz": "2"})
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[])
        refresh = AsyncMock(return_value=filled)
    class Journal:
        def __init__(self):
            self.saved = []
        def list_tracked(self):
            return [prior]
        def refresh(self, worker_id, order):
            self.saved.append((worker_id, order))
            return True
    ledger = Ledger()
    journal = Journal()
    assert asyncio.run(poll_once(reader=Reader(), ledger=ledger,
                                 lease=Lease(), journal=journal))
    assert journal.saved == [("worker-1", filled)]
    assert not ledger.written.healthy
    assert ledger.written.reason == "filled_order_requires_position_reconciliation"

def test_position_write_rejection_marks_sync_unhealthy():
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[])
    ledger = Ledger()
    position_journal = PositionJournal(accepts=False)
    assert asyncio.run(poll_once(reader=Reader(), ledger=ledger,
                                 lease=Lease(), position_journal=position_journal))
    assert not ledger.written.healthy
    assert ledger.written.reason == "position_journal_write_denied"

class FillStore:
    def __init__(self, checkpoint, accepts=True):
        self.checkpoint = checkpoint
        self.accepts = accepts
        self.saved = []
    def read_checkpoint(self):
        return self.checkpoint
    def append_verified_window(self, worker_id, window):
        self.saved.append((worker_id, tuple(window)))
        return self.accepts, "overlap_appended" if self.accepts else "lease_denied"

def test_missing_initial_fill_baseline_never_reports_healthy():
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[])
    ledger = Ledger()
    store = FillStore(None)
    assert asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease(),
                                 position_journal=PositionJournal(),
                                 fill_store=store))
    assert store.saved == []
    assert not ledger.written.healthy
    assert ledger.written.reason == "initial_baseline_required"

def test_fill_watermark_write_rejection_marks_unhealthy():
    from okx_fills import parse_fill
    from okx_fill_checkpoint import FillCheckpoint
    old = parse_fill({"tradeId": "10", "ordId": "100",
                      "instId": "BTC-USDT-SWAP", "side": "buy",
                      "fillSz": "1", "fillPx": "100"})
    class Reader:
        swap_positions = AsyncMock(return_value=[])
        get = AsyncMock(return_value=[{
            "tradeId": "10", "billId": "110", "ordId": "100",
            "instId": "BTC-USDT-SWAP", "side": "buy",
            "fillSz": "1", "fillPx": "100"}])
    ledger = Ledger()
    store = FillStore(FillCheckpoint(old.trade_id, old), accepts=False)
    assert asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease(),
                                 position_journal=PositionJournal(),
                                 fill_store=store))
    assert not ledger.written.healthy
    assert ledger.written.reason == "fill_order_quantity_mismatch"
    assert store.saved == []
