from pathlib import Path
import json
import re
import time
import os
import threading
from flask import Blueprint, render_template, request, redirect, flash, jsonify, Response
from core.deps import RouteDeps

web_bp = Blueprint('web_bp', __name__)




SETTINGS_SAVE_KEYS = (
    'log_retention_days', 'auto_clean_logs', 'default_extensions', 'default_exclude_dirs',
    'delete_files_with_torrent', 'notify_on_delete',
    'allowed_roots', 'proxy_url', 'tg_api_base', 'backup_dir', 'backup_keep_last', 'notify_on_risky_delete', 'delete_match_strict_mode',
    'manual_dest_delete_delete_source', 'manual_source_delete_delete_dest',
    'downloader_dest_delete_delete_source', 'downloader_source_delete_delete_dest',
    'downloader_dest_delete_delete_torrent', 'downloader_source_delete_delete_torrent',
    'pending_source_guard_enabled', 'pending_source_guard_seconds', 'pending_source_warn_threshold', 'pending_source_log_mode',
    'github_version_check_enabled', 'github_repo', 'github_api_base',
    'app_log_max_mb', 'app_log_backup_count', 'version_check_cache_minutes', 'critical_action_passphrase',
    'backfill_batch_limit', 'backfill_max_candidates', 'backfill_file_fetch_workers', 'backfill_max_failures', 'backfill_path_mappings',
    'dev_mode', 'dev_auto_pull', 'dev_git_repo', 'dev_git_branch', 'dev_auto_pip_sync', 'dev_pip_sync_timeout', 'dev_git_token', 'dev_proxy_url', 'dev_no_proxy',
)

IMPORTABLE_SETTINGS_KEYS = frozenset(SETTINGS_SAVE_KEYS)
DEV_DEFAULT_GIT_REPO = 'https://github.com/MaroD1M/HLM-Demo.git'
OPERATION_TYPE_LABELS = {
    'hardlink_created': '创建硬链接',
    'hardlink_skipped': '跳过硬链接',
    'hardlink_failed': '硬链接失败',
    'hardlink_scan': '硬链接扫描',
    'hardlink_scan_stopped': '硬链接扫描中止',
    'hardlink_task_created': '新建硬链接任务',
    'hardlink_task_updated': '更新硬链接任务',
    'hardlink_task_toggled': '启用/禁用硬链接任务',
    'hardlink_task_deleted': '删除硬链接任务',
    'task_run': '执行任务',
    'task_updated': '更新任务',
    'task_deleted': '删除任务',
    'task_toggled': '启用/禁用任务',
    'delete_scan': '删除联动扫描',
    'delete_scan_stopped': '删除联动扫描中止',
    'delete_detected_manual': '删除联动-检测到手动来源删除',
    'delete_detected_no_downloader': '删除联动-未关联下载器',
    'linked_file_deleted': '删除联动-已删除对侧文件',
    'linked_file_delete_skip': '删除联动-跳过对侧文件',
    'delete_guard_truncated': '删除联动-按阈值截断执行',
    'delete_pending_source': '来源待判定事件',
    'torrent_match_miss': '删除联动-未匹配到种子',
    'torrent_delete_pending': '删除联动-加入待确认',
    'torrent_delete_dry_run': '删除联动-测试删除',
    'torrent_deleted': '删除联动-已删除种子',
    'torrent_delete_failed': '删除联动-删种失败',
    'torrent_delete_disabled': '删除联动-策略禁用删种',
    'pending_delete_confirmed': '确认删种',
    'pending_delete_failed': '确认删种失败',
    'pending_delete_rejected': '驳回待确认',
    'pending_delete_bulk': '批量处理待确认',
    'mapping_record_deleted': '删除映射记录',
    'mapping_backfill_fail_reset': '重置自动关联失败计数',
    'mapping_retry_linked': '映射重试关联成功',
    'backfill_matched': '自动关联匹配成功',
    'backfill_conflict': '自动关联匹配冲突',
    'backfill_skipped': '自动关联跳过',
    'backfill_stopped': '自动关联中止',
    'mapping_deleted': '删除映射',
    'mapping_cleared': '清空映射',
    'cache_record_deleted': '删除缓存记录',
    'cache_deleted': '删除缓存',
    'cache_cleared': '清空缓存',
    'cron_added': '新建定时任务',
    'cron_updated': '更新定时任务',
    'cron_deleted': '删除定时任务',
    'cron_toggled': '启用/禁用定时任务',
    'cron_executed': '定时任务执行',
    'cron_skipped': '定时任务跳过',
    'cron_skipped_already_running': '定时任务跳过（已在运行）',
    'cron_test': '测试任务',
    'downloader_test': '测试下载器',
    'downloader_updated': '更新下载器',
    'notifier_updated': '更新通知器',
    'backfill_metrics': '自动关联指标',
    'job_execute_failed': '任务执行异常',
    'db_backup': '数据库备份',
}


SETTINGS_SAVE_SCOPES = {
    'general_template': {'default_extensions', 'default_exclude_dirs'},
    'log_cleanup': {'log_retention_days', 'auto_clean_logs', 'app_log_max_mb', 'app_log_backup_count'},
    'delete_notify': {'delete_files_with_torrent', 'notify_on_delete', 'notify_on_risky_delete', 'delete_match_strict_mode', 'pending_source_guard_enabled', 'pending_source_guard_seconds', 'pending_source_warn_threshold', 'pending_source_log_mode'},
    'source_policy': {'manual_dest_delete_delete_source', 'manual_source_delete_delete_dest', 'downloader_dest_delete_delete_source', 'downloader_source_delete_delete_dest', 'downloader_dest_delete_delete_torrent', 'downloader_source_delete_delete_torrent'},
    'network_notify': {'allowed_roots', 'proxy_url', 'tg_api_base'},
    'backfill': {'backfill_batch_limit', 'backfill_max_candidates', 'backfill_file_fetch_workers', 'backfill_max_failures', 'backfill_path_mappings'},
    'backup': {'backup_dir', 'backup_keep_last'},
    'dev': {'dev_mode', 'dev_auto_pull', 'dev_git_repo', 'dev_git_branch', 'dev_auto_pip_sync', 'dev_pip_sync_timeout', 'dev_git_token', 'dev_proxy_url', 'dev_no_proxy'},
    'update': {'github_version_check_enabled', 'github_repo', 'github_api_base', 'version_check_cache_minutes', 'critical_action_passphrase'},
}



def _op_type_label(op_type):
    key = str(op_type or '').strip()
    if not key:
        return '未归类'
    if key in OPERATION_TYPE_LABELS:
        return OPERATION_TYPE_LABELS[key]
    return f'未归类（{key}）'

