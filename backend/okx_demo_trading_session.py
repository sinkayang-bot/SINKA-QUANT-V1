"""Bounded, default-off demo entry/recovery coordination; not a deployed worker.

Uses the existing durable lease, bill/flat proof and daily risk controls. It
accepts a strategy source supplied by a trusted runtime, not arbitrary HTTP
input. Acknowledgements never prove fills. Unknown writes are never retried.
Emergency exits halt the strategy for separate settlement and cleanup review.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from okx_demo_bill_reader import DemoBillReader
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import receipt
from okx_demo_protection import assess_recovery
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_trading_transport import OKXDemoTradingTransport
from okx_demo_transport import DEMO_HEADERS
from okx_orders import ACTIVE, parse_order
from okx_reconcile import number
from okx_strategy_bridge import plan_strategy_signal,plan_execution_test


def outcome(phase):
    return dict(mode='demo_trading',phase=phase,new_entry_allowed=False,
                automatic_runtime_ready=False,live_trading_enabled=False)


class DemoTradingSession:
    def __init__(self,lifecycle,reader,*,enabled=False,enable_execution_test=False):
        if (not isinstance(lifecycle,DemoCycleLifecycle) or not isinstance(reader,DemoSnapshotReader)
                or type(reader.transport) is not OKXDemoTradingTransport
                or lifecycle.authority.scope != reader.scope):
            raise ValueError('Bound durable lifecycle and fixed demo trading reader required')
        self.lifecycle,self.reader=lifecycle,reader
        self.authority,self.journal=lifecycle.authority,lifecycle.authority.journal
        self.db,self.broker=self.journal.db,reader.transport
        self.enabled=enabled is True
        self.entry_stop_requested=False
        self.execution_test_enabled=enable_execution_test is True
        self.execution_test_diagnostics={}
        if not hasattr(self.authority,'reconciliation_lock'):
            self.authority.reconciliation_lock=asyncio.Lock()
        self.lock=self.authority.reconciliation_lock
        # Global one-shot tombstones survive cycle archival and process restarts.
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_emergency_exit ('
            'entry_id TEXT PRIMARY KEY,client_id TEXT UNIQUE NOT NULL,body TEXT NOT NULL,'
            'status TEXT NOT NULL,exchange_order_id TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_trading_clock ('
            'entry_id TEXT PRIMARY KEY,dispatched_at INTEGER NOT NULL)')

    def armed(self):
        return (self.enabled is True and self.broker.enabled is True
                and self.broker.orders_enabled is True and self.broker.environment=='demo')

    def halt(self):
        self.authority.invalidate_snapshot()
        try: self.authority.set_kill_switch(True)
        except Exception: pass  # Never alter a newer owner's state.

    def _owner(self):
        now=self.authority._now()
        self.authority._owner(now)
        if not self.armed(): raise ValueError('Demo session disabled')
        return now

    async def enter(self,source,*,execution_test=False):
        if type(execution_test) is not bool: raise ValueError("Boolean execution test required")
        if execution_test and not self.execution_test_enabled: return outcome("execution_test_disabled")
        planner=plan_execution_test if execution_test else plan_strategy_signal
        if not self.armed(): return outcome('disabled')
        if self.entry_stop_requested: return outcome('stop_requested')
        if self.lock.locked(): return outcome('busy')
        async with self.lock:
            try:
                if not self.authority.acquire(): return outcome('lease_busy')
                now=self._owner()
                if self.authority._owner(now)[5]: return outcome('kill_switch_active')
                if self.db.execute('SELECT 1 FROM demo_emergency_exit').fetchone():
                    return outcome('emergency_exit_review_required')
                self.lifecycle.verify_archive_chain()
                if self.db.execute('SELECT 1 FROM demo_intent').fetchone():
                    return outcome('existing_intent_recovery_required')
                baseline=self.db.execute('SELECT baseline_at FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
                # The cash baseline is required; initialization is never implicit.
                if baseline is None or not self.db.execute('SELECT 1 FROM demo_cash_baseline').fetchone():
                    return outcome('baseline_required')
                bundle=await DemoBillReader(self.reader).collect(begin_ms=baseline[0])
                proof=self.lifecycle.audit_idle(bundle['snapshot'],bundle['bills'])
                self.authority.accept_snapshot(proof)
                frozen=deepcopy(source)
                # Account amounts/specification cannot be supplied by the signal caller.
                frozen.update(account_scope=self.reader.scope,symbol=self.reader.symbol,
                    instrument=proof['instrument'],equity_usdt=proof['equity_usdt'],
                    available_usdt=proof['available_usdt'],leverage=proof['isolated_leverage'],
                    balance_received_at_ms=proof['received_at_ms'],
                    instrument_received_at_ms=proof['received_at_ms'],demo=True)
                frozen.pop('now_ms',None)
                candidate=planner(**frozen,now_ms=self._owner())
                if not self.authority.allows(candidate): return outcome('entry_gate_blocked')
                client_id=self.journal.register(candidate['plan'])
                self.journal.reserve_entry(client_id)
                self.db.execute('INSERT INTO demo_trading_clock VALUES(?,?)',(client_id,self._owner()))
                # No await between the final durable owner/risk check and dispatch.
                current=planner(**frozen,now_ms=self._owner())
                if self.entry_stop_requested or current != candidate or not self.authority.allows(current):
                    self.journal.entry_receipt(client_id,'blocked_before_send')
                    self.halt()
                    return outcome('entry_blocked_after_reservation')
                response=None
                try:
                    response=await self.broker.place_entry(deepcopy(candidate['plan']['body']),
                        dict(DEMO_HEADERS),deadline_ms=candidate['expires_at_ms'])
                    order_id=receipt(response,client_id)
                except Exception:
                    order_id=None
                if execution_test and isinstance(response,dict):
                    code=response.get('code');rows=response.get('data',[])
                    subcode=rows[0].get('sCode') if len(rows)==1 else None
                    self.execution_test_diagnostics={key:value for key,value in (
                        ('entry_api_code',code),('entry_event_code',subcode))
                        if isinstance(value,str) and re.fullmatch(r'[0-9]{1,8}',value)}
                self.journal.entry_receipt(client_id,'acknowledged' if order_id else 'unknown',order_id)
                # Even unknown receipts are queried using the original client ID.
                return await self._recover(client_id)
            except asyncio.CancelledError:
                self.halt()
                raise
            except Exception:
                self.halt()
                return outcome('blocked_reconciliation_required')

    async def recover(self,client_id,*,force_exit=False):
        if not self.armed(): return outcome('disabled')
        if type(force_exit) is not bool: raise ValueError('Boolean exit request required')
        if self.lock.locked(): return outcome('busy')
        async with self.lock:
            try:
                if not self.authority.acquire(): return outcome('lease_busy')
                return await self._recover(client_id,force_exit=force_exit)
            except asyncio.CancelledError:
                self.halt()
                raise
            except Exception:
                self.halt()
                return outcome('blocked_reconciliation_required')

    async def _evidence(self,client_id):
        if not self.authority.acquire(): raise ValueError('Recovery lease unavailable')
        tracked=self.journal.tracked(client_id)
        plan=json.loads(tracked[0]); body=plan['body']
        if body['instId'] != self.reader.symbol: raise ValueError('Reader/intent instrument mismatch')
        start=self._owner()
        async with asyncio.timeout(5):
            before=await self.reader.collect()
            first=await self.broker.lookup_entry(body['instId'],client_id)
            second=await self.broker.lookup_entry(body['instId'],client_id)
            after=await self.reader.collect()
        now=self._owner()
        value=lambda snap:{k:v for k,v in snap.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
        if (now-start>5000 or value(before)!=value(after) or first!=second
                or first.get('code')!='0' or len(first.get('data',[]))!=1
                or before.get('order_permission_verified') is not True):
            raise ValueError('Changing or unverified recovery snapshot')
        raw=first['data'][0]; parsed=parse_order(raw)
        if tracked[3] is not None and parsed.order_id!=tracked[3]:
            raise ValueError('Order receipt identity mismatch')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._owner()
            self.journal._observe_in_transaction(client_id,raw)
            self.db.execute('UPDATE demo_execution SET observed_at_ms=?,exchange_order_id=COALESCE(exchange_order_id,?) WHERE id=?',
                (start,parsed.order_id,client_id))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
        groups=dict(entry=[raw],positions=before['positions'],pending_orders=before['pending_orders'],pending_algos=before['pending_algos'])
        evidence=dict(demo_verified=True,**{name:dict(complete=True,received_at_ms=start,rows=rows) for name,rows in groups.items()})
        return plan,before,parsed,evidence,start

    async def _recover(self,client_id,*,force_exit=False):
        existing=self.db.execute('SELECT client_id,body,exchange_order_id FROM demo_emergency_exit WHERE entry_id=?',(client_id,)).fetchone()
        if existing is not None:
            self.halt()
            return await self._query_exit(existing)
        plan,snapshot,entry,evidence,stamp=await self._evidence(client_id)
        assessment=assess_recovery(plan,evidence,now_ms=self._owner())
        if assessment['reason']=='invalid_or_stale_evidence': raise ValueError('Invalid recovery evidence')
        if entry.state in ACTIVE:
            # Cancel one owned remainder, durably once. Never treat ACK as cancellation.
            self.halt()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self._owner()
                if self.db.execute("UPDATE demo_execution SET cancel_status='reserved' WHERE id=? AND cancel_status='not_requested'",(client_id,)).rowcount!=1:
                    self.db.execute('ROLLBACK')
                    return outcome('cancel_already_reserved_query_required')
                self.db.execute('COMMIT')
            except Exception:
                if self.db.in_transaction: self.db.execute('ROLLBACK')
                raise
            if self._owner()-stamp>5000:
                self.journal.cancel_receipt(client_id,'blocked_before_send')
                return outcome('cancel_evidence_expired')
            try:
                response=await self.broker.cancel_entry({'instId':plan['body']['instId'],'clOrdId':client_id},dict(DEMO_HEADERS))
                accepted=receipt(response,client_id,entry.order_id) is not None
            except Exception: accepted=False
            self.journal.cancel_receipt(client_id,'acknowledged' if accepted else 'unknown')
            plan,snapshot,entry,evidence,stamp=await self._evidence(client_id)
            if entry.state in ACTIVE: return outcome('cancel_unconfirmed_no_retry')
            assessment=assess_recovery(plan,evidence,now_ms=self._owner())
        if assessment['protection_verified'] and not force_exit:
            proof=self.lifecycle.audit(snapshot)
            self.authority.accept_snapshot(proof)
            return outcome('native_protection_verified_monitor_required')
        self.halt()
        if assessment['reason']=='flat_requires_fill_audit':
            return outcome('flat_requires_settlement')
        # Reduction is allowed only for terminal, positively identified owned fills.
        if entry.state not in {'filled','canceled','mmp_canceled'} or entry.filled<=0 or snapshot['pending_orders']:
            return outcome('exposure_ambiguous_manual_review')
        body=plan['body']; direction=1 if body['side']=='buy' else -1
        positions=[r for r in snapshot['positions'] if number(r.get('pos'),allow_negative=True)!=0]
        if (len(positions)!=1 or positions[0].get('instId')!=body['instId']
                or positions[0].get('posSide')!='net' or positions[0].get('mgnMode')!='isolated'
                or number(positions[0].get('pos'),allow_negative=True)!=direction*entry.filled):
            return outcome('exposure_ambiguous_manual_review')
        attached=body['attachAlgoOrds'][0]['attachAlgoClOrdId']
        # Never race a foreign/partially triggered algo or an unidentified child.
        for algo in snapshot['pending_algos']:
            if (algo.get('algoClOrdId')!=attached or algo.get('state')!='live'
                    or algo.get('reduceOnly') not in (True,'true')):
                return outcome('algo_ambiguous_manual_review')
        exit_id='sr'+hashlib.sha256(('emergency:'+client_id).encode()).hexdigest()[:28]
        close=dict(instId=body['instId'],tdMode='isolated',posSide='net',
            side='sell' if direction==1 else 'buy',ordType='market',
            sz=format(entry.filled,'f'),clOrdId=exit_id,reduceOnly=True)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            now=self._owner()
            if now-stamp>5000: raise ValueError('Recovery evidence expired')
            old=self.db.execute('SELECT 1 FROM demo_emergency_exit WHERE entry_id=?',(client_id,)).fetchone()
            if old:
                self.db.execute('ROLLBACK')
                return outcome('exit_already_reserved_query_required')
            self.db.execute('INSERT INTO demo_emergency_exit VALUES(?,?,?,\'reserved\',NULL)',
                (client_id,exit_id,json.dumps(close,sort_keys=True)))
            self.db.execute('COMMIT')
        except Exception:
            if self.db.in_transaction: self.db.execute('ROLLBACK')
            raise
        self._owner()
        try:
            response=await self.broker.reduce_position(close,dict(DEMO_HEADERS))
            order_id=receipt(response,exit_id)
        except Exception: order_id=None
        self.db.execute('UPDATE demo_emergency_exit SET status=?,exchange_order_id=? WHERE entry_id=? AND status=\'reserved\'',
            ('acknowledged' if order_id else 'unknown',order_id,client_id))
        return outcome('exit_acknowledged_query_required' if order_id else 'exit_unknown_no_retry')

    async def _query_exit(self,record):
        if not self.authority.acquire(): raise ValueError('Recovery lease unavailable')
        client_id,encoded,expected_id=record
        body=json.loads(encoded); start=self._owner()
        async with asyncio.timeout(5):
            before=await self.reader.collect()
            first=await self.broker.lookup_reduction(body['instId'],client_id)
            second=await self.broker.lookup_reduction(body['instId'],client_id)
            after=await self.reader.collect()
        values=lambda snap:{k:v for k,v in snap.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
        if (self._owner()-start>5000 or values(before)!=values(after) or first!=second
                or first.get('code')!='0' or len(first.get('data',[]))!=1):
            raise ValueError('Exit query unavailable or inconsistent')
        raw=first['data'][0]; order=parse_order(raw)
        if (order.client_order_id!=client_id or order.instrument!=body['instId']
                or order.requested!=Decimal(body['sz']) or raw.get('reduceOnly') not in (True,'true')
                or any(raw.get(k)!=body[k] for k in ('tdMode','posSide','side','ordType'))
                or (expected_id is not None and expected_id!=order.order_id)):
            raise ValueError('Exit query identity mismatch')
        flat=not any(number(row.get('pos'),allow_negative=True) for row in after['positions'])
        if (order.state=='filled' and order.filled==order.requested and flat
                and not after['pending_orders'] and not after['pending_algos']):
            return outcome('flat_requires_emergency_settlement_review')
        return outcome('exit_observed_no_resubmit')
