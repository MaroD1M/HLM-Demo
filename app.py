import os
import base64
import hashlib
import hmac
import json
import secrets
import shutil
import struct
import uuid
import logging
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime, UTC, timedelta
from threading import Lock, get_ident
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:
    fcntl = None

import requests
from sqlalchemy import event
from flask import Flask, g, request, session, abort, redirect, url_for, render_template, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from core.services.backfill_service import scan_backfill_rows
from core.routes.api import init_api_routes
from core.routes.web import init_web_routes
from core.deps import RouteDeps
from core.services.config_service import build_default_config_rows, get_importable_setting_keys, validate_setting_value
from core.services.audit_service import redact_url_credentials
from core.services.hardlink_service import create_hardlink_for_file as svc_create_hardlink_for_file
from core.services.delete_service import scan_delete_rows
from core.services.backup_service import run_sqlite_backup
from core.services.execution_service import ExecutionManager
from core.services.migration_service import MigrationService
from core.services.security_service import is_ip_allowed, is_read_only_forbidden_endpoint
from core.extensions import db, scheduler
from core.models import HardlinkTask, DeleteMonitorTask, Downloader, Notifier, HardlinkCache, FileLinkMap, OperationLog, JobExecutionLog, DeletePendingAction, AppConfig, AppConfigSnapshot, CronJob, AdminUser, JobQueue, RecycleItem


_INSTANCE_PATH_ENV = (os.environ.get('HLM_INSTANCE_PATH') or '').strip()
def _env_int(name, default, minimum=None):
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    return value


app = Flask(__name__, instance_path=(os.path.abspath(_INSTANCE_PATH_ENV) if _INSTANCE_PATH_ENV else None))
APP_STARTED_AT = datetime.now(UTC)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    stream=sys.stdout,
    force=True,
)
app.logger.setLevel(logging.INFO)


@app.after_request
def add_security_headers(resp):
    # Enforce local-only static assets to avoid UI breakage when external network is unavailable.
    resp.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('Referrer-Policy', 'no-referrer')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
    resp.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    resp.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
    resp.headers.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
    if request.endpoint != 'static':
        resp.headers.setdefault('Cache-Control', 'no-store')
    request_id = getattr(g, 'request_id', '')
    if request_id:
        resp.headers.setdefault('X-Request-ID', request_id)
    return resp


def install_sqlite_pragmas():
    global SQLITE_PRAGMAS_INSTALLED
    if SQLITE_PRAGMAS_INSTALLED:
        return
    engine = db.engine
    if engine.dialect.name != 'sqlite':
        SQLITE_PRAGMAS_INSTALLED = True
        return

    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.execute('PRAGMA foreign_keys=ON')
        finally:
            cursor.close()

    SQLITE_PRAGMAS_INSTALLED = True


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'ts': datetime.fromtimestamp(record.created, UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        request_id = getattr(record, 'request_id', '')
        if request_id:
            payload['request_id'] = request_id
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _log_formatter():
    if str(os.environ.get('LOG_FORMAT', 'text')).lower() == 'json':
        return JsonLogFormatter()
    return logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')


def init_console_logger():
    # Ensure logs are always visible in container stdout (e.g., Synology Container Manager).
    # FileHandler is a StreamHandler subclass, so we must explicitly check for stdout/stderr streams.
    has_stdout_stream = False
    for h in app.logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            stream = getattr(h, 'stream', None)
            if stream in {sys.stdout, sys.stderr}:
                has_stdout_stream = True
                break

    if not has_stdout_stream:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(_log_formatter())
        app.logger.addHandler(sh)
    for root_handler in logging.getLogger().handlers:
        root_handler.setFormatter(_log_formatter())

    app.logger.propagate = False

DEFAULT_SECRET_KEY = 'default-secret-key-for-dev-only'
WEAK_SECRET_KEYS = {DEFAULT_SECRET_KEY, 'replace-with-a-strong-random-string'}
SENSITIVE_CONFIG_KEYS = frozenset({
    'critical_action_passphrase',
    'dev_git_token',
    'security_2fa_secret',
    'webhook_secret',
    'api_access_token',
})
UNDECRYPTABLE_SECRET = '__HLM_UNDECRYPTABLE_' + secrets.token_urlsafe(32)


def _load_or_create_secret_key():
    configured = (os.environ.get('SECRET_KEY') or '').strip()
    if configured and configured not in WEAK_SECRET_KEYS:
        return configured
    secret_file = Path(app.instance_path) / 'secret_key'
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        if secret_file.exists():
            stored = secret_file.read_text(encoding='utf-8').strip()
            if stored and stored not in WEAK_SECRET_KEYS:
                os.environ['SECRET_KEY'] = stored
                return stored
        generated = secrets.token_urlsafe(48)
        secret_file.write_text(generated + '\n', encoding='utf-8')
        try:
            secret_file.chmod(0o600)
        except Exception:
            pass
        os.environ['SECRET_KEY'] = generated
        return generated
    except Exception:
        generated = secrets.token_urlsafe(48)
        os.environ['SECRET_KEY'] = generated
        return generated


app.config['SECRET_KEY'] = _load_or_create_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hardlink_manager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['APP_USERNAME'] = os.environ.get('APP_USERNAME', '').strip()
app.config['APP_PASSWORD'] = os.environ.get('APP_PASSWORD', '')
app.config['REQUEST_TIMEOUT_SECONDS'] = _env_int('REQUEST_TIMEOUT_SECONDS', 10, 1)
app.config['ACCESS_LOG_ENABLED'] = os.environ.get('ACCESS_LOG_ENABLED', 'true').lower() == 'true'
app.config['APP_VERSION'] = (Path(__file__).resolve().parent / 'VERSION').read_text(encoding='utf-8').strip() if (Path(__file__).resolve().parent / 'VERSION').exists() else 'dev'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=_env_int('LOGIN_REMEMBER_DAYS', 14, 1))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = str(os.environ.get('SESSION_COOKIE_SECURE', 'false')).lower() == 'true'
app.config['TRUST_PROXY_HEADERS'] = str(os.environ.get('TRUST_PROXY_HEADERS', 'false')).lower() == 'true'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['ALLOW_NO_AUTH'] = str(os.environ.get('HLM_ALLOW_NO_AUTH', 'false')).lower() == 'true'
app.config['WORKER_MODE'] = str(os.environ.get('HLM_WORKER', 'false')).lower() == 'true'
app.config['DISABLE_SCHEDULER'] = str(os.environ.get('HLM_DISABLE_SCHEDULER', 'false')).lower() == 'true'

try:
    APP_TZ = ZoneInfo(os.environ.get('TZ', 'Asia/Shanghai'))
except Exception:
    APP_TZ = UTC

# Keep scheduler cron trigger timezone aligned with UI/runtime timezone.
scheduler.configure(timezone=APP_TZ)


def _dir_is_writable(path_obj):
    try:
        path = Path(path_obj)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / '.hlm_write_probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def init_file_logger():
    log_dir = Path(os.environ.get('APP_LOG_DIR', '/app/data/logs'))
    if not _dir_is_writable(log_dir):
        # Fallback for local tests/non-container env where /app is not writable.
        log_dir = Path(__file__).resolve().parent / 'data' / 'logs'
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

    max_mb = _env_int('APP_LOG_MAX_MB', 10, 1)
    backup_count = _env_int('APP_LOG_BACKUP_COUNT', 5, 1)
    log_file = log_dir / 'app.log'

    for h in app.logger.handlers:
        if isinstance(h, RotatingFileHandler) and Path(getattr(h, 'baseFilename', '')) == log_file:
            return

    try:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max(1, max_mb) * 1024 * 1024,
            backupCount=max(1, backup_count),
            encoding='utf-8',
        )
    except Exception as exc:
        app.logger.warning('file logging disabled: %s', exc)
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(_log_formatter())
    app.logger.addHandler(handler)
    try:
        log_file.chmod(0o600)
    except Exception:
        pass


def _inside_container():
    return Path('/.dockerenv').exists() or (os.environ.get('HLM_INSTANCE_PATH', '') or '').startswith('/app')


def _outbound_proxies(override=None):
    proxy = (override or '').strip() or (get_config('proxy_url', '') or '').strip()
    if not proxy:
        return None
    if _inside_container():
        host = ''
        try:
            host = (urlparse(proxy).hostname or '').lower()
        except Exception:
            host = ''
        if host in {'127.0.0.1', 'localhost', '::1'}:
            app.logger.warning('ignoring loopback proxy inside container: %s', host)
            return None
    return {'http': proxy, 'https': proxy}


def get_release_info(force_refresh=False):
    local_version = app.config.get('APP_VERSION', 'dev')
    enabled = str(get_config('github_version_check_enabled', 'true')).lower() == 'true'
    if not enabled and not force_refresh:
        return {
            'local_version': local_version,
            'remote_version': '-',
            'has_update': False,
            'repo': get_config('github_repo', 'marod1m/HLM-Demo'),
            'checked_at': '-',
            'message': '已关闭版本检查',
        }

    repo = (get_config('github_repo', 'marod1m/HLM-Demo') or 'marod1m/HLM-Demo').strip()
    api_base = (get_config('github_api_base', 'https://api.github.com') or 'https://api.github.com').strip().rstrip('/')
    url = f"{api_base}/repos/{repo}/releases/latest"
    checked_at = datetime.now(UTC).isoformat()
    ttl = int(get_config('version_check_cache_minutes', '720') or '720')
    if not force_refresh:
        cache_remote = (get_config('version_check_cached_remote', '') or '').strip()
        cache_checked = (get_config('version_check_cached_at', '') or '').strip()
        if cache_remote and cache_checked:
            try:
                cached_ts = datetime.fromisoformat(cache_checked.replace('Z', '+00:00'))
                if cached_ts.tzinfo is None:
                    cached_ts = cached_ts.replace(tzinfo=UTC)
                age_min = (datetime.now(UTC) - cached_ts.astimezone(UTC)).total_seconds() / 60
                if age_min < max(1, ttl):
                    return {
                        'local_version': local_version,
                        'remote_version': cache_remote,
                        'has_update': bool(cache_remote not in {'-', local_version}),
                        'repo': repo,
                        'checked_at': cache_checked,
                        'message': f'缓存({int(age_min)}分钟内)',
                    }
            except Exception as exc:
                app.logger.debug('version cache parse failed: %s', exc)
    try:
        resp = requests.get(url, timeout=app.config['REQUEST_TIMEOUT_SECONDS'], proxies=_outbound_proxies(), headers={'Accept': 'application/vnd.github+json'})
        if resp.status_code == 404:
            # Fallback: some repos only use tags without GitHub Release objects.
            tag_url = f"{api_base}/repos/{repo}/tags"
            tag_resp = requests.get(tag_url, timeout=app.config['REQUEST_TIMEOUT_SECONDS'], proxies=_outbound_proxies(), headers={'Accept': 'application/vnd.github+json'})
            tag_resp.raise_for_status()
            tags = tag_resp.json() or []
            remote = str(tags[0].get('name') if tags else '-').strip() or '-'
            msg = '检查成功（Tag模式）'
        else:
            resp.raise_for_status()
            payload = resp.json() or {}
            remote = str(payload.get('tag_name') or '').strip() or '-'
            msg = '检查成功（Release模式）'

        has_update = bool(remote not in {'-', local_version})
        set_config('version_check_cached_remote', remote, commit=False)
        set_config('version_check_cached_at', checked_at, commit=False)
        db.session.commit()
        return {
            'local_version': local_version,
            'remote_version': remote,
            'has_update': has_update,
            'repo': repo,
            'checked_at': checked_at,
            'message': msg,
        }
    except Exception as exc:
        app.logger.warning('github_version_check_failed repo=%s err=%s', repo, redact_url_credentials(str(exc)))
        return {
            'local_version': local_version,
            'remote_version': '-',
            'has_update': False,
            'repo': repo,
            'checked_at': checked_at,
            'message': f'检查失败: {redact_url_credentials(str(exc))}',
        }



