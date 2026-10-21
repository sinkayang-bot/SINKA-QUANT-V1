"""Cloud Shell demo acceptance, fixed Secret references, no secret output.

Default is a local plan. --check reads server Secrets and GETs only. --run is
an explicitly armed, bounded demo trial using a prior pinned account binding.
No cloud resource, IAM, scheduler, VM or Secret is created or modified.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from okx_demo_snapshot import DemoSnapshotReader,DemoSnapshotError,digits,PENDING_ALGO_STATES
from okx_demo_transport import DemoCredentials,OKXDemoReadTransport,DemoTransportError
from okx_demo_trading_worker import execute
from okx_reconcile import number
from okx_risk import positive
from okx_instruments import instrument_spec
from okx_demo_reads import ALGO_TYPES

PROJECT='sinka-quant'
SECRET_NAMES=('OKX_DEMO_TRADE_API_KEY','OKX_DEMO_TRADE_API_SECRET','OKX_DEMO_TRADE_API_PASSPHRASE')
SYMBOL='BTC-USDT-SWAP'


class SetupBlocked(RuntimeError):
    def __init__(self,reason,*,diagnostics=None):
        super().__init__(reason)
        self.diagnostics=diagnostics


async def algo_diagnostics(reader,transport,error):
    """Fixed labels/counts only. Never emit account IDs, order IDs, sizes or prices."""
    errors={
        'Invalid response identity':'invalid_identity',
        'Oversized inventory page':'oversized_page',
        'Duplicate or regressing inventory cursor':'duplicate_or_regressing_cursor',
        'Inventory instrument missing':'missing_instrument',
        'Inconsistent pending algo':'type_or_state_mismatch',
        'Inventory exceeds scan bound':'inventory_bound_exceeded',
        'Missing terminal inventory page':'terminal_page_missing',
    }
    def causes(value):
        children=getattr(value,'exceptions',None)
        if children is not None:
            return sorted({name for child in children for name in causes(child)})
        name=type(value).__name__
        return [name if name in {'AttributeError','TypeError','ValueError','TimeoutError','DemoTransportError'} else 'other']
    report={'python_version':'.'.join(map(str,sys.version_info[:3])),
            'group_error_types':causes(error),'types':{},'duplicate_ids_across_types':0}
    seen=set()
    for kind in ALGO_TYPES:
        info={}; report['types'][kind]=info
        try:
            response=await transport.account_read('algos',algo_type=kind)
            code=response.get('code')
            info['api_code']=code if isinstance(code,str) and re.fullmatch(r'[0-9]{1,8}',code) else 'unknown'
            if code!='0': continue
            rows=response.get('data',[])
            info['first_page_count']=len(rows)
            info['states']=sorted({row.get('state') if row.get('state') in PENDING_ALGO_STATES else 'missing_or_other' for row in rows})
            info['returned_types']=sorted({row.get('ordType') if row.get('ordType') in ALGO_TYPES else 'missing_or_other' for row in rows})
            info['missing_instrument_count']=sum(not bool(row.get('instId')) for row in rows)
            try:
                complete=await reader._scan('algos',algo_type=kind)
                info['scan']='verified'
                info['total_count']=len(complete)
                identities={row['algoId'] for row in complete}
                report['duplicate_ids_across_types']+=len(identities&seen)
                seen|=identities
            except ValueError as failure:
                info['scan']=errors.get(failure.args[0] if failure.args else '', 'schema_or_read_failed')
        except DemoTransportError as failure:
            info['scan']='transport_failed'
            info['http_status']=failure.http_status
        except Exception:
            info['scan']='schema_or_read_failed'
    return report


def command(args):
    try:
        value=subprocess.run(args,capture_output=True,text=True,check=False,timeout=30)
        if value.returncode!=0 or len(value.stdout)>16384: raise ValueError()
        return value.stdout
    except Exception:
        raise SetupBlocked('cloud_command_unavailable_or_denied') from None


def credentials():
    values=[]
    for name in SECRET_NAMES:
        raw=command(['gcloud','secrets','versions','describe','latest','--secret',name,
                     '--project',PROJECT,'--format=json','--quiet'])
        try:
            info=json.loads(raw)
            version=info['name'].split('/')[-1]
            if info.get('state')!='ENABLED' or not re.fullmatch(r'[1-9][0-9]{0,19}',version):
                raise ValueError()
        except Exception:
            raise SetupBlocked('new_trade_secret_version_unavailable') from None
        values.append(command(['gcloud','secrets','versions','access',version,'--secret',name,
                               '--project',PROJECT,'--quiet']).rstrip('\r\n'))
    try: return DemoCredentials(*values)
    except Exception: raise SetupBlocked('new_trade_secret_format_invalid') from None


def persistent_storage(root):
    """Require a mounted local disk, reject temp/overlay/network filesystems."""
    home=Path.home().resolve()
    if root != home/'sinka-demo-trial' or root.is_symlink():
        raise SetupBlocked('dedicated_home_storage_required')
    try:
        mounts=[]
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left,right=line.split(' - ',1)
            mount=left.split()[4].replace('\\040',' ').replace('\\134','\\')
            path=Path(mount)
            if path==home or path in home.parents:
                mounts.append((len(path.parts),right.split()[0]))
        selected=max(mounts) if mounts else (0,None)
        if selected[0]<=1 or selected[1] not in {'ext4','xfs','btrfs'}:
            raise SetupBlocked('persistent_local_disk_not_verified')
        root.mkdir(mode=0o700,exist_ok=True)
        root.chmod(0o700)
        if any((root/name).is_symlink() for name in ('account.json','ledger.sqlite','storage-probe.sqlite')):
            raise SetupBlocked('dedicated_storage_invalid')
        probe=root/'storage-probe.sqlite'
        one=sqlite3.connect(probe,isolation_level=None,timeout=0)
        two=None
        try:
            if one.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal': raise ValueError()
            one.execute('PRAGMA synchronous=FULL')
            one.execute('CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)')
            one.execute('BEGIN IMMEDIATE')
            two=sqlite3.connect(probe,isolation_level=None,timeout=0)
            try:
                two.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as error:
                if getattr(error,'sqlite_errorcode',None)!=sqlite3.SQLITE_BUSY: raise
            else: raise ValueError('Lock not enforced')
            one.execute('ROLLBACK')
        finally:
            if two is not None: two.close()
            one.close()
    except SetupBlocked: raise
    except Exception: raise SetupBlocked('persistent_storage_check_failed') from None


async def account_check(creds,binding=None,*,recovery_only=False):
    clock=lambda:int(time.time()*1000)
    transport=OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
    stage='demo_config_connection_failed'
    try:
        config=await transport.account_read('config')
        code=config.get('code')
        if code!='0':
            suffix=code if isinstance(code,str) and re.fullmatch(r'[0-9]{1,8}',code) else 'unknown'
            raise SetupBlocked('demo_config_api_rejected_code_'+suffix)
        if len(config.get('data',[]))!=1: raise SetupBlocked('demo_config_response_invalid')
        row=config['data'][0]
        stage='demo_config_identity_invalid'
        uid=digits(row.get('uid'))
        scope='okx-demo:'+hashlib.sha256(uid.encode()).hexdigest()[:24]
        expected={'version':1,'uid':uid,'account_scope':scope,'symbol':SYMBOL}
        if binding is not None and binding!=expected:
            raise SetupBlocked('pinned_demo_account_mismatch')
        if row.get('posMode')!='net_mode':
            raise SetupBlocked('demo_net_position_mode_required')
        if row.get('acctLv') not in {'2','3'}:
            raise SetupBlocked('demo_futures_or_multi_currency_account_required')
        reader=DemoSnapshotReader(transport,account_scope=scope,expected_uid=uid,symbol=SYMBOL,clock=clock,recovery_only=recovery_only)
        stage='demo_api_permission_configuration_invalid'
        mode=reader._config([row])
        if mode['order_permission_verified'] is not True:
            raise SetupBlocked('demo_trade_permission_missing')
        stage='demo_isolated_leverage_read_failed'
        leverage=await reader._read('leverage',symbol=SYMBOL)
        stage='demo_isolated_leverage_1_to_3_required'
        if (len(leverage)!=1 or leverage[0].get('instId')!=SYMBOL
                or leverage[0].get('mgnMode')!='isolated' or leverage[0].get('posSide')!='net'):
            raise ValueError()
        lever=positive(leverage[0].get('lever'),'leverage')
        if lever!=lever.to_integral_value() or not 1<=lever<=3: raise ValueError()
        stage='demo_usdt_balance_read_failed'
        balances=await reader._read('balance')
        stage='demo_usdt_balance_missing_or_invalid'
        if len(balances)!=1 or len(balances[0].get('details',[]))!=1: raise ValueError()
        detail=balances[0]['details'][0]
        if detail.get('ccy')!='USDT': raise ValueError()
        equity=positive(detail.get('eq'),'equity')
        available=min(number(detail.get('availEq')),number(detail.get('availBal')))
        if available>equity: raise ValueError()
        # A cash baseline is mandatory for settlement; distinguish absent cash now.
        number(detail.get('cashBal'),allow_negative=True)
        stage='demo_instrument_read_failed'
        instruments=await reader._read('instrument',symbol=SYMBOL)
        stage='demo_instrument_specification_unsupported'
        if len(instruments)!=1: raise ValueError()
        instrument_spec(instruments[0])
        positive(instruments[0].get('maxMktSz'),'market maximum')
        positive(instruments[0].get('maxStopSz'),'stop maximum')
        stage='demo_positions_inventory_invalid'
        await reader._read('positions')
        stage='demo_pending_orders_inventory_invalid'
        await reader._scan('orders')
        stage='demo_pending_algos_inventory_invalid'
        try:
            await reader._algos()
        except Exception as group_error:
            # Diagnose the failed group without treating any rejected type as empty.
            # These first-page probes grant no completeness proof or trading authority.
            details=await algo_diagnostics(reader,transport,group_error)
            for kind,info in details['types'].items():
                code=info.get('api_code')
                if code is not None and code!='0':
                    raise SetupBlocked('demo_pending_algos_'+kind+'_api_rejected_code_'+code,diagnostics=details)
                status=info.get('http_status')
                if status is not None:
                    raise SetupBlocked('demo_pending_algos_'+kind+'_read_failed_http_'+str(status),diagnostics=details)
            raise SetupBlocked('demo_pending_algos_rows_or_pagination_invalid',diagnostics=details)
        stage='demo_recent_fills_inventory_invalid'
        await reader._scan('fills')
        stage='demo_full_snapshot_inconsistent_or_expired'
        snapshot=await startup_snapshot(reader)
        if snapshot['order_permission_verified'] is not True:
            raise SetupBlocked('demo_trade_permission_missing')
        flat=(not snapshot['pending_orders'] and not snapshot['pending_algos']
              and all(number(p['pos'],allow_negative=True)==0 for p in snapshot['positions']))
        if binding is None and not flat: raise SetupBlocked('first_binding_requires_flat_clean_account')
        return expected,flat
    except SetupBlocked: raise
    except DemoTransportError as error:
        suffix='_http_'+str(error.http_status) if error.http_status is not None else ''
        raise SetupBlocked(stage+suffix) from None
    except Exception:
        raise SetupBlocked(stage) from None


async def startup_snapshot(reader):
    """Repeat only complete GET snapshots; never reuse partial/expired evidence."""
    for attempt in range(1,4):
        try:
            return await reader.collect()
        except DemoSnapshotError as error:
            retryable=(error.kind in {'timeout','transport_failure'} or
                (error.stage=='snapshot_consistency' and error.kind=='validation_failed'))
            if retryable and attempt<3:
                await asyncio.sleep(0.25)
                continue
            stages={'snapshot_start','account_config','inventory_first_pass',
                'inventory_second_pass','snapshot_consistency'}
            kinds={'timeout','transport_failure','validation_failed','ledger_read_error',
                'multiple_read_failures','unavailable'}
            issues={'snapshot_expired','snapshot_changed'}
            raise SetupBlocked('demo_full_snapshot_inconsistent_or_expired',diagnostics={
                'snapshot_stage':error.stage if error.stage in stages else 'unknown',
                'failure_kind':error.kind if error.kind in kinds else 'unavailable',
                'snapshot_issue':error.issue if error.issue in issues else 'unknown',
                'read_attempts':attempt}) from None


def prepare(root,creds,*,require_binding=False,recovery_only=False):
    persistent_storage(root)
    path=root/'account.json'; binding=None
    try:
        if path.exists(): binding=json.loads(path.read_text())
        elif require_binding or recovery_only or (root/'ledger.sqlite').exists():
            raise SetupBlocked('prior_pinned_binding_required')
    except SetupBlocked: raise
    except Exception: raise SetupBlocked('pinned_binding_invalid') from None
    expected,flat=asyncio.run(account_check(creds,binding,recovery_only=True) if recovery_only else account_check(creds,binding))
    if binding is None:
        try:
            descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w') as target:
                json.dump(expected,target,sort_keys=True); target.flush(); os.fsync(target.fileno())
        except Exception: raise SetupBlocked('binding_not_saved') from None
    return expected,flat


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true')
    modes.add_argument('--run',action='store_true')
    modes.add_argument('--audit-trade',action='store_true',help='GET-only review of the retained test, manual close, fees and cash')
    modes.add_argument('--close-reviewed-test',action='store_true',help='GET-only exchange reads and atomic local archival of the reviewed manual test close')
    modes.add_argument('--close-reviewed-trade',action='store_true',help='GET-only exact tracked strategy-reduction settlement and archival; no new orders')
    parser.add_argument('--initialize-baseline',action='store_true')
    parser.add_argument('--recover-only',action='store_true',help='Recover the existing tracked intent without any new entry')
    parser.add_argument('--daily-limits',action='store_true',help='Opt back into daily loss halt, entry cap and cooldown; disabled for demo trials by default')
    parser.add_argument('--strategy-profile',choices=('original','expanded'),default='expanded',help='20-market demo profile: original breakout or original plus fast breakout and retest')
    parser.add_argument('--test-entry',action='store_true',help='One minimum-size BTC demo test through the existing worker')
    parser.add_argument('--scan-markets',type=int,choices=(1,20),default=1)
    parser.add_argument('--resume-idle',action='store_true',help='Reconcile an untouched or verified archived idle baseline up to 12 hours old; preserve daily risk')
    parser.add_argument('--seconds',type=int,default=300,choices=range(1,901),metavar='1..900')
    args=parser.parse_args(argv)
    result=dict(mode='demo_cloudshell_acceptance',phase='plan',secret_values_printed=False,
                cloud_resources_changed=False,orders_enabled=False,live_trading_enabled=False)
    if not args.check and not args.run and not args.audit_trade and not args.close_reviewed_test and not args.close_reviewed_trade:
        print(json.dumps(result,sort_keys=True)); return 0
    try:
        if sys.version_info<(3,11) or not hasattr(asyncio,'TaskGroup') or not hasattr(asyncio,'timeout'):
            raise SetupBlocked('python_3_11_or_newer_required')
        root=Path.home()/'sinka-demo-trial'
        persistent_storage(root)  # Reject ephemeral storage before reading any Secret.
        creds=credentials()
        binding,flat=prepare(root,creds,require_binding=args.run or args.audit_trade or args.close_reviewed_test or args.close_reviewed_trade,recovery_only=args.recover_only and args.run)
        if args.close_reviewed_test or args.close_reviewed_trade:
            from okx_demo_manual_close import close_test,ManualCloseBlocked
            try:
                closed=asyncio.run(close_test(root/'ledger.sqlite',binding,creds,**({'allow_strategy':True} if args.close_reviewed_trade else {})))
            except ManualCloseBlocked as error:
                raise SetupBlocked('manual_test_close_'+error.stage+'_blocked_prior_ledger_retained') from None
            except Exception:
                raise SetupBlocked('manual_test_close_unverified_prior_ledger_retained') from None
            result.update(phase=closed['phase'],closure=closed)
            code=0
        elif args.audit_trade:
            from okx_demo_manual_review import audit_trade,TradeReviewBlocked
            try:
                review=asyncio.run(audit_trade(root/'ledger.sqlite',binding,creds))
            except TradeReviewBlocked as error:
                raise SetupBlocked('manual_trade_'+error.stage+'_'+error.kind+'_ledger_preserved',diagnostics=error.diagnostics) from None
            except Exception:
                raise SetupBlocked('manual_trade_evidence_unavailable_ledger_preserved') from None
            result.update(phase=review['phase'],review=review)
            code=0 if review['evidence_matches'] else 2
        elif args.check:
            result.update(phase='demo_account_verified',account_binding_pinned=True,
                          trade_permission_verified=True,flat_clean=flat,persistent_storage_verified=True)
            code=0
        else:
            version=command(['node','--version']).strip()
            if not re.fullmatch(r'v(?:22|24)\.[0-9]+\.[0-9]+',version):
                raise SetupBlocked('supported_node_runtime_required')
            env=dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1',DEMO_ARM_ENTRIES='0' if args.recover_only else '1',DEMO_PROGRESS='1',
                DEMO_INITIALIZE_BASELINE='1' if args.initialize_baseline else '0',
                DEMO_RESUME_IDLE='1' if args.resume_idle else '0',DEMO_SCAN_MARKETS=str(args.scan_markets),
                DEMO_DAILY_LIMITS='1' if args.daily_limits else '0',
                DEMO_STRATEGY_PROFILE=args.strategy_profile,
                DEMO_EXECUTION_TEST='1' if args.test_entry else '0',DEMO_RECOVER_ONLY='1' if args.recover_only else '0',
                DEMO_MAX_SECONDS=str(min(args.seconds,90) if args.test_entry else args.seconds),DEMO_LEDGER_PATH=str(root/'ledger.sqlite'),
                DEMO_ACCOUNT_SCOPE=binding['account_scope'],OKX_EXPECTED_UID=binding['uid'],
                DEMO_SYMBOL=SYMBOL,OKX_API_KEY=creds.key,OKX_API_SECRET=creds.secret,OKX_API_PASSPHRASE=creds.passphrase)
            result['orders_enabled']=True
            worker,code=execute(env)
            result.update(phase='trial_finished',worker=worker)
    except SetupBlocked as error:
        result.update(phase='blocked',reason=error.args[0]); code=2
        if error.diagnostics is not None:
            result['read_diagnostics' if args.audit_trade or 'snapshot_stage' in error.diagnostics else 'algo_diagnostics']=error.diagnostics
    except KeyboardInterrupt:
        result.update(phase='interrupted_reconciliation_required'); code=130
    except Exception:
        result.update(phase='blocked',reason='setup_or_runtime_unavailable'); code=2
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return code


if __name__=='__main__':
    raise SystemExit(main())
