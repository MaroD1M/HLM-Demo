import os
import secrets
from datetime import datetime, UTC, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, request, session, abort
from core.services.backfill_service import scan_backfill_rows
from core.routes.api import init_api_routes
from core.routes.web import init_web_routes
from core.deps import RouteDeps
from core.services.hardlink_service import create_hardlink_for_file as svc_create_hardlink_for_file
from core.services.delete_service import scan_delete_rows
from core.extensions import db, bcrypt, scheduler
from core.models import HardlinkTask, DeleteMonitorTask, Downloader, Notifier, HardlinkCache, FileLinkMap, OperationLog, AppConfig, CronJob


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key-for-dev-only')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hardlink_manager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['APP_USERNAME'] = os.environ.get('APP_USERNAME', '').strip()
app.config['APP_PASSWORD'] = os.environ.get('APP_PASSWORD', '')
app.config['REQUEST_TIMEOUT_SECONDS'] = int(os.environ.get('REQUEST_TIMEOUT_SECONDS', '10'))

db.init_app(app)
bcrypt.init_app(app)


def ensure_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': ensure_csrf_token()}


@app.before_request
def security_guard():
    if request.endpoint == 'static':
        return
    if app.config['APP_USERNAME'] and app.config['APP_PASSWORD']:
        auth = request.authorization
        if not auth or auth.username != app.config['APP_USERNAME'] or auth.password != app.config['APP_PASSWORD']:
            return ('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="HLM"'})
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

        proxy_url = (get_config('tg_proxy_url', '') or '').strip()
        proxies = None
        if proxy_url:
            proxies = {'http': proxy_url, 'https': proxy_url}

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


def scan_hardlink_task(task_id):
    task = db.session.get(HardlinkTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    source = Path(task.source_dir)
    if not source.exists() or not source.is_dir():
        return False, '源目录不存在'

    created = 0
    scanned = 0
    for file_path in source.rglob('*'):
        if not file_path.is_file() or file_path.is_symlink():
            continue
        scanned += 1
        try:
            ok, msg = svc_create_hardlink_for_file(task, file_path, HardlinkCache, FileLinkMap, db, safe_unlink)
            if ok:
                created += 1
        except Exception as exc:
            db.session.rollback()
            log_operation('hardlink_failed', 'HardlinkTask', task.id, task.name, f'{file_path}: {exc}', False)

    db.session.commit()
    log_operation('hardlink_scan', 'HardlinkTask', task.id, task.name, f'扫描 {scanned} 个文件，创建 {created} 个硬链接')
    return True, f'扫描 {scanned}，创建 {created}'


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


def scan_delete_task(task_id):
    task = db.session.get(DeleteMonitorTask, task_id)
    if not task or not task.enabled:
        return False, '任务不存在或已禁用'

    rows = FileLinkMap.query.filter(
        (FileLinkMap.source_path.like(f"{task.directory}%")) | (FileLinkMap.dest_path.like(f"{task.directory}%")),
        FileLinkMap.deleted_at.is_(None)
    ).all()

    ok, deleted_torrents, hit_count = scan_delete_rows(
        task,
        rows,
        try_match_torrent_by_mapping_or_name,
        delete_torrent,
        log_operation,
        get_config,
        send_telegram_notification,
    )
    db.session.commit()

    if not ok:
        return False, '超过单次删除阈值，已阻断执行'
    return True, f'检测删除 {hit_count} 条，联动删除种子 {deleted_torrents} 条'
def scan_backfill_task(downloader_id=None, limit=500):
    query = FileLinkMap.query.filter(FileLinkMap.torrent_hash.is_(None), FileLinkMap.deleted_at.is_(None))
    if downloader_id:
        query = query.filter(FileLinkMap.downloader_id == downloader_id)
    rows = query.limit(limit).all()

    def resolve_downloader(did):
        if did:
            return db.session.get(Downloader, did)
        return Downloader.query.filter_by(enabled=True, type='qbittorrent').first()

    matched, conflicts, skipped = scan_backfill_rows(rows, resolve_downloader, list_torrents, log_operation)
    db.session.commit()
    return True, f'回填成功 {matched}，冲突 {conflicts}，跳过 {skipped}'
def run_cron_job(job_id):
    with app.app_context():
        job = db.session.get(CronJob, job_id)
        if not job or not job.enabled:
            return

        try:
            if job.task_type in {'batch_hardlink', 'hardlink_scan'}:
                ok, msg = scan_hardlink_task(job.target_id)
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'hardlink_scan: {msg}', ok)
            elif job.task_type in {'delete_scan', 'delete_monitor_scan'}:
                ok, msg = scan_delete_task(job.target_id)
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'delete_scan: {msg}', ok)
            elif job.task_type in {'backfill_mapping', 'backfill_torrent_mapping'}:
                ok, msg = scan_backfill_task(job.target_id)
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'backfill: {msg}', ok)
            elif job.task_type == 'clean_logs':
                retention = int(get_config('log_retention_days', '30'))
                cutoff = datetime.now(UTC) - timedelta(days=retention)
                OperationLog.query.filter(OperationLog.created_at < cutoff).delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, f'清理 {retention} 天前日志')
            elif job.task_type == 'clean_cache':
                HardlinkCache.query.delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, '清理缓存成功')
        except Exception as exc:
            db.session.rollback()
            log_operation('cron_failed', 'CronJob', job.id, job.name, str(exc), False)


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


