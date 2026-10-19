"""One-shot default-off GET-only reconciliation orchestration.

No strategy entry, order/cancel request, secret discovery or deployment wiring.
A supported terminal entry is closed, bill-settled and archived in ONE local
transaction. Active/ambiguous/unsupported exposure is preserved and blocked.
"""
import asyncio
from copy import deepcopy
import json
from okx_demo_bill_reader import DemoBillReader, DemoBillReadError
from okx_demo_close_reader import DemoCloseReader
from okx_demo_cycles import DemoCycleLifecycle
from okx_demo_settlement import scan
from okx_demo_snapshot import DemoSnapshotReader, DemoSnapshotError
from okx_demo_read_errors import failure_kind
from okx_strategy_bridge import timestamp


def status(phase,**fields):
    return dict(phase=phase,mode='demo_read_reconciliation',new_entry_allowed=False,
        exchange_submission_enabled=False,orders_sent=0,**fields)


class DemoReconciliationRunner:
    def __init__(self,lifecycle,snapshot_reader,*,enabled=False):
        if (not isinstance(lifecycle,DemoCycleLifecycle) or not isinstance(snapshot_reader,DemoSnapshotReader)
                or lifecycle.authority.scope!=snapshot_reader.scope):
            raise ValueError('Matching persistent cycle authority and verified demo reader required')
        self.lifecycle=lifecycle; self.authority=lifecycle.authority; self.reader=snapshot_reader
        self.db=lifecycle.db; self.enabled=enabled is True
        self.close_reader=DemoCloseReader(snapshot_reader); self.bill_reader=DemoBillReader(snapshot_reader)
        if not hasattr(self.authority,'reconciliation_lock'):
            self.authority.reconciliation_lock=asyncio.Lock()
        self.lock=self.authority.reconciliation_lock
        self.last_result=None

    async def _verified_read(self, operation):
        # Retry only complete GET evidence acquisition, never ledger transitions.
        for attempt in range(1, 4):
            self.read_attempts = attempt
            try:
                return await operation()
            except (DemoSnapshotError, DemoBillReadError, TimeoutError) as error:
                kind = failure_kind(error)
                stage = getattr(error, 'stage', 'unknown')
                transient = kind in {'timeout', 'transport_failure'} or (
                    kind == 'validation_failed' and (
                        stage in {'snapshot_consistency', 'bill_consistency'} or
                        getattr(error, 'snapshot_stage', None) == 'snapshot_consistency'))
                if not transient or attempt == 3:
                    raise
                self.reader.last_snapshot = None
                self.bill_reader.last_bundle = None
                await asyncio.sleep(0.25)

    async def _closed_bundle(self,plan,expected_id,begin):
        start=timestamp(self.reader.clock())
        body=plan['body']
        if body['instId']!=self.reader.symbol: raise ValueError('Plan/reader instrument mismatch')
        async with asyncio.timeout(5):
            before=await self.reader.collect()
            through=before['received_at_ms']
            first=await self.close_reader._details(body,before,expected_id)
            bills1=await self.bill_reader._pages(begin,through)
            second=await self.close_reader._details(body,before,expected_id)
            bills2=await self.bill_reader._pages(begin,through)
            after=await self.reader.collect()
        now=timestamp(self.reader.clock())
        def values(snapshot):
            return {k:v for k,v in snapshot.items() if k not in {'received_at_ms','scan_finished_at_ms'}}
        if (not start<=through<=now or now-start>5000 or values(before)!=values(after)
                or any(first[k]['rows']!=second[k]['rows'] for k in ('entry','algo','child'))
                or scan(bills1,begin,through)!=scan(bills2,begin,through)):
            raise ValueError('Composite read changed or expired')
        snapshot=deepcopy(before); snapshot['scan_finished_at_ms']=now
        bills=dict(demo_verified=True,account_scope=self.reader.scope,begin_ms=begin,through_ms=through,
            received_at_ms=through,query=dict(ccy='USDT',begin=str(begin),end=str(through),limit='100'),passes=[bills1,bills2])
        return dict(snapshot=snapshot,close=first,bills=bills)

    def _failed(self):
        self.authority.invalidate_snapshot()
        self.reader.last_snapshot=None
        self.close_reader.last_bundle=None; self.bill_reader.last_bundle=None
        # An old owner must not invalidate a newer owner's committed proof.
        try: self.lifecycle._begin_attempt()
        except Exception: pass

    async def run_once(self,*,initialize_baseline=False,resume_idle=False):
        if not self.enabled:
            self.last_result=status('disabled'); return deepcopy(self.last_result)
        if self.lock.locked(): return status('busy')
        async with self.lock:
            self.last_result=None
            self.read_attempts=0
            stage='lease_verification'
            try:
                if not self.authority.acquire():
                    self.last_result=status('lease_unavailable'); return deepcopy(self.last_result)
                stage='ledger_verification'
                self.lifecycle._begin_attempt()
                self.lifecycle.verify_archive_chain()
                baseline=self.db.execute('SELECT baseline_at FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
                if baseline is None:
                    if initialize_baseline is not True:
                        self.last_result=status('baseline_required'); return deepcopy(self.last_result)
                    stage='baseline_snapshot'
                    snapshot=await self._verified_read(self.reader.collect)
                    stage='baseline_commit'
                    proof=self.lifecycle.bootstrap(snapshot,baseline_authorized=True)
                    self.authority.accept_snapshot(proof)
                    self.last_result=status('baseline_initialized')
                    return deepcopy(self.last_result)
                rows=self.db.execute('SELECT id,plan,dispatched FROM demo_intent').fetchall()
                if not rows:
                    stage='idle_read'
                    bundle=await self._verified_read(lambda:self.bill_reader.collect(begin_ms=baseline[0]))
                    stage='idle_audit'
                    proof=self.lifecycle.audit_idle(bundle['snapshot'],bundle['bills'],resume_idle=resume_idle)
                    self.authority.accept_snapshot(proof)
                    self.last_result=status('idle_verified',sequence=self.lifecycle.verify_archive_chain()[0])
                elif len(rows)!=1:
                    raise ValueError('Ambiguous active intent set')
                elif rows[0][2]!=1:
                    self.last_result=status('unsubmitted_intent_preserved')
                elif not self.lifecycle.enabled:
                    self.last_result=status('cycle_transition_disabled')
                else:
                    client_id,encoded,_=rows[0]
                    receipt=self.db.execute('SELECT exchange_order_id FROM demo_execution WHERE id=?',(client_id,)).fetchone()
                    if receipt is None: raise ValueError('Missing execution reservation')
                    stage='closed_read'
                    bundle=await self._verified_read(lambda:self._closed_bundle(json.loads(encoded),receipt[0],baseline[0]))
                    stage='closed_commit'
                    self.db.execute('BEGIN IMMEDIATE')
                    try:
                        now=self.authority._now(); self.authority._owner(now)
                        self.lifecycle._audit_closed_in_transaction(bundle['snapshot'],bundle['close'],now)
                        result=self.lifecycle._advance_cycle_in_transaction(bundle['snapshot'],bundle['bills'],now)
                        self.db.execute('COMMIT')
                    except Exception:
                        self.db.execute('ROLLBACK'); raise
                    self.authority.accept_snapshot(result['snapshot'])
                    self.last_result=status('cycle_archived',sequence=result['sequence'],archive_hash=result['archive_hash'])
            except asyncio.CancelledError:
                self._failed(); self.last_result=status('cancelled'); raise
            except Exception as error:
                self._failed()
                allowed={'snapshot_start','account_config','inventory_first_pass','inventory_second_pass',
                    'snapshot_consistency','bill_window','snapshot_before','snapshot_after',
                    'bills_first_pass','bills_second_pass','bill_consistency'}
                read_stage=getattr(error,'stage','unknown')
                snapshot_stage=getattr(error,'snapshot_stage','unknown')
                self.last_result=status('blocked',reason='reconciliation_unavailable',
                    recovery_stage=stage, failure_kind=failure_kind(error),
                    read_stage=read_stage if read_stage in allowed else 'unknown',
                    snapshot_stage=snapshot_stage if snapshot_stage in allowed else 'unknown',
                    read_attempts=self.read_attempts)
            return deepcopy(self.last_result)
