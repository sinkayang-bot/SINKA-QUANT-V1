"""Existing worker's explicitly enabled one-shot demo execution mode."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from test_okx_demo_trading_supervisor import SupervisorTests
from test_okx_strategy_bridge import args
from okx_demo_trading_supervisor import DemoTradingSupervisor
from okx_demo_trading_session import DemoTradingSession
from okx_demo_trading_worker import execute
from okx_strategy_bridge import plan_execution_test


def execution_source():
    source=args()
    source.update(strategy_version='DEMO-EXECUTION-TEST-1',signal=None)
    return source


class ExecutionModeTests(SupervisorTests):
    def enable(self):
        self.owner.execution_test_enabled=True
        self.session.execution_test_enabled=True
        async def source(*a): return execution_source()
        self.supervisor=DemoTradingSupervisor(self.session,self.runner,source,enabled=True,
            execution_test=True,max_seconds=90)

    async def test_minimum_entry_protection_and_immediate_reduce_only_exit(self):
        self.enable()
        result=await self.supervisor.run(arm_entries=True)
        self.assertTrue(result['execution_test'])
        self.assertEqual(result['entry_api_code'],'0')
        self.assertEqual(result['entry_event_code'],'0')
        self.assertTrue(result['protection_verified'])
        self.assertTrue(result['flat_verified'])
        self.assertTrue(result['test_roundtrip_verified'])
        self.assertFalse(result['strategy_performance_sample'])
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),2)
        self.assertEqual(self.f.posts[0][1]['sz'],self.f.instrument['minSz'])
        self.assertIs(self.f.posts[1][1]['reduceOnly'],True)
        self.assertEqual(self.f.positions,[])
        self.assertEqual(self.journal.db.execute('SELECT entries FROM demo_daily').fetchone()[0],1)
        self.assertIn('execution_test',self.journal.db.execute('SELECT plan FROM demo_intent').fetchone()[0])
        self.assertEqual(self.journal.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)

    async def test_test_mode_cannot_take_over_a_normal_strategy_intent(self):
        await self.session.enter(args())
        self.enable()
        result=await self.supervisor.run(arm_entries=True)
        self.assertEqual(result['phase'],'existing_strategy_intent_preserved')
        self.assertEqual(len(self.f.posts),1)
        self.assertTrue(self.f.positions)

    async def test_default_session_cannot_force_entry(self):
        result=await self.session.enter(execution_source(),execution_test=True)
        self.assertEqual(result['phase'],'execution_test_disabled')
        self.assertEqual(self.f.posts,[])

    async def test_authority_needs_separate_explicit_test_admission(self):
        self.session.execution_test_enabled=True
        result=await self.session.enter(execution_source(),execution_test=True)
        self.assertEqual(result['phase'],'entry_gate_blocked')
        self.assertEqual(self.f.posts,[])

    async def test_restart_queries_existing_exit_without_another_entry(self):
        self.enable();await self.supervisor.run(arm_entries=True)
        result=await self.session.recover(self.f.row['clOrdId'],force_exit=True)
        self.assertEqual(result['phase'],'flat_requires_emergency_settlement_review')
        self.assertEqual(len(self.f.posts),2)

    async def test_unknown_entry_receipt_is_queried_not_retried(self):
        self.enable();self.f.timeout_entry=True
        result=await self.supervisor.run(arm_entries=True)
        self.assertTrue(result['test_roundtrip_verified'])
        self.assertEqual(len(self.f.posts),2)

    async def test_failed_entry_lookup_never_claims_roundtrip(self):
        self.enable()
        async def missing(*a): return {'code':'0','data':[]}
        with patch.object(self.transport,'lookup_entry',side_effect=missing):
            result=await self.supervisor.run(arm_entries=True)
        self.assertFalse(result['test_roundtrip_verified'])
        self.assertEqual(len(self.f.posts),1)
        # Restore the original lookup and recover only the tracked owned intent.
        result=await self.session.recover(self.f.row['clOrdId'],force_exit=True)
        self.assertEqual(result['phase'],'exit_acknowledged_query_required')
        self.assertEqual(len(self.f.posts),2)

    async def test_minimum_too_large_never_reserves_or_submits(self):
        self.enable()
        self.f.instrument.update(ctVal='1000',minSz='1',lotSz='1')
        result=await self.supervisor.run(arm_entries=True)
        self.assertFalse(result['test_roundtrip_verified'])
        self.assertEqual(self.f.posts,[])
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_intent').fetchone())


class TestPlanTests(unittest.TestCase):
    def test_test_plan_is_distinct_minimal_and_stable_between_validation_calls(self):
        source=execution_source()
        first=plan_execution_test(**source)
        source['now_ms']+=1
        self.assertEqual(first,plan_execution_test(**source))
        self.assertEqual(first['strategy'],'DEMO-EXECUTION-TEST-1')
        self.assertEqual(first['plan']['purpose'],'execution_test')
        self.assertEqual(first['plan']['body']['sz'],str(source['instrument']['minSz']))
        for change in ({'demo':False},{'symbol':'ETH-USDT-SWAP'},{'strategy_version':'SNR-5M-1'},{'signal':{}}):
            with self.subTest(change=change),self.assertRaises(ValueError): plan_execution_test(**(source|change))

    def test_unsupported_or_unbounded_test_start_fails_before_network(self):
        env=dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1',DEMO_EXECUTION_TEST='1',
            OKX_API_KEY='synthetic',OKX_API_SECRET='synthetic',OKX_API_PASSPHRASE='synthetic',
            OKX_EXPECTED_UID='123',DEMO_ACCOUNT_SCOPE='scope',DEMO_LEDGER_PATH='/tmp/unused.sqlite')
        self.assertEqual(execute(env)[1],2)
        env.update(DEMO_MAX_SECONDS='90',DEMO_SCAN_MARKETS='20')
        self.assertEqual(execute(env)[1],2)
