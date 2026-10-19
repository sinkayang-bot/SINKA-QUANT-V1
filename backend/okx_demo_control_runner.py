"""Opt-in Cloud Shell command executor using the existing durable demo ledger.

Only five fixed actions; no shell strings, external executable paths, account
parameters, keys or strategy parameters are accepted from the command mailbox.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from okx_demo_control_store import FirestoreControlStore

SCALARS=frozenset({'phase','stop_reason','recovery_stage','failure_kind','snapshot_stage','selected_symbol',
    'selected_strategy','strategy_profile','reason','gate_reason','mode','instrument_context_source'})
NUMBERS=frozenset({'ticks','ticks_observed','sequence','read_attempts','pending_algo_count','heartbeat_at',
    'universe','eligible','checked','no_signal','expired','prestart','fetch_failed','signals','excluded',
    'breakout_not_met','trend_not_met','warmup','invalid_stop','cooldown_remaining_seconds'})
BOOLS=frozenset({'flat_verified','protection_verified','ledger_archived','automatic_exit_verified',
    'native_protection_verified','strategy_trade','strategy_performance_sample','test_roundtrip_verified',
    'daily_limits_enabled','new_entry_allowed','within_deadline','snapshot_stable','order_lookup_stable',
    'order_lookup_valid','permission_verified'})
GROUPS=frozenset({'worker','closure','stop_diagnostics','scan','tick_counts','entry_gate_counts',
    'protection_diagnostics','consistency_checks'})
AMOUNTS=frozenset({'net_usdt','fees_usdt','realized_pnl_usdt'})
LABEL=re.compile(r'[A-Za-z0-9_-]{1,100}\Z')


def safe_report(value,depth=0):
    if not isinstance(value,dict) or depth>6:return {}
    out={}
    for key,item in value.items():
        if key in SCALARS and isinstance(item,str) and LABEL.fullmatch(item):out[key]=item
        elif key in BOOLS and type(item) is bool:out[key]=item
        elif key in NUMBERS and type(item) is int and 0<=item<=10**12:out[key]=item
        elif key in AMOUNTS and isinstance(item,str) and re.fullmatch(r'-?[0-9]{1,30}(\.[0-9]{1,30})?',item):out[key]=item
        elif key in GROUPS and isinstance(item,dict):
            if key in {'tick_counts','entry_gate_counts'}:
                out[key]={k:v for k,v in item.items() if LABEL.fullmatch(k) and type(v) is int and 0<=v<=10000}
            else:out[key]=safe_report(item,depth+1)
    return out


def arguments(action):
    common=['--scan-markets','20','--seconds','900']
    return {'start':['--run','--resume-idle','--strategy-profile','expanded']+common,
        'recover':['--run','--recover-only','--scan-markets','20','--seconds','90'],
        'audit':['--audit-trade'],'settle':['--close-reviewed-trade']}[action]


def effective_report(report):
    # Preserve the envelope and its worker/closure, expose actual final phase.
    nested=report.get('worker') or report.get('closure')
    if isinstance(nested,dict) and isinstance(nested.get('phase'),str):
        report=dict(report,phase=nested['phase'])
    report['live_trading_enabled']=False
    return report


class CommandProcess:
    def __init__(self,action,lock_fd):
        script=Path(__file__).with_name('okx_demo_cloudshell.py')
        self.process=subprocess.Popen([sys.executable,str(script),*arguments(action)],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,
            pass_fds=(lock_fd,))
        self.reports=queue.Queue(maxsize=100)
        self.last={'phase':'starting','live_trading_enabled':False}
        self.stop_sent=False
        self.reader_thread=threading.Thread(target=self._read,daemon=True)
        self.reader_thread.start()
    def _read(self):
        for line in self.process.stdout:
            if len(line)>65536:continue
            try:
                report=safe_report(json.loads(line))
                if not report:continue
                if len(json.dumps(report))>12000:report={'phase':'report_exceeds_limit_check_required'}
                self.reports.put_nowait(effective_report(report))
            except (ValueError,queue.Full):pass
    def drain(self):
        while True:
            try:self.last=self.reports.get_nowait()
            except queue.Empty:return self.last
    def stop(self):
        if not self.stop_sent and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT);self.stop_sent=True


def run(store,*,seconds=3600):
    root=Path.home()/'sinka-demo-trial'
    # Existing pinned binding and ledger are mandatory; never bootstrap/reset.
    if not (root/'ledger.sqlite').is_file() or not (root/'account.json').is_file():
        raise ValueError('Existing demo account binding and ledger required')
    lock=open(root/'control-runner.lock','a')
    os.chmod(root/'control-runner.lock',0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    runner=uuid.uuid4().hex;generation=store.acquire(runner)
    print(json.dumps({'phase':'runner_connected','live_trading_enabled':False,'trading_started':False}),flush=True)
    child=None;request_id=None;stopping=False;deadline=time.monotonic()+seconds
    def stop(*unused):
        nonlocal stopping
        stopping=True
    previous={signum:signal.signal(signum,stop) for signum in (signal.SIGINT,signal.SIGTERM)}
    try:
        while time.monotonic()<deadline and not stopping:
            command=store.poll(runner,generation)
            if command:
                if command['action']=='stop':
                    if child and request_id==command['target']:child.stop()
                else:
                    request_id=command['request_id']
                    child=CommandProcess(command['action'],lock.fileno())
            if child:
                report=child.drain();finished=child.process.poll() is not None
                if finished:
                    # Drain the pipe completely before publishing the final result.
                    child.process.wait()
                    child.reader_thread.join(timeout=2)
                    report=child.drain()
                    if report.get('phase') in {'starting','waiting_for_signal','native_protection_verified_monitor_required'}:
                        report=dict(report,phase='process_ended_check_required')
                store.publish(runner,generation,request_id,report,finished=finished)
                if finished:child=None;request_id=None
            time.sleep(2)
    finally:
        if child:
            child.stop()
            # Graceful cleanup only; never kill an in-flight exchange operation.
            try:child.process.wait(timeout=45)
            except subprocess.TimeoutExpired:pass
        for signum,handler in previous.items():signal.signal(signum,handler)
        lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect',action='store_true')
    parser.add_argument('--seconds',type=int,default=3600,choices=range(60,14401))
    args=parser.parse_args()
    if not args.connect:
        print(json.dumps({'phase':'disabled','live_trading_enabled':False}));return 0
    from google.cloud import firestore
    try:run(FirestoreControlStore(firestore.Client(project='sinka-quant',database='sinka-demo-control')),seconds=args.seconds)
    except Exception:
        print(json.dumps({'phase':'runner_stopped_check_required','live_trading_enabled':False}));return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
