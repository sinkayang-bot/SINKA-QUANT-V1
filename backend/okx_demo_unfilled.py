"""Explicit GET-only review of an unknown, unacknowledged demo attempt.

Archives evidence of no effects AS OF the review cutoff, never a rejection
claim. Does not resend, refund risk reservations, reset a baseline, create a
closed trade, or enable entries. The old client ID remains permanently fenced.
"""
import asyncio
from copy import deepcopy
import json
import time

from okx_demo_authority import DemoEntryAuthority
from okx_demo_baseline_history import recover_baseline_history, with_baseline_history
from okx_demo_bill_reader import DemoBillReader
from okx_demo_cycles import DemoCycleLifecycle, digest
from okx_demo_executor import DemoExecutionJournal
from okx_demo_fill_audit import encoded
from okx_demo_protection import _plan_body
from okx_demo_settlement import scan, money
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import OKXDemoReadTransport


def retained(db, scope, now):
    rows=db.execute('SELECT i.id,i.plan,i.dispatched,i.snapshot,e.entry_status,'
        'e.exchange_order_id,e.observed_at_ms,e.cancel_status FROM demo_intent i '
        'JOIN demo_execution e ON e.id=i.id').fetchall()
    if (len(rows)!=1 or db.execute('SELECT COUNT(*) FROM demo_intent').fetchone()[0]!=1
            or db.execute('SELECT COUNT(*) FROM demo_execution').fetchone()[0]!=1):
        raise ValueError('Single original unknown attempt required')
    row=rows[0]; client, raw, dispatched, observed, status, order, at, cancel=row
    plan=json.loads(raw); _plan_body(plan)
    if (plan.get('purpose') is not None or plan.get('environment')!='demo'
            or plan.get('headers')!={'x-simulated-trading':'1'}
            or plan['body']['clOrdId']!=client or dispatched!=1 or status!='unknown'
            or order is not None or observed is not None or at is not None or cancel!='not_requested'):
        raise ValueError('Unknown unacknowledged strategy attempt required')
    stamp=db.execute('SELECT dispatched_at FROM demo_trading_clock WHERE entry_id=?',(client,)).fetchone()
    meta=db.execute('SELECT scope,baseline_at,last_checked,anchor,revision FROM demo_fill_audit_meta').fetchone()
    cash=db.execute('SELECT cash FROM demo_cash_baseline').fetchone()
    risk=db.execute('SELECT * FROM demo_risk_entry WHERE id=?',(client,)).fetchone()
    if (not stamp or not 60000<=now-stamp[0]<=604800000 or not meta or meta[0]!=scope
            or not 0<=now-meta[1]<=604800000 or not cash or not risk):
        raise ValueError('Original bounded baseline and consumed reservation required')
    for table in ('demo_closed_trade','demo_trade_settlement','demo_account_bill'):
        if db.execute('SELECT 1 FROM '+table).fetchone(): raise ValueError('Unsettled activity exists')
    if db.execute('SELECT 1 FROM demo_audit_fills WHERE baseline=0').fetchone():
        raise ValueError('Observed fill exists')
    if db.execute('SELECT 1 FROM demo_emergency_exit WHERE entry_id=?',(client,)).fetchone():
        raise ValueError('Emergency exit exists')
    baseline={r[0]:json.loads(r[1]) for r in db.execute('SELECT identity,body FROM demo_audit_fills WHERE baseline=1')}
    return dict(client=client,record=list(row),plan=plan,begin=meta[1],meta=list(meta),cash=cash[0],
        baseline=baseline,clock=list(stamp),risk=list(risk),
        daily=db.execute('SELECT * FROM demo_daily WHERE day=?',(risk[1],)).fetchone(),
        entry_context=db.execute('SELECT * FROM demo_entry_context WHERE client_id=?',(client,)).fetchall())


def verify_chain(db):
    previous=''; count=0
    for client, raw, hashed in db.execute('SELECT client_id,body,hash FROM demo_no_effect_archive ORDER BY rowid'):
        count+=1; payload=json.loads(raw)
        if (payload['client_id']!=client or payload['sequence']!=count
                or payload['previous_hash']!=previous or digest(payload)!=hashed):
            raise ValueError('No-effect archive chain changed')
        previous=hashed
    return count,previous


