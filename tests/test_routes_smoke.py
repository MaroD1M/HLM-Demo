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



def test_settings_save_pending_guard_keys():
    client = _client()
    with app.app_context():
        from app import set_config, get_config
        set_config('pending_source_guard_enabled', 'true')
        set_config('pending_source_guard_seconds', '900')

    with client.session_transaction() as sess:
        token = sess.get('_csrf_token')

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

    with client.session_transaction() as sess:
        token = sess.get('_csrf_token')

    resp = client.post('/settings/save', data={
        'csrf_token': token,
        'pending_source_warn_threshold': '123',
    })
    assert resp.status_code in (200, 302)

    with app.app_context():
        from app import get_config
        assert get_config('pending_source_warn_threshold') == '123'
