"""Fail-closed reconciliation of local and OKX isolated swap positions.

Pure validation: does not submit, amend or cancel exchange orders.
"""
from decimal import Decimal, InvalidOperation
from okx_risk import SYMBOLS

def number(value, *, allow_negative=False):
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid position quantity") from exc
    if not x.is_finite() or (x < 0 and not allow_negative):
        raise ValueError("Invalid position quantity")
    return x

def reconcile_positions(local, exchange_rows):
    """Return readiness for new orders; reject unknown or ambiguous exposure."""
    if not isinstance(exchange_rows, list):
        raise ValueError("Invalid exchange positions")
    active = []
    for row in exchange_rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid exchange position")
        qty = number(row.get("pos"), allow_negative=True)
        if qty == 0:
            continue
        if row.get("instId") not in SYMBOLS or row.get("mgnMode") != "isolated":
            return {"ready": False, "reason": "unexpected_exchange_exposure"}
        active.append(row)
    if len(active) > 1:
        return {"ready": False, "reason": "multiple_exchange_positions"}
    if local is None:
        if active:
            return {"ready": False, "reason": "exchange_position_not_tracked"}
        return {"ready": True, "reason": "flat"}
    if not isinstance(local, dict) or local.get("symbol") not in SYMBOLS:
        return {"ready": False, "reason": "invalid_local_position"}
    if not active:
        return {"ready": False, "reason": "local_position_missing_on_exchange"}
    remote = active[0]
    if remote["instId"] != local["symbol"]:
        return {"ready": False, "reason": "instrument_mismatch"}
    local_side = local.get("side")
    remote_side = remote.get("posSide")
    signed = number(remote["pos"], allow_negative=True)
    if remote_side == "long":
        if signed <= 0:
            return {"ready": False, "reason": "invalid_position_sign"}
        side = 1
    elif remote_side == "short":
        if signed == 0:
            return {"ready": False, "reason": "invalid_position_sign"}
        side = -1
    elif remote_side == "net":
        side = 1 if signed > 0 else -1
    else:
        return {"ready": False, "reason": "unknown_position_side"}
    if side != local_side:
        return {"ready": False, "reason": "position_side_mismatch"}
    if "contracts" not in local:
        return {"ready": False, "reason": "local_contract_quantity_missing"}
    if number(local["contracts"]) != abs(signed):
        return {"ready": False, "reason": "position_quantity_mismatch"}
    return {"ready": True, "reason": "matched"}