def ensure_compat_columns():
    # Lightweight migration for sqlite; keeps existing db usable without Alembic.
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
    db.session.commit()


def init_defaults():
    default_configs = [
        ('log_retention_days', '30', '日志保留天数'),
        ('auto_clean_logs', 'true', '自动清理日志'),
        ('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv', '默认文件扩展名'),
        ('default_exclude_dirs', 'sample,subs', '默认排除目录'),
        ('delete_files_with_torrent', 'false', '删除种子时同时删除文件'),
        ('delete_delay_seconds', '120', '删除确认冷却秒数'),
        ('notify_on_hardlink', 'false', '启用硬链接通知'),
        ('notify_on_delete', 'true', '启用删除通知'),
        ('allowed_roots', '', '允许访问的路径根目录，逗号分隔'),
        ('tg_proxy_url', '', 'Telegram请求代理地址，如http://127.0.0.1:7890'),
        ('tg_api_base', 'https://api.telegram.org', 'Telegram API基础地址'),
    ]
    for key, value, desc in default_configs:
        if not AppConfig.query.filter_by(key=key).first():
            set_config(key, value, desc)


def init_system_jobs():
    if not CronJob.query.filter_by(name='系统日志清理').first():
        db.session.add(CronJob(name='系统日志清理', task_type='clean_logs', cron_expression='0 3 * * *', description='每天凌晨3点自动清理日志'))
    if not CronJob.query.filter_by(name='系统缓存清理').first():
        db.session.add(CronJob(name='系统缓存清理', task_type='clean_cache', cron_expression='30 3 * * *', description='每天凌晨3:30清理缓存'))
    if not CronJob.query.filter_by(name='系统映射回填').first():
        db.session.add(CronJob(name='系统映射回填', task_type='backfill_mapping', cron_expression='*/30 * * * *', description='每30分钟回填文件与种子映射'))
    db.session.commit()


def init_app():
    if app.config['SECRET_KEY'] == 'default-secret-key-for-dev-only':
        app.logger.warning('Using default SECRET_KEY; set SECRET_KEY in production to avoid session forgery risk.')

    with app.app_context():
        db.create_all()
        ensure_compat_columns()
        init_defaults()
        init_system_jobs()
        start_cron_scheduler()
        app.register_blueprint(init_api_routes(HardlinkTask, DeleteMonitorTask))
        app.register_blueprint(init_web_routes(RouteDeps(
            HardlinkTask=HardlinkTask,
            DeleteMonitorTask=DeleteMonitorTask,
            Downloader=Downloader,
            Notifier=Notifier,
            HardlinkCache=HardlinkCache,
            FileLinkMap=FileLinkMap,
            OperationLog=OperationLog,
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
            update_cron_job=update_cron_job,
            list_torrents=list_torrents,
            send_telegram_notification=send_telegram_notification,
        )))


if __name__ == '__main__':
    init_app()
    app.run(host='0.0.0.0', port=5000, debug=False)
