"""Manual close reports must never grant trading authority or change a ledger."""
from decimal import Decimal
from copy import deepcopy
import json
import sqlite3

import pytest
from okx_demo_manual_review import assess, retained_test,protection_detail


def fixture():
    start=1800000000000; symbol='BTC-USDT-SWAP'; client='sk'+'a'*28
    body=dict(instId=symbol,clOrdId=client,sz='1',side='buy')
    saved=dict(client=client,body=body,order_id='11',begin=start,cash='100',baseline={})
    fills=[]; bills=[]
    for index,side,order,pnl in [(1,'buy','11','0'),(2,'sell','12','1')]:
        f=dict(instId=symbol,tradeId=str(index),billId=str(index),ordId=order,side=side,
               fillTime=str(start+index*1000),fillSz='1',fillPx='100',fee='-0.1',feeCcy='USDT',fillPnl=pnl)
        fills.append(f)
        bills.append(dict(instType='SWAP',ccy='USDT',instId=symbol,billId=str(index),ts=f['fillTime'],type='2',
            subType=str(index),ordId=order,tradeId=str(index),sz='1',px='100',pnl=pnl,fee='-0.1',balChg='-0.1' if index==1 else '0.9'))
    pages=[dict(code='0',data=list(reversed(bills)))]
    bundle=dict(snapshot=dict(recent_fills=fills,pending_orders=[],pending_algos=[],positions=[],cash_balance_usdt='100.8'),
        bills=dict(through_ms=start+8000,passes=[deepcopy(pages),deepcopy(pages)]))
    entry=dict(body,ordId='11',state='filled',accFillSz='1',tdMode='isolated',posSide='net',ordType='market')
    close=dict(entry,ordId='12',clOrdId='',side='sell',reduceOnly='true')
    return saved,bundle,entry,close


def test_matching_manual_close_preserves_automatic_exit_failure():
    result=assess(*fixture())
    assert result['evidence_matches']
    assert result['fees_usdt']=='-0.2'
    assert result['cash_change_usdt']=='0.8'
    assert result['automatic_exit_verified'] is False
    assert result['new_entry_allowed'] is False and result['ledger_changed'] is False
    assert result['bill_fill_mismatches']=={}


def test_tracked_reduction_requires_exact_journal_and_full_cash_proof():
    saved,bundle,entry,close=fixture()
    close['clOrdId']='sr'+'b'*28
    body={k:close[k] for k in ('instId','tdMode','posSide','side','ordType','sz','clOrdId')}; body['reduceOnly']=True
    saved['reduction']=dict(client_id=close['clOrdId'],body=body,order_id=close['ordId'])
    report=assess(saved,bundle,entry,close)
    assert report['automatic_exit_verified']
    assert report['closure_source']=='tracked_emergency_exit'
    assert not report['test_roundtrip_verified'] and not report['native_protection_verified']
    assert not report['new_entry_allowed']
    bundle['snapshot']['cash_balance_usdt']='99'
    assert not assess(saved,bundle,entry,close)['automatic_exit_verified']
    saved['reduction']['order_id']='999'
    assert assess(saved,bundle,entry,close)['closure_source']=='manual_or_untracked_close'


def test_historical_protection_detail_never_claims_live_verification_or_prints_ids():
    saved,_,_,_=fixture()
    bracket=dict(attachAlgoClOrdId='sa'+'a'*28,tpTriggerPx='110',slTriggerPx='90',tpOrdPx='-1',slOrdPx='-1',tpTriggerPxType='last',slTriggerPxType='last')
    saved['body']['attachAlgoOrds']=[bracket]
    row=dict(algoClOrdId=bracket['attachAlgoClOrdId'],instId='BTC-USDT-SWAP',instType='SWAP',state='canceled',ordType='conditional',
        posSide='net',tdMode='isolated',side='sell',sz='1',reduceOnly='true',**{k:v for k,v in bracket.items() if k!='attachAlgoClOrdId'})
    report=protection_detail(saved,dict(code='0',data=[row]))
    assert report['detail_available'] and report['order_type']=='conditional'
    assert all(report['matches'].values())
    assert not report['live_protection_verified']
    assert bracket['attachAlgoClOrdId'] not in json.dumps(report)
    row['sz']=''
    report=protection_detail(saved,dict(code='0',data=[row]))
    assert 'sz' in report['missing_fields'] and not report['matches']['fixed_size']
    assert not protection_detail(saved,dict(code='51603',data=[]))['detail_available']


