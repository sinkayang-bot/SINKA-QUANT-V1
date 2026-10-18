"""Atomic manual test archival with synthetic full bills and existing risk state."""
from copy import deepcopy
import asyncio
import json
import unittest
from unittest.mock import patch

import test_okx_demo_settlement as fixtures
from okx_demo_cycles import CYCLE_TABLES,unresolved_emergency_exit
from okx_demo_manual_close import ManualTestLifecycle
from okx_demo_manual_review import retained_in_transaction
import okx_demo_manual_close as manual
from okx_demo_transport import DemoCredentials,OKXDemoReadTransport
from okx_strategy_bridge import plan_execution_test
from test_okx_strategy_bridge import args


class ManualCloseTests(unittest.TestCase):
    def setUp(self):
        self.t=fixtures.SettlementTests('test_zero_funding_requires_complete_scans_and_cash_check')
        self.t.auditor_class=ManualTestLifecycle
        self.t.setUp(); self.addCleanup(self.t.doCleanups)
        self.snapshot,self.bills,self.trade,self.broker,_=self.t.prepared('0')
        self.db=self.t.journal.db; self.lifecycle=self.t.audit; self.lifecycle.enabled=True
        self.db.execute('DELETE FROM demo_closed_trade')  # Fixture had audited native lineage; manual path has not.
        plan=json.loads(self.db.execute('SELECT plan FROM demo_intent').fetchone()[0]); plan['purpose']='execution_test'
        self.db.execute('UPDATE demo_intent SET plan=?',(json.dumps(plan),))
        self.saved=retained_in_transaction(self.db,self.t.authority.scope)
        self.entry=deepcopy(self.broker.row)
        self.entry.update(ordType='market',tdMode='isolated',posSide='net')
        self.closing=dict(self.entry,ordId='555',side='sell',reduceOnly=True)
        self.closing['clOrdId']=''  # Manual UI exit has no bot-generated client identity.
        self.t.authority.set_kill_switch(True)

    def records(self):
        return {table:self.db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
            for table in CYCLE_TABLES if table!='demo_audit_receipt'}

    def close(self):
        return self.lifecycle.close_reviewed_test(self.snapshot,self.bills,self.entry,self.closing,self.saved)

    def test_manual_test_archives_every_record_preserves_risk_and_stays_halted(self):
        self.db.execute('CREATE TABLE demo_emergency_exit(entry_id TEXT PRIMARY KEY,client_id TEXT UNIQUE,body TEXT,status TEXT,exchange_order_id TEXT)')
        tombstone=(self.saved['client'],'sr'+'a'*28,'{}','unknown',None)
        self.db.execute('INSERT INTO demo_emergency_exit VALUES(?,?,?,?,?)',tombstone)
        self.assertTrue(unresolved_emergency_exit(self.db))
        before=self.records(); entries=self.db.execute('SELECT * FROM demo_risk_entry').fetchall()
        result=self.close()
        self.assertEqual(result['phase'],'manual_test_closed_and_archived')
        self.assertFalse(result['automatic_exit_verified']); self.assertFalse(result['new_entry_allowed'])
        archive=json.loads(self.db.execute('SELECT body FROM demo_cycle_archive').fetchone()[0])
        self.assertEqual(archive['settlement']['closure_source'],'manual_test_close')
        self.assertFalse(archive['settlement']['strategy_performance_sample'])
        self.assertEqual(archive['records']['demo_intent']['rows'],[list(r) for r in before['demo_intent']])
        self.assertEqual(archive['records']['demo_cash_baseline']['rows'],[list(r) for r in before['demo_cash_baseline']])
        self.assertEqual(len(archive['records']['demo_account_bill']['rows']),2)
        self.assertEqual(self.db.execute('SELECT * FROM demo_risk_entry').fetchall(),entries)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertIsNone(self.db.execute('SELECT 1 FROM demo_intent').fetchone())
        self.assertEqual(self.db.execute('SELECT kill FROM demo_authority').fetchone()[0],1)
        self.assertEqual(self.lifecycle.verify_archive_chain()[0],1)
        self.assertEqual(self.db.execute('SELECT * FROM demo_emergency_exit').fetchall(),[tombstone])
        self.assertFalse(unresolved_emergency_exit(self.db))
        with self.assertRaises(ValueError): self.close()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)

    def test_failure_during_archive_rolls_back_settlement_and_risk(self):
        before=self.records()
        with patch.object(self.lifecycle,'_baseline_metadata',side_effect=ValueError('injected failure')):
            with self.assertRaises(ValueError): self.close()
        self.assertEqual(self.records(),before)
        self.assertEqual(self.lifecycle.verify_archive_chain(),(0,'',0))
        self.assertIsNone(self.db.execute('SELECT 1 FROM demo_risk_close').fetchone())

    def test_manual_gap_can_only_be_closed_with_fresh_full_original_evidence(self):
        self.t.now+=120000
        self.t.authority.acquire(); self.t.authority.set_kill_switch(True)
        self.snapshot.update(received_at_ms=self.t.now,scan_finished_at_ms=self.t.now)
        self.bills.update(through_ms=self.t.now,received_at_ms=self.t.now)
        self.bills['query']['end']=str(self.t.now)
        self.assertTrue(self.close()['ledger_archived'])

    def test_next_test_requires_current_archive_identity_and_keeps_cooldown(self):
        self.close(); boundary=self.snapshot['received_at_ms']
        self.t.authority.execution_test_enabled=True
        def proof():
            snap=deepcopy(self.snapshot); snap.update(received_at_ms=self.t.now,scan_finished_at_ms=self.t.now)
            bills=dict(demo_verified=True,account_scope=self.t.authority.scope,begin_ms=boundary,through_ms=self.t.now,
                received_at_ms=self.t.now,query=dict(ccy='USDT',begin=str(boundary),end=str(self.t.now),limit='100'),
                passes=[[dict(code='0',data=[])],[dict(code='0',data=[])]])
            self.t.authority.acquire()
            self.t.authority.accept_snapshot(self.lifecycle.audit_idle(snap,bills))
        def candidate(cycle):
            return plan_execution_test(**args(strategy_version='DEMO-EXECUTION-TEST-1',signal=None,test_cycle=cycle,
                now_ms=self.t.now,started_at_ms=boundary,quote=dict(symbol='BTC-USDT-SWAP',price='101',ts=self.t.now),
                balance_received_at_ms=self.t.now,instrument_received_at_ms=self.t.now))
        self.t.now+=1; proof(); self.t.authority.set_kill_switch(False)
        self.assertFalse(self.t.authority.allows(candidate(1)))  # Preserve the 15 minute cooldown.
        for _ in range(46):
            self.t.now+=20000; proof()
        self.t.authority.set_kill_switch(False)
        self.assertFalse(self.t.authority.allows(candidate(0)))
        self.assertFalse(self.t.authority.allows(candidate(2)))
        self.assertTrue(self.t.authority.allows(candidate(1)))
        self.assertEqual(self.db.execute('SELECT entries FROM demo_daily').fetchone()[0],1)

    def test_exchange_adapter_is_get_only_and_persists_only_after_stable_double_reads(self):
        self.db.execute('UPDATE demo_authority SET expires=0')
        calls=[]
        class ReadPort(OKXDemoReadTransport):
            async def lookup_entry(port,*args):
                calls.append('GET entry'); return dict(code='0',data=[deepcopy(self.entry)])
            async def lookup_child(port,*args):
                calls.append('GET close'); return dict(code='0',data=[deepcopy(self.closing)])
            async def read_bills(port,*args,**kwargs):
                calls.append('GET bills'); return deepcopy(self.bills['passes'][0][0])
        async def collect(reader):
            calls.append('GET snapshot'); return deepcopy(self.snapshot)
        path=self.t.journal.db.execute('PRAGMA database_list').fetchone()[2]
        from pathlib import Path
        port=ReadPort(DemoCredentials('test','test','test'),enable_demo_requests=True)
        with patch.object(manual.time,'time',return_value=self.t.now/1000),patch.object(manual,'OKXDemoReadTransport',return_value=port),patch.object(manual.DemoSnapshotReader,'collect',collect):
            result=asyncio.run(manual.close_test(Path(path),dict(account_scope=self.t.authority.scope,uid='123'),DemoCredentials('test','test','test')))
        self.assertEqual(result['orders_sent'],0)
        self.assertEqual(calls.count('GET entry'),2)
        self.assertEqual(calls.count('GET close'),2)
        self.assertEqual(calls.count('GET bills'),2)
        self.assertEqual(calls.count('GET snapshot'),2)
        self.assertEqual(self.db.execute('SELECT kill,expires FROM demo_authority').fetchone(),(1,0))

    def test_cash_changes_missing_bills_changed_entry_and_stale_proof_preserve_original(self):
        for kind in ('cash','bill','identity','stale','disabled','kill','plan'):
            with self.subTest(kind=kind):
                snap=deepcopy(self.snapshot); bills=deepcopy(self.bills); entry=deepcopy(self.entry); saved=deepcopy(self.saved)
                before=self.records()
                if kind=='cash': snap['cash_balance_usdt']='9999'
                elif kind=='bill': bills['passes'][1][0]['data'].pop()
                elif kind=='identity': entry['ordId']='99'
                elif kind=='stale': self.t.now+=6000
                elif kind=='disabled': self.lifecycle.enabled=False
                elif kind=='kill': self.t.authority.set_kill_switch(False)
                else: saved['cash']='9000'
                with self.assertRaises(ValueError):
                    self.lifecycle.close_reviewed_test(snap,bills,entry,self.closing,saved)
                self.assertEqual(self.records(),before)
                self.assertIsNone(self.db.execute('SELECT 1 FROM demo_risk_close').fetchone())
                if kind=='stale': self.t.now-=6000; self.t.authority.seen_clock=self.t.now
                self.lifecycle.enabled=True
                self.t.authority.set_kill_switch(True)