db.init_app(app)
AUTH_LOCK = Lock()
AUTH_FAIL_WINDOW_SECONDS = 60
AUTH_FAIL_MAX_TIMES = 3
AUTH_BLOCK_SECONDS = 1800
AUTH_STATE = {}
AUTH_STATE_MAX_ENTRIES = 5000
APP_BOOTSTRAPPED = False
SQLITE_PRAGMAS_INSTALLED = False
SCHEDULER_LOCK_FILE = None
SCHEDULER_LOCK_PID = None
QB_SESSION_LOCK = Lock()
QB_SESSION_CACHE = {}
QB_SESSION_TTL_SECONDS = _env_int('QB_SESSION_TTL_SECONDS', 900, 60)
EXEC_MGR = ExecutionManager(db, JobExecutionLog, lambda: datetime.now(UTC), lock_dir_getter=lambda: Path(app.instance_path) / 'runlocks')
MIGRATION_SVC = MigrationService(
    db=db,
    logger=app.logger,
    instance_path_getter=lambda: app.instance_path,
    app_version_getter=lambda: app.config.get('APP_VERSION', 'dev'),
    get_config=lambda key, default=None: get_config(key, default),
    run_sqlite_backup=run_sqlite_backup,
)



def _env_auth_configured():
    username = (app.config.get('APP_USERNAME') or '').strip()
    password = app.config.get('APP_PASSWORD') or ''
    return bool(username and password)


def _db_admin_user(username=None):
    if username:
        return AdminUser.query.filter_by(username=str(username)).first()
    return AdminUser.query.order_by(AdminUser.id.asc()).first()


def _auth_configured():
    if _env_auth_configured():
        return True
    try:
        return _db_admin_user() is not None
    except Exception:
        return False


def _auth_enabled():
    return _auth_configured()


def _setup_required():
    return not _auth_configured() and not app.config.get('ALLOW_NO_AUTH', False)


def _ensure_setup_token():
    configured = (app.config.get('SETUP_TOKEN') or os.environ.get('HLM_SETUP_TOKEN') or '').strip()
    if configured:
        app.config['SETUP_TOKEN'] = configured
        return configured
    token_file = Path(app.instance_path) / 'setup_token.txt'
    try:
        if token_file.exists():
            stored = token_file.read_text(encoding='utf-8').strip()
            if stored:
                app.config['SETUP_TOKEN'] = stored
                return stored
    except Exception:
        pass
    token = secrets.token_urlsafe(24)
    app.config['SETUP_TOKEN'] = token
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token + '\n', encoding='utf-8')
        token_file.chmod(0o600)
    except Exception:
        app.logger.warning('could not write setup token file; token is only available in process memory')
    return token


def _is_logged_in():
    return bool(session.get('logged_in'))


def _generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def _totp_code(secret, counter):
    padding = '=' * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper())
    digest = hmac.new(key, struct.pack('>Q', int(counter)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{value % 1000000:06d}'


def _verify_totp(secret, code):
    text = str(code or '').strip()
    if not secret or not text.isdigit() or len(text) != 6:
        return False
    counter = int(datetime.now(UTC).timestamp()) // 30
    return any(hmac.compare_digest(_totp_code(secret, counter + delta), text) for delta in (-1, 0, 1))


def _verify_credentials(username, password):
    env_user = (app.config.get('APP_USERNAME') or '').strip()
    env_pass = app.config.get('APP_PASSWORD') or ''
    if env_user and env_pass and hmac.compare_digest(username.encode('utf-8'), env_user.encode('utf-8')) and hmac.compare_digest(password.encode('utf-8'), env_pass.encode('utf-8')):
        return True, None
    try:
        admin = _db_admin_user(username)
    except Exception:
        admin = None
    if admin and check_password_hash(admin.password_hash, password):
        return True, admin
    return False, None


def _current_admin_user():
    admin_id = session.get('admin_user_id')
    if admin_id:
        try:
            admin = db.session.get(AdminUser, int(admin_id))
        except Exception:
            admin = None
        if admin:
            return admin
    username = (session.get('login_user') or '').strip()
    return _db_admin_user(username) if username else None


def _get_client_ip():
    if app.config.get('TRUST_PROXY_HEADERS'):
        real_ip = (request.headers.get('X-Real-IP') or '').strip()
        if real_ip:
            return real_ip
        xff = (request.headers.get('X-Forwarded-For') or '').strip()
        if xff:
            return xff.split(',')[-1].strip()
    return request.remote_addr or 'unknown'


def _auth_is_blocked(ip):
    now = datetime.now(UTC)
    with AUTH_LOCK:
        state = AUTH_STATE.get(ip)
        if not state:
            return False, 0
        blocked_until = state.get('blocked_until')
        if blocked_until and blocked_until > now:
            return True, int((blocked_until - now).total_seconds())
        # Unblock and reset stale counters after block expiry.
        if blocked_until and blocked_until <= now:
            AUTH_STATE.pop(ip, None)
    return False, 0


def _record_auth_failure(ip):
    now = datetime.now(UTC)
    with AUTH_LOCK:
        state = AUTH_STATE.get(ip, {'fails': [], 'blocked_until': None})
        window_start = now - timedelta(seconds=AUTH_FAIL_WINDOW_SECONDS)
        fails = [ts for ts in state.get('fails', []) if ts >= window_start]
        fails.append(now)
        state['fails'] = fails
        if len(fails) >= AUTH_FAIL_MAX_TIMES:
            state['blocked_until'] = now + timedelta(seconds=AUTH_BLOCK_SECONDS)
        AUTH_STATE[ip] = state
        if len(AUTH_STATE) > AUTH_STATE_MAX_ENTRIES:
            stale_cutoff = now - timedelta(seconds=AUTH_FAIL_WINDOW_SECONDS)
            for key in list(AUTH_STATE.keys()):
                item = AUTH_STATE.get(key) or {}
                blocked_until = item.get('blocked_until')
                recent = [ts for ts in item.get('fails', []) if ts >= stale_cutoff]
                if not blocked_until and not recent:
                    AUTH_STATE.pop(key, None)
        return state.get('blocked_until')


def _clear_auth_failure(ip):
    with AUTH_LOCK:
        AUTH_STATE.pop(ip, None)


def ensure_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': ensure_csrf_token(), 'fmt_dt': format_datetime_local, 'auth_enabled': _auth_enabled(), 'is_logged_in': _is_logged_in()}


def format_datetime_local(dt_obj, fmt='%Y-%m-%d %H:%M:%S'):
    if not dt_obj:
        return '-'
    if isinstance(dt_obj, str):
        raw = dt_obj.strip()
        if not raw:
            return '-'
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            dt_obj = parsed
        except Exception:
            # Fall back to original string for unknown formats.
            return raw
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)
    try:
        return dt_obj.astimezone(APP_TZ).strftime(fmt)
    except Exception:
        try:
            return dt_obj.strftime(fmt)
        except Exception:
            return str(dt_obj)


@app.before_request
def security_guard():
    incoming_request_id = (request.headers.get('X-Request-ID') or '').strip()
    g.request_id = incoming_request_id[:80] if incoming_request_id else uuid.uuid4().hex
    if request.endpoint == 'static':
        return None
    is_api_request = bool(request.path.startswith('/api/'))

    if _setup_required():
        if request.endpoint in {'setup_page', 'api_bp.api_health'}:
            if request.method == 'POST':
                request_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
                session_token = session.get('_csrf_token')
                if not session_token or not request_token or request_token != session_token:
                    abort(400, description='Invalid CSRF token')
            return None
        if is_api_request:
            return jsonify({'ok': False, 'message': 'initial setup required'}), 503
        return redirect(url_for('setup_page'))

    # Allow unauthenticated health checks and auth pages, but still require CSRF on auth POSTs.
    if request.endpoint in {'api_bp.api_health', 'login_page', 'logout', 'setup_page'}:
        if request.method == 'POST':
            request_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
            session_token = session.get('_csrf_token')
            if not session_token or not request_token or request_token != session_token:
                abort(400, description='Invalid CSRF token')
        return None

    if _auth_enabled() and not _is_logged_in():
        if is_api_request:
            return jsonify({'ok': False, 'message': 'unauthorized'}), 401
        next_path = request.full_path if request.query_string else request.path
        if request.headers.get('HX-Request') == 'true':
            response = jsonify({'ok': False, 'message': 'unauthorized'})
            response.headers['HX-Redirect'] = url_for('login_page', next=next_path)
            return response, 401
        return redirect(url_for('login_page', next=next_path))

    if is_api_request:
        required_token = str(get_config('api_access_token', '') or '').strip()
        if required_token:
            provided_token = (request.headers.get('X-API-Token') or '').strip()
            if not provided_token or not secrets.compare_digest(provided_token.encode('utf-8'), required_token.encode('utf-8')):
                return jsonify({'ok': False, 'message': 'invalid api token'}), 401

    # Optional security: IP allowlist (default off).
    if str(get_config('security_ip_allowlist_enabled', 'false')).lower() == 'true':
        client_ip = _get_client_ip()
        allowlist = get_config('security_ip_allowlist', '')
        if not is_ip_allowed(client_ip, allowlist):
            log_operation(
                'security_ip_blocked',
                'Security',
                None,
                request.endpoint or '-',
                f'ip={client_ip};path={request.path}',
                False,
            )
            abort(403, description='IP not allowed')

    # Optional security: read-only mode (default off).
    if str(get_config('security_read_only_enabled', 'false')).lower() == 'true':
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and is_read_only_forbidden_endpoint(request.endpoint):
            log_operation(
                'security_read_only_blocked',
                'Security',
                None,
                request.endpoint or '-',
                f'method={request.method};path={request.path}',
                False,
            )
            abort(403, description='Read-only mode is enabled')

    if app.config.get('ACCESS_LOG_ENABLED', True):
        app.logger.info('request method=%s path=%s endpoint=%s ip=%s rid=%s', request.method, request.path, request.endpoint, request.remote_addr, g.request_id, extra={'request_id': g.request_id})
    if request.method == 'POST' and not is_api_request:
        request_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        session_token = session.get('_csrf_token')
        if not session_token or not request_token or request_token != session_token:
            abort(400, description='Invalid CSRF token')
    return None


def _safe_next_path(raw, default='/'):
    candidate = (raw or '').strip()
    if not candidate:
        return default
    decoded = unquote(candidate)
    if not candidate.startswith('/') or candidate.startswith('//') or '\\' in decoded or decoded.startswith('//'):
        return default
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    return candidate


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if _setup_required():
        return redirect(url_for('setup_page'))
    if not _auth_enabled():
        return redirect('/')

    if _is_logged_in():
        return redirect(_safe_next_path(request.args.get('next')))

    if request.method == 'POST':
        client_ip = _get_client_ip()
        blocked, remain_seconds = _auth_is_blocked(client_ip)
        if blocked:
            flash(f'登录失败次数过多，请 {remain_seconds} 秒后再试。', 'danger')
            return render_template('login.html', next_path=_safe_next_path(request.form.get('next'))), 429

        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        otp = (request.form.get('otp') or '').strip()
        remember = request.form.get('remember_me') == 'on'
        next_path = _safe_next_path(request.form.get('next'))

        credentials_ok, admin_user = _verify_credentials(username, password)
        failure_message = '用户名或密码错误'
        if credentials_ok and admin_user and admin_user.totp_enabled and not _verify_totp(admin_user.get_totp_secret(), otp):
            credentials_ok = False
            failure_message = '动态验证码错误'
        if credentials_ok:
            session.clear()
            session['logged_in'] = True
            session['login_user'] = username
            session['admin_user_id'] = admin_user.id if admin_user else None
            session.permanent = bool(remember)
            _clear_auth_failure(client_ip)
            flash('登录成功', 'success')
            return redirect(next_path)

        blocked_until = _record_auth_failure(client_ip)
        if blocked_until:
            flash('1分钟内错误达到3次，已封禁30分钟。', 'danger')
            return render_template('login.html', next_path=next_path), 429
        flash(failure_message, 'danger')
        return render_template('login.html', next_path=next_path), 401

    return render_template('login.html', next_path=_safe_next_path(request.args.get('next')))


@app.route('/setup', methods=['GET', 'POST'])
def setup_page():
    if not _setup_required():
        return redirect('/')
    if request.method == 'POST':
        expected_token = _ensure_setup_token()
        provided_token = (request.form.get('setup_token') or '').strip()
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '')
        confirm = (request.form.get('confirm_password') or '')
        if expected_token and not hmac.compare_digest(provided_token.encode('utf-8'), expected_token.encode('utf-8')):
            flash('初始化令牌错误', 'danger')
        elif not username or len(username) > 100:
            flash('用户名不能为空且不能超过 100 个字符', 'danger')
        elif len(password) < 8 or len(password) > 300:
            flash('密码长度必须在 8-300 个字符之间', 'danger')
        elif password != confirm:
            flash('两次输入的密码不一致', 'danger')
        else:
            try:
                if AdminUser.query.first():
                    return redirect(url_for('login_page'))
                admin = AdminUser(username=username, password_hash=generate_password_hash(password))
                db.session.add(admin)
                db.session.commit()
                app.config['SETUP_TOKEN'] = ''
                (Path(app.instance_path) / 'setup_token.txt').unlink(missing_ok=True)
                session.clear()
                session['logged_in'] = True
                session['login_user'] = username
                session['admin_user_id'] = admin.id
                flash('管理员账号已创建', 'success')
                return redirect('/')
            except Exception as exc:
                db.session.rollback()
                app.logger.warning('initial admin setup failed: %s', exc)
                flash('管理员创建失败，请检查数据库权限', 'danger')
    return render_template('setup.html')


