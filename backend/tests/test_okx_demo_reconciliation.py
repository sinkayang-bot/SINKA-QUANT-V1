"""Complete intercepted GET runner, atomic failure and restart recovery."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
import test_okx_demo_settlement as fixtures
from test_okx_demo_close_reader import CloseFixture
from okx_demo_reconciliation import DemoReconciliationRunner
from okx_demo_bill_reader import DemoBillReadError
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import DemoExecutionJournal
from okx_demo_authority import DemoEntryAuthority


class RunnerFixture(CloseFixture):
    def __init__(self):
        super().__init__(); self.bills=[]; self.bill_calls=0; self.bill_mutate=None
        self.balance['details'][0]['cashBal']='10000'

    def override(self,request):
        if request.url.path!='/api/v5/account/bills': return super().override(request)
        self.bill_calls+=1
        if self.bill_mutate: self.bill_mutate(self)
        q=dict(request.url.params)
        if set(q)-{'ccy','begin','end','limit','after'} or q['ccy']!='USDT':
            raise AssertionError('Unfiltered account-wide USDT bills required')
        rows=[r for r in self.bills if int(q['begin'])<=int(r['ts'])<=int(q['end'])]
        if 'after' in q: rows=[r for r in rows if int(r['billId'])<int(q['after'])]
        return httpx.Response(200,json=dict(code='0',data=deepcopy(rows[:100]),msg='private'))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        t=fixtures.SettlementTests('test_zero_funding_requires_complete_scans_and_cash_check')
        t.auditor_class=DemoCycleLifecycle; t.setUp(); self.addCleanup(t.doCleanups); self.t=t
        raw,close,broker,engine=t.trade()
        self.key=broker.body['clOrdId']; self.original_plan=deepcopy(t.candidate['plan'])
        self.f=RunnerFixture(); f=self.f
        f.plan=deepcopy(self.original_plan); f.entry=deepcopy(broker.row)
        f.algo=deepcopy(close['algo']['rows'][0]); f.child=deepcopy(close['child']['rows'][0]); f.fills=deepcopy(raw['recent_fills'])
        net=sum((Decimal(row['fillPnl'])+Decimal(row['fee']) for row in f.fills),Decimal(0))-1
        for row in f.fills:
            f.bills.append(dict(billId=row['billId'],ts=row['fillTime'],type='2',subType='1' if row['side']=='buy' else '2',
                ordId=row['ordId'],tradeId=row['tradeId'],instId=row['instId'],instType='SWAP',ccy='USDT',
                pnl=row['fillPnl'],fee=row['fee'],sz=row['fillSz'],px=row['fillPx'],balChg=str(Decimal(row['fillPnl'])+Decimal(row['fee']))))
        f.bills.append(dict(billId='103',ts=str(t.now),type='8',subType='173',ordId='',tradeId='',instId='BTC-USDT-SWAP',
            instType='SWAP',ccy='USDT',pnl='-1',fee='0',balChg='-1',sz=broker.body['sz'],px='101'))
        f.bills.sort(key=lambda b:int(b['billId']),reverse=True)
        t.now+=6000; f.now=t.now
        f.balance['details'][0]['cashBal']=str(Decimal('10000')+net)
        t.journal.db.execute('UPDATE demo_intent SET snapshot=NULL')
        t.journal.db.execute('UPDATE demo_execution SET observed_at_ms=NULL')
        t.audit.enabled=True
        self.runner=DemoReconciliationRunner(t.audit,f.reader(),enabled=True)

    def run_once(self,**kwargs): return asyncio.run(self.runner.run_once(**kwargs))

    def test_explicit_native_close_is_get_only_atomic_and_keeps_kill_switch(self):
        from okx_demo_reconciliation import close_native
        self.t.authority.release()
        binding=dict(account_scope='synthetic-v1',uid=self.f.config['uid'])
        with patch('time.time',return_value=self.f.now/1000), patch('okx_demo_transport.OKXDemoReadTransport',return_value=self.f.reader().transport):
            result=asyncio.run(close_native(self.t.path,binding,object()))
        self.assertEqual(result['phase'],'cycle_archived')
        self.assertTrue(result['native_exit_verified']); self.assertTrue(result['ledger_archived'])
        self.assertFalse(result['new_entry_allowed'])
        self.assertEqual(self.t.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_explicit_native_close_preserves_intent_on_unverified_lineage(self):
        from okx_demo_reconciliation import close_native
        self.t.authority.release(); self.f.child['algoId']='999'
        binding=dict(account_scope='synthetic-v1',uid=self.f.config['uid'])
        with patch('time.time',return_value=self.f.now/1000), patch('okx_demo_transport.OKXDemoReadTransport',return_value=self.f.reader().transport):
            result=asyncio.run(close_native(self.t.path,binding,object()))
        self.assertEqual(result['phase'],'blocked')
        db=self.t.journal.db
        self.assertIsNotNone(db.execute('SELECT 1 FROM demo_intent').fetchone())
        for table in ('demo_cycle_archive','demo_risk_close','demo_closed_trade'):
            self.assertIsNone(db.execute('SELECT 1 FROM '+table).fetchone())
        self.assertEqual(db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_expired_baseline_is_retrieved_twice_before_fresh_native_settlement(self):
        from okx_demo_fill_audit import normalize_fill
        begin=self.t.journal.db.execute('SELECT baseline_at FROM demo_fill_audit_meta').fetchone()[0]
        old=dict(self.f.fills[0],billId='50',tradeId='50',ordId='999',fillTime=str(begin-1))
        self.t.audit._put(normalize_fill(old,self.f.now),baseline=True)
        previous=self.f.response_override; reads=[]
        def override(request):
            if request.url.path=='/api/v5/trade/fills-history':
                reads.append(request)
                return httpx.Response(200,json={'code':'0','data':[old]})
            return previous(request)
        self.f.response_override=override
        result=self.run_once()
        self.assertEqual(result['phase'],'cycle_archived')
        self.assertEqual(len(reads),2)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_native_close_waits_for_existing_bill_delay_without_committing(self):
        for f in self.f.fills: f['fillTime']=str(self.f.now-1000)
        for bill in self.f.bills:
            if bill['type']=='2': bill['ts']=str(self.f.now-1000)
        result=self.run_once()
        self.assertEqual(result['phase'],'native_settlement_pending')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_closed_trade').fetchone())
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_risk_close').fetchone())
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def historical_runner(self,days=2):
        self.t.now+=days*86400000; self.f.now=self.t.now
        self.t.authority.acquire(); self.t.authority.set_kill_switch(True)
        return DemoReconciliationRunner(self.t.audit,self.f.reader(),enabled=True,historical_close=True)

    def test_explicit_historical_native_close_preserves_original_risk_day(self):
        from okx_demo_authority import day_key
        original_day=day_key(self.t.now)
        runner=self.historical_runner()
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'cycle_archived')
        db=self.t.journal.db
        closed=db.execute('SELECT closed_at FROM demo_risk_close').fetchone()[0]
        self.assertEqual(day_key(closed),original_day)
        self.assertNotEqual(day_key(closed),day_key(self.t.now))
        self.assertEqual(db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_historical_mode_is_explicit_and_requires_kill_switch(self):
        self.t.now+=2*86400000; self.f.now=self.t.now
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.t.authority.acquire(); self.t.authority.set_kill_switch(False)
        runner=DemoReconciliationRunner(self.t.audit,self.f.reader(),enabled=True,historical_close=True)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'blocked')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())
        with self.assertRaises(ValueError):
            DemoReconciliationRunner(self.t.audit,self.f.reader(),enabled=True,historical_close='true')

    def test_historical_close_missing_bill_rolls_back_and_can_retry(self):
        runner=self.historical_runner(); missing=self.f.bills.pop(1)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'blocked')
        db=self.t.journal.db
        for table in ('demo_cycle_archive','demo_risk_close','demo_closed_trade'):
            self.assertIsNone(db.execute('SELECT 1 FROM '+table).fetchone())
        self.assertIsNotNone(db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.f.bills.append(missing); self.f.bills.sort(key=lambda x:int(x['billId']),reverse=True)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'cycle_archived')

    def test_historical_close_outside_seven_days_does_not_archive(self):
        runner=self.historical_runner(days=8)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'blocked')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())
        self.assertIsNotNone(self.t.journal.db.execute('SELECT 1 FROM demo_intent').fetchone())

    def test_single_run_reads_closes_settles_archives_and_next_run_is_idle(self):
        result=self.run_once()
        self.assertEqual(result['phase'],'cycle_archived')
        self.assertEqual(result['sequence'],1)
        self.assertFalse(result['new_entry_allowed']); self.assertEqual(result['orders_sent'],0)
        db=self.t.journal.db
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertIsNone(db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.assertEqual(self.run_once()['phase'],'idle_verified')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))
        self.assertNotIn('private',json.dumps(result)); self.assertNotIn('123456',json.dumps(result))

    def test_archived_idle_next_day_get_only_resume_and_window_diagnostic(self):
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        self.t.now+=25*60*60*1000; self.f.now=self.t.now
        result=self.run_once(resume_idle=True)
        self.assertEqual(result['phase'],'idle_verified')
        self.assertEqual(result['sequence'],1)
        self.assertEqual(result['orders_sent'],0)
        self.t.now+=24*60*60*1000; self.f.now=self.t.now
        result=self.run_once(resume_idle=True)
        self.assertEqual(result['phase'],'idle_verified')
        self.assertEqual(result['orders_sent'],0)
        self.t.now+=48*60*60*1000+1; self.f.now=self.t.now
        result=self.run_once(resume_idle=True)
        self.assertEqual(result['phase'],'blocked')
        self.assertEqual(result['audit_stage'],'archived_idle_window')
        self.assertEqual(result['failure_kind'],'validation_failed')
        self.assertEqual(result['recovery_stage'],'idle_audit')
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_idle_transient_read_recovers_with_fresh_bundle(self):
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        original=self.runner.bill_reader.collect
        calls=[]
        async def transient(**kwargs):
            calls.append(1)
            if len(calls)<3:
                raise DemoBillReadError('private-response',stage='snapshot_before',
                    kind='validation_failed',snapshot_stage='snapshot_consistency')
            return await original(**kwargs)
        with patch.object(self.runner.bill_reader,'collect',side_effect=transient):
            self.assertEqual(self.run_once()['phase'],'idle_verified')
        self.assertEqual(len(calls),3)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)

    def test_idle_persistent_timeout_is_bounded_and_invalidates_readiness(self):
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        error=DemoBillReadError('secret',stage='snapshot_after',kind='timeout')
        with patch.object(self.runner.bill_reader,'collect',side_effect=error) as collect:
            result=self.run_once()
        self.assertEqual(collect.call_count,3)
        self.assertEqual(result['read_attempts'],3)
        self.assertEqual(result['failure_kind'],'timeout')
        self.assertEqual(result['recovery_stage'],'idle_read')
        self.assertEqual(result['read_stage'],'snapshot_after')
        self.assertFalse(result['new_entry_allowed'])
        self.assertIsNone(self.t.authority.snapshot)
        self.assertNotIn('secret',json.dumps(result))

    def test_account_validation_failure_is_never_retried(self):
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        error=DemoBillReadError('private',stage='snapshot_before',
            kind='validation_failed',snapshot_stage='account_config')
        with patch.object(self.runner.bill_reader,'collect',side_effect=error) as collect:
            result=self.run_once()
        self.assertEqual(collect.call_count,1)
        self.assertEqual(result['snapshot_stage'],'account_config')
        self.assertEqual(result['phase'],'blocked')

    def test_ledger_transition_failure_is_not_retried(self):
        with patch.object(self.t.audit,'_advance_cycle_in_transaction',side_effect=ValueError('private')) as advance:
            result=self.run_once()
        self.assertEqual(advance.call_count,1)
        self.assertEqual(result['recovery_stage'],'closed_commit')
        self.assertEqual(result['read_attempts'],1)
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())

    def test_disabled_runner_and_disabled_transition_never_reach_http(self):
        self.runner.enabled=False
        self.assertEqual(self.run_once()['phase'],'disabled'); self.assertEqual(self.f.calls,[])
        self.runner.enabled=True; self.t.audit.enabled=False
        self.assertEqual(self.run_once()['phase'],'cycle_transition_disabled'); self.assertEqual(self.f.calls,[])

    def test_isolated_margin_close_archives_then_resumes_idle_without_duplicate_risk(self):
        for row in self.f.bills:
            if row['type']=='2':
                margin=Decimal('12.5') if row['subType']=='1' else Decimal('-12.5')
                row.update(posBalChg=str(margin),balChg=str(Decimal(row['balChg'])-margin))
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        db=self.t.journal.db
        archive=json.loads(db.execute('SELECT body FROM demo_cycle_archive').fetchone()[0])
        bill_records=archive['records']['demo_account_bill']
        bodies=[json.loads(r[bill_records['columns'].index('body')]) for r in bill_records['rows']]
        self.assertEqual(sum(Decimal(r.get('posBalChg','0')) for r in bodies),0)
        self.assertEqual(len([r for r in bodies if 'posBalChg' in r]),2)
        self.assertEqual(self.run_once()['phase'],'idle_verified')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_unsubmitted_intent_preserved_without_request_or_resubmit(self):
        self.t.journal.db.execute('UPDATE demo_intent SET dispatched=0')
        self.assertEqual(self.run_once()['phase'],'unsubmitted_intent_preserved')
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.t.journal.db.execute('SELECT dispatched FROM demo_intent').fetchone()[0],0)

    def test_wrong_uid_changes_no_order_or_fill_ledger(self):
        self.f.config['uid']='999'
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertIsNone(self.t.journal.db.execute('SELECT snapshot FROM demo_intent').fetchone()[0])
        self.assertTrue(all(r.url.path.endswith('/config') for r in self.f.calls))

    def test_active_or_ambiguous_native_exit_preserves_intent(self):
        self.f.algo.update(state='live',ordIdList=[])
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())

    def test_data_change_between_passes_rejects_composite_bundle(self):
        self.f.bill_mutate=lambda f:f.balance['details'][0].update(cashBal='10000')
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_audit_fills').fetchone())

    def test_archive_failure_rolls_back_ingested_observation_close_fills_and_risk(self):
        with patch.object(self.t.audit,'_advance_cycle_in_transaction',side_effect=ValueError('private-failure')):
            result=self.run_once()
        self.assertEqual(result['phase'],'blocked'); self.assertNotIn('private-failure',json.dumps(result))
        db=self.t.journal.db
        self.assertIsNone(db.execute('SELECT snapshot FROM demo_intent').fetchone()[0])
        self.assertIsNone(db.execute('SELECT observed_at_ms FROM demo_execution').fetchone()[0])
        for table in ('demo_audit_fills','demo_closed_trade','demo_risk_close','demo_cycle_archive'):
            self.assertIsNone(db.execute('SELECT 1 FROM '+table).fetchone())
        self.assertEqual(self.run_once()['phase'],'cycle_archived')
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_missing_bill_rolls_back_close_ingestion_and_can_resume(self):
        missing=self.f.bills.pop(1)
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_closed_trade').fetchone())
        self.f.bills.append(missing); self.f.bills.sort(key=lambda b:int(b['billId']),reverse=True)
        self.assertEqual(self.run_once()['phase'],'cycle_archived')

    def test_crash_after_commit_before_acceptance_recovers_from_archive_without_duplication(self):
        with patch.object(self.t.authority,'accept_snapshot',side_effect=RuntimeError('simulated handoff crash')):
            self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)
        self.assertEqual(self.run_once()['phase'],'idle_verified')
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)

    def test_restart_after_failed_read_resolves_original_id_without_resubmission(self):
        self.f.child['algoId']='999'; self.assertEqual(self.run_once()['phase'],'blocked')
        self.f.child['algoId']='444'; self.t.now+=30001; self.f.now=self.t.now
        journal=self.t.open_journal(); owner=self.t.owner(journal,'second')
        lifecycle=DemoCycleLifecycle(owner,enable_cycle_transition=True)
        runner=DemoReconciliationRunner(lifecycle,self.f.reader(),enabled=True)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'cycle_archived')
        self.assertEqual(journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_competing_owner_cannot_start_private_reads(self):
        journal=self.t.open_journal(); owner=self.t.owner(journal,'second')
        lifecycle=DemoCycleLifecycle(owner,enable_cycle_transition=True)
        runner=DemoReconciliationRunner(lifecycle,self.f.reader(),enabled=True)
        self.assertEqual(asyncio.run(runner.run_once())['phase'],'lease_unavailable')
        self.assertEqual(self.f.calls,[])

    def test_concurrent_runs_share_authority_lock_and_do_not_duplicate_work(self):
        async def exercise():
            started=asyncio.Event(); release=asyncio.Event()
            original=self.runner._closed_bundle
            async def stalled(*args):
                started.set(); await release.wait(); return await original(*args)
            other=DemoReconciliationRunner(self.t.audit,self.f.reader(),enabled=True)
            with patch.object(self.runner,'_closed_bundle',side_effect=stalled):
                first=asyncio.create_task(self.runner.run_once()); await started.wait()
                self.assertEqual((await other.run_once())['phase'],'busy')
                release.set(); self.assertEqual((await first)['phase'],'cycle_archived')
        asyncio.run(exercise())
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)

    def test_external_cancellation_preserves_intent_and_clears_old_readiness(self):
        async def exercise():
            started=asyncio.Event()
            async def stalled(*a): started.set(); await asyncio.Future()
            with patch.object(self.runner,'_closed_bundle',side_effect=stalled):
                task=asyncio.create_task(self.runner.run_once()); await started.wait(); task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
        asyncio.run(exercise())
        self.assertEqual(self.runner.last_result['phase'],'cancelled')
        self.assertIsNone(self.t.authority.snapshot)
        self.assertEqual(self.t.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)
        self.assertEqual(self.t.journal.db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)

    def test_fencing_during_network_scan_cannot_commit_or_invalidate_new_owner(self):
        takeover=[]
        def change_owner(f):
            if takeover: return
            self.t.now+=30001; f.now=self.t.now
            owner=self.t.owner(self.t.open_journal(),'second'); owner.acquire(); takeover.append(owner)
        self.f.bill_mutate=change_owner
        self.assertEqual(self.run_once()['phase'],'blocked')
        self.assertEqual(self.t.journal.db.execute('SELECT session FROM demo_authority').fetchone()[0],'second')
        self.assertIsNone(self.t.journal.db.execute('SELECT 1 FROM demo_cycle_archive').fetchone())

    def test_scope_mismatch_rejected_at_constructor(self):
        other=self.f.reader(account_scope='other')
        with self.assertRaises(ValueError): DemoReconciliationRunner(self.t.audit,other)


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f=RunnerFixture()
        self.journal=DemoExecutionJournal(Path(self.temp.name)/'ledger.sqlite'); self.addCleanup(self.journal.close)
        self.owner=DemoEntryAuthority(self.journal,account_scope='synthetic-v1',worker_id='worker',clock=lambda:self.f.now)
        self.lifecycle=DemoCycleLifecycle(self.owner,enable_cycle_transition=True)
        self.runner=DemoReconciliationRunner(self.lifecycle,self.f.reader(),enabled=True)

    def test_first_baseline_requires_explicit_flag_and_starts_killed(self):
        self.assertEqual(asyncio.run(self.runner.run_once())['phase'],'baseline_required')
        self.assertEqual(self.f.calls,[])
        self.assertEqual(asyncio.run(self.runner.run_once(initialize_baseline=True))['phase'],'baseline_initialized')
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(asyncio.run(self.runner.run_once())['phase'],'idle_verified')
        self.assertTrue(all(r.method=='GET' for r in self.f.calls))

    def test_initialization_never_resets_existing_unsubmitted_intent(self):
        self.journal.register(self.f.plan)
        self.assertEqual(asyncio.run(self.runner.run_once(initialize_baseline=True))['phase'],'blocked')
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_fill_audit_meta').fetchone())
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0],1)
