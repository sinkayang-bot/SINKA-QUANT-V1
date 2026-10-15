"""Fail-closed preflight for OKX isolated USDT swap orders.

This module validates candidate orders only. It never sends requests to OKX.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

SYMBOLS = frozenset({
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
    "XRP-USDT-SWAP", "DOGE-USDT-SWAP",
})

def positive(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"Invalid {name}")
    return number

@dataclass(frozen=True)
class RiskLimits:
    max_leverage: int = 3
    max_margin_fraction: Decimal = Decimal("0.10")
    max_loss_fraction: Decimal = Decimal("0.005")

def validate_isolated_order(*, symbol, side, contracts, price, stop,
                            equity_usdt, ct_val, lot_sz, min_sz,
                            leverage, limits=RiskLimits()):
    if symbol not in SYMBOLS:
        raise ValueError("Instrument not allowlisted")
    if side not in ("buy", "sell"):
        raise ValueError("Invalid side")
    qty = positive(contracts, "contracts")
    px = positive(price, "price")
    stop_px = positive(stop, "stop")
    equity = positive(equity_usdt, "equity")
    contract_value = positive(ct_val, "contract value")
    lot = positive(lot_sz, "lot size")
    minimum = positive(min_sz, "minimum size")
    lev = positive(leverage, "leverage")
    if lev != lev.to_integral_value() or lev > limits.max_leverage:
        raise ValueError("Leverage exceeds limit")
    if qty < minimum or qty != (qty / lot).to_integral_value(rounding=ROUND_DOWN) * lot:
        raise ValueError("Invalid contract size increment")
    if side == "buy" and stop_px >= px:
        raise ValueError("Long stop must be below entry")
    if side == "sell" and stop_px <= px:
        raise ValueError("Short stop must be above entry")
    # Conservative linear-USDT contract approximation. Reject inverse contracts.
    if not symbol.endswith("-USDT-SWAP"):
        raise ValueError("Only linear USDT swaps supported")
    notional = qty * contract_value * px
    margin = notional / lev
    worst_case_price_loss = qty * contract_value * abs(px - stop_px)
    if margin > equity * limits.max_margin_fraction:
        raise ValueError("Margin allocation exceeds limit")
    if worst_case_price_loss > equity * limits.max_loss_fraction:
        raise ValueError("Stop distance risk exceeds limit")
    return {"symbol": symbol, "tdMode": "isolated", "side": side,
            "sz": str(qty), "leverage": str(lev),
            "notional_usdt": str(notional),
            "estimated_margin_usdt": str(margin),
            "estimated_stop_loss_usdt": str(worst_case_price_loss),
            "live_submission_allowed": False}
