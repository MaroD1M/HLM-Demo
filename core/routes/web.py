from pathlib import Path
import json
from flask import Blueprint, render_template, request, redirect, flash, jsonify, Response
from core.deps import RouteDeps

web_bp = Blueprint('web_bp', __name__)


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

    def _critical_guard():
        expected = (get_config('critical_action_passphrase', '') or '').strip()
        if not expected:
            return True
        provided = (request.form.get('critical_passphrase') or '').strip()
        return provided == expected


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
        return {
            'jobs': jobs,
            'cron_hints': {j.id: _cron_human_text(j.cron_expression) for j in jobs},
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

        running_snaps = get_running_executions_snapshot()
        running_ids = set([x.get('execution_id') for x in running_snaps if x.get('execution_id')])
        stale_running_ids = set([e.id for e in executions if e.status == 'running' and e.id not in running_ids])

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
        return render_template('hardlink.html', **_hardlink_payload())

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

        d = Downloader(name=name, type=request.form.get('type', 'qbittorrent'), host=host, port=port, username=request.form.get('username'), proxy_url=(request.form.get('proxy_url') or '').strip() or None)
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

        d.name = name
        d.type = request.form.get('type', 'qbittorrent')
        d.host = host
        d.port = port
        d.username = request.form.get('username')
        d.proxy_url = (request.form.get('proxy_url') or '').strip() or None
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


    def _mapping_payload(page=None, cache_page=None, q=None, hash_state=None, source_type=None):
        page = max(page if page is not None else request.args.get('page', 1, type=int), 1)
        cache_page = max(cache_page if cache_page is not None else request.args.get('cache_page', 1, type=int), 1)
        q = (q if q is not None else request.args.get('q') or '').strip()
        hash_state = (hash_state if hash_state is not None else request.args.get('hash_state') or 'all').strip()
        source_type = (source_type if source_type is not None else request.args.get('source_type') or 'all').strip()

        mapping_q = FileLinkMap.query
        if q:
            like = f"%{q}%"
            mapping_q = mapping_q.filter(
                (FileLinkMap.source_path.like(like)) |
                (FileLinkMap.dest_path.like(like)) |
                (FileLinkMap.torrent_hash.like(like))
            )
        if hash_state == 'linked':
            mapping_q = mapping_q.filter(FileLinkMap.torrent_hash.isnot(None))
        elif hash_state == 'unlinked':
            mapping_q = mapping_q.filter(FileLinkMap.torrent_hash.is_(None))
        if source_type == 'manual':
            mapping_q = mapping_q.filter(FileLinkMap.source_type == 'manual')
        elif source_type == 'downloader':
            mapping_q = mapping_q.filter(FileLinkMap.source_type == 'downloader')

        mapping_q = mapping_q.order_by(FileLinkMap.created_at.desc())
        mapping_pg = mapping_q.paginate(page=page, per_page=50, error_out=False)

        cache_q = HardlinkCache.query
        if q:
            like = f"%{q}%"
            cache_q = cache_q.filter((HardlinkCache.source_path.like(like)) | (HardlinkCache.dest_path.like(like)))
        cache_q = cache_q.order_by(HardlinkCache.created_at.desc())
        cache_pg = cache_q.paginate(page=cache_page, per_page=50, error_out=False)

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
        q = (request.args.get('q') or '').strip()
        op = (request.args.get('op') or '').strip()
        success = (request.args.get('success') or 'all').strip()

        log_q = OperationLog.query
        if q:
            like = f"%{q}%"
            log_q = log_q.filter((OperationLog.message.like(like)) | (OperationLog.target_name.like(like)) | (OperationLog.operation_type.like(like)))
        if op:
            log_q = log_q.filter(OperationLog.operation_type == op)
        if success == 'ok':
            log_q = log_q.filter(OperationLog.success.is_(True))
        elif success == 'fail':
            log_q = log_q.filter(OperationLog.success.is_(False))

        pagination = log_q.order_by(OperationLog.created_at.desc()).paginate(page=page, per_page=50, error_out=False)
        operations = [r[0] for r in db.session.query(OperationLog.operation_type).distinct().order_by(OperationLog.operation_type.asc()).all()]
        return render_template(
            'logs.html',
            logs=pagination.items,
            page=page,
            total_pages=max(pagination.pages, 1),
            executions=JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc()).limit(50).all(),
            q=q,
            op=op,
            success=success,
            operations=operations,
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

    @web_bp.route('/settings')
    def settings_page():
        settings = {c.key: c.value for c in AppConfig.query.all()}
        release = get_release_info()
        return render_template('settings.html', settings=settings, release=release)

    @web_bp.route('/settings/save', methods=['POST'])
    def settings_save():
        for key in [
            'log_retention_days', 'auto_clean_logs', 'default_extensions', 'default_exclude_dirs',
            'delete_files_with_torrent', 'notify_on_delete',
            'allowed_roots', 'proxy_url', 'tg_api_base', 'backup_dir', 'backup_keep_last', 'notify_on_risky_delete', 'delete_match_strict_mode',
            'manual_dest_delete_delete_source', 'manual_source_delete_delete_dest',
            'downloader_dest_delete_delete_source', 'downloader_source_delete_delete_dest',
            'downloader_dest_delete_delete_torrent', 'downloader_source_delete_delete_torrent',
            'github_version_check_enabled', 'github_repo', 'github_api_base',
            'app_log_max_mb', 'app_log_backup_count', 'version_check_cache_minutes', 'critical_action_passphrase',
        ]:
            val = request.form.get(key)
            if val is not None:
                set_config(key, val.strip() if isinstance(val, str) else val)
        return _json_or_redirect(True, '设置已保存并生效', '/settings')


    @web_bp.route('/settings/check-update', methods=['POST'])
    def settings_check_update():
        info = get_release_info(force_refresh=True)
        return _json_or_redirect(True, f"版本检查完成：本地 {info.get('local_version','-')}，远端 {info.get('remote_version','-')}（{info.get('message','-')}）", '/settings')


    @web_bp.route('/settings/export', methods=['GET'])
    def settings_export():
        data = {c.key: c.value for c in AppConfig.query.all()}
        for k in ('critical_action_passphrase',):
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
        allowed = {
            'log_retention_days','auto_clean_logs','default_extensions','default_exclude_dirs','delete_files_with_torrent','notify_on_delete',
            'allowed_roots','proxy_url','tg_api_base','backup_dir','backup_keep_last','notify_on_risky_delete','delete_match_strict_mode',
            'manual_dest_delete_delete_source','manual_source_delete_delete_dest','downloader_dest_delete_delete_source','downloader_source_delete_delete_dest',
            'downloader_dest_delete_delete_torrent','downloader_source_delete_delete_torrent','github_version_check_enabled','github_repo','github_api_base',
            'app_log_max_mb','app_log_backup_count','version_check_cache_minutes','critical_action_passphrase'
        }
        applied = 0
        for k,v in payload.items():
            if k in allowed and v is not None and v != '***':
                set_config(k, str(v))
                applied += 1
        return _json_or_redirect(True, f'配置导入完成，已更新 {applied} 项', '/settings')

    @web_bp.route('/diagnostics')
    def diagnostics_page():
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
        checks.append(('数据库结构版本', True, f'current={current_schema}, target=7'))

        checks.append(('代理配置', True, (get_config('proxy_url','') or '').strip() or '未设置（直连）'))
        checks.append(('应用版本', True, get_release_info().get('local_version','-')))
        checks.append(('日志目录', True, str(Path('data/logs').resolve())))

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

        return render_template('diagnostics.html', checks=checks, running_rows=running_rows)

    @web_bp.route('/delete-monitor/preview/<int:task_id>', methods=['POST'])
    def delete_monitor_preview(task_id):
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
        return _json_or_redirect(True, f'预演完成：当前可能触发联动 {hits} 条（仅预估，不执行删除）', '/delete-monitor')

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

        allowed_types = {'batch_hardlink', 'backfill_mapping', 'clean_logs', 'clean_cache', 'db_backup'}
        if task_type not in allowed_types:
            return _json_or_redirect(False, '不支持的任务类型', '/cron', status=400)
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            return _json_or_redirect(False, '请选择有效的硬链接任务', '/cron', status=400)
        if task_type in {'clean_logs', 'clean_cache', 'backfill_mapping', 'db_backup'}:
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

        allowed_types = {'batch_hardlink', 'backfill_mapping', 'clean_logs', 'clean_cache', 'db_backup'}
        if task_type not in allowed_types:
            return _json_or_redirect(False, '不支持的任务类型', '/cron', status=400)
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            return _json_or_redirect(False, '请选择有效的硬链接任务', '/cron', status=400)
        if task_type in {'clean_logs', 'clean_cache', 'backfill_mapping', 'db_backup'}:
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
