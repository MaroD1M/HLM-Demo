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
from flask import Flask, request, session, abort
from core.services.backfill_service import scan_backfill_rows
from core.routes.api import init_api_routes
from core.routes.web import init_web_routes
from core.deps import RouteDeps
from core.services.hardlink_service import create_hardlink_for_file as svc_create_hardlink_for_file
from core.services.delete_service import scan_delete_rows
from core.services.backup_service import run_sqlite_backup
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


def init_console_logger():
    # Ensure logs are always visible in container stdout (e.g., Synology Container Manager).
    has_stream = any(isinstance(h, logging.StreamHandler) for h in app.logger.handlers)
    if not has_stream:
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

try:
    APP_TZ = ZoneInfo(os.environ.get('TZ', 'Asia/Shanghai'))
except Exception:
    APP_TZ = UTC


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
            except Exception:
                pass
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
        set_config('version_check_cached_remote', remote)
        set_config('version_check_cached_at', checked_at)
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

RUN_LOCK = Lock()
RUNNING_META = {}
STOP_REQUESTED_KEYS = set()
RUNNING_KEYS = set()
AUTH_LOCK = Lock()
AUTH_FAIL_WINDOW_SECONDS = 60
AUTH_FAIL_MAX_TIMES = 3
AUTH_BLOCK_SECONDS = 1800
AUTH_STATE = {}
APP_BOOTSTRAPPED = False


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
    return {'csrf_token': ensure_csrf_token(), 'fmt_dt': format_datetime_local}


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

    # Allow unauthenticated health checks for container/runtime probes.
    if request.endpoint in {'api_bp.api_health'}:
        return

    if app.config['APP_USERNAME'] and app.config['APP_PASSWORD']:
        client_ip = _get_client_ip()
        blocked, remain_seconds = _auth_is_blocked(client_ip)
        if blocked:
            app.logger.warning('auth_blocked method=%s path=%s ip=%s remain=%ss', request.method, request.path, client_ip, remain_seconds)
            return ('Too Many Requests: 登录失败次数过多，请30分钟后重试或重启容器。', 429, {'Retry-After': str(remain_seconds)})

        auth = request.authorization
        if not auth or auth.username != app.config['APP_USERNAME'] or auth.password != app.config['APP_PASSWORD']:
            blocked_until = _record_auth_failure(client_ip)
            if blocked_until:
                app.logger.warning('auth_failed_blocked method=%s path=%s ip=%s blocked_until=%s', request.method, request.path, client_ip, blocked_until.isoformat())
                return ('Too Many Requests: 1分钟内错误超过3次，已封禁30分钟。', 429, {'Retry-After': str(AUTH_BLOCK_SECONDS)})
            app.logger.warning('auth_failed method=%s path=%s ip=%s', request.method, request.path, client_ip)
            return ('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="HLM"'})
        _clear_auth_failure(client_ip)

    if app.config.get('ACCESS_LOG_ENABLED', True):
        app.logger.info('request method=%s path=%s endpoint=%s ip=%s', request.method, request.path, request.endpoint, request.remote_addr)
    if request.method == 'POST':
        request_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        session_token = session.get('_csrf_token')
        if not session_token or not request_token or request_token != session_token:
            abort(400, description='Invalid CSRF token')


def get_config(key, default=None):
    config = AppConfig.query.filter_by(key=key).first()
    return config.value if config else default


def set_config(key, value, description=None):
    config = AppConfig.query.filter_by(key=key).first()
    if config:
        config.value = value
        if description:
            config.description = description
    else:
        db.session.add(AppConfig(key=key, value=value, description=description))
    db.session.commit()


