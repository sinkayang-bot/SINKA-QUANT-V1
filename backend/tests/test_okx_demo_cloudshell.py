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


def test_manual_close_mode_never_starts_order_worker(monkeypatch,capsys):
    import okx_demo_manual_close as close
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope'},True))
    monkeypatch.setattr(setup,'execute',lambda *a,**k:pytest.fail('Trading worker called during manual close'))
    async def done(*args): return dict(phase='manual_test_closed_and_archived',new_entry_allowed=False,orders_sent=0)
    monkeypatch.setattr(close,'close_test',done)
    assert setup.main(['--close-reviewed-test'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False and result['closure']['orders_sent']==0
    async def fail(*args): raise close.ManualCloseBlocked('composite_exchange_reads')
    monkeypatch.setattr(close,'close_test',fail)
    assert setup.main(['--close-reviewed-test'])==2
    assert 'composite_exchange_reads' in json.loads(capsys.readouterr().out)['reason']


def test_strategy_close_mode_is_explicit_and_never_starts_order_worker(monkeypatch,capsys):
    import okx_demo_manual_close as close
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope'},True))
    monkeypatch.setattr(setup,'execute',lambda *a,**k:pytest.fail('Order worker called'))
    async def done(*args,**kwargs):
        assert kwargs=={'allow_strategy':True}
        return dict(phase='tracked_strategy_exit_closed_and_archived',orders_sent=0)
    monkeypatch.setattr(close,'close_test',done)
    assert setup.main(['--close-reviewed-trade'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False and result['closure']['orders_sent']==0


def test_trade_review_error_surfaces_safe_stage_not_exception_contents(monkeypatch,capsys):
    import okx_demo_manual_review as review
    from okx_demo_bill_reader import DemoBillReadError
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope'},True))
    async def failed(*args):
        raise review.TradeReviewBlocked('bill_bundle',DemoBillReadError('private-key',stage='snapshot_before',kind='timeout'))
    monkeypatch.setattr(review,'audit_trade',failed)
    assert setup.main(['--audit-trade'])==2
    output=capsys.readouterr().out;result=json.loads(output)
    assert result['read_diagnostics']['read_stage']=='snapshot_before'
    assert result['read_diagnostics']['kind']=='timeout'
    assert 'private-key' not in output


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


def test_rejected_algo_type_is_identified_and_never_treated_as_empty(monkeypatch):
    import httpx
    fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
    def override(request):
        if request.url.path.endswith('/orders-algo-pending') and request.url.params.get('ordType')=='chase':
            return httpx.Response(200,json={'code':'51000','msg':'private-response','data':[]})
    fixture.response_override=override
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('synthetic-private-key','test','test')))
    assert str(caught.value)=='demo_pending_algos_chase_api_rejected_code_51000'
    assert 'private' not in str(caught.value)
    assert all(request.method=='GET' for request in fixture.calls)


def test_algo_http_failure_retains_type_and_status_only(monkeypatch):
    import httpx
    fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
    def override(request):
        if request.url.path.endswith('/orders-algo-pending') and request.url.params.get('ordType')=='trigger':
            return httpx.Response(429,json={'code':'50011','msg':'private-response','data':[]})
    fixture.response_override=override
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('test','test','test')))
    assert str(caught.value)=='demo_pending_algos_trigger_read_failed_http_429'


def test_diagnostics_disclose_shape_not_ids_sizes_or_private_payload(monkeypatch):
    fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
    fixture.algos['oco']=[dict(algoId='777123456',instId='BTC-USDT-SWAP',ordType='oco',state='unsupported-private-state',sz='12345.6789')]
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('test','test','test')))
    report=caught.value.diagnostics
    assert report['types']['oco']['scan']=='type_or_state_mismatch'
    assert report['types']['oco']['states']==['missing_or_other']
    assert report['types']['oco']['first_page_count']==1
    for private in ('777123456','12345.6789','unsupported-private-state','BTC-USDT-SWAP'):
        assert private not in json.dumps(report)


def test_cross_type_duplicate_is_diagnosed_without_exposing_identity(monkeypatch):
    fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
    for kind in ('oco','conditional'):
        fixture.algos[kind]=[dict(algoId='777123456',instId='BTC-USDT-SWAP',ordType=kind,state='live')]
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.account_check(DemoCredentials('test','test','test')))
    assert caught.value.diagnostics['duplicate_ids_across_types']==1
    assert '777123456' not in json.dumps(caught.value.diagnostics)


def test_old_python_is_blocked_before_storage_or_secrets(monkeypatch,capsys):
    monkeypatch.setattr(setup.sys,'version_info',(3,10,12))
    monkeypatch.setattr(setup,'credentials',lambda:pytest.fail('Secret accessed'))
    monkeypatch.setattr(setup,'persistent_storage',lambda path:pytest.fail('Storage modified'))
    assert setup.main(['--check'])==2
    assert json.loads(capsys.readouterr().out)['reason']=='python_3_11_or_newer_required'


