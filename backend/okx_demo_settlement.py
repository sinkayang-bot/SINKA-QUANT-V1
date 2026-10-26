"""Offline bounded USDT bill reconciliation and atomic daily-risk publication.

Two paginated scans and cash amounts are supplied by a trusted reader contract;
this module authenticates no API response and performs no network requests.
Reconciliation is as of the supplied scan, not a promise against late bills.
No intent release, credential access, or trading is implemented.
"""
from decimal import Decimal, localcontext
import json
from okx_demo_close_audit import DemoClosedTradeAuditor, fixed
from okx_demo_fill_audit import DemoFillAuditError, encoded, ident, money, MAX_AUDIT_GAP_MS
from okx_strategy_bridge import timestamp

# Fixed absolute allowance for demo API cash serialization tails (1e-10 USDT).
# Applies only to aggregate cash arithmetic; bill/fill fields remain exact.
# Never scale this with balance, fill count or an observed discrepancy.
CASH_ROUNDING_TOLERANCE=Decimal('0.0000000001')


def cash_matches(left,right):
    with localcontext() as ctx:
        ctx.prec=512
        return abs(Decimal(left)-Decimal(right))<=CASH_ROUNDING_TOLERANCE


def bill(raw):
    if not isinstance(raw,dict) or raw.get('ccy')!='USDT' or raw.get('instType')!='SWAP':
        raise ValueError('Unsupported bill currency or instrument type')
    result={k:ident(raw.get(k)) for k in ('billId','ts','type','subType')}
    result.update({k:raw.get(k,'') for k in ('ordId','tradeId','instId')})
    if any(not isinstance(result[k],str) or len(result[k])>128 for k in ('ordId','tradeId','instId')):
        raise ValueError('Invalid bill identity')
    for field in ('pnl','fee','balChg'):
        result[field]=money(raw.get(field))
    # Preserve explicit position cash changes. A missing field cannot explain
    # an account cash movement that differs from the matched fill's pnl+fee.
    if raw.get('posBalChg') not in (None,''):
        result['posBalChg']=money(raw['posBalChg'])
    for field in ('sz','px'):
        result[field]=money(raw.get(field),positive_only=True)
    return result


def scan(pages, begin, through):
    if not isinstance(pages,list) or not 1<=len(pages)<=6:
        raise ValueError('Bounded paginated bills required')
    result={}; cursor=None
    for index,page in enumerate(pages):
        if not isinstance(page,dict) or page.get('code')!='0' or not isinstance(page.get('data'),list) or len(page['data'])>100:
            raise ValueError('Failed bill page')
        rows=page['data']
        if (index<len(pages)-1 and len(rows)!=100) or (index==len(pages)-1 and len(rows)>=100):
            raise ValueError('Terminal short page required')
        for raw in rows:
            row=bill(raw); identity=row['billId']
            if identity in result or (cursor is not None and int(identity)>=cursor):
                raise ValueError('Repeated or regressing bill cursor')
            if not begin<=int(row['ts'])<=through:
                raise ValueError('Bill outside requested interval')
            result[identity]=row; cursor=int(identity)
            if len(result)>500: raise ValueError('Bill interval exceeds bound')
    return result


