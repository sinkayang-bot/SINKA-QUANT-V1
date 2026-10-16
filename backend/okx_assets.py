"""Owner-facing demo snapshot: bounded GETs and explicit output fields only."""
import re
from decimal import Decimal

from okx_readonly import ALLOWED_SYMBOLS

DECIMAL = re.compile(r"-?\d+(?:\.\d+)?\Z", re.ASCII)
CURRENCY = re.compile(r"[A-Z0-9]{1,24}\Z", re.ASCII)


def number(value, *, optional=False, nonnegative=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 96 or not DECIMAL.fullmatch(value):
        raise ValueError("Invalid numeric field")
    parsed = Decimal(value)
    if nonnegative and parsed < 0:
        raise ValueError("Negative numeric field")
    return value


def currency(value):
    if not isinstance(value, str) or not CURRENCY.fullmatch(value):
        raise ValueError("Invalid currency")
    return value


def choice(value, allowed):
    if value not in allowed:
        raise ValueError("Invalid enum field")
    return value


def rows(value, limit):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(r, dict) for r in value):
        raise ValueError("Invalid rows")
    return value


def balance_snapshot(raw):
    account = rows(raw, 1)
    if len(account) != 1:
        raise ValueError("Missing account balance")
    account = account[0]
    currencies, seen = [], set()
    for row in rows(account.get("details"), 1000):
        ccy = currency(row.get("ccy"))
        if ccy in seen:
            raise ValueError("Duplicate currency")
        seen.add(ccy)
        currencies.append({"currency": ccy, **{
            name: number(row.get(field), optional=True)
            for name, field in (("equity", "eq"), ("cash_balance", "cashBal"),
                                ("available_balance", "availBal"), ("unrealized_pnl", "upl"))}})
    return {"total_equity_usd": number(account.get("totalEq"), optional=True)}, currencies


def position_snapshot(raw):
    result, seen = [], set()
    for row in rows(raw, 1000):
        qty = number(row.get("pos"))
        if Decimal(qty) == 0:
            continue
        instrument = choice(row.get("instId"), ALLOWED_SYMBOLS)
        side = choice(row.get("posSide"), {"net", "long", "short"})
        margin = choice(row.get("mgnMode"), {"cross", "isolated"})
        ccy = currency(row.get("ccy"))
        if side != "net" and Decimal(qty) < 0:
            raise ValueError("Invalid directional quantity")
        identity = (instrument, side, margin, ccy)
        if identity in seen:
            raise ValueError("Duplicate position")
        seen.add(identity)
        result.append({"instrument": instrument, "side": side, "margin_mode": margin,
                       "currency": ccy, "contracts": qty,
                       "average_price": number(row.get("avgPx"), optional=True, nonnegative=True),
                       "mark_price": number(row.get("markPx"), optional=True, nonnegative=True),
                       "unrealized_pnl": number(row.get("upl"), optional=True),
                       "notional_usd": number(row.get("notionalUsd"), optional=True)})
    return result


async def pending_snapshot(reader):
    result, seen, cursor = [], set(), None
    for _ in range(20):
        params = {"instType": "SWAP", "limit": "100"}
        if cursor:
            params["after"] = cursor
        page = rows(await reader.get("/api/v5/trade/orders-pending", params), 100)
        for row in page:
            order_id = row.get("ordId")
            if not isinstance(order_id, str) or not re.fullmatch(r"[0-9]{1,40}", order_id):
                raise ValueError("Invalid order identity")
            if order_id in seen:
                raise ValueError("Repeated order identity")
            seen.add(order_id)
            size = number(row.get("sz"), nonnegative=True)
            filled = number(row.get("accFillSz"), nonnegative=True)
            if Decimal(size) <= 0 or Decimal(filled) >= Decimal(size):
                raise ValueError("Inconsistent active order quantity")
            result.append({"instrument": choice(row.get("instId"), ALLOWED_SYMBOLS),
                           "side": choice(row.get("side"), {"buy", "sell"}),
                           "position_side": choice(row.get("posSide"), {"net", "long", "short"}),
                           "state": choice(row.get("state"), {"live", "partially_filled"}),
                           "contracts": size, "filled_contracts": filled,
                           "price": number(row.get("px"), optional=True, nonnegative=True)})
        if len(page) < 100:
            return result
        cursor = page[-1]["ordId"]
    raise ValueError("Pending order snapshot exceeds bounded scan")


async def inspect_assets(reader):
    # Strict permission check before any account amounts are read.
    config = await reader.account_config()
    if (not isinstance(config, list) or len(config) != 1 or not isinstance(config[0], dict)
            or config[0].get("perm") != "read_only"):
        raise ValueError("Exclusively read-only API permission required")
    summary, currencies = balance_snapshot(await reader.balances())
    positions = position_snapshot(await reader.swap_positions())
    pending = await pending_snapshot(reader)
    return {"summary": summary, "currencies": currencies, "positions": positions, "pending_orders": pending}
