"""Validate OKX SWAP instrument metadata before risk sizing. No order submission."""
from decimal import Decimal
from okx_risk import SYMBOLS, positive, validate_isolated_order

def instrument_spec(row):
    if not isinstance(row, dict):
        raise ValueError("Invalid instrument metadata")
    symbol = row.get("instId")
    if symbol not in SYMBOLS or row.get("instType") != "SWAP":
        raise ValueError("Instrument not allowed")
    if row.get("state") != "live":
        raise ValueError("Instrument not trading")
    if row.get("ctType") != "linear" or row.get("settleCcy") != "USDT":
        raise ValueError("Not a linear USDT-settled swap")
    if row.get("ctValCcy") != symbol.split("-")[0]:
        raise ValueError("Unexpected contract denomination")
    return {
        "symbol": symbol,
        "ct_val": positive(row.get("ctVal"), "contract value"),
        "lot_sz": positive(row.get("lotSz"), "lot size"),
        "min_sz": positive(row.get("minSz"), "minimum size"),
        "tick_sz": positive(row.get("tickSz"), "tick size"),
    }

def validate_with_instrument(*, instrument, side, contracts, price, stop,
                             equity_usdt, leverage):
    spec = instrument_spec(instrument)
    tick = spec["tick_sz"]
    for label, value in (("price", price), ("stop", stop)):
        amount = positive(value, label)
        if amount % tick != Decimal("0"):
            raise ValueError(f"{label} not aligned to tick size")
    return validate_isolated_order(
        symbol=spec["symbol"], side=side, contracts=contracts,
        price=price, stop=stop, equity_usdt=equity_usdt,
        ct_val=spec["ct_val"], lot_sz=spec["lot_sz"],
        min_sz=spec["min_sz"], leverage=leverage)
