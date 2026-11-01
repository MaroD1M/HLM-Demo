import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import app as app_module
from core.models import Downloader
from core.services.delete_service import scan_delete_rows
from core.services.security_service import is_read_only_forbidden_endpoint


def _deps(logs):
    def log_operation(op, *_args, **_kwargs):
        logs.append(op)

    return (
        lambda _path, _downloader: (None, 'no_match'),
        lambda _downloader, _hash: True,
        log_operation,
        lambda key, default=None: 'true' if key == 'manual_source_delete_delete_dest' else default,
        lambda _n, _m: None,
        lambda *_a, **_k: None,
    )


def _task(tmp_path, dry_run):
    return SimpleNamespace(
        id=1,
        name='dry-run-test',
        directory=str(tmp_path),
        prune_roots=[str(tmp_path)],
        cooldown_seconds=120,
        max_deletes_per_run=5,
        dry_run=dry_run,
        notifier=None,
        notify_on_delete=False,
        notify_on_risky_delete=False,
        downloader=None,
    )


def _row(src, dst):
    return SimpleNamespace(
        id=1,
        source_path=str(src),
        dest_path=str(dst),
        last_seen_at=datetime.now(UTC) - timedelta(seconds=600),
        deleted_at=None,
        source_type='manual',
        torrent_hash='',
        downloader_id=None,
    )


def test_dry_run_does_not_delete_counterpart_or_consume_record(tmp_path):
    src = tmp_path / 'a.mkv'
    dst = tmp_path / 'b.mkv'
    src.write_text('x', encoding='utf-8')
    dst.write_text('x', encoding='utf-8')
    src.unlink()
    row = _row(src, dst)
    logs = []
    try_match, delete_torrent, log_operation, get_config, send_notification, create_pending = _deps(logs)

    ok, deleted, hits, pending = scan_delete_rows(
        _task(tmp_path, dry_run=True),
        [row],
        try_match,
        delete_torrent,
        log_operation,
        get_config,
        send_notification,
        create_pending,
    )

    assert ok is True
    assert deleted == 0
    assert hits == 1
    assert pending == 0
    assert dst.exists()
    assert row.deleted_at is None
    assert 'delete_dry_run' in logs


def test_non_dry_run_deletes_counterpart_and_consumes_record(tmp_path):
    src = tmp_path / 'a.mkv'
    dst = tmp_path / 'b.mkv'
    src.write_text('x', encoding='utf-8')
    dst.write_text('x', encoding='utf-8')
    src.unlink()
    row = _row(src, dst)
    logs = []
    try_match, delete_torrent, log_operation, get_config, send_notification, create_pending = _deps(logs)

    ok, deleted, hits, pending = scan_delete_rows(
        _task(tmp_path, dry_run=False),
        [row],
        try_match,
        delete_torrent,
        log_operation,
        get_config,
        send_notification,
        create_pending,
    )

    assert ok is True
    assert deleted == 0
    assert hits == 1
    assert pending == 0
    assert not dst.exists()
    assert row.deleted_at is not None


def test_missing_source_root_aborts_delete_before_touching_files(tmp_path):
    src_dir = tmp_path / 'missing-source'
    dst_dir = tmp_path / 'library'
    dst_dir.mkdir()
    dst = dst_dir / 'a.mkv'
    dst.write_text('keep', encoding='utf-8')

    with app_module.app.app_context():
        task = app_module.HardlinkTask(
            name='root-guard-test',
            source_dir=str(src_dir),
            dest_dir=str(dst_dir),
            extensions='.mkv',
            monitor_source_delete=True,
            monitor_dest_delete=True,
        )
        app_module.db.session.add(task)
        app_module.db.session.flush()
        mapping = app_module.FileLinkMap(
            task_id=task.id,
            source_path=str(src_dir / 'a.mkv'),
            dest_path=str(dst),
            source_type='manual',
        )
        app_module.db.session.add(mapping)
        app_module.db.session.commit()
        task_id = task.id

    try:
        with app_module.app.app_context():
            ok, message = app_module.scan_delete_for_hardlink_task(task_id)
        assert ok is False
        assert '源目录不存在' in message
        assert dst.exists()
    finally:
        with app_module.app.app_context():
            app_module.FileLinkMap.query.filter_by(task_id=task_id).delete()
            task = app_module.db.session.get(app_module.HardlinkTask, task_id)
            if task:
                app_module.db.session.delete(task)
            app_module.db.session.commit()


