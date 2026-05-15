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
    assert resp.get_json()['status'] == 'ok'


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


def test_cron_page_contains_timezone_hint():
    client = _client()
    resp = client.get('/cron')
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert '按本地时区（TZ）解释并调度 Cron' in text


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
