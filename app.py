import os
import secrets
import logging
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime, UTC, timedelta
from threading import Lock
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, session, abort, redirect, url_for, render_template, flash
from core.services.backfill_service import scan_backfill_rows
from core.routes.api import init_api_routes
from core.routes.web import init_web_routes
from core.deps import RouteDeps
from core.services.hardlink_service import create_hardlink_for_file as svc_create_hardlink_for_file
from core.services.delete_service import scan_delete_rows
from core.services.backup_service import run_sqlite_backup
from core.services.execution_service import ExecutionManager
from core.services.migration_service import MigrationService
from core.extensions import db, bcrypt, scheduler
from core.models import HardlinkTask, DeleteMonitorTask, Downloader, Notifier, HardlinkCache, FileLinkMap, OperationLog, JobExecutionLog, DeletePendingAction, AppConfig, CronJob


app = Flask(__name__)
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
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('Referrer-Policy', 'no-referrer')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    return resp


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
        sh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        app.logger.addHandler(sh)

    app.logger.propagate = True

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key-for-dev-only')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hardlink_manager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['APP_USERNAME'] = os.environ.get('APP_USERNAME', '').strip()
app.config['APP_PASSWORD'] = os.environ.get('APP_PASSWORD', '')
app.config['REQUEST_TIMEOUT_SECONDS'] = int(os.environ.get('REQUEST_TIMEOUT_SECONDS', '10'))
app.config['ACCESS_LOG_ENABLED'] = os.environ.get('ACCESS_LOG_ENABLED', 'true').lower() == 'true'
app.config['APP_VERSION'] = (Path(__file__).resolve().parent / 'VERSION').read_text(encoding='utf-8').strip() if (Path(__file__).resolve().parent / 'VERSION').exists() else 'dev'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=max(1, int(os.environ.get('LOGIN_REMEMBER_DAYS', '14') or '14')))

try:
    APP_TZ = ZoneInfo(os.environ.get('TZ', 'Asia/Shanghai'))
except Exception:
    APP_TZ = UTC

# Keep scheduler cron trigger timezone aligned with UI/runtime timezone.
scheduler.configure(timezone=APP_TZ)


def init_file_logger():
    log_dir = Path(os.environ.get('APP_LOG_DIR', '/app/data/logs'))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback for local tests/non-container env where /app is not writable.
        log_dir = Path(__file__).resolve().parent / 'data' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)

    max_mb = int(os.environ.get('APP_LOG_MAX_MB', '10') or '10')
    backup_count = int(os.environ.get('APP_LOG_BACKUP_COUNT', '5') or '5')
    log_file = log_dir / 'app.log'

    for h in app.logger.handlers:
        if isinstance(h, RotatingFileHandler) and Path(getattr(h, 'baseFilename', '')) == log_file:
            return

    handler = RotatingFileHandler(
        log_file,
        maxBytes=max(1, max_mb) * 1024 * 1024,
        backupCount=max(1, backup_count),
        encoding='utf-8',
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    app.logger.addHandler(handler)


def _outbound_proxies(override=None):
    proxy = (override or '').strip() or (get_config('proxy_url', '') or '').strip()
    if not proxy:
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
        app.logger.warning('github_version_check_failed repo=%s err=%s', repo, exc)
        return {
            'local_version': local_version,
            'remote_version': '-',
            'has_update': False,
            'repo': repo,
            'checked_at': checked_at,
            'message': f'检查失败: {exc}',
        }



db.init_app(app)
bcrypt.init_app(app)

AUTH_LOCK = Lock()
AUTH_FAIL_WINDOW_SECONDS = 60
AUTH_FAIL_MAX_TIMES = 3
AUTH_BLOCK_SECONDS = 1800
AUTH_STATE = {}
APP_BOOTSTRAPPED = False
QB_SESSION_LOCK = Lock()
QB_SESSION_CACHE = {}
QB_SESSION_TTL_SECONDS = max(60, int(os.environ.get('QB_SESSION_TTL_SECONDS', '900') or '900'))
EXEC_MGR = ExecutionManager(db, JobExecutionLog, lambda: datetime.now(UTC))
MIGRATION_SVC = MigrationService(
    db=db,
    logger=app.logger,
    instance_path_getter=lambda: app.instance_path,
    app_version_getter=lambda: app.config.get('APP_VERSION', 'dev'),
    get_config=lambda key, default=None: get_config(key, default),
    run_sqlite_backup=run_sqlite_backup,
)



def _auth_enabled():
    username = (app.config.get('APP_USERNAME') or '').strip()
    password = app.config.get('APP_PASSWORD') or ''
    return bool(username and password)


def _is_logged_in():
    return bool(session.get('logged_in'))


def _get_client_ip():
    # Prefer reverse-proxy forwarded IP if present.
    xff = (request.headers.get('X-Forwarded-For') or '').strip()
    if xff:
        return xff.split(',')[0].strip()
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
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)
    try:
        return dt_obj.astimezone(APP_TZ).strftime(fmt)
    except Exception:
        return dt_obj.strftime(fmt)


