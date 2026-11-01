from app import app, init_app


_INITIALIZED = False


def _client():
    global _INITIALIZED
    if not _INITIALIZED:
        with app.app_context():
            init_app()
        _INITIALIZED = True
    return app.test_client()


def test_health_endpoint():
    client = _client()
    resp = client.get('/api/health')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    assert resp.get_json()['status'] == 'ok'


def test_api_runtime_summary_get():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('dev_mode', 'true')
        set_config('security_read_only_enabled', 'true')
        set_config('security_ip_allowlist_enabled', 'false')
        set_config('webhook_enabled', 'true')
    resp = client.get('/api/runtime/summary')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert 'release' in data
    assert data['settings']['dev_mode'] == 'true'
    assert data['settings']['read_only'] == 'true'
    assert data['settings']['webhook_enabled'] == 'true'


def test_api_webhook_test_is_disabled_by_default():
    client = _client()
    resp = client.post('/api/webhooks/test', json={'hello': 'world'})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['ok'] is False


def test_api_runtime_summary_requires_auth_when_login_enabled():
    client = _client()
    old_user = app.config.get('APP_USERNAME', '')
    old_pass = app.config.get('APP_PASSWORD', '')
    try:
        app.config['APP_USERNAME'] = 'admin'
        app.config['APP_PASSWORD'] = 'secret'
        resp = client.get('/api/runtime/summary')
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['ok'] is False
    finally:
        app.config['APP_USERNAME'] = old_user
        app.config['APP_PASSWORD'] = old_pass


def test_api_webhook_test_enabled_handles_failure_response():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('webhook_enabled', 'true')
        set_config('webhook_url', 'http://127.0.0.1:9/hook')
        set_config('webhook_secret', 'abc')
    try:
        resp = client.post('/api/webhooks/test', json={'probe': 1})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['ok'] is False
    finally:
        with app.app_context():
            from app import set_config
            set_config('webhook_enabled', 'false')
            set_config('webhook_url', '')
            set_config('webhook_secret', '')


def test_api_webhook_test_requires_api_token_when_configured():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('webhook_enabled', 'false')
        set_config('api_access_token', 'abc-token')
    try:
        r1 = client.post('/api/webhooks/test', json={'probe': 1})
        assert r1.status_code == 401
        r2 = client.post('/api/webhooks/test', json={'probe': 1}, headers={'X-API-Token': 'bad'})
        assert r2.status_code == 401
        r3 = client.post('/api/webhooks/test', json={'probe': 1}, headers={'X-API-Token': 'abc-token'})
        assert r3.status_code in (200, 400)
    finally:
        with app.app_context():
            from app import set_config
            set_config('api_access_token', '')


