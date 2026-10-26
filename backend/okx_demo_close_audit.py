"""Offline single OCO child close audit. No exchange calls or entry release.

A trusted caller supplies fresh algo/child detail groups. These are assertions,
not authentication. Funding/account bills remain unaudited, so no risk-close
producer or automatic intent unlock is provided here.
"""
from decimal import Decimal, localcontext
import json
from okx_demo_fill_audit import DemoFillAuditor, DemoFillAuditError, MAX_AUDIT_GAP_MS, encoded, ident
from okx_demo_protection import _plan_body
from okx_instruments import instrument_spec
from okx_reconcile import number


def truth(value):
    return value is True or value == "true"


def fixed(value):
    return format(value, 'f')


def spec_digest(row):
    return encoded({k:str(v) for k,v in instrument_spec(row).items()})


def group(evidence, name, snapshot, now):
    value=evidence[name]
    at=value['received_at_ms']
    if (value.get('complete') is not True or type(at) is not int
            or not snapshot['received_at_ms'] <= at <= now or now-at > 5000
            or not isinstance(value.get('rows'),list) or len(value['rows']) != 1
            or not isinstance(value['rows'][0],dict)):
        raise ValueError('Fresh single detail result required')
    return value['rows'][0]


def validate_lineage(body, entry, evidence, snapshot, now, scope):
    if (evidence.get('demo_verified') is not True or evidence.get('account_scope') != scope):
        raise ValueError('Same trusted demo account required')
    algo=group(evidence,'algo',snapshot,now)
    child=group(evidence,'child',snapshot,now)
    bracket=body['attachAlgoOrds'][0]
    opposite='sell' if body['side']=='buy' else 'buy'
    algo_id, child_id = ident(algo.get('algoId')), ident(child.get('ordId'))
    if (algo.get('algoClOrdId') != bracket['attachAlgoClOrdId']
            or algo.get('state') != 'effective' or algo.get('ordType') != 'oco'
            or algo.get('ordIdList') != [child_id] or algo.get('subAlgoIdList',[]) != []
            or algo.get('closeFraction','') != '' or algo.get('instType') != 'SWAP'
            or number(algo.get('sz')) != entry.filled
            or any(algo.get(k) != v for k,v in dict(instId=body['instId'],side=opposite,posSide='net',tdMode='isolated').items())
            or not truth(algo.get('reduceOnly'))
            or any(algo.get(k) != bracket[k] for k in ('tpOrdPx','slOrdPx','tpTriggerPxType','slTriggerPxType'))
            or any(number(algo.get(k)) != Decimal(bracket[k]) for k in ('tpTriggerPx','slTriggerPx'))):
        raise ValueError('Unsupported or mismatched protective lineage')
    if (child_id==entry.order_id or child.get('algoId') != algo_id
            or child.get('algoClOrdId') != bracket['attachAlgoClOrdId']
            or child.get('state') != 'filled' or child.get('ordType') != 'market'
            or not truth(child.get('reduceOnly'))
            or number(child.get('sz')) != entry.filled or number(child.get('accFillSz')) != entry.filled
            or any(child.get(k) != v for k,v in dict(instId=body['instId'],side=opposite,posSide='net',tdMode='isolated').items())):
        raise ValueError('Child not fully filled or not attributable to protective algo')
    return child_id,algo_id,opposite


