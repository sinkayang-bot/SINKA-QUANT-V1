"""Bounded demo-only automatic strategy worker. Disabled by default.

Server configuration only; no secret retrieval or arbitrary URLs. Requires
independent demo trade credentials, pinned UID, account scope and durable disk.
No unattended deployment/scheduler is provided by this entry point.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import time

from okx_demo_authority import DemoEntryAuthority
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import DemoExecutionJournal
from okx_demo_reconciliation import DemoReconciliationRunner
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_strategy_source import DemoStrategySource
from okx_demo_trading_session import DemoTradingSession,outcome
from okx_demo_trading_supervisor import DemoTradingSupervisor
from okx_demo_trading_transport import OKXDemoTradingTransport
from okx_demo_transport import DemoCredentials,OKXDemoReadTransport

REQUIRED=('OKX_API_KEY','OKX_API_SECRET','OKX_API_PASSPHRASE','OKX_EXPECTED_UID','DEMO_ACCOUNT_SCOPE','DEMO_LEDGER_PATH')


class DemoProgress:
    """Report fixed labels only; never serialize account or upstream data."""
    PHASES=frozenset({'waiting_for_signal','entry_gate_blocked','cycle_archived',
        'native_protection_verified_monitor_required','kill_switch_active',
        'stop_requested','flat_requires_settlement','exit_acknowledged_query_required',
        'exit_unknown_no_retry','cancel_unconfirmed_no_retry',
        'unsubmitted_intent_recovery_required','disabled','supervisor_not_started',
        'flat_requires_emergency_settlement_review','exit_observed_no_resubmit'})

    def __init__(self,enabled=False):
        self.enabled=enabled
        self.counts={}
        self.previous=None

    def __call__(self,result):
        phase=result.get('phase')
        if phase not in self.PHASES: phase='other_status'
        self.counts[phase]=self.counts.get(phase,0)+1
        if self.enabled and (phase!=self.previous or sum(self.counts.values())%6==0):
            try:
                print(json.dumps({'mode':'demo_progress','phase':phase,
                    'ticks':sum(self.counts.values()),'live_trading_enabled':False},
                    sort_keys=True,separators=(',',':')),flush=True)
            except OSError:
                pass
        self.previous=phase


async def run_supervisor(supervisor,env):
    loop=asyncio.get_running_loop(); installed=[]
    progress=DemoProgress(env.get('DEMO_PROGRESS')=='1')
    try:
        for signum in (signal.SIGINT,signal.SIGTERM):
            loop.add_signal_handler(signum,supervisor.stop); installed.append(signum)
        result=await supervisor.run(initialize_baseline=env.get('DEMO_INITIALIZE_BASELINE')=='1',
            arm_entries=env.get('DEMO_ARM_ENTRIES')=='1',on_status=progress)
        return result|{'tick_counts':dict(progress.counts),'ticks_observed':sum(progress.counts.values())}
    finally:
        for signum in installed: loop.remove_signal_handler(signum)


def execute(environ=None):
    env=os.environ if environ is None else environ
    if env.get('RUN_DEMO_TRADING')!='1': return outcome('disabled'),0
    if env.get('DEMO_TRADING_AUTHORIZED')!='1': return outcome('demo_authorization_required'),2
    if any(not env.get(name) for name in REQUIRED): return outcome('configuration_missing'),2
    journal=None
    try:
        duration=int(env.get('DEMO_MAX_SECONDS','300'))
        poll=int(env.get('DEMO_POLL_SECONDS','5'))
        if not 1<=duration<=900 or not 2<=poll<=10: raise ValueError('Bounded runtime required')
        path=Path(env['DEMO_LEDGER_PATH'])
        if not path.is_absolute() or not path.parent.is_dir(): raise ValueError('Durable ledger parent required')
        clock=lambda:int(time.time()*1000)
        credentials=DemoCredentials(env['OKX_API_KEY'],env['OKX_API_SECRET'],env['OKX_API_PASSPHRASE'])
        trading=OKXDemoTradingTransport(credentials,enable_demo_requests=True,enable_demo_orders=True,clock=clock)
        readonly=OKXDemoReadTransport(credentials,enable_demo_requests=True,clock=clock)
        reader_args=dict(account_scope=env['DEMO_ACCOUNT_SCOPE'],expected_uid=env['OKX_EXPECTED_UID'],
            symbol=env.get('DEMO_SYMBOL','BTC-USDT-SWAP'),clock=clock)
        reader=DemoSnapshotReader(trading,**reader_args)
        read_reader=DemoSnapshotReader(readonly,**reader_args)
        journal=DemoExecutionJournal(path)
        owner=DemoEntryAuthority(journal,account_scope=reader.scope,worker_id='demo-strategy',clock=clock)
        lifecycle=DemoCycleLifecycle(owner,enable_cycle_transition=True)
        session=DemoTradingSession(lifecycle,reader,enabled=True)
        reconciliation=DemoReconciliationRunner(lifecycle,read_reader,enabled=True)
        supervisor=DemoTradingSupervisor(session,reconciliation,DemoStrategySource(),enabled=True,
            max_seconds=duration,poll_seconds=poll)
        result=asyncio.run(run_supervisor(supervisor,env))
        clean=(result['phase']=='verified_but_not_armed' or
            (result['phase']=='stopped_no_active_intent' and result.get('stop_reason') in {'stop_requested','runtime_limit'}))
        return result,0 if clean else 2
    except KeyboardInterrupt:
        return outcome('interrupted_reconciliation_required'),130
    except Exception:
        return outcome('configuration_or_runtime_unavailable'),2
    finally:
        if journal is not None: journal.close()


if __name__=='__main__':
    result,code=execute()
    print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    raise SystemExit(code)
