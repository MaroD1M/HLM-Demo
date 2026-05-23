from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
import json
import urllib.request
import zipfile


SENSITIVE_KEY_HINTS = (
    'password',
    'secret',
    'token',
    'passphrase',
    'api_key',
    'apikey',
)

SENSITIVE_KEY_EXACT = {
    'APP_PASSWORD',
    'SECRET_KEY',
    'critical_action_passphrase',
    'dev_git_token',
    'security_2fa_secret',
    'webhook_secret',
    'api_access_token',
}


def _human_size(size_bytes):
    size = float(max(0, int(size_bytes or 0)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
        size /= 1024
    return f'{int(size_bytes or 0)} B'


def _safe_file_size(path_obj: Path):
    try:
        if path_obj.exists() and path_obj.is_file():
            return path_obj.stat().st_size
    except Exception:
        pass
    return None


def _safe_dir_size(path_obj: Path):
    total = 0
    files = 0
    try:
        if not path_obj.exists() or not path_obj.is_dir():
            return 0, 0
        for child in path_obj.rglob('*'):
            if child.is_file():
                try:
                    total += child.stat().st_size
                    files += 1
                except Exception:
                    continue
    except Exception:
        return None, None
    return total, files


def _check_writable(path_obj: Path):
    try:
        path_obj.mkdir(parents=True, exist_ok=True)
        probe = path_obj / '.hlm_write_probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _schema_meta_get(db, key, default=''):
    try:
        row = db.session.execute(db.text("SELECT value FROM schema_meta WHERE key=:k"), {'k': key}).fetchone()
        return str(row[0]) if row and row[0] is not None else str(default)
    except Exception:
        return str(default)


def _probe_url(url, timeout=3):
    try:
        req = urllib.request.Request(url, method='HEAD')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, 'status', 200) or 200
        return True, f'HTTP {code}'
    except Exception as exc:
        return False, str(exc)


def _parse_backfill_metrics_message(message):
    metrics = {}
    text = str(message or '').strip()
    if not text:
        return metrics
    for item in text.split(';'):
        if '=' not in item:
            continue
        k, v = item.split('=', 1)
        key = k.strip()
        val = v.strip()
        if key:
            metrics[key] = val
    return metrics


def _is_sensitive_key(key):
    key_text = str(key or '').strip()
    if not key_text:
        return False
    lowered = key_text.lower()
    if key_text in SENSITIVE_KEY_EXACT:
        return True
    return any(token in lowered for token in SENSITIVE_KEY_HINTS)


def _redact_kv_message(message):
    text = str(message or '').strip()
    if not text:
        return text
    parts = []
    for chunk in text.split(';'):
        if '=' not in chunk:
            parts.append(chunk)
            continue
        key, value = chunk.split('=', 1)
        key = key.strip()
        value = value.strip()
        if _is_sensitive_key(key):
            value = '***'
        parts.append(f'{key}={value}')
    return ';'.join(parts)


def _redact_config_rows(rows):
    def _get(row, key, default=''):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    items = []
    for row in rows:
        key = str(_get(row, 'key', '') or '')
        value = _get(row, 'value', '')
        updated_at = _get(row, 'updated_at', None)
        items.append({
            'key': key,
            'value': '***' if _is_sensitive_key(key) and value else value,
            'description': _get(row, 'description', '') or '',
            'updated_at': updated_at.isoformat() if updated_at and hasattr(updated_at, 'isoformat') else None,
        })
    return items


def _redact_operation_rows(rows):
    items = []
    for row in rows:
        items.append({
            'id': row.id,
            'operation_type': row.operation_type,
            'target_type': row.target_type,
            'target_id': row.target_id,
            'target_name': row.target_name,
            'success': row.success,
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'message': _redact_kv_message(row.message),
        })
    return items


def _redact_job_rows(rows):
    items = []
    for row in rows:
        items.append({
            'id': row.id,
            'job_name': row.job_name,
            'job_type': row.job_type,
            'source': row.source,
            'status': row.status,
            'started_at': row.started_at.isoformat() if row.started_at else None,
            'finished_at': row.finished_at.isoformat() if row.finished_at else None,
            'duration_ms': row.duration_ms,
            'message': _redact_kv_message(row.message),
        })
    return items


def _dt_to_iso(value):
    if not value:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _latest_log_summary(rows):
    if not rows:
        return {'count': 0, 'failed': 0, 'latest_at': None, 'latest_type': '-', 'latest_message': '-'}
    failed = sum(1 for row in rows if not row.success)
    latest = rows[0]
    return {
        'count': len(rows),
        'failed': failed,
        'latest_at': latest.created_at.isoformat() if latest.created_at else None,
        'latest_type': latest.operation_type or '-',
        'latest_message': _redact_kv_message(latest.message) or '-',
    }


def _build_schema_summary(db):
    try:
        row = db.session.execute(db.text("SELECT value FROM schema_meta WHERE key='db_schema_version'")).fetchone()
        current_schema = str(row[0]) if row and row[0] is not None else '0'
    except Exception:
        current_schema = '0'

    try:
        from core.services.migration_service import MigrationService
        target_schema = MigrationService.get_target_schema_version()
    except Exception:
        target_schema = 0

    try:
        current_schema_num = int(current_schema)
    except Exception:
        current_schema_num = 0

    if current_schema_num == target_schema:
        schema_state = '已是最新版本'
    elif current_schema_num < target_schema:
        schema_state = '低于目标版本（通常重启后会自动升级）'
    else:
        schema_state = '高于应用目标版本（兼容模式）'

    migration_status = _schema_meta_get(db, 'last_migration_status', 'unknown')
    migration_error = _schema_meta_get(db, 'last_migration_error', '')
    migration_at = _schema_meta_get(db, 'pre_migration_backup_at', '')
    migration_detail = f'状态={migration_status}'
    if migration_at:
        migration_detail += f'，最近升级前备份时间={migration_at}'
    if migration_error:
        migration_detail += f'，错误={migration_error}'

    return {
        'current_version': current_schema_num,
        'target_version': target_schema,
        'state': schema_state,
        'migration_status': migration_status,
        'migration_detail': migration_detail,
    }


def collect_diagnostics(*, db, current_app, get_config, get_release_info, get_running_executions_snapshot, models, panel_view='overview'):
    OperationLog = models['OperationLog']
    FileLinkMap = models['FileLinkMap']
    AppConfig = models['AppConfig']
    JobExecutionLog = models['JobExecutionLog']
    AppConfigSnapshot = models['AppConfigSnapshot']
    HardlinkTask = models['HardlinkTask']
    DeleteMonitorTask = models['DeleteMonitorTask']
    Downloader = models['Downloader']
    Notifier = models['Notifier']
    CronJob = models['CronJob']

    checks = []
    try:
        db.session.execute(db.text('SELECT 1')).fetchone()
        checks.append({'name': '数据库连接', 'ok': True, 'detail': '正常'})
    except Exception as exc:
        checks.append({'name': '数据库连接', 'ok': False, 'detail': str(exc)})

    schema = _build_schema_summary(db)
    checks.append({'name': '数据库结构状态', 'ok': True, 'detail': schema['state']})
    checks.append({'name': '数据库结构版本（内部）', 'ok': True, 'detail': f"当前 {schema['current_version']}，目标 {schema['target_version']}"})
    checks.append({'name': '最近迁移状态', 'ok': schema['migration_status'] in {'success', 'backup_ok', 'unknown'}, 'detail': schema['migration_detail']})

    db_file = Path(current_app.instance_path) / 'hardlink_manager.db'
    db_size_bytes = _safe_file_size(db_file)
    if db_size_bytes is None:
        checks.append({'name': '数据库文件大小', 'ok': False, 'detail': f'未找到数据库文件：{db_file}'})
    else:
        checks.append({'name': '数据库文件大小', 'ok': True, 'detail': f'{_human_size(db_size_bytes)} ({db_file})'})

    logs_dir = Path('data/logs').resolve()
    logs_size, logs_files = _safe_dir_size(logs_dir)
    if logs_size is None:
        checks.append({'name': '日志目录占用', 'ok': False, 'detail': f'统计失败：{logs_dir}'})
    else:
        checks.append({'name': '日志目录占用', 'ok': True, 'detail': f'{_human_size(logs_size)}，{logs_files} 个文件'})

    log_max_mb = str(get_config('app_log_max_mb', '10') or '10').strip()
    log_keep = str(get_config('app_log_backup_count', '5') or '5').strip()
    log_days = str(get_config('log_retention_days', '30') or '30').strip()
    checks.append({'name': '日志保留策略', 'ok': True, 'detail': f'单文件上限 {log_max_mb}MB，滚动保留 {log_keep} 份，清理保留天数 {log_days} 天'})

    backup_dir = Path((get_config('backup_dir', '/app/data/backups') or '/app/data/backups').strip()).resolve()
    backup_files = []
    try:
        from core.services.backup_service import list_backup_files
        backup_files = list_backup_files(str(backup_dir))
    except Exception:
        backup_files = []

    instance_dir = Path(current_app.instance_path)
    checks.append({'name': '目录可写性(instance)', 'ok': _check_writable(instance_dir), 'detail': str(instance_dir)})
    checks.append({'name': '目录可写性(logs)', 'ok': _check_writable(logs_dir), 'detail': str(logs_dir)})
    checks.append({'name': '目录可写性(backup)', 'ok': _check_writable(backup_dir), 'detail': str(backup_dir)})
    if backup_files:
        latest_backup = backup_files[0]
        latest_ok = bool(latest_backup.get('ok'))
        latest_msg = latest_backup.get('message', '-') or '-'
        if latest_backup.get('legacy_without_manifest'):
            latest_ok = True
            latest_msg = f"{latest_msg}（兼容旧备份，建议新建一次备份生成清单）"
        checks.append({'name': '最近备份', 'ok': latest_ok, 'detail': f"{latest_backup.get('name', '-')}: {latest_msg}"})
    else:
        checks.append({'name': '最近备份', 'ok': False, 'detail': '暂无备份文件'})

    version_check_enabled = str(get_config('github_version_check_enabled', 'true') or 'true').strip().lower() == 'true'
    github_api_base = (get_config('github_api_base', 'https://api.github.com') or 'https://api.github.com').strip()
    if version_check_enabled and github_api_base:
        ok_probe, probe_detail = _probe_url(github_api_base, timeout=3)
        checks.append({'name': '版本检查地址可达性', 'ok': ok_probe, 'detail': f'{github_api_base} -> {probe_detail}'})
    else:
        checks.append({'name': '版本检查地址可达性', 'ok': True, 'detail': '已禁用版本检查或未配置地址'})

    checks.append({'name': '代理配置', 'ok': True, 'detail': (get_config('proxy_url', '') or '').strip() or '未设置（直连）'})
    checks.append({'name': '应用版本', 'ok': True, 'detail': get_release_info().get('local_version', '-')})
    checks.append({'name': '日志目录', 'ok': True, 'detail': str(logs_dir)})
    checks.append({'name': '待判定来源保护', 'ok': True, 'detail': f"enabled={get_config('pending_source_guard_enabled', 'true')}, window={get_config('pending_source_guard_seconds', '900')}s, log_mode={get_config('pending_source_log_mode', 'aggregate')}"})

    pending_count = FileLinkMap.query.filter(FileLinkMap.source_type == 'pending', FileLinkMap.deleted_at.is_(None)).count()
    try:
        pending_warn_threshold = int((get_config('pending_source_warn_threshold', '200') or '200').strip())
    except Exception:
        pending_warn_threshold = 200
    pending_warn_threshold = max(1, pending_warn_threshold)
    checks.append({'name': '待判定映射数量', 'ok': pending_count < pending_warn_threshold, 'detail': f'{pending_count} (threshold={pending_warn_threshold})'})

    pending_events = OperationLog.query.filter_by(operation_type='delete_pending_source').order_by(OperationLog.created_at.desc()).limit(5).all()
    backfill_metrics_logs = OperationLog.query.filter_by(operation_type='backfill_metrics').order_by(OperationLog.created_at.desc()).limit(5).all()
    recent_operation_rows = OperationLog.query.order_by(OperationLog.created_at.desc()).limit(20).all()
    recent_job_rows = JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc()).limit(20).all()

    backfill_metrics_rows = [
        {
            'created_at': row.created_at,
            'metrics': _parse_backfill_metrics_message(row.message),
            'message': row.message or '',
        }
        for row in backfill_metrics_logs
    ]

    running_snaps = get_running_executions_snapshot()
    now = datetime.now(UTC)
    running_rows = []
    for snap in running_snaps:
        started = snap.get('started_at')
        elapsed = int((now - started).total_seconds()) if started else 0
        running_rows.append({
            'id': snap.get('execution_id'),
            'job_name': snap.get('job_name') or '-',
            'job_type': snap.get('job_type') or '-',
            'source': snap.get('source') or '-',
            'elapsed_seconds': max(0, elapsed),
            'target_id': snap.get('target_id'),
            'has_snapshot': True,
        })

    config_rows = AppConfig.query.order_by(AppConfig.key.asc()).all()
    recent_24h_cutoff = datetime.now(UTC) - timedelta(days=1)

    def _row_dt(value):
        if not value:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    failed_operations_24h = sum(1 for row in recent_operation_rows if not row.success and _row_dt(row.created_at) and _row_dt(row.created_at) >= recent_24h_cutoff)
    operations_24h = sum(1 for row in recent_operation_rows if _row_dt(row.created_at) and _row_dt(row.created_at) >= recent_24h_cutoff)
    failed_jobs = sum(1 for row in recent_job_rows if str(row.status or '').lower() not in {'success', 'done', 'finished', 'ok'})

    counts = {
        'hardlink_tasks': HardlinkTask.query.count(),
        'delete_monitor_tasks': DeleteMonitorTask.query.count(),
        'downloaders': Downloader.query.count(),
        'notifiers': Notifier.query.count(),
        'cron_jobs': CronJob.query.count(),
        'file_link_maps': FileLinkMap.query.count(),
        'pending_events': len(pending_events),
        'running_jobs': len(running_rows),
        'backfill_metrics': len(backfill_metrics_rows),
        'config_items': len(config_rows),
        'snapshots': AppConfigSnapshot.query.count(),
        'operations_24h': operations_24h,
        'failed_operations_24h': failed_operations_24h,
        'job_rows': len(recent_job_rows),
        'failed_jobs': failed_jobs,
    }

    storage = {
        'db_file': str(db_file),
        'db_size_bytes': db_size_bytes,
        'db_size_human': _human_size(db_size_bytes) if db_size_bytes is not None else '-',
        'logs_dir': str(logs_dir),
        'logs_size_bytes': logs_size,
        'logs_size_human': _human_size(logs_size) if logs_size is not None else '-',
        'logs_files': logs_files,
        'backup_dir': str(backup_dir),
        'backup_count': len(backup_files),
        'backup_latest': backup_files[0] if backup_files else None,
    }

    release_info = get_release_info()
    health_summary = summarize_runtime_health({
        'checks': checks,
        'counts': counts,
        'storage': storage,
        'schema': schema,
        'release': release_info,
        'pending_events': pending_events,
        'running_rows': running_rows,
        'backup_files': backup_files,
        'recent_operations': recent_operation_rows,
        'recent_job_rows': recent_job_rows,
    })

    return {
        'panel_view': panel_view,
        'generated_at': datetime.now(UTC).isoformat(),
        'checks': checks,
        'running_rows': running_rows,
        'pending_events': pending_events,
        'backfill_metrics_rows': backfill_metrics_rows,
        'backup_files': backup_files,
        'config_rows': config_rows,
        'recent_operations': recent_operation_rows,
        'recent_job_rows': recent_job_rows,
        'counts': counts,
        'storage': storage,
        'schema': schema,
        'release': release_info,
        'health_summary': health_summary,
    }


