"""Real intercepted GET evidence and durable unknown-attempt preservation."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import httpx
import test_okx_demo_reconciliation as fixtures
from test_okx_strategy_bridge import args
from okx_strategy_bridge import plan_strategy_signal
from okx_demo_authority import day_key
from okx_demo_transport import OKXDemoReadTransport
from okx_demo_unfilled import resolve_unfilled,verify_chain


class UnfilledTests(unittest.TestCase):
    def setUp(self):
        base=fixtures.RunnerTests('test_single_run_reads_closes_settles_archives_and_next_run_is_idle')
        base.setUp(); self.addCleanup(base.doCleanups)
        self.base=base; self.t=base.t; self.f=base.f; self.db=self.t.journal.db
        self.assertEqual(base.run_once()['phase'],'cycle_archived')
        self.plan=plan_strategy_signal(**args(strategy_version='SNR-5M-FAST-1'))['plan']
        self.client=self.plan['body']['clOrdId']
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_trading_clock (entry_id TEXT PRIMARY KEY,dispatched_at INTEGER NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_emergency_exit (entry_id TEXT PRIMARY KEY,client_id TEXT,body TEXT,status TEXT,exchange_order_id TEXT)')
        self.db.execute('INSERT INTO demo_intent VALUES(?,?,1,NULL)',(self.client,json.dumps(self.plan)))
        self.db.execute("INSERT INTO demo_execution(id,entry_status) VALUES(?,'unknown')",(self.client,))
        self.db.execute('INSERT INTO demo_trading_clock VALUES(?,?)',(self.client,self.t.now))
        self.db.execute('INSERT INTO demo_risk_entry VALUES(?,?,?,?)',
            (self.client,day_key(self.t.now),self.t.authority.epoch,self.t.authority.session))
        self.db.execute('UPDATE demo_daily SET entries=entries+1 WHERE day=?',(day_key(self.t.now),))
        self.t.now+=60001; self.f.now=self.t.now
        self.lookup_code='51603'; self.lookup_data=[]; self.calls=[]
        original=self.f.handler
        def handler(request):
            self.calls.append(request)
            if request.url.path.endswith('/order') and request.url.params.get('clOrdId')==self.client:
                return httpx.Response(200,json={'code':self.lookup_code,'data':self.lookup_data})
            return original(request)
        self.transport=OKXDemoReadTransport(enable_demo_requests=True,credentials=self.f.reader().transport.credentials,
            transport=httpx.MockTransport(handler),clock=lambda:self.f.now)
        self.binding=dict(account_scope='synthetic-v1',uid=self.f.config['uid'])
        self.before_risk=self.db.execute('SELECT * FROM demo_daily ORDER BY day').fetchall()
        self.before_meta=self.db.execute('SELECT * FROM demo_fill_audit_meta').fetchall()

    def resolve(self):
        return asyncio.run(resolve_unfilled(self.t.path,self.binding,None,
            clock=lambda:self.f.now,transport=self.transport))

    def assert_preserved(self):
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM demo_intent WHERE id=?',(self.client,)).fetchone())
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM demo_execution WHERE id=?',(self.client,)).fetchone())
        self.assertEqual(self.db.execute('SELECT * FROM demo_daily ORDER BY day').fetchall(),self.before_risk)
        self.assertEqual(self.db.execute('SELECT * FROM demo_fill_audit_meta').fetchall(),self.before_meta)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM demo_cycle_archive').fetchone()[0],1)

    def test_no_effect_attempt_archived_without_fake_trade_risk_refund_or_replay(self):
        result=self.resolve()
        self.assertEqual(result['phase'],'unfilled_attempt_archived_halted',result)
        self.assertTrue(result['attempt_archived']); self.assertFalse(result['new_entry_allowed'])
        self.assertEqual(result['sequence'],1)
        self.assertIsNone(self.db.execute('SELECT 1 FROM demo_intent').fetchone())
        payload=json.loads(self.db.execute('SELECT body FROM demo_no_effect_archive').fetchone()[0])
        self.assertEqual(payload['retained']['plan'],self.plan)
        self.assertEqual(verify_chain(self.db)[0],1)
        self.assertEqual(self.db.execute('SELECT * FROM demo_daily ORDER BY day').fetchall(),self.before_risk)
        self.assertEqual(self.db.execute('SELECT * FROM demo_fill_audit_meta').fetchall(),self.before_meta)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM demo_risk_entry WHERE id=?',(self.client,)).fetchone())
        self.assertEqual(self.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        with self.assertRaises(ValueError): self.t.journal.register(self.plan)
        self.assertTrue(all(r.method=='GET' for r in self.calls))
        self.assertNotIn(self.client,str(result))

    def test_lookup_found_or_unavailable_does_not_resolve(self):
        for code,data in [('0',[{'ordId':'999'}]),('50011',[]),('51603',[{}])]:
            self.lookup_code,self.lookup_data=code,data
            self.assertEqual(self.resolve()['phase'],'blocked')
            self.assert_preserved()

    def test_changed_cash_or_inventory_does_not_resolve(self):
        self.f.balance['details'][0]['cashBal']='9999'
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()

    def test_new_fill_or_bill_prevents_resolution_even_when_cash_is_unchanged(self):
        original=deepcopy(self.f.fills)
        self.f.fills.insert(0,dict(original[0],billId='999',tradeId='999',fillTime=str(self.f.now-1)))
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()
        self.f.fills=original
        self.f.bills.insert(0,dict(self.f.bills[0],billId='999',ts=str(self.f.now-1)))
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()

    def test_wrong_uid_prevents_order_queries_and_resolution(self):
        self.binding['uid']='999'
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()
        self.assertTrue(all(r.url.path.endswith('/config') for r in self.calls))

    def test_observed_order_and_too_recent_dispatch_are_not_adopted(self):
        self.db.execute("UPDATE demo_execution SET exchange_order_id='123'")
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()
        self.db.execute('UPDATE demo_execution SET exchange_order_id=NULL')
        self.db.execute('UPDATE demo_trading_clock SET dispatched_at=?',(self.f.now,))
        self.assertEqual(self.resolve()['phase'],'blocked'); self.assert_preserved()
        self.assertEqual(self.calls,[])

    def test_insert_failure_rolls_back_active_records_and_archive(self):
        # Simulate an atomic archive storage failure, after valid GET evidence.
        self.db.execute('CREATE TABLE demo_no_effect_archive (client_id TEXT PRIMARY KEY,body TEXT,hash TEXT)')
        self.db.execute("CREATE TRIGGER no_archive BEFORE INSERT ON demo_no_effect_archive BEGIN SELECT RAISE(ABORT,'private'); END")
        result=self.resolve()
        self.assertEqual(result['phase'],'blocked'); self.assert_preserved()
        self.assertIsNone(self.db.execute('SELECT 1 FROM demo_no_effect_archive').fetchone())
        self.assertNotIn('private',str(result))