def test_api_tasks_status_get():
    client = _client()
    resp = client.get('/api/tasks/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'hardlink_tasks' in data
    assert 'delete_tasks' in data


def test_dashboard_get():
    client = _client()
    resp = client.get('/')
    assert resp.status_code == 200



def _get_csrf_token(client, warmup_path='/settings'):
    client.get(warmup_path)
    with client.session_transaction() as sess:
        return sess.get('_csrf_token')


def test_settings_save_pending_guard_keys():
    client = _client()
    with app.app_context():
        from app import set_config, get_config
        set_config('pending_source_guard_enabled', 'true')
        set_config('pending_source_guard_seconds', '900')

    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'pending_source_guard_enabled': 'false',
        'pending_source_guard_seconds': '321',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('pending_source_guard_enabled') == 'false'
        assert get_config('pending_source_guard_seconds') == '321'


def test_diagnostics_page_get():
    client = _client()
    resp = client.get('/diagnostics')
    assert resp.status_code == 200



def test_mapping_pending_filter_get():
    client = _client()
    resp = client.get('/mapping?source_type=pending')
    assert resp.status_code == 200


def test_settings_save_pending_warn_threshold():
    client = _client()
    with app.app_context():
        from app import set_config, get_config
        set_config('pending_source_warn_threshold', '200')

    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'pending_source_warn_threshold': '123',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('pending_source_warn_threshold') == '123'


def test_settings_save_pending_log_mode():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('pending_source_log_mode', 'aggregate')

    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'pending_source_log_mode': 'detail',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('pending_source_log_mode') == 'detail'


def test_login_page_when_auth_enabled():
    client = _client()
    old_user = app.config.get('APP_USERNAME', '')
    old_pass = app.config.get('APP_PASSWORD', '')
    try:
        app.config['APP_USERNAME'] = 'admin'
        app.config['APP_PASSWORD'] = 'secret'

        resp = client.get('/')
        assert resp.status_code == 302
        assert '/login' in (resp.headers.get('Location') or '')

        login_page = client.get('/login')
        assert login_page.status_code == 200

        token = _get_csrf_token(client, '/login')

        resp = client.post('/login', data={
            'csrf_token': token,
            'username': 'admin',
            'password': 'secret',
            'remember_me': 'on',
            'next': '/',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert (resp.headers.get('Location') or '').endswith('/')

        home = client.get('/')
        assert home.status_code == 200
    finally:
        app.config['APP_USERNAME'] = old_user
        app.config['APP_PASSWORD'] = old_pass


def test_login_bypass_when_auth_not_enabled():
    client = _client()
    old_user = app.config.get('APP_USERNAME', '')
    old_pass = app.config.get('APP_PASSWORD', '')
    try:
        app.config['APP_USERNAME'] = ''
        app.config['APP_PASSWORD'] = ''
        resp = client.get('/login', follow_redirects=False)
        assert resp.status_code == 302
        assert (resp.headers.get('Location') or '').endswith('/')
    finally:
        app.config['APP_USERNAME'] = old_user
        app.config['APP_PASSWORD'] = old_pass


def test_login_page_uses_local_assets():
    client = _client()
    old_user = app.config.get('APP_USERNAME', '')
    old_pass = app.config.get('APP_PASSWORD', '')
    try:
        app.config['APP_USERNAME'] = 'admin'
        app.config['APP_PASSWORD'] = 'secret'
        resp = client.get('/login')
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert '/static/vendor/bootstrap/bootstrap.min.css' in text
        assert '/static/vendor/fontawesome/css/all.min.css' in text
        assert 'fonts.googleapis.com' not in text
    finally:
        app.config['APP_USERNAME'] = old_user
        app.config['APP_PASSWORD'] = old_pass

def test_settings_save_backfill_tuning_keys():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('backfill_batch_limit', '500')
        set_config('backfill_max_candidates', '120')
        set_config('backfill_file_fetch_workers', '4')
        set_config('backfill_max_failures', '2')
        set_config('backfill_path_mappings', '')

    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'backfill_batch_limit': '800',
        'backfill_max_candidates': '160',
        'backfill_file_fetch_workers': '6',
        'backfill_max_failures': '3',
        'backfill_path_mappings': '/host=>/media;/mnt/a=>/data/a',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('backfill_batch_limit') == '800'
        assert get_config('backfill_max_candidates') == '160'
        assert get_config('backfill_file_fetch_workers') == '6'
        assert get_config('backfill_max_failures') == '3'
        assert get_config('backfill_path_mappings') == '/host=>/media;/mnt/a=>/data/a'


def test_settings_save_backfill_invalid_range_rejected():
    client = _client()
    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'backfill_file_fetch_workers': '99',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})

    assert resp.status_code == 400
    data = resp.get_json()
    assert data['ok'] is False
    assert 'backfill_file_fetch_workers' in data['message']


def test_settings_save_dev_mode_keys_and_token_keep_clear():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('dev_mode', 'false')
        set_config('dev_auto_pull', 'false')
        set_config('dev_git_repo', '')
        set_config('dev_git_branch', 'master')
        set_config('dev_auto_pip_sync', 'true')
        set_config('dev_pip_sync_timeout', '120')
        set_config('dev_git_token', 'old_token')

    token = _get_csrf_token(client, '/settings')

    # keep token when input is empty
    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'dev_mode': 'true',
        'dev_auto_pull': 'true',
        'dev_git_repo': 'https://github.com/MaroD1M/HLM-Demo.git',
        'dev_git_branch': 'master',
        'dev_auto_pip_sync': 'false',
        'dev_pip_sync_timeout': '180',
        'dev_git_token': '',
        'dev_git_token_clear': 'false',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('dev_mode') == 'true'
        assert get_config('dev_auto_pull') == 'true'
        assert get_config('dev_git_repo') == 'https://github.com/MaroD1M/HLM-Demo.git'
        assert get_config('dev_auto_pip_sync') == 'false'
        assert get_config('dev_pip_sync_timeout') == '180'
        assert get_config('dev_git_token') == 'old_token'

    token2 = _get_csrf_token(client, '/settings')
    resp2 = client.post('/settings/save', data={
        'csrf_token': token2,
        'dev_git_token': '',
        'dev_git_token_clear': 'true',
    })
    assert resp2.status_code in (200, 302)
    with app.app_context():
        from app import get_config
        assert get_config('dev_git_token') == ''


def test_settings_save_dev_mode_invalid_rejected():
    client = _client()
    token = _get_csrf_token(client, '/settings')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'dev_git_branch': 'bad branch name',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})

    assert resp.status_code == 400
    data = resp.get_json()
    assert data['ok'] is False
    assert 'dev_git_branch' in data['message']


