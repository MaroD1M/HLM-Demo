from pathlib import Path
from flask import Blueprint, render_template, request, redirect, flash, jsonify
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
    run_backup_once = ctx.run_backup_once
    run_cron_job = ctx.run_cron_job
    update_cron_job = ctx.update_cron_job
    list_torrents = ctx.list_torrents
    send_telegram_notification = ctx.send_telegram_notification

    def _wants_json():
        return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _json_or_redirect(ok, message, redirect_path, html=None, target=None, status=200):
        if _wants_json():
            return jsonify({'ok': ok, 'message': message, 'html': html, 'target': target}), status
        flash(message, 'success' if ok else 'danger')
        return redirect(redirect_path)

    def _hardlink_payload():
        return {
            'tasks': HardlinkTask.query.all(),
            'default_extensions': get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv'),
        }

    def _delete_payload():
        pending_actions = DeletePendingAction.query.filter_by(status='pending').order_by(DeletePendingAction.created_at.desc()).limit(100).all()
        return {
            'tasks': DeleteMonitorTask.query.all(),
            'downloaders': Downloader.query.all(),
            'notifiers': Notifier.query.all(),
            'pending_actions': pending_actions,
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
        return render_template(
            'dashboard.html',
            hardlink_tasks=hardlink_tasks,
            delete_tasks=delete_tasks,
            downloaders=Downloader.query.all(),
            notifiers=Notifier.query.all(),
            cron_jobs=CronJob.query.all(),
            recent_logs=OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all(),
            recent_executions=executions,
            running_count=sum(1 for t in hardlink_tasks if t.enabled) + sum(1 for t in delete_tasks if t.enabled),
            total_tasks=len(hardlink_tasks) + len(delete_tasks),
            hardlink_count=len(hardlink_tasks),
            delete_count=len(delete_tasks),
            downloader_count=Downloader.query.count(),
            notifier_count=Notifier.query.count(),
            success_runs=success_runs,
            failed_runs=failed_runs,
        )

    @web_bp.route('/hardlink')
    def hardlink_list():
        return render_template('hardlink.html', **_hardlink_payload())

    @web_bp.route('/hardlink/add', methods=['POST'])
    def hardlink_add():
        name = (request.form.get('name') or '').strip()
        source_dir = str(Path(request.form.get('source_dir', '')))
        dest_dir = str(Path(request.form.get('dest_dir', '')))
        extensions = request.form.get('extensions', '').strip() or get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv')
        exclude_dirs = request.form.get('exclude_dirs', 'sample,subs')
        create_folder = request.form.get('create_folder') == 'on'
        use_cache = request.form.get('use_cache') == 'on'

        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/hardlink', status=400)
        for label, val in [('源目录', source_dir), ('目标目录', dest_dir)]:
            ok, msg = validate_path(val)
            if not ok:
                return _json_or_redirect(False, f'{label}无效: {msg}', '/hardlink', status=400)

        min_file_age = request.form.get('min_file_age_seconds', type=int)
        if min_file_age is None or min_file_age < 0 or min_file_age > 86400:
            return _json_or_redirect(False, '最小文件年龄必须在 0-86400 秒之间', '/hardlink', status=400)

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
        exclude_dirs = request.form.get('exclude_dirs', 'sample,subs')
        exclude_extensions = request.form.get('exclude_extensions', '')
        create_folder = request.form.get('create_folder') == 'on'
        use_cache = request.form.get('use_cache') == 'on'
        min_file_age = request.form.get('min_file_age_seconds', type=int)

        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/hardlink', status=400)
        for label, val in [('源目录', source_dir), ('目标目录', dest_dir)]:
            ok, msg = validate_path(val)
            if not ok:
                return _json_or_redirect(False, f'{label}无效: {msg}', '/hardlink', status=400)
        if min_file_age is None or min_file_age < 0 or min_file_age > 86400:
            return _json_or_redirect(False, '最小文件年龄必须在 0-86400 秒之间', '/hardlink', status=400)

        task.name = name
        task.source_dir = source_dir
        task.dest_dir = dest_dir
        task.extensions = extensions
        task.exclude_dirs = exclude_dirs
        task.exclude_extensions = exclude_extensions
        task.create_folder = create_folder
        task.use_cache = use_cache
        task.min_file_age_seconds = min_file_age
        db.session.commit()
        log_operation('hardlink_task_updated', 'HardlinkTask', task.id, task.name)

        payload = _hardlink_payload()
        html = render_template('_hardlink_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '硬链接任务已更新', '/hardlink', html=html, target='hardlinkJobsPanel')

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
    def delete_monitor_add():
        name = (request.form.get('name') or '').strip()
        directory = str(Path(request.form.get('directory', '')))
        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/delete-monitor', status=400)
        ok, msg = validate_path(directory)
        if not ok:
            return _json_or_redirect(False, f'监控目录无效: {msg}', '/delete-monitor', status=400)
        cooldown = request.form.get('cooldown_seconds', type=int)
        max_deletes = request.form.get('max_deletes_per_run', type=int)
        if cooldown is None or cooldown < 0 or cooldown > 86400:
            return _json_or_redirect(False, '冷却秒数必须在 0-86400 之间', '/delete-monitor', status=400)
        if max_deletes is None or max_deletes < 1 or max_deletes > 1000:
            return _json_or_redirect(False, '单次最大删除必须在 1-1000 之间', '/delete-monitor', status=400)

        task = DeleteMonitorTask(
            name=name,
            directory=directory,
            downloader_id=request.form.get('downloader_id') or None,
            notifier_id=request.form.get('notifier_id') or None,
            events=request.form.get('events', 'unlink'),
            cooldown_seconds=cooldown,
            max_deletes_per_run=max_deletes,
            dry_run=request.form.get('dry_run') == 'on',
        )
        db.session.add(task)
        db.session.commit()
        log_operation('delete_task_created', 'DeleteMonitorTask', task.id, task.name)
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '删除监控任务已添加（定时扫描模式）', '/delete-monitor', html=html, target='deleteJobsPanel')


    @web_bp.route('/delete-monitor/update/<int:task_id>', methods=['POST'])
    def delete_monitor_update(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/delete-monitor', status=404)

        name = (request.form.get('name') or '').strip()
        directory = str(Path(request.form.get('directory', '')))
        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/delete-monitor', status=400)
        ok, msg = validate_path(directory)
        if not ok:
            return _json_or_redirect(False, f'监控目录无效: {msg}', '/delete-monitor', status=400)

        cooldown = request.form.get('cooldown_seconds', type=int)
        max_deletes = request.form.get('max_deletes_per_run', type=int)
        if cooldown is None or cooldown < 0 or cooldown > 86400:
            return _json_or_redirect(False, '冷却秒数必须在 0-86400 之间', '/delete-monitor', status=400)
        if max_deletes is None or max_deletes < 1 or max_deletes > 1000:
            return _json_or_redirect(False, '单次最大删除必须在 1-1000 之间', '/delete-monitor', status=400)

        task.name = name
        task.directory = directory
        task.events = request.form.get('events', 'unlink')
        task.downloader_id = request.form.get('downloader_id') or None
        task.notifier_id = request.form.get('notifier_id') or None
        task.cooldown_seconds = cooldown
        task.max_deletes_per_run = max_deletes
        task.dry_run = request.form.get('dry_run') == 'on'
        db.session.commit()
        log_operation('delete_task_updated', 'DeleteMonitorTask', task.id, task.name)

        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '删除联动任务已更新', '/delete-monitor', html=html, target='deleteJobsPanel')

    @web_bp.route('/delete-monitor/run/<int:task_id>', methods=['POST'])
    def delete_monitor_run(task_id):
        ok, msg = run_delete_once(task_id)
        task = db.session.get(DeleteMonitorTask, task_id)
        log_operation('delete_manual_run', 'DeleteMonitorTask', task_id, task.name if task else '-', msg, ok)
        if _wants_json():
            payload = _delete_payload()
            html = render_template('_delete_jobs_panel.html', **payload)
            return _json_or_redirect(ok, msg, '/delete-monitor', html=html if ok else None, target='deleteJobsPanel', status=200 if ok else 400)
        return _json_or_redirect(ok, msg, '/delete-monitor')

    @web_bp.route('/delete-monitor/toggle/<int:task_id>', methods=['POST'])
    def delete_monitor_toggle(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/delete-monitor', status=404)
        task.enabled = not task.enabled
        db.session.commit()
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, f'任务 {task.name} 已{"启用" if task.enabled else "禁用"}', '/delete-monitor', html=html, target='deleteJobsPanel')

    @web_bp.route('/delete-monitor/delete/<int:task_id>', methods=['POST'])
    def delete_monitor_delete(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            return _json_or_redirect(False, '任务不存在', '/delete-monitor', status=404)
        db.session.delete(task)
        db.session.commit()
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '任务已删除', '/delete-monitor', html=html, target='deleteJobsPanel')


    @web_bp.route('/delete-monitor/pending/confirm/<int:pending_id>', methods=['POST'])
    def delete_pending_confirm(pending_id):
        pending = db.session.get(DeletePendingAction, pending_id)
        if not pending or pending.status != 'pending':
            return _json_or_redirect(False, '待确认记录不存在或已处理', '/delete-monitor', status=404)

        task = db.session.get(DeleteMonitorTask, pending.task_id)
        if not task or not task.downloader:
            return _json_or_redirect(False, '关联任务或下载器不可用', '/delete-monitor', status=400)

        torrent_hash = (pending.torrent_hash or '').strip()
        if not torrent_hash:
            pending.status = 'rejected'
            pending.confirmed_at = db.func.now()
            db.session.commit()
            return _json_or_redirect(False, '待确认记录缺少种子哈希，已驳回', '/delete-monitor', status=400)

        from flask import current_app
        delete_torrent = current_app.config.get('DELETE_TORRENT_FUNC')
        if not callable(delete_torrent):
            return _json_or_redirect(False, '删除执行器不可用', '/delete-monitor', status=500)

        ok = delete_torrent(task.downloader, torrent_hash)
        pending.status = 'confirmed' if ok else 'failed'
        pending.confirmed_at = db.func.now()
        db.session.commit()

        log_operation('pending_delete_confirmed' if ok else 'pending_delete_failed', 'DeletePendingAction', pending.id, task.name, f'手动确认删除种子 {torrent_hash}', ok)
        payload = _delete_payload()
        html = render_template('_delete_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(ok, '已确认删除并执行' if ok else '执行删除失败', '/delete-monitor', html=html if ok else None, target='deleteJobsPanel', status=200 if ok else 400)

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

        d = Downloader(name=name, type=request.form.get('type', 'qbittorrent'), host=host, port=port, username=request.form.get('username'))
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
        new_password = request.form.get('password')
        if (new_password or '').strip():
            d.set_password(new_password)
        db.session.commit()
        log_operation('downloader_updated', 'Downloader', d.id, d.name)
        payload = _downloader_payload()
        html = render_template('_downloader_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '下载器已更新', '/downloader', html=html, target='downloaderJobsPanel')

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
        n = Notifier(name=name, type=request.form.get('type', 'telegram'), api_key=api_key, chat_id=request.form.get('chat_id'))
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
        db.session.commit()
        log_operation('notifier_updated', 'Notifier', n.id, n.name)
        payload = _notifier_payload()
        html = render_template('_notifier_jobs_panel.html', **payload) if _wants_json() else None
        return _json_or_redirect(True, '通知器已更新', '/notifier', html=html, target='notifierJobsPanel')

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


    @web_bp.route('/mapping')
    def mapping_list():
        page = max(request.args.get('page', 1, type=int), 1)
        cache_page = max(request.args.get('cache_page', 1, type=int), 1)
        q = (request.args.get('q') or '').strip()
        hash_state = (request.args.get('hash_state') or 'all').strip()

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

        mapping_q = mapping_q.order_by(FileLinkMap.created_at.desc())
        mapping_pg = mapping_q.paginate(page=page, per_page=100, error_out=False)

        cache_q = HardlinkCache.query
        if q:
            like = f"%{q}%"
            cache_q = cache_q.filter((HardlinkCache.source_path.like(like)) | (HardlinkCache.dest_path.like(like)))
        cache_q = cache_q.order_by(HardlinkCache.created_at.desc())
        cache_pg = cache_q.paginate(page=cache_page, per_page=100, error_out=False)

        return render_template(
            'mapping.html',
            mappings=mapping_pg.items,
            map_page=page,
            map_total_pages=max(mapping_pg.pages, 1),
            caches=cache_pg.items,
            cache_page=cache_page,
            cache_total_pages=max(cache_pg.pages, 1),
            q=q,
            hash_state=hash_state,
        )

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
        return _json_or_redirect(True, '缓存记录已删除，可再次硬链接', '/mapping')

    @web_bp.route('/mapping/cache/clear', methods=['POST'])
    def mapping_cache_clear():
        count = HardlinkCache.query.delete()
        db.session.commit()
        log_operation('cache_cleared', 'HardlinkCache', None, '全部缓存', f'清理 {count} 条缓存')
        return _json_or_redirect(True, f'已清理 {count} 条缓存', '/mapping')

    @web_bp.route('/logs')
    def logs_list():
        page = max(request.args.get('page', 1, type=int), 1)
        pagination = OperationLog.query.order_by(OperationLog.created_at.desc()).paginate(page=page, per_page=100, error_out=False)
        return render_template(
            'logs.html',
            logs=pagination.items,
            page=page,
            total_pages=max(pagination.pages, 1),
            executions=JobExecutionLog.query.order_by(JobExecutionLog.started_at.desc()).limit(100).all(),
        )

    @web_bp.route('/logs/clear', methods=['POST'])
    def logs_clear():
        OperationLog.query.delete()
        db.session.commit()
        flash('操作日志已清空', 'success')
        return redirect('/logs')

    @web_bp.route('/settings')
    def settings_page():
        settings = {c.key: c.value for c in AppConfig.query.all()}
        return render_template('settings.html', settings=settings)

    @web_bp.route('/settings/save', methods=['POST'])
    def settings_save():
        for key in [
            'log_retention_days', 'auto_clean_logs', 'default_extensions', 'default_exclude_dirs',
            'delete_files_with_torrent', 'delete_delay_seconds', 'notify_on_hardlink', 'notify_on_delete',
            'allowed_roots', 'tg_proxy_url', 'tg_api_base', 'backup_dir', 'backup_keep_last', 'notify_on_risky_delete', 'delete_match_strict_mode',
        ]:
            val = request.form.get(key)
            if val is not None:
                set_config(key, val.strip() if isinstance(val, str) else val)
        flash('设置已保存', 'success')
        return redirect('/settings')

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

        allowed_types = {'batch_hardlink', 'delete_scan', 'backfill_mapping', 'clean_logs', 'clean_cache', 'db_backup'}
        if task_type not in allowed_types:
            return _json_or_redirect(False, '不支持的任务类型', '/cron', status=400)
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            return _json_or_redirect(False, '请选择有效的硬链接任务', '/cron', status=400)
        if task_type == 'delete_scan' and (not target_id or not db.session.get(DeleteMonitorTask, target_id)):
            return _json_or_redirect(False, '请选择有效的删除监控任务', '/cron', status=400)
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
        cron_expression = (request.form.get('custom_cron') or '').strip()
        if not name:
            return _json_or_redirect(False, '任务名称不能为空', '/cron', status=400)
        if not validate_cron_expression(cron_expression):
            return _json_or_redirect(False, 'Cron 表达式格式错误（应为 5 段，例如 0 3 * * *）', '/cron', status=400)

        c.name = name
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
        elif c.task_type == 'delete_scan':
            ok, msg = run_delete_once(c.target_id)
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
