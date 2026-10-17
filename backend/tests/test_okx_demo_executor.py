"""Complete synthetic exchange-port rehearsals. No network or credentials."""
import asyncio
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from test_okx_strategy_bridge import args, READY
from test_okx_demo_intent import order
from test_okx_demo_protection import evidence
from okx_demo_executor import DemoExecutor, DemoExecutionJournal, HEADERS


class FakeExchange:
    environment = "demo"
    rehearsal_only = True

    def __init__(self):
        self.calls = []
        self.body = None
        self.row = None
        self.place_mode = "normal"
        self.cancel_mode = "normal"

    async def place_entry(self, body, headers):
        self.calls.append(("place", deepcopy(body), deepcopy(headers)))
        self.body = deepcopy(body)
        self.row = order({"body": body}, sz=body["sz"], instId=body["instId"], side=body["side"])
        if self.place_mode == "timeout": raise TimeoutError("sensitive error must not escape")
        if self.place_mode == "crash": raise asyncio.CancelledError()
        if self.place_mode == "reject": return {"code":"1", "data":[{"sCode":"51000", "sMsg":"private"}]}
        if self.place_mode == "wrong": return {"code":"0", "data":[{"sCode":"0", "clOrdId":"other", "ordId":"12345"}]}
        if self.place_mode == "malformed": return {"code":"0", "data":[]}
        return {"code":"0", "data":[dict(sCode="0", clOrdId=body["clOrdId"], ordId="12345")]}

    async def lookup_entry(self, symbol, client_id):
        self.calls.append(("lookup", symbol, client_id))
        return {"code":"0", "data":[] if self.row is None else [deepcopy(self.row)]}

    async def cancel_entry(self, body, headers):
        self.calls.append(("cancel", deepcopy(body), deepcopy(headers)))
        if self.cancel_mode == "timeout": raise TimeoutError("private")
        if self.cancel_mode == "fill_race":
            self.row.update(state="filled", accFillSz=self.row["sz"])
        return {"code":"0", "data":[dict(sCode="0", clOrdId=body["clOrdId"], ordId="12345")]}


class DemoExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "executor.sqlite"
        self.journal = DemoExecutionJournal(self.path)
        self.addCleanup(self.journal.close)
        self.broker = FakeExchange()
        self.now = READY+1000
        self.source = args()
        del self.source["now_ms"]
        self.engine = DemoExecutor(self.journal, self.broker, clock=lambda:self.now,
            entry_gate=lambda plan:True, recovery_gate=lambda body:True, enabled=True)

    def key(self):
        return self.broker.body["clOrdId"]

    async def partial(self):
        await self.engine.enter(self.source)
        self.broker.row.update(state="partially_filled", accFillSz="0.4")
        await self.engine.refresh(self.key())

    async def test_disabled_default_has_no_broker_calls_or_intent(self):
        self.engine.enabled = False
        self.assertEqual((await self.engine.enter(self.source))["phase"], "disabled")
        self.assertEqual(self.broker.calls, [])
        self.assertIsNone(self.journal.db.execute("SELECT 1 FROM demo_intent").fetchone())

    async def test_live_or_non_rehearsal_broker_never_called(self):
        for attribute, value in (("environment", "live"), ("rehearsal_only", False)):
            setattr(self.broker, attribute, value)
            self.assertEqual((await self.engine.enter(self.source))["phase"], "disabled")
            setattr(self.broker, attribute, "demo" if attribute == "environment" else True)
        self.assertEqual(self.broker.calls, [])

    async def test_gate_denial_before_and_after_reservation(self):
        self.engine.entry_gate = lambda p:False
        self.assertEqual((await self.engine.enter(self.source))["phase"], "entry_gate_blocked")
        calls = iter([True, False])
        self.engine.entry_gate = lambda p:next(calls)
        self.assertEqual((await self.engine.enter(self.source))["phase"], "entry_blocked_after_reservation")
        self.assertEqual(self.broker.calls, [])
        self.assertEqual(self.journal.db.execute("SELECT dispatched FROM demo_intent").fetchone()[0], 1)

    async def test_slow_gate_cannot_send_expired_plan(self):
        count = 0
        def gate(p):
            nonlocal count
            count += 1
            if count == 2: self.now += 6000
            return True
        self.engine.entry_gate = gate
        self.assertEqual((await self.engine.enter(self.source))["phase"], "entry_blocked_after_reservation")
        self.assertEqual(self.broker.calls, [])

    async def test_gate_cannot_mutate_order(self):
        def gate(p):
            p["plan"]["body"]["sz"] = "999999"
            return True
        self.engine.entry_gate = gate
        await self.engine.enter(self.source)
        self.assertNotEqual(self.broker.body["sz"], "999999")
        self.assertEqual(self.broker.calls[0][2], HEADERS)

    async def test_acknowledgement_is_not_fill_and_no_duplicate_entry(self):
        r = await self.engine.enter(self.source)
        self.assertEqual(r["phase"], "entry_acknowledged_query_required")
        self.assertFalse(r["exchange_submission_enabled"])
        self.assertIsNone(self.journal.tracked(self.key())[1])
        with self.assertRaises(ValueError): await self.engine.enter(self.source)
        self.assertEqual(sum(c[0]=="place" for c in self.broker.calls), 1)

    async def test_timeout_lookup_after_reopen_finds_accepted_order_without_resend(self):
        self.broker.place_mode = "timeout"
        self.assertEqual((await self.engine.enter(self.source))["phase"], "entry_unknown_query_no_resubmit")
        reopened = DemoExecutionJournal(self.path)
        try:
            self.engine.journal = reopened
            self.assertEqual((await self.engine.refresh(self.key()))["phase"], "entry_observed_live")
            with self.assertRaises(ValueError): await self.engine.enter(self.source)
            self.assertEqual(sum(c[0]=="place" for c in self.broker.calls), 1)
        finally: reopened.close()

    async def test_crash_keeps_reservation_and_does_not_unlock(self):
        self.broker.place_mode = "crash"
        with self.assertRaises(asyncio.CancelledError): await self.engine.enter(self.source)
        self.assertEqual(self.journal.tracked(self.key())[2], "reserved")
        with self.assertRaises(ValueError): await self.engine.enter(self.source)
        await self.engine.refresh(self.key())
        self.assertFalse(self.journal.status(self.key())["new_entry_allowed"])

    async def test_rejection_and_malformed_receipts_are_not_success(self):
        # Separate independent ledgers: each failed attempt stays blocked.
        for mode in ("reject", "wrong", "malformed"):
            path = Path(self.temp.name) / (mode+".sqlite")
            journal = DemoExecutionJournal(path)
            broker = FakeExchange(); broker.place_mode = mode
            engine = DemoExecutor(journal, broker, clock=lambda:self.now,
                entry_gate=lambda p:True, recovery_gate=lambda p:True, enabled=True)
            try:
                self.assertEqual((await engine.enter(self.source))["phase"], "entry_unknown_query_no_resubmit")
                with self.assertRaises(ValueError): await engine.enter(self.source)
            finally: journal.close()

    async def test_unknown_or_wrong_lookup_is_not_adopted(self):
        await self.engine.enter(self.source)
        original = deepcopy(self.broker.row)
        for change in (dict(ordId="999"), dict(clOrdId="foreign"), dict(sz="999"), dict(side="sell")):
            self.broker.row = original | change
            self.assertEqual((await self.engine.refresh(self.key()))["phase"], "lookup_failed_halt_no_resubmit")
        self.broker.row = None
        self.assertEqual((await self.engine.refresh(self.key()))["phase"], "lookup_failed_halt_no_resubmit")

    async def test_cancel_ack_requires_followup_and_preserves_partial_fill(self):
        await self.partial()
        self.assertEqual((await self.engine.cancel_remaining(self.key()))["phase"], "cancel_acknowledged_query_required")
        self.assertEqual(json_row(self.journal.tracked(self.key())[1])["state"], "partially_filled")
        self.broker.row.update(state="canceled")
        self.assertEqual((await self.engine.refresh(self.key()))["phase"], "entry_observed_canceled")
        self.assertIn("verify_position_and_native_protection", self.journal.status(self.key())["next_action"])
        with self.assertRaises(ValueError): await self.engine.cancel_remaining(self.key())
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 1)

    async def test_cancel_fill_race_records_full_fill_without_second_cancel(self):
        await self.partial()
        self.broker.cancel_mode = "fill_race"
        await self.engine.cancel_remaining(self.key())
        self.assertEqual((await self.engine.refresh(self.key()))["phase"], "entry_observed_filled")
        with self.assertRaises(ValueError): await self.engine.cancel_remaining(self.key())
        self.assertFalse(self.journal.status(self.key())["new_entry_allowed"])

    async def test_cancel_timeout_is_not_retried_after_reopen(self):
        await self.partial()
        self.broker.cancel_mode = "timeout"
        self.assertEqual((await self.engine.cancel_remaining(self.key()))["phase"], "cancel_unknown_query_no_retry")
        reopened = DemoExecutionJournal(self.path)
        try:
            self.engine.journal = reopened
            await self.engine.refresh(self.key())
            with self.assertRaises(ValueError): await self.engine.cancel_remaining(self.key())
        finally: reopened.close()
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 1)

    async def test_stale_observation_or_lost_recovery_gate_blocks_cancel(self):
        await self.partial()
        self.engine.recovery_gate = lambda b:False
        self.assertEqual((await self.engine.cancel_remaining(self.key()))["phase"], "recovery_gate_blocked")
        self.engine.recovery_gate = lambda b:True
        self.now += 5001
        with self.assertRaises(ValueError): await self.engine.cancel_remaining(self.key())
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 0)

    async def test_slow_recovery_gate_cannot_cancel_using_stale_observation(self):
        await self.partial()
        count = 0
        def gate(b):
            nonlocal count
            count += 1
            if count == 2: self.now += 5001
            return True
        self.engine.recovery_gate = gate
        self.assertEqual((await self.engine.cancel_remaining(self.key()))["phase"], "cancel_blocked_after_reservation")
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 0)

    async def test_disk_failure_prevents_broker_entry(self):
        self.journal.reserve_entry = lambda key:(_ for _ in ()).throw(OSError("disk failure"))
        with self.assertRaises(OSError): await self.engine.enter(self.source)
        self.assertEqual(self.broker.calls, [])

    async def test_disable_during_last_gate_prevents_entry(self):
        count = 0
        def entry_gate(p):
            nonlocal count
            count += 1
            if count == 2: self.engine.enabled = False
            return True
        self.engine.entry_gate = entry_gate
        self.assertEqual((await self.engine.enter(self.source))["phase"], "entry_blocked_after_reservation")
        self.assertEqual(self.broker.calls, [])

    async def test_disable_during_last_recovery_gate_prevents_cancel(self):
        await self.partial()
        count = 0
        def gate(b):
            nonlocal count
            count += 1
            if count == 2: self.engine.enabled = False
            return True
        self.engine.recovery_gate = gate
        self.assertEqual((await self.engine.cancel_remaining(self.key()))["phase"], "cancel_blocked_after_reservation")
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 0)

    async def test_failed_lookup_invalidates_prior_cancel_freshness(self):
        await self.partial()
        self.broker.row["ordId"] = "999"
        self.assertEqual((await self.engine.refresh(self.key()))["phase"], "lookup_failed_halt_no_resubmit")
        with self.assertRaises(ValueError): await self.engine.cancel_remaining(self.key())
        self.assertEqual(sum(c[0]=="cancel" for c in self.broker.calls), 0)

    async def test_second_journal_cannot_reserve_cancel_twice(self):
        await self.partial()
        self.journal.reserve_cancel(self.key(), now_ms=self.now)
        second = DemoExecutionJournal(self.path)
        try:
            with self.assertRaises(ValueError): second.reserve_cancel(self.key(), now_ms=self.now)
        finally: second.close()

    async def test_partial_fill_to_verified_protection_uses_persisted_actual_size(self):
        await self.partial()
        row = self.journal.tracked(self.key())
        p = json_row(row[0])
        e = evidence(p, fill="0.4", state="partially_filled")
        e["entry"]["rows"] = [deepcopy(self.broker.row)]
        e["pending_orders"]["rows"] = [deepcopy(self.broker.row)]
        for name in ("entry", "positions", "pending_orders", "pending_algos"):
            e[name]["received_at_ms"] = self.now
        # The helper fixture's sz=1 is not reused; protection is for the actual .4 contracts.
        r = self.journal.protection_report(self.key(), e, now_ms=self.now)
        self.assertTrue(r["protection_verified"])
        self.assertIn("cancel_remainder_then_reconcile", r["actions"])
        self.assertFalse(r["new_entry_allowed"])


def json_row(value):
    import json
    return json.loads(value)


if __name__ == "__main__": unittest.main()