def test_settings_import_backfill_invalid_value_rejected():
    client = _client()
    token = _get_csrf_token(client, '/settings')

    bad_payload = '{"backfill_batch_limit":"10"}'.encode('utf-8')
    resp = client.post(
        '/settings/import',
        data={
            'csrf_token': token,
            'config_file': (__import__('io').BytesIO(bad_payload), 'settings.json'),
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data['ok'] is False
    assert 'backfill_batch_limit' in data['message']


def test_settings_save_writes_audit_log():
    client = _client()
    token = _get_csrf_token(client, '/settings')
    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'pending_source_warn_threshold': '222',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import OperationLog
        row = OperationLog.query.filter_by(operation_type='settings_saved').order_by(OperationLog.id.desc()).first()
        assert row is not None
        assert 'changed=' in (row.message or '')


def test_settings_import_failure_writes_audit_log():
    client = _client()
    token = _get_csrf_token(client, '/settings')
    bad_payload = '{"dev_git_branch":"bad branch name"}'.encode('utf-8')
    resp = client.post(
        '/settings/import',
        data={
            'csrf_token': token,
            'config_file': (__import__('io').BytesIO(bad_payload), 'settings.json'),
        },
        content_type='multipart/form-data',
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400
    with app.app_context():
        from app import OperationLog
        row = OperationLog.query.filter_by(operation_type='settings_import_failed').order_by(OperationLog.id.desc()).first()
        assert row is not None


def test_settings_export_includes_meta_and_settings_wrapper():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('critical_action_passphrase', 'secret')
        set_config('dev_git_token', 'tok-1')
        set_config('security_2fa_secret', '2fa')
        set_config('webhook_secret', 'wh-secret')
        set_config('api_access_token', 'api-token')
    resp = client.get('/settings/export')
    assert resp.status_code == 200
    payload = __import__('json').loads(resp.get_data(as_text=True))
    assert '__meta' in payload
    assert 'settings' in payload
    assert isinstance(payload['settings'], dict)
    assert payload['settings'].get('critical_action_passphrase') == '***'
    assert payload['settings'].get('dev_git_token') == '***'
    assert payload['settings'].get('security_2fa_secret') == '***'
    assert payload['settings'].get('webhook_secret') == '***'
    assert payload['settings'].get('api_access_token') == '***'


def test_settings_snapshot_save_and_list_render():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('security_read_only_enabled', 'false')
    token = _get_csrf_token(client, '/settings')
    resp = client.post('/settings/snapshot/save', data={
        'csrf_token': token,
        'snapshot_label': 'smoke-snapshot',
        'snapshot_note': 'note',
    })
    assert resp.status_code in (200, 302)

    page = client.get('/settings')
    assert page.status_code == 200
    assert 'smoke-snapshot' in page.get_data(as_text=True)
    assert '功能说明' in page.get_data(as_text=True)


def test_settings_snapshot_delete():
    client = _client()
    token = _get_csrf_token(client, '/settings')
    save_resp = client.post('/settings/snapshot/save', data={
        'csrf_token': token,
        'snapshot_label': 'delete-me-snapshot',
        'snapshot_note': 'delete test',
    })
    assert save_resp.status_code in (200, 302)

    with app.app_context():
        from app import AppConfigSnapshot
        row = AppConfigSnapshot.query.filter_by(label='delete-me-snapshot').order_by(AppConfigSnapshot.id.desc()).first()
        assert row is not None
        sid = row.id

    token2 = _get_csrf_token(client, '/settings')
    del_resp = client.post(f'/settings/snapshot/delete/{sid}', data={'csrf_token': token2})
    assert del_resp.status_code in (200, 302)

    with app.app_context():
        from app import AppConfigSnapshot, db
        deleted = db.session.get(AppConfigSnapshot, sid)
        assert deleted is None


def test_settings_import_legacy_flat_payload_compatible():
    client = _client()
    token = _get_csrf_token(client, '/settings')
    legacy_payload = '{"pending_source_warn_threshold":"321"}'.encode('utf-8')
    resp = client.post(
        '/settings/import',
        data={
            'csrf_token': token,
            'config_file': (__import__('io').BytesIO(legacy_payload), 'legacy.json'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code in (200, 302)
    with app.app_context():
        from app import get_config
        assert get_config('pending_source_warn_threshold') == '321'


def test_diagnostics_backfill_metrics_endpoint_get():
    client = _client()
    resp = client.get('/diagnostics/backfill-metrics')
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert isinstance(payload['items'], list)


def test_cron_add_clean_backfill_failures_type():
    client = _client()
    token = _get_csrf_token(client, '/cron')

    resp = client.post('/cron/add', data={
        'csrf_token': token,
        'name': '回填失败重置测试',
        'task_type': 'clean_backfill_failures',
        'custom_cron': '0 5 * * *',
        'description': 'smoke',
    })
    assert resp.status_code in (200, 302)



def test_cron_test_rejects_unsupported_type():
    client = _client()
    token = _get_csrf_token(client, '/cron')

    add_resp = client.post('/cron/add', data={
        'csrf_token': token,
        'name': '日志清理测试拒绝测试',
        'task_type': 'clean_logs',
        'custom_cron': '15 5 * * *',
        'description': 'test reject smoke',
    })
    assert add_resp.status_code in (200, 302)

    from app import CronJob
    with client.application.app_context():
        job = CronJob.query.filter_by(name='日志清理测试拒绝测试').order_by(CronJob.id.desc()).first()
        assert job is not None
        job_id = job.id

    token2 = _get_csrf_token(client, '/cron')
    resp = client.post(
        f'/cron/test/{job_id}',
        data={'csrf_token': token2},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload['ok'] is False
    assert '暂不支持测试' in payload['message']

def test_cron_test_clean_backfill_failures_type():
    client = _client()
    token = _get_csrf_token(client, '/cron')

    add_resp = client.post('/cron/add', data={
        'csrf_token': token,
        'name': '回填失败重置测试模式测试',
        'task_type': 'clean_backfill_failures',
        'custom_cron': '10 5 * * *',
        'description': 'test smoke',
    })
    assert add_resp.status_code in (200, 302)

    from app import CronJob
    with client.application.app_context():
        job = CronJob.query.filter_by(name='回填失败重置测试模式测试').order_by(CronJob.id.desc()).first()
        assert job is not None
        job_id = job.id

    token2 = _get_csrf_token(client, '/cron')
    resp = client.post(
        f'/cron/test/{job_id}',
        data={'csrf_token': token2},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert '仅测试不写入' in payload['message']


def test_scheduler_timezone_matches_app_tz():
    from app import scheduler, APP_TZ
    sch_tz = str(getattr(scheduler, 'timezone', ''))
    app_tz = str(APP_TZ)
    assert sch_tz == app_tz


def test_cron_page_contains_help_toggle():
    client = _client()
    resp = client.get('/cron')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '页面说明' in text


def test_cron_page_contains_next_run_and_last_exec_columns():
    client = _client()
    resp = client.get('/cron')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '下次执行（本地）' in text
    assert '最近执行' in text

def test_settings_dev_restart_disabled():
    client = _client()
    token = _get_csrf_token(client, '/settings/devops')
    resp = client.post('/settings/dev-restart', data={
        'csrf_token': token,
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload['ok'] is False
    assert '已禁用应用内自动重启' in payload['message']



def test_settings_devops_page_has_no_inapp_restart_controls():
    client = _client()
    resp = client.get('/settings/devops')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '保存并重启应用' not in text
    assert '最近应用状态' not in text
    assert '安全与访问控制' in text
    assert '启用只读模式' in text
    assert '启用 IP 白名单' in text


def test_settings_devops_save_optional_security_controls():
    client = _client()
    with app.app_context():
        from app import set_config, get_config
        set_config('security_read_only_enabled', 'false')
        set_config('security_ip_allowlist_enabled', 'false')
        set_config('security_ip_allowlist', '')
        set_config('security_role_model_enabled', 'false')
        set_config('security_2fa_enabled', 'false')
        set_config('security_2fa_secret', '')

    token = _get_csrf_token(client, '/settings/devops')
    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'save_scope': 'update',
        'security_read_only_enabled': 'true',
        'security_ip_allowlist_enabled': 'true',
        'security_ip_allowlist': '127.0.0.1,10.0.0.0/24',
        'security_role_model_enabled': 'true',
        'security_2fa_enabled': 'true',
        'security_2fa_secret': 'test-secret',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('security_read_only_enabled') == 'true'
        assert get_config('security_ip_allowlist_enabled') == 'true'
        assert get_config('security_ip_allowlist') == '127.0.0.1,10.0.0.0/24'
        assert get_config('security_role_model_enabled') == 'true'
        assert get_config('security_2fa_enabled') == 'true'
        assert get_config('security_2fa_secret') == 'test-secret'


def test_settings_devops_save_webhook_controls():
    client = _client()
    token = _get_csrf_token(client, '/settings/devops')
    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'save_scope': 'update',
        'webhook_enabled': 'true',
        'webhook_url': 'https://example.com/hook',
        'webhook_secret': 'abc123',
        'api_access_token': 'api-token',
    })
    assert resp.status_code in (200, 302)
    with app.app_context():
        from app import get_config
        assert get_config('webhook_enabled') == 'true'
        assert get_config('webhook_url') == 'https://example.com/hook'
        assert get_config('webhook_secret') == 'abc123'
        assert get_config('api_access_token') == 'api-token'


def test_settings_save_update_scope_no_nameerror_regression():
    client = _client()
    token = _get_csrf_token(client, '/settings/devops')
    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'save_scope': 'update',
        'github_version_check_enabled': 'true',
    })
    assert resp.status_code in (200, 302)


def test_hardlink_page_shows_preflight_action():
    client = _client()
    with app.app_context():
        from app import HardlinkTask, db
        task = HardlinkTask(name='preflight-smoke', source_dir='/tmp/src', dest_dir='/tmp/dst', extensions='.mkv', exclude_dirs='sample')
        db.session.add(task)
        db.session.commit()
        task_id = task.id
    resp = client.get('/hardlink')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '预览' in text
    assert '立即执行一次' in text
    with app.app_context():
        from app import HardlinkTask, db
        task = db.session.get(HardlinkTask, task_id)
        if task:
            db.session.delete(task)
            db.session.commit()


def test_cron_page_shows_preview_action():
    client = _client()
    resp = client.get('/cron')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '预览' in text


def test_logs_panel_view_switch():
    client = _client()
    resp_logs = client.get('/logs?panel_view=logs')
    assert resp_logs.status_code == 200
    t1 = resp_logs.get_data(as_text=True)
    assert '操作日志' in t1

    resp_exec = client.get('/logs?panel_view=executions')
    assert resp_exec.status_code == 200
    t2 = resp_exec.get_data(as_text=True)
    assert '任务执行历史' in t2


def test_logs_type_label_fallback_rendered():
    client = _client()
    resp = client.get('/logs')
    assert resp.status_code == 200
    assert '未归类（' in resp.get_data(as_text=True) or '操作日志' in resp.get_data(as_text=True)


def test_hardlink_new_edit_pages_accessible():
    client = _client()
    r1 = client.get('/hardlink/new')
    assert r1.status_code == 200
    assert '新建硬链接任务' in r1.get_data(as_text=True)

    with app.app_context():
        from app import HardlinkTask, db
        t = HardlinkTask(name='tmp', source_dir='/tmp/src', dest_dir='/tmp/dst', extensions='.mkv', exclude_dirs='sample')
        db.session.add(t)
        db.session.commit()
        tid = t.id

    r2 = client.get(f'/hardlink/edit/{tid}')
    assert r2.status_code == 200
    assert '编辑硬链接任务' in r2.get_data(as_text=True)

    with app.app_context():
        from app import HardlinkTask, db
        t = db.session.get(HardlinkTask, tid)
        if t:
            db.session.delete(t)
            db.session.commit()


def test_diagnostics_panel_view_switch():
    client = _client()
    r1 = client.get('/diagnostics?panel_view=overview')
    assert r1.status_code == 200
    t1 = r1.get_data(as_text=True)
    assert '环境检查' in t1
    assert '备份与恢复' in t1

    r2 = client.get('/diagnostics?panel_view=backfill')
    assert r2.status_code == 200
    t2 = r2.get_data(as_text=True)
    assert '最近自动关联指标' in t2


def test_diagnostics_backup_panel_handles_empty_state():
    client = _client()
    resp = client.get('/diagnostics?panel_view=overview')
    assert resp.status_code == 200
    assert '备份与恢复' in resp.get_data(as_text=True)


def test_diagnostics_support_bundle_download():
    client = _client()
    resp = client.get('/diagnostics/support-bundle?format=zip')
    assert resp.status_code == 200
    assert resp.headers.get('Content-Type', '').startswith('application/zip')
    assert 'attachment; filename=hlm-support-bundle-' in (resp.headers.get('Content-Disposition') or '')


def test_diagnostics_backup_restore_preview_and_restore_reject_invalid_path():
    client = _client()
    preview = client.get('/diagnostics/backup/restore-preview/../bad.db')
    assert preview.status_code in (400, 404)
    resp = client.post(
        '/diagnostics/backup/restore/../bad.db',
        data={'csrf_token': _get_csrf_token(client, '/diagnostics')},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code in (400, 403, 404)


def test_delete_pending_preview_endpoint_gets_precheck_message():
    client = _client()
    token = _get_csrf_token(client, '/delete-monitor')
    resp = client.post(
        '/delete-monitor/pending/preview',
        data={'csrf_token': token},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True


def test_logs_execution_id_filter_works():
    client = _client()
    with app.app_context():
        from app import db, OperationLog
        row = OperationLog(
            operation_type='test_exec_filter',
            target_type='Test',
            target_id=1,
            execution_id=987654321,
            target_name='smoke',
            message='exec-filter-smoke',
            success=True,
        )
        db.session.add(row)
        db.session.commit()

    resp = client.get('/logs?execution_id=987654321')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert 'exec-filter-smoke' in text


def test_dashboard_contains_new_kpi_blocks():
    client = _client()
    resp = client.get('/')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '最近20次成功率' in text
    assert '最近20次平均耗时' in text
    assert '最近200条失败类型' in text
