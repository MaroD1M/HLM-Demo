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