def test_paused_order_still_prevents_first_account_binding(monkeypatch):
    fixture=Fixture(); fixture.balance['details'][0]['cashBal']='10000'
    fixture.algos['oco']=[dict(algoId='777',instId='BTC-USDT-SWAP',ordType='oco',state='pause')]
    monkeypatch.setattr(setup,'OKXDemoReadTransport',lambda creds,**opts:fixture.reader().transport)
    with pytest.raises(setup.SetupBlocked,match='first_binding_requires_flat_clean_account'):
        asyncio.run(setup.account_check(DemoCredentials('test','test','test')))


@pytest.mark.parametrize('acceptance',[False,True])
def test_resume_flag_reaches_worker_without_resetting_baseline(monkeypatch,capsys,acceptance):
    captured={}
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    monkeypatch.setattr(setup,'credentials',lambda:DemoCredentials('synthetic','synthetic','synthetic'))
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope','uid':'123'},True))
    monkeypatch.setattr(setup,'command',lambda args:'v24.21.0')
    def execute(env):
        captured.update(env)
        return {'phase':'stopped_no_active_intent'},0
    monkeypatch.setattr(setup,'execute',execute)
    assert setup.main(['--run','--resume-idle','--scan-markets','20','--seconds',
                       '7200' if acceptance else '900']+(['--acceptance-window'] if acceptance else []))==0
    assert captured['DEMO_ACCEPTANCE_WINDOW']==('1' if acceptance else '0')
    assert captured['DEMO_MAX_SECONDS']==('7200' if acceptance else '900')
    assert captured['DEMO_SCAN_MARKETS']=='20'
    assert captured['DEMO_RESUME_IDLE']=='1'
    assert captured['DEMO_INITIALIZE_BASELINE']=='0'
    assert captured['DEMO_PROGRESS']=='1'
    assert captured['DEMO_DAILY_LIMITS']=='0'
    assert captured['DEMO_STRATEGY_PROFILE']=='expanded'
    assert setup.main(['--run','--resume-idle','--daily-limits'])==0
    assert captured['DEMO_DAILY_LIMITS']=='1'
    assert 'synthetic' not in capsys.readouterr().out


def test_one_shot_entry_uses_same_worker_and_bounded_runtime(monkeypatch,capsys):
    captured={}
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    monkeypatch.setattr(setup,'credentials',lambda:DemoCredentials('synthetic','synthetic','synthetic'))
    monkeypatch.setattr(setup,'prepare',lambda *a,**k:({'account_scope':'scope','uid':'123'},True))
    monkeypatch.setattr(setup,'command',lambda args:'v24.21.0')
    def execute(env):
        captured.update(env)
        return {'phase':'test_result'},0
    monkeypatch.setattr(setup,'execute',execute)
    assert setup.main(['--run','--resume-idle','--test-entry'])==0
    assert captured['DEMO_EXECUTION_TEST']=='1'
    assert captured['DEMO_MAX_SECONDS']=='90'
    assert captured['DEMO_SCAN_MARKETS']=='1'
    assert captured['DEMO_LEDGER_PATH'].endswith('/sinka-demo-trial/ledger.sqlite')
    assert captured['DEMO_INITIALIZE_BASELINE']=='0'
    assert 'synthetic' not in capsys.readouterr().out


def test_recover_only_uses_existing_worker_without_entry_or_baseline_optin(monkeypatch,capsys):
    captured={};prepare_args={}
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    monkeypatch.setattr(setup,'credentials',lambda:DemoCredentials('synthetic','synthetic','synthetic'))
    def prepare(*args,**kwargs):
        prepare_args.update(kwargs);return {'account_scope':'scope','uid':'123'},False
    monkeypatch.setattr(setup,'prepare',prepare)
    monkeypatch.setattr(setup,'command',lambda args:'v24.21.0')
    def execute(env):
        captured.update(env);return {'phase':'recovery_result'},0
    monkeypatch.setattr(setup,'execute',execute)
    assert setup.main(['--run','--recover-only','--seconds','90'])==0
    assert captured['DEMO_RECOVER_ONLY']=='1'
    assert captured['DEMO_ARM_ENTRIES']=='0'
    assert captured['DEMO_EXECUTION_TEST']=='0'
    assert captured['DEMO_INITIALIZE_BASELINE']=='0'
    assert prepare_args['recovery_only'] is True
    assert 'synthetic' not in capsys.readouterr().out


def test_startup_snapshot_retries_only_complete_get_reads():
    from unittest.mock import AsyncMock
    from okx_demo_snapshot import DemoSnapshotError
    reader=SimpleNamespace(collect=AsyncMock(side_effect=[
        DemoSnapshotError('private',stage='snapshot_consistency',kind='validation_failed',issue='snapshot_changed'),
        DemoSnapshotError('private',stage='inventory_second_pass',kind='timeout'),{'complete':True}]))
    assert asyncio.run(setup.startup_snapshot(reader))=={'complete':True}
    assert reader.collect.await_count==3