@pytest.mark.parametrize('field,value,label', [('ordId','99','order_identity'),('ts','1800000001000','bill_timestamp_before_fill'),
    ('sz','0.5','sz'),('px','101','px'),('fee','-0.2','fee'),('pnl','2','pnl'),('subType','1','side'),('tradeId','99','bill_without_matching_fill')])
def test_bill_difference_reports_fixed_field_without_exposing_identifiers(field,value,label):
    saved,bundle,entry,close=fixture()
    for pages in bundle['bills']['passes']: pages[0]['data'][0][field]=value
    report=assess(saved,bundle,entry,close)
    assert report['bill_fill_mismatches'][label]==1
    assert not report['evidence_matches']
    assert not report['new_entry_allowed']
    assert saved['client'] not in json.dumps(report)


@pytest.mark.parametrize('change', ['cash','missing_fill','foreign_bill','not_reduce_only','position','partial','entry_identity','changed_pass'])
def test_missing_or_conflicting_evidence_cannot_pass(change):
    saved,bundle,entry,close=fixture()
    if change=='cash': bundle['snapshot']['cash_balance_usdt']='101'
    elif change=='missing_fill': bundle['snapshot']['recent_fills'].pop()
    elif change=='foreign_bill':
        for pages in bundle['bills']['passes']: pages[0]['data'][0]['ordId']='99'
    elif change=='not_reduce_only': close['reduceOnly']='false'
    elif change=='position': bundle['snapshot']['positions']=[dict(pos='1')]
    elif change=='partial': close['accFillSz']='0.5'
    elif change=='entry_identity': entry['clOrdId']='wrong'
    else: bundle['bills']['passes'][1][0]['data'].pop()
    try: result=assess(saved,bundle,entry,close)
    except ValueError:
        assert change=='changed_pass'
    else:
        assert not result['evidence_matches']
        assert result['new_entry_allowed'] is False


def test_readonly_loader_preserves_original_tables_and_cash(tmp_path):
    saved,_,_,_=fixture(); path=tmp_path/'ledger.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE demo_intent(id,plan,dispatched); CREATE TABLE demo_execution(id,exchange_order_id);'
            'CREATE TABLE demo_fill_audit_meta(singleton,scope,baseline_at); CREATE TABLE demo_cash_baseline(singleton,cash);'
            'CREATE TABLE demo_audit_fills(identity,body,baseline);')
        plan=dict(purpose='execution_test',environment='demo',headers={'x-simulated-trading':'1'},body=saved['body'])
        db.execute('INSERT INTO demo_intent VALUES(?,?,1)',(saved['client'],json.dumps(plan)))
        db.execute('INSERT INTO demo_execution VALUES(?,?)',(saved['client'],'11'))
        db.execute('INSERT INTO demo_fill_audit_meta VALUES(1,?,?)',('scope',saved['begin']))
        db.execute("INSERT INTO demo_cash_baseline VALUES(1,'100')")
    before=path.read_bytes()
    assert retained_test(path,'scope')==saved
    assert path.read_bytes()==before
    with pytest.raises(ValueError): retained_test(path,'other')
    assert path.read_bytes()==before


def test_missing_ledger_is_not_created(tmp_path):
    path=tmp_path/'missing.sqlite'
    with pytest.raises(sqlite3.OperationalError): retained_test(path,'scope')
    assert not path.exists()


def test_exchange_decimal_tail_is_reported_but_missing_money_is_not_tolerated():
    saved,bundle,entry,close=fixture()
    bundle['snapshot']['recent_fills'][1]['fillPnl']='1.0000000000000026'
    for pages in bundle['bills']['passes']:
        pages[0]['data'][0].update(pnl='1.0000000000000026',balChg='0.9000000000000026')
    result=assess(saved,bundle,entry,close)
    assert result['evidence_matches']
    assert result['cash_bill_rounding_delta_usdt']=='-0.0000000000000026'
    assert result['cash_rounding_tolerance_usdt']=='0.0000000001'
    bundle['snapshot']['cash_balance_usdt']='100.8000000002'
    assert not assess(saved,bundle,entry,close)['evidence_matches']
    bundle['snapshot']['cash_balance_usdt']='100.8'
    bundle['bills']['passes'][1][0]['data'].pop()
    with pytest.raises(ValueError): assess(saved,bundle,entry,close)


