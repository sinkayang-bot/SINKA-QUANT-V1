"""Synthetic close lineage/accounting only. No real account requests."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import unittest
import test_okx_demo_fill_audit as fixtures
from okx_demo_close_audit import DemoClosedTradeAuditor
from okx_demo_fill_audit import DemoFillAuditError
from okx_demo_executor import DemoExecutor
from test_okx_demo_executor import FakeExchange
from test_okx_strategy_bridge import args


class CloseAuditTests(unittest.TestCase):
    setUp=fixtures.FillAuditTests.setUp
    open_journal=fixtures.FillAuditTests.open_journal
    owner=fixtures.FillAuditTests.owner
    snapshot=fixtures.FillAuditTests.snapshot
    fill=fixtures.FillAuditTests.fill
    bootstrap=fixtures.FillAuditTests.bootstrap

    def trade(self, short=False):
        self.audit=getattr(self,"auditor_class",DemoClosedTradeAuditor)(self.authority); self.bootstrap()
        broker=FakeExchange()
        engine=DemoExecutor(self.journal,broker,clock=lambda:self.now,
            entry_gate=self.authority.allows,recovery_gate=lambda b:True,enabled=True)
        source=args()
        if short: source['signal'].update(side=-1,stop='102.4')
        del source['now_ms']
        self.assertEqual(asyncio.run(engine.enter(source))['phase'],'entry_acknowledged_query_required')
        q=broker.body['sz']; broker.row.update(state='filled',accFillSz=q)
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        bracket=broker.body['attachAlgoOrds'][0]; gross=Decimal(q)*Decimal('0.01')
        side='sell' if short else 'buy'; opposite='buy' if short else 'sell'
        entry=self.fill(fillSz=q,fee='-0.1',side=side)
        exit=self.fill(billId='102',tradeId='202',ordId='555',side=opposite,fillSz=q,
            fillPx='100' if short else '102',fillPnl=str(gross),fee='-0.2')
        algo=dict(algoId='444',algoClOrdId=bracket['attachAlgoClOrdId'],instId='BTC-USDT-SWAP',
            instType='SWAP',state='effective',ordType='oco',side=opposite,posSide='net',tdMode='isolated',
            reduceOnly='true',sz=q,ordIdList=['555'],subAlgoIdList=[],closeFraction='') | {
                k:v for k,v in bracket.items() if k!='attachAlgoClOrdId'}
        child=dict(ordId='555',clOrdId='',algoId='444',algoClOrdId=algo['algoClOrdId'],instId='BTC-USDT-SWAP',
            state='filled',ordType='market',side=opposite,posSide='net',tdMode='isolated',reduceOnly=True,sz=q,accFillSz=q)
        evidence=dict(demo_verified=True,account_scope='synthetic-v1',
            algo=dict(complete=True,received_at_ms=self.now,rows=[algo]),
            child=dict(complete=True,received_at_ms=self.now,rows=[child]))
        return self.snapshot(recent_fills=[entry,exit]),evidence,broker,engine

    def reject(self,snapshot,evidence):
        with self.assertRaises(DemoFillAuditError): self.audit.audit_closed(snapshot,evidence)
        self.assertIsNone(self.authority.snapshot)
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_closed_trade').fetchone())

    def test_long_close_deducts_both_fees_without_entry_or_daily_risk_release(self):
        snap,e,broker,_=self.trade(); result=self.audit.audit_closed(snap,e)
        gross=Decimal(broker.body['sz'])*Decimal('0.01')
        self.assertEqual(Decimal(result['gross_usdt']),gross)
        self.assertEqual(Decimal(result['signed_fees_usdt']),Decimal('-0.3'))
        self.assertEqual(Decimal(result['trading_net_usdt']),gross-Decimal('0.3'))
        for flag in ('new_entry_allowed','funding_audited','submission_enabled','account_bills_audited'):
            self.assertFalse(result[flag])
        self.assertFalse(self.authority.allows(self.candidate))
        self.assertEqual(self.journal.db.execute('SELECT valid FROM demo_audit_receipt').fetchone()[0],0)
        self.assertIsNone(self.journal.db.execute('SELECT 1 FROM demo_risk_close').fetchone())
        other=self.candidate['plan'] | dict(body=self.candidate['plan']['body'] | dict(clOrdId='sk'+'0'*28))
        with self.assertRaises(ValueError): self.journal.register(other)

    def test_idempotent_close_preserves_rebate_sign(self):
        snap,e,_,_=self.trade(); snap['recent_fills'][1]['fee']='0.2'
        result=self.audit.audit_closed(snap,e)
        self.assertEqual(Decimal(result['signed_fees_usdt']),Decimal('0.1'))
        self.assertEqual(self.audit.audit_closed(snap,e),result)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_closed_trade').fetchone()[0],1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],2)

    def test_parent_child_mismatch_split_and_spawned_algo_rejected(self):
        snap,e,_,_=self.trade()
        for name,change in (('algo',dict(algoClOrdId='foreign')),('algo',dict(ordIdList=['556'])),
            ('algo',dict(ordIdList=['555','556'])),('algo',dict(subAlgoIdList=['777'])),
            ('child',dict(algoId='445')),('child',dict(algoClOrdId='foreign')),('child',dict(ordId='12345'))):
            bad=deepcopy(e); bad[name]['rows'][0].update(change)
            with self.subTest(name=name,change=change): self.reject(snap,bad)

    def test_partial_nonreducing_failed_and_untriggered_details_rejected(self):
        snap,e,_,_=self.trade()
        for name,change in (('algo',dict(state='live')),('algo',dict(state='order_failed')),
            ('algo',dict(reduceOnly=1)),('algo',dict(sz='1')),('algo',dict(ordType='conditional')),
            ('child',dict(reduceOnly=False)),('child',dict(state='partially_filled')),
            ('child',dict(accFillSz='1')),('child',dict(side='buy')),('child',dict(tdMode='cross'))):
            bad=deepcopy(e); bad[name]['rows'][0].update(change)
            with self.subTest(name=name,change=change): self.reject(snap,bad)

    def test_stop_target_and_trigger_type_changes_rejected(self):
        snap,e,_,_=self.trade()
        for field,value in (('slTriggerPx','1'),('tpTriggerPx','100000'),('slOrdPx','2'),('tpTriggerPxType','mark')):
            bad=deepcopy(e); bad['algo']['rows'][0][field]=value
            with self.subTest(field=field): self.reject(snap,bad)

    def test_stale_wrong_account_incomplete_and_ambiguous_evidence_rejected(self):
        snap,e,_,_=self.trade()
        for change in (dict(demo_verified=False),dict(account_scope='other')): self.reject(snap,e | change)
        for name in ('algo','child'):
            for change in (dict(received_at_ms=self.now-1),dict(received_at_ms=self.now+1),dict(complete=False),dict(rows=[])):
                bad=deepcopy(e); bad[name].update(change)
                with self.subTest(name=name,change=change): self.reject(snap,bad)

    def test_missing_extra_unknown_and_inconsistent_pnl_fills_roll_back(self):
        snap,e,_,_=self.trade()
        for fills in (snap['recent_fills'][:1],snap['recent_fills'][1:],
            [snap['recent_fills'][0],snap['recent_fills'][1] | dict(fillSz='1')],
            [snap['recent_fills'][0],snap['recent_fills'][1] | dict(ordId='999')],
            [snap['recent_fills'][0],snap['recent_fills'][1] | dict(fillPnl='999')]):
            with self.subTest(fills=fills): self.reject(snap | dict(recent_fills=fills),e)
            self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM demo_audit_fills').fetchone()[0],0)

    def test_exposure_pending_cleanup_and_contract_metadata_changes_rejected(self):
        snap,e,_,_=self.trade()
        for change in (dict(positions=[dict(pos='1')]),dict(pending_orders=[dict(ordId='123')]),
            dict(pending_algos=[dict(algoId='444')]),dict(instrument=snap['instrument'] | dict(ctVal='0.02'))):
            with self.subTest(change=change): self.reject(snap | change,e)

    def test_exit_before_entry_and_partial_entry_cancel_rejected(self):
        snap,e,broker,_=self.trade()
        wrong=deepcopy(snap); wrong['recent_fills'][1]['fillTime']=str(self.now-1)
        self.reject(wrong,e)
        row=broker.row | dict(state='canceled',accFillSz='1')
        self.journal.db.execute('UPDATE demo_intent SET snapshot=?',(json.dumps(row),))
        self.reject(snap,e)

    def test_restart_keeps_closed_trade_without_duplicate_or_unlock(self):
        snap,e,broker,engine=self.trade(); result=self.audit.audit_closed(snap,e)
        self.now+=30001; journal=self.open_journal(); owner=self.owner(journal,'second'); owner.acquire()
        auditor=DemoClosedTradeAuditor(owner); engine.journal=journal
        asyncio.run(engine.refresh(broker.body['clOrdId']))
        snap.update(received_at_ms=self.now,scan_finished_at_ms=self.now)
        for name in ('algo','child'): e[name]['received_at_ms']=self.now
        self.assertEqual(auditor.audit_closed(snap,e),result)
        self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM demo_closed_trade').fetchone()[0],1)
        self.assertFalse(owner.allows(self.candidate))

    def test_completed_fill_cannot_be_rewritten(self):
        snap,e,_,_=self.trade(); self.audit.audit_closed(snap,e)
        snap['recent_fills'][1]['fee']='-999'
        with self.assertRaises(DemoFillAuditError): self.audit.audit_closed(snap,e)
        self.assertNotIn('-999',self.journal.db.execute('SELECT body FROM demo_closed_trade').fetchone()[0])

    def test_old_baseline_cannot_retroactively_adopt_current_contract_metadata(self):
        self.bootstrap(); self.audit=DemoClosedTradeAuditor(self.authority)
        with self.assertRaises(DemoFillAuditError): self.audit.audit_closed(self.snapshot(),{})

    def test_short_profit_and_long_loss_have_correct_direction_and_fees(self):
        snap,e,broker,_=self.trade(short=True)
        result=self.audit.audit_closed(snap,e)
        self.assertGreater(Decimal(result['gross_usdt']),0)
        self.assertEqual(Decimal(result['trading_net_usdt']),Decimal(broker.body['sz'])*Decimal('0.01')-Decimal('0.3'))

    def test_losing_long_close_is_not_sign_flipped(self):
        snap,e,broker,_=self.trade()
        snap['recent_fills'][1].update(fillPx='100',fillPnl=str(-Decimal(broker.body['sz'])*Decimal('0.01')))
        result=self.audit.audit_closed(snap,e)
        self.assertLess(Decimal(result['gross_usdt']),0)
        self.assertLess(Decimal(result['trading_net_usdt']),Decimal(result['gross_usdt']))

    def test_split_fills_use_weighted_values_and_sum_all_fees_once(self):
        snap,e,broker,_=self.trade()
        q=Decimal(broker.body['sz']); half=q/2
        snap['recent_fills']=[self.fill(fillSz=str(half),fillPx='100',fee='-0.1'),
            self.fill(billId='102',tradeId='202',fillSz=str(half),fillPx='102',fee='-0.1'),
            self.fill(billId='103',tradeId='203',ordId='555',side='sell',fillSz=str(half),fillPx='102',fee='-0.1',fillPnl=str(q*Decimal('0.005'))),
            self.fill(billId='104',tradeId='204',ordId='555',side='sell',fillSz=str(half),fillPx='104',fee='-0.1',fillPnl=str(q*Decimal('0.015')))]
        result=self.audit.audit_closed(snap,e)
        self.assertEqual(Decimal(result['gross_usdt']),q*Decimal('0.02'))
        self.assertEqual(Decimal(result['signed_fees_usdt']),Decimal('-0.4'))
        self.assertEqual(len(result['fill_ids']),4)

    def test_stale_individual_entry_lookup_never_accepts_flat_account_alone(self):
        snap,e,_,_=self.trade()
        self.journal.db.execute('UPDATE demo_execution SET observed_at_ms=?',(self.now-1,))
        self.reject(snap,e)