class DemoClosedTradeAuditor(DemoFillAuditor):
    def __init__(self, authority):
        super().__init__(authority)
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_close_context ('
            'singleton INTEGER PRIMARY KEY CHECK(singleton=1),spec TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_entry_context (client_id TEXT PRIMARY KEY,spec TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_closed_trade ('
            'client_id TEXT PRIMARY KEY,child_id TEXT UNIQUE NOT NULL,body TEXT NOT NULL)')

    def _baseline_metadata(self, snapshot):
        self.db.execute('INSERT INTO demo_close_context VALUES(1,?)',(spec_digest(snapshot['instrument']),))

    def _lineage(self, body, entry, evidence, snapshot, now):
        return validate_lineage(body,entry,evidence,snapshot,now,self.authority.scope)

    def _audit_closed_in_transaction(self,snapshot,evidence,now,*,allow_historical=False):
        if type(allow_historical) is not bool: raise ValueError('Boolean historical close mode required')
        if not self.db.in_transaction:
            raise ValueError('Atomic close transaction required')
        incoming=self._snapshot(snapshot,now)
        current=self.authority._now(); self.authority._owner(current)
        self._snapshot(snapshot,current)
        meta=self.db.execute('SELECT baseline_at,last_checked,anchor,revision FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
        context=self.db.execute('SELECT spec FROM demo_close_context WHERE singleton=1').fetchone()
        bound=self.db.execute('SELECT c.spec FROM demo_entry_context c JOIN demo_intent i ON i.id=c.client_id WHERE i.dispatched=1').fetchall()
        if bound:
            if len(bound)!=1: raise ValueError('One entry specification required')
            context=bound[0]
        if not meta: raise ValueError('Original close baseline missing')
        if not context: raise ValueError('Original contract specification missing')
        if context[0]!=spec_digest(snapshot['instrument']):
            raise ValueError('Original contract specification changed')
        if (not 0<=now-meta[1] or (not allow_historical and now-meta[1]>MAX_AUDIT_GAP_MS)
                or (allow_historical and not 0<=now-meta[0]<=604800000)):
            raise ValueError('Close audit continuity window exceeded')
        if meta[2] is not None and meta[2] not in incoming:
            raise ValueError('Original fill checkpoint unavailable')
        if (snapshot['pending_orders'] or snapshot['pending_algos']
                or any(number(p.get('pos'),allow_negative=True) != 0 for p in snapshot['positions'])):
            raise ValueError('Flat account and complete protective cleanup required')
        if "entry" in evidence:
            if evidence.get("demo_verified") is not True or evidence.get("account_scope")!=self.authority.scope:
                raise ValueError("Same verified demo account required")
            incoming_entry=group(evidence,"entry",snapshot,now)
            client_id=incoming_entry.get("clOrdId")
            receipt=self.db.execute("SELECT exchange_order_id,entry_status FROM demo_execution WHERE id=?",(client_id,)).fetchone()
            if (not receipt or receipt[1]=="blocked_before_send"
                    or (receipt[0] is not None and receipt[0]!=incoming_entry.get("ordId"))):
                raise ValueError("Entry receipt identity mismatch")
            self.authority.journal._observe_in_transaction(client_id,incoming_entry)
            self.db.execute("UPDATE demo_execution SET observed_at_ms=?,exchange_order_id=COALESCE(exchange_order_id,?) WHERE id=?",
                (evidence["entry"]["received_at_ms"],ident(incoming_entry.get("ordId")),client_id))
        owned=self._owned_orders(snapshot,now)
        if len(owned) != 1:
            raise ValueError('Exactly one owned entry supported')
        entry_id,(entry,side)=next(iter(owned.items()))
        if entry.state != 'filled' or entry.filled != entry.requested or entry.filled<=0:
            raise ValueError('Terminal filled entry required')
        plan_json=self.db.execute('SELECT plan FROM demo_intent WHERE id=?',(entry.client_order_id,)).fetchone()[0]
        body,_=_plan_body(json.loads(plan_json))
        child_id,algo_id,opposite=self._lineage(body,entry,evidence,snapshot,now)
        for identity,fill in incoming.items():
            old=self.db.execute('SELECT body FROM demo_audit_fills WHERE identity=?',(identity,)).fetchone()
            if old:
                if old[0] != encoded(fill): raise ValueError('Recorded fill changed')
                continue
            expected_side=side if fill['ordId']==entry_id else opposite if fill['ordId']==child_id else None
            if (expected_side is None or fill['side'] != expected_side or fill['instId'] != entry.instrument
                    or int(fill['fillTime']) < meta[0]
                    or (fill['ordId']==entry_id and Decimal(fill['fillPnl']) != 0)):
                raise ValueError('Unowned or mismatched fill')
            self._put(fill,baseline=False)
        stored=[json.loads(r[0]) for r in self.db.execute('SELECT body FROM demo_audit_fills WHERE baseline=0')]
        if any(f['ordId'] not in (entry_id,child_id) for f in stored):
            raise ValueError('Unexpected ledger order')
        entries=[f for f in stored if f['ordId']==entry_id]
        exits=[f for f in stored if f['ordId']==child_id]
        with localcontext() as ctx:
            ctx.prec=512
            for rows in (entries,exits):
                if sum((Decimal(f['fillSz']) for f in rows),Decimal(0)) != entry.filled:
                    raise ValueError('Missing or excess trade fills')
            opened=max(int(f['fillTime']) for f in entries)
            closed=max(int(f['fillTime']) for f in exits)
            if min(int(f['fillTime']) for f in exits) < opened:
                raise ValueError('Exit predates completed entry')
            entry_value=sum((Decimal(f['fillSz'])*Decimal(f['fillPx']) for f in entries),Decimal(0))
            exit_value=sum((Decimal(f['fillSz'])*Decimal(f['fillPx']) for f in exits),Decimal(0))
            gross=(exit_value-entry_value)*instrument_spec(snapshot['instrument'])['ct_val']*(1 if side=='buy' else -1)
            reported=sum((Decimal(f['fillPnl']) for f in exits),Decimal(0))
            if abs(gross-reported)>Decimal('0.00000001'):
                raise ValueError('Reported realised P&L differs from fill arithmetic')
            fees=sum((Decimal(f['fee']) for f in stored),Decimal(0))
            result=dict(client_id=entry.client_order_id,entry_order_id=entry_id,child_order_id=child_id,
                algo_id=algo_id,symbol=entry.instrument,contracts=fixed(entry.filled),
                gross_usdt=fixed(gross),signed_fees_usdt=fixed(fees),trading_net_usdt=fixed(gross+fees),
                closed_at_ms=closed,fill_ids=sorted(f['instId']+':'+f['tradeId'] for f in stored),
                funding_audited=False,account_bills_audited=False,new_entry_allowed=False,submission_enabled=False)
        previous=self.db.execute('SELECT body FROM demo_closed_trade WHERE client_id=?',(entry.client_order_id,)).fetchone()
        if previous and previous[0] != encoded(result): raise ValueError('Closed trade cannot change')
        self.db.execute('INSERT OR IGNORE INTO demo_closed_trade VALUES(?,?,?)',(entry.client_order_id,child_id,encoded(result)))
        anchor=max(incoming,key=lambda k:int(incoming[k]['billId'])) if incoming else None
        finished=self.authority._now(); self.authority._owner(finished); self._snapshot(snapshot,finished)
        self.db.execute('UPDATE demo_fill_audit_meta SET last_checked=?,anchor=?,revision=? WHERE singleton=1',(finished,anchor,meta[3]+1))
        self.db.execute('UPDATE demo_authority SET last_clock=? WHERE singleton=1',(finished,))
        return result

    def audit_closed(self, snapshot, evidence):
        """Commit immutable trading P&L before funding. Never grants readiness."""
        try:
            now=self._begin_attempt()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result=self._audit_closed_in_transaction(snapshot,evidence,now)
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK'); raise
            return result
        except Exception:
            raise DemoFillAuditError('Closed-trade audit incomplete; entry remains blocked') from None
