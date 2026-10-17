"""Offline demo order planning and durable duplicate prevention. NO transport.

Not imported by any API, container, or deployed worker. No credentials are read.
Recording a dispatch is a simulated lifecycle step, never an exchange request.
"""
import hashlib
import json
import re
import sqlite3
from decimal import Decimal

from okx_instruments import instrument_spec, validate_with_instrument
from okx_orders import parse_order, reconcile
from okx_risk import positive


def demo_plan(*, account_scope, signal_id, instrument, side, contracts,
              price, stop, target, equity_usdt, leverage, demo=False):
    if demo is not True:
        raise ValueError("Explicit demo mode required")
    for value in (account_scope, signal_id):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
            raise ValueError("Stable account scope and signal identity required")
    validated = validate_with_instrument(
        instrument=instrument, side=side, contracts=contracts, price=price,
        stop=stop, equity_usdt=equity_usdt, leverage=leverage)
    spec = instrument_spec(instrument)
    px, sl, tp = (positive(value, name) for value, name in
                  ((price, "price"), (stop, "stop"), (target, "target")))
    if tp % spec["tick_sz"] or (tp - px) != 2 * (px - sl):
        raise ValueError("Target must be tick-aligned and exactly 2R")
    # Preserve existing margin/price-risk limits; add adverse slippage and fees.
    qty = positive(contracts, "contracts")
    direction = Decimal(1) if side == "buy" else Decimal(-1)
    entry = px * (1 + direction * Decimal("0.0002"))
    exit_px = sl * (1 - direction * Decimal("0.0002"))
    modeled_loss = qty * spec["ct_val"] * (
        abs(entry - exit_px) + (entry + exit_px) * Decimal("0.0005"))
    if modeled_loss > positive(equity_usdt, "equity") * Decimal("0.005"):
        raise ValueError("Fee-inclusive modeled loss exceeds limit")
    symbol = validated["symbol"]
    identity = json.dumps(["demo", account_scope, signal_id, symbol], separators=(",", ":"))
    digest = hashlib.sha256(identity.encode()).hexdigest()[:28]
    body = {
        "instId": symbol, "tdMode": "isolated", "posSide": "net",
        "side": side, "ordType": "market", "sz": format(qty, "f"),
        "clOrdId": "sk" + digest,
        "attachAlgoOrds": [{
            "attachAlgoClOrdId": "sa" + digest,
            "tpTriggerPx": format(tp, "f"), "tpOrdPx": "-1",
            "tpTriggerPxType": "last", "slTriggerPx": format(sl, "f"),
            "slOrdPx": "-1", "slTriggerPxType": "last",
        }],
    }
    return {"version": 1, "environment": "demo", "submission_enabled": False,
            "method": "POST", "path": "/api/v5/trade/order",
            "headers": {"x-simulated-trading": "1"}, "body": body,
            "preconditions": {"position_mode": "net_mode", "margin_mode": "isolated",
                              "verified_leverage": str(leverage)},
            "modeled_loss_usdt": str(modeled_loss)}


class DemoIntentStore:
    """Offline lifecycle journal. One unresolved intent blocks another entry.

    Durable dispatch reservation is one-shot: timeout, crash, missing order,
    rejection, or even terminal entry NEVER grants an automatic retry. A future
    executor must prove no exposure plus protective-order cleanup separately.
    This version deliberately has no unlock or submit method.
    """
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_intent ("
                        "id TEXT PRIMARY KEY, plan TEXT NOT NULL, "
                        "dispatched INTEGER NOT NULL DEFAULT 0, snapshot TEXT)")

    def close(self):
        self.db.close()

    def register(self, plan):
        # Only locally generated plan envelopes may enter the offline journal.
        if (not isinstance(plan, dict) or plan.get("version") != 1
                or plan.get("environment") != "demo"
                or plan.get("submission_enabled") is not False
                or plan.get("method") != "POST"
                or plan.get("path") != "/api/v5/trade/order"
                or plan.get("headers") != {"x-simulated-trading": "1"}):
            raise ValueError("Offline demo plan required")
        encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False)
        client_id = plan["body"]["clOrdId"]
        if not re.fullmatch(r"sk[0-9a-f]{28}", client_id):
            raise ValueError("Invalid client identity")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT plan FROM demo_intent WHERE id=?", (client_id,)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError("Signal identity reused with a different plan")
            elif self.db.execute("SELECT 1 FROM demo_intent LIMIT 1").fetchone():
                raise ValueError("Existing intent requires exposure reconciliation")
            else:
                self.db.execute("INSERT INTO demo_intent(id,plan) VALUES(?,?)", (client_id, encoded))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return client_id

    def reserve_dispatch(self, client_id):
        """Exercise crash fencing offline. Returns no request and sends nothing."""
        changed = self.db.execute(
            "UPDATE demo_intent SET dispatched=1 WHERE id=? AND dispatched=0", (client_id,)).rowcount
        if changed != 1:
            raise ValueError("Unknown or already dispatched intent; reconcile, never retry")

    def observe(self, client_id, row):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            record = self.db.execute(
                "SELECT plan,dispatched,snapshot FROM demo_intent WHERE id=?", (client_id,)).fetchone()
            if not record or record[1] != 1:
                raise ValueError("Dispatch reservation required")
            body = json.loads(record[0])["body"]
            current = parse_order(row)
            if (current.client_order_id != client_id or current.instrument != body["instId"]
                    or current.requested != Decimal(body["sz"])
                    or row.get("side") != body["side"] or row.get("posSide") != "net"
                    or row.get("tdMode") != "isolated" or row.get("ordType") != "market"):
                raise ValueError("Observed order does not match intent")
            if ((current.state == "filled" and current.filled != current.requested)
                    or (current.state == "live" and current.filled != 0)
                    or (current.state == "partially_filled" and
                        not 0 < current.filled < current.requested)):
                raise ValueError("Inconsistent order state and fills")
            if record[2]:
                reconcile(parse_order(json.loads(record[2])), row)
            # Store only known fields, never arbitrary response/error data.
            safe = {key: row[key] for key in
                    ("ordId", "clOrdId", "instId", "state", "sz", "accFillSz",
                     "side", "posSide", "tdMode", "ordType")}
            self.db.execute("UPDATE demo_intent SET snapshot=? WHERE id=?",
                            (json.dumps(safe, sort_keys=True), client_id))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def status(self, client_id):
        record = self.db.execute(
            "SELECT dispatched,snapshot FROM demo_intent WHERE id=?", (client_id,)).fetchone()
        if not record:
            raise ValueError("Unknown intent")
        if not record[0]:
            action = "offline_plan_only"
        elif not record[1]:
            action = "query_by_client_id_no_resubmit"
        else:
            order = parse_order(json.loads(record[1]))
            if order.filled > 0:
                # Attached TP/SL is NOT proof of active protection, especially partial fills.
                action = "halt_entries_verify_position_and_native_protection"
            elif order.state == "live":
                action = "query_by_client_id_no_resubmit"
            else:
                action = "verify_flat_and_cleanup_before_any_new_intent"
        return {"submission_enabled": False, "new_entry_allowed": False,
                "next_action": action}
