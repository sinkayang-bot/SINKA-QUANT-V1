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
    stage = "balances"
    try:
        balances = await reader.balances()
        if not isinstance(balances, list):
            raise ValueError("Malformed balance response")
        stage = "positions"
        positions = await reader.swap_positions()
        if not isinstance(positions, list):
            raise ValueError("Malformed position response")
        stage = "instruments"
        instruments = await reader.swap_instruments()
        if not isinstance(instruments, list):
            raise ValueError("Malformed instrument response")
        stage = "pending_orders"
        pending = await pending_swap_inventory(reader)

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
            "failed_stage": stage,
            "trading_enabled": False,
        }

def main():
    if os.getenv("SINKA_OKX_DEMO") != "true":
        raise SystemExit("Demo mode must be explicitly enabled.")
    if os.getenv("SINKA_OKX_READONLY_SMOKE") != "true":
        raise SystemExit("Read-only smoke check is disabled by default.")
    try:
        reader = OKXReadOnly(demo=True)
    except ValueError:
        result = {"ok": False, "mode": "demo_readonly",
                  "reason": "credentials_not_configured",
                  "failed_stage": "configuration", "trading_enabled": False}
    else:
        result = asyncio.run(inspect_demo(reader))
    print(json.dumps(result, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
