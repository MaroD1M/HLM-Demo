from app import app, init_app


_INITIALIZED = False


def _client():
    global _INITIALIZED
    if not _INITIALIZED:
        with app.app_context():
            init_app()
        _INITIALIZED = True
    return app.test_client()


def _csrf(client, path='/settings'):
    client.get(path)
    with client.session_transaction() as sess:
        return sess.get('_csrf_token')


def test_optional_security_defaults_do_not_block_dashboard():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('security_read_only_enabled', 'false')
        set_config('security_ip_allowlist_enabled', 'false')
    resp = client.get('/')
    assert resp.status_code == 200


def test_ip_allowlist_blocks_when_enabled():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('security_ip_allowlist_enabled', 'true')
        set_config('security_ip_allowlist', '10.10.10.10')

    try:
        resp = client.get('/settings', headers={'X-Forwarded-For': '127.0.0.1'})
        assert resp.status_code == 403
    finally:
        with app.app_context():
            from app import set_config
            set_config('security_ip_allowlist_enabled', 'false')
            set_config('security_ip_allowlist', '')


def test_read_only_blocks_mutating_routes_when_enabled():
    client = _client()
    with app.app_context():
        from app import set_config
        set_config('security_read_only_enabled', 'true')

    token = _csrf(client, '/settings')
    try:
        resp = client.post('/settings/save', data={
            'csrf_token': token,
            'pending_source_warn_threshold': '222',
        })
        assert resp.status_code == 403
    finally:
        with app.app_context():
            from app import set_config
            set_config('security_read_only_enabled', 'false')
