"""Supervision exercises actual intercepted account readers and trading session."""
import asyncio
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx
from okx_demo_reconciliation import DemoReconciliationRunner
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_strategy_source import DemoStrategySource
from okx_demo_trading_supervisor import DemoTradingSupervisor
from okx_demo_transport import OKXDemoReadTransport
from okx_demo_trading_worker import execute,DemoProgress,run_supervisor
import test_okx_demo_trading_session as fixtures
from test_okx_demo_snapshot import UID
from test_okx_strategy_bridge import args,READY


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TradingTests.asyncSetUp(self)
        read=OKXDemoReadTransport(self.transport.credentials,enable_demo_requests=True,
            transport=httpx.MockTransport(self.f.handler),clock=lambda:self.f.now)
        self.read_reader=DemoSnapshotReader(read,account_scope='synthetic-v1',expected_uid=UID,
            symbol='BTC-USDT-SWAP',clock=lambda:self.f.now)
        self.runner=DemoReconciliationRunner(self.cycles,self.read_reader,enabled=True)
        self.sources=[]
        async def source(symbol,started):
            self.sources.append((symbol,started)); return None
        self.supervisor=DemoTradingSupervisor(self.session,self.runner,source,enabled=True,max_seconds=1)

    async def test_default_disabled_supervisor_never_reads_or_arms(self):
        off=DemoTradingSupervisor(self.session,self.runner,self.supervisor.source)
        self.assertEqual((await off.run(arm_entries=True))['phase'],'disabled')
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.f.posts,[])

    async def test_long_acceptance_window_is_explicit_bounded_and_stoppable_without_entry(self):
        with self.assertRaises(ValueError):
            DemoTradingSupervisor(self.session,self.runner,self.supervisor.source,max_seconds=901)
        for options in ({'max_seconds':7201},{'max_seconds':7200,'recover_only':True},
                        {'max_seconds':90,'execution_test':True}):
            with self.assertRaises(ValueError):
                DemoTradingSupervisor(self.session,self.runner,self.supervisor.source,acceptance_window=True,**options)
        long=DemoTradingSupervisor(self.session,self.runner,self.supervisor.source,
            enabled=True,max_seconds=7200,acceptance_window=True)
        async def source(symbol,started):
            long.stop()
            return None
        long.source=source
        result=await run_supervisor(long,{'DEMO_ARM_ENTRIES':'1'})
        self.assertEqual(result['stop_reason'],'stop_requested')
        self.assertTrue(result['acceptance_window'])
        self.assertEqual(result['max_seconds'],7200)
        self.assertEqual(self.f.posts,[])

    async def test_no_explicit_arm_only_verifies_and_restores_kill(self):
        result=await self.supervisor.run()
        self.assertEqual(result['phase'],'verified_but_not_armed')
        self.assertEqual(self.sources,[]); self.assertEqual(self.f.posts,[])
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_waiting_without_signal_stops_without_order(self):
        def status(result): self.supervisor.stop()
        result=await self.supervisor.run(arm_entries=True,on_status=status)
        self.assertEqual(result['phase'],'stopped_no_active_intent')
        self.assertEqual(result['stop_reason'],'stop_requested')
        self.assertEqual(self.sources,[('BTC-USDT-SWAP',self.f.now)])
        self.assertEqual(self.f.posts,[])

    async def test_worker_wrapper_returns_actual_waiting_counts(self):
        result=await run_supervisor(self.supervisor,{'DEMO_ARM_ENTRIES':'1'})
        self.assertEqual(result['stop_reason'],'runtime_limit')
        self.assertGreaterEqual(result['ticks_observed'],1)
        self.assertEqual(result['tick_counts'],{'waiting_for_signal':result['ticks_observed']})
        self.assertEqual(self.f.posts,[])

    async def test_explicit_first_trial_resume_reaches_signal_source_without_order(self):
        self.f.now+=7200000
        async def source(symbol,started):
            self.sources.append((symbol,started))
            self.supervisor.stop()
            return None
        self.supervisor.source=source
        result=await self.supervisor.run(resume_idle=True,arm_entries=True)
        self.assertEqual(result['stop_reason'],'stop_requested')
        self.assertEqual(len(self.sources),1)
        self.assertEqual(self.f.posts,[])
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_idle_resume').fetchone()[0],1)
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_stop_existing_protected_position_reduces_and_confirms_flat(self):
        await self.session.enter(args())
        result=await self.supervisor.run(on_status=lambda r:self.supervisor.stop())
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),2)
        self.assertIs(self.f.posts[-1][1]['reduceOnly'],True)
        self.assertEqual(self.sources,[])

    async def test_old_position_forces_exit_and_never_fetches_entry_signal(self):
        await self.session.enter(args())
        self.journal.db.execute('UPDATE demo_trading_clock SET dispatched_at=?',(self.f.now-3600001,))
        result=await self.supervisor.run()
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(result['stop_reason'],'exit_acknowledged_query_required')
        self.assertEqual(self.sources,[])
        self.assertIs(self.f.posts[-1][1]['reduceOnly'],True)

    async def test_original_failure_survives_cleanup_stage_change(self):
        await self.session.enter(args())
        original={'phase':'blocked_reconciliation_required','recovery_stage':'protection_fill_audit',
            'failure_kind':'validation_failed','protection_diagnostics':{'pending_algo_count':1}}
        async def failed_tick(): return original
        self.supervisor.tick=failed_tick
        result=await self.supervisor.run()
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(result['stop_diagnostics'],original)
        self.assertEqual(result['stop_diagnostics']['recovery_stage'],'protection_fill_audit')
        self.assertEqual(len(self.f.posts),2)
        self.assertIs(self.f.posts[-1][1]['reduceOnly'],True)

    async def test_source_failure_is_reported_and_stops_without_dispatch(self):
        async def broken(*args): raise RuntimeError('private-source-detail')
        self.supervisor.source=broken
        result=await self.supervisor.run(arm_entries=True)
        self.assertEqual(result['stop_reason'],'runtime_failed')
        self.assertNotIn('private-source-detail',str(result))
        self.assertEqual(self.f.posts,[])

    async def test_different_read_account_is_rejected_before_use(self):
        self.read_reader.uid='999'
        with self.assertRaises(ValueError):
            DemoTradingSupervisor(self.session,self.runner,self.supervisor.source)

    async def test_stop_arriving_during_source_fetch_prevents_entry(self):
        async def source(*values):
            self.supervisor.stop()
            return args()
        self.supervisor.source=source
        result=await self.supervisor.run(arm_entries=True)
        self.assertEqual(result['stop_reason'],'stop_requested')
        self.assertEqual(self.f.posts,[])

    async def test_direct_tick_before_start_cannot_query_or_trade(self):
        self.assertEqual((await self.supervisor.tick())['phase'],'supervisor_not_started')
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.f.posts,[])