def test_downloader_password_uses_authenticated_encryption():
    downloader = Downloader(name='qb', host='http://127.0.0.1', port=8080)
    downloader.set_password('secret-password')
    assert downloader.encrypted_password.startswith('fernet:')
    assert 'secret-password' not in downloader.encrypted_password
    assert downloader.get_password() == 'secret-password'


def test_notifier_api_key_is_not_stored_plaintext():
    with app_module.app.app_context():
        notifier = app_module.Notifier(name='tg', chat_id='1')
        notifier.set_api_key('123456:telegram-token')
        assert notifier.api_key.startswith('fernet:')
        assert 'telegram-token' not in notifier.api_key
        assert notifier.get_api_key() == '123456:telegram-token'


def test_legacy_xor_password_still_decrypts():
    key = hashlib.sha256(b'default-secret-key-for-dev-only').digest()
    raw = b'legacy-password'
    xored = bytes([b ^ key[i % len(key)] for i, b in enumerate(raw)])
    downloader = Downloader(name='qb', host='http://127.0.0.1', port=8080)
    downloader.encrypted_password = 'enc:' + base64.urlsafe_b64encode(xored).decode('utf-8')
    assert downloader.get_password() == 'legacy-password'


def test_safe_next_path_rejects_external_and_protocol_relative_urls():
    assert app_module._safe_next_path('/hardlink') == '/hardlink'
    assert app_module._safe_next_path('//evil.example') == '/'
    assert app_module._safe_next_path('https://evil.example') == '/'
    assert app_module._safe_next_path('/\\evil.example') == '/'


def test_read_only_mode_blocks_restore_retry_and_log_clear():
    assert is_read_only_forbidden_endpoint('web_bp.execution_retry') is True
    assert is_read_only_forbidden_endpoint('web_bp.diagnostics_backup_restore') is True
    assert is_read_only_forbidden_endpoint('web_bp.logs_clear') is True
    assert is_read_only_forbidden_endpoint('web_bp.dashboard') is False