def test_explicit_isolated_margin_changes_reconcile_only_when_returned_in_full():
    saved,bundle,entry,close=fixture()
    for pages in bundle['bills']['passes']:
        opened,exited=reversed(pages[0]['data'])
        opened.update(balChg='-10.1',posBalChg='10')
        exited.update(balChg='10.9',posBalChg='-10')
    report=assess(saved,bundle,entry,close)
    assert report['evidence_matches']
    assert report['position_transfer_bill_count']==2
    assert report['position_cash_change_usdt']=='0'
    assert report['automatic_exit_verified'] is False and report['new_entry_allowed'] is False
    for pages in bundle['bills']['passes']:
        pages[0]['data'][0].update(balChg='9.9',posBalChg='-9')
    bundle['snapshot']['cash_balance_usdt']='99.8'
    report=assess(saved,bundle,entry,close)
    assert report['checks']['cash_matches_bills']
    assert report['checks']['all_bills_match_fills']
    assert not report['checks']['position_cash_returns_to_zero']
    assert not report['evidence_matches']


def test_unexplained_margin_change_cannot_be_inferred_from_total_cash_match():
    saved,bundle,entry,close=fixture()
    for pages in bundle['bills']['passes']:
        opened,exited=reversed(pages[0]['data'])
        opened['balChg']='-10.1'; exited['balChg']='10.9'
    report=assess(saved,bundle,entry,close)
    assert report['checks']['cash_matches_bills']
    assert report['bill_fill_mismatches']['balance_change_equation']==2
    assert not report['evidence_matches']


def test_audit_cli_cannot_start_worker_or_enable_orders(monkeypatch,capsys):
    import okx_demo_cloudshell as setup
    import okx_demo_manual_review as review
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope'},True))
    monkeypatch.setattr(setup,'execute',lambda *a,**k:pytest.fail('Worker must not run'))
    async def audit(*args): return assess(*fixture())
    monkeypatch.setattr(review,'audit_trade',audit)
    assert setup.main(['--audit-trade'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False
    assert result['review']['automatic_exit_verified'] is False


def test_audit_cli_failure_preserves_ledger_and_sanitizes_details(monkeypatch,capsys):
    import okx_demo_cloudshell as setup
    import okx_demo_manual_review as review
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope'},True))
    async def fail(*args): raise ValueError('synthetic-private-detail')
    monkeypatch.setattr(review,'audit_trade',fail)
    assert setup.main(['--audit-trade'])==2
    output=capsys.readouterr().out
    assert 'synthetic-private-detail' not in output
    assert json.loads(output)['orders_enabled'] is False


def test_observed_multi_fill_cash_tail_passes_but_larger_discrepancy_stays_blocked():
    saved,bundle,entry,close=fixture()
    tail=Decimal('0.000000000015152')
    original=Decimal(bundle['snapshot']['cash_balance_usdt'])
    bundle['snapshot']['cash_balance_usdt']=str(original+tail)
    report=assess(saved,bundle,entry,close)
    assert report['evidence_matches']
    assert report['cash_bill_rounding_delta_usdt']=='0.000000000015152'
    assert report['bill_fill_mismatches']=={}
    bundle['snapshot']['cash_balance_usdt']=str(original+Decimal('0.000000000100001'))
    assert not assess(saved,bundle,entry,close)['checks']['cash_matches_bills']
    bundle['snapshot']['cash_balance_usdt']=str(original+tail)
    # Even a smaller fee mismatch is rejected; tolerance is not per-fill.
    bundle['snapshot']['recent_fills'][0]['fee']='-0.100000000000001'
    report=assess(saved,bundle,entry,close)
    assert not report['checks']['all_bills_match_fills']
    assert not report['evidence_matches']
