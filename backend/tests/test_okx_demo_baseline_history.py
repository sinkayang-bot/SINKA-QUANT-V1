"""Expired baseline rows need actual, exact, repeated exchange evidence."""
import asyncio
from copy import deepcopy

import pytest
from okx_demo_baseline_history import recover_baseline_history, with_baseline_history
from okx_demo_fill_audit import key, normalize_fill
from okx_demo_manual_review import assess
from okx_demo_reads import read_route
from test_okx_demo_manual_review import fixture


def prepared():
    saved, bundle, entry, close = fixture()
    now = bundle['bills']['through_ms']
    old = deepcopy(bundle['snapshot']['recent_fills'][0])
    old.update(tradeId='3', billId='3', ordId='30', fillTime=str(saved['begin']-86400000))
    old = normalize_fill(old, now)
    saved['baseline'] = {key(old): old}
    return saved, bundle, entry, close, now, old


class Port:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    async def account_read(self, kind, *, after=None):
        self.calls.append((kind, after))
        return dict(code='0', data=deepcopy(next(self.pages)))


def test_recovery_requires_two_real_reads_then_restores_strict_review():
    saved, bundle, entry, close, now, old = prepared()
    original = deepcopy(bundle)
    assert not assess(saved, bundle, entry, close)['checks']['baseline_fills_retained']
    port = Port([[old], [old]])
    history = asyncio.run(recover_baseline_history(saved, bundle['snapshot'], port, now))
    bundle['snapshot'] = with_baseline_history(bundle['snapshot'], saved, history, now)
    assert assess(saved, bundle, entry, close)['evidence_matches']
    assert len(original['snapshot']['recent_fills']) == 2
    assert port.calls == [('fills_history', None)] * 2
    assert bundle['snapshot']['recent_fills'][-1] == old
    bundle['snapshot']['cash_balance_usdt'] = '99'
    assert not assess(saved, bundle, entry, close)['evidence_matches']


@pytest.mark.parametrize('change', ['missing', 'fee', 'second_pass', 'duplicate', 'future'])
def test_missing_changed_or_ambiguous_history_never_passes(change):
    saved, bundle, _, _, now, old = prepared()
    rows = [deepcopy(old)]
    second = [deepcopy(old)]
    if change == 'missing': rows = []
    elif change == 'fee': rows[0]['fee'] = '-9'
    elif change == 'second_pass': second = []
    elif change == 'duplicate': rows.append(deepcopy(old))
    elif change == 'future': rows[0]['fillTime'] = str(now+1)
    with pytest.raises(ValueError):
        asyncio.run(recover_baseline_history(saved, bundle['snapshot'], Port([rows, second]), now))


def test_current_trade_cannot_be_reclassified_as_baseline_and_no_local_synthesis():
    saved, bundle, _, _, now, old = prepared()
    current = normalize_fill(bundle['snapshot']['recent_fills'][0], now)
    with pytest.raises(ValueError):
        with_baseline_history(bundle['snapshot'], saved, {key(current): current}, now)
    assert len(with_baseline_history(bundle['snapshot'], saved, {}, now)['recent_fills']) == 2
    saved['baseline'] = {key(current): current}
    with pytest.raises(ValueError):
        asyncio.run(recover_baseline_history(saved, {'recent_fills': []}, Port([]), now))


def test_present_baseline_does_not_fetch_history_and_route_is_fixed():
    saved, bundle, _, _, now, old = prepared()
    bundle['snapshot']['recent_fills'].append(old)
    port = Port([])
    assert asyncio.run(recover_baseline_history(saved, bundle['snapshot'], port, now)) == {}
    assert not port.calls
    assert read_route('fills_history', after='100') == '/api/v5/trade/fills-history?instType=SWAP&limit=100&after=100'
    with pytest.raises(ValueError): read_route('fills_history', symbol='BTC-USDT-SWAP')


def test_history_pagination_keeps_exact_cursor_and_ignores_nonbaseline_rows():
    saved, bundle, _, _, now, old = prepared()
    page=[]
    for identity in range(200,100,-1):
        row=deepcopy(old)
        row.update(billId=str(identity),tradeId=str(identity))
        page.append(row)
    port=Port([page,[old],page,[old]])
    history=asyncio.run(recover_baseline_history(saved,bundle['snapshot'],port,now))
    assert history == {key(old):old}
    assert port.calls == [('fills_history',None),('fills_history','101')]*2


def test_historical_transport_uses_demo_get_and_strips_unapproved_fields():
    import httpx
    from okx_demo_transport import DemoCredentials,OKXDemoReadTransport
    _, _, _, _, now, old=prepared()
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=dict(code='0',data=[dict(old,unapproved='private')]))
    transport=OKXDemoReadTransport(DemoCredentials('synthetic','synthetic','synthetic'),
        enable_demo_requests=True,clock=lambda:now,transport=httpx.MockTransport(handler))
    result=asyncio.run(transport.account_read('fills_history'))
    assert len(requests)==1 and requests[0].method=='GET'
    assert requests[0].headers['x-simulated-trading']=='1'
    assert requests[0].url.path=='/api/v5/trade/fills-history'
    assert result['data']==[old]