def test_task_path_update_invalidates_old_mapping_and_cache(tmp_path):
    src_dir = tmp_path / 'src'
    old_dst_dir = tmp_path / 'old-dst'
    new_dst_dir = tmp_path / 'new-dst'
    src_dir.mkdir()
    old_dst_dir.mkdir()
    new_dst_dir.mkdir()
    src_file = src_dir / 'a.mkv'
    src_file.write_text('x', encoding='utf-8')
    old_dst = old_dst_dir / 'a.mkv'
    old_dst.write_text('x', encoding='utf-8')

    with app_module.app.app_context():
        task = app_module.HardlinkTask(
            name='update-invalidate-test',
            source_dir=str(src_dir),
            dest_dir=str(old_dst_dir),
            extensions='.mkv',
            monitor_source_delete=True,
            monitor_dest_delete=True,
        )
        app_module.db.session.add(task)
        app_module.db.session.flush()
        app_module.db.session.add(app_module.FileLinkMap(
            task_id=task.id,
            source_path=str(src_file),
            dest_path=str(old_dst),
            source_type='manual',
        ))
        app_module.db.session.add(app_module.HardlinkCache(
            source_path=str(src_file),
            dest_path=str(old_dst),
        ))
        app_module.db.session.commit()
        task_id = task.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['_csrf_token'] = 'test-csrf-token'

    try:
        response = client.post(
            f'/hardlink/update/{task_id}',
            data={
                'csrf_token': 'test-csrf-token',
                'name': 'update-invalidate-test',
                'source_dir': str(src_dir),
                'dest_dir': str(new_dst_dir),
                'extensions': '.mkv',
                'exclude_dirs': '',
                'min_file_age_seconds': '0',
                'delete_cooldown_seconds': '120',
                'delete_max_deletes_per_run': '20',
                'monitor_source_delete': 'on',
                'monitor_dest_delete': 'on',
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert response.status_code == 200

        with app_module.app.app_context():
            task = app_module.db.session.get(app_module.HardlinkTask, task_id)
            assert task.dest_dir == str(new_dst_dir)
            assert app_module.FileLinkMap.query.filter_by(task_id=task_id).count() == 0
            assert app_module.HardlinkCache.query.filter_by(source_path=str(src_file)).count() == 0
    finally:
        with app_module.app.app_context():
            app_module.FileLinkMap.query.filter_by(task_id=task_id).delete()
            app_module.HardlinkCache.query.filter_by(source_path=str(src_file)).delete()
            task = app_module.db.session.get(app_module.HardlinkTask, task_id)
            if task:
                app_module.db.session.delete(task)
            app_module.db.session.commit()


def test_saving_update_scope_does_not_clear_passphrase():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        app_module.set_config('critical_action_passphrase', 'keep-me')
    try:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = 'test-csrf-token'
        response = client.post('/settings/save', data={
            'csrf_token': 'test-csrf-token',
            'save_scope': 'update',
            'critical_action_passphrase': '',
        })
        assert response.status_code in (200, 302)
        with app_module.app.app_context():
            assert app_module.get_config('critical_action_passphrase') == 'keep-me'

        with client.session_transaction() as sess:
            sess['_csrf_token'] = 'test-csrf-token'
        response = client.post('/settings/save', data={
            'csrf_token': 'test-csrf-token',
            'save_scope': 'update',
            'critical_action_passphrase_clear': 'true',
        })
        assert response.status_code in (200, 302)
        with app_module.app.app_context():
            assert app_module.get_config('critical_action_passphrase') == ''
    finally:
        with app_module.app.app_context():
            app_module.set_config('critical_action_passphrase', '')


def test_replaced_source_file_relinks_and_clears_stale_torrent_mapping(tmp_path):
    src_dir = tmp_path / 'src'
    dst_dir = tmp_path / 'dst'
    src_dir.mkdir()
    dst_dir.mkdir()
    src = src_dir / 'a.mkv'
    src.write_text('old-content', encoding='utf-8')

    with app_module.app.app_context():
        task = app_module.HardlinkTask(
            name='relink-test',
            source_dir=str(src_dir),
            dest_dir=str(dst_dir),
            extensions='.mkv',
            create_folder=True,
            use_cache=True,
            min_file_age_seconds=0,
        )
        app_module.db.session.add(task)
        app_module.db.session.commit()
        task_id = task.id

        ok, _ = app_module.svc_create_hardlink_for_file(
            task,
            src,
            app_module.HardlinkCache,
            app_module.FileLinkMap,
            app_module.db,
            app_module.safe_unlink,
        )
        assert ok is True
        app_module.db.session.commit()
        mapping = app_module.FileLinkMap.query.filter_by(source_path=str(src)).first()
        old_inode = mapping.source_inode
        old_dest = Path(mapping.dest_path)
        mapping.torrent_hash = 'old-hash'
        mapping.source_type = 'downloader'
        app_module.db.session.commit()

        src.unlink()
        src.write_text('new-content-with-different-size', encoding='utf-8')
        ok2, _ = app_module.svc_create_hardlink_for_file(
            task,
            src,
            app_module.HardlinkCache,
            app_module.FileLinkMap,
            app_module.db,
            app_module.safe_unlink,
        )
        app_module.db.session.commit()
        mapping = app_module.FileLinkMap.query.filter_by(source_path=str(src)).first()
        assert ok2 is True
        assert old_dest.exists()
        assert old_dest.samefile(src)
        assert mapping.source_inode != old_inode
        assert mapping.torrent_hash is None
        assert mapping.source_type == 'pending'

    with app_module.app.app_context():
        app_module.DeletePendingAction.query.filter_by(file_map_id=mapping.id).delete()
        app_module.FileLinkMap.query.filter_by(task_id=task_id).delete()
        app_module.HardlinkCache.query.filter_by(source_path=str(src)).delete()
        task = app_module.db.session.get(app_module.HardlinkTask, task_id)
        if task:
            app_module.db.session.delete(task)
        app_module.db.session.commit()


def test_sensitive_config_is_encrypted_at_rest_and_api_get_requires_token():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        app_module.set_config('api_access_token', 'api-secret-token')
        row = app_module.AppConfig.query.filter_by(key='api_access_token').first()
        assert row.value.startswith('fernet:')
        assert 'api-secret-token' not in row.value
        assert app_module.get_config('api_access_token') == 'api-secret-token'
    try:
        assert client.get('/api/tasks/status').status_code == 401
        ok = client.get('/api/tasks/status', headers={'X-API-Token': 'api-secret-token'})
        assert ok.status_code == 200
    finally:
        with app_module.app.app_context():
            app_module.set_config('api_access_token', '')


def test_startup_repair_removes_mappings_outside_task_roots(tmp_path):
    task_src = tmp_path / 'task-src'
    task_dst = tmp_path / 'task-dst'
    other = tmp_path / 'other'
    task_src.mkdir()
    task_dst.mkdir()
    other.mkdir()
    outside = other / 'a.mkv'
    inside = task_dst / 'a.mkv'
    outside.write_text('x', encoding='utf-8')
    inside.write_text('x', encoding='utf-8')

    with app_module.app.app_context():
        app_module._delete_schema_meta_key('mapping_root_repair_v1')
        task = app_module.HardlinkTask(
            name='repair-test',
            source_dir=str(task_src),
            dest_dir=str(task_dst),
            extensions='.mkv',
        )
        app_module.db.session.add(task)
        app_module.db.session.flush()
        app_module.db.session.add(app_module.FileLinkMap(
            task_id=task.id,
            source_path=str(outside),
            dest_path=str(inside),
            source_type='manual',
        ))
        app_module.db.session.add(app_module.HardlinkCache(
            source_path=str(outside),
            dest_path=str(inside),
        ))
        app_module.db.session.commit()
        task_id = task.id

        removed = app_module.repair_out_of_root_mappings()
        assert removed >= 1
        assert app_module.FileLinkMap.query.filter_by(task_id=task_id).count() == 0
        assert app_module.HardlinkCache.query.filter_by(source_path=str(outside)).count() == 0

    with app_module.app.app_context():
        app_module.FileLinkMap.query.filter_by(task_id=task_id).delete()
        task = app_module.db.session.get(app_module.HardlinkTask, task_id)
        if task:
            app_module.db.session.delete(task)
        app_module.db.session.commit()


def test_config_snapshot_round_trips_encrypted_secret():
    with app_module.app.app_context():
        app_module.set_config('critical_action_passphrase', 'snapshot-secret')
        snapshot = app_module.save_config_snapshot(label='secret-snapshot')
        app_module.set_config('critical_action_passphrase', 'changed')
        ok, _ = app_module.restore_config_snapshot(snapshot)
        assert ok is True
        assert app_module.get_config('critical_action_passphrase') == 'snapshot-secret'
        app_module.AppConfigSnapshot.query.filter_by(id=snapshot.id).delete()
        app_module.set_config('critical_action_passphrase', '')
        app_module.db.session.commit()


def test_validate_path_rejects_system_roots_and_accepts_media_tmp(tmp_path):
    with app_module.app.app_context():
        ok, _ = app_module.validate_path('/etc/passwd')
        assert ok is False
        ok2, _ = app_module.validate_path('/app/instance/hardlink_manager.db')
        assert ok2 is False
        ok3, _ = app_module.validate_path(str(tmp_path / 'media'))
        assert ok3 is True
        ok4, _ = app_module.validate_path('/')
        assert ok4 is False


def test_existing_hardlink_recovers_missing_mapping_and_cache(tmp_path):
    src_dir = tmp_path / 'src'
    dst_dir = tmp_path / 'dst'
    src_dir.mkdir()
    dst_dir.mkdir()
    src = src_dir / 'a.mkv'
    src.write_text('recover-me', encoding='utf-8')

    with app_module.app.app_context():
        task = app_module.HardlinkTask(
            name='recover-test',
            source_dir=str(src_dir),
            dest_dir=str(dst_dir),
            extensions='.mkv',
            create_folder=True,
            use_cache=True,
            min_file_age_seconds=0,
        )
        app_module.db.session.add(task)
        app_module.db.session.commit()
        task_id = task.id

        ok, _ = app_module.svc_create_hardlink_for_file(
            task, src, app_module.HardlinkCache, app_module.FileLinkMap, app_module.db, app_module.safe_unlink
        )
        assert ok is True
        app_module.db.session.commit()

        app_module.FileLinkMap.query.filter_by(source_path=str(src)).delete()
        app_module.db.session.commit()
        ok2, msg2 = app_module.svc_create_hardlink_for_file(
            task, src, app_module.HardlinkCache, app_module.FileLinkMap, app_module.db, app_module.safe_unlink
        )
        app_module.db.session.commit()
        assert ok2 is True
        assert '已存在' in msg2
        assert app_module.FileLinkMap.query.filter_by(source_path=str(src)).count() == 1

        app_module.HardlinkCache.query.filter_by(source_path=str(src)).delete()
        app_module.db.session.commit()
        ok3, _ = app_module.svc_create_hardlink_for_file(
            task, src, app_module.HardlinkCache, app_module.FileLinkMap, app_module.db, app_module.safe_unlink
        )
        app_module.db.session.commit()
        assert ok3 is True
        assert app_module.HardlinkCache.query.filter_by(source_path=str(src)).count() == 1

    with app_module.app.app_context():
        app_module.FileLinkMap.query.filter_by(task_id=task_id).delete()
        app_module.HardlinkCache.query.filter_by(source_path=str(src)).delete()
        task = app_module.db.session.get(app_module.HardlinkTask, task_id)
        if task:
            app_module.db.session.delete(task)
        app_module.db.session.commit()


def test_security_headers_and_no_store_for_dynamic_pages():
    client = app_module.app.test_client()
    response = client.get('/')
    assert response.status_code == 200
    assert "object-src 'none'" in response.headers.get('Content-Security-Policy', '')
    assert response.headers.get('Cache-Control') == 'no-store'
    assert response.headers.get('Permissions-Policy')
    assert response.headers.get('Cross-Origin-Opener-Policy') == 'same-origin'


def test_validate_host_rejects_paths_credentials_and_query_strings():
    with app_module.app.app_context():
        assert app_module.validate_host('http://qb.local:8080')[0] is True
        assert app_module.validate_host('http://qb.local:8080/api')[0] is False
        assert app_module.validate_host('http://user:pass@qb.local:8080')[0] is False
        assert app_module.validate_host('http://qb.local:8080?x=1')[0] is False


def test_schema_v12_execution_indexes_exist():
    with app_module.app.app_context():
        rows = app_module.db.session.execute(app_module.db.text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_job_execution_status_started','idx_job_execution_cron_lookup')"
        )).fetchall()
    assert {row[0] for row in rows} == {'idx_job_execution_status_started', 'idx_job_execution_cron_lookup'}


def test_initial_setup_creates_database_admin():
    client = app_module.app.test_client()
    old_allow = app_module.app.config.get('ALLOW_NO_AUTH')
    app_module.app.config['ALLOW_NO_AUTH'] = False
    with app_module.app.app_context():
        app_module.AdminUser.query.delete()
        app_module.db.session.commit()
    app_module.app.config['SETUP_TOKEN'] = 'test-setup-token'
    try:
        response = client.get('/', follow_redirects=False)
        assert response.status_code == 302
        assert '/setup' in (response.headers.get('Location') or '')
        page = client.get('/setup')
        assert page.status_code == 200
        with client.session_transaction() as sess:
            token = sess.get('_csrf_token')
        created = client.post('/setup', data={
            'csrf_token': token,
            'setup_token': 'test-setup-token',
            'username': 'setup-admin',
            'password': 'setup-password-123',
            'confirm_password': 'setup-password-123',
        }, follow_redirects=False)
        assert created.status_code == 302
        with app_module.app.app_context():
            admin = app_module.AdminUser.query.filter_by(username='setup-admin').first()
            assert admin is not None
            assert admin.password_hash != 'setup-password-123'
        assert client.get('/').status_code == 200
    finally:
        with app_module.app.app_context():
            app_module.AdminUser.query.delete()
            app_module.db.session.commit()
        app_module.app.config['ALLOW_NO_AUTH'] = old_allow


def test_database_admin_login_requires_valid_totp():
    client = app_module.app.test_client()
    old_allow = app_module.app.config.get('ALLOW_NO_AUTH')
    app_module.app.config['ALLOW_NO_AUTH'] = False
    with app_module.app.app_context():
        app_module.AdminUser.query.delete()
        secret = app_module._generate_totp_secret()
        admin = app_module.AdminUser(
            username='totp-admin',
            password_hash=app_module.generate_password_hash('totp-password-123'),
            totp_secret=secret,
            totp_enabled=True,
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    try:
        client.get('/login')
        with client.session_transaction() as sess:
            token = sess.get('_csrf_token')
        bad = client.post('/login', data={
            'csrf_token': token,
            'username': 'totp-admin',
            'password': 'totp-password-123',
            'otp': '000000',
        })
        assert bad.status_code == 401
        code = app_module._totp_code(secret, int(app_module.datetime.now(app_module.UTC).timestamp()) // 30)
        good = client.post('/login', data={
            'csrf_token': token,
            'username': 'totp-admin',
            'password': 'totp-password-123',
            'otp': code,
        }, follow_redirects=False)
        assert good.status_code == 302
    finally:
        with app_module.app.app_context():
            app_module.AdminUser.query.delete()
            app_module.db.session.commit()
        app_module.app.config['ALLOW_NO_AUTH'] = old_allow


def test_cross_seed_detection_by_content_path(monkeypatch):
    downloader = SimpleNamespace(id=1, enabled=True, type='qbittorrent', category='')
    torrents = [
        {'hash': 'aaa', 'name': 'show', 'content_path': '/data/show', 'save_path': '/data'},
        {'hash': 'bbb', 'name': 'show-copy', 'content_path': '/data/show', 'save_path': '/data'},
        {'hash': 'ccc', 'name': 'other', 'content_path': '/data/other', 'save_path': '/data'},
    ]
    monkeypatch.setattr(app_module, 'list_torrents', lambda _d, use_category=True: torrents)
    related = app_module.find_cross_seed_torrents(downloader, 'aaa')
    assert [item['hash'] for item in related] == ['bbb']


def test_recycle_bin_moves_file_instead_of_unlink(tmp_path):
    recycle_dir = tmp_path / 'recycle'
    src = tmp_path / 'victim.mkv'
    src.write_text('keep', encoding='utf-8')
    with app_module.app.app_context():
        app_module.set_config('delete_recycle_bin_enabled', 'true')
        app_module.set_config('delete_recycle_bin_dir', str(recycle_dir))
        ok, dest = app_module.move_to_recycle_bin(src)
        assert ok is True
        assert not src.exists()
        assert Path(dest).exists()
        assert Path(dest).read_text(encoding='utf-8') == 'keep'
        app_module.set_config('delete_recycle_bin_enabled', 'false')


def test_job_queue_enqueue_claim_finish_and_dedupe():
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        app_module.db.session.commit()
        row, created, _ = app_module.enqueue_job('backup', source='test')
        assert row is not None and created is True
        duplicate, created2, _ = app_module.enqueue_job('backup', source='test')
        assert duplicate.id == row.id
        assert created2 is False
        claimed = app_module.claim_next_job('unit-worker')
        assert claimed is not None
        assert claimed.status == 'running'
        app_module.finish_job(claimed, True, 'ok')
        assert app_module.db.session.get(app_module.JobQueue, row.id).status == 'success'
        app_module.JobQueue.query.delete()
        app_module.db.session.commit()


def test_job_queue_api_create_status_and_stop():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        app_module.db.session.commit()
    try:
        created = client.post('/api/v1/jobs', json={'type': 'backup'})
        assert created.status_code == 201
        job = created.get_json()['job']
        fetched = client.get(f"/api/v1/jobs/{job['id']}")
        assert fetched.status_code == 200
        assert fetched.get_json()['job']['status'] == 'queued'
        stopped = client.post(f"/api/v1/jobs/{job['id']}/stop")
        assert stopped.status_code == 200
        assert stopped.get_json()['ok'] is True
    finally:
        with app_module.app.app_context():
            app_module.JobQueue.query.delete()
            app_module.db.session.commit()


def test_metrics_openapi_and_jobs_partial_endpoints():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        app_module.db.session.commit()
    try:
        metrics = client.get('/api/v1/metrics')
        assert metrics.status_code == 200
        assert b'hlm_tasks_total' in metrics.data
        spec = client.get('/api/v1/openapi.json')
        assert spec.status_code == 200
        assert '/api/v1/jobs' in spec.get_json()['paths']
        partial = client.get('/partials/jobs')
        assert partial.status_code == 200
    finally:
        with app_module.app.app_context():
            app_module.JobQueue.query.delete()
            app_module.db.session.commit()


def test_worker_processes_backup_job(tmp_path):
    import worker
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        old_backup_dir = app_module.get_config('backup_dir')
        app_module.set_config('backup_dir', str(tmp_path / 'backups'))
        row, created, _ = app_module.enqueue_job('backup', source='test-worker')
        assert created is True
        job_id = row.id
    try:
        assert worker.process_one('unit-worker') is True
        with app_module.app.app_context():
            fresh = app_module.db.session.get(app_module.JobQueue, job_id)
            assert fresh.status in {'success', 'failed'}
            assert fresh.finished_at is not None
    finally:
        with app_module.app.app_context():
            app_module.set_config('backup_dir', old_backup_dir)
            app_module.JobQueue.query.delete()
            app_module.db.session.commit()


def test_list_torrents_applies_category_filter(monkeypatch):
    downloader = SimpleNamespace(id=1, enabled=True, type='qbittorrent', category='movies')
    calls = []

    def fake_qb_request(_downloader, _method, _path, params=None, data=None, retry_auth=True):
        calls.append(params)
        return SimpleNamespace(json=lambda: [])

    monkeypatch.setattr(app_module, 'qb_request', fake_qb_request)
    app_module.list_torrents(downloader, use_category=True)
    app_module.list_torrents(downloader, use_category=False)
    assert calls[0] == {'category': 'movies'}
    assert calls[1] is None


def test_recycle_item_restore_route(tmp_path):
    source = tmp_path / 'media' / 'a.mkv'
    source.parent.mkdir()
    source.write_text('restore-me', encoding='utf-8')
    recycle_dir = tmp_path / 'recycle'
    with app_module.app.app_context():
        app_module.set_config('delete_recycle_bin_enabled', 'true')
        app_module.set_config('delete_recycle_bin_dir', str(recycle_dir))
        ok, dest = app_module.move_to_recycle_bin(source)
        assert ok is True
        item = app_module.RecycleItem.query.filter_by(recycled_path=dest).first()
        assert item is not None
        item_id = item.id
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['_csrf_token'] = 'recycle-csrf'
    try:
        response = client.post(f'/recycle/restore/{item_id}', data={'csrf_token': 'recycle-csrf'}, follow_redirects=False)
        assert response.status_code in (200, 302)
        assert source.exists()
        assert source.read_text(encoding='utf-8') == 'restore-me'
        with app_module.app.app_context():
            fresh = app_module.db.session.get(app_module.RecycleItem, item_id)
            assert fresh.restored_at is not None
    finally:
        with app_module.app.app_context():
            app_module.RecycleItem.query.filter_by(id=item_id).delete()
            app_module.set_config('delete_recycle_bin_enabled', 'false')
            app_module.db.session.commit()


def test_hardlink_execute_queues_when_worker_heartbeat_is_live(tmp_path):
    source = tmp_path / 'src'
    dest = tmp_path / 'dst'
    source.mkdir(); dest.mkdir()
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        task = app_module.HardlinkTask(name='async-exec-test', source_dir=str(source), dest_dir=str(dest), extensions='.mkv')
        app_module.db.session.add(task)
        app_module.db.session.commit()
        task_id = task.id
        app_module.set_config('worker_last_seen_at', app_module.datetime.now(app_module.UTC).isoformat())
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['_csrf_token'] = 'async-csrf'
    try:
        resp = client.post(f'/hardlink/execute/{task_id}', data={'csrf_token': 'async-csrf'}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 200
        with app_module.app.app_context():
            row = app_module.JobQueue.query.filter_by(job_type='hardlink', target_id=task_id).first()
            assert row is not None
            assert row.status == 'queued'
    finally:
        with app_module.app.app_context():
            app_module.JobQueue.query.delete()
            app_module.HardlinkTask.query.filter_by(id=task_id).delete()
            app_module.set_config('worker_last_seen_at', '')
            app_module.db.session.commit()


def test_backup_now_queues_when_worker_is_live():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        app_module.set_config('worker_last_seen_at', app_module.datetime.now(app_module.UTC).isoformat())
    with client.session_transaction() as sess:
        sess['_csrf_token'] = 'backup-csrf'
    try:
        resp = client.post('/cron/backup-now', data={'csrf_token': 'backup-csrf'}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 200
        with app_module.app.app_context():
            row = app_module.JobQueue.query.filter_by(job_type='backup').first()
            assert row is not None
            assert row.status == 'queued'
    finally:
        with app_module.app.app_context():
            app_module.JobQueue.query.delete()
            app_module.set_config('worker_last_seen_at', '')
            app_module.db.session.commit()


def test_cross_seed_delete_path_creates_pending_without_deleting_torrent(tmp_path):
    from core.services.delete_service import scan_delete_rows
    src = tmp_path / 'src.mkv'
    dst = tmp_path / 'dst.mkv'
    dst.write_text('x', encoding='utf-8')
    src.unlink(missing_ok=True)
    row = _row(src, dst)
    row.source_type = 'downloader'
    row.torrent_hash = 'hash-target'
    row.downloader_id = 1
    task = _task(tmp_path, dry_run=False)
    task.downloader = SimpleNamespace(id=1, enabled=True, type='qbittorrent')
    pending = []
    deleted = []

    def get_config(key, default=None):
        if key == 'delete_protect_cross_seed':
            return 'true'
        if key in {'delete_match_strict_mode', 'notify_on_delete', 'notify_on_risky_delete'}:
            return 'false'
        return default

    def create_pending(_task, _row, path, torrent_hash, match_by, reason):
        pending.append((path, torrent_hash, match_by, reason))

    ok, deleted_count, _hits, pending_count = scan_delete_rows(
        task, [row], lambda *_a: (_ for _ in ()).throw(AssertionError('should not match')),
        lambda *_a: deleted.append(True) or True,
        lambda *_a, **_k: None, get_config, lambda *_a: None, create_pending,
        find_cross_seed=lambda _d, _h: [{'hash': 'hash-other', 'name': 'other'}],
    )
    assert ok is True
    assert deleted == []
    assert deleted_count == 0
    assert pending_count == 1
    assert pending and pending[0][1] == 'hash-target'
    assert 'cross-seed' in pending[0][3]


def test_admin_password_change_updates_hash():
    client = app_module.app.test_client()
    old_allow = app_module.app.config.get('ALLOW_NO_AUTH')
    app_module.app.config['ALLOW_NO_AUTH'] = False
    with app_module.app.app_context():
        app_module.AdminUser.query.delete()
        admin = app_module.AdminUser(username='pw-admin', password_hash=app_module.generate_password_hash('old-password-123'))
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        admin_id = admin.id
    try:
        with client.session_transaction() as sess:
            sess['_csrf_token'] = 'pw-csrf'
            sess['logged_in'] = True
            sess['login_user'] = 'pw-admin'
            sess['admin_user_id'] = admin_id
        resp = client.post('/settings/admin/password', data={
            'csrf_token': 'pw-csrf',
            'current_password': 'old-password-123',
            'new_password': 'new-password-456',
            'confirm_password': 'new-password-456',
        }, follow_redirects=False)
        assert resp.status_code in (200, 302)
        with app_module.app.app_context():
            admin = app_module.db.session.get(app_module.AdminUser, admin_id)
            assert app_module.check_password_hash(admin.password_hash, 'new-password-456')
    finally:
        with app_module.app.app_context():
            app_module.AdminUser.query.delete()
            app_module.db.session.commit()
        app_module.app.config['ALLOW_NO_AUTH'] = old_allow


def test_recycle_permanent_delete_removes_file_and_record(tmp_path):
    source = tmp_path / 'src' / 'a.mkv'
    source.parent.mkdir()
    source.write_text('delete-me', encoding='utf-8')
    recycle_dir = tmp_path / 'recycle'
    with app_module.app.app_context():
        app_module.set_config('delete_recycle_bin_enabled', 'true')
        app_module.set_config('delete_recycle_bin_dir', str(recycle_dir))
        app_module.set_config('critical_action_passphrase', '')
        ok, dest = app_module.move_to_recycle_bin(source)
        assert ok is True
        item = app_module.RecycleItem.query.filter_by(recycled_path=dest).first()
        item_id = item.id
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['_csrf_token'] = 'recycle-del-csrf'
    try:
        resp = client.post(f'/recycle/delete/{item_id}', data={'csrf_token': 'recycle-del-csrf'}, follow_redirects=False)
        assert resp.status_code in (200, 302)
        assert not Path(dest).exists()
        with app_module.app.app_context():
            assert app_module.db.session.get(app_module.RecycleItem, item_id) is None
    finally:
        with app_module.app.app_context():
            app_module.RecycleItem.query.filter_by(id=item_id).delete()
            app_module.set_config('delete_recycle_bin_enabled', 'false')
            app_module.db.session.commit()


def test_worker_reconciles_stale_running_job_and_cancels_queued_stop():
    import worker
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        stale = app_module.JobQueue(
            run_key='backup:stale', job_name='stale', job_type='backup', source='test',
            status='running', started_at=app_module.datetime.now(app_module.UTC) - app_module.timedelta(hours=2),
        )
        cancelled = app_module.JobQueue(
            run_key='backup:cancel', job_name='cancel', job_type='backup', source='test',
            status='queued', stop_requested=True,
        )
        app_module.db.session.add_all([stale, cancelled])
        app_module.db.session.commit()
        stale_id, cancelled_id = stale.id, cancelled.id
    try:
        assert worker.reconcile_stale_jobs(60) >= 1
        assert worker.process_one('cancel-worker') is True
        with app_module.app.app_context():
            assert app_module.db.session.get(app_module.JobQueue, stale_id).status == 'failed'
            assert app_module.db.session.get(app_module.JobQueue, cancelled_id).status == 'cancelled'
    finally:
        with app_module.app.app_context():
            app_module.JobQueue.query.delete()
            app_module.db.session.commit()


def test_job_queue_priority_and_retry():
    with app_module.app.app_context():
        app_module.JobQueue.query.delete()
        low, _, _ = app_module.enqueue_job('backup', source='test', priority=0, max_retries=1)
        high, _, _ = app_module.enqueue_job('hardlink', target_id=999999, source='test', priority=10, max_retries=1)
        claimed = app_module.claim_next_job('priority-worker')
        assert claimed.id == high.id
        app_module.finish_job(claimed, False, 'boom')
        assert app_module.db.session.get(app_module.JobQueue, high.id).status == 'queued'
        assert app_module.db.session.get(app_module.JobQueue, high.id).retry_count == 1
        claimed2 = app_module.claim_next_job('retry-worker')
        assert claimed2.id == high.id
        app_module.finish_job(claimed2, True, 'ok')
        assert app_module.db.session.get(app_module.JobQueue, high.id).status == 'success'
        app_module.JobQueue.query.delete()
        app_module.db.session.commit()
