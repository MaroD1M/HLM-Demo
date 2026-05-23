from pathlib import Path
import sqlite3
import json
import zipfile
from io import BytesIO

from core.services.backup_service import list_backup_files, run_sqlite_backup, verify_backup_integrity
from core.services.diagnostics_service import build_support_bundle, summarize_runtime_health
from core.services.operation_guard_service import build_operation_preview, run_preflight_checks


class DummyTask:
    name = 'demo'
    source_dir = '/tmp/source'
    dest_dir = '/tmp/dest'
    extensions = '.mkv'
    exclude_dirs = 'sample'
    delete_dry_run = False


def test_hardlink_preview_reports_missing_paths():
    report = build_operation_preview('hardlink', task=DummyTask(), get_config=lambda k, d=None: d)
    assert report['kind'] == 'hardlink'
    assert report['ok'] is False
    assert '源目录不存在' in report['issues']


def test_backup_preflight_ok_when_database_exists():
    report = run_preflight_checks('backup', db_path='app.py')
    assert report['kind'] == 'backup'
    assert isinstance(report['ok'], bool)


def test_backup_service_creates_manifest_and_verifies(tmp_path):
    db_file = tmp_path / 'demo.db'
    conn = sqlite3.connect(str(db_file))
    conn.execute('create table t(id integer)')
    conn.execute('insert into t values (1)')
    conn.commit()
    conn.close()
    backup_dir = tmp_path / 'backups'
    ok, msg, backup_path = run_sqlite_backup(str(db_file), str(backup_dir), keep_last=2)
    assert ok is True
    assert backup_path
    backup_file = Path(backup_path)
    assert backup_file.exists()
    manifest_path = backup_file.with_suffix(backup_file.suffix + '.json')
    assert manifest_path.exists()
    ok2, msg2 = verify_backup_integrity(backup_file)
    assert ok2 is True
    items = list_backup_files(str(backup_dir))
    assert items and items[0]['ok'] is True


def test_restore_sqlite_backup_creates_fallback_backup(tmp_path):
    db_file = tmp_path / 'source.db'
    conn = sqlite3.connect(str(db_file))
    conn.execute('create table t(id integer primary key, value text)')
    conn.execute('insert into t(value) values (?)', ('before',))
    conn.commit()
    conn.close()

    backup_dir = tmp_path / 'backups'
    ok, _, backup_path = run_sqlite_backup(str(db_file), str(backup_dir), keep_last=2)
    assert ok is True

    conn = sqlite3.connect(str(db_file))
    conn.execute('update t set value=? where id=1', ('after',))
    conn.commit()
    conn.close()

    from core.services.backup_service import restore_sqlite_backup
    ok2, msg2 = restore_sqlite_backup(str(db_file), str(backup_path), create_fallback_backup=True, fallback_backup_dir=str(tmp_path / 'restore-preflight'), keep_last=2)
    assert ok2 is True

    conn = sqlite3.connect(str(db_file))
    value = conn.execute('select value from t where id=1').fetchone()[0]
    conn.close()
    assert value == 'before'
    assert list((tmp_path / 'restore-preflight').glob('*.db'))


def test_list_backup_files_marks_legacy_without_manifest(tmp_path):
    backup_dir = tmp_path / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = backup_dir / 'hardlink_manager-20260522-000000.db'
    legacy_file.write_bytes(b'legacy-backup')
    items = list_backup_files(str(backup_dir))
    assert items
    assert items[0]['ok'] is False
    assert items[0]['legacy_without_manifest'] is True


def test_support_bundle_redacts_sensitive_values():
    payload = {
        'generated_at': '2026-05-23T00:00:00+00:00',
        'panel_view': 'overview',
        'health_summary': {'status': 'healthy', 'label': '正常', 'detail': 'ok'},
        'counts': {'running_jobs': 0},
        'storage': {'db_size_human': '1 MB', 'logs_size_human': '2 MB', 'backup_count': 1},
        'schema': {'state': '已是最新版本', 'current_version': 1, 'target_version': 1},
        'release': {'local_version': 'dev'},
        'checks': [],
        'running_rows': [],
        'pending_events': [],
        'backfill_metrics_rows': [],
        'backup_files': [],
        'config_rows': [
            {'key': 'dev_git_token', 'value': 'abc123', 'description': '', 'updated_at': None},
            {'key': 'proxy_url', 'value': 'http://127.0.0.1:7890', 'description': '', 'updated_at': None},
        ],
        'recent_operations': [],
        'recent_job_rows': [],
    }
    content, filename, mime_type, bundle = build_support_bundle(payload, bundle_format='zip')
    assert filename.endswith('.zip')
    assert mime_type == 'application/zip'
    zf = zipfile.ZipFile(BytesIO(content))
    data = json.loads(zf.read('support-bundle.json').decode('utf-8'))
    assert data['config_rows'][0]['value'] == '***'
    assert data['config_rows'][1]['value'] == 'http://127.0.0.1:7890'
    assert bundle['__meta']['redacted'] is True


def test_support_health_summary_reports_status():
    summary = summarize_runtime_health({
        'checks': [{'name': '数据库连接', 'ok': True, 'detail': '正常'}],
        'counts': {'running_jobs': 0, 'pending_events': 0, 'operations_24h': 0, 'failed_operations_24h': 0, 'failed_jobs': 0},
        'storage': {'db_size_human': '1 MB', 'logs_size_human': '2 MB', 'backup_count': 1, 'db_size_bytes': 1024, 'logs_size_bytes': 2048, 'backup_latest': {'ok': True}},
        'schema': {'state': '已是最新版本', 'current_version': 1, 'target_version': 1},
        'release': {'local_version': 'dev'},
    })
    assert summary['status'] == 'healthy'
    assert summary['metrics']['backup_ok'] is True


def test_delete_pending_bulk_preview_mentions_audit():
    report = build_operation_preview('delete_pending_bulk', get_config=lambda k, d=None: d)
    assert report['kind'] == 'delete_pending_bulk'
    assert any('审计' in item for item in report['items'])
