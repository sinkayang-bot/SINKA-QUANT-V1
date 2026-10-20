"""Offline multi-cycle rehearsal; no exchange or credentials."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import unittest
from unittest.mock import patch
import test_okx_demo_settlement as fixtures
from test_okx_strategy_bridge import args,READY
from test_okx_demo_executor import FakeExchange
from okx_strategy_bridge import plan_strategy_signal
from okx_demo_cycles import DemoCycleLifecycle, ARCHIVED_IDLE_RESUME_MS, DemoIdleAuditError
from okx_demo_fill_audit import DemoFillAuditError,DemoFillAuditor
from okx_demo_executor import DemoExecutor


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.t=fixtures.SettlementTests('test_zero_funding_requires_complete_scans_and_cash_check')
        self.t.auditor_class=DemoCycleLifecycle
        self.t.setUp(); self.addCleanup(self.t.doCleanups)

    def prepared(self,funding='-1'):
        self.raw,self.bills,self.trade,self.broker,self.engine=self.t.prepared(funding)
        self.cycles=self.t.audit; self.cycles.enabled=True
        self.plan=deepcopy(self.t.candidate['plan'])

    def roll(self,funding='-1'):
        self.prepared(funding)
        self.rolled=self.cycles.advance_cycle(self.raw,self.bills)
        self.t.authority.accept_snapshot(self.rolled['snapshot'])
        self.boundary=self.raw['received_at_ms']
        return self.rolled

    def idle(self):
        raw=deepcopy(self.raw); raw.update(received_at_ms=self.t.now,scan_finished_at_ms=self.t.now)
        pages=[dict(code='0',data=[])]
        bills=dict(demo_verified=True,account_scope='synthetic-v1',begin_ms=self.boundary,
            through_ms=self.t.now,received_at_ms=self.t.now,
            query=dict(ccy='USDT',begin=str(self.boundary),end=str(self.t.now),limit='100'),passes=[deepcopy(pages),deepcopy(pages)])
        return raw,bills

    def wait_until(self,target):
        while self.t.now<target:
            self.t.now=min(self.t.now+20000,target)
            self.t.authority.acquire()
            raw,bills=self.idle()
            self.t.authority.accept_snapshot(self.cycles.audit_idle(raw,bills))

    def source(self):
        ready=(self.t.now//300000)*300000
        source=args(now_ms=self.t.now,started_at_ms=self.boundary,instrument_received_at_ms=self.t.now,balance_received_at_ms=self.t.now)
        source['signal'].update(id=str(ready-300000),ts=ready-300000,readyAt=ready)
        source['quote']['ts']=ready+100
        return source

    def test_transition_default_off_preserves_unarchived_intent(self):
        self.prepared(); self.cycles.enabled=False
        with self.assertRaises(DemoFillAuditError): self.cycles.advance_cycle(self.raw,self.bills)
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())

    def test_archive_retains_complete_cycle_and_does_not_clear_daily_risk(self):
        result=self.roll()
        db=self.t.journal.db
        self.assertFalse(result['new_entry_allowed']); self.assertFalse(result['submission_enabled'])
        body=json.loads(db.execute('SELECT body FROM demo_cycle_archive').fetchone()[0])
        self.assertEqual(body['sequence'],1); self.assertEqual(body['previous_hash'],'')
        self.assertEqual(len(body['records']['demo_intent']['rows']),1)
        self.assertEqual(len(body['records']['demo_audit_fills']['rows']),2)
        self.assertEqual(len(body['records']['demo_account_bill']['rows']),3)
        self.assertEqual(db.execute('SELECT entries FROM demo_daily').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertIsNone(db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.assertEqual(db.execute('SELECT DISTINCT baseline FROM demo_audit_fills').fetchall(),[(1,)])
        self.assertEqual(self.cycles.verify_archive_chain()[0],1)

    def test_archived_signal_and_dispatch_cannot_be_replayed(self):
        self.roll(); journal=self.t.journal; key=self.plan['body']['clOrdId']
        with self.assertRaises(ValueError): journal.register(self.plan)
        with self.assertRaises(ValueError): journal.reserve_dispatch(key)
        with self.assertRaises(ValueError): journal.reserve_entry(key)
        with self.assertRaises(ValueError): journal.tracked(key)

    def test_signal_must_follow_new_cycle_and_cooldown_still_blocks(self):
        self.roll()
        with self.assertRaisesRegex(ValueError,'Signal predates'):
            self.t.authority._assess(self.t.candidate,self.t.now)
        self.assertFalse(self.t.authority.allows(self.t.candidate))

    def test_new_signal_allowed_only_after_cooldown_with_continuous_idle_audits(self):
        self.roll(); self.wait_until(READY+1200000+1000)
        candidate=plan_strategy_signal(**self.source())
        self.assertTrue(self.t.authority.allows(candidate))
        self.assertEqual(self.t.journal.db.execute('SELECT entries FROM demo_daily').fetchone()[0],1)

    def test_third_loss_halt_survives_archive_and_cooldown(self):
        self.prepared('-100'); self.t.journal.db.execute('UPDATE demo_daily SET loss_streak=2')
        result=self.cycles.advance_cycle(self.raw,self.bills)
        self.boundary=self.raw['received_at_ms']; self.t.authority.accept_snapshot(result['snapshot'])
        self.wait_until(READY+1200000+1000)
        self.assertEqual(self.t.journal.db.execute('SELECT loss_streak,halt FROM demo_daily').fetchone(),(3,1))
        self.assertFalse(self.t.authority.allows(plan_strategy_signal(**self.source())))

    def test_daily_entry_cap_and_kill_switch_not_reset(self):
        self.roll(); self.t.journal.db.execute('UPDATE demo_daily SET entries=6')
        self.wait_until(READY+1200000+1000)
        self.assertFalse(self.t.authority.allows(plan_strategy_signal(**self.source())))
        self.t.journal.db.execute('UPDATE demo_daily SET entries=1')
        self.t.authority.set_kill_switch(True)
        self.assertFalse(self.t.authority.allows(plan_strategy_signal(**self.source())))

    def test_archive_failure_rolls_back_settlement_risk_and_active_ledger_clear(self):
        self.prepared(); db=self.t.journal.db
        with patch.object(self.cycles,'_baseline_metadata',side_effect=ValueError('failure after archive insert')):
            with self.assertRaises(DemoFillAuditError): self.cycles.advance_cycle(self.raw,self.bills)
        self.assertIsNone(db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())
        self.assertIsNone(db.execute('SELECT 1 FROM demo_trade_settlement').fetchone())
        self.assertIsNone(db.execute('SELECT 1 FROM demo_risk_close').fetchone())
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],2)
        self.assertEqual(db.execute('SELECT sequence FROM demo_cycle_state').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)

    def test_incomplete_or_changed_settlement_never_archives(self):
        self.prepared()
        for changes in (dict(cash_balance_usdt='10000'),dict(pending_algos=[dict(algoId='444')])):
            with self.subTest(changes=changes),self.assertRaises(DemoFillAuditError):
                self.cycles.advance_cycle(self.raw | changes,self.bills)
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())

    def test_idle_unknown_bill_cash_or_fill_rejects_and_invalidates_receipt(self):
        self.roll()
        for which in ('cash','fill','bill'):
            raw,bills=self.idle()
            if which=='cash': raw['cash_balance_usdt']='1'
            elif which=='fill': raw['recent_fills'].append(self.t.fill(billId='999',tradeId='999',ordId='999'))
            else:
                unknown=dict(billId='999',ts=str(self.t.now),type='8',subType='173',ordId='',tradeId='',
                    instId='BTC-USDT-SWAP',instType='SWAP',ccy='USDT',pnl='-1',fee='0',balChg='-1',sz='1',px='100')
                for pages in bills['passes']: pages[0]['data']=[unknown]
            with self.subTest(which=which),self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills)
            self.assertIsNone(self.t.authority.snapshot)
            self.assertEqual(self.t.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)

    def test_generic_fill_audit_cannot_bypass_idle_bill_receipt(self):
        self.roll(); raw,_=self.idle()
        proof=DemoFillAuditor(self.t.authority).audit(raw)
        with self.assertRaises(ValueError): self.t.authority.accept_snapshot(proof)
        self.assertFalse(self.t.authority.allows(self.t.candidate))

    def test_archive_corruption_and_state_reset_are_detected(self):
        self.roll(); db=self.t.journal.db
        db.execute("UPDATE demo_cycle_archive SET body='{}'")
        with self.assertRaises((ValueError,KeyError)): self.cycles.verify_archive_chain()
        raw,bills=self.idle()
        with self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills)
        db.execute("UPDATE demo_cycle_state SET sequence=0,previous_hash=''")
        with self.assertRaises(ValueError): self.t.authority.accept_snapshot(self.rolled['snapshot'])

    def test_restart_requires_current_session_idle_receipt_and_preserves_counters(self):
        self.roll(); old=self.rolled['snapshot']; self.t.now+=30001
        journal=self.t.open_journal(); owner=self.t.owner(journal,'second'); owner.acquire()
        cycles=DemoCycleLifecycle(owner,enable_cycle_transition=True)
        with self.assertRaises(ValueError): owner.accept_snapshot(old)
        raw,bills=self.idle(); proof=cycles.audit_idle(raw,bills); owner.accept_snapshot(proof)
        self.assertEqual(journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertEqual(cycles.verify_archive_chain()[0],1)
        with self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills)
        self.assertEqual(journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],1)

    def test_idle_gap_requires_explicit_resume_and_preserves_original_baseline(self):
        self.roll(); self.t.now+=60001; self.t.authority.acquire()
        raw,bills=self.idle()
        with self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills)
        proof=self.cycles.audit_idle(raw,bills,resume_idle=True)
        self.assertTrue(proof['fill_audit_ready'])
        self.assertEqual(self.t.journal.db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0],self.boundary)

    def test_archived_idle_resume_keeps_loss_halt_history_and_kill_switch(self):
        self.prepared('-100'); db=self.t.journal.db
        db.execute('UPDATE demo_daily SET loss_streak=2')
        self.rolled=self.cycles.advance_cycle(self.raw,self.bills)
        self.boundary=self.raw['received_at_ms']
        self.t.authority.set_kill_switch(True)
        tables=('demo_cycle_archive','demo_daily','demo_risk_entry','demo_risk_close','demo_cash_baseline')
        before={t:db.execute('SELECT * FROM '+t).fetchall() for t in tables}
        self.t.now+=7200000; self.t.authority.acquire()
        raw,bills=self.idle()
        self.t.authority.accept_snapshot(self.cycles.audit_idle(raw,bills,resume_idle=True))
        self.assertEqual(before,{t:db.execute('SELECT * FROM '+t).fetchall() for t in tables})
        self.assertEqual(db.execute('SELECT halt FROM demo_daily').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        receipt=json.loads(db.execute('SELECT body FROM demo_idle_resume').fetchone()[0])
        self.assertEqual(receipt['cycle_sequence'],1)
        self.assertEqual(receipt['archive_hash'],self.rolled['archive_hash'])
        self.assertFalse(self.t.authority.allows(plan_strategy_signal(**self.source())))

    def test_archived_idle_gap_rejects_stale_changed_or_unsettled_evidence(self):
        for change in ('too_old','cash','fill','bill','archive','baseline','emergency','write_failure'):
            with self.subTest(change=change):
                self.setUp(); self.roll(); db=self.t.journal.db
                self.t.now+=ARCHIVED_IDLE_RESUME_MS+1 if change=='too_old' else 7200000
                self.t.authority.acquire(); raw,bills=self.idle()
                if change=='cash': raw['cash_balance_usdt']='1'
                if change=='fill': raw['recent_fills'].append(self.t.fill(billId='999',tradeId='999',ordId='999'))
                if change=='bill':
                    for pages in bills['passes']: pages[0]['data']=[{}]
                if change=='archive': db.execute("UPDATE demo_cycle_archive SET hash='bad'")
                if change=='baseline': db.execute('UPDATE demo_fill_audit_meta SET baseline_at=baseline_at-1')
                if change=='emergency':
                    db.execute('CREATE TABLE IF NOT EXISTS demo_emergency_exit (entry_id TEXT)')
                    db.execute("INSERT INTO demo_emergency_exit(entry_id) VALUES('unresolved')")
                if change=='write_failure':
                    db.execute("CREATE TRIGGER fail_resume BEFORE INSERT ON demo_idle_resume BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
                before=db.execute('SELECT baseline_at,last_checked,revision FROM demo_fill_audit_meta').fetchone()
                with self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills,resume_idle=True)
                self.assertEqual(db.execute('SELECT baseline_at,last_checked,revision FROM demo_fill_audit_meta').fetchone(),before)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)
                self.assertIsNone(db.execute('SELECT 1 FROM demo_idle_resume').fetchone())

    def test_archived_idle_next_day_resume_preserves_original_evidence(self):
        for elapsed in (25*60*60*1000, ARCHIVED_IDLE_RESUME_MS):
            with self.subTest(elapsed=elapsed):
                self.setUp(); self.roll(); db=self.t.journal.db
                tables=('demo_cycle_archive','demo_daily','demo_risk_entry','demo_risk_close',
                    'demo_cash_baseline','demo_audit_fills')
                before={t:db.execute('SELECT * FROM '+t).fetchall() for t in tables}
                self.t.now+=elapsed; self.t.authority.acquire()
                raw,bills=self.idle()
                proof=self.cycles.audit_idle(raw,bills,resume_idle=True)
                self.assertTrue(proof['fill_audit_ready'])
                self.assertEqual(before,{t:db.execute('SELECT * FROM '+t).fetchall() for t in tables})
                self.assertEqual(db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0],self.boundary)
                self.assertEqual(db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    def test_archived_idle_window_failure_has_safe_diagnostic(self):
        self.roll(); self.t.now+=ARCHIVED_IDLE_RESUME_MS+1; self.t.authority.acquire()
        raw,bills=self.idle()
        with self.assertRaises(DemoIdleAuditError) as raised:
            self.cycles.audit_idle(raw,bills,resume_idle=True)
        self.assertEqual(raised.exception.audit_stage,'archived_idle_window')

    def test_next_day_resume_still_rejects_activity_and_missing_anchor(self):
        for change in ('cash','bill','fill','anchor','order','algo','position'):
            with self.subTest(change=change):
                self.setUp(); self.roll(); db=self.t.journal.db
                self.t.now+=25*60*60*1000; self.t.authority.acquire()
                raw,bills=self.idle()
                if change=='cash': raw['cash_balance_usdt']='1'
                if change=='bill':
                    for pages in bills['passes']: pages[0]['data']=[{}]
                if change=='fill': raw['recent_fills'].append(self.t.fill(billId='999',tradeId='999',ordId='999'))
                if change=='anchor': raw['recent_fills']=[]
                if change=='order': raw['pending_orders']=[{}]
                if change=='algo': raw['pending_algos']=[{}]
                if change=='position': raw['positions']=[{'pos':'1'}]
                with self.assertRaises(DemoFillAuditError):
                    self.cycles.audit_idle(raw,bills,resume_idle=True)
                self.assertIsNone(db.execute('SELECT 1 FROM demo_idle_resume').fetchone())
                self.assertEqual(db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0],self.boundary)

    def test_resume_receipt_identity_does_not_repeat_after_cycle_revision_reset(self):
        self.roll(); db=self.t.journal.db
        # Earlier-cycle receipts survive archival while the current cycle's
        # fill-audit revision restarts at one.
        previous='{"cycle_sequence":0,"revision":2}'
        db.execute('INSERT INTO demo_idle_resume VALUES(2,?)',(previous,))
        self.t.now+=60001; self.t.authority.acquire(); raw,bills=self.idle()
        self.cycles.audit_idle(raw,bills,resume_idle=True)
        rows=db.execute('SELECT revision,body FROM demo_idle_resume ORDER BY revision').fetchall()
        self.assertEqual(rows[0],(2,previous))
        self.assertEqual(rows[1][0],3)
        self.assertEqual(json.loads(rows[1][1])['audit_revision'],2)

    def test_double_archive_attempt_cannot_erase_history_or_duplicate_risk(self):
        self.roll()
        with self.assertRaises(DemoFillAuditError): self.cycles.advance_cycle(self.raw,self.bills)
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)

    def test_two_complete_cycles_with_restart_preserve_history_and_daily_risk(self):
        self.roll(); first_hash=self.rolled['archive_hash']
        # Reopen under a new lease session, then continue bounded idle monitoring.
        self.t.now+=30001
        journal=self.t.open_journal(); owner=self.t.owner(journal,'second'); owner.acquire()
        self.t.journal=journal; self.t.authority=owner
        self.cycles=DemoCycleLifecycle(owner,enable_cycle_transition=True); self.t.audit=self.cycles
        raw,bills=self.idle(); owner.accept_snapshot(self.cycles.audit_idle(raw,bills)); owner.set_kill_switch(False)
        self.wait_until(READY+1200000+1000)
        source=self.source(); candidate=plan_strategy_signal(**source)
        self.assertTrue(owner.allows(candidate))
        class NextExchange(FakeExchange):
            async def place_entry(self,body,headers):
                reply=await super().place_entry(body,headers)
                self.row['ordId']='12346'; reply['data'][0]['ordId']='12346'
                return reply
        broker=NextExchange()
        engine=DemoExecutor(journal,broker,clock=lambda:self.t.now,entry_gate=owner.allows,
            recovery_gate=owner.recovery_allowed,enabled=True)
        del source['now_ms']
        self.assertEqual(asyncio.run(engine.enter(source))['phase'],'entry_acknowledged_query_required')
        q=broker.body['sz']; broker.row.update(state='filled',accFillSz=q)
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        opened=self.t.now; gross=Decimal(q)*Decimal('0.01')
        entry=self.t.fill(billId='201',tradeId='301',ordId='12346',fillSz=q,fee='-0.1')
        exit=self.t.fill(billId='202',tradeId='302',ordId='556',side='sell',fillSz=q,fillPx='102',fee='-0.2',fillPnl=str(gross))
        # Real monitoring after a previous archive needs a new owned-position
        # receipt, not the old flat-cycle entry receipt.
        monitoring,_=self.idle(); monitoring['recent_fills'].append(entry)
        monitoring['positions']=[dict(instId='BTC-USDT-SWAP',pos=q,posSide='net',mgnMode='isolated')]
        proof=self.cycles.audit(monitoring); owner.accept_snapshot(proof)
        self.assertTrue(proof['recovery_only'])
        self.assertFalse(owner.allows(candidate))
        snap,_=self.idle(); snap['recent_fills'] += [entry,exit]
        bracket=broker.body['attachAlgoOrds'][0]
        algo=dict(algoId='445',algoClOrdId=bracket['attachAlgoClOrdId'],instId='BTC-USDT-SWAP',instType='SWAP',
            state='effective',ordType='oco',side='sell',posSide='net',tdMode='isolated',reduceOnly=True,sz=q,
            ordIdList=['556'],subAlgoIdList=[],closeFraction='') | {k:v for k,v in bracket.items() if k!='attachAlgoClOrdId'}
        child=dict(ordId='556',clOrdId='',algoId='445',algoClOrdId=bracket['attachAlgoClOrdId'],instId='BTC-USDT-SWAP',
            state='filled',ordType='market',side='sell',posSide='net',tdMode='isolated',reduceOnly=True,sz=q,accFillSz=q)
        evidence=dict(demo_verified=True,account_scope='synthetic-v1',algo=dict(complete=True,received_at_ms=opened,rows=[algo]),
            child=dict(complete=True,received_at_ms=opened,rows=[child]))
        trade=self.cycles.audit_closed(snap,evidence)
        self.assertEqual(Decimal(trade['trading_net_usdt']),gross-Decimal('0.3'))
        self.t.now+=6000; asyncio.run(engine.refresh(broker.body['clOrdId']))
        snap.update(received_at_ms=self.t.now,scan_finished_at_ms=self.t.now,
            cash_balance_usdt=str(Decimal(snap['cash_balance_usdt'])+Decimal(trade['trading_net_usdt'])))
        bills=[]
        for f in (entry,exit):
            bills.append(dict(billId=f['billId'],ts=str(opened),type='2',subType='1' if f['side']=='buy' else '2',
                ordId=f['ordId'],tradeId=f['tradeId'],instId=f['instId'],instType='SWAP',ccy='USDT',pnl=f['fillPnl'],
                fee=f['fee'],sz=f['fillSz'],px=f['fillPx'],balChg=str(Decimal(f['fillPnl'])+Decimal(f['fee']))))
        pages=[dict(code='0',data=sorted(bills,key=lambda f:int(f['billId']),reverse=True))]
        evidence=dict(demo_verified=True,account_scope='synthetic-v1',begin_ms=self.boundary,
            through_ms=self.t.now,received_at_ms=self.t.now,
            query=dict(ccy='USDT',begin=str(self.boundary),end=str(self.t.now),limit='100'),passes=[deepcopy(pages),deepcopy(pages)])
        result=self.cycles.advance_cycle(snap,evidence)
        self.assertEqual(result['sequence'],2)
        self.assertEqual(self.cycles.verify_archive_chain()[0],2)
        history=[json.loads(row[0]) for row in journal.db.execute('SELECT body FROM demo_cycle_archive ORDER BY sequence')]
        self.assertEqual(history[1]['previous_hash'],first_hash)
        self.assertEqual(history[0]['client_id'],self.plan['body']['clOrdId'])
        self.assertEqual(history[1]['client_id'],broker.body['clOrdId'])
        self.assertEqual(journal.db.execute('SELECT entries FROM demo_daily').fetchone()[0],2)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],2)
        self.assertIsNone(journal.db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills WHERE baseline=1').fetchone()[0],4)
        with self.assertRaises(ValueError): journal.register(candidate['plan'])
        owner.accept_snapshot(result['snapshot'])
        self.assertFalse(owner.allows(candidate))

    def test_cycle_boundary_corruption_is_detected_before_new_readiness(self):
        self.roll(); self.t.journal.db.execute('UPDATE demo_cycle_state SET started_at=started_at-1')
        raw,bills=self.idle()
        with self.assertRaises(DemoFillAuditError): self.cycles.audit_idle(raw,bills)

    def test_intercepted_bill_reader_renews_idle_cycle_without_unlocking_cooldown(self):
        from test_okx_demo_bill_reader import BillsFixture
        from okx_demo_bill_reader import DemoBillReader
        self.roll(); self.t.now+=1000
        f=BillsFixture(); f.now=self.t.now
        f.balance['details'][0]['cashBal']=self.raw['cash_balance_usdt']
        f.fills=deepcopy(self.raw['recent_fills'])
        bundle=asyncio.run(DemoBillReader(f.reader()).collect(begin_ms=self.boundary))
        proof=self.cycles.audit_idle(bundle['snapshot'],bundle['bills'])
        self.t.authority.accept_snapshot(proof)
        self.assertTrue(proof['fill_audit_ready'])
        self.assertFalse(self.t.authority.allows(self.t.candidate))
        self.assertEqual(self.cycles.verify_archive_chain()[0],1)
        self.assertTrue(all(r.method=='GET' for r in f.calls))