def summarize_runtime_health(diagnostics):
    checks = diagnostics.get('checks') or []
    counts = diagnostics.get('counts') or {}
    storage = diagnostics.get('storage') or {}
    schema = diagnostics.get('schema') or {}
    release = diagnostics.get('release') or {}

    failed_checks = [item for item in checks if not item.get('ok')]
    status = 'healthy'
    if failed_checks:
        status = 'degraded'

    detail = '所有检查通过'
    if failed_checks:
        detail = failed_checks[0].get('detail') or failed_checks[0].get('name') or '存在异常'

    backup_latest = storage.get('backup_latest') or {}
    labels = {
        'healthy': '正常',
        'degraded': '需要关注',
        'critical': '严重异常',
    }
    if storage.get('db_size_bytes') is None or storage.get('logs_size_bytes') is None:
        status = 'critical' if failed_checks else 'degraded'

    return {
        'status': status,
        'label': labels.get(status, status),
        'detail': detail,
        'summary': f"数据库 {storage.get('db_size_human', '-')}, 日志 {storage.get('logs_size_human', '-')}, 备份 {storage.get('backup_count', 0)} 份",
        'metrics': {
            'schema_state': schema.get('state', '-'),
            'schema_version': schema.get('current_version', 0),
            'schema_target': schema.get('target_version', 0),
            'running_jobs': counts.get('running_jobs', 0),
            'pending_events': counts.get('pending_events', 0),
            'backup_count': storage.get('backup_count', 0),
            'backup_ok': bool(backup_latest.get('ok')) if isinstance(backup_latest, dict) else bool(backup_latest),
            'operations_24h': counts.get('operations_24h', 0),
            'failed_operations_24h': counts.get('failed_operations_24h', 0),
            'failed_jobs': counts.get('failed_jobs', 0),
            'release': release.get('local_version', '-'),
        },
    }