class DemoSettlementAuditor(DemoClosedTradeAuditor):
    def __init__(self, authority):
        super().__init__(authority)
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_cash_baseline ('
            'singleton INTEGER PRIMARY KEY CHECK(singleton=1),cash TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_account_bill (id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS demo_trade_settlement ('
            'client_id TEXT PRIMARY KEY,body TEXT NOT NULL)')

    def _baseline_metadata(self,snapshot):
        super()._baseline_metadata(snapshot)
        cash=money(snapshot.get('cash_balance_usdt'))
        self.db.execute('INSERT INTO demo_cash_baseline VALUES(1,?)',(cash,))

    def _settle_in_transaction(self,snapshot,evidence,now,*,allow_historical=False):
        if type(allow_historical) is not bool: raise ValueError('Boolean historical close mode required')
        if not self.db.in_transaction:
            raise ValueError('Atomic settlement transaction required')
        incoming=self._snapshot(snapshot,now)
        current=self.authority._now(); self.authority._owner(current); self._snapshot(snapshot,current)
        meta=self.db.execute('SELECT baseline_at,last_checked,anchor FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
        baseline=self.db.execute('SELECT cash FROM demo_cash_baseline WHERE singleton=1').fetchone()
        trades=self.db.execute('SELECT client_id,child_id,body FROM demo_closed_trade').fetchall()
        if not meta or not baseline or len(trades)!=1:
            raise ValueError('Original cash baseline and one audited close required')
        if not 0<=now-meta[1]<=MAX_AUDIT_GAP_MS or (meta[2] is not None and meta[2] not in incoming):
            raise ValueError('Close fill continuity lost')
        for identity,fill in incoming.items():
            saved=self.db.execute('SELECT body FROM demo_audit_fills WHERE identity=?',(identity,)).fetchone()
            if saved is None or saved[0]!=encoded(fill):
                raise ValueError('Unknown or changed post-close fill')
        client_id,child_id,trade_json=trades[0]; trade=json.loads(trade_json)
        through=timestamp(evidence['through_ms']); received=timestamp(evidence['received_at_ms'])
        begin=timestamp(evidence['begin_ms'])
        if (evidence.get('demo_verified') is not True or evidence.get('account_scope')!=self.authority.scope
                or evidence.get('query')!={'ccy':'USDT','begin':str(begin),'end':str(through),'limit':'100'}
                or begin!=meta[0] or through!=snapshot['received_at_ms']
                or not through<=received<=now or now-received>5000
                or through<trade['closed_at_ms']+5000):
            raise ValueError('Fresh full account bill interval required')
        if snapshot['pending_orders'] or snapshot['pending_algos'] or any(Decimal(p['pos'])!=0 for p in snapshot['positions']):
            raise ValueError('Account not flat or protective cleanup pending')
        owned=self._owned_orders(snapshot,now)
        if len(owned)!=1 or trade['entry_order_id'] not in owned:
            raise ValueError('Fresh original terminal order observation required')
        passes=evidence['passes']
        if not isinstance(passes,list) or len(passes)!=2:
            raise ValueError('Two independent complete bill scans required')
        rows=scan(passes[0],begin,through)
        if rows!=scan(passes[1],begin,through): raise ValueError('Bills changed between passes')
        fills={f['instId']+':'+f['tradeId']:f for f in (json.loads(r[0]) for r in self.db.execute('SELECT body FROM demo_audit_fills WHERE baseline=0'))}
        if sorted(fills)!=trade['fill_ids']: raise ValueError('Closed fill set changed')
        seen=set(); funding=Decimal(0); movement=Decimal(0); position_movement=Decimal(0)
        with localcontext() as ctx:
            ctx.prec=512
            for row in rows.values():
                pnl,fee,delta=(Decimal(row[k]) for k in ('pnl','fee','balChg'))
                position_delta=Decimal(row.get('posBalChg','0'))
                if delta+position_delta!=pnl+fee:
                    raise ValueError('Unsupported account/position balance movement')
                if row['instId']!=trade['symbol']: raise ValueError('Foreign account bill')
                if row['type']=='2' and row['subType'] in {'1','2','3','4','5','6'}:
                    identity=row['instId']+':'+ident(row['tradeId']); fill=fills.get(identity)
                    direction='buy' if row['subType'] in {'1','3','6'} else 'sell'
                    if (fill is None or identity in seen or direction!=fill['side']
                            or row['ordId']!=fill['ordId'] or int(row['ts'])<int(fill['fillTime'])
                            or any(Decimal(row[k])!=Decimal(fill[v]) for k,v in
                                (('sz','fillSz'),('px','fillPx'),('fee','fee'),('pnl','fillPnl')))):
                        raise ValueError('Bill differs from known trade fill')
                    seen.add(identity)
                elif row['type']=='8' and row['subType'] in {'173','174'}:
                    first=max(int(f['fillTime']) for f in fills.values() if f['ordId']==trade['entry_order_id'])
                    if (Decimal(row['sz'])!=Decimal(trade['contracts']) or row['ordId']!='' or row['tradeId']!='' or fee!=0 or position_delta!=0
                            or not first<=int(row['ts'])<=trade['closed_at_ms']
                            or (row['subType']=='173' and pnl>=0) or (row['subType']=='174' and pnl<=0)):
                        raise ValueError('Funding attribution or sign mismatch')
                    funding+=pnl
                else: raise ValueError('Unclassified transfer, liquidation or adjustment bill')
                movement+=delta
                position_movement+=position_delta
            if seen!=set(fills): raise ValueError('Trade bill missing')
            net=Decimal(trade['trading_net_usdt'])+funding
            cash=Decimal(money(snapshot.get('cash_balance_usdt')))
            cash_change=cash-Decimal(baseline[0])
            if position_movement!=0 or not cash_matches(movement,net) or not cash_matches(cash_change,movement):
                raise ValueError('USDT cash movement does not reconcile')
            result=dict(client_id=client_id,net_usdt=fixed(net),funding_usdt=fixed(funding),
                cash_before_usdt=baseline[0],cash_after_usdt=fixed(cash),bill_ids=sorted(rows,key=int),
                closed_at_ms=trade['closed_at_ms'],reconciled_through_ms=through,
                funding_audited=True,account_bills_audited=True,new_entry_allowed=False,submission_enabled=False)
            if cash_change!=movement or movement!=net:
                result.update(cash_bill_rounding_delta_usdt=fixed(cash_change-movement),
                    bill_model_rounding_delta_usdt=fixed(movement-net),
                    cash_rounding_tolerance_usdt=fixed(CASH_ROUNDING_TOLERANCE))
        old_rows={r[0]:r[1] for r in self.db.execute('SELECT id,body FROM demo_account_bill')}
        if any(identity not in rows or encoded(rows[identity])!=body for identity,body in old_rows.items()):
            raise ValueError('Previously audited bill changed or disappeared')
        for identity,row in rows.items():
            self.db.execute('INSERT OR IGNORE INTO demo_account_bill VALUES(?,?)',(identity,encoded(row)))
        old=self.db.execute('SELECT body FROM demo_trade_settlement WHERE client_id=?',(client_id,)).fetchone()
        if old:
            previous=json.loads(old[0]); comparable=dict(result,reconciled_through_ms=previous['reconciled_through_ms'])
            if previous!=comparable: raise ValueError('Finalized settlement changed')
            result=previous
        self.authority._record_close_in_transaction('settled:'+client_id,pnl_usdt=result['net_usdt'],
            closed_at_ms=result['closed_at_ms'],audit_verified=True,allow_historical=allow_historical)
        self.db.execute('INSERT OR IGNORE INTO demo_trade_settlement VALUES(?,?)',(client_id,encoded(result)))
        return result

    def settle(self,snapshot,evidence):
        try:
            now=self._begin_attempt()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result=self._settle_in_transaction(snapshot,evidence,now)
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK'); raise
            return result
        except Exception:
            raise DemoFillAuditError('Bill settlement incomplete; entry remains blocked') from None
