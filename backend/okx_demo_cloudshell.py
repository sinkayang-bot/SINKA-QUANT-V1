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
import time

from okx_demo_snapshot import DemoSnapshotReader,digits
from okx_demo_transport import DemoCredentials,OKXDemoReadTransport
from okx_demo_trading_worker import execute
from okx_reconcile import number

PROJECT='sinka-quant'
SECRET_NAMES=('OKX_DEMO_TRADE_API_KEY','OKX_DEMO_TRADE_API_SECRET','OKX_DEMO_TRADE_API_PASSPHRASE')
SYMBOL='BTC-USDT-SWAP'


class SetupBlocked(RuntimeError):
    pass


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


async def account_check(creds,binding=None):
    clock=lambda:int(time.time()*1000)
    transport=OKXDemoReadTransport(creds,enable_demo_requests=True,clock=clock)
    try:
        config=await transport.account_read('config')
        if config.get('code')!='0' or len(config.get('data',[]))!=1: raise ValueError()
        row=config['data'][0]
        uid=digits(row.get('uid'))
        scope='okx-demo:'+hashlib.sha256(uid.encode()).hexdigest()[:24]
        expected={'version':1,'uid':uid,'account_scope':scope,'symbol':SYMBOL}
        if binding is not None and binding!=expected:
            raise SetupBlocked('pinned_demo_account_mismatch')
        reader=DemoSnapshotReader(transport,account_scope=scope,expected_uid=uid,symbol=SYMBOL,clock=clock)
        snapshot=await reader.collect()
        if snapshot['order_permission_verified'] is not True:
            raise SetupBlocked('demo_trade_permission_missing')
        flat=(not snapshot['pending_orders'] and not snapshot['pending_algos']
              and all(number(p['pos'],allow_negative=True)==0 for p in snapshot['positions']))
        if binding is None and not flat: raise SetupBlocked('first_binding_requires_flat_clean_account')
        return expected,flat
    except SetupBlocked: raise
    except Exception:
        raise SetupBlocked('demo_account_mode_leverage_or_connection_check_failed') from None


def prepare(root,creds,*,require_binding=False):
    persistent_storage(root)
    path=root/'account.json'; binding=None
    try:
        if path.exists(): binding=json.loads(path.read_text())
        elif require_binding or (root/'ledger.sqlite').exists():
            raise SetupBlocked('prior_pinned_binding_required')
    except SetupBlocked: raise
    except Exception: raise SetupBlocked('pinned_binding_invalid') from None
    expected,flat=asyncio.run(account_check(creds,binding))
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
    parser.add_argument('--initialize-baseline',action='store_true')
    parser.add_argument('--seconds',type=int,default=300,choices=range(1,901),metavar='1..900')
    args=parser.parse_args(argv)
    result=dict(mode='demo_cloudshell_acceptance',phase='plan',secret_values_printed=False,
                cloud_resources_changed=False,orders_enabled=False,live_trading_enabled=False)
    if not args.check and not args.run:
        print(json.dumps(result,sort_keys=True)); return 0
    try:
        root=Path.home()/'sinka-demo-trial'
        persistent_storage(root)  # Reject ephemeral storage before reading any Secret.
        creds=credentials()
        binding,flat=prepare(root,creds,require_binding=args.run)
        if args.check:
            result.update(phase='demo_account_verified',account_binding_pinned=True,
                          trade_permission_verified=True,flat_clean=flat,persistent_storage_verified=True)
            code=0
        else:
            version=command(['node','--version']).strip()
            if not re.fullmatch(r'v(?:22|24)\.[0-9]+\.[0-9]+',version):
                raise SetupBlocked('supported_node_runtime_required')
            env=dict(RUN_DEMO_TRADING='1',DEMO_TRADING_AUTHORIZED='1',DEMO_ARM_ENTRIES='1',
                DEMO_INITIALIZE_BASELINE='1' if args.initialize_baseline else '0',
                DEMO_MAX_SECONDS=str(args.seconds),DEMO_LEDGER_PATH=str(root/'ledger.sqlite'),
                DEMO_ACCOUNT_SCOPE=binding['account_scope'],OKX_EXPECTED_UID=binding['uid'],
                DEMO_SYMBOL=SYMBOL,OKX_API_KEY=creds.key,OKX_API_SECRET=creds.secret,OKX_API_PASSPHRASE=creds.passphrase)
            result['orders_enabled']=True
            worker,code=execute(env)
            result.update(phase='trial_finished',worker=worker)
    except SetupBlocked as error:
        result.update(phase='blocked',reason=error.args[0]); code=2
    except KeyboardInterrupt:
        result.update(phase='interrupted_reconciliation_required'); code=130
    except Exception:
        result.update(phase='blocked',reason='setup_or_runtime_unavailable'); code=2
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return code


if __name__=='__main__':
    raise SystemExit(main())
