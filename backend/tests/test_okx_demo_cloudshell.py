"""Synthetic Secret values and intercepted GETs; never reads actual credentials."""
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import okx_demo_cloudshell as setup
from okx_demo_transport import DemoCredentials
from test_okx_demo_snapshot import Fixture


def test_plan_never_reads_secrets_or_account(monkeypatch,capsys):
    monkeypatch.setattr(setup,'credentials',lambda:pytest.fail('Secret resolution attempted'))
    assert setup.main([])==0
    result=json.loads(capsys.readouterr().out)
    assert result['phase']=='plan' and result['orders_enabled'] is False


def test_fixed_secret_versions_are_pinned_and_values_are_not_printed(monkeypatch):
    calls=[]
    def command(args):
        calls.append(args)
        if 'describe' in args:
            return json.dumps(dict(name='projects/123/secrets/name/versions/7',state='ENABLED'))
        return 'synthetic-secret-value\n'
    monkeypatch.setattr(setup,'command',command)
    creds=setup.credentials()
    assert creds.key=='synthetic-secret-value'
    assert 'synthetic-secret-value' not in repr(creds)
    assert len(calls)==6
    assert all(call[-3:]==['--project','sinka-quant','--quiet'] for call in calls if 'access' in call)
    assert all(call[4]=='7' for call in calls if 'access' in call)
    assert {call[call.index('--secret')+1] for call in calls}==set(setup.SECRET_NAMES)


@pytest.mark.parametrize('info',[dict(name='versions/latest',state='ENABLED'),dict(name='versions/7',state='DISABLED')])
def test_bad_or_disabled_version_cannot_resolve_payload(monkeypatch,info):
    calls=[]
    monkeypatch.setattr(setup,'command',lambda args:calls.append(args) or json.dumps(info))
    with pytest.raises(setup.SetupBlocked): setup.credentials()
    assert len(calls)==1


def test_cloud_error_details_are_sanitized(monkeypatch):
    monkeypatch.setattr(setup.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='private',stderr='private'))
    with pytest.raises(setup.SetupBlocked) as caught: setup.command(['gcloud'])
    assert 'private' not in str(caught.value)


def test_binding_reads_are_structurally_get_only(monkeypatch):
    fixture=Fixture()
    fixture.balance['details'][0]['cashBal']='10000'
    def transport(creds,**opts): return fixture.reader().transport
    monkeypatch.setattr(setup,'OKXDemoReadTransport',transport)
    binding,flat=asyncio.run(setup.account_check(DemoCredentials('test','test','test')))
    assert flat is True and binding['uid']==fixture.config['uid']
    assert all(request.method=='GET' for request in fixture.calls)
    fixture.config['perm']='read_only'
    with pytest.raises(setup.SetupBlocked,match='demo_trade_permission_missing'):
        asyncio.run(setup.account_check(DemoCredentials('test','test','test')))


def test_pinned_account_cannot_be_replaced(monkeypatch):
    fixture=Fixture()
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked,match='pinned_demo_account_mismatch'):
        asyncio.run(setup.account_check(DemoCredentials('test','test','test'),dict(uid='wrong')))
    assert len(fixture.calls)==1


def test_existing_ledger_without_binding_cannot_be_adopted(monkeypatch,tmp_path):
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    (tmp_path/'ledger.sqlite').write_text('existing')
    with pytest.raises(setup.SetupBlocked,match='prior_pinned_binding_required'):
        setup.prepare(tmp_path,DemoCredentials('test','test','test'))


def test_binding_is_saved_privately_once_and_never_overwritten(monkeypatch,tmp_path):
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    async def check(creds,binding): return dict(version=1,uid='123',account_scope='scope',symbol=setup.SYMBOL),True
    monkeypatch.setattr(setup,'account_check',check)
    setup.prepare(tmp_path,DemoCredentials('test','test','test'))
    assert (tmp_path/'account.json').stat().st_mode & 0o777==0o600
    original=(tmp_path/'account.json').read_bytes()
    setup.prepare(tmp_path,DemoCredentials('test','test','test'))
    assert (tmp_path/'account.json').read_bytes()==original