async def resolve_unfilled(path,binding,creds,*,clock=None,transport=None):
    if not path.is_file() or path.is_symlink(): raise ValueError('Original ledger required')
    clock=clock or (lambda:int(time.time()*1000))
    if transport is not None and type(transport) is not OKXDemoReadTransport:
        raise ValueError('GET-only demo transport required')
    journal=DemoExecutionJournal(path); owner=None; stage='ledger_lease'
    try:
        owner=DemoEntryAuthority(journal,account_scope=binding['account_scope'],worker_id='unfilled-review',clock=clock)
        lifecycle=DemoCycleLifecycle(owner)
        db=journal.db
        db.execute('CREATE TABLE IF NOT EXISTS demo_no_effect_archive ('
            'client_id TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL)')
        if not owner.acquire(): raise ValueError('Another owner holds ledger')
        owner.set_kill_switch(True)
        stage='retained_attempt'
        saved=retained(db,owner.scope,clock()); state=lifecycle.verify_archive_chain()
        # This path resumes only an existing verified traded-cycle baseline.
        if state[0]<1: raise ValueError('Original verified archive required')
        chain=verify_chain(db)
        t=transport or OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
        reader=DemoSnapshotReader(t,account_scope=owner.scope,expected_uid=binding['uid'],
            symbol=saved['plan']['body']['instId'],clock=clock)
        stage='baseline_history'
        history=await recover_baseline_history(saved,await reader.collect(),t,clock())
        bills=DemoBillReader(reader); body=saved['plan']['body']
        stage='fresh_no_effect_reads'; start=clock()
        async with asyncio.timeout(5):
            before=await reader.collect(); through=before['received_at_ms']
            first=await t.lookup_entry(body['instId'],saved['client'])
            pages1=await bills._pages(saved['begin'],through)
            second=await t.lookup_entry(body['instId'],saved['client'])
            pages2=await bills._pages(saved['begin'],through)
            after=await reader.collect()
        now=clock(); stage='no_effect_validation'
        values=lambda s:{k:v for k,v in s.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
        if not 0<=now-start<=5000 or values(before)!=values(after):
            raise ValueError('Fresh stable complete snapshots required')
        if any(r.get('code')!='51603' or r.get('data')!=[] for r in (first,second)):
            raise ValueError('Two exact order-not-found reads required')
        snapshot=with_baseline_history(deepcopy(before),saved,history,now)
        snapshot['scan_finished_at_ms']=now
        incoming=lifecycle._snapshot(snapshot,now)
        if ({k:encoded(v) for k,v in incoming.items()}!={k:encoded(v) for k,v in saved['baseline'].items()}
                or snapshot['pending_orders'] or snapshot['pending_algos']
                or any(money(p['pos'])!='0' for p in snapshot['positions'])
                or money(snapshot['cash_balance_usdt'])!=money(saved['cash'])
                or scan(pages1,saved['begin'],through) or scan(pages2,saved['begin'],through)):
            raise ValueError('No effects and unchanged original evidence required')
        stage='atomic_attempt_archive'
        db.execute('BEGIN IMMEDIATE')
        try:
            stamp=owner._now(); owner._owner(stamp); lifecycle._snapshot(snapshot,stamp)
            if (retained(db,owner.scope,stamp)!=saved or lifecycle.verify_archive_chain()!=state
                    or verify_chain(db)!=chain or not owner._owner(stamp)[5]):
                raise ValueError('Original attempt or owner changed')
            payload=dict(version=1,sequence=chain[0]+1,previous_hash=chain[1],client_id=saved['client'],
                scope=owner.scope,disposition='no_effects_as_of_review_never_resubmit',
                retained=saved,snapshot=snapshot,bill_passes=[pages1,pages2],
                lookup_codes=['51603','51603'],verified_through_ms=through,cycle_state=list(state))
            db.execute('INSERT INTO demo_no_effect_archive VALUES(?,?,?)',
                (saved['client'],encoded(payload),digest(payload)))
            db.execute('DELETE FROM demo_execution WHERE id=?',(saved['client'],))
            db.execute('DELETE FROM demo_entry_context WHERE client_id=?',(saved['client'],))
            db.execute('DELETE FROM demo_intent WHERE id=?',(saved['client'],))
            db.execute('UPDATE demo_audit_receipt SET valid=0 WHERE singleton=1')
            owner._owner(owner._now()); lifecycle._snapshot(snapshot,owner._now())
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK'); raise
        return dict(phase='unfilled_attempt_archived_halted',attempt_archived=True,
            no_effects_verified=True,sequence=state[0],risk_reservation_preserved=True,
            baseline_preserved=True,new_entry_allowed=False,orders_sent=0,live_trading_enabled=False)
    except Exception:
        return dict(phase='blocked',resolution_stage=stage,new_entry_allowed=False,
            orders_sent=0,live_trading_enabled=False)
    finally:
        try:
            if owner is not None: owner.release()
        finally: journal.close()