@app.before_request
def security_guard():
    if request.endpoint == 'static':
        return

    # Allow unauthenticated health checks and auth pages.
    if request.endpoint in {'api_bp.api_health', 'login_page', 'logout'}:
        return

    if _auth_enabled() and not _is_logged_in():
        next_path = request.full_path if request.query_string else request.path
        return redirect(url_for('login_page', next=next_path))

    if app.config.get('ACCESS_LOG_ENABLED', True):
        app.logger.info('request method=%s path=%s endpoint=%s ip=%s', request.method, request.path, request.endpoint, request.remote_addr)
    if request.method == 'POST':
        request_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        session_token = session.get('_csrf_token')
        if not session_token or not request_token or request_token != session_token:
            abort(400, description='Invalid CSRF token')



@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if not _auth_enabled():
        return redirect('/')

    if _is_logged_in():
        return redirect(request.args.get('next') or '/')

    if request.method == 'POST':
        client_ip = _get_client_ip()
        blocked, remain_seconds = _auth_is_blocked(client_ip)
        if blocked:
            flash(f'登录失败次数过多，请 {remain_seconds} 秒后再试。', 'danger')
            return render_template('login.html', next_path=(request.form.get('next') or '/')), 429

        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        remember = request.form.get('remember_me') == 'on'
        next_path = (request.form.get('next') or '/').strip() or '/'

        if username == app.config['APP_USERNAME'] and password == app.config['APP_PASSWORD']:
            session['logged_in'] = True
            session['login_user'] = username
            session.permanent = bool(remember)
            _clear_auth_failure(client_ip)
            flash('登录成功', 'success')
            if not next_path.startswith('/'):
                next_path = '/'
            return redirect(next_path)

        blocked_until = _record_auth_failure(client_ip)
        if blocked_until:
            flash('1分钟内错误超过3次，已封禁30分钟。', 'danger')
            return render_template('login.html', next_path=next_path), 429
        flash('用户名或密码错误', 'danger')
        return render_template('login.html', next_path=next_path), 401

    return render_template('login.html', next_path=(request.args.get('next') or '/'))


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    session.pop('login_user', None)
    session.permanent = False
    flash('已退出登录', 'success')
    return redirect('/login' if _auth_enabled() else '/')


def get_config(key, default=None):
    config = AppConfig.query.filter_by(key=key).first()
    return config.value if config else default


def set_config(key, value, description=None, commit=True):
    config = AppConfig.query.filter_by(key=key).first()
    if config:
        config.value = value
        if description:
            config.description = description
    else:
        db.session.add(AppConfig(key=key, value=value, description=description))
    if commit:
        db.session.commit()


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