@app.route('/settings/admin/password', methods=['POST'])
def admin_password_change():
    if not _is_logged_in():
        return redirect(url_for('login_page'))
    admin = _current_admin_user()
    if not admin:
        flash('当前使用环境变量账号，密码请在部署环境变量中修改', 'warning')
        return redirect('/settings/devops')
    current_password = request.form.get('current_password') or ''
    new_password = request.form.get('new_password') or ''
    confirm_password = request.form.get('confirm_password') or ''
    if not check_password_hash(admin.password_hash, current_password):
        flash('当前密码错误', 'danger')
    elif len(new_password) < 8 or len(new_password) > 300:
        flash('新密码长度必须在 8-300 个字符之间', 'danger')
    elif new_password != confirm_password:
        flash('两次输入的新密码不一致', 'danger')
    else:
        admin.password_hash = generate_password_hash(new_password)
        db.session.commit()
        log_operation('admin_password_changed', 'AdminUser', admin.id, admin.username, '管理员密码已更新')
        flash('管理员密码已更新', 'success')
    return redirect('/settings/devops')


@app.route('/settings/admin/2fa/start', methods=['POST'])
def admin_2fa_start():
    if not _is_logged_in():
        return redirect(url_for('login_page'))
    admin = _current_admin_user()
    if not admin:
        flash('环境变量账号不支持应用内 2FA，请使用管理员数据库账号', 'warning')
    else:
        admin.set_totp_secret(_generate_totp_secret())
        admin.totp_enabled = False
        db.session.commit()
        flash('2FA 密钥已生成，请使用验证器应用添加后输入动态码确认', 'success')
    return redirect('/settings/devops')


@app.route('/settings/admin/2fa/confirm', methods=['POST'])
def admin_2fa_confirm():
    if not _is_logged_in():
        return redirect(url_for('login_page'))
    admin = _current_admin_user()
    otp = (request.form.get('otp') or '').strip()
    if not admin or not admin.get_totp_secret():
        flash('请先生成 2FA 密钥', 'danger')
    elif not _verify_totp(admin.get_totp_secret(), otp):
        flash('动态验证码错误', 'danger')
    else:
        admin.totp_enabled = True
        db.session.commit()
        log_operation('admin_2fa_enabled', 'AdminUser', admin.id, admin.username, '管理员已启用 2FA')
        flash('2FA 已启用', 'success')
    return redirect('/settings/devops')


@app.route('/settings/admin/2fa/disable', methods=['POST'])
def admin_2fa_disable():
    if not _is_logged_in():
        return redirect(url_for('login_page'))
    admin = _current_admin_user()
    password = request.form.get('current_password') or ''
    if not admin or not check_password_hash(admin.password_hash, password):
        flash('验证失败，无法关闭 2FA', 'danger')
    else:
        admin.totp_enabled = False
        admin.set_totp_secret(None)
        db.session.commit()
        log_operation('admin_2fa_disabled', 'AdminUser', admin.id, admin.username, '管理员已关闭 2FA')
        flash('2FA 已关闭', 'success')
    return redirect('/settings/devops')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    session.permanent = False
    flash('已退出登录', 'success')
    return redirect('/login' if _auth_enabled() else '/')


def _decrypt_config_value(key, value):
    text = '' if value is None else str(value)
    if key not in SENSITIVE_CONFIG_KEYS or not text:
        return text if value is not None else value
    try:
        return Downloader._decrypt_text(text)
    except Exception:
        return UNDECRYPTABLE_SECRET


def _encrypt_config_value(key, value):
    if key not in SENSITIVE_CONFIG_KEYS or value is None:
        return value
    text = str(value)
    if not text or text.startswith(('fernet:', 'enc:')):
        return text
    return Downloader._encrypt_text(text)


def get_config(key, default=None):
    config = AppConfig.query.filter_by(key=key).first()
    return _decrypt_config_value(key, config.value) if config else default


def set_config(key, value, description=None, commit=True):
    stored_value = _encrypt_config_value(key, value)
    config = AppConfig.query.filter_by(key=key).first()
    if config:
        config.value = stored_value
        if description:
            config.description = description
    else:
        db.session.add(AppConfig(key=key, value=stored_value, description=description))
    if commit:
        db.session.commit()


def build_config_snapshot_payload(label='snapshot', note='', created_by=''):
    rows = AppConfig.query.order_by(AppConfig.key.asc()).all()
    items = {}
    for row in rows:
        items[row.key] = {
            'value': row.value,
            'description': row.description,
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        }
    return {
        '__meta': {
            'format_version': 1,
            'kind': 'config_snapshot',
            'label': label,
            'created_at': datetime.now(UTC).isoformat(),
            'created_by': created_by or '',
            'note': note or '',
        },
        'settings': items,
    }


def save_config_snapshot(label='snapshot', note='', created_by=''):
    payload = build_config_snapshot_payload(label=label, note=note, created_by=created_by)
    row = AppConfigSnapshot(
        label=label or 'snapshot',
        payload_json=json.dumps(payload, ensure_ascii=False, indent=2),
        created_by=created_by or '',
        note=note or '',
    )
    db.session.add(row)
    db.session.commit()
    return row


def restore_config_snapshot(snapshot_row):
    try:
        payload = json.loads(snapshot_row.payload_json or '{}')
    except Exception:
        return False, '快照内容格式错误'
    settings = payload.get('settings') if isinstance(payload, dict) else None
    if not isinstance(settings, dict):
        return False, '快照缺少 settings'
    applied = 0
    skipped = 0
    for key, item in settings.items():
        if key not in get_importable_setting_keys():
            continue
        value = item.get('value') if isinstance(item, dict) else item
        if value is None or value == '***':
            continue
        text_value = str(value)
        if text_value.startswith(('fernet:', 'enc:')):
            set_config(key, text_value, commit=False)
            applied += 1
            continue
        ok, checked = validate_setting_value(key, text_value)
        if not ok:
            skipped += 1
            continue
        set_config(key, checked, commit=False)
        applied += 1
    db.session.commit()
    message = f'已回滚 {applied} 项'
    if skipped:
        message += f'，跳过 {skipped} 项非法值'
    return True, message


def log_operation(operation_type, target_type=None, target_id=None, target_name=None, message=None, success=True, commit=True, execution_id=None):
    if execution_id is None:
        execution_id = EXEC_MGR.current_execution()
    db.session.add(OperationLog(
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        execution_id=execution_id,
        target_name=target_name,
        message=message,
        success=success,
    ))
    if commit:
        db.session.commit()

    level = logging.INFO if success else logging.WARNING
    app.logger.log(
        level,
        'op=%s success=%s exec=%s target=%s:%s name=%s msg=%s',
        operation_type,
        success,
        execution_id if execution_id is not None else '-',
        target_type or '-',
        target_id if target_id is not None else '-',
        target_name or '-',
        message or '-',
    )


FORBIDDEN_PATH_ROOTS = (
    '/proc', '/sys', '/dev', '/etc', '/boot', '/root', '/run',
    '/bin', '/sbin', '/lib', '/lib64', '/usr', '/app',
    '/var/lib', '/var/log',
)


def validate_path(path):
    if not path:
        return False, '路径不能为空'
    if '\x00' in path or '~' in path or '\\' in path:
        return False, '非法路径字符'
    p = Path(path)
    if not p.is_absolute():
        return False, '路径必须为绝对路径'
    try:
        if str(p.resolve(strict=False)) == '/':
            return False, '不允许使用文件系统根目录'
    except Exception:
        pass
    for forbidden in FORBIDDEN_PATH_ROOTS:
        if _is_path_within_root(p, Path(forbidden)):
            return False, '不允许使用系统关键目录'

    allowed_roots = [r.strip() for r in (get_config('allowed_roots', '') or '').split(',') if r.strip()]
    if allowed_roots:
        allowed = False
        for root in allowed_roots:
            root_path = Path(root)
            if not root_path.is_absolute():
                continue
            if _is_path_within_root(p, root_path):
                allowed = True
                break
        if not allowed:
            return False, '路径不在允许范围内'
    return True, ''


def validate_host(host):
    parsed = urlparse(host or '')
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return False, '无效的主机地址格式'
    if parsed.username or parsed.password:
        return False, '主机地址不能包含账号密码'
    if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        return False, '主机地址只能填写协议、主机和端口'
    return True, ''


def validate_cron_expression(expr):
    parts = (expr or '').strip().split()
    if len(parts) != 5:
        return False

    def _ok(part, lo, hi):
        part = (part or '').strip()
        if part == '*':
            return True
        if part.startswith('*/'):
            step = part[2:]
            return step.isdigit() and int(step) > 0
        if part.isdigit():
            v = int(part)
            return lo <= v <= hi
        return False

    return (
        _ok(parts[0], 0, 59) and
        _ok(parts[1], 0, 23) and
        _ok(parts[2], 1, 31) and
        _ok(parts[3], 1, 12) and
        _ok(parts[4], 0, 7)
    )


def _is_path_within_root(path_obj: Path, root_obj: Path):
    try:
        path_resolved = path_obj.resolve(strict=False)
        root_resolved = root_obj.resolve(strict=False)
    except Exception:
        return False
    try:
        return os.path.commonpath([str(path_resolved), str(root_resolved)]) == str(root_resolved)
    except Exception:
        return False


def recycle_bin_root():
    configured = (get_config('delete_recycle_bin_dir', '/app/data/recycle') or '/app/data/recycle').strip()
    candidates = [Path(configured), Path(app.instance_path) / 'recycle']
    for candidate in candidates:
        if _dir_is_writable(candidate):
            return candidate
    return None


def move_to_recycle_bin(file_path):
    if not _config_bool('delete_recycle_bin_enabled', 'false'):
        return False, ''
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        return False, ''
    root = recycle_bin_root()
    if root is None:
        app.logger.warning('recycle bin is not writable; refusing to permanently delete %s', source)
        return False, ''
    try:
        stamp = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
        target_dir = root / stamp
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        counter = 1
        while target.exists():
            target = target_dir / f'{source.stem}.{counter}{source.suffix}'
            counter += 1
        shutil.move(str(source), str(target))
        try:
            db.session.add(RecycleItem(original_path=str(source), recycled_path=str(target), size=int(target.stat().st_size or 0)))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.warning('recycle metadata write failed: %s', exc)
        return True, str(target)
    except Exception as exc:
        app.logger.warning('recycle move failed path=%s err=%s', source, exc)
        return False, ''


