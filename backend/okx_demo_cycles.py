"""Explicit local cycle archival and entry eligibility for offline rehearsals.

No exchange request is sent. Shared SQLite leases, daily limits and default-off
network execution remain in effect. Archives retain prior active ledgers in a
hash chain; this is corruption detection, not a cryptographic trust boundary.
"""
import hashlib
import json
from decimal import Decimal
from okx_demo_authority import snapshot_digest
from okx_demo_fill_audit import DemoFillAuditError,MAX_AUDIT_GAP_MS,encoded,money
from okx_demo_settlement import DemoSettlementAuditor,scan
from okx_demo_reads import bill_route
from okx_strategy_bridge import timestamp

CYCLE_TABLES=('demo_intent','demo_execution','demo_fill_audit_meta','demo_audit_fills',
    'demo_close_context','demo_cash_baseline','demo_account_bill','demo_closed_trade',
    'demo_trade_settlement','demo_audit_receipt')


def digest(body):
    return hashlib.sha256(encoded(body).encode()).hexdigest()


def unresolved_emergency_exit(db):
    # Tombstones are permanent; only a verified archived flat cycle resolves them.
    return db.execute('SELECT 1 FROM demo_emergency_exit e WHERE NOT EXISTS '
        '(SELECT 1 FROM demo_cycle_archive a WHERE a.client_id=e.entry_id) LIMIT 1').fetchone() is not None


