"""Persistent local rehearsal authority. No exchange calls or real execution.

Serializes workers sharing ONE SQLite ledger. This is not a cloud-wide lease.
Snapshots are supplied by a trusted-reader port, not authenticated here.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import re
import uuid

from okx_demo_intent import demo_plan
from okx_instruments import instrument_spec
from okx_reconcile import number
from okx_risk import positive
from okx_strategy_bridge import timestamp


def day_key(now):
    return datetime.fromtimestamp(now / 1000, timezone.utc).astimezone(timezone(timedelta(hours=8))).date().isoformat()


class DemoEntryAuthority:
    def __init__(self, journal, *, account_scope, worker_id, clock, session_id=None):
        for value in (account_scope, worker_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
                raise ValueError("Stable account and worker identity required")
        if getattr(journal, "entry_reservation_guard", None) is not None:
            raise ValueError("Journal already has an entry authority")
        self.db, self.journal, self.clock = journal.db, journal, clock
        self.scope, self.worker = account_scope, worker_id
        self.session = session_id or uuid.uuid4().hex
        self.epoch = None
        self.acquired_at = None
        self.snapshot = None
        self.approved = None
        self.seen_clock = 0
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_authority ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),scope TEXT NOT NULL,"
            "worker TEXT,session TEXT,epoch INTEGER NOT NULL,expires INTEGER NOT NULL,"
            "last_clock INTEGER NOT NULL,kill INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO demo_authority VALUES(1,?,NULL,NULL,0,0,0,1)", (self.scope,))
        if self.db.execute("SELECT scope FROM demo_authority WHERE singleton=1").fetchone()[0] != self.scope:
            raise ValueError("Ledger belongs to another account scope")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_daily ("
            "day TEXT PRIMARY KEY,start_equity TEXT NOT NULL,entries INTEGER NOT NULL,"
            "loss_streak INTEGER NOT NULL,halt INTEGER NOT NULL,cooldown INTEGER NOT NULL,last_close INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_risk_entry ("
            "id TEXT PRIMARY KEY,day TEXT NOT NULL,epoch INTEGER NOT NULL,session TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS demo_risk_close ("
            "id TEXT PRIMARY KEY,pnl TEXT NOT NULL,closed_at INTEGER NOT NULL)")
        journal.entry_reservation_guard = self._consume_entry

    def _now(self):
        now = timestamp(self.clock())
        if now < self.seen_clock:
            raise ValueError("Clock rollback")
        self.seen_clock = now
        return now

    def acquire(self):
        now = self._now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT worker,session,epoch,expires,last_clock FROM demo_authority WHERE singleton=1").fetchone()
            if now < row[4]:
                raise ValueError("Persisted clock rollback")
            same = row[0] == self.worker and row[1] == self.session
            if row[3] > now and not same:
                self.db.execute("ROLLBACK")
                return False
            epoch = row[2] if same and row[3] > now else row[2] + 1
            if epoch != row[2]:
                self.db.execute("UPDATE demo_authority SET kill=1 WHERE singleton=1")
            self.db.execute("UPDATE demo_authority SET worker=?,session=?,epoch=?,expires=?,last_clock=? WHERE singleton=1",
                (self.worker, self.session, epoch, now+30000, now))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        if self.epoch != epoch:
            self.acquired_at = now
            self.snapshot = None
            self.approved = None
        self.epoch = epoch
        return True

    def _owner(self, now):
        row = self.db.execute("SELECT worker,session,epoch,expires,last_clock,kill FROM demo_authority WHERE singleton=1").fetchone()
        if (row[0] != self.worker or row[1] != self.session or row[2] != self.epoch
                or row[3] <= now or now < row[4]):
            raise ValueError("Current execution lease required")
        return row

    def set_kill_switch(self, active):
        if type(active) is not bool:
            raise ValueError("Boolean kill switch required")
        now = self._now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._owner(now)
            self.db.execute("UPDATE demo_authority SET kill=?,last_clock=? WHERE singleton=1", (int(active), now))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _validate_snapshot(self, snapshot, now):
        if not isinstance(snapshot, dict) or snapshot.get("account_scope") != self.scope:
            raise ValueError("Account scope mismatch")
        if not all(snapshot.get(k) is True for k in ("demo_verified", "complete", "fill_audit_ready", "monitor_healthy", "order_permission_verified")):
            raise ValueError("Complete verified snapshot required")
        received = timestamp(snapshot.get("received_at_ms"))
        if self.acquired_at is None or received < self.acquired_at or not 0 <= now - received <= 5000:
            raise ValueError("Stale account snapshot")
        if snapshot.get("position_mode") != "net_mode":
            raise ValueError("Net account mode required")
        if type(snapshot.get("isolated_leverage")) is not int or not 1 <= snapshot["isolated_leverage"] <= 3:
            raise ValueError("Verified isolated leverage required")
        positive(snapshot.get("equity_usdt"), "equity")
        if positive(snapshot.get("available_usdt"), "available") > positive(snapshot["equity_usdt"], "equity"):
            raise ValueError("Inconsistent available equity")
        for key in ("positions", "pending_orders", "pending_algos"):
            if not isinstance(snapshot.get(key), list) or len(snapshot[key]) > 500:
                raise ValueError("Complete bounded inventory required")
            if any(not isinstance(row, dict) for row in snapshot[key]):
                raise ValueError("Malformed inventory")
        spec = instrument_spec(snapshot["instrument"])
        if spec["symbol"] != snapshot.get("symbol"):
            raise ValueError("Instrument snapshot mismatch")
        return spec

    def accept_snapshot(self, snapshot):
        self.snapshot = None
        self.approved = None
        now = self._now()
        self._validate_snapshot(snapshot, now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._owner(now)
            day = day_key(now)
            equity = positive(snapshot["equity_usdt"], "equity")
            previous = self.db.execute("SELECT cooldown,last_close FROM demo_daily ORDER BY day DESC LIMIT 1").fetchone()
            self.db.execute("INSERT OR IGNORE INTO demo_daily VALUES(?,?,0,0,0,?,?)",
                (day, str(equity), previous[0] if previous else 0, previous[1] if previous else 0))
            start = positive(self.db.execute("SELECT start_equity FROM demo_daily WHERE day=?", (day,)).fetchone()[0], "day start")
            if equity <= start * Decimal("0.98"):
                self.db.execute("UPDATE demo_daily SET halt=1 WHERE day=?", (day,))
            self.db.execute("UPDATE demo_authority SET last_clock=? WHERE singleton=1", (now,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.snapshot = deepcopy(snapshot)
        self.approved = None

    def _assess(self, candidate, now):
        owner = self._owner(now)
        if owner[5]:
            raise ValueError("Kill switch active")
        spec = self._validate_snapshot(self.snapshot, now)
        snapshot = self.snapshot
        if snapshot["pending_orders"] or snapshot["pending_algos"]:
            raise ValueError("Unresolved orders or protection")
        if any(number(p.get("pos"), allow_negative=True) != 0 for p in snapshot["positions"]):
            raise ValueError("Account is not flat")
        plan = candidate["plan"]
        body = plan["body"]
        if (candidate.get("account_scope") != self.scope or candidate.get("strategy") != "SNR-5M-1"
                or candidate["symbol"] != snapshot["symbol"] or body["instId"] != snapshot["symbol"] or plan.get("environment") != "demo"
                or plan.get("submission_enabled") is not False or candidate.get("submission_enabled") is not False
                or candidate["expires_at_ms"] < now
                or Decimal(plan["preconditions"]["verified_leverage"]) != snapshot["isolated_leverage"]):
            raise ValueError("Candidate does not match current account")
        equity = positive(snapshot["equity_usdt"], "equity")
        sizing = candidate["sizing"]
        with localcontext() as context:
            context.prec = 60
            qty = positive(body["sz"], "contracts")
            anchor = positive(sizing["entry_anchor"], "entry anchor")
            side = 1 if body["side"] == "buy" else -1
            entry = anchor * (1 + side * Decimal("0.0002"))
            bracket = body["attachAlgoOrds"][0]
            recalculated = demo_plan(account_scope=self.scope, signal_id="SNR5M1:" + candidate["signal_id"],
                instrument=snapshot["instrument"], side=body["side"], contracts=qty,
                price=anchor, stop=bracket["slTriggerPx"], target=bracket["tpTriggerPx"],
                equity_usdt=equity, leverage=snapshot["isolated_leverage"], demo=True)
            margin = qty * spec["ct_val"] * max(anchor, entry) / snapshot["isolated_leverage"]
            fee = qty * spec["ct_val"] * entry * Decimal("0.0005")
            if (recalculated != plan or qty * spec["ct_val"] != positive(sizing["base_quantity"], "base quantity")
                    or margin != positive(sizing["modeled_margin_usdt"], "margin")
                    or fee != positive(sizing["modeled_entry_fee_usdt"], "fee")
                    or positive(sizing["modeled_loss_usdt"], "loss") != positive(plan["modeled_loss_usdt"], "plan loss")
                    or margin > equity * Decimal("0.10") or margin + fee > positive(snapshot["available_usdt"], "available")
                    or qty > positive(snapshot["instrument"].get("maxMktSz"), "market maximum")
                    or qty > positive(snapshot["instrument"].get("maxStopSz"), "stop maximum")):
                raise ValueError("Candidate differs from current verified account/specification")
        daily = self.db.execute("SELECT entries,loss_streak,halt,cooldown FROM demo_daily WHERE day=?", (day_key(now),)).fetchone()
        if (daily is None or any(type(v) is not int or v < 0 for v in daily)
                or daily[1] >= 3 or daily[2] or now < daily[3]):
            raise ValueError("Daily limit or cooldown")
        reserved = self.db.execute("SELECT day,epoch,session FROM demo_risk_entry WHERE id=?", (body["clOrdId"],)).fetchone()
        same = reserved == (day_key(now), self.epoch, self.session)
        if reserved is not None and not same:
            raise ValueError("Reservation belongs to an older execution session")
        if daily[0] >= (7 if same else 6):
            raise ValueError("Daily entry limit")

    def allows(self, candidate):
        try:
            self._assess(candidate, self._now())
            self.approved = deepcopy(candidate)
            return True
        except (ValueError, TypeError, KeyError, ArithmeticError, IndexError):
            self.approved = None
            return False

    def _consume_entry(self, client_id):
        # Called INSIDE DemoExecutionJournal's BEGIN IMMEDIATE transaction.
        if not self.db.in_transaction or self.approved is None:
            raise ValueError("Entry authority not checked in this session")
        now = self._now()
        candidate = self.approved
        self._assess(candidate, now)
        stored = self.db.execute("SELECT plan FROM demo_intent WHERE id=?", (client_id,)).fetchone()
        if (candidate["plan"]["body"]["clOrdId"] != client_id or not stored
                or json.loads(stored[0]) != candidate["plan"]):
            raise ValueError("Reserved plan differs from approved candidate")
        self.db.execute("INSERT INTO demo_risk_entry VALUES(?,?,?,?)", (client_id, day_key(now), self.epoch, self.session))
        self.db.execute("UPDATE demo_daily SET entries=entries+1 WHERE day=?", (day_key(now),))
        self.db.execute("UPDATE demo_authority SET last_clock=? WHERE singleton=1", (now,))

    def record_verified_close(self, close_id, *, pnl_usdt, closed_at_ms, audit_verified=False):
        if audit_verified is not True or not isinstance(close_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", close_id):
            raise ValueError("Audited close identity required")
        now = self._now()
        closed = timestamp(closed_at_ms)
        if closed > now or day_key(closed) != day_key(now):
            raise ValueError("Current-day completed close required")
        pnl = number(pnl_usdt, allow_negative=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._owner(now)
            old = self.db.execute("SELECT pnl,closed_at FROM demo_risk_close WHERE id=?", (close_id,)).fetchone()
            if old is not None:
                if Decimal(old[0]) != pnl or old[1] != closed:
                    raise ValueError("Close identity reused with changed values")
            else:
                daily = self.db.execute("SELECT loss_streak,cooldown,last_close FROM demo_daily WHERE day=?", (day_key(now),)).fetchone()
                if daily is None or closed < daily[2]:
                    raise ValueError("Close chronology or daily baseline missing")
                streak = daily[0] + 1 if pnl < 0 else 0
                self.db.execute("INSERT INTO demo_risk_close VALUES(?,?,?)", (close_id, str(pnl), closed))
                self.db.execute("UPDATE demo_daily SET loss_streak=?,halt=MAX(halt,?),cooldown=?,last_close=? WHERE day=?",
                    (streak, int(streak >= 3), max(daily[1], closed+900000), closed, day_key(now)))
            self.db.execute("UPDATE demo_authority SET last_clock=? WHERE singleton=1", (now,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.approved = None

    def recovery_allowed(self, _body=None):
        # Kill switch/daily halt must not block reduction of existing exposure.
        try:
            now = self._now()
            self._owner(now)
            self._validate_snapshot(self.snapshot, now)
            return True
        except (ValueError, TypeError, KeyError, ArithmeticError, IndexError):
            return False
