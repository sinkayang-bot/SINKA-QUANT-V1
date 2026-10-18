"""Local-ledger lease and risk integration, with synthetic trusted snapshots."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import tempfile
from pathlib import Path
import unittest

from test_okx_demo_executor import FakeExchange
from test_okx_strategy_bridge import args, READY
from okx_demo_authority import DemoEntryAuthority, day_key
from okx_demo_executor import DemoExecutor, DemoExecutionJournal
from okx_strategy_bridge import plan_strategy_signal


class DemoAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.now = READY+1000
        self.journal = self.open_journal()
        self.authority = self.make_authority(self.journal)
        self.candidate = plan_strategy_signal(**args())

    def open_journal(self):
        journal = DemoExecutionJournal(self.path)
        self.addCleanup(journal.close)
        return journal

    def make_authority(self, journal, worker="worker1", session="session1"):
        return DemoEntryAuthority(journal, account_scope="synthetic-v1", worker_id=worker,
                                  session_id=session, clock=lambda:self.now)

    def snapshot(self, **updates):
        return dict(account_scope="synthetic-v1", demo_verified=True, complete=True,
            fill_audit_ready=True, monitor_healthy=True, order_permission_verified=True, received_at_ms=self.now,
            position_mode="net_mode", isolated_leverage=3, equity_usdt="10000", available_usdt="10000",
            positions=[], pending_orders=[], pending_algos=[], symbol="BTC-USDT-SWAP",
            instrument=args()["instrument"]) | updates

    def ready(self):
        self.assertTrue(self.authority.acquire())
        self.authority.accept_snapshot(self.snapshot())
        self.authority.set_kill_switch(False)
        self.assertTrue(self.authority.allows(self.candidate))

    def reserve(self):
        key = self.journal.register(self.candidate["plan"])
        self.journal.reserve_entry(key)
        return key

    def test_default_kill_and_missing_fresh_session_snapshot_block(self):
        self.assertFalse(self.authority.allows(self.candidate))
        self.authority.acquire()
        self.authority.set_kill_switch(False)
        self.assertFalse(self.authority.allows(self.candidate))
        self.authority.accept_snapshot(self.snapshot())
        self.assertTrue(self.authority.allows(self.candidate))
        self.authority.set_kill_switch(True)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertTrue(self.authority.recovery_allowed())

    def test_two_workers_and_same_name_different_session_cannot_share_live_lease(self):
        self.ready()
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        third = self.make_authority(self.open_journal(), worker="worker1", session="session3")
        self.assertFalse(second.acquire())
        self.assertFalse(third.acquire())
        self.assertFalse(second.allows(self.candidate))
        self.assertFalse(third.allows(self.candidate))

    def test_release_allows_immediate_handoff_without_clearing_intent_or_risk(self):
        self.ready(); self.reserve()
        intent=self.journal.db.execute('SELECT * FROM demo_intent').fetchall()
        risk=self.journal.db.execute('SELECT * FROM demo_risk_entry').fetchall()
        daily=self.journal.db.execute('SELECT * FROM demo_daily').fetchall()
        self.assertTrue(self.authority.release())
        self.assertIsNone(self.authority.snapshot)
        self.assertIsNone(self.authority.approved)
        self.assertEqual(self.journal.db.execute('SELECT kill,expires FROM demo_authority').fetchone(),(1,0))
        second=self.make_authority(self.open_journal(),worker='closer',session='close-session')
        self.assertTrue(second.acquire())
        self.assertEqual(self.journal.db.execute('SELECT * FROM demo_intent').fetchall(),intent)
        self.assertEqual(self.journal.db.execute('SELECT * FROM demo_risk_entry').fetchall(),risk)
        self.assertEqual(self.journal.db.execute('SELECT * FROM demo_daily').fetchall(),daily)

    def test_release_cannot_touch_a_newer_owner_or_an_unacquired_lease(self):
        self.ready()
        second=self.make_authority(self.open_journal(),worker='closer',session='close-session')
        before=self.journal.db.execute('SELECT * FROM demo_authority').fetchone()
        self.assertFalse(second.release())
        self.assertEqual(self.journal.db.execute('SELECT * FROM demo_authority').fetchone(),before)
        self.now+=30001
        self.assertTrue(second.acquire()); second.set_kill_switch(False)
        before=self.journal.db.execute('SELECT * FROM demo_authority').fetchone()
        self.assertFalse(self.authority.release())
        self.assertEqual(self.journal.db.execute('SELECT * FROM demo_authority').fetchone(),before)

    def test_expired_owner_fenced_after_takeover_and_new_owner_starts_killed(self):
        self.ready()
        self.now += 30001
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        self.assertTrue(second.acquire())
        self.assertGreater(second.epoch, self.authority.epoch)
        self.assertFalse(self.authority.recovery_allowed())
        with self.assertRaises(ValueError): self.authority.set_kill_switch(False)
        self.assertIsNone(second.snapshot)
        second.accept_snapshot(self.snapshot())
        self.assertTrue(second.recovery_allowed())
        self.assertEqual(second.db.execute("SELECT kill FROM demo_authority").fetchone()[0], 1)

    def test_renewal_preserves_epoch_and_rollback_is_rejected(self):
        self.ready()
        epoch = self.authority.epoch
        self.now += 1000
        self.assertTrue(self.authority.acquire())
        self.assertEqual(self.authority.epoch, epoch)
        self.now -= 1
        self.assertFalse(self.authority.allows(self.candidate))
        with self.assertRaises(ValueError): self.authority.acquire()

    def test_account_scope_cannot_change_for_the_same_ledger(self):
        with self.assertRaises(ValueError):
            DemoEntryAuthority(self.open_journal(), account_scope="different", worker_id="w", clock=lambda:self.now)

    def test_snapshot_flags_modes_staleness_and_malformed_rows_fail_closed(self):
        self.ready()
        for change in (dict(demo_verified=False), dict(complete="true"), dict(fill_audit_ready=False),
            dict(monitor_healthy=False), dict(account_scope="other"), dict(position_mode="long_short_mode"),
            dict(isolated_leverage=True), dict(isolated_leverage=4), dict(equity_usdt="NaN"),
            dict(available_usdt="10001"), dict(received_at_ms=self.now-5001),
            dict(received_at_ms=self.now+1), dict(positions=[None]), dict(pending_orders={})):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.authority.accept_snapshot(self.snapshot(**change))
                self.assertFalse(self.authority.allows(self.candidate))
                self.authority.accept_snapshot(self.snapshot())

    def test_active_positions_and_any_pending_normal_or_algo_order_block(self):
        self.ready()
        cases = (dict(positions=[dict(instId="ETH-USDT-SWAP", pos="1")]),
                 dict(pending_orders=[dict(ordId="123")]), dict(pending_algos=[dict(algoId="456")]))
        for change in cases:
            self.authority.accept_snapshot(self.snapshot(**change))
            self.assertFalse(self.authority.allows(self.candidate))
        self.authority.accept_snapshot(self.snapshot(positions=[dict(pos="0")]))
        self.assertTrue(self.authority.allows(self.candidate))

    def test_candidate_tampering_or_changed_metadata_rejected(self):
        self.ready()
        for target, key, value in (("root", "account_scope", "other"), ("root", "symbol", "ETH-USDT-SWAP"),
                                  ("sizing", "base_quantity", "999"), ("sizing", "modeled_margin_usdt", "0.1")):
            candidate = deepcopy(self.candidate)
            (candidate if target == "root" else candidate[target])[key] = value
            self.assertFalse(self.authority.allows(candidate))
        spec = args()["instrument"] | dict(ctVal="0.02")
        self.authority.accept_snapshot(self.snapshot(instrument=spec))
        self.assertFalse(self.authority.allows(self.candidate))
        self.authority.accept_snapshot(self.snapshot(instrument=args()["instrument"] | dict(maxMktSz="1")))
        self.assertFalse(self.authority.allows(self.candidate))

    def test_atomic_reservation_counts_attempt_once_and_post_gate_still_allows_sixth(self):
        self.ready()
        day = day_key(self.now)
        self.journal.db.execute("UPDATE demo_daily SET entries=5 WHERE day=?", (day,))
        self.assertTrue(self.authority.allows(self.candidate))
        key = self.reserve()
        self.assertEqual(self.journal.db.execute("SELECT entries FROM demo_daily WHERE day=?", (day,)).fetchone()[0], 6)
        self.assertTrue(self.authority.allows(self.candidate))  # same sixth reservation, not a seventh attempt
        with self.assertRaises(ValueError): self.journal.reserve_entry(key)
        a = args(); a["signal"].update(ts=READY, id=str(READY), readyAt=READY+300000)
        a.update(now_ms=READY+301000, started_at_ms=READY, balance_received_at_ms=READY+300000,
                 instrument_received_at_ms=READY+300000)
        a["quote"]["ts"] = READY+300100
        other = plan_strategy_signal(**a)
        self.now = READY+301000
        self.authority.acquire(); self.authority.accept_snapshot(self.snapshot())
        self.assertFalse(self.authority.allows(other))

    def test_snapshot_invalidated_between_allow_and_reserve_rolls_back_dispatch_and_risk_count(self):
        self.ready()
        key = self.journal.register(self.candidate["plan"])
        self.now += 5001
        with self.assertRaises(ValueError): self.journal.reserve_entry(key)
        self.assertEqual(self.journal.db.execute("SELECT dispatched FROM demo_intent").fetchone()[0], 0)
        self.assertEqual(self.journal.db.execute("SELECT entries FROM demo_daily").fetchone()[0], 0)
        self.assertIsNone(self.journal.db.execute("SELECT 1 FROM demo_execution").fetchone())

    def test_three_audited_net_losses_halt_persistently_and_replay_is_idempotent(self):
        self.ready()
        for i in range(3):
            self.now += 1
            self.authority.record_verified_close("fill:"+str(i), pnl_usdt="-1", closed_at_ms=self.now, audit_verified=True)
        self.assertFalse(self.authority.allows(self.candidate))
        self.authority.record_verified_close("fill:2", pnl_usdt="-1", closed_at_ms=self.now, audit_verified=True)
        self.assertEqual(self.journal.db.execute("SELECT loss_streak,halt FROM demo_daily").fetchone(), (3,1))
        with self.assertRaises(ValueError):
            self.authority.record_verified_close("fill:2", pnl_usdt="1", closed_at_ms=self.now, audit_verified=True)
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        self.now += 30001; second.acquire(); second.accept_snapshot(self.snapshot()); second.set_kill_switch(False)
        self.assertEqual(second.db.execute("SELECT loss_streak,halt FROM demo_daily").fetchone(), (3,1))

    def test_equity_loss_halt_cannot_be_cleared_by_rebound_or_kill_toggle(self):
        self.ready()
        self.authority.accept_snapshot(self.snapshot(equity_usdt="9800", available_usdt="9800"))
        self.authority.accept_snapshot(self.snapshot())
        self.authority.set_kill_switch(False)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertEqual(self.journal.db.execute("SELECT halt FROM demo_daily").fetchone()[0], 1)

    def test_close_requires_audit_and_cooldown_persists_across_day_boundary(self):
        self.ready()
        with self.assertRaises(ValueError): self.authority.record_verified_close("a", pnl_usdt="1", closed_at_ms=self.now)
        with self.assertRaises(ValueError):
            self.authority.record_verified_close("a", pnl_usdt="1", closed_at_ms=self.now+1, audit_verified=True)
        self.now = int(datetime(2026,10,17,15,59,59,tzinfo=timezone.utc).timestamp()*1000)
        self.authority.acquire(); self.authority.accept_snapshot(self.snapshot()); self.authority.set_kill_switch(False)
        self.authority.record_verified_close("last-day-close", pnl_usdt="-1", closed_at_ms=self.now, audit_verified=True)
        self.now += 2000
        self.authority.acquire(); self.authority.accept_snapshot(self.snapshot())
        rows = self.journal.db.execute("SELECT day,entries,loss_streak,halt,cooldown FROM demo_daily ORDER BY day DESC LIMIT 2").fetchall()
        self.assertEqual(rows[0][0], "2026-10-18")
        self.assertEqual(rows[1][0], "2026-10-17")
        self.assertEqual(rows[0][1:4], (0,0,0))
        self.assertGreater(rows[0][4], self.now)
        self.assertEqual(rows[0][4], rows[1][4])

    def test_reopened_journal_without_bound_authority_cannot_bypass_lease(self):
        self.ready()
        key = self.journal.register(self.candidate["plan"])
        unbound = self.open_journal()
        with self.assertRaisesRegex(ValueError, "bound execution authority"):
            unbound.reserve_entry(key)
        self.assertEqual(self.journal.db.execute("SELECT dispatched FROM demo_intent").fetchone()[0], 0)

    def test_snapshot_from_before_current_lease_cannot_be_replayed(self):
        self.ready()
        self.now += 30001
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        second.acquire()
        with self.assertRaises(ValueError): second.accept_snapshot(self.snapshot(received_at_ms=self.now-1))
        self.assertFalse(second.recovery_allowed())

    def test_negative_persisted_counter_fails_closed(self):
        self.ready()
        self.journal.db.execute("UPDATE demo_daily SET entries=-1")
        self.assertFalse(self.authority.allows(self.candidate))

    def test_quick_restart_retains_original_day_baseline_and_entry_count(self):
        self.ready()
        self.reserve()
        self.now += 30001
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        second.acquire()
        second.accept_snapshot(self.snapshot(equity_usdt="9900", available_usdt="9900"))
        daily = second.db.execute("SELECT start_equity,entries FROM demo_daily").fetchone()
        self.assertEqual(Decimal(daily[0]), 10000)
        self.assertEqual(daily[1], 1)
        self.assertFalse(second.allows(self.candidate))

    def test_lease_takeover_between_check_and_reservation_rolls_back_everything(self):
        self.ready()
        key = self.journal.register(self.candidate["plan"])
        self.now += 30001
        second = self.make_authority(self.open_journal(), worker="worker2", session="session2")
        second.acquire()
        with self.assertRaises(ValueError): self.journal.reserve_entry(key)
        self.assertEqual(self.journal.db.execute("SELECT dispatched FROM demo_intent").fetchone()[0], 0)
        self.assertEqual(self.journal.db.execute("SELECT entries FROM demo_daily").fetchone()[0], 0)
        self.assertIsNone(self.journal.db.execute("SELECT 1 FROM demo_risk_entry").fetchone())

    def test_full_intercepted_http_executor_uses_authority_and_single_risk_reservation(self):
        import httpx
        import json
        from okx_demo_transport import OKXDemoTransport, DemoCredentials
        self.ready()
        calls = []
        def handler(request):
            calls.append(request)
            body = json.loads(request.content)
            return httpx.Response(200, json={"code":"0", "data":[
                dict(sCode="0", clOrdId=body["clOrdId"], ordId="12345")]})
        broker = OKXDemoTransport(DemoCredentials("synthetic-key", "synthetic-secret", "synthetic-pass"),
            enable_demo_requests=True, transport=httpx.MockTransport(handler), clock=lambda:self.now)
        engine = DemoExecutor(self.journal, broker, clock=lambda:self.now,
            entry_gate=self.authority.allows, recovery_gate=self.authority.recovery_allowed, enabled=True)
        source = args(); del source["now_ms"]
        self.assertEqual(asyncio.run(engine.enter(source))["phase"], "entry_acknowledged_query_required")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].headers["x-simulated-trading"], "1")
        self.assertEqual(self.journal.db.execute("SELECT entries FROM demo_daily").fetchone()[0], 1)
        self.assertEqual(engine.broker.environment, "demo")

    def test_full_fake_executor_uses_persistent_authority_instead_of_always_true_gate(self):
        self.ready()
        broker = FakeExchange()
        engine = DemoExecutor(self.journal, broker, clock=lambda:self.now, entry_gate=self.authority.allows,
                              recovery_gate=self.authority.recovery_allowed, enabled=True)
        source = args(); del source["now_ms"]
        result = asyncio.run(engine.enter(source))
        self.assertEqual(result["phase"], "entry_acknowledged_query_required")
        self.assertEqual(len(broker.calls), 1)
        self.assertEqual(self.journal.db.execute("SELECT entries FROM demo_daily").fetchone()[0], 1)
        self.authority.set_kill_switch(True)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertTrue(self.authority.recovery_allowed())


if __name__ == "__main__": unittest.main()
