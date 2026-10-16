"""Opt-in OKX demo-account read-only connection smoke check.

Prints sanitized status ONLY. Never sends trade requests or reveals balances,
positions, order IDs, client IDs, credentials, or exchange error bodies.
"""
import asyncio
import json
import os

from okx_readonly import OKXReadOnly
from okx_pending_orders import pending_swap_inventory

async def inspect_demo(reader):
    try:
        balances = await reader.balances()
        positions = await reader.swap_positions()
        instruments = await reader.swap_instruments()
        pending = await pending_swap_inventory(reader)
        if not all(isinstance(x, list) for x in (balances, positions, instruments)):
            raise ValueError("Malformed OKX response")
        return {
            "ok": True,
            "mode": "demo_readonly",
            "balances_endpoint": "ok",
            "positions_endpoint": "ok",
            "instruments_endpoint": "ok",
            "pending_orders_endpoint": "ok",
            "pending_order_count": len(pending),
            "trading_enabled": False,
        }
    except Exception:
        return {
            "ok": False, "mode": "demo_readonly",
            "reason": "readonly_connection_or_validation_failed",
            "trading_enabled": False,
        }

def main():
    if os.getenv("SINKA_OKX_DEMO") != "true":
        raise SystemExit("Demo mode must be explicitly enabled.")
    if os.getenv("SINKA_OKX_READONLY_SMOKE") != "true":
        raise SystemExit("Read-only smoke check is disabled by default.")
    result = asyncio.run(inspect_demo(OKXReadOnly(demo=True)))
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