def test_startup_snapshot_exhaustion_reports_safe_diagnostics():
    from unittest.mock import AsyncMock
    from okx_demo_snapshot import DemoSnapshotError
    reader=SimpleNamespace(collect=AsyncMock(side_effect=DemoSnapshotError(
        'private account',stage='snapshot_consistency',kind='validation_failed',issue='snapshot_expired')))
    with pytest.raises(setup.SetupBlocked) as caught:
        asyncio.run(setup.startup_snapshot(reader))
    assert reader.collect.await_count==3
    assert caught.value.diagnostics==dict(snapshot_stage='snapshot_consistency',
        failure_kind='validation_failed',snapshot_issue='snapshot_expired',read_attempts=3)
    assert 'private' not in str(caught.value.diagnostics)


def test_startup_snapshot_identity_validation_is_never_retried():
    from unittest.mock import AsyncMock
    from okx_demo_snapshot import DemoSnapshotError
    reader=SimpleNamespace(collect=AsyncMock(side_effect=DemoSnapshotError(
        'private account',stage='account_config',kind='validation_failed')))
    with pytest.raises(setup.SetupBlocked): asyncio.run(setup.startup_snapshot(reader))
    assert reader.collect.await_count==1


def test_startup_snapshot_diagnostics_reach_final_control_report(monkeypatch,capsys):
    monkeypatch.setattr(setup,'persistent_storage',lambda path:None)
    from okx_demo_control_runner import safe_report
    monkeypatch.setattr(setup,'credentials',lambda:DemoCredentials('test','test','test'))
    def fail(*args,**kwargs):
        raise setup.SetupBlocked('demo_full_snapshot_inconsistent_or_expired',diagnostics={
            'snapshot_stage':'snapshot_consistency','snapshot_issue':'snapshot_changed',
            'failure_kind':'validation_failed','read_attempts':3})
    monkeypatch.setattr(setup,'prepare',fail)
    assert setup.main(['--check'])==2
    result=safe_report(json.loads(capsys.readouterr().out))
    assert result['read_diagnostics']['snapshot_issue']=='snapshot_changed'
    assert result['read_diagnostics']['read_attempts']==3

@pytest.mark.parametrize('args',[
    ['--run','--seconds','901'],
    ['--run','--acceptance-window','--test-entry'],
    ['--run','--acceptance-window','--recover-only'],
    ['--run','--acceptance-window','--initialize-baseline'],
    ['--check','--acceptance-window'],
])
def test_extended_window_cannot_bypass_admission_or_reset_baseline(monkeypatch,capsys,args):
    monkeypatch.setattr(setup,'credentials',lambda:pytest.fail('Unexpected credential access'))
    assert setup.main(args)==2
    result=json.loads(capsys.readouterr().out)
    assert result['reason']=='explicit_strategy_acceptance_window_required'
    assert not result['orders_enabled'] and not result['live_trading_enabled']


def test_native_close_mode_never_calls_order_worker_and_requires_archival(monkeypatch,capsys):
    import okx_demo_reconciliation as native
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    def prepare(*a,**k):
        assert k['require_binding'] is True
        return {'account_scope':'scope'},True
    monkeypatch.setattr(setup,'prepare',prepare)
    monkeypatch.setattr(setup,'execute',lambda *a,**k:pytest.fail('Order worker called'))
    async def done(*a): return dict(phase='cycle_archived',ledger_archived=True,new_entry_allowed=False,orders_sent=0)
    monkeypatch.setattr(native,'close_native',done)
    assert setup.main(['--close-reviewed-native'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False and result['closure']['new_entry_allowed'] is False
    async def blocked(*a): return dict(phase='blocked',new_entry_allowed=False,orders_sent=0)
    monkeypatch.setattr(native,'close_native',blocked)
    assert setup.main(['--close-reviewed-native'])==2
    assert json.loads(capsys.readouterr().out)['phase']=='blocked'


def test_unfilled_resolution_is_explicit_and_never_starts_trading(monkeypatch,capsys):
    import okx_demo_unfilled as unfilled
    monkeypatch.setattr(setup,'persistent_storage',lambda root:None)
    monkeypatch.setattr(setup,'credentials',lambda:object())
    def prepare(*args,**kwargs):
        assert kwargs['require_binding'] is True and kwargs['recovery_only'] is True
        return {'account_scope':'synthetic'},True
    monkeypatch.setattr(setup,'prepare',prepare)
    monkeypatch.setattr(setup,'execute',lambda *a,**k:pytest.fail('Order worker called'))
    async def done(*a): return dict(phase='unfilled_attempt_archived_halted',attempt_archived=True,new_entry_allowed=False,orders_sent=0)
    monkeypatch.setattr(unfilled,'resolve_unfilled',done)
    assert setup.main(['--resolve-unfilled'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['orders_enabled'] is False and result['closure']['new_entry_allowed'] is False