def build_support_bundle(diagnostics, bundle_format='zip'):
    payload = {
        '__meta': {
            'format_version': 1,
            'kind': 'support_bundle',
            'generated_at': diagnostics.get('generated_at') or datetime.now(UTC).isoformat(),
            'panel_view': diagnostics.get('panel_view') or 'overview',
            'redacted': True,
        },
        'health_summary': diagnostics.get('health_summary') or {},
        'counts': diagnostics.get('counts') or {},
        'storage': diagnostics.get('storage') or {},
        'schema': diagnostics.get('schema') or {},
        'release': diagnostics.get('release') or {},
        'checks': diagnostics.get('checks') or [],
        'running_rows': diagnostics.get('running_rows') or [],
        'pending_events': [
            {
                'created_at': _dt_to_iso(row.created_at),
                'target_type': row.target_type,
                'target_id': row.target_id,
                'message': _redact_kv_message(row.message),
            }
            for row in diagnostics.get('pending_events') or []
        ],
        'backfill_metrics_rows': diagnostics.get('backfill_metrics_rows') or [],
        'backup_files': [
            {
                **{k: v for k, v in row.items() if k != 'created_at'},
                'created_at': _dt_to_iso(row.get('created_at')),
            }
            for row in (diagnostics.get('backup_files') or [])
        ],
        'config_rows': _redact_config_rows(diagnostics.get('config_rows') or []),
        'recent_operations': _redact_operation_rows((diagnostics.get('recent_operations') or [])[:20]),
        'recent_job_rows': _redact_job_rows((diagnostics.get('recent_job_rows') or [])[:20]),
    }

    fmt = str(bundle_format or 'zip').strip().lower()
    ts = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    stem = f'hlm-support-bundle-{ts}'
    if fmt == 'json':
        return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'), f'{stem}.json', 'application/json', payload

    mem = BytesIO()
    with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('support-bundle.json', json.dumps(payload, ensure_ascii=False, indent=2))
        zf.writestr('README.txt', 'This support bundle is redacted. Sensitive values are masked as ***.')
    return mem.getvalue(), f'{stem}.zip', 'application/zip', payload