def clean_recycle_bin():
    root = recycle_bin_root()
    if root is None:
        return 0
    retention = _clamp_int(get_config('delete_recycle_retention_days', '30'), 1, 3650)
    cutoff = datetime.now(UTC) - timedelta(days=retention)
    removed = 0
    try:
        for child in list(root.iterdir()):
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=UTC)
                if mtime >= cutoff:
                    continue
                prefix = str(child)
                RecycleItem.query.filter((RecycleItem.recycled_path == prefix) | (RecycleItem.recycled_path.like(prefix + '%'))).delete(synchronize_session=False)
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
            except Exception:
                continue
    except Exception as exc:
        app.logger.warning('recycle cleanup failed: %s', exc)
    return removed


def safe_unlink(path_obj):
    try:
        if path_obj.exists() and path_obj.is_file():
            path_obj.unlink()
    except Exception as exc:
        app.logger.warning('safe_unlink_failed path=%s err=%s', path_obj, exc)


def send_telegram_notification(notifier, message):
    if not notifier or not notifier.enabled or notifier.type != 'telegram':
        return False
    try:
        api_base = (get_config('tg_api_base', '') or '').strip().rstrip('/')
        if not api_base:
            api_base = 'https://api.telegram.org'

        proxies = _outbound_proxies(getattr(notifier, 'proxy_url', '') or '')

        url = f"{api_base}/bot{notifier.get_api_key()}/sendMessage"
        resp = requests.post(
            url,
            data={'chat_id': notifier.chat_id, 'text': message},
            timeout=app.config['REQUEST_TIMEOUT_SECONDS'],
            proxies=proxies,
        )
        resp.raise_for_status()
        payload = resp.json()
        return bool(payload.get('ok', True))
    except Exception as exc:
        err = str(exc)
        token = str(notifier.get_api_key() or '')
        if token:
            err = err.replace(token, '***')
        app.logger.error('Telegram notification failed: %s', redact_url_credentials(err))
        return False


def send_notification(notifier, message):
    if not notifier or not notifier.enabled:
        return False
    if notifier.type == 'telegram':
        return send_telegram_notification(notifier, message)
    if notifier.type == 'apprise':
        try:
            import apprise
        except Exception:
            app.logger.error('Apprise notifier unavailable')
            return False
        try:
            target = notifier.get_api_key()
            if not target:
                return False
            client = apprise.Apprise()
            if not client.add(target):
                app.logger.error('invalid Apprise URL for notifier id=%s', notifier.id)
                return False
            return bool(client.notify(body=message, title='Hardlink Manager'))
        except Exception as exc:
            app.logger.error('Apprise notification failed: %s', redact_url_credentials(str(exc)))
            return False
    return False


def _qb_session_ttl_seconds(downloader):
    raw = getattr(downloader, 'session_ttl_seconds', None)
    try:
        if raw is not None and str(raw).strip() != '':
            return max(60, min(86400, int(raw)))
    except Exception:
        pass
    return QB_SESSION_TTL_SECONDS


def _qb_cache_key(downloader):
    return (
        int(getattr(downloader, 'id', 0) or 0),
        str(getattr(downloader, 'host', '') or ''),
        str(getattr(downloader, 'port', '') or ''),
        str(getattr(downloader, 'username', '') or ''),
        str(getattr(downloader, 'encrypted_password', '') or ''),
        str(getattr(downloader, 'proxy_url', '') or ''),
        int(_qb_session_ttl_seconds(downloader)),
    )


def _qb_new_session(downloader, login=True):
    session_obj = requests.Session()
    proxies = _outbound_proxies(getattr(downloader, 'proxy_url', '') or '')
    if proxies:
        session_obj.proxies.update(proxies)
    if login and downloader.username and downloader.encrypted_password:
        login_url = f"{downloader.host}:{downloader.port}/api/v2/auth/login"
        resp = session_obj.post(
            login_url,
            data={'username': downloader.username, 'password': downloader.get_password()},
            timeout=app.config['REQUEST_TIMEOUT_SECONDS'],
        )
        resp.raise_for_status()
        body = (resp.text or '').strip()
        if body and not body.lower().startswith('ok'):
            raise RuntimeError('qBittorrent login failed')
    return session_obj


def invalidate_qb_client(downloader):
    key = _qb_cache_key(downloader)
    with QB_SESSION_LOCK:
        QB_SESSION_CACHE.pop(key, None)


def get_qb_client(downloader):
    key = _qb_cache_key(downloader)
    thread_key = get_ident()
    now = datetime.now(UTC)
    with QB_SESSION_LOCK:
        bucket = QB_SESSION_CACHE.setdefault(key, {})
        for stale_key in [k for k, v in bucket.items() if not v.get('expires_at') or v['expires_at'] <= now]:
            bucket.pop(stale_key, None)
        cached = bucket.get(thread_key)
        if cached and cached.get('expires_at') and cached['expires_at'] > now:
            return cached['session']
    session_obj = _qb_new_session(downloader, login=True)
    with QB_SESSION_LOCK:
        bucket = QB_SESSION_CACHE.setdefault(key, {})
        bucket[thread_key] = {
            'session': session_obj,
            'expires_at': now + timedelta(seconds=_qb_session_ttl_seconds(downloader)),
        }
    return session_obj


def qb_request(downloader, method, api_path, params=None, data=None, retry_auth=True):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []

    client = get_qb_client(downloader)
    url = f"{downloader.host}:{downloader.port}{api_path}"
    resp = client.request(method, url, params=params, data=data, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])

    if resp.status_code in {401, 403} and retry_auth:
        invalidate_qb_client(downloader)
        client = get_qb_client(downloader)
        resp = client.request(method, url, params=params, data=data, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])

    resp.raise_for_status()
    return resp


def list_torrents(downloader, use_category=True):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []
    try:
        category = (getattr(downloader, 'category', '') or '').strip() if use_category else ''
        params = {'category': category} if category else None
        resp = qb_request(downloader, 'GET', '/api/v2/torrents/info', params=params)
        return resp.json() if resp is not None else None
    except Exception as exc:
        app.logger.error('list torrents failed: %s', redact_url_credentials(str(exc)))
        return None


def find_cross_seed_torrents(downloader, torrent_hash):
    target_hash = str(torrent_hash or '').strip().lower()
    if not target_hash:
        return []
    torrents = list_torrents(downloader, use_category=False)
    if not torrents:
        return []
    target = None
    for torrent in torrents:
        if str(torrent.get('hash') or '').strip().lower() == target_hash:
            target = torrent
            break
    if not target:
        return []
    target_content = str(target.get('content_path') or '').strip().lower()
    target_save = str(target.get('save_path') or '').strip().lower()
    target_name = str(target.get('name') or '').strip().lower()
    related = []
    for torrent in torrents:
        other_hash = str(torrent.get('hash') or '').strip().lower()
        if not other_hash or other_hash == target_hash:
            continue
        content_path = str(torrent.get('content_path') or '').strip().lower()
        save_path = str(torrent.get('save_path') or '').strip().lower()
        name = str(torrent.get('name') or '').strip().lower()
        if target_content and content_path and content_path == target_content:
            related.append({'hash': other_hash, 'name': torrent.get('name') or other_hash})
        elif target_save and target_name and save_path == target_save and name == target_name:
            related.append({'hash': other_hash, 'name': torrent.get('name') or other_hash})
    return related


def list_torrent_files(downloader, torrent_hash):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []
    try:
        resp = qb_request(downloader, 'GET', '/api/v2/torrents/files', params={'hash': torrent_hash})
        payload = resp.json() if resp is not None else None
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        app.logger.error('list torrent files failed: %s', redact_url_credentials(str(exc)))
        return None


def delete_torrent(downloader, torrent_hash):
    if not downloader or not downloader.enabled or downloader.type != 'qbittorrent':
        return False
    try:
        delete_files = get_config('delete_files_with_torrent', 'false') == 'true'
        qb_request(
            downloader,
            'POST',
            '/api/v2/torrents/delete',
            data={'hashes': torrent_hash, 'deleteFiles': 'true' if delete_files else 'false'},
        )
        return True
    except Exception as exc:
        app.logger.error('delete torrent failed: %s', redact_url_credentials(str(exc)))
        return False


def scan_hardlink_task(task_id, should_stop=None):
    task = db.session.get(HardlinkTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'
    if not str(task.source_dir or '').strip() or not str(task.dest_dir or '').strip():
        return False, '源目录或目标目录配置为空'
    for label, path_value in (('源目录', task.source_dir), ('目标目录', task.dest_dir)):
        ok, path_msg = validate_path(path_value)
        if not ok:
            return False, f'{label}无效: {path_msg}'

    source = Path(task.source_dir)
    if not source.exists() or not source.is_dir():
        return False, '源目录不存在'
    dest_root_check = Path(task.dest_dir)
    if _is_path_within_root(source, dest_root_check) or _is_path_within_root(dest_root_check, source):
        return False, '源目录与目标目录不能相同或互相包含'

    created = 0
    scanned = 0
    failed = 0
    cache_hit = 0
    skipped_ext = 0
    skipped_blacklist = 0
    skipped_exclude_dir = 0
    skipped_unstable = 0
    skipped_existing = 0

    for file_path in source.rglob('*'):
        if should_stop and should_stop():
            db.session.commit()
            summary = f'扫描 {scanned}，成功 {created}，失败 {failed}；缓存命中 {cache_hit}，扩展名不匹配 {skipped_ext}，黑名单 {skipped_blacklist}，排除目录 {skipped_exclude_dir}，写入中跳过 {skipped_unstable}，已存在跳过 {skipped_existing}；已手动停止'
            log_operation('hardlink_scan_stopped', 'HardlinkTask', task.id, task.name, summary, False)
            return False, summary
        if not file_path.is_file() or file_path.is_symlink():
            continue
        scanned += 1
        try:
            ok, msg = svc_create_hardlink_for_file(task, file_path, HardlinkCache, FileLinkMap, db, safe_unlink)
            if ok:
                if '已存在' in str(msg or ''):
                    skipped_existing += 1
                else:
                    created += 1
                db.session.commit()
            else:
                text = str(msg or '')
                if '命中缓存' in text:
                    cache_hit += 1
                elif '扩展名不匹配' in text:
                    skipped_ext += 1
                elif '黑名单' in text:
                    skipped_blacklist += 1
                elif '排除目录' in text:
                    skipped_exclude_dir += 1
                elif '写入中' in text:
                    skipped_unstable += 1
                elif '已存在' in text or '同名文件' in text:
                    skipped_existing += 1
        except Exception as exc:
            failed += 1
            db.session.rollback()
            log_operation('hardlink_failed', 'HardlinkTask', task.id, task.name, f'{file_path}: {exc}', False)

    db.session.commit()
    summary = (
        f'扫描 {scanned}，成功 {created}，失败 {failed}；'
        f'缓存命中 {cache_hit}，扩展名不匹配 {skipped_ext}，黑名单 {skipped_blacklist}，'
        f'排除目录 {skipped_exclude_dir}，写入中跳过 {skipped_unstable}，已存在跳过 {skipped_existing}'
    )
    log_operation('hardlink_scan', 'HardlinkTask', task.id, task.name, summary)
    return True, summary


def try_match_torrent_by_mapping_or_name(deleted_path: Path, downloader: Downloader):
    mapping = FileLinkMap.query.filter((FileLinkMap.source_path == str(deleted_path)) | (FileLinkMap.dest_path == str(deleted_path))).first()
    if mapping and mapping.torrent_hash:
        return mapping.torrent_hash, 'mapping_hash'

    torrents = list_torrents(downloader)
    if torrents is None:
        return None, 'downloader_unavailable'

    p_name = deleted_path.name.lower()
    p_parent = str(deleted_path.parent).lower()
    for torrent in torrents:
        t_name = str(torrent.get('name', '')).lower()
        save_path = str(torrent.get('save_path', '')).lower()
        content_path = str(torrent.get('content_path', '')).lower()
        if p_name and p_name in t_name:
            return torrent.get('hash'), 'name_match'
        if p_parent and (p_parent in save_path or p_parent in content_path):
            return torrent.get('hash'), 'path_match'
    return None, 'no_match'


def _config_bool(key, default='false'):
    return str(get_config(key, default)).lower() == 'true'


def _config_int(key, default=0):
    try:
        return int(get_config(key, str(default)) or str(default))
    except Exception:
        return int(default)


def _clamp_int(value, min_value, max_value):
    try:
        iv = int(value)
    except Exception:
        iv = int(min_value)
    return max(int(min_value), min(int(max_value), iv))


def _load_backfill_path_mappings():
    raw = (get_config('backfill_path_mappings', '') or '').strip()
    mappings = []
    if not raw:
        return mappings
    for chunk in raw.split(';'):
        part = chunk.strip()
        if not part or '=>' not in part:
            continue
        left, right = part.split('=>', 1)
        src = str(left or '').strip().rstrip('/\\')
        dst = str(right or '').strip().rstrip('/\\')
        if src and dst:
            mappings.append((src.lower(), dst))
    mappings.sort(key=lambda x: len(x[0]), reverse=True)
    return mappings


def _normalize_for_backfill(value, mappings=None):
    text = str(value or '').strip()
    if not text:
        return ''
    text = text.replace('\\\\', '/').replace('\\', '/')
    lower_text = text.lower()
    for src_low, dst in (mappings or []):
        if lower_text == src_low or lower_text.startswith(src_low + '/'):
            suffix = text[len(src_low):]
            merged = (dst.rstrip('/') + suffix).replace('\\\\', '/').replace('\\', '/')
            return merged.lower()
    return text.lower()


def _as_aware_utc(dt_obj):
    if not dt_obj:
        return None
    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=UTC)
    return dt_obj.astimezone(UTC)


