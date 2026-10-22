"""Offline demo ledger handoff. Never reads exchange credentials or sends orders.

Export permanently fences the source before snapshotting; failures leave it
fenced. Re-enabling the old host is deliberately not an automatic rollback.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

FENCE = 'migrated-to-vm.json'
FILES = ('ledger.sqlite', 'account.json')


def check_source_fence(root):
    if (root / FENCE).exists() or (root / FENCE).is_symlink():
        raise ValueError('Source migrated; this host must not run demo commands')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root):
    for name in FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 100_000_000:
            raise ValueError('Original ledger and binding required')
    binding = json.loads((root / 'account.json').read_text())
    if not isinstance(binding, dict) or not all(isinstance(binding.get(k), str) and binding[k]
                                             for k in ('uid', 'account_scope')):
        raise ValueError('Pinned account binding required')
    db = sqlite3.connect((root / 'ledger.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Ledger integrity failed')
        required = {'demo_intent', 'demo_authority', 'demo_fill_audit_meta', 'demo_cash_baseline'}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise ValueError('Existing trading ledger required; no initialization permitted')
        scope = db.execute('SELECT scope FROM demo_fill_audit_meta WHERE singleton=1').fetchone()
        if scope != (binding['account_scope'],):
            raise ValueError('Binding does not match original ledger')
    finally:
        db.close()


def active_processes(proc=Path('/proc')):
    """Old releases do not know the new fence, so also require no live workers."""
    for directory in proc.iterdir():
        if not directory.name.isdigit() or int(directory.name) == os.getpid():
            continue
        try:
            args = (directory / 'cmdline').read_bytes().split(b'\0')
            names = {Path(os.fsdecode(arg)).name for arg in args if arg}
            if names & {'okx_demo_control_runner.py', 'okx_demo_cloudshell.py', 'okx_demo_trading_worker.py'}:
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return False


def export(root, destination):
    if root.is_symlink() or destination.exists() or destination.is_symlink():
        raise ValueError('New export directory required; original storage unchanged')
    validate(root)
    with (root / 'control-runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if active_processes():
            raise ValueError('Stop all demo workers before migration; no processes killed')
        # A retained marker plus read-only original files also blocks older
        # checkout versions which do not yet understand check_source_fence.
        marker = root / FENCE
        if marker.is_symlink():
            raise ValueError('Invalid source fence')
        with marker.open('w') as handle:
            json.dump({'target': 'sinka-demo-runner', 'source_disabled': True}, handle)
            handle.flush(); os.fsync(handle.fileno())
        marker.chmod(0o400)
        for name in (*FILES, 'ledger.sqlite-wal', 'ledger.sqlite-shm'):
            path = root / name
            if path.is_symlink():
                raise ValueError('Symlinked ledger sidecar rejected')
            if path.exists(): path.chmod(0o400)
        destination.mkdir(mode=0o700, parents=False)
        source = sqlite3.connect((root / 'ledger.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(destination / 'ledger.sqlite')
        try:
            source.backup(target)
        finally:
            target.close(); source.close()
        shutil.copyfile(root / 'account.json', destination / 'account.json')
        validate(destination)
        manifest = {'version': 1, 'source_disabled': True,
                    'sha256': {name: digest(destination / name) for name in FILES}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
        for path in destination.iterdir(): path.chmod(0o600)
        # Account IDs, binding contents and historical orders never enter stdout.
        return {'phase': 'migration_export_verified', 'source_disabled': True,
                'orders_sent': 0, 'live_trading_enabled': False}


def restore(bundle, home):
    if bundle.is_symlink() or home.is_symlink(): raise ValueError('Symlinked migration path')
    manifest_path = bundle / 'manifest.json'
    if manifest_path.is_symlink(): raise ValueError('Invalid migration manifest')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('version') != 1 or manifest.get('source_disabled') is not True
            or set(manifest.get('sha256', {})) != set(FILES)):
        raise ValueError('Verified fenced-source migration required')
    validate(bundle)
    if any(digest(bundle / name) != manifest['sha256'][name] for name in FILES):
        raise ValueError('Migration checksum mismatch; no target installed')
    final = home / 'sinka-demo-trial'
    temporary = home / 'sinka-demo-import'
    if final.exists() or final.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise ValueError('Target already has state; no overwrite or reset permitted')
    temporary.mkdir(mode=0o700)
    for name in FILES:
        shutil.copyfile(bundle / name, temporary / name)
        (temporary / name).chmod(0o600)
    shutil.copyfile(manifest_path, temporary / 'migration-receipt.json')
    validate(temporary)
    os.rename(temporary, final)
    return {'phase': 'migration_import_verified', 'ledger_preserved': True,
            'orders_sent': 0, 'live_trading_enabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--export', type=Path)
    group.add_argument('--restore', type=Path)
    args = parser.parse_args()
    try:
        result = (export(Path.home() / 'sinka-demo-trial', args.export) if args.export
                  else restore(args.restore, Path.home()))
        print(json.dumps(result)); return 0
    except Exception:
        print(json.dumps({'phase': 'migration_blocked', 'orders_sent': 0,
                          'live_trading_enabled': False,
                          'source_may_be_fenced': True})); return 2


if __name__ == '__main__': raise SystemExit(main())
