"""Offline executor integration with an injected rehearsal exchange only.

No HTTP client, credentials, SDK, production broker or deployment wiring.
Enabled is a test harness opt-in, NOT permission to trade on an exchange.
"""
from copy import deepcopy
import json
from okx_demo_intent import DemoIntentStore
from okx_orders import ACTIVE, parse_order
from okx_strategy_bridge import plan_strategy_signal, timestamp

HEADERS = {"x-simulated-trading": "1"}


def result(phase):
    return {"mode": "offline_rehearsal", "phase": phase,
            "exchange_submission_enabled": False, "new_entry_allowed": False}


def receipt(response, client_id, expected_order_id=None):
    """Acceptance only; never proof of a fill or successful cancellation."""
    if not isinstance(response, dict) or response.get("code") != "0":
        return None
    rows = response.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    order_id = row.get("ordId")
    if (row.get("sCode") != "0" or row.get("clOrdId") != client_id
            or not isinstance(order_id, str) or not order_id.isdigit()
            or (expected_order_id is not None and order_id != expected_order_id)):
        return None
    return order_id


class DemoExecutionJournal(DemoIntentStore):
    def __init__(self, path):
        super().__init__(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_execution ("
            "id TEXT PRIMARY KEY, entry_status TEXT NOT NULL, exchange_order_id TEXT, "
            "observed_at_ms INTEGER, cancel_status TEXT NOT NULL DEFAULT 'not_requested')")

    def reserve_entry(self, client_id):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            changed = self.db.execute(
                "UPDATE demo_intent SET dispatched=1 WHERE id=? AND dispatched=0", (client_id,)).rowcount
            if changed != 1:
                raise ValueError("Entry already reserved; reconcile without resubmitting")
            self.db.execute("INSERT INTO demo_execution(id,entry_status) VALUES(?,'reserved')", (client_id,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def entry_receipt(self, client_id, status, order_id=None):
        if status not in {"acknowledged", "unknown", "blocked_before_send"}:
            raise ValueError("Invalid receipt status")
        if self.db.execute("UPDATE demo_execution SET entry_status=?,exchange_order_id=? "
            "WHERE id=? AND entry_status='reserved'", (status, order_id, client_id)).rowcount != 1:
            raise ValueError("Entry receipt already recorded or reservation missing")

    def tracked(self, client_id):
        row = self.db.execute("SELECT i.plan,i.snapshot,e.entry_status,e.exchange_order_id,"
            "e.observed_at_ms,e.cancel_status FROM demo_intent i JOIN demo_execution e ON i.id=e.id "
            "WHERE i.id=? AND i.dispatched=1", (client_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown executor intent")
        return row

    def reserve_cancel(self, client_id, *, now_ms):
        timestamp(now_ms)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.tracked(client_id)
            if not row[1] or type(row[4]) is not int or not 0 <= now_ms - row[4] <= 5000:
                raise ValueError("Fresh observed entry required")
            order = parse_order(json.loads(row[1]))
            if order.state not in ACTIVE:
                raise ValueError("No active remainder")
            if self.db.execute("UPDATE demo_execution SET cancel_status='reserved' "
                "WHERE id=? AND cancel_status='not_requested'", (client_id,)).rowcount != 1:
                raise ValueError("Cancel already reserved; query without retry")
            self.db.execute("COMMIT")
            return order
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def cancel_receipt(self, client_id, status):
        if status not in {"acknowledged", "unknown", "blocked_before_send"}:
            raise ValueError("Invalid cancel status")
        if self.db.execute("UPDATE demo_execution SET cancel_status=? "
            "WHERE id=? AND cancel_status='reserved'", (status, client_id)).rowcount != 1:
            raise ValueError("Cancel receipt already recorded or reservation missing")


class DemoExecutor:
    """One-shot rehearsal coordinator, disabled by default.

    Broker port: async place_entry(body, headers), lookup_entry(symbol, client_id),
    cancel_entry(body, headers). Only an explicitly marked demo rehearsal broker
    can be used. No implementation capable of contacting OKX is supplied.
    Gates are synchronous harness attestations, not account/lease verification.
    A future real runtime must implement and verify those boundaries separately.
    """
    def __init__(self, journal, broker, *, clock, entry_gate, recovery_gate,
                 enabled=False):
        self.journal, self.broker, self.clock = journal, broker, clock
        self.entry_gate, self.recovery_gate, self.enabled = entry_gate, recovery_gate, enabled

    def armed(self):
        return (self.enabled is True and getattr(self.broker, "environment", None) == "demo"
                and getattr(self.broker, "rehearsal_only", None) is True)

    async def enter(self, source):
        if not self.armed():
            return result("disabled")
        # Freeze caller inputs; gates receive copies and cannot mutate this plan.
        frozen = deepcopy(source)
        candidate = plan_strategy_signal(**frozen, now_ms=timestamp(self.clock()))
        if self.entry_gate(deepcopy(candidate)) is not True:
            return result("entry_gate_blocked")
        client_id = self.journal.register(candidate["plan"])
        self.journal.reserve_entry(client_id)  # durable BEFORE calling the exchange port
        try:
            current = plan_strategy_signal(**frozen, now_ms=timestamp(self.clock()))
            if (current != candidate or self.entry_gate(deepcopy(current)) is not True
                    or not self.armed()
                    or timestamp(self.clock()) > candidate["expires_at_ms"]):
                self.journal.entry_receipt(client_id, "blocked_before_send")
                return result("entry_blocked_after_reservation")
        except Exception:
            self.journal.entry_receipt(client_id, "blocked_before_send")
            return result("entry_blocked_after_reservation")
        try:
            response = await self.broker.place_entry(deepcopy(candidate["plan"]["body"]), deepcopy(HEADERS))
            order_id = receipt(response, client_id)
        except Exception:
            order_id = None  # no raw exception/response is persisted or returned
        self.journal.entry_receipt(client_id, "acknowledged" if order_id else "unknown", order_id)
        return result("entry_acknowledged_query_required" if order_id else "entry_unknown_query_no_resubmit")

    async def refresh(self, client_id):
        if not self.armed():
            return result("disabled")
        row = self.journal.tracked(client_id)
        body = json.loads(row[0])["body"]
        try:
            response = await self.broker.lookup_entry(body["instId"], client_id)
            if (not isinstance(response, dict) or response.get("code") != "0"
                    or not isinstance(response.get("data"), list) or len(response["data"]) != 1):
                raise ValueError("Missing or ambiguous lookup")
            incoming = response["data"][0]
            order = parse_order(incoming)
            if not order.order_id.isdigit() or (row[3] is not None and order.order_id != row[3]):
                raise ValueError("Receipt and lookup order mismatch")
            received = timestamp(self.clock())
            self.journal.observe(client_id, incoming)
            self.journal.db.execute("UPDATE demo_execution SET observed_at_ms=?,"
                "exchange_order_id=COALESCE(exchange_order_id,?) WHERE id=?",
                (received, order.order_id, client_id))
        except Exception:
            self.journal.db.execute("UPDATE demo_execution SET observed_at_ms=NULL WHERE id=?", (client_id,))
            return result("lookup_failed_halt_no_resubmit")
        return result("entry_observed_" + order.state)

    async def cancel_remaining(self, client_id):
        if not self.armed():
            return result("disabled")
        row = self.journal.tracked(client_id)
        body = json.loads(row[0])["body"]
        if self.recovery_gate(deepcopy(body)) is not True:
            return result("recovery_gate_blocked")
        order = self.journal.reserve_cancel(client_id, now_ms=timestamp(self.clock()))
        try:
            if (self.recovery_gate(deepcopy(body)) is not True
                    or not self.armed()
                    or not 0 <= timestamp(self.clock()) - row[4] <= 5000):
                self.journal.cancel_receipt(client_id, "blocked_before_send")
                return result("cancel_blocked_after_reservation")
        except Exception:
            self.journal.cancel_receipt(client_id, "blocked_before_send")
            return result("cancel_blocked_after_reservation")
        try:
            response = await self.broker.cancel_entry(
                {"instId": body["instId"], "clOrdId": client_id}, deepcopy(HEADERS))
            accepted = receipt(response, client_id, order.order_id) is not None
        except Exception:
            accepted = False
        self.journal.cancel_receipt(client_id, "acknowledged" if accepted else "unknown")
        return result("cancel_acknowledged_query_required" if accepted else "cancel_unknown_query_no_retry")