class DemoCycleLifecycle(DemoSettlementAuditor):
    def __init__(self,authority,*,enable_cycle_transition=False):
        super().__init__(authority)
        self.enabled=enable_cycle_transition is True
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_cycle_archive ('
            'client_id TEXT PRIMARY KEY,sequence INTEGER UNIQUE NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_cycle_state ('
            'singleton INTEGER PRIMARY KEY CHECK(singleton=1),sequence INTEGER NOT NULL,previous_hash TEXT NOT NULL,started_at INTEGER NOT NULL)')
        self.db.execute("INSERT OR IGNORE INTO demo_cycle_state VALUES(1,0,'',0)")
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_idle_resume ('
            'revision INTEGER PRIMARY KEY,body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_cycle_entry_receipt ('
            'singleton INTEGER PRIMARY KEY CHECK(singleton=1),sequence INTEGER NOT NULL,epoch INTEGER NOT NULL,session TEXT NOT NULL,snapshot_hash TEXT NOT NULL)')

    def verify_archive_chain(self):
        sequence=0; previous=''; last_boundary=0
        for client_id,seq,raw,hashed in self.db.execute('SELECT client_id,sequence,body,hash FROM demo_cycle_archive ORDER BY sequence'):
            body=json.loads(raw); sequence+=1
            if (seq!=sequence or body['sequence']!=seq or body['client_id']!=client_id
                    or body['previous_hash']!=previous or digest(body)!=hashed):
                raise ValueError('Archive chain is inconsistent')
            previous=hashed; last_boundary=body["verified_through_ms"]
        state=self.db.execute('SELECT sequence,previous_hash,started_at FROM demo_cycle_state WHERE singleton=1').fetchone()
        if state is None or state!=(sequence,previous,last_boundary):
            raise ValueError('Archive/state checkpoint mismatch')
        return state

    def _entry_receipt(self,verified,sequence):
        self.db.execute('INSERT OR REPLACE INTO demo_cycle_entry_receipt VALUES(1,?,?,?,?)',
            (sequence,self.authority.epoch,self.authority.session,snapshot_digest(verified)))

    def _advance_cycle_in_transaction(self,snapshot,bills,now):
        if not self.db.in_transaction or not self.enabled:
            raise ValueError('Enabled atomic cycle transaction required')
        state=self.verify_archive_chain()
        settlement=self._settle_in_transaction(snapshot,bills,now)
        return self._archive_settled_in_transaction(snapshot,bills,now,settlement,state)

    def _archive_settled_in_transaction(self,snapshot,bills,now,settlement,state):
        if not self.db.in_transaction or not self.enabled or state!=self.verify_archive_chain():
            raise ValueError('Enabled verified archive transaction required')
        client=settlement['client_id']
        intents=self.db.execute('SELECT id FROM demo_intent').fetchall()
        if intents!=[(client,)] or self.db.execute('SELECT 1 FROM demo_cycle_archive WHERE client_id=?',(client,)).fetchone():
            raise ValueError('One unarchived settled intent required')
        old_begin=self.db.execute('SELECT baseline_at FROM demo_fill_audit_meta WHERE singleton=1').fetchone()[0]
        if snapshot['received_at_ms']<=old_begin:
            raise ValueError('Cycle boundary must advance')
        records={}
        for table in CYCLE_TABLES:
            cursor=self.db.execute('SELECT * FROM '+table+' ORDER BY 1')
            records[table]=dict(columns=[c[0] for c in cursor.description],rows=cursor.fetchall())
        payload=dict(version=1,sequence=state[0]+1,previous_hash=state[1],client_id=client,
            verified_through_ms=snapshot['received_at_ms'],settlement=settlement,records=records,
            verification=dict(snapshot_hash=snapshot_digest(snapshot),bill_evidence_hash=digest(bills),
                equity_usdt=snapshot['equity_usdt'],cash_usdt=snapshot['cash_balance_usdt'],
                position_rows=len(snapshot['positions']),pending_orders=0,pending_algos=0),
            risk_close=self.db.execute('SELECT id,pnl,closed_at FROM demo_risk_close WHERE id=?',('settled:'+client,)).fetchone())
        hashed=digest(payload)
        self.db.execute('INSERT INTO demo_cycle_archive VALUES(?,?,?,?)',(client,state[0]+1,encoded(payload),hashed))
        # The previous ledgers now exist in the committed archive. Never
        # clear daily risk, lease, prior archives or used signal identities.
        for table in CYCLE_TABLES: self.db.execute('DELETE FROM '+table)
        fills=self._snapshot(snapshot,self.authority._now())
        for fill in fills.values():
            if int(fill['fillTime'])>snapshot['received_at_ms']:
                raise ValueError('Fill crosses cycle boundary')
            self._put(fill,baseline=True)
        anchor=max(fills,key=lambda k:int(fills[k]['billId'])) if fills else None
        self.db.execute('INSERT INTO demo_fill_audit_meta VALUES(1,?,?,?,?,1)',
            (self.authority.scope,snapshot['received_at_ms'],now,anchor))
        self._baseline_metadata(snapshot)
        self.db.execute('UPDATE demo_cycle_state SET sequence=?,previous_hash=?,started_at=? WHERE singleton=1',
            (state[0]+1,hashed,snapshot['received_at_ms']))
        finished=self.authority._now(); self.authority._owner(finished); self._snapshot(snapshot,finished)
        verified=self._certify(snapshot,finished,1)
        self._entry_receipt(verified,state[0]+1)
        return dict(archived_client_id=client,archive_hash=hashed,sequence=state[0]+1,
            snapshot=verified,new_entry_allowed=False,submission_enabled=False)

    def advance_cycle(self,snapshot,bills):
        try:
            if not self.enabled: raise ValueError('Cycle transition disabled by default')
            now=self._begin_attempt()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result=self._advance_cycle_in_transaction(snapshot,bills,now)
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK'); raise
            return result
        except Exception:
            raise DemoFillAuditError('Cycle not advanced; prior ledger retained and entry blocked') from None

    def _untouched_gap(self,meta,state,now):
        # Narrow recovery: only a never-traded first cycle, within 12 hours of
        # its ORIGINAL baseline. Full immutable fill and two bill scans below
        # still apply. Neither the baseline nor the risk ledger is reset.
        if (state!=(0,'',0) or not 0<=now-meta[0]<=43200000
                or not MAX_AUDIT_GAP_MS<now-meta[1]<=43200000):
            raise ValueError('Untouched bounded first cycle required')
        for table in ('demo_intent','demo_execution','demo_risk_entry','demo_risk_close',
                'demo_cycle_archive','demo_closed_trade','demo_trade_settlement','demo_account_bill'):
            if self.db.execute('SELECT 1 FROM '+table).fetchone():
                raise ValueError('Trading history requires separate reconciliation')
        emergency=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_emergency_exit'").fetchone()
        if emergency and self.db.execute('SELECT 1 FROM demo_emergency_exit').fetchone():
            raise ValueError('Emergency settlement required')

    def _idle_gap(self,meta,state,now,cash):
        if state[0]==0:
            return self._untouched_gap(meta,state,now)
        # Resume only the idle baseline created by a verified archive. Never
        # replace that baseline, erase risk, or adopt activity during the gap.
        if (meta[0]!=state[2] or not 0<=now-meta[0]<=43200000
                or not MAX_AUDIT_GAP_MS<now-meta[1]<=43200000):
            raise ValueError('Bounded archived idle baseline required')
        latest=json.loads(self.db.execute('SELECT body FROM demo_cycle_archive WHERE sequence=?',(state[0],)).fetchone()[0])
        if money(latest['verification']['cash_usdt'])!=cash:
            raise ValueError('Archived cash baseline mismatch')
        for table in ('demo_intent','demo_execution',
                'demo_closed_trade','demo_trade_settlement','demo_account_bill'):
            if self.db.execute('SELECT 1 FROM '+table).fetchone():
                raise ValueError('Unsettled activity requires reconciliation')
        emergency=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_emergency_exit'").fetchone()
        if emergency and unresolved_emergency_exit(self.db):
            raise ValueError('Unresolved emergency settlement required')

    def audit_idle(self,snapshot,bills,*,resume_idle=False):
        """Renew readiness only with empty current-cycle bills and unchanged cash."""
        try:
            now=self._begin_attempt()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.authority._owner(now); fills=self._snapshot(snapshot,now)
                state=self.verify_archive_chain()
                meta=self.db.execute('SELECT baseline_at,last_checked,anchor,revision FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
                cash=self.db.execute('SELECT cash FROM demo_cash_baseline WHERE singleton=1').fetchone()
                if type(resume_idle) is not bool:
                    raise ValueError('Explicit boolean resume choice required')
                if (meta is None or cash is None or self.db.execute('SELECT 1 FROM demo_intent').fetchone()
                        or now<meta[1] or (meta[2] is not None and meta[2] not in fills)):
                    raise ValueError('Current idle cycle required')
                gap=now-meta[1]>MAX_AUDIT_GAP_MS
                if gap:
                    if not resume_idle: raise ValueError('Continuous audit or explicit recovery required')
                    self._idle_gap(meta,state,now,cash[0])
                if snapshot['pending_orders'] or snapshot['pending_algos'] or any(Decimal(p['pos'])!=0 for p in snapshot['positions']):
                    raise ValueError('Account must stay flat and clean')
                for identity,fill in fills.items():
                    row=self.db.execute('SELECT body,baseline FROM demo_audit_fills WHERE identity=?',(identity,)).fetchone()
                    if row is None or row!=(encoded(fill),1): raise ValueError('Unknown or changed idle fill')
                begin=timestamp(bills['begin_ms']); through=timestamp(bills['through_ms']); received=timestamp(bills['received_at_ms'])
                if (bills.get('demo_verified') is not True or bills.get('account_scope')!=self.authority.scope
                        or begin!=meta[0] or through!=snapshot['received_at_ms'] or not through<=received<=now or now-received>5000
                        or bills.get('query')!={'ccy':'USDT','begin':str(begin),'end':str(through),'limit':'100'}):
                    raise ValueError('Fresh account-wide idle bill interval required')
                bill_route(begin,through)
                passes=bills['passes']
                if (not isinstance(passes,list) or len(passes)!=2 or any(scan(p,begin,through) for p in passes)
                        or money(snapshot.get('cash_balance_usdt'))!=cash[0]):
                    raise ValueError('Idle cash/bill changes require reconciliation')
                finished=self.authority._now(); self.authority._owner(finished); self._snapshot(snapshot,finished)
                if gap:
                    resume_revision=self.db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM demo_idle_resume').fetchone()[0]
                    self.db.execute('INSERT INTO demo_idle_resume VALUES(?,?)',(resume_revision,encoded(dict(
                        version=1,baseline_at=meta[0],previous_checked_at=meta[1],verified_through_ms=snapshot['received_at_ms'],
                        cycle_sequence=state[0],archive_hash=state[1],
                        verified_at=finished,epoch=self.authority.epoch,revision=resume_revision,audit_revision=meta[3]+1,
                        snapshot_hash=snapshot_digest(snapshot),bill_evidence_hash=digest(bills)))))
                self.db.execute('UPDATE demo_fill_audit_meta SET last_checked=?,revision=? WHERE singleton=1',(finished,meta[3]+1))
                verified=self._certify(snapshot,finished,meta[3]+1)
                self._entry_receipt(verified,state[0])
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK'); raise
            return verified
        except Exception:
            raise DemoFillAuditError('Idle cycle audit incomplete; entry remains blocked') from None
