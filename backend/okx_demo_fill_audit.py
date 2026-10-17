"""Durable owned-entry fill audit since an explicit flat baseline.

No exchange reads/writes. This is not complete account history, funding/P&L
reconciliation, exit-child adoption or authorization to trade.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import re
from okx_demo_authority import snapshot_digest
from okx_orders import ACTIVE, parse_order
from okx_reconcile import number
from okx_risk import SYMBOLS, positive
from okx_strategy_bridge import timestamp

MAX_AUDIT_GAP_MS = 60000


class DemoFillAuditError(RuntimeError):
    pass


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,39}", value):
        raise ValueError("Invalid fill identity")
    return value


def money(value, *, positive_only=False):
    if not isinstance(value, str) or len(value) > 96 or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
        raise ValueError("Invalid fill number")
    result = Decimal(value)
    if positive_only and result <= 0:
        raise ValueError("Nonpositive fill number")
    # Decimal.normalize can round under the current context; exact fixed-point
    # formatting and stripping only fractional zeroes preserves every digit.
    text = format(result, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if result == 0 else text


def normalize_fill(raw, now):
    if not isinstance(raw, dict) or raw.get("instId") not in SYMBOLS or raw.get("side") not in {"buy", "sell"}:
        raise ValueError("Unsupported fill")
    if raw.get("feeCcy") != "USDT" or raw.get("posSide") not in (None, "net"):
        raise ValueError("Unsupported fee or position side")
    at = int(ident(raw.get("fillTime")))
    timestamp(at)
    if at > now:
        raise ValueError("Future fill")
    fill = {k:ident(raw.get(k)) for k in ("billId", "tradeId", "ordId")}
    fill.update(instId=raw["instId"], side=raw["side"], fillTime=str(at),
        fillSz=money(raw.get("fillSz"), positive_only=True), fillPx=money(raw.get("fillPx"), positive_only=True),
        fee=money(raw.get("fee")), feeCcy="USDT", fillPnl=money("0" if raw.get("fillPnl") in (None, "") else raw["fillPnl"]))
    return fill


def key(fill):
    return fill["instId"] + ":" + fill["tradeId"]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DemoFillAuditor:
    def __init__(self, authority):
        self.authority, self.db = authority, authority.db
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_fill_audit_meta ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),scope TEXT NOT NULL,baseline_at INTEGER NOT NULL,"
            "last_checked INTEGER NOT NULL,anchor TEXT,revision INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_audit_fills ("
            "identity TEXT PRIMARY KEY,bill_id TEXT UNIQUE NOT NULL,order_id TEXT NOT NULL,"
            "symbol TEXT NOT NULL,quantity TEXT NOT NULL,fee TEXT NOT NULL,body TEXT NOT NULL,baseline INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_audit_receipt ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),scope TEXT NOT NULL,epoch INTEGER NOT NULL,"
            "session TEXT NOT NULL,checked_at INTEGER NOT NULL,snapshot_hash TEXT NOT NULL,valid INTEGER NOT NULL)")
        meta = self.db.execute("SELECT scope FROM demo_fill_audit_meta WHERE singleton=1").fetchone()
        if meta and meta[0] != authority.scope:
            raise ValueError("Audit belongs to another account scope")

    def _begin_attempt(self):
        now = self.authority._now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.authority._owner(now)
            self.db.execute("UPDATE demo_audit_receipt SET valid=0 WHERE singleton=1")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.authority.invalidate_snapshot()
        return now

    def _snapshot(self, snapshot, now):
        if not isinstance(snapshot, dict) or snapshot.get("fill_audit_ready") is not False or snapshot.get("fill_history_complete") is not False:
            raise ValueError("Fresh unaudited reader snapshot required")
        # Structural validation only. This temporary copy is NEVER published;
        # readiness comes only from the committed receipt below.
        structural = deepcopy(snapshot)
        structural["fill_audit_ready"] = True
        self.authority._validate_snapshot(structural, now)
        end = timestamp(snapshot["scan_finished_at_ms"])
        if not snapshot["received_at_ms"] <= end <= now:
            raise ValueError("Invalid scan chronology")
        rows = snapshot.get("recent_fills")
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("Bounded recent-fill window required")
        fills, bills = {}, set()
        for raw in rows:
            fill = normalize_fill(raw, now)
            if key(fill) in fills or fill["billId"] in bills:
                raise ValueError("Repeated fill identity")
            fills[key(fill)] = fill
            bills.add(fill["billId"])
        return fills

    def _put(self, fill, *, baseline):
        self.db.execute("INSERT INTO demo_audit_fills VALUES(?,?,?,?,?,?,?,?)", (
            key(fill), fill["billId"], fill["ordId"], fill["instId"], fill["fillSz"], fill["fee"], encoded(fill), int(baseline)))

    def _certify(self, snapshot, now, revision):
        verified = deepcopy(snapshot)
        verified.update(fill_audit_ready=True, audit_scope="owned_entries_since_flat_baseline", audit_revision=revision)
        self.db.execute("INSERT OR REPLACE INTO demo_audit_receipt VALUES(1,?,?,?,?,?,1)", (
            self.authority.scope, self.authority.epoch, self.authority.session, now, snapshot_digest(verified)))
        self.db.execute("UPDATE demo_authority SET last_clock=? WHERE singleton=1", (now,))
        return verified

    def bootstrap(self, snapshot, *, baseline_authorized=False):
        try:
            if baseline_authorized is not True:
                raise ValueError("Explicit baseline authorization required")
            now = self._begin_attempt()
            fills = self._snapshot(snapshot, now)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                current = self.authority._now()
                self.authority._owner(current)
                self._snapshot(snapshot, current)
                if self.db.execute("SELECT 1 FROM demo_fill_audit_meta").fetchone() or self.db.execute("SELECT 1 FROM demo_intent").fetchone():
                    raise ValueError("Existing audit/intent cannot be reset or re-baselined")
                if (snapshot["pending_orders"] or snapshot["pending_algos"]
                        or any(number(p.get("pos"), allow_negative=True) != 0 for p in snapshot["positions"])):
                    raise ValueError("A flat account without pending orders is required")
                for fill in fills.values():
                    if int(fill["fillTime"]) > snapshot["received_at_ms"]:
                        raise ValueError("Fill arrived across the baseline boundary")
                    self._put(fill, baseline=True)
                anchor = max(fills, key=lambda k:int(fills[k]["billId"])) if fills else None
                self.db.execute("INSERT INTO demo_fill_audit_meta VALUES(1,?,?,?,?,1)", (
                    self.authority.scope, snapshot["received_at_ms"], now, anchor))
                verified = self._certify(snapshot, now, 1)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return verified
        except Exception:
            raise DemoFillAuditError("Fill audit baseline not established; no state reset") from None

    def _owned_orders(self, snapshot, now):
        owned, expected_pending = {}, {}
        rows = self.db.execute("SELECT i.id,i.plan,i.snapshot,e.entry_status,e.exchange_order_id,e.observed_at_ms "
            "FROM demo_intent i LEFT JOIN demo_execution e ON i.id=e.id WHERE i.dispatched=1").fetchall()
        for client_id, plan_json, observed, status, order_id, observed_at in rows:
            if status == "blocked_before_send" and order_id is None and observed is None:
                continue
            if observed is None or order_id is None or type(observed_at) is not int or not snapshot["received_at_ms"] <= observed_at <= now:
                raise ValueError("Unresolved or stale owned order")
            raw = json.loads(observed)
            order = parse_order(raw)
            body = json.loads(plan_json)["body"]
            if (order.order_id != order_id or order.client_order_id != client_id or order.instrument != body["instId"]
                    or order.requested != positive(body["sz"], "requested size")
                    or any(raw.get(k) != body[k] for k in ("side", "posSide", "tdMode", "ordType"))):
                raise ValueError("Owned order identity mismatch")
            if order_id in owned:
                raise ValueError("Duplicate owned exchange order")
            owned[order_id] = (order, body["side"])
            if order.state in ACTIVE:
                expected_pending[order_id] = order
        incoming = {}
        for raw in snapshot["pending_orders"]:
            order = parse_order(raw)
            if order.order_id in incoming or order.order_id not in expected_pending or order != expected_pending[order.order_id]:
                raise ValueError("Unknown or changed pending order")
            if raw.get("side") != owned[order.order_id][1] or raw.get("posSide") != "net" or raw.get("tdMode") != "isolated":
                raise ValueError("Pending order direction mismatch")
            incoming[order.order_id] = order
        if incoming != expected_pending:
            raise ValueError("Pending inventory is not complete")
        return owned

    def audit(self, snapshot):
        try:
            now = self._begin_attempt()
            fills = self._snapshot(snapshot, now)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                current = self.authority._now()
                self.authority._owner(current)
                self._snapshot(snapshot, current)
                meta = self.db.execute("SELECT scope,baseline_at,last_checked,anchor,revision FROM demo_fill_audit_meta WHERE singleton=1").fetchone()
                if not meta or meta[0] != self.authority.scope or not 0 <= now - meta[2] <= MAX_AUDIT_GAP_MS:
                    raise ValueError("Baseline missing or continuity gap exceeds bound")
                if meta[3] is not None and meta[3] not in fills:
                    raise ValueError("Checkpoint is absent from recent window")
                owned = self._owned_orders(snapshot, now)
                for identity, fill in fills.items():
                    old = self.db.execute("SELECT body FROM demo_audit_fills WHERE identity=?", (identity,)).fetchone()
                    if old:
                        if old[0] != encoded(fill):
                            raise ValueError("Previously recorded fill changed")
                        continue
                    parent = owned.get(fill["ordId"])
                    if (not parent or parent[0].instrument != fill["instId"] or parent[1] != fill["side"]
                            or int(fill["fillTime"]) < meta[1] or Decimal(fill["fillPnl"]) != 0):
                        raise ValueError("Unknown, historical or closing fill; do not adopt")
                    self._put(fill, baseline=False)
                # Match every owned cumulative fill quantity and signed position.
                with localcontext() as context:
                    context.prec = 256
                    expected = {}
                    for order_id, (order, side) in owned.items():
                        stored = self.db.execute("SELECT symbol,quantity FROM demo_audit_fills WHERE order_id=? AND baseline=0", (order_id,)).fetchall()
                        if any(symbol != order.instrument for symbol, _ in stored):
                            raise ValueError("Owned fill instrument changed")
                        total = sum((Decimal(qty) for _, qty in stored), Decimal(0))
                        if total != order.filled:
                            raise ValueError("Missing or excess fills versus cumulative order")
                        expected[order.instrument] = expected.get(order.instrument, Decimal(0)) + (total if side == "buy" else -total)
                    actual = {}
                    for row in snapshot["positions"]:
                        size = number(row.get("pos"), allow_negative=True)
                        if not size:
                            continue
                        symbol = row.get("instId")
                        if symbol not in SYMBOLS or symbol in actual or row.get("posSide") != "net" or row.get("mgnMode") != "isolated":
                            raise ValueError("Unknown account exposure")
                        actual[symbol] = size
                    if actual != {symbol:qty for symbol, qty in expected.items() if qty != 0}:
                        raise ValueError("Position differs from owned fill ledger")
                anchor = max(fills, key=lambda k:int(fills[k]["billId"])) if fills else None
                revision = meta[4]+1
                self.db.execute("UPDATE demo_fill_audit_meta SET last_checked=?,anchor=?,revision=? WHERE singleton=1", (now, anchor, revision))
                verified = self._certify(snapshot, now, revision)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return verified
        except Exception:
            raise DemoFillAuditError("Owned fill audit incomplete; entry remains blocked") from None
