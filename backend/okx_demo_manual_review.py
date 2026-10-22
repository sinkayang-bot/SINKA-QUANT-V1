"""Read-only evidence report for a retained test closed manually or by reduction.

Never settles, unlocks, resets or submits. A matching report is informational;
An automatic reduction must match its durable reservation and complete fills;
historical algo details never verify live protection before the exit.
"""
import asyncio
import json
import re
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, localcontext

from okx_demo_bill_reader import DemoBillReader
from okx_demo_fill_audit import encoded, key, money, normalize_fill
from okx_demo_settlement import scan,cash_matches,CASH_ROUNDING_TOLERANCE
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import OKXDemoReadTransport
from okx_demo_protection import _plan_body
from okx_demo_read_errors import failure_kind
from okx_demo_baseline_history import recover_baseline_history,with_baseline_history


class TradeReviewBlocked(RuntimeError):
    def __init__(self,stage,error):
        super().__init__('Trade review read failed')
        if stage not in {'retained_ledger','reader_setup','baseline_history','bill_bundle','entry_lookup','exit_selection','exit_lookup','assessment','protection_detail'}:
            stage='unknown'
        self.stage=stage;self.kind=failure_kind(error)
        self.diagnostics={'stage':stage,'kind':self.kind}
        read_stage=getattr(error,'stage',None)
        if isinstance(read_stage,str) and read_stage in {'bill_window','snapshot_before','bills_first_pass','bills_second_pass','snapshot_after','bill_consistency'}:
            self.diagnostics['read_stage']=read_stage
        snapshot_stage=getattr(error,'snapshot_stage',None)
        if isinstance(snapshot_stage,str) and snapshot_stage in {'snapshot_start','account_config','inventory_first_pass','inventory_second_pass','snapshot_consistency'}:
            self.diagnostics['snapshot_stage']=snapshot_stage


def retained_test(path, scope,*,allow_strategy=False):
    # mode=ro and query_only prohibit ledger changes, including schema creation.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return retained_in_transaction(db,scope,allow_strategy=allow_strategy)