def _effective_source_type_for_delete(row, deleted_path: str, downloader):
    raw_source_type = (row.source_type or '').strip().lower()
    if raw_source_type == 'downloader':
        return 'downloader', None, None

    has_hash = bool((row.torrent_hash or '').strip())
    has_downloader = bool(row.downloader_id)
    if has_hash and has_downloader:
        return 'downloader', None, None

    now = datetime.now(UTC)
    created_at = _as_aware_utc(getattr(row, 'created_at', None)) or now
    age_seconds = max(0, int((now - created_at).total_seconds()))

    pending_enabled = _config_bool('pending_source_guard_enabled', 'true')
    pending_window = max(0, _config_int('pending_source_guard_seconds', 900))
    treat_unknown_as_pending = raw_source_type in {'', 'pending'}
    treat_recent_manual_as_pending = raw_source_type == 'manual' and age_seconds <= pending_window
    needs_pending_guard = pending_enabled and (treat_unknown_as_pending or treat_recent_manual_as_pending)

    if not needs_pending_guard:
        return 'manual', None, None

    if downloader is None:
        return 'pending', None, 'pending_no_downloader'

    torrent_hash, match_by = try_match_torrent_by_mapping_or_name(Path(deleted_path), downloader)
    if torrent_hash and match_by in {'mapping_hash'}:
        row.torrent_hash = torrent_hash
        row.downloader_id = downloader.id
        row.source_type = 'downloader'
        row.backfill_fail_count = 0
        row.backfill_last_attempt_at = None
        return 'downloader', torrent_hash, match_by

    # For pending rows we avoid using weak name/path match as auto downloader decision.
    return 'pending', torrent_hash, match_by or 'no_match'


def create_pending_delete_action(task, row, deleted_path, torrent_hash, match_by, reason):
    exists = DeletePendingAction.query.filter_by(
        task_id=task.id,
        file_map_id=row.id,
        torrent_hash=torrent_hash,
        status='pending',
    ).first()
    if exists:
        return exists
    pending = DeletePendingAction(
        task_id=task.id,
        file_map_id=row.id,
        deleted_path=deleted_path,
        torrent_hash=torrent_hash,
        match_by=match_by,
        status='pending',
        reason=reason,
    )
    db.session.add(pending)
    return pending


