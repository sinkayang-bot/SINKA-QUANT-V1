"""Bounded demo strategy supervision. Default-off; no credential/CLI wiring.

A trusted runtime supplies a public strategy source, explicit arming intent,
and two account readers bound to the same configured demo credentials/UID.
No scheduler or deployment is created. Emergency paths stop for separate review.
"""
import asyncio
from copy import deepcopy

from okx_demo_reconciliation import DemoReconciliationRunner
from okx_demo_trading_session import DemoTradingSession,outcome
from okx_demo_transport import OKXDemoReadTransport


class DemoTradingSupervisor:
    def __init__(self,session,reconciliation,source,*,enabled=False,max_seconds=300,poll_seconds=5):
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
        if source is None: return outcome('waiting_for_signal')
        source=deepcopy(source)
        source['started_at_ms']=self.started_at
        return await self.session.enter(source)

    async def run(self,*,initialize_baseline=False,arm_entries=False,on_status=None):
        if not self.enabled or not self.session.armed(): return outcome('disabled')
        if self.started_at is not None: return outcome('supervisor_already_started')
        if type(initialize_baseline) is not bool or type(arm_entries) is not bool:
            raise ValueError('Explicit boolean startup choices required')
        owner=self.session.authority
        loop=asyncio.get_running_loop(); deadline=loop.time()+self.max_seconds
        self.started_at=owner._now()
        if not owner.acquire(): return outcome('lease_busy')
        try:
            active=self.session.db.execute('SELECT 1 FROM demo_intent').fetchone()
            if active is None:
                initial=await self.reconciliation.run_once(initialize_baseline=initialize_baseline)
                if initial['phase'] not in {'baseline_initialized','idle_verified'}:
                    return initial
                if not arm_entries: return outcome('verified_but_not_armed')
                if self.session.db.execute('SELECT 1 FROM demo_emergency_exit').fetchone():
                    return outcome('emergency_exit_review_required')
                owner.set_kill_switch(False)
            # Recovery after restart never implicitly clears a takeover kill switch.
            reason='runtime_limit'
            while not self.stop_event.is_set() and loop.time()<deadline:
                result=await self.tick()
                if on_status is not None: on_status(deepcopy(result))
                if result['phase'] not in {'waiting_for_signal','entry_gate_blocked','cycle_archived',
                    'native_protection_verified_monitor_required','kill_switch_active'}:
                    reason=result['phase']
                    break
                remaining=min(self.poll_seconds,max(0,deadline-loop.time()))
                try: await asyncio.wait_for(self.stop_event.wait(),timeout=remaining)
                except TimeoutError: pass
            if self.stop_event.is_set(): reason='stop_requested'
            return (await self._cleanup())|{'stop_reason':reason}
        except asyncio.CancelledError:
            await self._cleanup()
            raise
        except Exception:
            return (await self._cleanup())|{'stop_reason':'runtime_failed'}
        finally:
            self.session.halt()