def test_ephemeral_storage_blocks_before_secret_access(monkeypatch,capsys):
    def fail(path): raise setup.SetupBlocked('persistent_local_disk_not_verified')
    monkeypatch.setattr(setup,'persistent_storage',fail)
    monkeypatch.setattr(setup,'credentials',lambda:pytest.fail('Secret accessed'))
    assert setup.main(['--check'])==2
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False


def test_check_mode_never_calls_trading_worker(monkeypatch,capsys):
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    monkeypatch.setattr(setup,'credentials',lambda:DemoCredentials('synthetic','synthetic','synthetic'))
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({},True))
    monkeypatch.setattr(setup,'execute',lambda env:pytest.fail('Trading worker started'))
    assert setup.main(['--check'])==0
    output=capsys.readouterr().out
    assert 'synthetic' not in output
    assert json.loads(output)['phase']=='demo_account_verified'


def test_storage_requires_a_distinct_persistent_mount_and_actual_sqlite_lock(monkeypatch,tmp_path):
    monkeypatch.setattr(setup.Path,'home',classmethod(lambda cls:tmp_path))
    original=setup.Path.read_text
    def read(path,*args,**kwargs):
        if str(path)=='/proc/self/mountinfo':
            return f'36 25 8:1 / {tmp_path} rw - ext4 /dev/test rw\n'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(setup.Path,'read_text',read)
    root=tmp_path/'sinka-demo-trial'
    setup.persistent_storage(root)
    assert root.stat().st_mode&0o777==0o700
    assert (root/'storage-probe.sqlite').exists()


@pytest.mark.parametrize('mount',[
    '36 25 0:1 / / rw - overlay overlay rw\n',
    '36 25 8:1 / / rw - ext4 /dev/root rw\n',
])
def test_ephemeral_or_unseparated_root_filesystem_is_not_proof_of_persistence(monkeypatch,tmp_path,mount):
    monkeypatch.setattr(setup.Path,'home',classmethod(lambda cls:tmp_path))
    original=setup.Path.read_text
    monkeypatch.setattr(setup.Path,'read_text',lambda path,*a,**k:mount if str(path)=='/proc/self/mountinfo' else original(path,*a,**k))
    with pytest.raises(setup.SetupBlocked,match='persistent_local_disk_not_verified'):
        setup.persistent_storage(tmp_path/'sinka-demo-trial')


@pytest.mark.parametrize('change,reason',[
    ('position_mode','demo_net_position_mode_required'),
    ('account_level','demo_futures_or_multi_currency_account_required'),
    ('leverage','demo_isolated_leverage_1_to_3_required'),
    ('balance','demo_usdt_balance_missing_or_invalid'),
    ('instrument','demo_instrument_specification_unsupported'),
])
def test_diagnostic_identifies_supported_configuration_blocker_without_private_values(monkeypatch,change,reason):
    fixture=Fixture()
    fixture.balance['details'][0]['cashBal']='10000'
    if change=='position_mode': fixture.config['posMode']='long_short_mode'
    if change=='account_level': fixture.config['acctLv']='1'
    if change=='leverage': fixture.leverage[0]['lever']='10'
    if change=='balance': fixture.balance['details']=[]
    if change=='instrument': fixture.instrument['maxStopSz']=''
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('synthetic-private-key','test','test')))
    assert str(caught.value)==reason
    assert 'synthetic-private-key' not in str(caught.value)
    assert all(request.method=='GET' for request in fixture.calls)


def test_config_http_failure_exposes_only_status_not_headers_or_body(monkeypatch):
    import httpx
    fixture=Fixture()
    fixture.response_override=lambda request:httpx.Response(401,json={'code':'50113','msg':'private-provider-message'})
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('synthetic-private-key','test','test')))
    assert str(caught.value)=='demo_config_connection_failed_http_401'
    assert 'private' not in str(caught.value)