def scan_delete_for_hardlink_task(task_id, should_stop=None):
    task = db.session.get(HardlinkTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    if (not task.monitor_source_delete) and (not task.monitor_dest_delete):
        return True, '删除联动未启用（源删/目标删均关闭）'

    if not str(task.source_dir or '').strip() or not str(task.dest_dir or '').strip():
        msg = '删除联动已中止：源目录或目标目录配置为空'
        log_operation('delete_scan_aborted_root_missing', 'HardlinkTask', task.id, task.name, msg, False)
        return False, msg
    for label, path_value in (('源目录', task.source_dir), ('目标目录', task.dest_dir)):
        ok, path_msg = validate_path(path_value)
        if not ok:
            msg = f'删除联动已中止：{label}无效: {path_msg}'
            log_operation('delete_scan_aborted_root_missing', 'HardlinkTask', task.id, task.name, msg, False)
            return False, msg
    rows = FileLinkMap.query.filter(FileLinkMap.task_id == task.id, FileLinkMap.deleted_at.is_(None)).all()
    source_root = Path(task.source_dir)
    dest_root = Path(task.dest_dir)
    if not source_root.exists() or not source_root.is_dir():
        msg = f'删除联动已中止：源目录不存在或不可读: {task.source_dir}'
        log_operation('delete_scan_aborted_root_missing', 'HardlinkTask', task.id, task.name, msg, False)
        return False, msg
    if _is_path_within_root(source_root, dest_root) or _is_path_within_root(dest_root, source_root):
        msg = '删除联动已中止：源目录与目标目录不能相同或互相包含'
        log_operation('delete_scan_aborted_root_missing', 'HardlinkTask', task.id, task.name, msg, False)
        return False, msg
    if not dest_root.exists() or not dest_root.is_dir():
        if rows:
            msg = f'删除联动已中止：目标目录不存在或不可读: {task.dest_dir}'
            log_operation('delete_scan_aborted_root_missing', 'HardlinkTask', task.id, task.name, msg, False)
            return False, msg
        return True, '目标目录尚不存在，已跳过删除联动'

    downloader = db.session.get(Downloader, task.delete_downloader_id) if task.delete_downloader_id else None
    notifier = db.session.get(Notifier, task.delete_notifier_id) if task.delete_notifier_id else None

    class _ProxyTask:
        pass

    legacy_task = DeleteMonitorTask.query.filter_by(name=f'[兼容]硬链接任务#{task.id}').first()
    if not legacy_task:
        legacy_task = DeleteMonitorTask(
            name=f'[兼容]硬链接任务#{task.id}',
            directory=task.source_dir,
            downloader_id=task.delete_downloader_id,
            notifier_id=task.delete_notifier_id,
            events='unlink,unlinkDir',
            cooldown_seconds=int(task.delete_cooldown_seconds or 120),
            max_deletes_per_run=int(task.delete_max_deletes_per_run or 20),
            dry_run=bool(task.delete_dry_run),
            notify_on_delete=bool(task.delete_notify_on_delete),
            notify_on_risky_delete=bool(task.delete_notify_on_risky_delete),
            enabled=False,
        )
        db.session.add(legacy_task)
        db.session.flush()

    now = datetime.now(UTC)
    cooldown = int(task.delete_cooldown_seconds or 120)
    max_del = int(task.delete_max_deletes_per_run or 20)

    candidates = []
    for row in rows:
        if should_stop and should_stop():
            break
        src = str(row.source_path or '')
        dst = str(row.dest_path or '')
        src_exists = Path(src).exists() if src else False
        dst_exists = Path(dst).exists() if dst else False

        deleted_side = None
        deleted_path = ''
        if task.monitor_source_delete and src and not src_exists:
            deleted_side = 'source'
            deleted_path = src
        elif task.monitor_dest_delete and dst and not dst_exists:
            deleted_side = 'dest'
            deleted_path = dst

        if not deleted_side:
            continue

        last_seen = row.last_seen_at
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        if last_seen and (now - last_seen).total_seconds() < cooldown:
            continue
        candidates.append((row, deleted_side, deleted_path))

    total_candidates = len(candidates)
    anomaly_enabled = _config_bool('delete_anomaly_guard_enabled', 'true')
    anomaly_min = _config_int('delete_anomaly_min_candidates', 50)
    anomaly_ratio = _config_int('delete_anomaly_max_ratio', 50)
    tracked = len(rows)
    if (
        anomaly_enabled
        and tracked > 0
        and total_candidates >= max(1, anomaly_min)
        and total_candidates * 100 > tracked * max(1, anomaly_ratio)
    ):
        msg = f'删除联动已熔断：本轮命中 {total_candidates}/{tracked}（阈值 {anomaly_ratio}%），请检查挂载点与路径配置'
        log_operation('delete_scan_anomaly_aborted', 'HardlinkTask', task.id, task.name, msg, False)
        return False, msg
    if total_candidates > max_del:
        log_operation('delete_guard_truncated', 'HardlinkTask', task.id, task.name, f'本轮命中 {total_candidates}，按阈值分批执行前 {max_del} 条，其余待下轮处理', True)
        candidates = candidates[:max_del]

    deleted_torrents = 0
    pending_total = 0
    hit_total = 0
    pending_source_hits = 0
    pending_source_samples = []
    pending_log_mode = (get_config('pending_source_log_mode', 'aggregate') or 'aggregate').strip().lower()
    if pending_log_mode not in {'aggregate', 'detail'}:
        pending_log_mode = 'aggregate'

    def _create_pending_action(_proxy_task, row, deleted_path, torrent_hash, match_by, reason):
        exists = DeletePendingAction.query.filter_by(
            task_id=legacy_task.id,
            file_map_id=row.id,
            torrent_hash=torrent_hash,
            status='pending',
        ).first()
        if exists:
            return exists
        pending = DeletePendingAction(
            task_id=legacy_task.id,
            file_map_id=row.id,
            deleted_path=deleted_path,
            torrent_hash=torrent_hash,
            match_by=match_by,
            status='pending',
            reason=reason,
        )
        db.session.add(pending)
        return pending

    for row, deleted_side, deleted_path in candidates:
        if should_stop and should_stop():
            log_operation('delete_scan_stopped', 'HardlinkTask', task.id, task.name, '收到停止指令，已中止本轮删除联动', False)
            break

        # 不能在这里提前写入时间戳，否则后续会被冷却窗口误判为“刚处理过”
        hit_total += 1

        source_type, pending_hash, pending_match_by = _effective_source_type_for_delete(row, deleted_path, downloader)
        if source_type == 'pending':
            row.last_seen_at = now
            pending_source_hits += 1
            if pending_log_mode == 'detail':
                log_operation(
                    'delete_pending_source',
                    'FileLinkMap',
                    row.id,
                    task.name,
                    f'来源待判定，暂不执行删种: path={deleted_path}, match={pending_match_by or "no_match"}, hash={pending_hash or "-"}',
                    False,
                )
            elif len(pending_source_samples) < 5:
                pending_source_samples.append(
                    f'id={row.id},match={pending_match_by or "no_match"},hash={pending_hash or "-"}'
                )

        proxy = _ProxyTask()
        proxy.id = task.id
        proxy.name = f'{task.name}:删除联动'
        proxy.directory = task.source_dir if deleted_side == 'source' else task.dest_dir
        proxy.downloader = downloader
        proxy.notifier = notifier
        proxy.cooldown_seconds = cooldown
        proxy.max_deletes_per_run = max_del
        proxy.dry_run = bool(task.delete_dry_run)
        proxy.notify_on_delete = bool(task.delete_notify_on_delete)
        proxy.notify_on_risky_delete = bool(task.delete_notify_on_risky_delete)
        proxy.prune_roots = [task.source_dir, task.dest_dir]

        if source_type == 'pending':
            db.session.commit()
            continue

        ok, deleted_cnt, _hits, pending_cnt = scan_delete_rows(
            proxy,
            [row],
            try_match_torrent_by_mapping_or_name,
            delete_torrent,
            log_operation,
            get_config,
            send_notification,
            _create_pending_action,
            should_stop=should_stop,
            find_cross_seed=find_cross_seed_torrents,
            recycle_file=move_to_recycle_bin,
        )
        if not ok:
            db.session.commit()
            return False, f'删除联动已阻断：检测删除 {hit_total}，删种 {deleted_torrents}，待确认 {pending_total}'
        deleted_torrents += int(deleted_cnt or 0)
        pending_total += int(pending_cnt or 0)
        db.session.commit()

    if pending_source_hits > 0 and pending_log_mode == 'aggregate':
        sample_text = '; '.join(pending_source_samples) if pending_source_samples else '-'
        log_operation(
            'delete_pending_source',
            'HardlinkTask',
            task.id,
            task.name,
            f'本轮待判定来源 {pending_source_hits} 条，暂不删种；样例: {sample_text}',
            False,
        )

    db.session.commit()
    return True, f'删除联动完成：本轮处理 {hit_total}/{total_candidates}，联动删种 {deleted_torrents}，待确认 {pending_total}，待判定 {pending_source_hits}'




def scan_delete_task(task_id, should_stop=None):
    task = db.session.get(DeleteMonitorTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    if not str(task.directory or '').strip():
        return False, '删除任务已中止：监控目录配置为空'
    monitor_path = Path(task.directory)
    if not monitor_path.exists() or not monitor_path.is_dir():
        msg = f'删除任务已中止：监控目录不存在或不可读: {task.directory}'
        log_operation('delete_scan_aborted_root_missing', 'DeleteMonitorTask', task.id, task.name, msg, False)
        return False, msg

    monitor_root = str(monitor_path.resolve(strict=False))
    root_like = f'{monitor_root.rstrip("/")}/%'
    rows = FileLinkMap.query.filter(
        FileLinkMap.deleted_at.is_(None),
        (
            FileLinkMap.source_path == monitor_root
        ) | (
            FileLinkMap.source_path.like(root_like)
        ) | (
            FileLinkMap.dest_path == monitor_root
        ) | (
            FileLinkMap.dest_path.like(root_like)
        )
    ).all()

    anomaly_enabled = _config_bool('delete_anomaly_guard_enabled', 'true')
    anomaly_min = _config_int('delete_anomaly_min_candidates', 50)
    anomaly_ratio = _config_int('delete_anomaly_max_ratio', 50)
    missing = 0
    for row in rows:
        if (row.source_path and not Path(row.source_path).exists()) or (row.dest_path and not Path(row.dest_path).exists()):
            missing += 1
    if (
        anomaly_enabled
        and rows
        and missing >= max(1, anomaly_min)
        and missing * 100 > len(rows) * max(1, anomaly_ratio)
    ):
        msg = f'删除任务已熔断：本轮缺失 {missing}/{len(rows)}（阈值 {anomaly_ratio}%），请检查挂载点与路径配置'
        log_operation('delete_scan_anomaly_aborted', 'DeleteMonitorTask', task.id, task.name, msg, False)
        return False, msg

    ok, deleted_torrents, hit_count, pending_count = scan_delete_rows(
        task,
        rows,
        try_match_torrent_by_mapping_or_name,
        delete_torrent,
        log_operation,
        get_config,
        send_notification,
        create_pending_delete_action,
        should_stop=should_stop,
        find_cross_seed=find_cross_seed_torrents,
        recycle_file=move_to_recycle_bin,
    )
    db.session.commit()

    if not ok:
        return False, '删除任务执行失败'
    return True, f'删除联动完成：本轮处理 {min(hit_count, task.max_deletes_per_run)}/{hit_count} 条，联动删除种子 {deleted_torrents} 条，待确认 {pending_count} 条'
def scan_backfill_task(downloader_id=None, limit=500, should_stop=None):
    configured_limit = _clamp_int(get_config('backfill_batch_limit', str(limit)), 50, 3000)
    limit = _clamp_int(limit or configured_limit, 50, 3000)
    max_failures = _clamp_int(get_config('backfill_max_failures', '2'), 0, 10)
    max_candidates = _clamp_int(get_config('backfill_max_candidates', '120'), 20, 500)
    file_fetch_workers = _clamp_int(get_config('backfill_file_fetch_workers', '4'), 1, 16)

    query = FileLinkMap.query.filter(FileLinkMap.torrent_hash.is_(None), FileLinkMap.deleted_at.is_(None), db.func.coalesce(FileLinkMap.backfill_fail_count, 0) <= max_failures)
    if downloader_id:
        query = query.filter(FileLinkMap.downloader_id == downloader_id)
    rows = query.order_by(FileLinkMap.created_at.asc()).limit(limit).all()

    def resolve_downloader(did):
        if did:
            return db.session.get(Downloader, did)
        return Downloader.query.filter_by(enabled=True, type='qbittorrent').first()

    started_at = datetime.now(UTC)
    mappings = _load_backfill_path_mappings()
    matched, conflicts, skipped = scan_backfill_rows(
        rows,
        resolve_downloader,
        list_torrents,
        list_torrent_files,
        log_operation,
        should_stop=should_stop,
        max_failures=max_failures,
        max_candidates=max_candidates,
        file_fetch_workers=file_fetch_workers,
        normalize_path=lambda v: _normalize_for_backfill(v, mappings),
    )
    db.session.commit()

    duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    processed = matched + conflicts + skipped
    next_limit = limit
    if processed >= int(limit * 0.9) and duration_ms < 15000:
        next_limit = min(3000, limit + 100)
    elif duration_ms > 45000:
        next_limit = max(50, limit - 100)

    if next_limit != configured_limit:
        set_config('backfill_batch_limit', str(next_limit), '映射回填批次大小（自动调优）')
        db.session.commit()

    return True, f'回填成功 {matched}，冲突 {conflicts}，跳过 {skipped}（耗时 {duration_ms}ms，批次 {limit}，下次批次 {next_limit}）'
def is_stop_requested(run_key):
    return EXEC_MGR.is_stop_requested(run_key)


def request_stop_by_execution(execution_id):
    return EXEC_MGR.request_stop_by_execution(execution_id)


def get_running_executions_snapshot():
    return EXEC_MGR.get_running_executions_snapshot()


def is_run_key_active(run_key):
    return EXEC_MGR.is_run_key_active(run_key)


def get_run_meta(run_key):
    return EXEC_MGR.get_run_meta(run_key)


def execute_with_guard(run_key, job_name, job_type, runner, source='manual', target_id=None):
    return EXEC_MGR.execute_with_guard(
        run_key,
        job_name,
        job_type,
        runner,
        log_operation=log_operation,
        source=source,
        target_id=target_id,
    )


def run_backup_task():
    backup_dir = (get_config('backup_dir', '/app/data/backups') or '/app/data/backups').strip()
    if not _dir_is_writable(backup_dir):
        backup_dir = str(Path(app.instance_path) / 'backups')
    keep_last = _clamp_int(get_config('backup_keep_last', '7'), 1, 3650)
    db_file = Path(app.instance_path) / 'hardlink_manager.db'
    ok, msg, backup_path = run_sqlite_backup(str(db_file), backup_dir, keep_last=max(1, keep_last))
    log_operation('db_backup', 'System', None, '数据库备份', f"{msg} | {backup_path or '-'}", ok)
    return ok, msg


def run_hardlink_once(task_id):
    task = db.session.get(HardlinkTask, task_id)
    if not task:
        return False, '任务不存在'

    def _runner(stop_checker=None):
        del_ok, del_msg = scan_delete_for_hardlink_task(task_id, should_stop=stop_checker)
        if not del_ok:
            return False, f'删除联动阶段失败：{del_msg}'
        hl_ok, hl_msg = scan_hardlink_task(task_id, should_stop=stop_checker)
        if not hl_ok:
            return False, f'{del_msg}；硬链接阶段失败：{hl_msg}'
        return True, f'{del_msg}；{hl_msg}'

    return execute_with_guard(f'hardlink:{task_id}', task.name, 'batch_hardlink', _runner, source='manual', target_id=task_id)


def run_delete_once(task_id):
    task = db.session.get(DeleteMonitorTask, task_id)
    if not task:
        return False, '任务不存在'

    def _runner(stop_checker=None):
        return scan_delete_task(task_id, should_stop=stop_checker)

    return execute_with_guard(f'delete:{task_id}', task.name, 'delete_scan', _runner, source='manual', target_id=task_id)


def run_backfill_once(downloader_id=None):
    run_key = f'backfill:{downloader_id or 0}'

    def _runner(stop_checker=None):
        return scan_backfill_task(downloader_id, should_stop=stop_checker)

    return execute_with_guard(run_key, '映射回填', 'backfill_mapping', _runner, source='manual', target_id=downloader_id)


def run_backfill_for_map_id(map_id, deep_retry=False):
    row = db.session.get(FileLinkMap, map_id)
    if not row:
        return False, '映射记录不存在'
    if row.torrent_hash:
        return True, '该映射已关联种子，无需重试'

    if not row.downloader_id:
        d = Downloader.query.filter_by(enabled=True, type='qbittorrent').order_by(Downloader.id.asc()).first()
        if d:
            row.downloader_id = d.id
            db.session.commit()

    def resolve_downloader(did):
        if did:
            return db.session.get(Downloader, did)
        return Downloader.query.filter_by(enabled=True, type='qbittorrent').first()

    run_key = f'backfill:single:{map_id}'

    def _runner(stop_checker=None):
        target = db.session.get(FileLinkMap, map_id)
        if not target:
            return False, '映射记录不存在'
        if target.torrent_hash:
            return True, '该映射已关联种子，无需重试'
        mappings = _load_backfill_path_mappings()
        matched, conflicts, skipped = scan_backfill_rows(
            [target],
            resolve_downloader,
            list_torrents,
            list_torrent_files,
            log_operation,
            should_stop=stop_checker,
            max_failures=2,
            allow_global_fallback=bool(deep_retry),
            max_candidates=120 if deep_retry else 40,
            normalize_path=lambda v: _normalize_for_backfill(v, mappings),
        )
        db.session.commit()
        db.session.refresh(target)
        if target.torrent_hash:
            return True, f'单条回填成功：hash={target.torrent_hash}（匹配统计：成功{matched}/冲突{conflicts}/跳过{skipped}）'
        if conflicts:
            return False, f'单条回填未成功：检测到候选冲突（成功{matched}/冲突{conflicts}/跳过{skipped}）'
        if skipped:
            return False, f'单条回填未成功：未命中可用种子或已达到跳过阈值（成功{matched}/冲突{conflicts}/跳过{skipped}）'
        return False, f'单条回填未成功：未匹配到候选种子（成功{matched}/冲突{conflicts}/跳过{skipped}）'

    mode_text = '深度重试' if deep_retry else '快速重试'
    return execute_with_guard(run_key, f'单条映射回填（{mode_text}）', 'backfill_mapping_single', _runner, source='manual', target_id=map_id)


JOB_TYPE_META = {
    'hardlink': ('硬链接任务', 'hardlink'),
    'delete': ('删除联动', 'delete'),
    'backfill': ('映射回填', 'backfill'),
    'backup': ('数据库备份', 'backup'),
}


def enqueue_job(job_type, target_id=None, source='manual', payload=None, job_name=None, priority=0, max_retries=0):
    job_type = str(job_type or '').strip().lower()
    if job_type not in JOB_TYPE_META:
        return None, False, '不支持的任务类型'
    label, run_prefix = JOB_TYPE_META[job_type]
    try:
        target = int(target_id) if target_id not in (None, '') else None
    except Exception:
        return None, False, 'target_id 必须为整数'
    if job_type in {'hardlink', 'delete'} and not target:
        return None, False, '该任务类型必须提供 target_id'
    run_key = f'{run_prefix}:{target or 0}'
    existing = JobQueue.query.filter(
        JobQueue.run_key == run_key,
        JobQueue.status.in_(('queued', 'running')),
    ).order_by(JobQueue.id.asc()).first()
    if existing:
        return existing, False, '同任务已在队列或执行中'
    row = JobQueue(
        run_key=run_key,
        job_name=(job_name or label).strip() or label,
        job_type=job_type,
        target_id=target,
        source=(source or 'manual').strip() or 'manual',
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        status='queued',
        priority=_clamp_int(priority, -100, 100),
        max_retries=_clamp_int(max_retries, 0, 10),
    )
    db.session.add(row)
    db.session.commit()
    return row, True, '已加入队列'


def claim_next_job(worker_id):
    row = JobQueue.query.filter_by(status='queued').order_by(JobQueue.priority.desc(), JobQueue.id.asc()).first()
    if not row:
        return None
    now = datetime.now(UTC)
    updated = JobQueue.query.filter_by(id=row.id, status='queued').update({
        'status': 'running',
        'worker_id': str(worker_id),
        'lease_owner': str(worker_id),
        'lease_expires_at': now + timedelta(minutes=30),
        'started_at': now,
    }, synchronize_session=False)
    db.session.commit()
    if not updated:
        return None
    return db.session.get(JobQueue, row.id)


def finish_job(row, ok, message):
    if not row:
        return
    message = str(message or '')[:1000]
    if not ok and int(row.retry_count or 0) < int(row.max_retries or 0):
        row.retry_count = int(row.retry_count or 0) + 1
        row.status = 'queued'
        row.message = f'失败，第 {row.retry_count} 次重试排队: {message}'[:1000]
        row.started_at = None
        row.finished_at = None
        row.worker_id = None
        row.lease_owner = None
        row.lease_expires_at = None
    else:
        row.status = 'success' if ok else 'failed'
        row.message = message
        row.finished_at = datetime.now(UTC)
        row.lease_owner = None
        row.lease_expires_at = None
    db.session.commit()


def request_stop_queued_job(job_id):
    row = db.session.get(JobQueue, job_id)
    if not row or row.status not in {'queued', 'running'}:
        return False, '任务不存在或已结束'
    row.stop_requested = True
    db.session.commit()
    return True, '已请求停止'


def execute_queued_job(row):
    if row.job_type == 'hardlink':
        return run_hardlink_once(row.target_id)
    if row.job_type == 'delete':
        return run_delete_once(row.target_id)
    if row.job_type == 'backfill':
        return run_backfill_once(row.target_id)
    if row.job_type == 'backup':
        return run_backup_once()
    return False, f'未知任务类型: {row.job_type}'


def run_backup_once():
    def _runner(stop_checker=None):
        return run_backup_task()

    return execute_with_guard('backup:manual', '数据库备份', 'db_backup', _runner, source='manual')


def run_cron_job(job_id):
    with app.app_context():
        job = db.session.get(CronJob, job_id)
        if not job or not job.enabled:
            return

        related_key = None
        if job.task_type in {'batch_hardlink', 'hardlink_scan'} and job.target_id:
            related_key = f'hardlink:{job.target_id}'
        elif job.task_type in {'delete_scan', 'delete_monitor_scan'} and job.target_id:
            related_key = f'delete:{job.target_id}'
        elif job.task_type in {'backfill_mapping', 'backfill_torrent_mapping'}:
            related_key = f'backfill:{job.target_id or 0}'

        if related_key and is_run_key_active(related_key):
            meta = get_run_meta(related_key) or {}
            started = meta.get('started_at')
            elapsed = int((datetime.now(UTC) - started).total_seconds()) if started else 0
            msg = f'上次同任务仍在执行（{elapsed} 秒），已跳过本次定时执行'
            log_operation('cron_skipped_already_running', 'CronJob', job.id, job.name, msg, False)
            return

        def _runner(stop_checker=None):
            if job.task_type in {'batch_hardlink', 'hardlink_scan'}:
                del_ok, del_msg = scan_delete_for_hardlink_task(job.target_id, should_stop=stop_checker)
                if not del_ok:
                    msg = f'删除联动阶段失败: {del_msg}'
                    log_operation('cron_executed', 'CronJob', job.id, job.name, f'hardlink_scan: {msg}', False)
                    return False, msg
                ok, msg2 = scan_hardlink_task(job.target_id, should_stop=stop_checker)
                msg = f'{del_msg}; {msg2}'
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'hardlink_scan: {msg}', ok)
                return ok, msg
            if job.task_type in {'delete_scan', 'delete_monitor_scan'}:
                ok, msg = scan_delete_task(job.target_id, should_stop=stop_checker)
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'delete_scan: {msg}', ok)
                return ok, msg
            if job.task_type in {'backfill_mapping', 'backfill_torrent_mapping'}:
                ok, msg = scan_backfill_task(job.target_id, should_stop=stop_checker)
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'backfill: {msg}', ok)
                return ok, msg
            if job.task_type == 'clean_logs':
                if get_config('auto_clean_logs', 'true') != 'true':
                    log_operation('cron_skipped', 'CronJob', job.id, job.name, 'auto_clean_logs=off，已跳过日志清理')
                    return True, '已关闭自动清理日志，跳过执行'
                retention = _clamp_int(get_config('log_retention_days', '30'), 1, 3650)
                cutoff = datetime.now(UTC) - timedelta(days=retention)
                op_count = OperationLog.query.filter(OperationLog.created_at < cutoff).delete(synchronize_session=False)
                exec_count = JobExecutionLog.query.filter(JobExecutionLog.started_at < cutoff).delete(synchronize_session=False)
                pending_count = DeletePendingAction.query.filter(
                    DeletePendingAction.status != 'pending',
                    DeletePendingAction.created_at < cutoff,
                ).delete(synchronize_session=False)
                old_map_ids = [row[0] for row in db.session.query(FileLinkMap.id).filter(
                    FileLinkMap.deleted_at.is_not(None),
                    FileLinkMap.deleted_at < cutoff,
                ).all()]
                if old_map_ids:
                    DeletePendingAction.query.filter(DeletePendingAction.file_map_id.in_(old_map_ids)).delete(synchronize_session=False)
                    FileLinkMap.query.filter(FileLinkMap.id.in_(old_map_ids)).delete(synchronize_session=False)
                db.session.commit()
                recycle_count = clean_recycle_bin()
                queue_count = JobQueue.query.filter(
                    JobQueue.status.in_(('success', 'failed', 'cancelled')),
                    JobQueue.finished_at.is_not(None),
                    JobQueue.finished_at < cutoff,
                ).delete(synchronize_session=False)
                db.session.commit()
                msg = f'清理 {retention} 天前日志：操作 {op_count}，执行 {exec_count}，待确认 {pending_count}，已删除映射 {len(old_map_ids)}，后台任务 {queue_count}，回收站 {recycle_count}'
                log_operation('cron_executed', 'CronJob', job.id, job.name, msg)
                return True, msg
            if job.task_type == 'clean_cache':
                HardlinkCache.query.delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, '清理缓存成功')
                return True, '清理缓存成功'
            if job.task_type == 'clean_backfill_failures':
                days = _clamp_int(get_config('backfill_failure_retention_days', '7'), 1, 90)
                cutoff = datetime.now(UTC) - timedelta(days=days)
                rows = FileLinkMap.query.filter(
                    FileLinkMap.torrent_hash.is_(None),
                    FileLinkMap.deleted_at.is_(None),
                    db.func.coalesce(FileLinkMap.backfill_fail_count, 0) > 2,
                    FileLinkMap.backfill_last_attempt_at.is_not(None),
                    FileLinkMap.backfill_last_attempt_at < cutoff,
                ).all()
                count = 0
                for row in rows:
                    row.backfill_fail_count = 0
                    row.backfill_last_attempt_at = None
                    count += 1
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'重置长期失败回填记录 {count} 条（>{days}天）')
                return True, f'重置长期失败回填记录 {count} 条（>{days}天）'
            if job.task_type == 'db_backup':
                ok, msg = run_backup_task()
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'db_backup: {msg}', ok)
                return ok, msg
            return False, f'未知任务类型: {job.task_type}'

        execute_with_guard(f'cron:{job.id}', job.name, job.task_type, _runner, source='cron', target_id=job.target_id)


