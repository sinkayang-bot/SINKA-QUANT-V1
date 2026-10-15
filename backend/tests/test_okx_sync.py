"""Mocked OKX sync checks. No exchange access."""
import asyncio
from unittest.mock import AsyncMock
from okx_sync import sync_once
from okx_orders import parse_order

def reader(positions=None, fail=False):
    class Fake:
        async def swap_positions(self):
            if fail:
                raise ConnectionError("network unavailable")
            return [] if positions is None else positions
        refresh = AsyncMock()
    return Fake()

def test_flat_synced():
    status = asyncio.run(sync_once(reader=reader(), local_position=None))
    assert status.healthy and status.positions_matched and status.orders_matched

def test_unknown_position_blocks():
    remote = [{"instId": "BTC-USDT-SWAP", "mgnMode": "isolated",
               "posSide": "long", "pos": "1"}]
    status = asyncio.run(sync_once(reader=reader(remote), local_position=None))
    assert not status.healthy
    assert status.reason == "exchange_position_not_tracked"

def test_network_failure_blocks():
    status = asyncio.run(sync_once(reader=reader(fail=True), local_position=None))
    assert not status.healthy
    assert status.reason == "exchange_sync_failed"

def test_active_orders_refreshed():
    prior = parse_order({"ordId": "123", "clOrdId": "sinka123",
                         "instId": "BTC-USDT-SWAP", "state": "live",
                         "sz": "1", "accFillSz": "0"})
    obj = reader()
    status = asyncio.run(sync_once(reader=obj, local_position=None,
                                   tracked_orders=[prior]))
    assert status.healthy
    obj.refresh.assert_awaited_once_with(prior)
