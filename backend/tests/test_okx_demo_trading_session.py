"""All GET/POST traffic is intercepted; synthetic credentials only."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from okx_demo_authority import DemoEntryAuthority
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import DemoExecutionJournal
from okx_demo_snapshot import DemoSnapshotReader, DemoSnapshotError
from unittest.mock import patch
from okx_demo_trading_session import DemoTradingSession
from okx_demo_trading_transport import OKXDemoTradingTransport
from okx_demo_transport import DemoCredentials,DemoTransportError
from test_okx_demo_snapshot import Fixture,UID
from test_okx_strategy_bridge import args


class TradingFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.balance['details'][0].update(cashBal='10000')
        self.row=None; self.exit_row=None; self.mode='protected'; self.posts=[]
        self.timeout_entry=False; self.timeout_exit=False; self.cancel_race=False
        self.request_hook=None

    def handler(self,request):
        if self.request_hook: self.request_hook(request)
        assert request.headers['x-simulated-trading']=='1'
        assert request.url.host=='www.okx.com'
        if request.method=='POST':
            body=json.loads(request.content); self.posts.append((request.url.path,body))
            if request.url.path.endswith('/cancel-order'):
                if self.cancel_race:
                    self.row.update(state='filled',accFillSz=self.row['sz'])
                    self.positions[0]['pos']=self.row['sz']
                else: self.row['state']='canceled'
                self.orders=[]
                row=self.row
            elif body.get('reduceOnly') is True:
                self.exit_row=dict(body,ordId='54321',state='filled',accFillSz=body['sz'])
                self.positions=[]; self.orders=[]
                self.algos={kind:[] for kind in self.algos}
                row=self.exit_row
                if self.timeout_exit: raise httpx.ReadTimeout('private-server-message')
            else:
                qty='0.4' if self.mode=='partial' else body['sz']
                self.row={k:body[k] for k in ('instId','clOrdId','side','tdMode','posSide','ordType','sz')}
                self.row.update(ordId='12345',state='partially_filled' if self.mode=='partial' else 'filled',accFillSz=qty)
                self.positions=[dict(instId=body['instId'],instType='SWAP',posId='1',mgnMode='isolated',posSide='net',pos=qty)]
                self.fills=[dict(billId='101',tradeId='201',ordId='12345',instId=body['instId'],side='buy',posSide='net',
                    fillTime=str(self.now),fillSz=qty,fillPx='101',fee='-0.001',feeCcy='USDT',fillPnl='0')]
                self.orders=[deepcopy(self.row)] if self.mode=='partial' else []
                if self.mode=='protected':
                    bracket=body['attachAlgoOrds'][0]
                    algo={k:v for k,v in bracket.items() if k!='attachAlgoClOrdId'}
                    algo.update(algoId='5678',algoClOrdId=bracket['attachAlgoClOrdId'],instId=body['instId'],instType='SWAP',
                        tdMode='isolated',posSide='net',side='sell',reduceOnly='true',ordType='oco',state='live',sz=qty,closeFraction='')
                    self.algos['oco']=[algo]
                row=self.row
                if self.timeout_entry: raise httpx.ReadTimeout('private-server-message')
            return httpx.Response(200,json={'code':'0','data':[dict(sCode='0',ordId=row['ordId'],clOrdId=row['clOrdId'])]})
        if request.url.path.endswith('/order'):
            self.calls.append(request)
            key=request.url.params['clOrdId']
            row=self.exit_row if key.startswith('sr') else self.row
            return httpx.Response(200,json={'code':'0','data':[] if row is None else [deepcopy(row)]})
        if request.url.path.endswith('/bills'):
            self.calls.append(request)
            return httpx.Response(200,json={'code':'0','data':[]})
        return super().handler(request)


class TradingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.f=TradingFixture(); self.path=Path(self.temp.name)/'ledger.sqlite'
        self.journal=DemoExecutionJournal(self.path); self.addCleanup(self.journal.close)
        self.owner=DemoEntryAuthority(self.journal,account_scope='synthetic-v1',worker_id='test',clock=lambda:self.f.now)
        self.owner.acquire(); self.cycles=DemoCycleLifecycle(self.owner)
        self.transport=OKXDemoTradingTransport(DemoCredentials('synthetic-key','synthetic-secret','synthetic-pass'),
            enable_demo_requests=True,enable_demo_orders=True,transport=httpx.MockTransport(self.f.handler),clock=lambda:self.f.now)
        self.reader=DemoSnapshotReader(self.transport,account_scope='synthetic-v1',expected_uid=UID,
            symbol='BTC-USDT-SWAP',clock=lambda:self.f.now)
        snapshot=await self.reader.collect()
        self.owner.accept_snapshot(self.cycles.bootstrap(snapshot,baseline_authorized=True))
        self.owner.set_kill_switch(False)
        self.session=DemoTradingSession(self.cycles,self.reader,enabled=True)
        self.f.calls=[]

    def key(self): return self.f.row['clOrdId']

    async def test_disabled_gates_do_not_query_or_write(self):
        self.session.enabled=False
        self.assertEqual((await self.session.enter(args()))['phase'],'disabled')
        self.session.enabled=True; self.transport.orders_enabled=False
        self.assertEqual((await self.session.enter(args()))['phase'],'disabled')
        self.assertEqual(self.f.calls,[]); self.assertEqual(self.f.posts,[])

    async def test_readonly_permission_blocks_before_any_write(self):
        self.f.config['perm']='read_only'
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(self.f.posts,[])
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_full_entry_checks_native_protection_and_cannot_duplicate(self):
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertFalse(result['automatic_runtime_ready'])
        self.assertEqual(len(self.f.posts),1)
        self.assertEqual((await self.session.enter(args()))['phase'],'existing_intent_recovery_required')
        self.assertEqual(len(self.f.posts),1)
        self.assertEqual(self.journal.db.execute('SELECT entries FROM demo_daily').fetchone()[0],1)

    async def test_transient_lookup_race_rechecks_without_new_order_or_exit(self):
        lookups=0
        def changing(request):
            nonlocal lookups
            if request.url.path.endswith('/order') and self.f.row:
                lookups+=1
                self.f.row['ordId']='99999' if lookups==1 else '12345'
        self.f.request_hook=changing
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(lookups,4)
        self.assertEqual(len(self.f.posts),1)
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_emergency_exit').fetchone())

    async def test_snapshot_consistency_race_rechecks_entire_entry_evidence(self):
        original=self.session._evidence_once
        count=0
        async def transient(key):
            nonlocal count
            count+=1
            if count<3:
                raise DemoSnapshotError('private',stage='snapshot_consistency',kind='validation_failed')
            return await original(key)
        with patch.object(self.session,'_evidence_once',side_effect=transient):
            result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(count,3)
        self.assertEqual(len(self.f.posts),1)

    async def test_exit_confirmation_retries_reads_without_resending_reduction(self):
        self.f.mode='unprotected'
        self.assertEqual((await self.session.enter(args()))['phase'],'exit_acknowledged_query_required')
        original=self.session._query_exit_once
        count=0
        async def transient(record):
            nonlocal count
            count+=1
            if count==1:
                raise DemoSnapshotError('private',stage='snapshot_consistency',kind='validation_failed')
            return await original(record)
        with patch.object(self.session,'_query_exit_once',side_effect=transient):
            result=await self.session.recover(self.key())
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(count,2)
        self.assertEqual(len(self.f.posts),2)

    async def test_persistent_exit_snapshot_change_is_bounded_and_never_resubmits(self):
        self.f.mode='unprotected'
        await self.session.enter(args())
        with patch.object(self.session,'_query_exit_once',side_effect=DemoSnapshotError(
                'private',stage='snapshot_consistency',kind='validation_failed')) as read:
            result=await self.session.recover(self.key())
        self.assertEqual(read.call_count,3)
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(len(self.f.posts),2)

    async def test_exit_account_validation_is_never_retried(self):
        self.f.mode='unprotected'
        await self.session.enter(args())
        with patch.object(self.session,'_query_exit_once',side_effect=DemoSnapshotError(
                'private',stage='account_config',kind='validation_failed')) as read:
            result=await self.session.recover(self.key())
        self.assertEqual(read.call_count,1)
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(len(self.f.posts),2)

    async def test_order_lookup_difference_reports_fixed_consistency_checks(self):
        lookups=0
        def changing(request):
            nonlocal lookups
            if request.url.path.endswith('/order') and self.f.row:
                lookups+=1
                self.f.row['ordId']=str(12345+lookups)
        self.f.request_hook=changing
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        checks=result['protection_diagnostics']['consistency_checks']
        self.assertFalse(checks['order_lookup_stable'])
        self.assertTrue(checks['snapshot_stable'])
        self.assertTrue(checks['within_deadline'])
        self.assertEqual(len(self.f.posts),1)
        self.assertNotIn('synthetic-key',str(result))

    async def test_fast_strategy_uses_same_native_protection_and_duplicate_guard(self):
        result=await self.session.enter(args(strategy_version='SNR-5M-FAST-1'))
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(len(self.f.posts),1)
        self.assertEqual((await self.session.enter(args(strategy_version='SNR-5M-RETEST-1')))['phase'],'existing_intent_recovery_required')
        self.assertEqual(len(self.f.posts),1)

    async def test_narrow_stop_candidate_skips_without_killing_next_valid_signal(self):
        source=args(strategy_version='SNR-5M-FAST-1')
        source['signal']['stop']='100.99'
        result=await self.session.enter(source)
        self.assertEqual(result['gate_reason'],'signal_stop_distance')
        self.assertEqual(self.f.posts,[])
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],0)
        self.assertEqual((await self.session.enter(args()))['phase'],'native_protection_verified_monitor_required')

    async def test_demo_without_daily_limits_enters_with_existing_halt_and_still_prevents_duplicate(self):
        self.owner.daily_limits_enabled=False
        self.journal.db.execute('UPDATE demo_daily SET entries=6,loss_streak=3,halt=1,cooldown=?',(self.f.now+900000,))
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(len(self.f.posts),1)
        self.assertTrue(self.f.posts[0][1]['attachAlgoOrds'])
        self.assertEqual((await self.session.enter(args()))['phase'],'existing_intent_recovery_required')
        self.assertEqual(len(self.f.posts),1)
        self.assertEqual(self.journal.db.execute('SELECT entries,loss_streak,halt FROM demo_daily').fetchone(),(7,3,1))

    async def test_timeout_entry_is_queried_by_same_id_without_resubmission(self):
        self.f.timeout_entry=True
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertEqual(self.journal.tracked(self.key())[2],'unknown')
        await self.session.recover(self.key())
        self.assertEqual(len(self.f.posts),1)
        self.assertNotIn('private-server-message',str(result))

    async def test_partial_fill_cancel_then_reduce_only_emergency_and_query(self):
        self.f.mode='partial'
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'exit_acknowledged_query_required')
        self.assertEqual(len(self.f.posts),3)
        close=self.f.posts[-1][1]
        self.assertIs(close['reduceOnly'],True)
        self.assertEqual(close['sz'],'0.4')
        self.assertEqual(close['side'],'sell')
        result=await self.session.recover(self.key())
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),3)
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_missing_protection_is_not_claimed_protected(self):
        self.f.mode='unprotected'
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'exit_acknowledged_query_required')
        self.assertIs(self.f.posts[-1][1]['reduceOnly'],True)

    async def test_cancel_fill_race_uses_final_filled_size(self):
        self.f.mode='partial'; self.f.cancel_race=True
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'exit_acknowledged_query_required')
        self.assertEqual(self.f.posts[-1][1]['sz'],self.f.row['sz'])

    async def test_exit_timeout_and_restart_never_resend(self):
        self.f.mode='unprotected'; self.f.timeout_exit=True
        self.assertEqual((await self.session.enter(args()))['phase'],'exit_unknown_no_retry')
        count=len(self.f.posts)
        self.f.now+=30001
        reopened=DemoExecutionJournal(self.path); self.addCleanup(reopened.close)
        owner=DemoEntryAuthority(reopened,account_scope='synthetic-v1',worker_id='new',clock=lambda:self.f.now)
        resumed=DemoTradingSession(DemoCycleLifecycle(owner),self.reader,enabled=True)
        self.assertEqual((await resumed.recover(self.key()))['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),count)

    async def test_force_exit_never_increases_exposure(self):
        await self.session.enter(args())
        self.assertEqual((await self.session.recover(self.key(),force_exit=True))['phase'],'exit_acknowledged_query_required')
        self.assertIs(self.f.posts[-1][1]['reduceOnly'],True)

    async def test_kill_switch_and_daily_halt_block_new_entry(self):
        self.owner.set_kill_switch(True)
        self.assertEqual((await self.session.enter(args()))['phase'],'kill_switch_active')
        self.assertEqual(self.f.posts,[])
        self.owner.set_kill_switch(False)
        self.journal.db.execute('UPDATE demo_daily SET halt=1')
        self.assertEqual((await self.session.enter(args()))['phase'],'entry_gate_blocked')
        self.assertEqual(self.f.posts,[])

    async def test_wrong_uid_or_foreign_inventory_cannot_send(self):
        self.f.config['uid']='999'
        self.assertEqual((await self.session.enter(args()))['phase'],'blocked_reconciliation_required')
        self.assertEqual(self.f.posts,[])

    async def test_cancelled_dispatch_keeps_durable_reservation(self):
        def hook(request):
            if request.method=='POST': raise asyncio.CancelledError()
        self.f.request_hook=hook
        with self.assertRaises(asyncio.CancelledError): await self.session.enter(args())
        self.assertEqual(self.journal.db.execute('SELECT entry_status FROM demo_execution').fetchone()[0],'reserved')
        self.assertEqual(self.journal.db.execute('SELECT dispatched FROM demo_intent').fetchone()[0],1)
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_shared_reconciliation_lock_blocks_concurrent_entry(self):
        await self.session.lock.acquire()
        try: self.assertEqual((await self.session.enter(args()))['phase'],'busy')
        finally: self.session.lock.release()
        self.assertEqual(self.f.posts,[])

    async def test_reduction_transport_rejects_opening_or_untracked_requests(self):
        body=dict(instId='BTC-USDT-SWAP',tdMode='isolated',posSide='net',side='sell',ordType='market',
                  sz='1',clOrdId='sr'+'a'*28,reduceOnly=True)
        for update in ({'reduceOnly':False},{'reduceOnly':'true'},{'tdMode':'cross'},{'clOrdId':'foreign'},{'instId':'BTC-USDT'}):
            with self.assertRaises(ValueError):
                await self.transport.reduce_position(body|update,{'x-simulated-trading':'1'})
        self.transport.orders_enabled=False
        with self.assertRaises(DemoTransportError):
            await self.transport.reduce_position(body,{'x-simulated-trading':'1'})
        self.assertEqual(self.f.posts,[])

    async def test_daily_entry_limit_and_foreign_pending_order_prevent_dispatch(self):
        self.journal.db.execute('UPDATE demo_daily SET entries=6')
        self.assertEqual((await self.session.enter(args()))['phase'],'entry_gate_blocked')
        self.assertEqual(self.f.posts,[])
        self.journal.db.execute('UPDATE demo_daily SET entries=0')
        from test_okx_demo_snapshot import pending
        self.f.orders=[pending(777)]
        self.assertEqual((await self.session.enter(args()))['phase'],'blocked_reconciliation_required')
        self.assertEqual(self.f.posts,[])

    async def test_caller_cannot_supply_inflated_equity_or_leverage(self):
        self.assertEqual((await self.session.enter(args(equity_usdt='99999999',available_usdt='99999999',leverage=125)))['phase'],
            'native_protection_verified_monitor_required')
        # Current verified balances/specification win over the signal caller.
        from okx_strategy_bridge import plan_strategy_signal
        expected=plan_strategy_signal(**args(available_usdt='9500'))
        self.assertEqual(self.f.posts[0][1]['sz'],expected['plan']['body']['sz'])

    async def test_lost_lease_blocks_recovery_writes(self):
        await self.session.enter(args())
        self.f.algos['oco']=[]
        original=self.f.handler
        def takeover(request):
            response=original(request)
            if request.url.path.endswith('/order'):
                self.journal.db.execute("UPDATE demo_authority SET session='another-session',expires=?",(self.f.now+30000,))
            return response
        self.transport._transport=httpx.MockTransport(takeover)
        result=await self.session.recover(self.key())
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(len(self.f.posts),1)

    async def test_wrong_exit_lookup_is_never_adopted_or_resent(self):
        self.f.mode='unprotected'
        await self.session.enter(args())
        self.f.exit_row['clOrdId']='sr'+'f'*28
        self.assertEqual((await self.session.recover(self.key()))['phase'],'blocked_reconciliation_required')
        self.assertEqual(len(self.f.posts),2)