def validate_path(path):
    if not path:
        return False, '路径不能为空'
    if '\x00' in path or '~' in path or '\\' in path:
        return False, '非法路径字符'
    p = Path(path)
    if not p.is_absolute():
        return False, '路径必须为绝对路径'

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

        url = f"{api_base}/bot{notifier.api_key}/sendMessage"
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
        app.logger.error(f'Telegram notification failed: {exc}')
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
    return session_obj


def invalidate_qb_client(downloader):
    key = _qb_cache_key(downloader)
    with QB_SESSION_LOCK:
        QB_SESSION_CACHE.pop(key, None)


def get_qb_client(downloader):
    key = _qb_cache_key(downloader)
    now = datetime.now(UTC)
    with QB_SESSION_LOCK:
        cached = QB_SESSION_CACHE.get(key)
        if cached and cached.get('expires_at') and cached['expires_at'] > now:
            return cached['session']
    session_obj = _qb_new_session(downloader, login=True)
    with QB_SESSION_LOCK:
        QB_SESSION_CACHE[key] = {
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


def list_torrents(downloader):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []
    try:
        resp = qb_request(downloader, 'GET', '/api/v2/torrents/info')
        return resp.json() if resp is not None else None
    except Exception as exc:
        app.logger.error(f'list torrents failed: {exc}')
        return None


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
        app.logger.error(f'list torrent files failed: {exc}')
        return None


def delete_torrent(downloader, torrent_hash):
    if not downloader or downloader.type != 'qbittorrent':
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
        app.logger.error(f'delete torrent failed: {exc}')
        return False


def scan_hardlink_task(task_id, should_stop=None):
    task = db.session.get(HardlinkTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    source = Path(task.source_dir)
    if not source.exists() or not source.is_dir():
        return False, '源目录不存在'

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
                created += 1
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

    rows = FileLinkMap.query.filter(FileLinkMap.task_id == task.id, FileLinkMap.deleted_at.is_(None)).all()
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
    if total_candidates > max_del:
        log_operation('delete_guard_truncated', 'HardlinkTask', task.id, task.name, f'本轮命中 {total_candidates}，按阈值分批执行前 {max_del} 条，其余待下轮处理', True)
        candidates = candidates[:max_del]

    deleted_torrents = 0
    pending_total = 0
    hit_total = 0
    linked_removed = 0
    pending_source_hits = 0
    pending_source_samples = []
    pending_log_mode = (get_config('pending_source_log_mode', 'aggregate') or 'aggregate').strip().lower()
    if pending_log_mode not in {'aggregate', 'detail'}:
        pending_log_mode = 'aggregate'

    def _prune_empty_dirs_from(file_path: str, root_path: str):
        try:
            root = Path(root_path).resolve(strict=False)
            cur = Path(file_path).parent
            while True:
                try:
                    cur_res = cur.resolve(strict=False)
                    if str(cur_res) == str(root) or not str(cur_res).startswith(str(root)):
                        break
                    if any(cur.iterdir()):
                        break
                    cur.rmdir()
                    cur = cur.parent
                except Exception:
                    break
        except Exception:
            return

    def _create_pending_action(_proxy_task, row, deleted_path, torrent_hash, match_by, reason):
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

        src = str(row.source_path or '')
        dst = str(row.dest_path or '')
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

        policy_source_type = 'manual' if source_type == 'pending' else source_type
        if deleted_side == 'source':
            counterpart = dst
            policy_key = 'manual_source_delete_delete_dest' if policy_source_type == 'manual' else 'downloader_source_delete_delete_dest'
            action_label = 'source_deleted'
        else:
            counterpart = src
            policy_key = 'manual_dest_delete_delete_source' if policy_source_type == 'manual' else 'downloader_dest_delete_delete_source'
            action_label = 'dest_deleted'

        counterpart_removed = None
        if counterpart and str(get_config(policy_key, 'true')).lower() == 'true':
            counterpart_removed = False
            try:
                cp = Path(counterpart)
                if cp.exists() and cp.is_file():
                    cp.unlink()
                    counterpart_removed = True
                    linked_removed += 1
                    # 对侧文件删除后，顺带清理空目录，避免残留 a/ 这类空壳目录。
                    if deleted_side == 'source':
                        _prune_empty_dirs_from(counterpart, task.dest_dir)
                    else:
                        _prune_empty_dirs_from(counterpart, task.source_dir)
            except Exception:
                counterpart_removed = False

            if counterpart_removed:
                log_operation('linked_file_deleted', 'FileLinkMap', row.id, task.name, f'{action_label} -> delete_counterpart: {counterpart}')
            else:
                log_operation('linked_file_delete_skip', 'FileLinkMap', row.id, task.name, f'{action_label} -> counterpart_missing_or_failed: {counterpart}')
                # 即便文件已不存在，也尝试清理可能遗留的空目录。
                if deleted_side == 'source':
                    _prune_empty_dirs_from(counterpart, task.dest_dir)
                else:
                    _prune_empty_dirs_from(counterpart, task.source_dir)

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

        if source_type == 'pending':
            db.session.flush()
            continue

        ok, deleted_cnt, _hits, pending_cnt = scan_delete_rows(
            proxy,
            [row],
            try_match_torrent_by_mapping_or_name,
            delete_torrent,
            log_operation,
            get_config,
            send_telegram_notification,
            _create_pending_action,
            should_stop=should_stop,
        )
        if not ok:
            db.session.commit()
            return False, f'删除联动已阻断：检测删除 {hit_total}，删种 {deleted_torrents}，待确认 {pending_total}'
        deleted_torrents += int(deleted_cnt or 0)
        pending_total += int(pending_cnt or 0)

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
    return True, f'删除联动完成：本轮处理 {hit_total}/{total_candidates}，联动删种 {deleted_torrents}，待确认 {pending_total}，对侧删除 {linked_removed}，待判定 {pending_source_hits}'




def scan_delete_task(task_id, should_stop=None):
    task = db.session.get(DeleteMonitorTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    monitor_root = str(Path(task.directory).resolve(strict=False))
    root_like = f'{monitor_root}/%'
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

    ok, deleted_torrents, hit_count, pending_count = scan_delete_rows(
        task,
        rows,
        try_match_torrent_by_mapping_or_name,
        delete_torrent,
        log_operation,
        get_config,
        send_telegram_notification,
        create_pending_delete_action,
        should_stop=should_stop,
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
    keep_last = int(get_config('backup_keep_last', '7') or '7')
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
                retention = int(get_config('log_retention_days', '30'))
                cutoff = datetime.now(UTC) - timedelta(days=retention)
                OperationLog.query.filter(OperationLog.created_at < cutoff).delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'清理 {retention} 天前日志')
                return True, f'清理 {retention} 天前日志'
            if job.task_type == 'clean_cache':
                HardlinkCache.query.delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, '清理缓存成功')
                return True, '清理缓存成功'
            if job.task_type == 'clean_backfill_failures':
                days = int(get_config('backfill_failure_retention_days', '7') or '7')
                days = max(1, min(90, days))
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


def start_cron_scheduler():
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
    scheduler.add_job(run_cron_job, 'cron', id=key, args=[job_id], minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4], timezone=APP_TZ)


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


def init_defaults():
    default_configs = [
        ('log_retention_days', '30', '日志保留天数'),
        ('auto_clean_logs', 'true', '自动清理日志'),
        ('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv', '默认文件扩展名'),
        ('default_exclude_dirs', 'sample,subs', '默认排除目录'),
        ('delete_files_with_torrent', 'false', '删除种子时同时删除文件'),
        ('notify_on_delete', 'true', '启用删除通知'),
        ('notify_on_risky_delete', 'true', '疑似误删风险通知'),
        ('delete_match_strict_mode', 'true', '删除联动仅精确匹配自动执行'),
        ('pending_source_guard_enabled', 'true', '删除联动来源待判定保护开关'),
        ('pending_source_guard_seconds', '900', '删除联动来源待判定窗口（秒）'),
        ('pending_source_warn_threshold', '200', '系统诊断：待判定映射告警阈值'),
        ('pending_source_log_mode', 'aggregate', '待判定来源日志模式（aggregate/detail）'),
        ('manual_dest_delete_delete_source', 'true', '手动来源：删除目标后自动删除源文件'),
        ('manual_source_delete_delete_dest', 'true', '手动来源：删除源文件后自动删除目标文件'),
        ('downloader_dest_delete_delete_source', 'true', '下载器来源：删除目标后自动删除源文件'),
        ('downloader_source_delete_delete_dest', 'true', '下载器来源：删除源文件后自动删除目标文件'),
        ('downloader_dest_delete_delete_torrent', 'true', '下载器来源：删除目标后自动删种'),
        ('downloader_source_delete_delete_torrent', 'true', '下载器来源：删除源后自动删种'),
        ('allowed_roots', '', '允许访问的路径根目录，逗号分隔'),
        ('tg_api_base', 'https://api.telegram.org', 'Telegram API基础地址'),
        ('backup_dir', '/app/data/backups', '数据库备份目录'),
        ('backup_keep_last', '7', '数据库备份保留数量'),
        ('github_version_check_enabled', 'true', '启用GitHub版本检查'),
        ('github_repo', 'marod1m/HLM-Demo', 'GitHub仓库 owner/repo'),
        ('github_api_base', 'https://api.github.com', 'GitHub API基础地址'),
        ('proxy_url', 'http://127.0.0.1:7890', '统一外网代理地址（Telegram/GitHub），留空则直连'),
        ('app_log_max_mb', '10', '应用日志单文件大小上限（MB）'),
        ('app_log_backup_count', '5', '应用日志滚动保留文件数'),
        ('version_check_cache_minutes', '720', '版本检查缓存分钟数'),
        ('critical_action_passphrase', '', '关键操作口令（留空=不启用）'),
        ('backfill_batch_limit', '500', '映射回填批次大小（自动调优）'),
        ('backfill_max_candidates', '120', '映射回填候选上限'),
        ('backfill_file_fetch_workers', '4', '映射回填文件列表并发请求数'),
        ('backfill_max_failures', '2', '映射回填失败跳过阈值'),
        ('backfill_path_mappings', '', '回填路径映射，格式 /host/path=>/container/path;...'),
        ('backfill_failure_retention_days', '7', '长期失败回填记录重置阈值（天）'),
        ('dev_mode', (os.environ.get('APP_DEV_MODE', 'false') or 'false').lower(), '开发模式开关（页面配置）'),
        ('dev_auto_pull', (os.environ.get('APP_DEV_AUTO_PULL', 'false') or 'false').lower(), '开发模式：启动自动拉取'),
        ('dev_git_repo', os.environ.get('APP_DEV_GIT_REPO', '') or 'https://github.com/MaroD1M/HLM-Demo.git', '开发模式：Git 仓库地址'),
        ('dev_git_branch', os.environ.get('APP_DEV_GIT_BRANCH', 'master') or 'master', '开发模式：Git 分支'),
        ('dev_auto_pip_sync', (os.environ.get('APP_DEV_AUTO_PIP_SYNC', 'true') or 'true').lower(), '开发模式：依赖自动同步'),
        ('dev_pip_sync_timeout', os.environ.get('APP_DEV_PIP_SYNC_TIMEOUT', '120') or '120', '开发模式：pip 同步超时（秒）'),
        ('dev_git_token', os.environ.get('APP_DEV_GIT_TOKEN', '') or '', '开发模式：Git 访问令牌（敏感）'),
        ('dev_proxy_url', os.environ.get('APP_DEV_PROXY_URL', '') or '', '开发模式：代理地址'),
        ('dev_no_proxy', os.environ.get('APP_DEV_NO_PROXY', 'localhost,127.0.0.1,::1') or 'localhost,127.0.0.1,::1', '开发模式：NO_PROXY'),
        ('last_dev_apply_status', '', '开发模式最近应用状态'),
        ('last_dev_apply_message', '', '开发模式最近应用消息'),
        ('last_dev_apply_at', '', '开发模式最近应用时间'),
    ]
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
        db.session.add(CronJob(name='系统缓存清理', task_type='clean_cache', cron_expression='30 3 * * *', description='【系统维护】清理缓存记录（可能影响已处理记录追踪），默认禁用，建议手动按需执行', enabled=False))
    else:
        # 历史版本兼容：把系统缓存清理改为默认禁用，避免误清理。
        cache_job.enabled = False
        if not cache_job.description or '默认禁用' not in (cache_job.description or ''):
            cache_job.description = '【系统维护】清理缓存记录（可能影响已处理记录追踪），默认禁用，建议手动按需执行'

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
    stale = JobExecutionLog.query.filter_by(status='running').all()
    if not stale:
        return 0
    now = datetime.now(UTC)
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


def init_app():
    global APP_BOOTSTRAPPED
    init_console_logger()
    if app.config['SECRET_KEY'] == 'default-secret-key-for-dev-only':
        app.logger.warning('Using default SECRET_KEY; set SECRET_KEY in production to avoid session forgery risk.')

    with app.app_context():
        db.create_all()
        init_defaults()
        restart_flag = Path('instance') / 'dev_restart_request.flag'
        if restart_flag.exists():
            try:
                set_config('last_dev_apply_status', 'success')
                set_config('last_dev_apply_message', '服务已重启并重新启动')
                set_config('last_dev_apply_at', (datetime.now(UTC)).isoformat())
                restart_flag.unlink(missing_ok=True)
            except Exception as exc:
                app.logger.warning('consume dev restart marker failed: %s', exc)

        apply_result = Path('instance') / 'dev_apply_result.json'
        if apply_result.exists():
            try:
                payload = __import__('json').loads(apply_result.read_text(encoding='utf-8') or '{}')
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
        _set_schema_meta_value('last_app_version', app.config.get('APP_VERSION', 'dev'))
        _set_schema_meta_value('last_migration_status', 'success')
        db.session.commit()
        os.environ['APP_LOG_MAX_MB'] = str(get_config('app_log_max_mb', os.environ.get('APP_LOG_MAX_MB', '10')) or '10')
        os.environ['APP_LOG_BACKUP_COUNT'] = str(get_config('app_log_backup_count', os.environ.get('APP_LOG_BACKUP_COUNT', '5')) or '5')
        init_file_logger()
        app.logger.info('startup access_log_enabled=%s tz=%s version=%s', app.config.get('ACCESS_LOG_ENABLED'), os.environ.get('TZ', ''), app.config.get('APP_VERSION'))
        init_system_jobs()
        reconcile_stale_running_executions()
        start_cron_scheduler()
        if APP_BOOTSTRAPPED:
            return
        app.register_blueprint(init_api_routes(HardlinkTask, DeleteMonitorTask))
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
            run_delete_once=run_delete_once,
            run_backfill_once=run_backfill_once,
            run_backfill_for_map_id=run_backfill_for_map_id,
            run_backup_once=run_backup_once,
            run_backup_task=run_backup_task,
            run_cron_job=run_cron_job,
            update_cron_job=update_cron_job,
            list_torrents=list_torrents,
            send_telegram_notification=send_telegram_notification,
            delete_torrent=delete_torrent,
            get_release_info=get_release_info,
            request_stop_by_execution=request_stop_by_execution,
            get_running_executions_snapshot=get_running_executions_snapshot,
        )))
        APP_BOOTSTRAPPED = True


if __name__ == '__main__':
    init_app()
    app.run(host='0.0.0.0', port=5000, debug=False)
