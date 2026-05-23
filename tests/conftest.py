from app import app, init_app, set_config


_INITIALIZED = False


def _ensure_init():
    global _INITIALIZED
    if not _INITIALIZED:
        with app.app_context():
            init_app()
        _INITIALIZED = True


def pytest_runtest_setup(item):
    _ensure_init()
    with app.app_context():
        # Keep optional security controls off by default so tests are isolated.
        set_config('security_read_only_enabled', 'false')
        set_config('security_ip_allowlist_enabled', 'false')
        set_config('security_ip_allowlist', '')
        set_config('api_access_token', '')
        set_config('webhook_enabled', 'false')
        set_config('webhook_url', '')
        set_config('webhook_secret', '')
        set_config('critical_action_passphrase', '')
