"""Explicit GET-only reviewed-test settlement. No entry arming or order transport.

Archives the supported single test atomically with original fills, bills, cash
baseline and risk close. Separately labels manual and tracked automatic exits;
neither a manual close nor a reduction alone verifies native TP/SL protection.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import time

from okx_demo_authority import DemoEntryAuthority,day_key
from okx_demo_bill_reader import DemoBillReader
from okx_demo_close_audit import spec_digest
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import DemoExecutionJournal
from okx_demo_fill_audit import encoded, key, money
from okx_demo_manual_review import assess, order_row, retained_in_transaction
from okx_demo_settlement import scan,cash_matches
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import OKXDemoReadTransport
from okx_instruments import instrument_spec


def legacy_strategy_spec(saved,instrument):
    """Bind old entries lacking a per-entry spec to their immutable loss model.

    This is only used after exact full fills/bills/cash reconciliation, not for
    new admission or native-exit attribution. No original record is rewritten.
    """
    plan=saved['original_plan']; body=plan['body']; bracket=body['attachAlgoOrds'][0]
    spec=instrument_spec(instrument)
    if spec['symbol']!=body['instId']: raise ValueError('Legacy contract symbol mismatch')
    with localcontext() as ctx:
        ctx.prec=60
        qty=Decimal(body['sz']); stop=Decimal(bracket['slTriggerPx']); target=Decimal(bracket['tpTriggerPx'])
        anchor=(target+2*stop)/3; direction=1 if body['side']=='buy' else -1
        entry=anchor*(1+direction*Decimal('.0002')); stopped=stop*(1-direction*Decimal('.0002'))
        expected=qty*spec['ct_val']*(abs(entry-stopped)+(entry+stopped)*Decimal('.0005'))
        if expected!=Decimal(plan['modeled_loss_usdt']):
            raise ValueError('Legacy contract differs from original fee-inclusive loss model')
    return 'original_plan_loss_model_and_full_cash_fills'


class ManualCloseBlocked(RuntimeError):
    def __init__(self,stage):
        super().__init__('Manual close blocked at '+stage)
        self.stage=stage


class ManualTestLifecycle(DemoCycleLifecycle):
    def close_reviewed_test(self,snapshot,bills,entry,closing,saved):
        if not self.enabled: raise ValueError('Explicit manual close mode required')
        now=self._begin_attempt()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state=self.verify_archive_chain()
            self.authority._owner(now)
            if not self.authority._owner(now)[5]: raise ValueError('Entries must remain halted')
            if retained_in_transaction(self.db,self.authority.scope,allow_strategy=saved.get('strategy_trade') is True)!=saved:
                raise ValueError('Retained test changed during reads')
            fills=self._snapshot(snapshot,now)
            begin,through=bills['begin_ms'],bills['through_ms']
            if (bills.get('demo_verified') is not True or bills.get('account_scope')!=self.authority.scope
                    or begin!=saved['begin'] or through!=snapshot['received_at_ms']
                    or bills.get('received_at_ms')!=through or not 0<=now-through<=5000
                    or now-begin>604800000
                    or bills.get('query')!={'ccy':'USDT','begin':str(begin),'end':str(through),'limit':'100'}):
                raise ValueError('Fresh original full bill window required')
            report=assess(saved,dict(snapshot=snapshot,bills=bills),entry,closing)
            if not report['evidence_matches']: raise ValueError('Manual close does not reconcile')
            strategy=saved.get('strategy_trade') is True
            if strategy and not report['automatic_exit_verified']:
                raise ValueError('Strategy review requires its exact durable automatic reduction')
            context=self.db.execute('SELECT spec FROM demo_close_context WHERE singleton=1').fetchone()
            bound=self.db.execute('SELECT spec FROM demo_entry_context WHERE client_id=?',(saved['client'],)).fetchone()
            if bound: context=bound
            spec_source='saved_entry_specification' if bound else 'original_baseline_specification'
            if strategy and not bound:
                spec_source=legacy_strategy_spec(saved,snapshot['instrument'])
            elif not context or context[0]!=spec_digest(snapshot['instrument']):
                raise ValueError('Original contract specification changed')
            client=saved['client']
            if (not self.db.execute('SELECT 1 FROM demo_risk_entry WHERE id=?',(client,)).fetchone()
                    or self.db.execute('SELECT 1 FROM demo_closed_trade').fetchone()
                    or self.db.execute('SELECT 1 FROM demo_trade_settlement').fetchone()
                    or self.db.execute('SELECT 1 FROM demo_account_bill').fetchone()):
                raise ValueError('Unsettled consumed test reservation required')
            stored={r[0]:r[1] for r in self.db.execute('SELECT identity,body FROM demo_audit_fills')}
            if any(identity not in fills or encoded(fills[identity])!=body for identity,body in stored.items()):
                raise ValueError('Recorded fill missing or changed')
            active={k:f for k,f in fills.items() if k not in saved['baseline']}
            opened=[f for f in active.values() if f['ordId']==saved['order_id']]
            exited=[f for f in active.values() if f['ordId']==closing['ordId']]
            with localcontext() as ctx:
                ctx.prec=512
                value=lambda rows:sum((Decimal(f['fillSz'])*Decimal(f['fillPx']) for f in rows),Decimal(0))
                gross=(value(exited)-value(opened))*instrument_spec(snapshot['instrument'])['ct_val']*(1 if saved['body']['side']=='buy' else -1)
                if (any(Decimal(f['fillPnl'])!=0 for f in opened)
                        or abs(gross-Decimal(report['realized_pnl_usdt']))>Decimal('0.00000001')
                        or not cash_matches(report['cash_change_usdt'],Decimal(report['realized_pnl_usdt'])+Decimal(report['fees_usdt']))):
                    raise ValueError('Reported P&L does not match contract arithmetic')
            closed=max(int(f['fillTime']) for f in exited)
            self.authority.journal._observe_in_transaction(client,entry)
            self.db.execute('UPDATE demo_execution SET observed_at_ms=? WHERE id=?',(through,client))
            for identity,fill in fills.items():
                if identity not in stored: self._put(fill,baseline=False)
            rows=scan(bills['passes'][0],begin,through)
            for identity,row in rows.items():
                self.db.execute('INSERT INTO demo_account_bill VALUES(?,?)',(identity,encoded(row)))
            settlement=dict(client_id=client,net_usdt=report['cash_change_usdt'],funding_usdt='0',
                cash_before_usdt=saved['cash'],cash_after_usdt=money(snapshot['cash_balance_usdt']),
                bill_ids=sorted(rows,key=int),closed_at_ms=closed,reconciled_through_ms=through,
                closure_source='tracked_emergency_exit' if report['automatic_exit_verified'] else 'manual_test_close',
                automatic_exit_verified=report['automatic_exit_verified'],native_protection_verified=report['native_protection_verified'],
                test_roundtrip_verified=report['test_roundtrip_verified'],strategy_performance_sample=False,
                instrument_context_source=spec_source,strategy_trade=strategy,
                cash_bill_rounding_delta_usdt=report['cash_bill_rounding_delta_usdt'],
                cash_rounding_tolerance_usdt=report['cash_rounding_tolerance_usdt'],
                account_bills_audited=True,funding_audited=True,new_entry_allowed=False,submission_enabled=False,
                close_evidence=dict(entry=entry,closing=closing,report=report,snapshot=snapshot,bills=bills,reduction_record=saved.get('reduction')))
            trade=dict(settlement,entry_order_id=saved['order_id'],child_order_id=closing['ordId'],
                symbol=saved['body']['instId'],fill_ids=sorted(active))
            self.db.execute('INSERT INTO demo_closed_trade VALUES(?,?,?)',(client,closing['ordId'],encoded(trade)))
            self.db.execute('INSERT INTO demo_trade_settlement VALUES(?,?)',(client,encoded(settlement)))
            self.authority._record_close_in_transaction('settled:'+client,pnl_usdt=settlement['net_usdt'],
                closed_at_ms=closed,audit_verified=True,allow_historical=True)
            result=self._archive_settled_in_transaction(snapshot,bills,now,settlement,state)
            finished=self.authority._now()
            daily=self.db.execute('SELECT loss_streak,halt,cooldown FROM demo_daily WHERE day=?',(day_key(closed),)).fetchone()
            cooldown=max(0,daily[2]-finished+999)//1000
            self.db.execute('COMMIT')
            return dict(phase='tracked_strategy_exit_closed_and_archived' if strategy else ('tracked_test_exit_closed_and_archived' if report['automatic_exit_verified'] else 'manual_test_closed_and_archived'),sequence=result['sequence'],
                ledger_archived=True,daily_risk_preserved=True,new_entry_allowed=False,
                risk_day=day_key(closed),
                cooldown_remaining_seconds=cooldown,risk_halted=bool(daily[0]>=3 or daily[1]),
                automatic_exit_verified=report['automatic_exit_verified'],native_protection_verified=report['native_protection_verified'],
                test_roundtrip_verified=report['test_roundtrip_verified'],strategy_trade=strategy,
                instrument_context_source=spec_source,strategy_performance_sample=False,
                cash_bill_rounding_delta_usdt=report['cash_bill_rounding_delta_usdt'],
                cash_rounding_tolerance_usdt=report['cash_rounding_tolerance_usdt'],
                orders_sent=0,net_usdt=settlement['net_usdt'])
        except Exception:
            self.db.execute('ROLLBACK')
            raise


async def close_test(path,binding,creds,*,allow_strategy=False):
    if not path.is_file() or path.is_symlink(): raise ValueError('Original ledger required')
    clock=lambda:int(time.time()*1000)
    journal=DemoExecutionJournal(path)
    owner=None
    stage='ledger_lease'
    try:
        owner=DemoEntryAuthority(journal,account_scope=binding['account_scope'],worker_id='manual-test-close',clock=clock)
        lifecycle=ManualTestLifecycle(owner,enable_cycle_transition=True)
        if not owner.acquire(): raise ValueError('Another worker owns ledger')
        owner.set_kill_switch(True)
        stage='retained_test'
        saved=retained_in_transaction(journal.db,owner.scope,allow_strategy=allow_strategy)
        transport=OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
        reader=DemoSnapshotReader(transport,account_scope=owner.scope,expected_uid=binding['uid'],symbol=saved['body']['instId'],clock=clock)
        start=clock()
        stage='composite_exchange_reads'
        async with asyncio.timeout(5):
            before=await reader.collect(); through=before['received_at_ms']
            exit_ids={f['ordId'] for f in before['recent_fills'] if key(f) not in saved['baseline'] and f['ordId']!=saved['order_id']}
            if len(exit_ids)!=1: raise ValueError('One full closing order required')
            exit_id=next(iter(exit_ids)); bill_reader=DemoBillReader(reader)
            entry=order_row(await transport.lookup_entry(reader.symbol,saved['client']))
            closing=order_row(await transport.lookup_child(reader.symbol,exit_id))
            first=await bill_reader._pages(saved['begin'],through)
            entry2=order_row(await transport.lookup_entry(reader.symbol,saved['client']))
            closing2=order_row(await transport.lookup_child(reader.symbol,exit_id))
            second=await bill_reader._pages(saved['begin'],through)
            after=await reader.collect()
        now=clock()
        stage='composite_consistency'
        values=lambda s:{k:v for k,v in s.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
        if (now-start>5000 or values(before)!=values(after) or entry!=entry2 or closing!=closing2
                or scan(first,saved['begin'],through)!=scan(second,saved['begin'],through)):
            raise ValueError('Composite manual close reads changed')
        snapshot=deepcopy(before); snapshot['scan_finished_at_ms']=now
        bills=dict(demo_verified=True,account_scope=owner.scope,begin_ms=saved['begin'],through_ms=through,
            received_at_ms=through,query=dict(ccy='USDT',begin=str(saved['begin']),end=str(through),limit='100'),passes=[first,second])
        stage='atomic_settlement_validation'
        return lifecycle.close_reviewed_test(snapshot,bills,entry,closing,saved)
    except Exception:
        raise ManualCloseBlocked(stage) from None
    finally:
        # Release only this owner's lease, keep the kill switch and all history.
        try:
            if owner is not None:
                try: owner.release()
                except Exception: pass  # Cleanup must not mask a committed close.
        finally:
            journal.close()
