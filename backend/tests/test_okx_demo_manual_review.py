"""Manual close reports must never grant trading authority or change a ledger."""
from copy import deepcopy
import json
import sqlite3

import pytest
from okx_demo_manual_review import assess, retained_test


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
