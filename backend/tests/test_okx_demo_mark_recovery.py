"""Reproduce moving unrealized equity during real-style filled-position reads."""
from copy import deepcopy
import unittest
from test_okx_demo_trading_supervisor import SupervisorTests
from test_okx_strategy_bridge import args
from okx_demo_trading_supervisor import DemoTradingSupervisor
from okx_demo_snapshot import DemoSnapshotError
from okx_demo_transport import DemoTransportError,DEMO_HEADERS


class MarkRecoveryTests(SupervisorTests):
    def moving_equity(self):
        def mutate(f,path,count):
            if f.row is not None and path.endswith('/balance'):
                f.balance['details'][0]['eq']=str(10000+count/100)
        self.f.mutate=mutate

    async def test_moving_equity_does_not_block_owned_protection_and_exit(self):
        self.moving_equity()
        result=await self.session.enter(args())
        self.assertEqual(result['phase'],'native_protection_verified_monitor_required')
        self.assertTrue(self.owner.snapshot['recovery_only'])
        with self.assertRaisesRegex(ValueError,'Recovery snapshots cannot authorize entry'):
            self.owner._assess({},self.f.now)
        with self.assertRaises(DemoSnapshotError): await self.reader.collect()
        async def forbidden(*a): raise AssertionError('Recovery mode queried entry source')
        supervisor=DemoTradingSupervisor(self.session,self.runner,forbidden,enabled=True,recover_only=True)
        result=await supervisor.run(arm_entries=True,initialize_baseline=True)
        self.assertTrue(result['flat_verified'])
        self.assertTrue(result['recovery_only'])
        self.assertTrue(result['protection_verified'])
        self.assertEqual(len(self.f.posts),2)
        self.assertIs(self.f.posts[1][1]['reduceOnly'],True)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_risk_entry').fetchone()[0],1)

    async def test_recovery_mode_without_intent_cannot_create_order(self):
        async def forbidden(*a): raise AssertionError('Recovery mode queried entry source')
        supervisor=DemoTradingSupervisor(self.session,self.runner,forbidden,enabled=True,recover_only=True)
        result=await supervisor.run(arm_entries=True,initialize_baseline=True)
        self.assertEqual(result['phase'],'no_active_intent_no_submission')
        self.assertEqual(self.f.posts,[])

    async def test_cash_changes_still_block_recovery(self):
        await self.session.enter(args())
        def mutate(f,path,count):
            if path.endswith('/balance'): f.balance['details'][0]['cashBal']=str(10000+count/100)
        self.f.mutate=mutate
        result=await self.session.recover(self.f.row['clOrdId'],force_exit=True)
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(result['recovery_stage'],'snapshot_before')
        self.assertEqual(len(self.f.posts),1)

    async def test_position_changes_still_block_recovery(self):
        await self.session.enter(args())
        def mutate(f,path,count):
            if path.endswith('/positions'): f.positions[0]['pos']=str(count/10)
        self.f.mutate=mutate
        result=await self.session.recover(self.f.row['clOrdId'],force_exit=True)
        self.assertEqual(result['phase'],'blocked_reconciliation_required')
        self.assertEqual(len(self.f.posts),1)

    async def test_recovery_can_run_after_continuous_audit_gap_without_new_entry(self):
        await self.session.enter(args());self.f.now+=120000
        async def forbidden(*a): raise AssertionError('Recovery mode queried entry source')
        supervisor=DemoTradingSupervisor(self.session,self.runner,forbidden,enabled=True,recover_only=True)
        result=await supervisor.run()
        self.assertTrue(result['flat_verified'])
        self.assertEqual(len(self.f.posts),2)


class RecoveryTransportTests(SupervisorTests):
    async def test_transport_cannot_enter_when_recovery_only_even_if_called(self):
        self.transport.entries_enabled=False
        with self.assertRaises(DemoTransportError):
            await self.transport.place_entry({},DEMO_HEADERS)
        self.assertEqual(self.f.calls,[])
        self.assertEqual(self.f.posts,[])
