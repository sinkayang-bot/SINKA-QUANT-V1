"""Read-only evidence report for a retained execution test closed outside the worker.

Never settles, unlocks, resets or submits. A matching report is informational;
manual exit evidence does not verify the automatic exit path.
"""
import asyncio
import json
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, localcontext

from okx_demo_bill_reader import DemoBillReader
from okx_demo_fill_audit import encoded, key, money, normalize_fill
from okx_demo_settlement import scan
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import OKXDemoReadTransport


def retained_test(path, scope):
    # mode=ro and query_only prohibit ledger changes, including schema creation.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows=db.execute('SELECT i.id,i.plan,e.exchange_order_id FROM demo_intent i '
            'JOIN demo_execution e ON e.id=i.id WHERE i.dispatched=1').fetchall()
        meta=db.execute('SELECT scope,baseline_at FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
        cash=db.execute('SELECT cash FROM demo_cash_baseline WHERE singleton=1').fetchone()
        baseline={r[0]:json.loads(r[1]) for r in db.execute('SELECT identity,body FROM demo_audit_fills WHERE baseline=1')}
    if len(rows)!=1 or not meta or meta[0]!=scope or not cash:
        raise ValueError('Original single test and baseline required')
    client, raw, order_id=rows[0]; plan=json.loads(raw)
    if (plan.get('purpose')!='execution_test' or plan.get('environment')!='demo'
            or plan.get('headers')!={'x-simulated-trading':'1'}
            or plan['body'].get('clOrdId')!=client or plan['body'].get('instId')!='BTC-USDT-SWAP'
            or not order_id):
        raise ValueError('Retained acknowledged BTC demo test required')
    return dict(client=client,body=plan['body'],order_id=order_id,
                begin=meta[1],cash=money(cash[0]),baseline=baseline)


def order_row(response):
    if response.get('code')!='0' or len(response.get('data',[]))!=1:
        raise ValueError('Terminal order evidence unavailable')
    return response['data'][0]


def assess(saved, bundle, entry, closing):
    """Exact Decimal checks; unknown bills or incomplete fills stay unresolved."""
    snap=bundle['snapshot']; evidence=bundle['bills']; through=evidence['through_ms']
    body=saved['body']; symbol=body['instId']; qty=Decimal(money(body['sz'],positive_only=True))
    report=dict(ledger_changed=False,new_entry_allowed=False,automatic_exit_verified=False,
                review_only=True,as_of_ms=through)
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
    seen=set(); match=True
    with localcontext() as context:
        context.prec=512
        movement=Decimal(0)
        for row in rows.values():
            identity=row['instId']+':'+row['tradeId']; fill=active.get(identity)
            direction='buy' if row['subType'] in {'1','3','6'} else 'sell'
            if (fill is None or identity in seen or row['type']!='2' or row['subType'] not in {'1','2','3','4','5','6'}
                    or row['ordId']!=fill['ordId'] or direction!=fill['side'] or int(row['ts'])<int(fill['fillTime'])
                    or any(Decimal(row[k])!=Decimal(fill[v]) for k,v in [('sz','fillSz'),('px','fillPx'),('fee','fee'),('pnl','fillPnl')])
                    or Decimal(row['balChg'])!=Decimal(row['pnl'])+Decimal(row['fee'])):
                match=False
            seen.add(identity); movement+=Decimal(row['balChg'])
        checks['all_bills_match_fills']=match and bool(active) and seen==set(active)
        cash_delta=Decimal(money(snap['cash_balance_usdt']))-Decimal(saved['cash'])
        checks['cash_matches_bills']=cash_delta==movement
        report.update(fill_count=len(active),bill_count=len(rows),
            fees_usdt=format(sum((Decimal(f['fee']) for f in active.values()),Decimal(0)),'f'),
            realized_pnl_usdt=format(sum((Decimal(f['fillPnl']) for f in active.values()),Decimal(0)),'f'),
            bill_movement_usdt=format(movement,'f'),cash_change_usdt=format(cash_delta,'f'))
    checks['close_reporting_delay_elapsed']=bool(exited) and through>=max(int(f['fillTime']) for f in exited.values())+5000
    report.update(checks=checks,evidence_matches=all(checks.values()),
                  phase='manual_close_evidence_matches' if all(checks.values()) else 'manual_close_review_required')
    return report


async def audit_trade(path, binding, creds):
    saved=retained_test(path,binding['account_scope'])
    clock=lambda:int(time.time()*1000)
    transport=OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
    reader=DemoSnapshotReader(transport,account_scope=binding['account_scope'],expected_uid=binding['uid'],symbol=saved['body']['instId'],clock=clock)
    async with asyncio.timeout(20):
        bundle=await DemoBillReader(reader).collect(begin_ms=saved['begin'])
        entry=order_row(await transport.lookup_entry(saved['body']['instId'],saved['client']))
        exit_ids={f['ordId'] for f in bundle['snapshot']['recent_fills']
                  if key(normalize_fill(f,bundle['bills']['through_ms'])) not in saved['baseline'] and f['ordId']!=saved['order_id']}
        closing=order_row(await transport.lookup_child(saved['body']['instId'],next(iter(exit_ids)))) if len(exit_ids)==1 else None
        return assess(saved,bundle,entry,closing)
