"""Offline handoff preserves original SQL state and fails closed on tampering."""
import fcntl
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
import okx_demo_migration as migration


@pytest.fixture
def source(tmp_path, monkeypatch):
    root=tmp_path/'old';root.mkdir()
    (root/'account.json').write_text(json.dumps({'uid':'123','account_scope':'synthetic'}))
    db=sqlite3.connect(root/'ledger.sqlite')
    db.executescript('''
        CREATE TABLE demo_intent(id TEXT);
        CREATE TABLE demo_authority(kill INTEGER,expires INTEGER);
        INSERT INTO demo_authority VALUES(1,0);
        CREATE TABLE demo_fill_audit_meta(singleton INTEGER,scope TEXT,baseline_at INTEGER);
        INSERT INTO demo_fill_audit_meta VALUES(1,'synthetic',1800000000000);
        CREATE TABLE demo_cash_baseline(cash TEXT);
        INSERT INTO demo_cash_baseline VALUES('9990.123456789012345');
        CREATE TABLE historical_archives(sequence INTEGER,body TEXT);
        INSERT INTO historical_archives VALUES(12,'original immutable evidence');
    ''');db.commit();db.close()
    monkeypatch.setattr(migration,'active_processes',lambda:False)
    return root


def sql_rows(path):
    db=sqlite3.connect(path)
    try:return list(db.iterdump())
    finally:db.close()


def test_export_restore_preserves_every_row_and_fences_source(source,tmp_path):
    original=sql_rows(source/'ledger.sqlite')
    bundle=tmp_path/'bundle';home=tmp_path/'new';home.mkdir()
    result=migration.export(source,bundle)
    assert result['source_disabled'] and result['orders_sent']==0
    assert (source/'ledger.sqlite').stat().st_mode&0o777 == 0o400
    assert sql_rows(source/'ledger.sqlite')==original
    with pytest.raises(ValueError,match='migrated'):migration.check_source_fence(source)
    restored=migration.restore(bundle,home)
    final=home/'sinka-demo-trial'
    assert restored['ledger_preserved'] and not restored['live_trading_enabled']
    assert sql_rows(final/'ledger.sqlite')==original
    assert (final/'account.json').read_bytes()==(source/'account.json').read_bytes()
    assert not (final/migration.FENCE).exists()
    with pytest.raises(ValueError,match='already has state'):migration.restore(bundle,home)
    assert sql_rows(final/'ledger.sqlite')==original


def test_active_or_locked_source_is_never_exported_or_disabled(source,tmp_path,monkeypatch):
    monkeypatch.setattr(migration,'active_processes',lambda:True)
    with pytest.raises(ValueError,match='Stop all'):migration.export(source,tmp_path/'bundle')
    assert not (source/migration.FENCE).exists()
    monkeypatch.setattr(migration,'active_processes',lambda:False)
    with (source/'control-runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):migration.export(source,tmp_path/'bundle')
    assert not (source/migration.FENCE).exists()


@pytest.mark.parametrize('change',['cash','binding','symlink','manifest','existing'])
def test_tamper_or_existing_target_cannot_overwrite_state(source,tmp_path,change):
    bundle=tmp_path/'bundle';home=tmp_path/'new';home.mkdir()
    migration.export(source,bundle)
    if change=='cash':
        db=sqlite3.connect(bundle/'ledger.sqlite')
        db.execute("UPDATE demo_cash_baseline SET cash='1'");db.commit();db.close()
    elif change=='binding':(bundle/'account.json').write_text('{"uid":"999","account_scope":"foreign"}')
    elif change=='symlink':
        (bundle/'account.json').unlink();(bundle/'account.json').symlink_to(source/'account.json')
    elif change=='manifest':(bundle/'manifest.json').write_text('{"version":1,"source_disabled":false}')
    elif change=='existing':(home/'sinka-demo-trial').mkdir()
    with pytest.raises(ValueError):migration.restore(bundle,home)
    assert not (home/'sinka-demo-trial'/'ledger.sqlite').exists()


def test_backup_includes_committed_wal_and_source_identity_cannot_change(source,tmp_path):
    db=sqlite3.connect(source/'ledger.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute("INSERT INTO historical_archives VALUES(13,'committed WAL evidence')");db.commit()
    try:
        migration.export(source,tmp_path/'bundle')
        check=sqlite3.connect(tmp_path/'bundle'/'ledger.sqlite')
        assert check.execute('SELECT COUNT(*) FROM historical_archives').fetchone()==(2,)
        check.close()
    finally:db.close()


def test_migrated_host_blocks_cli_before_secrets(source,monkeypatch,capsys):
    import okx_demo_cloudshell as setup
    (source/migration.FENCE).write_text('{}')
    monkeypatch.setattr(Path,'home',lambda:source.parent)
    source.rename(source.parent/'sinka-demo-trial')
    monkeypatch.setattr(setup,'credentials',lambda:pytest.fail('Secret access on retired host'))
    assert setup.main(['--run'])==2
    result=json.loads(capsys.readouterr().out)
    assert result['reason']=='source_migrated_use_cloud_runner' and not result['orders_enabled']


def test_migrated_controller_cannot_acquire_mailbox(source,monkeypatch):
    import okx_demo_control_runner as runner
    (source/migration.FENCE).write_text('{}')
    monkeypatch.setattr(Path,'home',lambda:source.parent)
    source.rename(source.parent/'sinka-demo-trial')
    with patch.object(runner.FirestoreControlStore,'acquire',side_effect=AssertionError('mailbox acquired')):
        with pytest.raises(ValueError,match='migrated'):runner.run(object())