def init_web_routes(ctx: RouteDeps):
    HardlinkTask = ctx.HardlinkTask
    DeleteMonitorTask = ctx.DeleteMonitorTask
    Downloader = ctx.Downloader
    Notifier = ctx.Notifier
    HardlinkCache = ctx.HardlinkCache
    FileLinkMap = ctx.FileLinkMap
    OperationLog = ctx.OperationLog
    JobExecutionLog = ctx.JobExecutionLog
    DeletePendingAction = ctx.DeletePendingAction
    AppConfig = ctx.AppConfig
    CronJob = ctx.CronJob
    db = ctx.db
    scheduler = ctx.scheduler

    get_config = ctx.get_config
    set_config = ctx.set_config
    log_operation = ctx.log_operation
    validate_path = ctx.validate_path
    validate_host = ctx.validate_host
    validate_cron_expression = ctx.validate_cron_expression

    run_hardlink_once = ctx.run_hardlink_once
    run_delete_once = ctx.run_delete_once
    run_backfill_once = ctx.run_backfill_once
    run_backfill_for_map_id = ctx.run_backfill_for_map_id
    run_backup_once = ctx.run_backup_once
    run_cron_job = ctx.run_cron_job
    update_cron_job = ctx.update_cron_job
    list_torrents = ctx.list_torrents
    send_telegram_notification = ctx.send_telegram_notification
    delete_torrent_func = ctx.delete_torrent
    get_release_info = ctx.get_release_info
    request_stop_by_execution = ctx.request_stop_by_execution
    get_running_executions_snapshot = ctx.get_running_executions_snapshot

    def _wants_json():
        return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _json_or_redirect(ok, message, redirect_path, html=None, target=None, status=200):
        if _wants_json():
            return jsonify({'ok': ok, 'message': message, 'html': html, 'target': target}), status
        flash(message, 'success' if ok else 'danger')
        return redirect(redirect_path)

    def _validate_backfill_setting(key, raw):
        text = str(raw or '').strip()
        if key == 'backfill_path_mappings':
            return True, text

        bounds = {
            'backfill_batch_limit': (50, 3000),
            'backfill_max_candidates': (20, 500),
            'backfill_file_fetch_workers': (1, 16),
            'backfill_max_failures': (0, 10),
        }
        lo, hi = bounds.get(key, (None, None))
        if lo is None:
            return True, text
        try:
            val = int(text)
        except Exception:
            return False, f'{key} must be an integer'
        if val < lo or val > hi:
            return False, f'{key} must be in [{lo}, {hi}]'
        return True, str(val)

    def _validate_dev_setting(key, raw):
        text = str(raw or '').strip()

        if key in {'dev_mode', 'dev_auto_pull', 'dev_auto_pip_sync'}:
            if text not in {'true', 'false'}:
                return False, f'{key} must be true or false'
            return True, text

        if key == 'dev_git_repo':
            if text and (not text.startswith('http://') and not text.startswith('https://')):
                return False, 'dev_git_repo must start with http:// or https://'
            if len(text) > 300:
                return False, 'dev_git_repo is too long'
            return True, text

        if key == 'dev_git_branch':
            if text and not re.fullmatch(r'[A-Za-z0-9._/-]{1,120}', text):
                return False, 'dev_git_branch contains invalid characters'
            return True, text

        if key == 'dev_pip_sync_timeout':
            try:
                val = int(text or '120')
            except Exception:
                return False, 'dev_pip_sync_timeout must be an integer'
            if val < 30 or val > 1800:
                return False, 'dev_pip_sync_timeout must be in [30, 1800]'
            return True, str(val)

        if key == 'dev_proxy_url':
            if text and (not text.startswith('http://') and not text.startswith('https://') and not text.startswith('socks5://')):
                return False, 'dev_proxy_url must start with http://, https:// or socks5://'
            if len(text) > 300:
                return False, 'dev_proxy_url is too long'
            return True, text

        if key == 'dev_no_proxy':
            if len(text) > 500:
                return False, 'dev_no_proxy is too long'
            return True, text

        if key == 'dev_git_token':
            if len(text) > 300:
                return False, 'dev_git_token is too long'
            return True, text

        return True, text

    def _sync_dev_runtime_env():
        runtime_file = Path('instance') / 'dev_runtime.env'
        keys = (
            ('APP_DEV_MODE', 'dev_mode', 'false'),
            ('APP_DEV_AUTO_PULL', 'dev_auto_pull', 'false'),
            ('APP_DEV_GIT_REPO', 'dev_git_repo', ''),
            ('APP_DEV_GIT_BRANCH', 'dev_git_branch', 'master'),
            ('APP_DEV_AUTO_PIP_SYNC', 'dev_auto_pip_sync', 'true'),
            ('APP_DEV_PIP_SYNC_TIMEOUT', 'dev_pip_sync_timeout', '120'),
            ('APP_DEV_GIT_TOKEN', 'dev_git_token', ''),
            ('APP_DEV_PROXY_URL', 'dev_proxy_url', ''),
            ('APP_DEV_NO_PROXY', 'dev_no_proxy', 'localhost,127.0.0.1,::1'),
        )
        lines = ['# Auto-generated by HLM settings page. Applied on container restart.']
        for env_key, cfg_key, default in keys:
            val = str(get_config(cfg_key, default) or default)
            escaped = val.replace("'", "'\"'\"'")
            lines.append(f"{env_key}='{escaped}'")
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def _critical_guard():
        expected = (get_config('critical_action_passphrase', '') or '').strip()
        if not expected:
            return True
        provided = (request.form.get('critical_passphrase') or '').strip()
        return provided == expected


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


    def _fmt_local_dt(dt_obj):
        if not dt_obj:
            return '-'
        try:
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=ctx.UTC)
            return dt_obj.astimezone(ctx.APP_TZ).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(dt_obj)


    def _hardlink_payload():
        return {
            'tasks': HardlinkTask.query.all(),
            'downloaders': Downloader.query.all(),
            'notifiers': Notifier.query.all(),
            'default_extensions': get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv'),
            'default_exclude_dirs': get_config('default_exclude_dirs', 'sample,subs'),
        }

    def _resolve_pending_executor(pending):
        # Compatible with legacy DeleteMonitorTask pending records and new hardlink built-in linkage records.
        task_name = '-'
        downloader = None
        notifier = None
        if pending.task_id:
            dm = db.session.get(DeleteMonitorTask, pending.task_id)
            if dm:
                task_name = dm.name
                downloader = getattr(dm, 'downloader', None)
                notifier = getattr(dm, 'notifier', None)
                return task_name, downloader, notifier

        fmap = db.session.get(FileLinkMap, pending.file_map_id) if pending.file_map_id else None
        if fmap and fmap.task_id:
            ht = db.session.get(HardlinkTask, fmap.task_id)
            if ht:
                task_name = ht.name
                downloader = db.session.get(Downloader, ht.delete_downloader_id) if ht.delete_downloader_id else None
                notifier = db.session.get(Notifier, ht.delete_notifier_id) if ht.delete_notifier_id else None
        return task_name, downloader, notifier

    def _delete_payload():
        pending_q = (request.args.get('pending_q') or '').strip()
        pending_match = (request.args.get('pending_match') or 'all').strip()
        pending_actions_q = DeletePendingAction.query.filter_by(status='pending')
        if pending_q:
            like = f"%{pending_q}%"
            pending_actions_q = pending_actions_q.filter(
                (DeletePendingAction.deleted_path.like(like)) |
                (DeletePendingAction.torrent_hash.like(like)) |
                (DeletePendingAction.reason.like(like))
            )
        if pending_match != 'all':
            pending_actions_q = pending_actions_q.filter(DeletePendingAction.match_by == pending_match)

        pending_actions = pending_actions_q.order_by(DeletePendingAction.created_at.desc()).limit(200).all()
        return {
            'downloaders': Downloader.query.all(),
            'notifiers': Notifier.query.all(),
            'pending_actions': pending_actions,
            'pending_q': pending_q,
            'pending_match': pending_match,
        }

    def _downloader_payload():
        return {'downloaders': Downloader.query.all()}

    def _notifier_payload():
        return {'notifiers': Notifier.query.all()}

    def _cron_human_text(expr):
        parts = (expr or '').split()
        if len(parts) != 5:
            return '格式错误（应为 5 段）'
        minute, hour, day, month, dow = parts
        if day == '*' and month == '*' and dow == '*':
            if minute.isdigit() and hour.isdigit():
                hh = int(hour)
                mm = int(minute)
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    return f'每天 {hh:02d}:{mm:02d} 执行（24小时制）'
            if hour.startswith('*/') and minute.isdigit():
                step = hour[2:]
                if step.isdigit() and int(step) > 0:
                    return f'每 {int(step)} 小时在 {int(minute):02d} 分执行（24小时制）'
            if minute.startswith('*/') and hour == '*':
                step = minute[2:]
                if step.isdigit() and int(step) > 0:
                    return f'每 {int(step)} 分钟执行一次'
        return '自定义 Cron（可参考下方示例）'

    def _cron_payload():
        jobs = CronJob.query.all()
        cron_next_runs = {}
        cron_last_execs = {}
        for job in jobs:
            next_local = '-'
            sch_job = scheduler.get_job(f'cron_{job.id}')
            if sch_job and getattr(sch_job, 'next_run_time', None):
                next_local = _fmt_local_dt(sch_job.next_run_time)
            cron_next_runs[job.id] = next_local

            # Use cron job id in display query to avoid mixing records when multiple maintenance jobs share task_type/target_id(None).
            last = JobExecutionLog.query.filter_by(source='cron', job_name=job.name, job_type=job.task_type).order_by(JobExecutionLog.started_at.desc()).first()
            if last:
                cron_last_execs[job.id] = {
                    'status': last.status or '-',
                    'started_at': _fmt_local_dt(last.started_at),
                    'message': last.message or '-',
                }
            else:
                cron_last_execs[job.id] = {'status': '-', 'started_at': '-', 'message': '-'}

        return {
            'jobs': jobs,
            'cron_hints': {j.id: _cron_human_text(j.cron_expression) for j in jobs},
            'cron_next_runs': cron_next_runs,
            'cron_last_execs': cron_last_execs,
            'hardlink_tasks': HardlinkTask.query.all(),
            'delete_tasks': DeleteMonitorTask.query.all(),
        }

    @web_bp.route('/')
    def dashboard():
        hardlink_tasks = HardlinkTask.query.all()
        delete_tasks = DeleteMonitorTask.query.all()
        executions = JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc()).limit(10).all()
        success_runs = JobExecutionLog.query.filter_by(status='success').count()
        failed_runs = JobExecutionLog.query.filter_by(status='failed').count()
        recent_20 = JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc()).limit(20).all()
        finished_20 = [e for e in recent_20 if e.status in {'success', 'failed'}]
        success_20 = len([e for e in finished_20 if e.status == 'success'])
        fail_20 = len([e for e in finished_20 if e.status == 'failed'])
        success_rate_20 = int((success_20 * 100) / len(finished_20)) if finished_20 else 100
        durations = [int(e.duration_ms or 0) for e in finished_20 if int(e.duration_ms or 0) > 0]
        avg_duration_ms_20 = int(sum(durations) / len(durations)) if durations else 0
        p95_duration_ms_20 = 0
        if durations:
            sorted_d = sorted(durations)
            idx = max(0, min(len(sorted_d) - 1, int(len(sorted_d) * 0.95) - 1))
            p95_duration_ms_20 = sorted_d[idx]

        fail_logs = OperationLog.query.filter_by(success=False).order_by(OperationLog.created_at.desc()).limit(200).all()
        fail_type_counter = {}
        for row in fail_logs:
            key = str(row.operation_type or 'unknown').strip() or 'unknown'
            fail_type_counter[key] = fail_type_counter.get(key, 0) + 1
        fail_type_top = []
        for op_key, cnt in sorted(fail_type_counter.items(), key=lambda x: x[1], reverse=True)[:5]:
            fail_type_top.append({
                'key': op_key,
                'label': _op_type_label(op_key),
                'count': cnt,
            })

        running_snaps = get_running_executions_snapshot()
        running_ids = set([x.get('execution_id') for x in running_snaps if x.get('execution_id')])
        stale_running_ids = set([e.id for e in executions if e.status == 'running' and e.id not in running_ids])

        pending_count = FileLinkMap.query.filter(FileLinkMap.source_type == 'pending', FileLinkMap.deleted_at.is_(None)).count()
        try:
            pending_warn_threshold = int((get_config('pending_source_warn_threshold', '200') or '200').strip())
        except Exception:
            pending_warn_threshold = 200
        pending_warn_threshold = max(1, pending_warn_threshold)
        pending_is_warn = pending_count >= pending_warn_threshold
        latest_pending_event = OperationLog.query.filter_by(operation_type='delete_pending_source').order_by(OperationLog.created_at.desc()).first()

        return render_template(
            'dashboard.html',
            hardlink_tasks=hardlink_tasks,
            delete_tasks=delete_tasks,
            downloaders=Downloader.query.all(),
            notifiers=Notifier.query.all(),
            cron_jobs=CronJob.query.all(),
            recent_logs=OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all(),
            recent_executions=executions,
            running_count=len(running_snaps),
            total_tasks=len(hardlink_tasks) + len(delete_tasks),
            hardlink_count=len(hardlink_tasks),
            delete_count=len(delete_tasks),
            downloader_count=Downloader.query.count(),
            notifier_count=Notifier.query.count(),
            success_runs=success_runs,
            failed_runs=failed_runs,
            running_executions=running_snaps,
            stale_running_ids=stale_running_ids,
            success_rate_20=success_rate_20,
            success_20=success_20,
            fail_20=fail_20,
            avg_duration_ms_20=avg_duration_ms_20,
            p95_duration_ms_20=p95_duration_ms_20,
            fail_type_top=fail_type_top,
            pending_count=pending_count,
            pending_warn_threshold=pending_warn_threshold,
            pending_is_warn=pending_is_warn,
            latest_pending_event=latest_pending_event,
        )

    @web_bp.route('/executions/retry/<int:execution_id>', methods=['POST'])
    def execution_retry(execution_id):
        e = db.session.get(JobExecutionLog, execution_id)
        if not e:
            return _json_or_redirect(False, '执行记录不存在', '/', status=404)

        if e.job_type in {'batch_hardlink', 'hardlink_scan'} and e.target_id:
            ok, msg = run_hardlink_once(e.target_id)
        elif e.job_type in {'delete_scan', 'delete_monitor_scan'} and e.target_id:
            ok, msg = run_delete_once(e.target_id)
        elif e.job_type in {'backfill_mapping', 'backfill_torrent_mapping'}:
            ok, msg = run_backfill_once(e.target_id)
        elif e.job_type == 'db_backup':
            ok, msg = run_backup_once()
        else:
            return _json_or_redirect(False, f'该任务类型暂不支持重试: {e.job_type}', '/', status=400)
        return _json_or_redirect(ok, msg, '/', status=200 if ok else 400)

    @web_bp.route('/executions/stop/<int:execution_id>', methods=['POST'])
    def execution_stop(execution_id):
        ok, meta = request_stop_by_execution(execution_id)
        if not ok:
            return _json_or_redirect(True, '任务已结束或未在运行，无需停止', request.referrer or '/')
        return _json_or_redirect(True, f"已发送停止请求：{meta.get('job_name','任务')}（请等待当前步骤结束）", request.referrer or '/')


    @web_bp.route('/hardlink')
    def hardlink_list():
        payload = _hardlink_payload()
        q = (request.args.get('q') or '').strip()
        enabled = (request.args.get('enabled') or 'all').strip().lower()
        tasks = payload.get('tasks', [])
        if q:
            key = q.lower()
            tasks = [t for t in tasks if key in (t.name or '').lower()]
        if enabled == 'enabled':
            tasks = [t for t in tasks if bool(t.enabled)]
        elif enabled == 'disabled':
            tasks = [t for t in tasks if not bool(t.enabled)]
        payload['tasks'] = tasks
        payload['q'] = q
        payload['enabled'] = enabled
        return render_template('hardlink.html', **payload)

    @web_bp.route('/hardlink/new')
    def hardlink_new_page():
        return render_template('hardlink_new.html', **_hardlink_payload())

    @web_bp.route('/hardlink/edit/<int:task_id>')
    def hardlink_edit_page(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            flash('任务不存在', 'danger')
            return redirect('/hardlink')
        payload = _hardlink_payload()
        payload['task'] = task
        return render_template('hardlink_edit.html', **payload)

    @web_bp.route('/hardlink/add', methods=['POST'])
    def hardlink_add():
        name = (request.form.get('name') or '').strip()
        source_dir = str(Path(request.form.get('source_dir', '')))
        dest_dir = str(Path(request.form.get('dest_dir', '')))
        extensions = request.form.get('extensions', '').strip() or get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv')
        exclude_dirs = request.form.get('exclude_dirs', '').strip() or get_config('default_exclude_dirs', 'sample,subs')
        create_folder = request.form.get('create_folder') == 'on'
        use_cache = request.form.get('use_cache') == 'on'
        monitor_source_delete = request.form.get('monitor_source_delete') == 'on'
        monitor_dest_delete = request.form.get('monitor_dest_delete') == 'on'
        delete_downloader_id = request.form.get('delete_downloader_id', type=int)
        delete_notifier_id = request.form.get('delete_notifier_id', type=int)
        delete_cooldown_seconds = request.form.get('delete_cooldown_seconds', type=int)
        delete_max_deletes_per_run = request.form.get('delete_max_deletes_per_run', type=int)
        delete_dry_run = request.form.get('delete_dry_run') == 'on'
        delete_notify_on_delete = request.form.get('delete_notify_on_delete') == 'on'
        delete_notify_on_risky_delete = request.form.get('delete_notify_on_risky_delete') == 'on'

        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/hardlink', status=400)
        for label, val in [('源目录', source_dir), ('目标目录', dest_dir)]:
            ok, msg = validate_path(val)
            if not ok:
                return _json_or_redirect(False, f'{label}无效: {msg}', '/hardlink', status=400)

        min_file_age = request.form.get('min_file_age_seconds', type=int)
        if min_file_age is None or min_file_age < 0 or min_file_age > 86400:
            return _json_or_redirect(False, '最小文件年龄必须在 0-86400 秒之间', '/hardlink', status=400)
        if delete_cooldown_seconds is None or delete_cooldown_seconds < 0 or delete_cooldown_seconds > 86400:
            return _json_or_redirect(False, '删除联动冷却秒数必须在 0-86400 秒之间', '/hardlink', status=400)
        if delete_max_deletes_per_run is None or delete_max_deletes_per_run < 1 or delete_max_deletes_per_run > 1000:
            return _json_or_redirect(False, '删除联动单次最大删除必须在 1-1000 之间', '/hardlink', status=400)

        task = HardlinkTask(
            name=name,
            source_dir=source_dir,
            dest_dir=dest_dir,
            extensions=extensions,
            exclude_dirs=exclude_dirs,
            exclude_extensions=request.form.get('exclude_extensions', ''),
            create_folder=create_folder,
            use_cache=use_cache,
            min_file_age_seconds=min_file_age,
            monitor_source_delete=monitor_source_delete,
            monitor_dest_delete=monitor_dest_delete,
            delete_downloader_id=delete_downloader_id,
            delete_notifier_id=delete_notifier_id,
            delete_cooldown_seconds=delete_cooldown_seconds,
            delete_max_deletes_per_run=delete_max_deletes_per_run,
            delete_dry_run=delete_dry_run,
            delete_notify_on_delete=delete_notify_on_delete,
            delete_notify_on_risky_delete=delete_notify_on_risky_delete,
        )
        db.session.add(task)
        db.session.commit()
        log_operation('hardlink_task_created', 'HardlinkTask', task.id, task.name)
        payload = _hardlink_payload()
        html = render_template('_hardlink_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '硬链接任务已添加（定时扫描模式）', '/hardlink', html=html, target='hardlinkJobsPanel')


    @web_bp.route('/hardlink/update/<int:task_id>', methods=['POST'])
    def hardlink_update(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/hardlink', status=404)

        name = (request.form.get('name') or '').strip()
        source_dir = str(Path(request.form.get('source_dir', '')))
        dest_dir = str(Path(request.form.get('dest_dir', '')))
        extensions = request.form.get('extensions', '').strip() or get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv')
        exclude_dirs = request.form.get('exclude_dirs', '').strip() or get_config('default_exclude_dirs', 'sample,subs')
        exclude_extensions = request.form.get('exclude_extensions', '')
        create_folder = request.form.get('create_folder') == 'on'
        use_cache = request.form.get('use_cache') == 'on'
        monitor_source_delete = request.form.get('monitor_source_delete') == 'on'
        monitor_dest_delete = request.form.get('monitor_dest_delete') == 'on'
        delete_downloader_id = request.form.get('delete_downloader_id', type=int)
        delete_notifier_id = request.form.get('delete_notifier_id', type=int)
        delete_cooldown_seconds = request.form.get('delete_cooldown_seconds', type=int)
        delete_max_deletes_per_run = request.form.get('delete_max_deletes_per_run', type=int)
        delete_dry_run = request.form.get('delete_dry_run') == 'on'
        delete_notify_on_delete = request.form.get('delete_notify_on_delete') == 'on'
        delete_notify_on_risky_delete = request.form.get('delete_notify_on_risky_delete') == 'on'
        min_file_age = request.form.get('min_file_age_seconds', type=int)

        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/hardlink', status=400)
        for label, val in [('源目录', source_dir), ('目标目录', dest_dir)]:
            ok, msg = validate_path(val)
            if not ok:
                return _json_or_redirect(False, f'{label}无效: {msg}', '/hardlink', status=400)
        if min_file_age is None or min_file_age < 0 or min_file_age > 86400:
            return _json_or_redirect(False, '最小文件年龄必须在 0-86400 秒之间', '/hardlink', status=400)
        if delete_cooldown_seconds is None or delete_cooldown_seconds < 0 or delete_cooldown_seconds > 86400:
            return _json_or_redirect(False, '删除联动冷却秒数必须在 0-86400 秒之间', '/hardlink', status=400)
        if delete_max_deletes_per_run is None or delete_max_deletes_per_run < 1 or delete_max_deletes_per_run > 1000:
            return _json_or_redirect(False, '删除联动单次最大删除必须在 1-1000 之间', '/hardlink', status=400)

        task.name = name
        task.source_dir = source_dir
        task.dest_dir = dest_dir
        task.extensions = extensions
        task.exclude_dirs = exclude_dirs
        task.exclude_extensions = exclude_extensions
        task.create_folder = create_folder
        task.use_cache = use_cache
        task.min_file_age_seconds = min_file_age
        task.monitor_source_delete = monitor_source_delete
        task.monitor_dest_delete = monitor_dest_delete
        task.delete_downloader_id = delete_downloader_id
        task.delete_notifier_id = delete_notifier_id
        task.delete_cooldown_seconds = delete_cooldown_seconds
        task.delete_max_deletes_per_run = delete_max_deletes_per_run
        task.delete_dry_run = delete_dry_run
        task.delete_notify_on_delete = delete_notify_on_delete
        task.delete_notify_on_risky_delete = delete_notify_on_risky_delete
        db.session.commit()
        log_operation('hardlink_task_updated', 'HardlinkTask', task.id, task.name)

        payload = _hardlink_payload()
        html = render_template('_hardlink_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '硬链接任务已更新并生效', '/hardlink', html=html, target='hardlinkJobsPanel')

    @web_bp.route('/hardlink/toggle/<int:task_id>', methods=['POST'])
    def hardlink_toggle(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/hardlink', status=404)
        task.enabled = not task.enabled
        db.session.commit()
        log_operation('hardlink_task_toggled', 'HardlinkTask', task.id, task.name, f"状态: {'已启用' if task.enabled else '已禁用'}")
        payload = _hardlink_payload()
        html = render_template('_hardlink_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'任务 {task.name} 已{"启用" if task.enabled else "禁用"}', '/hardlink', html=html, target='hardlinkJobsPanel')

    @web_bp.route('/hardlink/delete/<int:task_id>', methods=['POST'])
    def hardlink_delete(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/hardlink', status=404)
        task_root = str(Path(task.source_dir).resolve(strict=False))
        cache_rows = HardlinkCache.query.all()
        for row in cache_rows:
            try:
                source_path = str(Path(row.source_path).resolve(strict=False))
                if source_path == task_root or source_path.startswith(task_root + '/'):
                    db.session.delete(row)
            except Exception:
                continue
        FileLinkMap.query.filter_by(task_id=task.id).delete()
        db.session.delete(task)
        db.session.commit()
        log_operation('hardlink_task_deleted', 'HardlinkTask', task_id, task.name)
        payload = _hardlink_payload()
        html = render_template('_hardlink_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '任务已删除', '/hardlink', html=html, target='hardlinkJobsPanel')

    @web_bp.route('/hardlink/batch/<int:task_id>', methods=['POST'])
    @web_bp.route('/hardlink/execute/<int:task_id>', methods=['POST'])
    def hardlink_execute(task_id):
        ok, msg = run_hardlink_once(task_id)
        if _wants_json():
            payload = _hardlink_payload()
            html = render_template('_hardlink_jobs_panel.html', **payload)
            return _json_or_redirect(ok, msg, '/hardlink', html=html if ok else None, target='hardlinkJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, msg, '/hardlink')

    @web_bp.route('/delete-monitor')
    def delete_monitor_list():
        return render_template('delete_monitor.html', **_delete_payload())

    @web_bp.route('/delete-monitor/add', methods=['POST'])
    def delete_monitor_add_legacy():
        return _json_or_redirect(False, '删除联动任务入口已收敛，请到硬链接任务中配置删除联动', '/delete-monitor', status=400)


    @web_bp.route('/delete-monitor/update/<int:task_id>', methods=['POST'])
    def delete_monitor_update_legacy(task_id):
        return _json_or_redirect(False, '删除联动任务入口已收敛，请到硬链接任务中配置删除联动', '/delete-monitor', status=400)


    @web_bp.route('/delete-monitor/run/<int:task_id>', methods=['POST'])
    def delete_monitor_run_legacy(task_id):
        return _json_or_redirect(False, '独立删除任务已收敛，请改为执行硬链接任务', '/delete-monitor', status=400)


    @web_bp.route('/delete-monitor/toggle/<int:task_id>', methods=['POST'])
    def delete_monitor_toggle_legacy(task_id):
        return _json_or_redirect(False, '独立删除任务已收敛，请到硬链接任务中配置', '/delete-monitor', status=400)


    @web_bp.route('/delete-monitor/delete/<int:task_id>', methods=['POST'])
    def delete_monitor_delete_legacy(task_id):
        return _json_or_redirect(False, '独立删除任务已收敛，请到硬链接任务中配置', '/delete-monitor', status=400)


    @web_bp.route('/delete-monitor/pending/confirm/<int:pending_id>', methods=['POST'])
    def delete_pending_confirm(pending_id):
        if not _critical_guard():
            return _json_or_redirect(False, '关键操作口令错误', '/delete-monitor', status=403)
        pending = db.session.get(DeletePendingAction, pending_id)
        if not pending or pending.status != 'pending':
            return _json_or_redirect(False, '待确认记录不存在或已处理', '/delete-monitor', status=404)

        task_name, downloader, _notifier = _resolve_pending_executor(pending)
        if not downloader:
            return _json_or_redirect(False, '关联下载器不可用', '/delete-monitor', status=400)

        torrent_hash = (pending.torrent_hash or '').strip()
        if not torrent_hash:
            pending.status = 'rejected'
            pending.confirmed_at = db.func.now()
            db.session.commit()
            return _json_or_redirect(False, '待确认记录缺少种子哈希，已驳回', '/delete-monitor', status=400)

        ok = delete_torrent_func(downloader, torrent_hash)
        pending.status = 'confirmed' if ok else 'failed'
        pending.confirmed_at = db.func.now()
        db.session.commit()

        log_operation('pending_delete_confirmed' if ok else 'pending_delete_failed', 'DeletePendingAction', pending.id, task_name, f'手动确认删除种子 {torrent_hash}', ok)
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(ok, '已确认删除并执行' if ok else '执行删除失败', '/delete-monitor', html=html if ok else None, target='deleteJobsPanel', status=200 if ok else 400)


    @web_bp.route('/delete-monitor/pending/bulk', methods=['POST'])
    def delete_pending_bulk():
        if not _critical_guard():
            return _json_or_redirect(False, '关键操作口令错误', '/delete-monitor', status=403)
        action = (request.form.get('bulk_action') or '').strip()
        ids = request.form.getlist('pending_ids')
        if not ids:
            return _json_or_redirect(False, '请先勾选待确认项', '/delete-monitor', status=400)
        if action not in {'confirm', 'reject'}:
            return _json_or_redirect(False, '无效的批量操作', '/delete-monitor', status=400)

        done = 0
        failed = 0
        for raw_id in ids:
            try:
                pid = int(raw_id)
            except Exception:
                failed += 1
                continue

            pending = db.session.get(DeletePendingAction, pid)
            if not pending or pending.status != 'pending':
                failed += 1
                continue

            if action == 'reject':
                pending.status = 'rejected'
                pending.confirmed_at = db.func.now()
                done += 1
                continue

            task_name, downloader, _notifier = _resolve_pending_executor(pending)
            torrent_hash = (pending.torrent_hash or '').strip()
            if not downloader or not torrent_hash:
                pending.status = 'failed'
                pending.confirmed_at = db.func.now()
                failed += 1
                continue

            ok = delete_torrent_func(downloader, torrent_hash)
            pending.status = 'confirmed' if ok else 'failed'
            pending.confirmed_at = db.func.now()
            if ok:
                done += 1
            else:
                failed += 1

        db.session.commit()
        msg = f'批量处理完成：成功 {done}，失败 {failed}'
        log_operation('pending_delete_bulk', 'DeletePendingAction', None, action, msg, failed == 0)
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, msg, '/delete-monitor', html=html, target='deleteJobsPanel')

    @web_bp.route('/delete-monitor/pending/reject/<int:pending_id>', methods=['POST'])
    def delete_pending_reject(pending_id):
        pending = db.session.get(DeletePendingAction, pending_id)
        if not pending or pending.status != 'pending':
            return _json_or_redirect(False, '待确认记录不存在或已处理', '/delete-monitor', status=404)
        pending.status = 'rejected'
        pending.confirmed_at = db.func.now()
        db.session.commit()
        log_operation('pending_delete_rejected', 'DeletePendingAction', pending.id, str(pending.task_id), f'手动驳回待确认记录 #{pending.id}')
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '已驳回该待确认项', '/delete-monitor', html=html, target='deleteJobsPanel')

    @web_bp.route('/downloader')
    def downloader_list():
        return render_template('downloader.html', **_downloader_payload())

    @web_bp.route('/downloader/add', methods=['POST'])
    def downloader_add():
        name = (request.form.get('name') or '').strip()
        host = (request.form.get('host') or '').rstrip('/')
        port = request.form.get('port', type=int)
        if not name:
            return _json_or_redirect(False, '下载器名称不能为空', '/downloader', status=400)
        ok, msg = validate_host(host)
        if not ok:
            return _json_or_redirect(False, f'主机地址无效: {msg}', '/downloader', status=400)
        if port is None or port < 1 or port > 65535:
            return _json_or_redirect(False, '端口号必须在 1-65535', '/downloader', status=400)

        session_ttl_seconds = request.form.get('session_ttl_seconds', type=int)
        if session_ttl_seconds is not None and (session_ttl_seconds < 60 or session_ttl_seconds > 86400):
            return _json_or_redirect(False, '会话复用时长必须在 60-86400 秒', '/downloader', status=400)

        d = Downloader(name=name, type=request.form.get('type', 'qbittorrent'), host=host, port=port, username=request.form.get('username'), proxy_url=(request.form.get('proxy_url') or '').strip() or None, session_ttl_seconds=session_ttl_seconds)
        d.set_password(request.form.get('password'))
        db.session.add(d)
        db.session.commit()
        payload = _downloader_payload()
        html = render_template('_downloader_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '下载器已添加', '/downloader', html=html, target='downloaderJobsPanel')

    @web_bp.route('/downloader/test/<int:downloader_id>', methods=['POST'])
    def downloader_test(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            return _json_or_redirect(False, '下载器不存在', '/downloader', status=404)
        torrents = list_torrents(d)
        ok = torrents is not None
        msg = f'连接成功，种子数: {len(torrents)}' if ok else '连接失败，请检查配置'
        log_operation('downloader_test', 'Downloader', d.id, d.name, msg, ok)
        if _wants_json():
            payload = _downloader_payload()
            html = render_template('_downloader_jobs_panel.html', **payload)
            return _json_or_redirect(ok, msg, '/downloader', html=html if ok else None, target='downloaderJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, msg, '/downloader')


    @web_bp.route('/downloader/update/<int:downloader_id>', methods=['POST'])
    def downloader_update(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            return _json_or_redirect(False, '下载器不存在', '/downloader', status=404)

        name = (request.form.get('name') or '').strip()
        host = (request.form.get('host') or '').rstrip('/')
        port = request.form.get('port', type=int)
        if not name:
            return _json_or_redirect(False, '下载器名称不能为空', '/downloader', status=400)
        ok, msg = validate_host(host)
        if not ok:
            return _json_or_redirect(False, f'主机地址无效: {msg}', '/downloader', status=400)
        if port is None or port < 1 or port > 65535:
            return _json_or_redirect(False, '端口号必须在 1-65535', '/downloader', status=400)

        session_ttl_seconds = request.form.get('session_ttl_seconds', type=int)
        if session_ttl_seconds is not None and (session_ttl_seconds < 60 or session_ttl_seconds > 86400):
            return _json_or_redirect(False, '会话复用时长必须在 60-86400 秒', '/downloader', status=400)

        d.name = name
        d.type = request.form.get('type', 'qbittorrent')
        d.host = host
        d.port = port
        d.username = request.form.get('username')
        d.proxy_url = (request.form.get('proxy_url') or '').strip() or None
        d.session_ttl_seconds = session_ttl_seconds
        new_password = request.form.get('password')
        if (new_password or '').strip():
            d.set_password(new_password)
        db.session.commit()
        log_operation('downloader_updated', 'Downloader', d.id, d.name)
        payload = _downloader_payload()
        html = render_template('_downloader_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '下载器已更新并生效', '/downloader', html=html, target='downloaderJobsPanel')

    @web_bp.route('/downloader/toggle/<int:downloader_id>', methods=['POST'])
    def downloader_toggle(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            return _json_or_redirect(False, '下载器不存在', '/downloader', status=404)
        d.enabled = not d.enabled
        db.session.commit()
        payload = _downloader_payload()
        html = render_template('_downloader_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'下载器 {d.name} 已{"启用" if d.enabled else "禁用"}', '/downloader', html=html, target='downloaderJobsPanel')

    @web_bp.route('/downloader/delete/<int:downloader_id>', methods=['POST'])
    def downloader_delete(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            return _json_or_redirect(False, '下载器不存在', '/downloader', status=404)
        db.session.delete(d)
        db.session.commit()
        payload = _downloader_payload()
        html = render_template('_downloader_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '下载器已删除', '/downloader', html=html, target='downloaderJobsPanel')

    @web_bp.route('/notifier')
    def notifier_list():
        return render_template('notifier.html', **_notifier_payload())

    @web_bp.route('/notifier/add', methods=['POST'])
    def notifier_add():
        name = (request.form.get('name') or '').strip()
        api_key = (request.form.get('api_key') or '').strip()
        if not name or not api_key:
            return _json_or_redirect(False, '通知器名称和API Key不能为空', '/notifier', status=400)
        n = Notifier(name=name, type=request.form.get('type', 'telegram'), api_key=api_key, chat_id=request.form.get('chat_id'), proxy_url=(request.form.get('proxy_url') or '').strip() or None)
        db.session.add(n)
        db.session.commit()
        payload = _notifier_payload()
        html = render_template('_notifier_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '通知器已添加', '/notifier', html=html, target='notifierJobsPanel')

    @web_bp.route('/notifier/test/<int:notifier_id>', methods=['POST'])
    def notifier_test(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            return _json_or_redirect(False, '通知器不存在', '/notifier', status=404)
        ok = send_telegram_notification(n, 'Hardlink Manager 测试通知')
        if _wants_json():
            payload = _notifier_payload()
            html = render_template('_notifier_jobs_panel.html', **payload)
            return _json_or_redirect(ok, '发送成功' if ok else '发送失败', '/notifier', html=html if ok else None, target='notifierJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, '发送成功' if ok else '发送失败', '/notifier')


    @web_bp.route('/notifier/update/<int:notifier_id>', methods=['POST'])
    def notifier_update(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            return _json_or_redirect(False, '通知器不存在', '/notifier', status=404)

        name = (request.form.get('name') or '').strip()
        api_key = (request.form.get('api_key') or '').strip()
        if not name or not api_key:
            return _json_or_redirect(False, '通知器名称和API Key不能为空', '/notifier', status=400)

        n.name = name
        n.type = request.form.get('type', 'telegram')
        n.api_key = api_key
        n.chat_id = request.form.get('chat_id')
        n.proxy_url = (request.form.get('proxy_url') or '').strip() or None
        db.session.commit()
        log_operation('notifier_updated', 'Notifier', n.id, n.name)
        payload = _notifier_payload()
        html = render_template('_notifier_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '通知器已更新并生效', '/notifier', html=html, target='notifierJobsPanel')

    @web_bp.route('/notifier/toggle/<int:notifier_id>', methods=['POST'])
    def notifier_toggle(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            return _json_or_redirect(False, '通知器不存在', '/notifier', status=404)
        n.enabled = not n.enabled
        db.session.commit()
        payload = _notifier_payload()
        html = render_template('_notifier_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'通知器 {n.name} 已{"启用" if n.enabled else "禁用"}', '/notifier', html=html, target='notifierJobsPanel')

    @web_bp.route('/notifier/delete/<int:notifier_id>', methods=['POST'])
    def notifier_delete(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            return _json_or_redirect(False, '通知器不存在', '/notifier', status=404)
        db.session.delete(n)
        db.session.commit()
        payload = _notifier_payload()
        html = render_template('_notifier_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '通知器已删除', '/notifier', html=html, target='notifierJobsPanel')


    def _mapping_payload(page=None, cache_page=None, q=None, hash_state=None, source_type=None, panel_view=None, per_page=None):
        page = max(page if page is not None else request.args.get('page', 1, type=int), 1)
        cache_page = max(cache_page if cache_page is not None else request.args.get('cache_page', 1, type=int), 1)
        q = (q if q is not None else request.args.get('q') or '').strip()
        hash_state = (hash_state if hash_state is not None else request.args.get('hash_state') or 'all').strip()
        source_type = (source_type if source_type is not None else request.args.get('source_type') or 'all').strip()
        panel_view = (panel_view if panel_view is not None else request.args.get('panel_view') or 'mapping').strip().lower()
        if panel_view not in {'mapping', 'cache'}:
            panel_view = 'mapping'
        allowed_per_page = {10, 20, 50, 100}
        per_page_val = per_page if per_page is not None else request.args.get('per_page', 20, type=int)
        if per_page_val not in allowed_per_page:
            per_page_val = 20

        mapping_q = FileLinkMap.query
        if q:
            like = f"%{q}%"
            mapping_q = mapping_q.filter(
                (FileLinkMap.source_path.like(like)) |
                (FileLinkMap.dest_path.like(like)) |
                (FileLinkMap.torrent_hash.like(like))
            )
        if hash_state in {'linked', 'unlinked'}:
            mapping_q = mapping_q.filter(FileLinkMap.deleted_at.is_(None))
        if hash_state == 'linked':
            mapping_q = mapping_q.filter(FileLinkMap.torrent_hash.isnot(None))
        elif hash_state == 'unlinked':
            mapping_q = mapping_q.filter(FileLinkMap.torrent_hash.is_(None))
        if source_type == 'manual':
            mapping_q = mapping_q.filter(FileLinkMap.source_type == 'manual')
        elif source_type == 'downloader':
            mapping_q = mapping_q.filter(FileLinkMap.source_type == 'downloader')
        elif source_type == 'pending':
            mapping_q = mapping_q.filter(FileLinkMap.source_type == 'pending')

        mapping_q = mapping_q.order_by(FileLinkMap.created_at.desc())
        mapping_pg = mapping_q.paginate(page=page, per_page=per_page_val, error_out=False)

        cache_q = HardlinkCache.query
        if q:
            like = f"%{q}%"
            cache_q = cache_q.filter((HardlinkCache.source_path.like(like)) | (HardlinkCache.dest_path.like(like)))
        cache_q = cache_q.order_by(HardlinkCache.created_at.desc())
        cache_pg = cache_q.paginate(page=cache_page, per_page=per_page_val, error_out=False)

        return {
            'mappings': mapping_pg.items,
            'map_page': page,
            'map_total_pages': max(mapping_pg.pages, 1),
            'caches': cache_pg.items,
            'cache_page': cache_page,
            'cache_total_pages': max(cache_pg.pages, 1),
            'q': q,
            'hash_state': hash_state,
            'source_type': source_type,
            'panel_view': panel_view,
            'per_page': per_page_val,
            'per_page_options': [10, 20, 50, 100],
        }


    @web_bp.route('/mapping')
    def mapping_list():
        payload = _mapping_payload()
        return render_template('mapping.html', **payload)


    @web_bp.route('/mapping/link/bulk-delete', methods=['POST'])
    def mapping_link_bulk_delete():
        map_ids = request.form.getlist('map_ids')
        ids = []
        for v in map_ids:
            try:
                ids.append(int(v))
            except Exception:
                continue
        ids = sorted(set([i for i in ids if i > 0]))
        if not ids:
            return _json_or_redirect(False, '请先勾选要删除的映射记录', '/mapping', status=400)

        rows = FileLinkMap.query.filter(FileLinkMap.id.in_(ids)).all()
        if not rows:
            return _json_or_redirect(False, '未找到可删除的映射记录', '/mapping', status=404)

        count = 0
        for row in rows:
            log_operation('mapping_record_deleted', 'FileLinkMap', row.id, row.source_path, f'批量删除映射: {row.dest_path}')
            db.session.delete(row)
            count += 1
        db.session.commit()
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'已批量删除 {count} 条映射记录', '/mapping', html=html, target='mappingPanel')

    @web_bp.route('/mapping/link/clear', methods=['POST'])
    def mapping_link_clear():
        if not _critical_guard():
            return _json_or_redirect(False, '关键操作口令错误', '/mapping', status=403)
        count = FileLinkMap.query.delete()
        db.session.commit()
        log_operation('mapping_cleared', 'FileLinkMap', None, '全部映射', f'清理 {count} 条映射记录')
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'已清理 {count} 条映射记录', '/mapping', html=html, target='mappingPanel')


    @web_bp.route('/mapping/link/delete', methods=['POST'])
    def mapping_link_delete():
        map_id = request.form.get('map_id', type=int)
        if not map_id:
            return _json_or_redirect(False, 'map_id 不能为空', '/mapping', status=400)
        row = db.session.get(FileLinkMap, map_id)
        if not row:
            return _json_or_redirect(False, '映射记录不存在', '/mapping', status=404)
        src = row.source_path
        dst = row.dest_path
        db.session.delete(row)
        db.session.commit()
        log_operation('mapping_record_deleted', 'FileLinkMap', map_id, src, f'手动删除映射: {dst}')
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '映射记录已删除', '/mapping', html=html, target='mappingPanel')

    @web_bp.route('/mapping/link/retry', methods=['POST'])
    def mapping_link_retry():
        map_id = request.form.get('map_id', type=int)
        if not map_id:
            return _json_or_redirect(False, 'map_id 不能为空', '/mapping', status=400)
        row = db.session.get(FileLinkMap, map_id)
        if not row:
            return _json_or_redirect(False, '映射记录不存在', '/mapping', status=404)
        if row.torrent_hash:
            payload = _mapping_payload()
            html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
            return _json_or_redirect(True, '该映射已关联种子，无需重试', '/mapping', html=html, target='mappingPanel')

        retry_mode = (request.form.get('retry_mode') or 'fast').strip().lower()
        deep_retry = retry_mode == 'deep'
        ok, msg = run_backfill_for_map_id(map_id, deep_retry=deep_retry)
        db.session.refresh(row)
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        if ok and row.torrent_hash:
            log_operation('mapping_retry_linked', 'FileLinkMap', row.id, row.source_path, f'已关联 hash={row.torrent_hash}')
            return _json_or_redirect(True, f'重试成功：已关联种子（{row.torrent_hash}）', '/mapping', html=html, target='mappingPanel')

        return _json_or_redirect(False, f'重试完成但未关联成功：{msg}', '/mapping', html=html, target='mappingPanel', status=400)

    @web_bp.route('/mapping/link/reset-fail/<int:map_id>', methods=['POST'])
    def mapping_link_reset_fail(map_id):
        row = db.session.get(FileLinkMap, map_id)
        if not row:
            return _json_or_redirect(False, '映射记录不存在', '/mapping', status=404)
        row.backfill_fail_count = 0
        row.backfill_last_attempt_at = None
        db.session.commit()
        log_operation('mapping_backfill_fail_reset', 'FileLinkMap', row.id, row.source_path, '手动重置回填失败计数')
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '回填失败计数已重置，可再次参与回填', '/mapping', html=html, target='mappingPanel')


    @web_bp.route('/mapping/cache/delete', methods=['POST'])
    def mapping_cache_delete():
        source_path = (request.form.get('source_path') or '').strip()
        if not source_path:
            return _json_or_redirect(False, 'source_path 不能为空', '/mapping', status=400)
        row = HardlinkCache.query.filter_by(source_path=source_path).first()
        if not row:
            return _json_or_redirect(False, '缓存记录不存在', '/mapping', status=404)
        db.session.delete(row)
        db.session.commit()
        log_operation('cache_record_deleted', 'HardlinkCache', row.id, source_path, '手动删除缓存记录')
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '缓存记录已删除，可再次硬链接', '/mapping', html=html, target='mappingPanel')

    @web_bp.route('/mapping/cache/bulk-delete', methods=['POST'])
    def mapping_cache_bulk_delete():
        cache_ids = request.form.getlist('cache_ids')
        ids = []
        for v in cache_ids:
            try:
                ids.append(int(v))
            except Exception:
                continue
        ids = sorted(set([i for i in ids if i > 0]))
        if not ids:
            return _json_or_redirect(False, '请先勾选要删除的缓存记录', '/mapping', status=400)

        rows = HardlinkCache.query.filter(HardlinkCache.id.in_(ids)).all()
        if not rows:
            return _json_or_redirect(False, '未找到可删除的缓存记录', '/mapping', status=404)

        count = 0
        for row in rows:
            log_operation('cache_record_deleted', 'HardlinkCache', row.id, row.source_path, '批量删除缓存记录')
            db.session.delete(row)
            count += 1
        db.session.commit()
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'已批量删除 {count} 条缓存记录', '/mapping', html=html, target='mappingPanel')


    @web_bp.route('/mapping/cache/clear', methods=['POST'])
    def mapping_cache_clear():
        if not _critical_guard():
            return _json_or_redirect(False, '关键操作口令错误', '/mapping', status=403)
        count = HardlinkCache.query.delete()
        db.session.commit()
        log_operation('cache_cleared', 'HardlinkCache', None, '全部缓存', f'清理 {count} 条缓存')
        payload = _mapping_payload()
        html = render_template('_mapping_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'已清理 {count} 条缓存', '/mapping', html=html, target='mappingPanel')

    @web_bp.route('/logs')
    def logs_list():
        page = max(request.args.get('page', 1, type=int), 1)
        exec_page = max(request.args.get('exec_page', 1, type=int), 1)
        q = (request.args.get('q') or '').strip()
        op = (request.args.get('op') or '').strip()
        exec_id = (request.args.get('execution_id') or '').strip()
        success = (request.args.get('success') or 'all').strip()
        exec_status = (request.args.get('exec_status') or 'all').strip()
        panel_view = (request.args.get('panel_view') or 'logs').strip().lower()
        if panel_view not in {'logs', 'executions'}:
            panel_view = 'logs'
        allowed_per_page = {10, 20, 50, 100}
        per_page = request.args.get('per_page', 20, type=int)
        if per_page not in allowed_per_page:
            per_page = 20

        log_q = OperationLog.query
        if q:
            like = f"%{q}%"
            log_q = log_q.filter((OperationLog.message.like(like)) | (OperationLog.target_name.like(like)) | (OperationLog.operation_type.like(like)) | (OperationLog.target_type.like(like)))
        if op:
            log_q = log_q.filter(OperationLog.operation_type == op)
        if success == 'ok':
            log_q = log_q.filter(OperationLog.success.is_(True))
        elif success == 'fail':
            log_q = log_q.filter(OperationLog.success.is_(False))
        if exec_id:
            try:
                log_q = log_q.filter(OperationLog.execution_id == int(exec_id))
            except Exception:
                pass

        pagination = log_q.order_by(OperationLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        operations = [r[0] for r in db.session.query(OperationLog.operation_type).distinct().order_by(OperationLog.operation_type.asc()).all()]
        operation_options = [(item, _op_type_label(item)) for item in operations]

        executions_q = JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc())
        if exec_status in {'running', 'success', 'failed'}:
            executions_q = executions_q.filter(JobExecutionLog.status == exec_status)
        executions_pg = executions_q.paginate(page=exec_page, per_page=per_page, error_out=False)
        return render_template(
            'logs.html',
            logs=pagination.items,
            page=page,
            total_pages=max(pagination.pages, 1),
            executions=executions_pg.items,
            exec_page=exec_page,
            exec_total_pages=max(executions_pg.pages, 1),
            q=q,
            op=op,
            execution_id=exec_id,
            success=success,
            operations=operations,
            exec_status=exec_status,
            operation_options=operation_options,
            operation_type_labels=OPERATION_TYPE_LABELS,
            operation_type_labeler=_op_type_label,
            panel_view=panel_view,
            per_page=per_page,
            per_page_options=[10, 20, 50, 100],
        )

    @web_bp.route('/logs/clear', methods=['POST'])
    def logs_clear():
        if not _critical_guard():
            flash('关键操作口令错误，已拒绝清空日志', 'danger')
            return redirect('/logs')
        OperationLog.query.delete()
        db.session.commit()
        flash('操作日志已清空', 'success')
        return redirect('/logs')

    def _settings_page_payload():
        settings = {c.key: c.value for c in AppConfig.query.all()}
        if not (settings.get('dev_git_repo') or '').strip():
            settings['dev_git_repo'] = os.environ.get('APP_DEV_GIT_REPO', '') or DEV_DEFAULT_GIT_REPO
        settings['dev_git_token_masked'] = '******' if (settings.get('dev_git_token') or '').strip() else ''
        release = get_release_info()
        return settings, release

    @web_bp.route('/settings')
    def settings_page():
        settings, release = _settings_page_payload()
        return render_template('settings.html', settings=settings, release=release)

    @web_bp.route('/settings/devops')
    def settings_devops_page():
        settings, release = _settings_page_payload()
        return render_template('settings_devops.html', settings=settings, release=release)

    def _save_settings_from_form(form, allowed_keys=None):
        changed = False
        for key in SETTINGS_SAVE_KEYS:
            if allowed_keys is not None and key not in allowed_keys:
                continue
            val = form.get(key)
            if val is None:
                continue
            value = val.strip() if isinstance(val, str) else val

            if key == 'dev_git_token':
                clear_token = (form.get('dev_git_token_clear') or '').strip() == 'true'
                if clear_token:
                    set_config('dev_git_token', '', commit=False)
                    changed = True
                    continue
                if value == '':
                    # Keep existing token when password field is left empty.
                    continue

            if key.startswith('backfill_'):
                ok, checked = _validate_backfill_setting(key, value)
                if not ok:
                    return False, checked
                value = checked

            if key.startswith('dev_'):
                ok, checked = _validate_dev_setting(key, value)
                if not ok:
                    return False, checked
                value = checked

            set_config(key, value, commit=False)
            changed = True
        if changed:
            db.session.commit()
            # Keep entrypoint runtime config in sync with DB settings for next restart.
            if allowed_keys is not None and 'dev_mode' in allowed_keys:
                _sync_dev_runtime_env()
        return True, ''

    @web_bp.route('/settings/save', methods=['POST'])
    def settings_save():
        save_scope = (request.form.get('save_scope') or '').strip()
        allowed_keys = SETTINGS_SAVE_SCOPES.get(save_scope)
        ok, msg = _save_settings_from_form(request.form, allowed_keys=allowed_keys)
        redirect_to = '/settings/devops' if save_scope in {'dev', 'update'} else '/settings'
        if not ok:
            return _json_or_redirect(False, f'设置保存失败: {msg}', redirect_to, status=400)
        return _json_or_redirect(True, '设置已保存并生效', redirect_to)



    @web_bp.route('/settings/dev-restart', methods=['POST'])
    def settings_dev_restart():
        return _json_or_redirect(False, '已禁用应用内自动重启，请保存后手动重启容器以应用开发模式配置', '/settings/devops', status=400)

    
    @web_bp.route('/settings/check-update', methods=['POST'])
    def settings_check_update():
        info = get_release_info(force_refresh=True)
        return _json_or_redirect(True, f"版本检查完成：本地 {info.get('local_version','-')}，远端 {info.get('remote_version','-')}（{info.get('message','-')}）", '/settings/devops')


    @web_bp.route('/settings/export', methods=['GET'])
    def settings_export():
        data = {c.key: c.value for c in AppConfig.query.all()}
        for k in ('critical_action_passphrase', 'dev_git_token'):
            if k in data and data[k]:
                data[k] = '***'
        return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=hlm-settings.json'})

    @web_bp.route('/settings/import', methods=['POST'])
    def settings_import():
        f = request.files.get('config_file')
        if not f:
            flash('请上传配置文件', 'danger')
            return redirect('/settings')
        try:
            payload = json.loads(f.read().decode('utf-8'))
        except Exception:
            flash('配置文件格式错误', 'danger')
            return redirect('/settings')
        allowed = IMPORTABLE_SETTINGS_KEYS
        applied = 0
        changed = False
        for k,v in payload.items():
            if k in allowed and v is not None and v != '***':
                value = str(v)
                if k.startswith('backfill_'):
                    ok, checked = _validate_backfill_setting(k, value)
                    if not ok:
                        return _json_or_redirect(False, f'配置导入失败: {checked}', '/settings', status=400)
                    value = checked
                if k.startswith('dev_'):
                    ok, checked = _validate_dev_setting(k, value)
                    if not ok:
                        return _json_or_redirect(False, f'配置导入失败: {checked}', '/settings', status=400)
                    value = checked
                set_config(k, value, commit=False)
                changed = True
                applied += 1
        if changed:
            db.session.commit()
        return _json_or_redirect(True, f'配置导入完成，已更新 {applied} 项', '/settings')

    @web_bp.route('/diagnostics/backfill-metrics')
    def diagnostics_backfill_metrics():
        rows = OperationLog.query.filter_by(operation_type='backfill_metrics').order_by(OperationLog.created_at.desc()).limit(10).all()
        items = []
        for row in rows:
            items.append({
                'created_at': row.created_at.isoformat() if row.created_at else None,
                'metrics': _parse_backfill_metrics_message(row.message),
                'message': row.message or '',
            })
        return jsonify({'ok': True, 'items': items})


    @web_bp.route('/diagnostics')
    def diagnostics_page():
        panel_view = (request.args.get('panel_view') or 'overview').strip().lower()
        if panel_view not in {'overview', 'backfill'}:
            panel_view = 'overview'
        checks = []
        try:
            db.session.execute(db.text('SELECT 1')).fetchone()
            checks.append(('数据库连接', True, '正常'))
        except Exception as exc:
            checks.append(('数据库连接', False, str(exc)))

        try:
            row = db.session.execute(db.text("SELECT value FROM schema_meta WHERE key='db_schema_version'" )).fetchone()
            current_schema = str(row[0]) if row and row[0] is not None else '0'
        except Exception:
            current_schema = '0'
        checks.append(('数据库结构版本', True, f'current={current_schema}, target=9'))

        checks.append(('代理配置', True, (get_config('proxy_url','') or '').strip() or '未设置（直连）'))
        checks.append(('应用版本', True, get_release_info().get('local_version','-')))
        checks.append(('日志目录', True, str(Path('data/logs').resolve())))
        checks.append(('待判定来源保护', True, f"enabled={get_config('pending_source_guard_enabled','true')}, window={get_config('pending_source_guard_seconds','900')}s, log_mode={get_config('pending_source_log_mode','aggregate')}"))

        pending_count = FileLinkMap.query.filter(FileLinkMap.source_type == 'pending', FileLinkMap.deleted_at.is_(None)).count()
        try:
            pending_warn_threshold = int((get_config('pending_source_warn_threshold', '200') or '200').strip())
        except Exception:
            pending_warn_threshold = 200
        pending_warn_threshold = max(1, pending_warn_threshold)
        checks.append(('待判定映射数量', pending_count < pending_warn_threshold, f'{pending_count} (threshold={pending_warn_threshold})'))

        pending_events = OperationLog.query.filter_by(operation_type='delete_pending_source').order_by(OperationLog.created_at.desc()).limit(5).all()
        backfill_metrics_logs = OperationLog.query.filter_by(operation_type='backfill_metrics').order_by(OperationLog.created_at.desc()).limit(5).all()
        backfill_metrics_rows = [
            {
                'created_at': row.created_at,
                'metrics': _parse_backfill_metrics_message(row.message),
                'message': row.message or '',
            }
            for row in backfill_metrics_logs
        ]

        running_snaps = get_running_executions_snapshot()
        now = __import__('datetime').datetime.now(__import__('datetime').UTC)
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

        return render_template('diagnostics.html', checks=checks, running_rows=running_rows, pending_events=pending_events, backfill_metrics_rows=backfill_metrics_rows, panel_view=panel_view)

    @web_bp.route('/delete-monitor/test/<int:task_id>', methods=['POST'])
    def delete_monitor_test(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/delete-monitor', status=404)
        monitor_root = str(Path(task.directory).resolve(strict=False))
        rows = FileLinkMap.query.filter(FileLinkMap.deleted_at.is_(None)).all()
        hits = 0
        for row in rows:
            sp = str(Path(row.source_path or '').resolve(strict=False))
            dp = str(Path(row.dest_path or '').resolve(strict=False))
            if sp.startswith(monitor_root + '/') or sp == monitor_root or dp.startswith(monitor_root + '/') or dp == monitor_root:
                s_exists = Path(row.source_path or '').exists()
                d_exists = Path(row.dest_path or '').exists()
                if (not s_exists) or (not d_exists):
                    hits += 1
        return _json_or_redirect(True, f'测试完成：当前可能触发联动 {hits} 条（仅估算，不执行删除）', '/delete-monitor')

    @web_bp.route('/cron')
    def cron_list():
        return render_template('cron.html', **_cron_payload())

    @web_bp.route('/cron/add', methods=['POST'])
    def cron_add():
        name = (request.form.get('name') or '').strip()
        task_type = (request.form.get('task_type') or '').strip()
        target_id = request.form.get('target_id', type=int)
        cron_expression = (request.form.get('custom_cron') or request.form.get('cron_expression') or '').strip()
        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/cron', status=400)
        if not validate_cron_expression(cron_expression):
            return _json_or_redirect(False, 'Cron 表达式格式错误（应为 5 段，例如 0 3 * * *）', '/cron', status=400)

        allowed_types = {'batch_hardlink', 'backfill_mapping', 'clean_logs', 'clean_cache', 'clean_backfill_failures', 'db_backup'}
        if task_type not in allowed_types:
            return _json_or_redirect(False, '不支持的任务类型', '/cron', status=400)
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            return _json_or_redirect(False, '请选择有效的硬链接任务', '/cron', status=400)
        if task_type in {'clean_logs', 'clean_cache', 'clean_backfill_failures', 'backfill_mapping', 'db_backup'}:
            target_id = None

        c = CronJob(name=name, task_type=task_type, target_id=target_id, cron_expression=cron_expression, description=request.form.get('description'))
        db.session.add(c)
        db.session.commit()
        update_cron_job(c.id)

        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(True, '定时任务已添加', '/cron', html=html, target='cronJobsPanel')
        return _json_or_redirect(True, '定时任务已添加', '/cron')

    @web_bp.route('/cron/update/<int:job_id>', methods=['POST'])
    def cron_update(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            return _json_or_redirect(False, '定时任务不存在', '/cron', status=404)

        name = (request.form.get('name') or '').strip()
        task_type = (request.form.get('task_type') or c.task_type or '').strip()
        target_id = request.form.get('target_id', type=int)
        cron_expression = (request.form.get('custom_cron') or '').strip()
        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/cron', status=400)
        if not validate_cron_expression(cron_expression):
            return _json_or_redirect(False, 'Cron 表达式格式错误（应为 5 段，例如 0 3 * * *）', '/cron', status=400)

        allowed_types = {'batch_hardlink', 'backfill_mapping', 'clean_logs', 'clean_cache', 'clean_backfill_failures', 'db_backup'}
        if task_type not in allowed_types:
            return _json_or_redirect(False, '不支持的任务类型', '/cron', status=400)
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            return _json_or_redirect(False, '请选择有效的硬链接任务', '/cron', status=400)
        if task_type in {'clean_logs', 'clean_cache', 'clean_backfill_failures', 'backfill_mapping', 'db_backup'}:
            target_id = None

        c.name = name
        c.task_type = task_type
        c.target_id = target_id
        c.cron_expression = cron_expression
        c.description = request.form.get('description')
        db.session.commit()
        update_cron_job(c.id)

        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(True, '定时计划已更新', '/cron', html=html, target='cronJobsPanel')
        return _json_or_redirect(True, '定时计划已更新', '/cron')

    @web_bp.route('/cron/run/<int:job_id>', methods=['POST'])
    def cron_run_once(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            return _json_or_redirect(False, '定时任务不存在', '/cron', status=404)

        if c.task_type == 'batch_hardlink':
            ok, msg = run_hardlink_once(c.target_id)
        elif c.task_type == 'backfill_mapping':
            ok, msg = run_backfill_once(c.target_id)
        elif c.task_type == 'db_backup':
            ok, msg = run_backup_once()
        else:
            run_cron_job(c.id)
            ok, msg = True, f'已触发任务: {c.name}'

        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(ok, msg, '/cron', html=html if ok else None, target='cronJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, msg, '/cron')

    @web_bp.route('/cron/test/<int:job_id>', methods=['POST'])
    def cron_test(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            return _json_or_redirect(False, '定时任务不存在', '/cron', status=404)
        if c.task_type != 'clean_backfill_failures':
            return _json_or_redirect(False, '该任务类型暂不支持测试', '/cron', status=400)

        from datetime import UTC, datetime, timedelta

        days = int(get_config('backfill_failure_retention_days', '7') or '7')
        days = max(1, min(90, days))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        count = FileLinkMap.query.filter(
            FileLinkMap.torrent_hash.is_(None),
            FileLinkMap.deleted_at.is_(None),
            db.func.coalesce(FileLinkMap.backfill_fail_count, 0) > 2,
            FileLinkMap.backfill_last_attempt_at.is_not(None),
            FileLinkMap.backfill_last_attempt_at < cutoff,
        ).count()
        return _json_or_redirect(True, f'测试完成：将重置 {count} 条（>{days}天，仅测试不写入）', '/cron')

    @web_bp.route('/cron/backup-now', methods=['POST'])
    def backup_now():
        ok, msg = run_backup_once()
        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(ok, msg, '/cron', html=html if ok else None, target='cronJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, msg, '/cron')

    @web_bp.route('/cron/toggle/<int:job_id>', methods=['POST'])
    def cron_toggle(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            return _json_or_redirect(False, '定时任务不存在', '/cron', status=404)
        c.enabled = not c.enabled
        db.session.commit()
        update_cron_job(c.id)
        state = '启用' if c.enabled else '禁用'
        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(True, f'任务 {c.name} 已{state}', '/cron', html=html, target='cronJobsPanel')
        return _json_or_redirect(True, f'任务 {c.name} 已{state}', '/cron')

    @web_bp.route('/cron/delete/<int:job_id>', methods=['POST'])
    def cron_delete(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            return _json_or_redirect(False, '定时任务不存在', '/cron', status=404)
        db.session.delete(c)
        db.session.commit()
        if _wants_json():
            payload = _cron_payload()
            html = render_template('_cron_jobs_panel.html', **payload)
            return _json_or_redirect(True, '定时任务已删除', '/cron', html=html, target='cronJobsPanel')
        return _json_or_redirect(True, '定时任务已删除', '/cron')

    return web_bp
