from app import _is_remote_newer


def test_remote_newer_is_detected():
    assert _is_remote_newer('v0.5.0', 'v0.5.1') is True
    assert _is_remote_newer('v0.5.0', 'v0.6.0') is True
    assert _is_remote_newer('v0.5.0', 'v1.0.0') is True


def test_same_or_older_remote_is_not_an_update():
    assert _is_remote_newer('v0.5.0', 'v0.5.0') is False
    assert _is_remote_newer('v0.5.0', 'v0.4.0') is False
    assert _is_remote_newer('v0.5.0', '-') is False


def test_cached_only_version_check_does_not_call_network(monkeypatch):
    import app as app_module
    from datetime import UTC, datetime

    with app_module.app.app_context():
        old_remote = app_module.get_config('version_check_cached_remote', '')
        old_at = app_module.get_config('version_check_cached_at', '')
        try:
            app_module.set_config('github_version_check_enabled', 'true')
            app_module.set_config('version_check_cached_remote', '')
            app_module.set_config('version_check_cached_at', '')
            monkeypatch.setattr(app_module.requests, 'get', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network must not be used')))
            info = app_module.get_release_info(allow_network=False)
            assert info['remote_version'] == '-'
            assert info['message'] == '尚未检查更新'

            app_module.set_config('version_check_cached_remote', 'v9.0.0')
            app_module.set_config('version_check_cached_at', datetime.now(UTC).isoformat())
            info = app_module.get_release_info(allow_network=False)
            assert info['remote_version'] == 'v9.0.0'
            assert info['has_update'] is True
            assert info['message'].startswith('缓存')
        finally:
            app_module.set_config('version_check_cached_remote', old_remote)
            app_module.set_config('version_check_cached_at', old_at)
