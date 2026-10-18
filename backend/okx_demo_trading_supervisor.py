"""Bounded demo strategy supervision. Default-off; no credential/CLI wiring.

A trusted runtime supplies a public strategy source, explicit arming intent,
and two account readers bound to the same configured demo credentials/UID.
No scheduler or deployment is created. Emergency paths stop for separate review.
"""
import asyncio
import json
from copy import deepcopy

from okx_demo_reconciliation import DemoReconciliationRunner
from okx_demo_trading_session import DemoTradingSession,outcome
from okx_demo_transport import OKXDemoReadTransport
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_bill_reader import DemoBillReader
from okx_demo_close_reader import DemoCloseReader
from okx_demo_cycles import unresolved_emergency_exit
from okx_risk import SYMBOLS


class DemoTradingSupervisor:
    def __init__(self,session,reconciliation,source,*,enabled=False,max_seconds=300,poll_seconds=5,scan_markets=False,execution_test=False,recover_only=False):
        if (type(session) is not DemoTradingSession or type(reconciliation) is not DemoReconciliationRunner
                or reconciliation.lifecycle is not session.lifecycle
                or type(reconciliation.reader.transport) is not OKXDemoReadTransport
                or reconciliation.reader.uid!=session.reader.uid
                or reconciliation.reader.symbol!=session.reader.symbol
                or reconciliation.reader.transport.credentials!=session.broker.credentials
                or not callable(source)):
            raise ValueError('One lifecycle, matching account readers and trusted signal source required')
        if type(max_seconds) is not int or not 1<=max_seconds<=900:
            raise ValueError('Explicit bounded duration required (1..900 seconds)')
        if type(poll_seconds) is not int or not 2<=poll_seconds<=10:
            raise ValueError('Bounded monitor interval required (2..10 seconds)')
        self.session,self.reconciliation,self.source=session,reconciliation,source
        self.enabled=enabled is True
        self.scan_markets=scan_markets is True
        self.execution_test=execution_test is True
        self.recover_only=recover_only is True
        if self.execution_test and (max_seconds>90 or self.scan_markets or not session.execution_test_enabled
                or not session.authority.execution_test_enabled):
            raise ValueError('Explicit test admission in one-market mode required')
        self.max_seconds,self.poll_seconds=max_seconds,poll_seconds
        self.stop_event=asyncio.Event()
        self.started_at=None

    def stop(self):
        self.session.entry_stop_requested=True
        self.stop_event.set()

    async def _cleanup(self):
        self.session.halt()
        row=self.session.db.execute('SELECT id,dispatched FROM demo_intent').fetchone()
        if row is None: return outcome('stopped_no_active_intent')
        if row[1]!=1: return outcome('stopped_unsubmitted_intent_retained')
        try:
            async with asyncio.timeout(15):
                result=await self.session.recover(row[0],force_exit=True)
                if result['phase'] in {'exit_acknowledged_query_required','exit_unknown_no_retry'}:
                    result=await self.session.recover(row[0],force_exit=True)
                return result
        except Exception:
            return outcome('shutdown_recovery_unresolved')

    def _scan_status(self,result,symbol=None):
        if self.scan_markets:
            result=result|{'scan':dict(getattr(self.source,'last_scan',{}))}
            if symbol in SYMBOLS: result['selected_symbol']=symbol
        return result

    async def _select_market(self,symbol):
        if not self.scan_markets or symbol not in SYMBOLS:
            raise ValueError('Explicit allowlisted multi-market mode required')
        async with self.session.lock:
            owner=self.session.authority
            if not owner.acquire(): raise ValueError('Lease unavailable')
            self.session._owner()
            if self.session.db.execute('SELECT 1 FROM demo_intent').fetchone():
                raise ValueError('Cannot switch active intent market')
            owner.invalidate_snapshot()
            args=dict(account_scope=self.session.reader.scope,expected_uid=self.session.reader.uid,
                symbol=symbol,clock=self.session.reader.clock)
            read=DemoSnapshotReader(self.reconciliation.reader.transport,**args)
            try:
                # Selected instrument/isolated leverage are validated by the
                # account reader before rebinding. Entry repeats full bill audit.
                await read.collect()
            except Exception:
                excluded=getattr(self.source,'excluded',None)
                if isinstance(excluded,set): excluded.add(symbol)
                return False
            self.session._owner()
            if self.stop_event.is_set(): return False
            if self.session.db.execute('SELECT 1 FROM demo_intent').fetchone():
                raise ValueError('Concurrent intent prevents market switch')
            self.session.reader=DemoSnapshotReader(self.session.broker,**args)
            self.reconciliation.reader=read
            self.reconciliation.bill_reader=DemoBillReader(read)
            self.reconciliation.close_reader=DemoCloseReader(read)
            return True

    async def tick(self):
        if not self.enabled or not self.session.armed(): return outcome('disabled')
        if self.started_at is None: return outcome('supervisor_not_started')
        if self.stop_event.is_set(): return outcome('stop_requested')
        row=self.session.db.execute('SELECT id,dispatched FROM demo_intent').fetchone()
        if row is not None:
            if row[1]!=1: return outcome('unsubmitted_intent_recovery_required')
            opened=self.session.db.execute('SELECT dispatched_at FROM demo_trading_clock WHERE entry_id=?',(row[0],)).fetchone()
            now=self.session.authority._now()
            force=opened is None or not 0<=now-opened[0]<3600000
            result=await self.session.recover(row[0],force_exit=force)
            if result['phase']=='flat_requires_settlement':
                return await self.reconciliation.run_once()
            return result
        idle=await self.reconciliation.run_once()
        if idle['phase']!='idle_verified': return idle
        if self.stop_event.is_set(): return outcome('stop_requested')
        source=await self.source(self.session.reader.symbol,self.started_at)
        if self.stop_event.is_set(): return outcome('stop_requested')
        if source is None: return self._scan_status(outcome('waiting_for_signal'))
        selected=source.get('quote',{}).get('symbol')
        if self.scan_markets and selected!=self.session.reader.symbol:
            if not await self._select_market(selected):
                return self._scan_status(outcome('market_preflight_blocked'),selected)
        if self.stop_event.is_set(): return outcome('stop_requested')
        source=deepcopy(source)
        source['started_at_ms']=self.started_at
        return self._scan_status(await self.session.enter(source,execution_test=True) if self.execution_test else await self.session.enter(source),selected)

    async def run(self,*,initialize_baseline=False,arm_entries=False,resume_idle=False,on_status=None):
        if not self.enabled or not self.session.armed(): return outcome('disabled')
        if self.started_at is not None: return outcome('supervisor_already_started')
        if any(type(value) is not bool for value in (initialize_baseline,arm_entries,resume_idle)):
            raise ValueError('Explicit boolean startup choices required')
        owner=self.session.authority
        loop=asyncio.get_running_loop(); deadline=loop.time()+self.max_seconds
        self.started_at=owner._now()
        if not owner.acquire(): return outcome('lease_busy')
        try:
            if self.recover_only:
                owner.set_kill_switch(True)
                row=self.session.db.execute('SELECT id,dispatched FROM demo_intent').fetchone()
                if row is None: return outcome('no_active_intent_no_submission')|{'recovery_only':True}
                if row[1]!=1: return outcome('unsubmitted_intent_preserved')|{'recovery_only':True}
                # Existing tracked intent only. Never call the public source,
                # entry arming, baseline initialization or session.enter.
                result=await self._cleanup()
                protected=self.session.recovery_protection_verified
                flat=result['phase']=='flat_requires_emergency_settlement_review'
                return result|dict(recovery_only=True,protection_verified=protected,flat_verified=flat,
                    test_roundtrip_verified=protected and flat,strategy_performance_sample=False)
            active=self.session.db.execute('SELECT plan FROM demo_intent').fetchone()
            if self.execution_test and active is not None:
                try: owned_test=json.loads(active[0]).get('purpose')=='execution_test'
                except Exception: owned_test=False
                if not owned_test: return outcome('existing_strategy_intent_preserved')
            if active is None:
                initial=await self.reconciliation.run_once(initialize_baseline=initialize_baseline,resume_idle=resume_idle)
                if initial['phase'] not in {'baseline_initialized','idle_verified'}:
                    return initial
                if not arm_entries: return outcome('verified_but_not_armed')
                if unresolved_emergency_exit(self.session.db):
                    return outcome('emergency_exit_review_required')
                owner.set_kill_switch(False)
            # Recovery after restart never implicitly clears a takeover kill switch.
            reason='runtime_limit'
            test_protection=False
            test_attempts=[]
            while not self.stop_event.is_set() and loop.time()<deadline:
                result=await self.tick()
                if on_status is not None: on_status(deepcopy(result))
                if self.execution_test:
                    test_protection=(result['phase']=='native_protection_verified_monitor_required'
                        or self.session.recovery_protection_verified)
                    test_attempts.append(dict(phase=result['phase'],recovery_stage=self.session.recovery_stage,
                        protection_diagnostics=dict(self.session.recovery_diagnostics),
                        **{k:result[k] for k in ('gate_reason','retry_after_seconds') if k in result}))
                    active=self.session.db.execute('SELECT id,dispatched FROM demo_intent').fetchone()
                    if (not test_protection and result['phase']=='blocked_reconciliation_required'
                            and active is not None and active[1]==1 and len(test_attempts)<3
                            and not self.session.db.execute('SELECT 1 FROM demo_emergency_exit WHERE entry_id=?',(active[0],)).fetchone()
                            and loop.time()+6<deadline):
                        # Only GET-based recovery of the existing dispatched ID.
                        # No second session.enter call, new signal or POST retry.
                        try: await asyncio.wait_for(self.stop_event.wait(),timeout=1)
                        except TimeoutError: pass
                        continue
                    reason='execution_test_cycle'
                    break
                if result['phase'] not in {'waiting_for_signal','entry_gate_blocked','cycle_archived',
                    'native_protection_verified_monitor_required','kill_switch_active','market_preflight_blocked'}:
                    reason=result['phase']
                    break
                remaining=min(self.poll_seconds,max(0,deadline-loop.time()))
                try: await asyncio.wait_for(self.stop_event.wait(),timeout=remaining)
                except TimeoutError: pass
            if self.stop_event.is_set(): reason='stop_requested'
            final=(await self._cleanup())|{'stop_reason':reason}
            if self.execution_test:
                flat=final['phase']=='flat_requires_emergency_settlement_review'
                test_protection=test_protection or self.session.recovery_protection_verified
                final.update(execution_test=True,protection_verified=test_protection,
                    test_roundtrip_verified=test_protection and flat,
                    flat_verified=flat,strategy_performance_sample=False,test_attempts=test_attempts)
                final['cleanup_protection_diagnostics']=dict(self.session.recovery_diagnostics)
                final.update(self.session.execution_test_diagnostics)
            return final
        except asyncio.CancelledError:
            await self._cleanup()
            raise
        except Exception:
            return (await self._cleanup())|{'stop_reason':'runtime_failed'}
        finally:
            self.session.halt()