def retained_in_transaction(db,scope,*,allow_strategy=False):
    rows=db.execute('SELECT i.id,i.plan,e.exchange_order_id FROM demo_intent i '
        'JOIN demo_execution e ON e.id=i.id WHERE i.dispatched=1').fetchall()
    meta=db.execute('SELECT scope,baseline_at FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
    cash=db.execute('SELECT cash FROM demo_cash_baseline WHERE singleton=1').fetchone()
    baseline={r[0]:json.loads(r[1]) for r in db.execute('SELECT identity,body FROM demo_audit_fills WHERE baseline=1')}
    if len(rows)!=1 or not meta or meta[0]!=scope or not cash:
        raise ValueError('Original single test and baseline required')
    client, raw, order_id=rows[0]; plan=json.loads(raw)
    strategy=plan.get('purpose') is None and allow_strategy is True
    if strategy:
        _plan_body(plan)
        if not db.execute('SELECT 1 FROM demo_risk_entry WHERE id=?',(client,)).fetchone():
            raise ValueError('Consumed strategy reservation required')
    if ((not strategy and plan.get('purpose')!='execution_test') or plan.get('environment')!='demo'
            or plan.get('headers')!={'x-simulated-trading':'1'}
            or plan['body'].get('clOrdId')!=client or (not strategy and plan['body'].get('instId')!='BTC-USDT-SWAP')
            or not order_id):
        raise ValueError('Retained acknowledged BTC demo test required')
    result=dict(client=client,body=plan['body'],order_id=order_id,
                begin=meta[1],cash=money(cash[0]),baseline=baseline)
    if strategy: result.update(strategy_trade=True,original_plan=plan)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_emergency_exit'").fetchone():
        reduction=db.execute('SELECT client_id,body,exchange_order_id FROM demo_emergency_exit WHERE entry_id=?',(client,)).fetchone()
        if reduction:
            result['reduction']=dict(client_id=reduction[0],body=json.loads(reduction[1]),order_id=reduction[2])
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_protection_trace'").fetchone():
        proof=[]
        for at,raw in db.execute('SELECT observed_at,body FROM demo_protection_trace WHERE entry_id=? ORDER BY id',(client,)):
            observation=json.loads(raw); shape=observation.get('pending_protection',{})
            matches=shape.get('matches',{})
            if (observation.get('assessment_reason')=='open_position_protected' and observation.get('pending_algo_count')==1
                    and shape.get('historical_only') is False and shape.get('live_protection_verified') is True
                    and shape.get('state')=='live' and shape.get('order_type')=='oco'
                    and matches and all(v is True for v in matches.values())):
                proof.append(at)
        if proof: result['native_protection_observed_at_ms']=proof[0]
    return result


def order_row(response):
    if response.get('code')!='0' or len(response.get('data',[]))!=1:
        raise ValueError('Terminal order evidence unavailable')
    return response['data'][0]


def assess(saved, bundle, entry, closing):
    """Exact Decimal checks; unknown bills or incomplete fills stay unresolved."""
    snap=bundle['snapshot']; evidence=bundle['bills']; through=evidence['through_ms']
    body=saved['body']; symbol=body['instId']; qty=Decimal(money(body['sz'],positive_only=True))
    report=dict(ledger_changed=False,new_entry_allowed=False,automatic_exit_verified=False,
                native_protection_verified=False,test_roundtrip_verified=False,review_only=True,as_of_ms=through)
    checks={}
    checks['flat_clean']=not snap['pending_orders'] and not snap['pending_algos'] and all(Decimal(p['pos'])==0 for p in snap['positions'])
    fills={}
    for raw in snap['recent_fills']:
        fill=normalize_fill(raw,through); identity=key(fill)
        if identity in fills: raise ValueError('Duplicate fill')
        fills[identity]=fill
    checks['baseline_fills_retained']=all(k in fills and encoded(fills[k])==encoded(v) for k,v in saved['baseline'].items())
    active={k:f for k,f in fills.items() if k not in saved['baseline']}
    opened={k:f for k,f in active.items() if f['ordId']==saved['order_id']}
    exited={k:f for k,f in active.items() if f['ordId']!=saved['order_id']}
    opposite='sell' if body['side']=='buy' else 'buy'
    checks['entry_order_matches']=(entry.get('ordId')==saved['order_id'] and entry.get('clOrdId')==saved['client']
        and all(entry.get(k)==v for k,v in dict(instId=symbol,side=body['side'],tdMode='isolated',posSide='net',state='filled',ordType='market').items())
        and Decimal(money(entry.get('sz')))==qty and Decimal(money(entry.get('accFillSz')))==qty)
    checks['one_full_entry_and_exit']=(bool(opened) and bool(exited)
        and len({f['ordId'] for f in exited.values()})==1
        and all(f['instId']==symbol and int(f['fillTime'])>=saved['begin'] for f in active.values())
        and all(f['side']==body['side'] for f in opened.values()) and all(f['side']==opposite for f in exited.values())
        and sum((Decimal(f['fillSz']) for f in opened.values()),Decimal(0))==qty
        and sum((Decimal(f['fillSz']) for f in exited.values()),Decimal(0))==qty
        and max(int(f['fillTime']) for f in opened.values())<=min(int(f['fillTime']) for f in exited.values()))
    checks['closing_order_matches']=(bool(exited) and closing is not None
        and {f['ordId'] for f in exited.values()}=={closing.get('ordId')}
        and all(closing.get(k)==v for k,v in dict(instId=symbol,side=opposite,tdMode='isolated',posSide='net',state='filled',ordType='market').items())
        and closing.get('reduceOnly') in (True,'true')
        and Decimal(money(closing.get('sz')))==qty and Decimal(money(closing.get('accFillSz')))==qty)
    rows=scan(evidence['passes'][0],saved['begin'],through)
    if rows!=scan(evidence['passes'][1],saved['begin'],through): raise ValueError('Bill passes changed')
    seen=set(); mismatches={}
    def mismatch(label):
        mismatches[label]=mismatches.get(label,0)+1
    with localcontext() as context:
        context.prec=512
        movement=Decimal(0); position_movement=Decimal(0); transfer_rows=0
        for row in rows.values():
            identity=row['instId']+':'+row['tradeId']; fill=active.get(identity)
            direction='buy' if row['subType'] in {'1','3','6'} else 'sell'
            if fill is None: mismatch('bill_without_matching_fill')
            if identity in seen: mismatch('duplicate_trade_bill')
            if row['type']!='2': mismatch('unsupported_bill_type')
            if row['subType'] not in {'1','2','3','4','5','6'}: mismatch('unsupported_bill_subtype')
            if fill is not None:
                if row['ordId']!=fill['ordId']: mismatch('order_identity')
                if direction!=fill['side']: mismatch('side')
                if int(row['ts'])<int(fill['fillTime']): mismatch('bill_timestamp_before_fill')
                for field,other in [('sz','fillSz'),('px','fillPx'),('fee','fee'),('pnl','fillPnl')]:
                    if Decimal(row[field])!=Decimal(fill[other]): mismatch(field)
            position_delta=Decimal(row.get('posBalChg','0'))
            if Decimal(row['balChg'])+position_delta!=Decimal(row['pnl'])+Decimal(row['fee']):
                mismatch('balance_change_equation')
            if position_delta!=0: transfer_rows+=1
            position_movement+=position_delta
            seen.add(identity); movement+=Decimal(row['balChg'])
        if set(active)-seen: mismatches['fills_without_bill']=len(set(active)-seen)
        if not active: mismatch('no_trade_fills')
        checks['all_bills_match_fills']=not mismatches
        # For a complete flat-to-flat round trip, no cash may remain in the position.
        checks['position_cash_returns_to_zero']=position_movement==0
        report['bill_fill_mismatches']=mismatches
        cash_delta=Decimal(money(snap['cash_balance_usdt']))-Decimal(saved['cash'])
        checks['cash_matches_bills']=cash_matches(cash_delta,movement)
        report['cash_bill_rounding_delta_usdt']=format(cash_delta-movement,'f')
        report['cash_rounding_tolerance_usdt']=format(CASH_ROUNDING_TOLERANCE,'f')
        report.update(fill_count=len(active),bill_count=len(rows),
            position_cash_change_usdt=format(position_movement,'f'),position_transfer_bill_count=transfer_rows,
            fees_usdt=format(sum((Decimal(f['fee']) for f in active.values()),Decimal(0)),'f'),
            realized_pnl_usdt=format(sum((Decimal(f['fillPnl']) for f in active.values()),Decimal(0)),'f'),
            bill_movement_usdt=format(movement,'f'),cash_change_usdt=format(cash_delta,'f'))
    checks['close_reporting_delay_elapsed']=bool(exited) and through>=max(int(f['fillTime']) for f in exited.values())+5000
    report.update(checks=checks,evidence_matches=all(checks.values()),
                  phase='manual_close_evidence_matches' if all(checks.values()) else 'manual_close_review_required')
    reduction=saved.get('reduction')
    tracked=False
    if reduction and closing is not None:
        expected=reduction['body']
        tracked=(closing.get('clOrdId')==reduction['client_id']
            and (reduction['order_id'] is None or closing.get('ordId')==reduction['order_id'])
            and expected.get('clOrdId')==reduction['client_id']
            and expected.get('reduceOnly') is True
            and all(closing.get(k)==expected.get(k) for k in ('instId','tdMode','posSide','side','ordType'))
            and Decimal(money(closing.get('sz')))==Decimal(money(expected.get('sz'))))
    report['closure_source']='tracked_emergency_exit' if tracked else 'manual_or_untracked_close'
    report['automatic_exit_verified']=tracked and report['evidence_matches']
    report['native_protection_verified']=type(saved.get('native_protection_observed_at_ms')) is int and report['evidence_matches']
    report['test_roundtrip_verified']=report['automatic_exit_verified'] and report['native_protection_verified']
    if saved.get('strategy_trade') is True:
        report['reduction_roundtrip_verified']=report['test_roundtrip_verified']
        report['test_roundtrip_verified']=False
    if tracked:
        report['phase']='tracked_exit_evidence_matches' if report['evidence_matches'] else 'tracked_exit_review_required'
    return report


def protection_detail(saved,response):
    """Historical algo shape only; never a proof of live protection before exit."""
    code=response.get('code')
    result=dict(api_code=code if isinstance(code,str) and re.fullmatch(r'[0-9]{1,8}',code) else 'unknown',
                historical_only=True,live_protection_verified=False)
    rows=response.get('data',[])
    if code!='0' or not isinstance(rows,list) or len(rows)!=1:
        result['detail_available']=False; return result
    row=rows[0]; body=saved['body']; bracket=body['attachAlgoOrds'][0]
    def same_number(field,expected):
        try: return Decimal(money(row.get(field)))==Decimal(money(expected))
        except (ValueError,TypeError): return False
    result.update(detail_available=True,
        state=row.get('state') if row.get('state') in {'live','pause','canceled','effective','order_failed','partially_failed','partially_effective'} else 'other',
        order_type=row.get('ordType') if row.get('ordType') in {'oco','conditional','trigger','move_order_stop','chase'} else 'other',
        missing_fields=[k for k in ('algoClOrdId','instType','posSide','tdMode','side','sz','reduceOnly','tpTriggerPx','slTriggerPx','tpOrdPx','slOrdPx','tpTriggerPxType','slTriggerPxType') if row.get(k) in (None,'')],
        matches=dict(client_identity=row.get('algoClOrdId')==bracket['attachAlgoClOrdId'],
            instrument=row.get('instId')==body['instId'],instrument_type=row.get('instType')=='SWAP',
            position_side=row.get('posSide')=='net',margin_mode=row.get('tdMode')=='isolated',
            side=row.get('side')==('sell' if body['side']=='buy' else 'buy'),
            reduce_only=row.get('reduceOnly') in (True,'true'),
            fixed_size=same_number('sz',body['sz']),close_fraction_empty=row.get('closeFraction','')=='',
            **{k:(same_number(k,bracket[k]) if k in {'tpTriggerPx','slTriggerPx'} else row.get(k)==bracket[k])
                for k in ('tpTriggerPx','slTriggerPx','tpOrdPx','slOrdPx','tpTriggerPxType','slTriggerPxType')}))
    return result


async def _audit_trade_once(path,binding,creds):
    stage='retained_ledger'
    try:
        saved=retained_test(path,binding['account_scope'],allow_strategy=True)
        stage='reader_setup'
        clock=lambda:int(time.time()*1000)
        transport=OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
        reader=DemoSnapshotReader(transport,account_scope=binding['account_scope'],expected_uid=binding['uid'],symbol=saved['body']['instId'],clock=clock)
        stage='baseline_history'
        # Verify account identity before historical account reads; then obtain a
        # fresh complete bill bundle so history acquisition cannot age its proof.
        history=(await recover_baseline_history(saved,await reader.collect(),transport,clock())
                 if saved['baseline'] else {})
        async with asyncio.timeout(20):
            stage='bill_bundle'
            bundle=await DemoBillReader(reader).collect(begin_ms=saved['begin'])
            bundle['snapshot']=with_baseline_history(bundle['snapshot'],saved,history,clock())
            stage='entry_lookup'
            entry=order_row(await transport.lookup_entry(saved['body']['instId'],saved['client']))
            stage='exit_selection'
            exit_ids={f['ordId'] for f in bundle['snapshot']['recent_fills']
                      if key(normalize_fill(f,bundle['bills']['through_ms'])) not in saved['baseline'] and f['ordId']!=saved['order_id']}
            stage='exit_lookup'
            closing=order_row(await transport.lookup_child(saved['body']['instId'],next(iter(exit_ids)))) if len(exit_ids)==1 else None
            stage='assessment'
            report=assess(saved,bundle,entry,closing)
            report['baseline_history_recovered_count']=len(history)
            report['baseline_history_read_only']=True
        # Optional historical shape cannot invalidate an already complete audit.
        stage='protection_detail'
        try:
            async with asyncio.timeout(4):
                attached=saved['body']['attachAlgoOrds'][0]['attachAlgoClOrdId']
                first=await transport.lookup_algo(attached)
                second=await transport.lookup_algo(attached)
            report['protection_detail']=protection_detail(saved,first) if first==second else dict(detail_available=False,reason='detail_changed',historical_only=True,live_protection_verified=False)
        except Exception:
            report['protection_detail']=dict(detail_available=False,reason='detail_read_unavailable',historical_only=True,live_protection_verified=False)
        return report
    except Exception as error:
        raise TradeReviewBlocked(stage,error) from None


async def audit_trade(path,binding,creds):
    # Only read operations are retried. Fresh full evidence is required on each
    # attempt; neither ledger timestamps nor the five-second window is extended.
    failures=[]
    for attempt in range(1,4):
        try:
            report=await _audit_trade_once(path,binding,creds)
            return report|{'read_attempts':attempt,'prior_read_failures':failures}
        except TradeReviewBlocked as error:
            failures.append(dict(error.diagnostics))
            if attempt==3 or error.stage=='retained_ledger' or error.kind not in {'timeout','transport_failure','unavailable'}:
                error.diagnostics['read_attempts']=attempt
                raise
            await asyncio.sleep(1)
