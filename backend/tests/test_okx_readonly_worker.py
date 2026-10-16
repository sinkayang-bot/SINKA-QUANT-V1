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

def test_worker_writes_unverified_order_inventory_not_ready():
    class Reader:
        swap_positions = AsyncMock(return_value=[])
    ledger = Ledger()
    success = asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease()))
    assert success
    assert ledger.written.positions_matched
    assert not ledger.written.healthy
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
    ledger = Ledger(accepts=False)
    assert not asyncio.run(poll_once(reader=Reader(), ledger=ledger, lease=Lease()))