def _acquire_scheduler_lock():
    global SCHEDULER_LOCK_FILE, SCHEDULER_LOCK_PID
    if SCHEDULER_LOCK_FILE is not None and SCHEDULER_LOCK_PID == os.getpid():
        return True
    if SCHEDULER_LOCK_FILE is not None:
        try:
            SCHEDULER_LOCK_FILE.close()
        except Exception:
            pass
        SCHEDULER_LOCK_FILE = None
    if fcntl is None:
        SCHEDULER_LOCK_PID = os.getpid()
        return True
    try:
        lock_path = Path(app.instance_path) / 'scheduler.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open('w', encoding='utf-8')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        SCHEDULER_LOCK_FILE = handle
        SCHEDULER_LOCK_PID = os.getpid()
        return True
    except BlockingIOError:
        app.logger.warning('another process already owns the scheduler lock; cron jobs disabled in this worker')
        return False
    except Exception as exc:
        app.logger.warning('scheduler lock unavailable, continuing without cross-process protection: %s', exc)
        return True


def start_cron_scheduler():
    if not _acquire_scheduler_lock():
        return
    if scheduler.running:
        scheduler.shutdown(wait=False)
    for job in CronJob.query.filter_by(enabled=True).all():
        update_cron_job(job.id)
    if not scheduler.running:
        scheduler.start()


def update_cron_job(job_id):
    job = db.session.get(CronJob, job_id)
    if not job:
        return
    key = f'cron_{job_id}'
    if scheduler.get_job(key):
        scheduler.remove_job(key)
    if not job.enabled:
        return
    parts = job.cron_expression.split()
    if len(parts) != 5:
        return
    day_of_week = '0' if parts[4] == '7' else parts[4]
    scheduler.add_job(
        run_cron_job,
        'cron',
        id=key,
        args=[job_id],
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=day_of_week,
        timezone=APP_TZ,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )


def _ensure_schema_meta_table():
    MIGRATION_SVC._ensure_schema_meta_table()


def _get_schema_meta_value(key, default=''):
    return MIGRATION_SVC.get_schema_meta_value(key, default)


def _set_schema_meta_value(key, value):
    MIGRATION_SVC.set_schema_meta_value(key, value)


def _delete_schema_meta_key(key):
    MIGRATION_SVC.delete_schema_meta_key(key)


def _get_schema_version():
    try:
        return int(_get_schema_meta_value('db_schema_version', '0') or '0')
    except Exception:
        return 0


def _set_schema_version(version):
    _set_schema_meta_value('db_schema_version', str(version))


def ensure_compat_columns():
    MIGRATION_SVC.ensure_compat_columns()


def migrate_stored_secrets():
    changed = 0
    for notifier in Notifier.query.all():
        value = notifier.api_key or ''
        if value and not value.startswith(('fernet:', 'enc:')):
            notifier.api_key = Downloader._encrypt_text(value)
            changed += 1
    for downloader in Downloader.query.all():
        value = downloader.encrypted_password or ''
        if value.startswith('enc:'):
            raw = downloader.get_password()
            if raw:
                downloader.encrypted_password = Downloader._encrypt_text(raw)
                changed += 1
    for admin in AdminUser.query.all():
        value = admin.totp_secret or ''
        if value and not value.startswith(('fernet:', 'enc:')):
            admin.set_totp_secret(value)
            changed += 1
    for row in AppConfig.query.filter(AppConfig.key.in_(SENSITIVE_CONFIG_KEYS)).all():
        value = row.value or ''
        if value.startswith('enc:'):
            raw = Downloader._decrypt_text(value)
            if raw:
                row.value = Downloader._encrypt_text(raw)
                changed += 1
        elif value and not value.startswith('fernet:'):
            row.value = Downloader._encrypt_text(value)
            changed += 1
    if changed:
        db.session.commit()
        app.logger.info('migrated %s stored secret(s) to authenticated encryption', changed)
    return changed