def log_operation(operation_type, target_type=None, target_id=None, target_name=None, message=None, success=True):
    db.session.add(OperationLog(
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        message=message,
        success=success,
    ))
    db.session.commit()

    level = logging.INFO if success else logging.WARNING
    app.logger.log(
        level,
        'op=%s success=%s target=%s:%s name=%s msg=%s',
        operation_type,
        success,
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
    return len((expr or '').split()) == 5


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
    except Exception:
        pass


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


def qb_session(downloader):
    session_obj = requests.Session()
    proxies = _outbound_proxies(getattr(downloader, 'proxy_url', '') or '')
    if proxies:
        session_obj.proxies.update(proxies)
    if downloader.username and downloader.encrypted_password:
        login_url = f"{downloader.host}:{downloader.port}/api/v2/auth/login"
        resp = session_obj.post(login_url, data={'username': downloader.username, 'password': downloader.get_password()}, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])
        resp.raise_for_status()
    return session_obj


def list_torrents(downloader):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []
    try:
        s = qb_session(downloader)
        url = f"{downloader.host}:{downloader.port}/api/v2/torrents/info"
        resp = s.get(url, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        app.logger.error(f'list torrents failed: {exc}')
        return None




def list_torrent_files(downloader, torrent_hash):
    if not downloader or not downloader.enabled:
        return None
    if downloader.type != 'qbittorrent':
        return []
    try:
        sess = qb_session(downloader)
        url = f"{downloader.host}:{downloader.port}/api/v2/torrents/files"
        resp = sess.get(url, params={'hash': torrent_hash}, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        app.logger.error(f'list torrent files failed: {exc}')
        return None

def delete_torrent(downloader, torrent_hash):
    if downloader.type != 'qbittorrent':
        return False
    try:
        s = qb_session(downloader)
        url = f"{downloader.host}:{downloader.port}/api/v2/torrents/delete"
        delete_files = get_config('delete_files_with_torrent', 'false') == 'true'
        resp = s.post(url, data={'hashes': torrent_hash, 'deleteFiles': 'true' if delete_files else 'false'}, timeout=app.config['REQUEST_TIMEOUT_SECONDS'])
        resp.raise_for_status()
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

    downloader = db.session.get(Downloader, task.delete_downloader_id) if task.delete_downloader_id else None
    notifier = db.session.get(Notifier, task.delete_notifier_id) if task.delete_notifier_id else None

    class _ProxyTask:
        pass

    deleted_torrents = 0
    hit_total = 0
    pending_total = 0

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

    def _run_for_root(root_dir):
        nonlocal deleted_torrents, hit_total, pending_total
        proxy = _ProxyTask()
        proxy.id = task.id
        proxy.name = f'{task.name}:删除联动'
        proxy.directory = root_dir
        proxy.downloader = downloader
        proxy.notifier = notifier
        proxy.cooldown_seconds = int(task.delete_cooldown_seconds or 120)
        proxy.max_deletes_per_run = int(task.delete_max_deletes_per_run or 20)
        proxy.dry_run = bool(task.delete_dry_run)
        proxy.notify_on_delete = bool(task.delete_notify_on_delete)
        proxy.notify_on_risky_delete = bool(task.delete_notify_on_risky_delete)

        rows = FileLinkMap.query.filter(FileLinkMap.task_id == task.id, FileLinkMap.deleted_at.is_(None)).all()
        ok, deleted_cnt, hit_cnt, pending_cnt = scan_delete_rows(
            proxy,
            rows,
            try_match_torrent_by_mapping_or_name,
            delete_torrent,
            log_operation,
            get_config,
            send_telegram_notification,
            _create_pending_action,
            should_stop=should_stop,
        )
        deleted_torrents += int(deleted_cnt or 0)
        hit_total += int(hit_cnt or 0)
        pending_total += int(pending_cnt or 0)
        return ok

    if task.monitor_source_delete:
        if not _run_for_root(task.source_dir):
            db.session.commit()
            return False, f'删除联动已阻断：检测删除 {hit_total}，删种 {deleted_torrents}，待确认 {pending_total}'

    if task.monitor_dest_delete:
        if not _run_for_root(task.dest_dir):
            db.session.commit()
            return False, f'删除联动已阻断：检测删除 {hit_total}，删种 {deleted_torrents}，待确认 {pending_total}'

    db.session.commit()
    return True, f'删除联动完成：检测删除 {hit_total}，联动删种 {deleted_torrents}，待确认 {pending_total}'


def scan_delete_task(task_id, should_stop=None):
    task = db.session.get(DeleteMonitorTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    monitor_root = str(Path(task.directory).resolve(strict=False))
    rows = FileLinkMap.query.filter(FileLinkMap.deleted_at.is_(None)).all()
    rows = [
        row for row in rows
        if _is_path_within_root(Path(row.source_path or ''), Path(monitor_root))
        or _is_path_within_root(Path(row.dest_path or ''), Path(monitor_root))
    ]

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
        return False, '超过单次删除阈值，已阻断执行'
    return True, f'检测删除 {hit_count} 条，联动删除种子 {deleted_torrents} 条，待确认 {pending_count} 条'
def scan_backfill_task(downloader_id=None, limit=500, should_stop=None):
    query = FileLinkMap.query.filter(FileLinkMap.torrent_hash.is_(None), FileLinkMap.deleted_at.is_(None), db.func.coalesce(FileLinkMap.backfill_fail_count, 0) <= 2)
    if downloader_id:
        query = query.filter(FileLinkMap.downloader_id == downloader_id)
    rows = query.limit(limit).all()

    def resolve_downloader(did):
        if did:
            return db.session.get(Downloader, did)
        return Downloader.query.filter_by(enabled=True, type='qbittorrent').first()

    matched, conflicts, skipped = scan_backfill_rows(rows, resolve_downloader, list_torrents, list_torrent_files, log_operation, should_stop=should_stop, max_failures=2)
    db.session.commit()
    return True, f'回填成功 {matched}，冲突 {conflicts}，跳过 {skipped}'
def is_stop_requested(run_key):
    with RUN_LOCK:
        return run_key in STOP_REQUESTED_KEYS


def request_stop_by_execution(execution_id):
    with RUN_LOCK:
        for key, meta in RUNNING_META.items():
            if meta.get('execution_id') == execution_id:
                STOP_REQUESTED_KEYS.add(key)
                return True, meta
    return False, None


def get_running_executions_snapshot():
    with RUN_LOCK:
        return [dict(v) for _, v in RUNNING_META.items()]


def is_run_key_active(run_key):
    with RUN_LOCK:
        return run_key in RUNNING_KEYS


def get_run_meta(run_key):
    with RUN_LOCK:
        meta = RUNNING_META.get(run_key)
        return dict(meta) if meta else None


def _start_execution(job_name, job_type, source='manual', target_id=None):
    started_at = datetime.now(UTC)
    record = JobExecutionLog(
        job_name=job_name,
        job_type=job_type,
        source=source,
        target_id=target_id,
        status='running',
        started_at=started_at,
    )
    db.session.add(record)
    db.session.commit()
    return record, started_at


def _finish_execution(record, started_at, ok, message):
    finished_at = datetime.now(UTC)
    record.status = 'success' if ok else 'failed'
    record.message = message
    record.finished_at = finished_at
    record.duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    db.session.commit()


def execute_with_guard(run_key, job_name, job_type, runner, source='manual', target_id=None):
    with RUN_LOCK:
        if run_key in RUNNING_KEYS:
            meta = RUNNING_META.get(run_key, {})
            started = meta.get('started_at')
            if started:
                elapsed = int((datetime.now(UTC) - started).total_seconds())
                return False, f'任务正在执行中（已运行 {elapsed} 秒），请稍后重试'
            return False, '任务正在执行中，请稍后重试'
        RUNNING_KEYS.add(run_key)
        STOP_REQUESTED_KEYS.discard(run_key)

    record = None
    started_at = None
    try:
        record, started_at = _start_execution(job_name, job_type, source=source, target_id=target_id)
        with RUN_LOCK:
            RUNNING_META[run_key] = {
                'run_key': run_key,
                'execution_id': record.id,
                'job_name': job_name,
                'job_type': job_type,
                'source': source,
                'target_id': target_id,
                'started_at': started_at,
            }

        ok, message = runner(lambda: is_stop_requested(run_key))
        _finish_execution(record, started_at, ok, message)
        return ok, message
    except Exception as exc:
        db.session.rollback()
        err = f'执行异常: {exc}'
        if record and started_at:
            try:
                _finish_execution(record, started_at, False, err)
            except Exception:
                db.session.rollback()
        log_operation('job_execute_failed', 'Job', target_id, job_name, err, False)
        return False, err
    finally:
        with RUN_LOCK:
            RUNNING_KEYS.discard(run_key)
            RUNNING_META.pop(run_key, None)
            STOP_REQUESTED_KEYS.discard(run_key)


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
    scheduler.add_job(run_cron_job, 'cron', id=key, args=[job_id], minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])


def _ensure_schema_meta_table():
    db.session.execute(db.text("CREATE TABLE IF NOT EXISTS schema_meta (key VARCHAR(100) PRIMARY KEY, value VARCHAR(200))"))


def _get_schema_meta_value(key, default=''):
    _ensure_schema_meta_table()
    row = db.session.execute(db.text("SELECT value FROM schema_meta WHERE key=:k"), {'k': key}).fetchone()
    return str(row[0]) if row and row[0] is not None else default


def _set_schema_meta_value(key, value):
    _ensure_schema_meta_table()
    db.session.execute(db.text("DELETE FROM schema_meta WHERE key=:k"), {'k': key})
    db.session.execute(db.text("INSERT INTO schema_meta(key, value) VALUES (:k, :v)"), {'k': key, 'v': str(value)})


def _delete_schema_meta_key(key):
    _ensure_schema_meta_table()
    db.session.execute(db.text("DELETE FROM schema_meta WHERE key=:k"), {'k': key})


def _get_schema_version():
    try:
        return int(_get_schema_meta_value('db_schema_version', '0') or '0')
    except Exception:
        return 0


def _set_schema_version(version):
    _set_schema_meta_value('db_schema_version', str(version))


def _migration_v1():
    needed = {
        'hardlink_task': {
            'exclude_extensions': "ALTER TABLE hardlink_task ADD COLUMN exclude_extensions VARCHAR(500) DEFAULT ''",
            'min_file_age_seconds': 'ALTER TABLE hardlink_task ADD COLUMN min_file_age_seconds INTEGER DEFAULT 300',
        },
        'delete_monitor_task': {
            'cooldown_seconds': 'ALTER TABLE delete_monitor_task ADD COLUMN cooldown_seconds INTEGER DEFAULT 120',
            'max_deletes_per_run': 'ALTER TABLE delete_monitor_task ADD COLUMN max_deletes_per_run INTEGER DEFAULT 20',
            'dry_run': 'ALTER TABLE delete_monitor_task ADD COLUMN dry_run BOOLEAN DEFAULT 0',
        },
    }
    for table, fields in needed.items():
        existing = {row[1] for row in db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()}
        for col, sql in fields.items():
            if col not in existing:
                db.session.execute(db.text(sql))


def _migration_v2():
    db.session.execute(db.text("CREATE TABLE IF NOT EXISTS delete_pending_action (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, file_map_id INTEGER NOT NULL, deleted_path VARCHAR(1000) NOT NULL, torrent_hash VARCHAR(64), match_by VARCHAR(50) DEFAULT 'no_match', status VARCHAR(20) DEFAULT 'pending', reason VARCHAR(500), created_at DATETIME, confirmed_at DATETIME)"))


def _migration_v3():
    needed = {
        'downloader': {
            'proxy_url': 'ALTER TABLE downloader ADD COLUMN proxy_url VARCHAR(300)',
        },
        'notifier': {
            'proxy_url': 'ALTER TABLE notifier ADD COLUMN proxy_url VARCHAR(300)',
        },
        'file_link_map': {
            'source_type': "ALTER TABLE file_link_map ADD COLUMN source_type VARCHAR(20) DEFAULT 'manual'",
        },
    }
    for table, fields in needed.items():
        existing = {row[1] for row in db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()}
        for col, sql in fields.items():
            if col not in existing:
                db.session.execute(db.text(sql))

    db.session.execute(db.text("UPDATE file_link_map SET source_type = CASE WHEN torrent_hash IS NOT NULL AND TRIM(torrent_hash) <> '' THEN 'downloader' ELSE 'manual' END WHERE source_type IS NULL OR TRIM(source_type) = ''"))




def _migration_v4():
    needed = {
        'delete_monitor_task': {
            'notify_on_delete': 'ALTER TABLE delete_monitor_task ADD COLUMN notify_on_delete BOOLEAN DEFAULT 1',
            'notify_on_risky_delete': 'ALTER TABLE delete_monitor_task ADD COLUMN notify_on_risky_delete BOOLEAN DEFAULT 1',
        },
    }
    for table, fields in needed.items():
        existing = {row[1] for row in db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()}
        for col, sql in fields.items():
            if col not in existing:
                db.session.execute(db.text(sql))

def _migration_v5():
    # Add commonly used indexes to speed up logs/mapping pages on larger datasets.
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_created_at ON operation_log(created_at)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_type ON operation_log(operation_type)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_success_created ON operation_log(success, created_at)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_created_at ON file_link_map(created_at)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_hardlink_cache_created_at ON hardlink_cache(created_at)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_delete_pending_status_created ON delete_pending_action(status, created_at)'))
    db.session.execute(db.text('CREATE INDEX IF NOT EXISTS idx_job_execution_started_at ON job_execution_log(started_at)'))


def _migration_v6():
    needed = {
        'file_link_map': {
            'backfill_fail_count': 'ALTER TABLE file_link_map ADD COLUMN backfill_fail_count INTEGER DEFAULT 0',
            'backfill_last_attempt_at': 'ALTER TABLE file_link_map ADD COLUMN backfill_last_attempt_at DATETIME',
        },
    }
    for table, fields in needed.items():
        existing = {row[1] for row in db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()}
        for col, sql in fields.items():
            if col not in existing:
                db.session.execute(db.text(sql))
    db.session.execute(db.text("CREATE INDEX IF NOT EXISTS idx_file_link_map_backfill_fail_count ON file_link_map(backfill_fail_count)"))


def pre_migration_backup_if_needed(target_schema):
    # Create one-off backup on first run of a new app version before schema migration.
    current_version = app.config.get('APP_VERSION', 'dev')
    last_version = _get_schema_meta_value('last_app_version', '')
    current_schema = _get_schema_version()

    if current_schema >= target_schema:
        return True, '当前数据库结构已是目标版本，无需升级前备份'
    if last_version == current_version:
        return True, '当前版本已执行过启动流程，无需重复升级前备份'

    backup_done_for = _get_schema_meta_value('pre_migration_backup_for_version', '')
    backup_path_done = _get_schema_meta_value('pre_migration_backup_path', '')
    if backup_done_for == current_version and backup_path_done:
        return True, f'已存在本版本升级前备份: {backup_path_done}'

    db_file = Path(app.instance_path) / 'hardlink_manager.db'
    if not db_file.exists():
        return True, '数据库文件尚不存在，跳过升级前备份'

    backup_base = (get_config('backup_dir', '/app/data/backups') or '/app/data/backups').strip()
    backup_dir = str(Path(backup_base) / 'migration-pre')

    keep_last = int(get_config('backup_keep_last', '7') or '7')
    ok, msg, backup_path = run_sqlite_backup(str(db_file), backup_dir, keep_last=max(1, keep_last))
    if not ok:
        _set_schema_meta_value('last_migration_status', 'backup_failed')
        _set_schema_meta_value('last_migration_error', msg)
        db.session.commit()
        return False, msg

    stamped = datetime.now(UTC).isoformat()
    _set_schema_meta_value('pre_migration_backup_for_version', current_version)
    _set_schema_meta_value('pre_migration_backup_path', backup_path or '-')
    _set_schema_meta_value('pre_migration_backup_at', stamped)
    _set_schema_meta_value('last_migration_status', 'backup_ok')
    _delete_schema_meta_key('last_migration_error')
    db.session.commit()
    app.logger.info('pre migration backup created for %s: %s', current_version, backup_path)
    return True, f'升级前备份完成: {backup_path}'


def _migration_v7():
    needed = {
        'hardlink_task': {
            'monitor_source_delete': 'ALTER TABLE hardlink_task ADD COLUMN monitor_source_delete BOOLEAN DEFAULT 1',
            'monitor_dest_delete': 'ALTER TABLE hardlink_task ADD COLUMN monitor_dest_delete BOOLEAN DEFAULT 1',
            'delete_downloader_id': 'ALTER TABLE hardlink_task ADD COLUMN delete_downloader_id INTEGER',
            'delete_notifier_id': 'ALTER TABLE hardlink_task ADD COLUMN delete_notifier_id INTEGER',
            'delete_cooldown_seconds': 'ALTER TABLE hardlink_task ADD COLUMN delete_cooldown_seconds INTEGER DEFAULT 120',
            'delete_max_deletes_per_run': 'ALTER TABLE hardlink_task ADD COLUMN delete_max_deletes_per_run INTEGER DEFAULT 20',
            'delete_dry_run': 'ALTER TABLE hardlink_task ADD COLUMN delete_dry_run BOOLEAN DEFAULT 0',
            'delete_notify_on_delete': 'ALTER TABLE hardlink_task ADD COLUMN delete_notify_on_delete BOOLEAN DEFAULT 1',
            'delete_notify_on_risky_delete': 'ALTER TABLE hardlink_task ADD COLUMN delete_notify_on_risky_delete BOOLEAN DEFAULT 1',
        },
    }
    for table, fields in needed.items():
        existing = {row[1] for row in db.session.execute(db.text(f'PRAGMA table_info({table})')).fetchall()}
        for col, sql in fields.items():
            if col not in existing:
                db.session.execute(db.text(sql))


def ensure_compat_columns():
    # Versioned, idempotent migrations. Supports forward upgrades safely.
    # For downgrades, restore DB from backup before running older code.
    target = 7
    current = _get_schema_version()
    if current > target:
        app.logger.warning('db schema version %s is newer than app target %s; running in compatibility mode', current, target)
        return

    migrations = {1: _migration_v1, 2: _migration_v2, 3: _migration_v3, 4: _migration_v4, 5: _migration_v5, 6: _migration_v6, 7: _migration_v7}
    for version in range(current + 1, target + 1):
        migrations[version]()
        _set_schema_version(version)
        app.logger.info('db migration applied: v%s', version)

    db.session.commit()


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
    ]
    for key, value, desc in default_configs:
        if not AppConfig.query.filter_by(key=key).first():
            set_config(key, value, desc)


def init_system_jobs():
    if not CronJob.query.filter_by(name='系统日志清理').first():
        db.session.add(CronJob(name='系统日志清理', task_type='clean_logs', cron_expression='0 3 * * *', description='每天凌晨3点自动清理日志'))
    if not CronJob.query.filter_by(name='系统缓存清理').first():
        db.session.add(CronJob(name='系统缓存清理', task_type='clean_cache', cron_expression='30 3 * * *', description='每天凌晨3:30清理缓存'))
    backfill_job = CronJob.query.filter_by(name='系统映射回填').first()
    if not backfill_job:
        db.session.add(CronJob(name='系统映射回填', task_type='backfill_mapping', cron_expression='0 * * * *', description='每60分钟回填文件与种子映射'))
    else:
        if backfill_job.cron_expression == '*/30 * * * *':
            backfill_job.cron_expression = '0 * * * *'
            backfill_job.description = '每60分钟回填文件与种子映射'
    if not CronJob.query.filter_by(name='系统数据库备份').first():
        db.session.add(CronJob(name='系统数据库备份', task_type='db_backup', cron_expression='0 */6 * * *', description='每6小时执行一次数据库备份'))
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
    if app.config['SECRET_KEY'] == 'default-secret-key-for-dev-only':
        app.logger.warning('Using default SECRET_KEY; set SECRET_KEY in production to avoid session forgery risk.')

    with app.app_context():
        db.create_all()
        init_defaults()
        backup_ok, backup_msg = pre_migration_backup_if_needed(target_schema=7)
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
