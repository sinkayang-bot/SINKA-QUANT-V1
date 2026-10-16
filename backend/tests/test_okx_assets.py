import asyncio
import copy
import json

import pytest
from okx_assets import balance_snapshot, position_snapshot, pending_snapshot, inspect_assets


def balance():
    return [{"totalEq": "1234.567890123456789", "uid": "PRIVATE_UID", "details": [
        {"ccy": "USDT", "eq": "1200.5", "cashBal": "1190", "availBal": "", "upl": "10.5",
         "apiKey": "PRIVATE_KEY"}]}]


def order(identifier="123"):
    return {"ordId": identifier, "clOrdId": "PRIVATE_CLIENT_ID", "instId": "BTC-USDT-SWAP",
            "side": "buy", "posSide": "net", "state": "live", "sz": "2", "accFillSz": "0", "px": "65000"}


def test_balance_precision_missing_values_and_output_allowlist():
    summary, coins = balance_snapshot(balance())
    assert summary["total_equity_usd"] == "1234.567890123456789"
    assert coins[0]["available_balance"] is None
    assert coins[0]["unrealized_pnl"] == "10.5"
    assert "PRIVATE" not in json.dumps([summary, coins])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e99999", "<script>", 100, "1" * 97])
def test_invalid_balance_number_is_rejected(value):
    raw = balance(); raw[0]["totalEq"] = value
    with pytest.raises(ValueError): balance_snapshot(raw)


def test_duplicate_currencies_and_missing_account_are_not_silently_hidden():
    raw = balance(); raw[0]["details"] *= 2
    for candidate in (raw, [], {}, [{"details": "invalid"}]):
        with pytest.raises(ValueError): balance_snapshot(candidate)


def test_position_units_signed_quantity_and_currency_are_preserved():
    result = position_snapshot([{"instId": "BTC-USDT-SWAP", "posSide": "net", "mgnMode": "cross",
                                 "ccy": "USDT", "pos": "-2", "upl": "-0.0001", "avgPx": "65000",
                                 "markPx": "65001", "posId": "PRIVATE_POSITION_ID"}])
    assert result[0]["contracts"] == "-2"
    assert result[0]["currency"] == "USDT"
    assert result[0]["unrealized_pnl"] == "-0.0001"
    assert "PRIVATE" not in json.dumps(result)
    with pytest.raises(ValueError): position_snapshot([{"instId": "UNKNOWN", "pos": "1"}])


def test_pending_orders_do_not_require_client_ids_and_never_expose_identifiers():
    class Reader:
        async def get(self, path, params):
            assert path == "/api/v5/trade/orders-pending"
            assert params == {"instType": "SWAP", "limit": "100"}
            raw = order(); raw.pop("clOrdId")
            return [raw]
    result = asyncio.run(pending_snapshot(Reader()))
    assert result[0]["contracts"] == "2"
    assert "ordId" not in json.dumps(result) and "123" not in json.dumps(result)


def test_pending_pagination_repetition_fails_closed():
    class Reader:
        async def get(self, path, params): return [order(str(i)) for i in range(1, 101)]
    with pytest.raises(ValueError): asyncio.run(pending_snapshot(Reader()))


@pytest.mark.parametrize("field,value", [("sz", "0"), ("accFillSz", "2"), ("state", "filled"), ("side", "unknown")])
def test_inconsistent_pending_order_is_rejected(field, value):
    class Reader:
        async def get(self, path, params):
            raw = order(); raw[field] = value; return [raw]
    with pytest.raises(ValueError): asyncio.run(pending_snapshot(Reader()))


@pytest.mark.parametrize("permission", ["read_only,trade", "trade", "", None])
def test_permission_check_precedes_all_asset_requests(permission):
    class Reader:
        async def account_config(self): return [{"perm": permission}]
        async def balances(self): pytest.fail("Must not read balances")
    with pytest.raises(ValueError): asyncio.run(inspect_assets(Reader()))
