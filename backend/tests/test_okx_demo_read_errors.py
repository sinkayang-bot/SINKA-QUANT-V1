"""Read diagnostics and bounded GET retries never expose exception contents."""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock,patch
import pytest

from okx_demo_read_errors import failure_kind
from okx_demo_bill_reader import DemoBillReadError
from okx_demo_snapshot import DemoSnapshotError
from okx_demo_manual_review import TradeReviewBlocked,audit_trade
from okx_demo_bill_reader import DemoBillReader
from test_okx_demo_snapshot import Fixture
from test_okx_demo_manual_review import fixture as review_evidence


def test_nested_failures_use_fixed_labels_not_raw_text():
    for error,expected in [(TimeoutError('private-key'), 'timeout'),
            (ValueError('private-uid'),'validation_failed'),
            (sqlite3.OperationalError('private-path'),'ledger_read_error'),
            (ExceptionGroup('private-message',[TimeoutError('private'),TimeoutError()]),'timeout')]:
        assert failure_kind(error)==expected
    wrapped=TradeReviewBlocked('bill_bundle',DemoBillReadError('private-server-message',
        stage='snapshot_before',kind='timeout',snapshot_stage='inventory_first_pass'))
    assert wrapped.diagnostics==dict(stage='bill_bundle',kind='timeout',read_stage='snapshot_before',snapshot_stage='inventory_first_pass')
    assert 'private' not in str(wrapped)+json.dumps(wrapped.diagnostics)
    assert failure_kind(DemoSnapshotError('private',stage='account_config',kind='validation_failed'))=='validation_failed'


def test_real_reader_config_failure_keeps_safe_snapshot_and_bill_stages():
    fixture=Fixture();fixture.config['uid']='999'
    with pytest.raises(DemoSnapshotError) as caught: asyncio.run(fixture.reader().collect())
    assert caught.value.stage=='account_config' and caught.value.kind=='validation_failed'
    reader=DemoBillReader(fixture.reader())
    with pytest.raises(DemoBillReadError) as caught: asyncio.run(reader.collect(begin_ms=fixture.now-1000))
    assert caught.value.stage=='snapshot_before'
    assert caught.value.snapshot_stage=='account_config'
    assert caught.value.kind=='validation_failed'
    assert reader.last_bundle is None


def test_transient_reads_retry_fresh_attempt_and_report_prior_failures():
    failure=TradeReviewBlocked('bill_bundle',DemoBillReadError('private',stage='snapshot_before',kind='timeout'))
    once=AsyncMock(side_effect=[failure,dict(evidence_matches=True,ledger_changed=False)])
    with patch('okx_demo_manual_review._audit_trade_once',once),patch('okx_demo_manual_review.asyncio.sleep',AsyncMock()):
        result=asyncio.run(audit_trade(None,{},None))
    assert result['read_attempts']==2 and once.await_count==2
    assert result['prior_read_failures'][0]['kind']=='timeout'
    assert result['ledger_changed'] is False


def test_optional_protection_lookup_timeout_does_not_discard_complete_cash_audit(monkeypatch):
    import okx_demo_manual_review as review
    saved,bundle,entry,closed=review_evidence();calls=[]
    saved['body']['attachAlgoOrds']=[{'attachAlgoClOrdId':'synthetic'}]
    class Transport:
        async def lookup_entry(self,*args): calls.append('entry');return dict(code='0',data=[entry])
        async def lookup_child(self,*args): calls.append('exit');return dict(code='0',data=[closed])
        async def lookup_algo(self,*args): calls.append('protection');raise TimeoutError('private-upstream')
    class Bills:
        async def collect(self,**kwargs): return bundle
    monkeypatch.setattr(review,'retained_test',lambda *a,**k:saved)
    monkeypatch.setattr(review,'OKXDemoReadTransport',lambda *a,**k:Transport())
    monkeypatch.setattr(review,'DemoSnapshotReader',lambda *a,**k:object())
    monkeypatch.setattr(review,'DemoBillReader',lambda *a,**k:Bills())
    report=asyncio.run(review.audit_trade(None,dict(account_scope='scope',uid='123'),None))
    assert report['evidence_matches'] and report['read_attempts']==1
    assert report['protection_detail']['detail_available'] is False
    assert report['native_protection_verified'] is False
    assert calls==['entry','exit','protection']
    assert 'private' not in json.dumps(report)


@pytest.mark.parametrize('stage,kind,attempts',[('bill_bundle','timeout',3),
    ('bill_bundle','validation_failed',1),('retained_ledger','ledger_read_error',1)])
def test_failed_reads_are_bounded_without_retrying_validation_or_ledger(stage,kind,attempts):
    failure=TradeReviewBlocked(stage,DemoBillReadError('private',kind=kind))
    once=AsyncMock(side_effect=failure)
    with patch('okx_demo_manual_review._audit_trade_once',once),patch('okx_demo_manual_review.asyncio.sleep',AsyncMock()):
        with pytest.raises(TradeReviewBlocked) as caught: asyncio.run(audit_trade(None,{},None))
    assert once.await_count==attempts
    assert caught.value.diagnostics['read_attempts']==attempts
    assert 'private' not in json.dumps(caught.value.diagnostics)