class WorkerTests(unittest.TestCase):
    def test_disabled_or_unauthorized_worker_never_resolves_credentials(self):
        class NoSecretReads(dict):
            def __getitem__(self,key): raise AssertionError('Credential accessed')
        result,code=execute(NoSecretReads())
        self.assertEqual((result['phase'],code),('disabled',0))
        result,code=execute(NoSecretReads(RUN_DEMO_TRADING='1'))
        self.assertEqual((result['phase'],code),('demo_authorization_required',2))
        self.assertEqual(execute(dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1'))[1],2)

    def test_js_public_source_uses_real_engine_without_network_or_old_signal_replay(self):
        script='''
        import {sourceFromSnapshot} from './strategy/demo-signal.mjs';
        const bars=Array.from({length:80},(_,i)=>({ts:i*300000,o:100,h:100.4,l:99.6,c:100}));
        bars.push({ts:24000000,o:100,h:101.2,l:99.9,c:101});
        const snapshot={bars,price:101,quoteTs:24300100};
        const good=sourceFromSnapshot(snapshot,{symbol:'BTC-USDT-SWAP',startedAt:24299000,now:24301000});
        const old=sourceFromSnapshot(snapshot,{symbol:'BTC-USDT-SWAP',startedAt:24301000,now:24301000});
        console.log(JSON.stringify({good,old}));
        '''
        run=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[2],
            capture_output=True,text=True,timeout=10,check=True)
        result=json.loads(run.stdout)
        self.assertIsNone(result['old'])
        self.assertEqual(result['good']['strategy_version'],'SNR-5M-1')
        self.assertEqual(result['good']['signal']['side'],1)


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_node_subprocess_does_not_inherit_exchange_credentials_or_node_options(self):
        captured={}
        class Process:
            returncode=0
            async def communicate(self): return b'{"source":null}',b''
        async def spawn(*args,**kwargs): captured.update(kwargs); return Process()
        with patch.dict('os.environ',{'OKX_API_KEY':'synthetic-secret','NODE_OPTIONS':'--inspect'}), \
             patch('okx_demo_strategy_source.asyncio.create_subprocess_exec',side_effect=spawn):
            self.assertIsNone(await DemoStrategySource()('BTC-USDT-SWAP',READY))
        self.assertEqual(set(captured['env']),{'PATH'})
        self.assertNotIn('synthetic-secret',str(captured))


class ProgressTests(unittest.TestCase):
    def test_stop_distance_details_survive_waiting_and_filter_private_fields(self):
        import io
        from contextlib import redirect_stdout
        stream=io.StringIO(); reporter=DemoProgress(True)
        with redirect_stdout(stream):
            reporter(dict(phase='entry_gate_blocked',gate_reason='signal_stop_distance',
                selected_symbol='LTC-USDT-SWAP',selected_strategy='SNR-5M-FAST-1',
                entry_diagnostics={'stop_rejection':'below_minimum','stop_distance_pct':'0.12000000',
                    'min_stop_pct':'0.2','max_stop_pct':'3','api_key':'private', 'uid':'private'}))
            reporter({'phase':'waiting_for_signal'})
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        details=rows[0]['entry_diagnostics']
        self.assertEqual(details['stop_rejection'],'below_minimum')
        self.assertEqual(details['stop_distance_pct'],'0.12000000')
        self.assertEqual(reporter.last_entry_block['entry_diagnostics'],details)
        self.assertEqual(reporter.last_entry_block['selected_symbol'],'LTC-USDT-SWAP')
        self.assertNotIn('private',stream.getvalue())
        self.assertNotIn('entry_diagnostics',rows[-1])

    def test_block_reason_and_symbol_changes_print_and_survive_later_waiting(self):
        import io
        from contextlib import redirect_stdout
        stream=io.StringIO(); reporter=DemoProgress(True)
        with redirect_stdout(stream):
            for reason,symbol in [('daily_risk_halt','LTC-USDT-SWAP'),
                    ('risk_cooldown','LTC-USDT-SWAP'),('risk_cooldown','ETH-USDT-SWAP')]:
                reporter(dict(phase='entry_gate_blocked',gate_reason=reason,
                    retry_after_seconds=60,selected_symbol=symbol))
            reporter({'phase':'waiting_for_signal'})
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([r['ticks'] for r in rows],[1,2,3,4])
        self.assertEqual(rows[0]['gate_reason'],'daily_risk_halt')
        self.assertEqual(reporter.gate_counts,{'daily_risk_halt':1,'risk_cooldown':2})
        self.assertEqual(reporter.last_entry_block,dict(gate_reason='risk_cooldown',
            retry_after_seconds=60,selected_symbol='ETH-USDT-SWAP',ticks=3))
        self.assertNotIn('gate_reason',rows[-1])

    def test_block_diagnostics_filter_unknown_text_and_invalid_waits(self):
        import io
        from contextlib import redirect_stdout
        for retry in (True,-1,86401,'private-value',None):
            with self.subTest(retry=retry):
                stream=io.StringIO(); reporter=DemoProgress(True)
                with redirect_stdout(stream):
                    reporter(dict(phase='entry_gate_blocked',gate_reason='private-value',
                        selected_symbol='private-value',retry_after_seconds=retry,uid='private-value'))
                self.assertEqual(reporter.last_entry_block,dict(gate_reason='unspecified',ticks=1))
                self.assertNotIn('private-value',stream.getvalue())
                self.assertNotIn('retry_after_seconds',stream.getvalue())

    def test_filters_details_and_throttles_unchanged_status(self):
        import io
        from contextlib import redirect_stdout
        stream=io.StringIO(); reporter=DemoProgress(True)
        with redirect_stdout(stream):
            for _ in range(7): reporter({'phase':'waiting_for_signal','secret':'private-value'})
            reporter({'phase':'private-value','uid':'private-uid'})
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([r['ticks'] for r in rows],[1,6,8])
        self.assertEqual(reporter.counts,{'waiting_for_signal':7,'other_status':1})
        self.assertNotIn('private-value',stream.getvalue())
        self.assertNotIn('private-uid',stream.getvalue())

    def test_quiet_mode_counts_without_printing(self):
        with patch('builtins.print') as output:
            reporter=DemoProgress(); reporter({'phase':'entry_gate_blocked'})
        output.assert_not_called()
        self.assertEqual(reporter.counts,{'entry_gate_blocked':1})

    def test_broken_output_does_not_interrupt_recovery(self):
        reporter=DemoProgress(True)
        with patch('builtins.print',side_effect=BrokenPipeError):
            reporter({'phase':'waiting_for_signal'})
        self.assertEqual(reporter.counts,{'waiting_for_signal':1})
