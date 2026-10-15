"""Read-only order lookup tests; never contact OKX."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from okx_order_reader import OKXOrderReader

def reader():
    return OKXOrderReader(key="key", secret="secret", passphrase="pass")

def record():
    return {"ordId": "123", "clOrdId": "sinka123",
            "instId": "BTC-USDT-SWAP", "state": "live",
            "sz": "10", "accFillSz": "0"}

def test_read_order_by_id():
    obj = reader()
    obj.get = AsyncMock(return_value=[record()])
    result = asyncio.run(obj.order("BTC-USDT-SWAP", order_id="123"))
    assert result.order_id == "123"
    assert obj.get.await_args.args[0] == "/api/v5/trade/order"

def test_wrong_order_id_rejected():
    obj = reader()
    obj.get = AsyncMock(return_value=[record()])
    with pytest.raises(ValueError, match="Unexpected order ID"):
        asyncio.run(obj.order("BTC-USDT-SWAP", order_id="999"))

def test_ambiguous_lookup_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(reader().order("BTC-USDT-SWAP", order_id="123", client_order_id="sinka123"))

def test_order_refresh_does_not_send_orders():
    obj = reader()
    obj.get = AsyncMock(return_value=[record() | {"state": "filled", "accFillSz": "10"}])
    previous = __import__("okx_orders").parse_order(record())
    updated = asyncio.run(obj.refresh(previous))
    assert updated.state == "filled"
    assert obj.get.await_count == 1
