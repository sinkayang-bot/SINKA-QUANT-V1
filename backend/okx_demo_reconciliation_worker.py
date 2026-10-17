"""Bounded one-shot GET-only worker; disabled until explicitly configured.

Credentials are resolved only from server-side environment at an opted-in run.
No secret value is logged, no retry/loop is scheduled and no order method is used.
Use a persistent mounted ledger; no deployment or scheduling is performed here.
"""
import asyncio
import json
import os
from pathlib import Path
import time
from okx_demo_authority import DemoEntryAuthority
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_executor import DemoExecutionJournal
from okx_demo_reconciliation import DemoReconciliationRunner,status
from okx_demo_snapshot import DemoSnapshotReader
from okx_demo_transport import DemoCredentials,OKXDemoReadTransport

REQUIRED=('OKX_API_KEY','OKX_API_SECRET','OKX_API_PASSPHRASE','OKX_EXPECTED_UID','DEMO_ACCOUNT_SCOPE','DEMO_LEDGER_PATH')


def execute(environ=None):
    env=os.environ if environ is None else environ
    if env.get('RUN_DEMO_RECONCILIATION')!='1':
        return status('disabled'),0
    if any(not env.get(name) for name in REQUIRED):
        return status('configuration_missing'),2
    journal=None
    try:
        path=Path(env['DEMO_LEDGER_PATH'])
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError('Persistent ledger path required')
        clock=lambda:int(time.time()*1000)
        transport=OKXDemoReadTransport(DemoCredentials(env['OKX_API_KEY'],env['OKX_API_SECRET'],env['OKX_API_PASSPHRASE']),
            enable_demo_requests=True,clock=clock)
        # Validate fixed scope/UID before touching a ledger or contacting OKX.
        reader=DemoSnapshotReader(transport,account_scope=env['DEMO_ACCOUNT_SCOPE'],expected_uid=env['OKX_EXPECTED_UID'],
            symbol=env.get('DEMO_SYMBOL','BTC-USDT-SWAP'),clock=clock)
        journal=DemoExecutionJournal(path)
        authority=DemoEntryAuthority(journal,account_scope=env['DEMO_ACCOUNT_SCOPE'],
            worker_id=env.get('DEMO_WORKER_ID','read-reconciliation'),clock=clock)
        lifecycle=DemoCycleLifecycle(authority,enable_cycle_transition=env.get('DEMO_CYCLE_TRANSITIONS')=='1')
        runner=DemoReconciliationRunner(lifecycle,reader,enabled=True)
        result=asyncio.run(runner.run_once(initialize_baseline=env.get('DEMO_INITIALIZE_BASELINE')=='1'))
        code=0 if result['phase'] in {'idle_verified','cycle_archived','baseline_initialized'} else 3 if result['phase'] in {'lease_unavailable','busy'} else 2
        return result,code
    except Exception:
        return status('failed',reason='configuration_or_runtime_unavailable'),2
    finally:
        if journal is not None: journal.close()


def main():
    try:
        result,code=execute()
    except KeyboardInterrupt:
        result,code=status('interrupted'),130
    print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    return code


if __name__=='__main__':
    raise SystemExit(main())