def repair_out_of_root_mappings():
    if _get_schema_meta_value('mapping_root_repair_v1', '') == 'done':
        return 0
    removed = 0
    for task in HardlinkTask.query.all():
        source_text = str(task.source_dir or '').strip()
        dest_text = str(task.dest_dir or '').strip()
        if not source_text or not dest_text:
            continue
        source_root = Path(source_text)
        dest_root = Path(dest_text)
        if not source_root.is_absolute() or not dest_root.is_absolute():
            continue
        for row in FileLinkMap.query.filter_by(task_id=task.id).all():
            source_path = str(row.source_path or '')
            dest_path = str(row.dest_path or '')
            if (
                not source_path
                or not dest_path
                or not _is_path_within_root(Path(source_path), source_root)
                or not _is_path_within_root(Path(dest_path), dest_root)
            ):
                DeletePendingAction.query.filter_by(file_map_id=row.id).delete(synchronize_session=False)
                HardlinkCache.query.filter_by(source_path=row.source_path).delete(synchronize_session=False)
                db.session.delete(row)
                removed += 1
    valid_map_ids = {row[0] for row in db.session.query(FileLinkMap.id).all()}
    valid_task_ids = {row[0] for row in db.session.query(DeleteMonitorTask.id).all()}
    orphan_pending = 0
    for pending in DeletePendingAction.query.all():
        if pending.file_map_id not in valid_map_ids or pending.task_id not in valid_task_ids:
            db.session.delete(pending)
            orphan_pending += 1
    _set_schema_meta_value('mapping_root_repair_v1', 'done')
    db.session.commit()
    if removed or orphan_pending:
        app.logger.warning('safety repair removed mappings=%s orphan_pending=%s', removed, orphan_pending)
    return removed


def init_defaults():
    default_configs = build_default_config_rows()
    changed = False
    for key, value, desc in default_configs:
        if not AppConfig.query.filter_by(key=key).first():
            set_config(key, value, desc, commit=False)
            changed = True
    if changed:
        db.session.commit()


def init_system_jobs():
    log_job = CronJob.query.filter_by(name='系统日志清理').first()
    if not log_job:
        db.session.add(CronJob(name='系统日志清理', task_type='clean_logs', cron_expression='0 3 * * *', description='【系统维护】清理历史执行/操作日志，避免日志表过大导致卡顿', enabled=True))

    cache_job = CronJob.query.filter_by(name='系统缓存清理').first()
    if not cache_job:
        # 默认禁用：该任务会清理硬链接缓存与部分映射记录，不建议新手直接开启。
        db.session.add(CronJob(name='系统缓存清理', task_type='clean_cache', cron_expression='30 3 * * *', description='【系统维护】清理硬链接缓存记录（可能影响已处理记录追踪），默认禁用，建议手动按需执行', enabled=False))
    else:
        # 历史版本兼容：把系统缓存清理改为默认禁用，避免误清理。
        cache_job.enabled = False
        if not cache_job.description or '默认禁用' not in (cache_job.description or ''):
            cache_job.description = '【系统维护】清理硬链接缓存记录（可能影响已处理记录追踪），默认禁用，建议手动按需执行'

    backfill_job = CronJob.query.filter_by(name='系统映射回填').first()
    if not backfill_job:
        db.session.add(CronJob(name='系统映射回填', task_type='backfill_mapping', cron_expression='0 * * * *', description='【系统维护】每60分钟尝试将映射记录关联到下载器种子'))
    else:
        if backfill_job.cron_expression == '*/30 * * * *':
            backfill_job.cron_expression = '0 * * * *'
            backfill_job.description = '【系统维护】每60分钟尝试将映射记录关联到下载器种子'

    backup_job = CronJob.query.filter_by(name='系统数据库备份').first()
    if not backup_job:
        db.session.add(CronJob(name='系统数据库备份', task_type='db_backup', cron_expression='0 */6 * * *', description='【系统维护】每6小时自动备份数据库'))

    backfill_cleanup_job = CronJob.query.filter_by(name='系统回填失败重置').first()
    if not backfill_cleanup_job:
        db.session.add(CronJob(name='系统回填失败重置', task_type='clean_backfill_failures', cron_expression='30 4 * * *', description='【系统维护】重置长期失败的回填计数，降低历史脏数据影响', enabled=False))

    db.session.commit()


def reconcile_stale_running_executions():
    now = datetime.now(UTC)
    grace_seconds = _env_int('HLM_STALE_EXECUTION_GRACE_SECONDS', 600, 60)
    cutoff = now - timedelta(seconds=grace_seconds)
    stale = JobExecutionLog.query.filter(JobExecutionLog.status == 'running', (JobExecutionLog.started_at.is_(None)) | (JobExecutionLog.started_at < cutoff)).all()
    if not stale:
        return 0
    for row in stale:
        row.status = 'failed'
        row.message = '服务重启导致上次执行中断（已自动修正状态）'
        row.finished_at = now
        if row.started_at:
            started = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=UTC)
            row.duration_ms = max(0, int((now - started).total_seconds() * 1000))
        else:
            row.duration_ms = row.duration_ms or 0
    db.session.commit()
    app.logger.warning('reconciled stale running executions: %s', len(stale))
    return len(stale)


def init_sentry():
    dsn = (os.environ.get('SENTRY_DSN') or '').strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0') or '0'),
            environment=(os.environ.get('SENTRY_ENVIRONMENT') or 'production').strip(),
            release=app.config.get('APP_VERSION', 'dev'),
            send_default_pii=False,
        )
        return True
    except Exception as exc:
        app.logger.warning('sentry init failed: %s', exc)
        return False


def init_app():
    global APP_BOOTSTRAPPED
    init_console_logger()
    init_sentry()
    if app.config['SECRET_KEY'] == DEFAULT_SECRET_KEY:
        app.logger.warning('Using default SECRET_KEY; set SECRET_KEY in production to avoid session forgery risk.')
    if not _auth_enabled():
        if _setup_required():
            app.logger.warning('Initial setup required: open /setup and use the token from instance/setup_token.txt')
        else:
            app.logger.warning('Authentication is disabled by HLM_ALLOW_NO_AUTH; do not expose this instance to untrusted networks.')

    with app.app_context():
        install_sqlite_pragmas()
        db.create_all()
        try:
            (Path(app.instance_path) / 'hardlink_manager.db').chmod(0o600)
        except Exception:
            pass
        init_defaults()
        if _setup_required():
            _ensure_setup_token()
        if (get_config('backup_dir', '/app/data/backups') or '').strip() == '/app/data/backups' and not _dir_is_writable('/app/data/backups'):
            fallback_backup = str(Path(app.instance_path) / 'backups')
            set_config('backup_dir', fallback_backup, '数据库备份目录')
            app.logger.warning('default backup dir is not writable; using %s', fallback_backup)
        if _inside_container() and (get_config('proxy_url', '') or '').strip() in {'http://127.0.0.1:7890', 'http://localhost:7890'}:
            set_config('proxy_url', '', '统一外网代理地址')
            app.logger.warning('cleared legacy loopback proxy default inside container; configure host.docker.internal or a LAN proxy if needed')
        if not (get_config('allowed_roots', '') or '').strip():
            app.logger.warning('allowed_roots is empty: absolute paths configured in tasks are not restricted to a media root.')
        restart_flag = Path(app.instance_path) / 'dev_restart_request.flag'
        if restart_flag.exists():
            try:
                set_config('last_dev_apply_status', 'success')
                set_config('last_dev_apply_message', '服务已重启并重新启动')
                set_config('last_dev_apply_at', (datetime.now(UTC)).isoformat())
                restart_flag.unlink(missing_ok=True)
            except Exception as exc:
                app.logger.warning('consume dev restart marker failed: %s', exc)

        apply_result = Path(app.instance_path) / 'dev_apply_result.json'
        if apply_result.exists():
            try:
                payload = json.loads(apply_result.read_text(encoding='utf-8') or '{}')
                st = str(payload.get('status') or '').strip().lower()
                msg = str(payload.get('message') or '').strip()
                ts = str(payload.get('at') or '').strip() or datetime.now(UTC).isoformat()
                if st in {'success', 'failed', 'skipped'}:
                    set_config('last_dev_apply_status', st)
                    set_config('last_dev_apply_message', msg[:500] if msg else '-')
                    set_config('last_dev_apply_at', ts)
                apply_result.unlink(missing_ok=True)
            except Exception as exc:
                app.logger.warning('consume dev apply result failed: %s', exc)

        backup_ok, backup_msg = MIGRATION_SVC.pre_migration_backup_if_needed(target_schema=MIGRATION_SVC.get_target_schema_version())
        if not backup_ok:
            app.logger.error('pre migration backup failed: %s', backup_msg)
            raise RuntimeError(f'升级前备份失败，已中止启动: {backup_msg}')
        ensure_compat_columns()
        migrate_stored_secrets()
        repair_out_of_root_mappings()
        _set_schema_meta_value('last_app_version', app.config.get('APP_VERSION', 'dev'))
        _set_schema_meta_value('last_migration_status', 'success')
        db.session.commit()
        os.environ['APP_LOG_MAX_MB'] = str(get_config('app_log_max_mb', os.environ.get('APP_LOG_MAX_MB', '10')) or '10')
        os.environ['APP_LOG_BACKUP_COUNT'] = str(get_config('app_log_backup_count', os.environ.get('APP_LOG_BACKUP_COUNT', '5')) or '5')
        init_file_logger()
        app.logger.info('startup access_log_enabled=%s tz=%s version=%s', app.config.get('ACCESS_LOG_ENABLED'), os.environ.get('TZ', ''), app.config.get('APP_VERSION'))
        init_system_jobs()
        if not app.config.get('WORKER_MODE'):
            if not app.config.get('DISABLE_SCHEDULER'):
                reconcile_stale_running_executions()
                start_cron_scheduler()
        if APP_BOOTSTRAPPED:
            return
        app.register_blueprint(init_api_routes(
            HardlinkTask,
            DeleteMonitorTask,
            AppConfig=AppConfig,
            OperationLog=OperationLog,
            db=db,
            get_config=get_config,
            get_release_info=get_release_info,
            get_running_executions_snapshot=get_running_executions_snapshot,
            logger=app.logger,
            JobQueue=JobQueue,
            enqueue_job=enqueue_job,
            request_stop_queued_job=request_stop_queued_job,
            started_at=APP_STARTED_AT,
        ))
        app.register_blueprint(init_web_routes(RouteDeps(
            HardlinkTask=HardlinkTask,
            DeleteMonitorTask=DeleteMonitorTask,
            Downloader=Downloader,
            Notifier=Notifier,
            HardlinkCache=HardlinkCache,
            FileLinkMap=FileLinkMap,
            OperationLog=OperationLog,
            JobExecutionLog=JobExecutionLog,
            DeletePendingAction=DeletePendingAction,
            AppConfig=AppConfig,
            AppConfigSnapshot=AppConfigSnapshot,
            AdminUser=AdminUser,
            JobQueue=JobQueue,
            RecycleItem=RecycleItem,
            CronJob=CronJob,
            db=db,
            scheduler=scheduler,
            APP_TZ=APP_TZ,
            UTC=UTC,
            get_config=get_config,
            set_config=set_config,
            log_operation=log_operation,
            validate_path=validate_path,
            validate_host=validate_host,
            validate_cron_expression=validate_cron_expression,
            scan_hardlink_task=scan_hardlink_task,
            scan_delete_task=scan_delete_task,
            scan_backfill_task=scan_backfill_task,
            run_hardlink_once=run_hardlink_once,
            enqueue_job=enqueue_job,
            run_delete_once=run_delete_once,
            run_backfill_once=run_backfill_once,
            run_backfill_for_map_id=run_backfill_for_map_id,
            run_backup_once=run_backup_once,
            run_backup_task=run_backup_task,
            run_cron_job=run_cron_job,
            update_cron_job=update_cron_job,
            list_torrents=list_torrents,
            send_telegram_notification=send_telegram_notification,
            send_notification=send_notification,
            delete_torrent=delete_torrent,
            get_release_info=get_release_info,
            request_stop_by_execution=request_stop_by_execution,
            get_running_executions_snapshot=get_running_executions_snapshot,
            save_config_snapshot=save_config_snapshot,
            restore_config_snapshot=restore_config_snapshot,
        )))
        APP_BOOTSTRAPPED = True


def create_app(config_overrides=None):
    if config_overrides:
        app.config.update(config_overrides)
    init_app()
    return app


if __name__ == '__main__':
    init_app()
    app.run(host='0.0.0.0', port=5000, debug=False)
