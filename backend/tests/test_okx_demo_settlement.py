"""Synthetic full-interval bill settlement; no network/account credentials."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import unittest
import test_okx_demo_close_audit as close_fixtures
import test_okx_demo_fill_audit as fill_fixtures
from okx_demo_settlement import DemoSettlementAuditor, scan
from okx_demo_fill_audit import DemoFillAuditError
from okx_demo_authority import day_key


class SettlementTests(unittest.TestCase):
    auditor_class=DemoSettlementAuditor
    setUp=fill_fixtures.FillAuditTests.setUp
    open_journal=fill_fixtures.FillAuditTests.open_journal
    owner=fill_fixtures.FillAuditTests.owner
    fill=fill_fixtures.FillAuditTests.fill
    bootstrap=fill_fixtures.FillAuditTests.bootstrap
    trade=close_fixtures.CloseAuditTests.trade

    def snapshot(self,**updates):
        return fill_fixtures.FillAuditTests.snapshot(self,cash_balance_usdt='10000',**updates)

    def prepared(self,funding='-1'):
        snapshot,close,broker,engine=self.trade()
        trade=self.audit.audit_closed(snapshot,close)
        begin=self.now
        bills=[]
        for f in snapshot['recent_fills']:
            bills.append(dict(billId=f['billId'],ts=str(begin),type='2',subType='1' if f['side']=='buy' else '2',
                ordId=f['ordId'],tradeId=f['tradeId'],instId=f['instId'],instType='SWAP',ccy='USDT',
                pnl=f['fillPnl'],fee=f['fee'],sz=f['fillSz'],px=f['fillPx'],
                balChg=str(Decimal(f['fillPnl'])+Decimal(f['fee']))))
        if Decimal(funding)!=0:
            bills.append(dict(billId='103',ts=str(begin),type='8',subType='173' if Decimal(funding)<0 else '174',
                ordId='',tradeId='',instId='BTC-USDT-SWAP',instType='SWAP',ccy='USDT',pnl=funding,fee='0',
                sz=broker.body['sz'],px='101',balChg=funding))
        self.now+=6000
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snapshot.update(received_at_ms=self.now,scan_finished_at_ms=self.now,
            cash_balance_usdt=str(Decimal('10000')+Decimal(trade['trading_net_usdt'])+Decimal(funding)))
        pages=[dict(code='0',data=sorted(bills,key=lambda b:int(b['billId']),reverse=True))]
        evidence=dict(demo_verified=True,account_scope='synthetic-v1',begin_ms=begin,through_ms=self.now,
            received_at_ms=self.now,query=dict(ccy='USDT',begin=str(begin),end=str(self.now),limit='100'),
            passes=[deepcopy(pages),deepcopy(pages)])
        return snapshot,evidence,trade,broker,engine

    def reject(self,snapshot,evidence):
        with self.assertRaises(DemoFillAuditError): self.audit.settle(snapshot,evidence)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertIsNone(self.authority.snapshot)
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_trade_settlement').fetchone())
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_risk_close').fetchone())
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_account_bill').fetchone())

    def test_funding_expense_and_both_fees_reconcile_cash_and_publish_once(self):
        snap,e,trade,_,_=self.prepared()
        result=self.audit.settle(snap,e)
        self.assertEqual(Decimal(result['net_usdt']),Decimal(trade['trading_net_usdt'])-1)
        self.assertTrue(result['funding_audited']); self.assertTrue(result['account_bills_audited'])
        self.assertFalse(result['new_entry_allowed']); self.assertFalse(result['submission_enabled'])
        self.assertEqual(self.audit.settle(snap,e),result)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        cooldown=self.journal.db.execute('SELECT cooldown FROM demo_daily WHERE day=?',(day_key(self.now),)).fetchone()[0]
        self.assertEqual(cooldown,trade['closed_at_ms']+900000)
        self.assertEqual(self.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)

    def test_funding_income_keeps_positive_sign(self):
        snap,e,trade,_,_=self.prepared('2')
        result=self.audit.settle(snap,e)
        self.assertEqual(Decimal(result['net_usdt']),Decimal(trade['trading_net_usdt'])+2)

    def test_zero_funding_requires_complete_scans_and_cash_check(self):
        snap,e,trade,_,_=self.prepared('0')
        result=self.audit.settle(snap,e)
        self.assertEqual(Decimal(result['net_usdt']),Decimal(trade['trading_net_usdt']))
        self.assertEqual(result['funding_usdt'],'0')

    def test_funding_can_turn_gross_profit_into_net_loss_and_halt_third_loss(self):
        snap,e,trade,_,_=self.prepared('-100')
        self.journal.db.execute('UPDATE demo_daily SET loss_streak=2')
        result=self.audit.settle(snap,e)
        self.assertLess(Decimal(result['net_usdt']),0)
        self.assertEqual(self.journal.db.execute('SELECT loss_streak,halt FROM demo_daily').fetchone(),(3,1))
        self.audit.settle(snap,e)
        self.assertEqual(self.journal.db.execute('SELECT loss_streak FROM demo_daily').fetchone()[0],3)

    def test_missing_bill_mismatched_fee_price_quantity_and_foreign_bill_fail_atomically(self):
        snap,e,_,_,_=self.prepared()
        for changes in (dict(fee='-99'),dict(px='9'),dict(sz='1'),dict(instId='ETH-USDT-SWAP'),dict(ordId='999')):
            bad=deepcopy(e)
            for pages in bad['passes']: pages[0]['data'][1].update(changes)
            with self.subTest(changes=changes): self.reject(snap,bad)
        bad=deepcopy(e)
        for pages in bad['passes']: pages[0]['data'].pop()
        self.reject(snap,bad)

    def test_unclassified_transfer_liquidation_currency_and_funding_sign_rejected(self):
        snap,e,_,_,_=self.prepared()
        for change in (dict(type='1'),dict(subType='100'),dict(ccy='BTC'),dict(subType='174'),dict(fee='-1'),dict(ts=str(e['begin_ms']-1))):
            bad=deepcopy(e)
            for pages in bad['passes']: pages[0]['data'][0].update(change)
            with self.subTest(change=change): self.reject(snap,bad)

    def test_cash_mismatch_and_missing_original_cash_baseline_rejected(self):
        snap,e,_,_,_=self.prepared()
        self.reject(snap | dict(cash_balance_usdt='10000'),e)
        self.journal.db.execute('DELETE FROM demo_cash_baseline')
        self.reject(snap,e)

    def test_changed_pass_duplicate_page_failed_page_and_missing_terminal_rejected(self):
        snap,e,_,_,_=self.prepared()
        bad=deepcopy(e); bad['passes'][1][0]['data'][0]['pnl']='-2'; self.reject(snap,bad)
        bad=deepcopy(e); bad['passes'][1][0]['code']='1'; self.reject(snap,bad)
        bad=deepcopy(e)
        for pages in bad['passes']: pages[0]['data'].append(deepcopy(pages[0]['data'][-1]))
        self.reject(snap,bad)
        bad=deepcopy(e); bad['passes']=bad['passes'][:1]; self.reject(snap,bad)

    def test_query_filter_wrong_account_stale_future_and_short_coverage_rejected(self):
        snap,e,_,_,_=self.prepared()
        for change in (dict(account_scope='other'),dict(demo_verified=False),dict(received_at_ms=self.now-5001),
            dict(received_at_ms=self.now+1),dict(begin_ms=e['begin_ms']-1),dict(through_ms=self.now-1),dict(query=e['query'] | dict(instType='SWAP'))):
            with self.subTest(change=change): self.reject(snap,e | change)

    def test_missing_fresh_entry_observation_and_unclosed_inventory_rejected(self):
        snap,e,_,_,_=self.prepared()
        self.reject(snap | dict(pending_algos=[dict(algoId='444')]),e)
        self.journal.db.execute('UPDATE demo_execution SET observed_at_ms=NULL')
        self.reject(snap,e)

    def test_daily_publication_failure_rolls_back_bills_and_settlement(self):
        snap,e,_,_,_=self.prepared()
        self.journal.db.execute('DELETE FROM demo_daily')
        self.reject(snap,e)

    def test_restart_retains_settlement_without_duplicate_loss_count(self):
        snap,e,_,broker,engine=self.prepared('-100')
        result=self.audit.settle(snap,e)
        self.now+=30001
        journal=self.open_journal(); owner=self.owner(journal,'second'); owner.acquire()
        auditor=DemoSettlementAuditor(owner); engine.journal=journal
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snap.update(received_at_ms=self.now,scan_finished_at_ms=self.now)
        e.update(through_ms=self.now,received_at_ms=self.now); e['query']['end']=str(self.now)
        self.assertEqual(auditor.settle(snap,e),result)
        self.assertEqual(journal.db.execute('SELECT loss_streak FROM demo_daily').fetchone()[0],1)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_risk_close').fetchone()[0],1)
        self.assertFalse(owner.allows(self.candidate))

    def test_changed_or_disappeared_previously_audited_bill_cannot_rewrite_settlement(self):
        snap,e,_,_,_=self.prepared(); result=self.audit.settle(snap,e)
        bad=deepcopy(e)
        for pages in bad['passes']: pages[0]['data'].pop(0)
        with self.assertRaises(DemoFillAuditError): self.audit.settle(snap,bad)
        self.assertEqual(json.loads(self.journal.db.execute('SELECT body FROM demo_trade_settlement').fetchone()[0]),result)

    def test_bill_pagination_requires_full_pages_then_short_terminal(self):
        raw=dict(billId='1',ts='1',type='8',subType='173',ordId='',tradeId='',instId='BTC-USDT-SWAP',
            instType='SWAP',ccy='USDT',pnl='-1',fee='0',balChg='-1',sz='1',px='100')
        rows=[raw | dict(billId=str(i)) for i in range(100,0,-1)]
        with self.assertRaises(ValueError): scan([dict(code='0',data=rows)],1,2)
        self.assertEqual(len(scan([dict(code='0',data=rows),dict(code='0',data=[])],1,2)),100)
        with self.assertRaises(ValueError): scan([dict(code='0',data=rows[:1]),dict(code='0',data=[])],1,2)

    def test_unknown_changed_and_missing_close_fill_anchor_block_settlement(self):
        snap,e,_,_,_=self.prepared()
        for fills in ([],snap['recent_fills']+[self.fill(billId='900',tradeId='900',ordId='999')],
            [snap['recent_fills'][0] | dict(fee='-9'),snap['recent_fills'][1]]):
            with self.subTest(fills=fills): self.reject(snap | dict(recent_fills=fills),e)

    def test_snapshot_reader_retains_actual_cash_separately_from_equity(self):
        from test_okx_demo_snapshot import Fixture,DemoSnapshotError
        fixture=Fixture(); fixture.balance['details'][0]['cashBal']='9990'
        snapshot=asyncio.run(fixture.reader().collect())
        self.assertEqual(snapshot['cash_balance_usdt'],'9990')
        self.assertEqual(snapshot['equity_usdt'],'10000')
        self.audit=DemoSettlementAuditor(self.authority)
        self.audit.bootstrap(snapshot,baseline_authorized=True)
        self.assertEqual(self.journal.db.execute('SELECT cash FROM demo_cash_baseline').fetchone()[0],'9990')
        fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
        def mutate(f,path,count):
            if path.endswith('/balance') and count==2: f.balance['details'][0]['cashBal']='9999'
        fixture.mutate=mutate
        with self.assertRaises(DemoSnapshotError): asyncio.run(fixture.reader().collect())

    def test_missing_cash_fails_bootstrap_without_partial_baseline(self):
        self.audit=DemoSettlementAuditor(self.authority)
        with self.assertRaises(DemoFillAuditError):
            self.audit.bootstrap(self.snapshot() | dict(cash_balance_usdt=None),baseline_authorized=True)
        for table in ('demo_fill_audit_meta','demo_close_context','demo_cash_baseline'):
            self.assertIsNone(self.journal.db.execute('SELECT 1 FROM '+table).fetchone())
