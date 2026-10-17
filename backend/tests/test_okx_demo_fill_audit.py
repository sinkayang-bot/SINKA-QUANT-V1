"""Durable entry-fill proofs using synthetic data only; no exchange access."""
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from okx_demo_authority import DemoEntryAuthority
from okx_demo_executor import DemoExecutionJournal, DemoExecutor
from okx_demo_fill_audit import DemoFillAuditor, DemoFillAuditError, normalize_fill
from okx_strategy_bridge import plan_strategy_signal
from test_okx_strategy_bridge import args, READY
from test_okx_demo_executor import FakeExchange


class FillAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'ledger.sqlite'
        self.now = READY+1000
        self.journal = self.open_journal()
        self.authority = self.owner(self.journal, 'first')
        self.authority.acquire()
        self.audit = DemoFillAuditor(self.authority)
        self.candidate = plan_strategy_signal(**args())

    def open_journal(self):
        journal = DemoExecutionJournal(self.path)
        self.addCleanup(journal.close)
        return journal

    def owner(self, journal, session):
        return DemoEntryAuthority(journal, account_scope='synthetic-v1', worker_id=session,
            session_id=session, clock=lambda:self.now)

    def snapshot(self, **updates):
        return dict(account_scope='synthetic-v1', symbol='BTC-USDT-SWAP', demo_verified=True,
            complete=True, monitor_healthy=True, order_permission_verified=True,
            fill_audit_ready=False, fill_history_complete=False, received_at_ms=self.now,
            scan_finished_at_ms=self.now, position_mode='net_mode', isolated_leverage=3,
            equity_usdt='10000', available_usdt='10000', positions=[], pending_orders=[],
            pending_algos=[], recent_fills=[], instrument=args()['instrument']) | updates

    def fill(self, **updates):
        return dict(billId='101', tradeId='201', ordId='12345', instId='BTC-USDT-SWAP',
            side='buy', posSide='net', fillTime=str(self.now), fillSz='0.4', fillPx='101',
            fee='-0.001', feeCcy='USDT', fillPnl='0') | updates

    def bootstrap(self, **updates):
        proof = self.audit.bootstrap(self.snapshot(**updates), baseline_authorized=True)
        self.authority.accept_snapshot(proof)
        self.authority.set_kill_switch(False)
        return proof

    def partial(self):
        self.bootstrap()
        broker = FakeExchange()
        engine = DemoExecutor(self.journal, broker, clock=lambda:self.now,
            entry_gate=self.authority.allows, recovery_gate=lambda b:self.authority.recovery_allowed(), enabled=True)
        source=args(); del source['now_ms']
        result=asyncio.run(engine.enter(source))
        self.assertEqual(result['phase'], 'entry_acknowledged_query_required')
        broker.row.update(state='partially_filled', accFillSz='0.4')
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snapshot=self.snapshot(recent_fills=[self.fill()], pending_orders=[deepcopy(broker.row)],
            positions=[dict(instId='BTC-USDT-SWAP', pos='0.4', posSide='net', mgnMode='isolated')])
        return snapshot, broker, engine

    def reject(self, snapshot):
        with self.assertRaises(DemoFillAuditError): self.audit.audit(snapshot)
        self.assertIsNone(self.authority.snapshot)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertEqual(self.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)

    def test_boolean_readiness_without_committed_receipt_cannot_unlock(self):
        with self.assertRaises(ValueError): self.authority.accept_snapshot(self.snapshot(fill_audit_ready=True))
        proof=self.bootstrap()
        self.assertTrue(self.authority.allows(self.candidate))
        self.assertFalse(proof['fill_history_complete'])
        with self.assertRaises(ValueError): self.authority.accept_snapshot(proof | dict(equity_usdt='9999'))

    def test_explicit_baseline_required_and_existing_baseline_never_reset(self):
        with self.assertRaises(DemoFillAuditError): self.audit.bootstrap(self.snapshot())
        self.bootstrap()
        with self.assertRaises(DemoFillAuditError): self.audit.bootstrap(self.snapshot(), baseline_authorized=True)
        self.assertEqual(self.journal.db.execute('SELECT revision FROM demo_fill_audit_meta').fetchone()[0],1)

    def test_nonflat_pending_and_existing_intent_cannot_bootstrap(self):
        for change in (dict(positions=[dict(pos='1')]),dict(pending_orders=[dict(ordId='1')]),dict(pending_algos=[dict(algoId='1')])):
            with self.subTest(change=change), self.assertRaises(DemoFillAuditError):
                self.audit.bootstrap(self.snapshot(**change), baseline_authorized=True)
        self.journal.register(self.candidate['plan'])
        with self.assertRaises(DemoFillAuditError): self.audit.bootstrap(self.snapshot(),baseline_authorized=True)

    def test_partial_fill_persists_exactly_once_and_blocks_new_entry(self):
        snap,_,_=self.partial()
        proof=self.audit.audit(snap); self.authority.accept_snapshot(proof)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertTrue(self.authority.recovery_allowed())
        self.audit.audit(snap)
        self.assertEqual(self.journal.db.execute('SELECT quantity,fee FROM demo_audit_fills').fetchall(), [('0.4','-0.001')])

    def test_multiple_partial_fills_and_terminal_full_fill(self):
        snap,broker,engine=self.partial()
        self.audit.audit(snap)
        quantity=broker.body['sz']
        from decimal import Decimal
        remainder=format(Decimal(quantity)-Decimal('0.4'),'f')
        broker.row.update(state='filled',accFillSz=quantity)
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snap.update(pending_orders=[], positions=[dict(instId='BTC-USDT-SWAP',pos=quantity,posSide='net',mgnMode='isolated')])
        snap['recent_fills'].append(self.fill(billId='102',tradeId='202',fillSz=remainder))
        self.audit.audit(snap)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],2)

    def test_missing_excess_unknown_and_closing_fills_rejected(self):
        snap,_,_=self.partial()
        for change in (dict(recent_fills=[]),dict(recent_fills=[self.fill(fillSz='0.5')]),
            dict(recent_fills=[self.fill(ordId='999')]),dict(recent_fills=[self.fill(side='sell')]),
            dict(recent_fills=[self.fill(fillPnl='1')]),dict(positions=[]),dict(pending_orders=[])):
            with self.subTest(change=change): self.reject(snap | change)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],0)

    def test_changed_fill_and_lost_anchor_invalidate_previous_proof(self):
        snap,_,_=self.partial()
        proof=self.audit.audit(snap); self.authority.accept_snapshot(proof)
        self.reject(snap | dict(recent_fills=[self.fill(fee='-0.002')]))
        with self.assertRaises(ValueError): self.authority.accept_snapshot(proof)
        self.reject(snap | dict(recent_fills=[]))
        self.assertEqual(self.journal.db.execute('SELECT fee FROM demo_audit_fills').fetchone()[0],'-0.001')

    def test_atomic_rollback_after_valid_fill_then_unknown_fill(self):
        snap,_,_=self.partial()
        snap['recent_fills'].append(self.fill(billId='102',tradeId='202',ordId='999'))
        self.reject(snap)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],0)
        self.assertEqual(self.journal.db.execute('SELECT revision FROM demo_fill_audit_meta').fetchone()[0],1)

    def test_baseline_history_is_preserved_but_never_adopted_as_strategy_pnl(self):
        self.bootstrap(recent_fills=[self.fill(ordId='999',fillPnl='12')])
        self.audit.audit(self.snapshot(recent_fills=[self.fill(ordId='999',fillPnl='12')]))
        self.assertEqual(self.journal.db.execute('SELECT baseline FROM demo_audit_fills').fetchone()[0],1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],0)

    def test_restart_reuses_ledger_and_fences_old_receipt_and_old_owner(self):
        old=self.bootstrap()
        self.now+=30001
        journal=self.open_journal(); owner=self.owner(journal,'second'); self.assertTrue(owner.acquire())
        audit=DemoFillAuditor(owner)
        with self.assertRaises(ValueError): owner.accept_snapshot(old)
        proof=audit.audit(self.snapshot()); owner.accept_snapshot(proof)
        self.assertFalse(owner.allows(self.candidate))  # takeover restores kill switch
        with self.assertRaises(DemoFillAuditError): self.audit.audit(self.snapshot())
        self.assertEqual(journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],1)
        self.assertTrue(owner.recovery_allowed())

    def test_long_gap_requires_catchup_and_cannot_reset_baseline(self):
        self.bootstrap(); self.now+=60001; self.authority.acquire()
        self.reject(self.snapshot())
        with self.assertRaises(DemoFillAuditError): self.audit.bootstrap(self.snapshot(),baseline_authorized=True)

    def test_malformed_duplicate_and_stale_reader_windows(self):
        self.bootstrap()
        for change in (dict(received_at_ms=self.now-5001),dict(scan_finished_at_ms=self.now+1),
            dict(fill_audit_ready=True),dict(fill_history_complete=True),dict(order_permission_verified=False),
            dict(recent_fills=[self.fill(),self.fill()]),dict(recent_fills=[self.fill(fee='NaN')])):
            with self.subTest(change=change): self.reject(self.snapshot(**change))

    def test_decimal_canonicalization_does_not_round_long_values(self):
        value='0.123456789012345678901234567890123456789'
        self.assertEqual(normalize_fill(self.fill(fee=value),self.now)['fee'],value)
        for change in (dict(tradeId='01'),dict(fillTime=str(self.now+1)),dict(fillSz='0'),dict(feeCcy='BTC')):
            with self.subTest(change=change),self.assertRaises(ValueError): normalize_fill(self.fill(**change),self.now)

    def test_reader_to_audit_to_authority_integration_uses_only_intercepted_gets(self):
        from test_okx_demo_snapshot import Fixture
        fixture=Fixture()
        raw=asyncio.run(fixture.reader().collect())
        with self.assertRaises(ValueError): self.authority.accept_snapshot(raw)
        proof=self.audit.bootstrap(raw,baseline_authorized=True)
        self.authority.accept_snapshot(proof); self.authority.set_kill_switch(False)
        self.assertTrue(self.authority.allows(self.candidate))
        next_raw=asyncio.run(fixture.reader().collect())
        self.authority.accept_snapshot(self.audit.audit(next_raw))
        self.assertTrue(self.authority.allows(self.candidate))
        self.assertTrue(all(r.method=='GET' for r in fixture.calls))

    def test_restart_preserves_owned_fills_without_double_counting(self):
        snap,broker,engine=self.partial(); self.audit.audit(snap)
        self.now+=30001
        journal=self.open_journal(); owner=self.owner(journal,'second'); owner.acquire()
        audit=DemoFillAuditor(owner)
        engine.journal=journal
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snap.update(received_at_ms=self.now,scan_finished_at_ms=self.now)
        proof=audit.audit(snap); owner.accept_snapshot(proof)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],1)
        self.assertTrue(owner.recovery_allowed())
        self.assertFalse(owner.allows(self.candidate))
