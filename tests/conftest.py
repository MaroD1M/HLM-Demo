import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_INSTANCE = tempfile.mkdtemp(prefix='hlm-test-instance-')
os.environ.setdefault('HLM_INSTANCE_PATH', _TEST_INSTANCE)
os.environ.setdefault('SECRET_KEY', 'hlm-test-only-secret-key')
os.environ.setdefault('APP_LOG_DIR', os.path.join(_TEST_INSTANCE, 'logs'))
os.environ.setdefault('ACCESS_LOG_ENABLED', 'false')
os.environ.setdefault('TRUST_PROXY_HEADERS', 'false')
os.environ.setdefault('HLM_ALLOW_NO_AUTH', 'true')
atexit.register(lambda: shutil.rmtree(_TEST_INSTANCE, ignore_errors=True))

from app import app, init_app, set_config


_INITIALIZED = False


def _ensure_init():
    global _INITIALIZED
    if not _INITIALIZED:
        with app.app_context():
            init_app()
            set_config('backup_dir', str(Path(app.instance_path) / 'backups'))
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
        set_config('github_version_check_enabled', 'false')
