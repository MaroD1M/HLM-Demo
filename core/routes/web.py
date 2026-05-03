from pathlib import Path
from flask import Blueprint, render_template, request, redirect, flash
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
    AppConfig = ctx.AppConfig
    CronJob = ctx.CronJob
    db = ctx.db

    get_config = ctx.get_config
    set_config = ctx.set_config
    log_operation = ctx.log_operation
    validate_path = ctx.validate_path
    validate_host = ctx.validate_host
    validate_cron_expression = ctx.validate_cron_expression

    scan_hardlink_task = ctx.scan_hardlink_task
    update_cron_job = ctx.update_cron_job
    list_torrents = ctx.list_torrents
    send_telegram_notification = ctx.send_telegram_notification

    @web_bp.route('/')
    def dashboard():
        hardlink_tasks = HardlinkTask.query.all()
        delete_tasks = DeleteMonitorTask.query.all()
        return render_template(
            'dashboard.html',
            hardlink_tasks=hardlink_tasks,
            delete_tasks=delete_tasks,
            downloaders=Downloader.query.all(),
            notifiers=Notifier.query.all(),
            cron_jobs=CronJob.query.all(),
            recent_logs=OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all(),
            running_count=sum(1 for t in hardlink_tasks if t.enabled) + sum(1 for t in delete_tasks if t.enabled),
            total_tasks=len(hardlink_tasks) + len(delete_tasks),
            hardlink_count=len(hardlink_tasks),
            delete_count=len(delete_tasks),
            downloader_count=Downloader.query.count(),
            notifier_count=Notifier.query.count(),
        )

    @web_bp.route('/hardlink')
    def hardlink_list():
        return render_template('hardlink.html', tasks=HardlinkTask.query.all(), default_extensions=get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv'))

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
            flash('任务名称不能为空', 'danger')
            return redirect('/hardlink')

        for label, val in [('源目录', source_dir), ('目标目录', dest_dir)]:
            ok, msg = validate_path(val)
            if not ok:
                flash(f'{label}无效: {msg}', 'danger')
                return redirect('/hardlink')

        min_file_age = request.form.get('min_file_age_seconds', type=int)
        if min_file_age is None or min_file_age < 0 or min_file_age > 86400:
            flash('最小文件年龄必须在 0-86400 秒之间', 'danger')
            return redirect('/hardlink')

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
        flash('硬链接任务已添加（定时扫描模式）', 'success')
        return redirect('/hardlink')

    @web_bp.route('/hardlink/toggle/<int:task_id>', methods=['POST'])
    def hardlink_toggle(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            flash('任务不存在', 'danger')
            return redirect('/hardlink')
        task.enabled = not task.enabled
        db.session.commit()
        log_operation('hardlink_task_toggled', 'HardlinkTask', task.id, task.name, f"状态: {'已启用' if task.enabled else '已禁用'}")
        flash(f'任务 {task.name} 已{"启用" if task.enabled else "禁用"}', 'success')
        return redirect('/hardlink')

    @web_bp.route('/hardlink/delete/<int:task_id>', methods=['POST'])
    def hardlink_delete(task_id):
        task = db.session.get(HardlinkTask, task_id)
        if not task:
            flash('任务不存在', 'danger')
            return redirect('/hardlink')
        HardlinkCache.query.filter(HardlinkCache.source_path.like(f"{task.source_dir}%")).delete()
        FileLinkMap.query.filter_by(task_id=task.id).delete()
        db.session.delete(task)
        db.session.commit()
        log_operation('hardlink_task_deleted', 'HardlinkTask', task_id, task.name)
        flash('任务已删除', 'success')
        return redirect('/hardlink')

    @web_bp.route('/hardlink/batch/<int:task_id>', methods=['POST'])
    @web_bp.route('/hardlink/execute/<int:task_id>', methods=['POST'])
    def hardlink_execute(task_id):
        ok, msg = scan_hardlink_task(task_id)
        flash(msg, 'success' if ok else 'danger')
        return redirect('/hardlink')

    @web_bp.route('/delete-monitor')
    def delete_monitor_list():
        return render_template('delete_monitor.html', tasks=DeleteMonitorTask.query.all(), downloaders=Downloader.query.all(), notifiers=Notifier.query.all())

    @web_bp.route('/delete-monitor/add', methods=['POST'])
    def delete_monitor_add():
        name = (request.form.get('name') or '').strip()
        directory = str(Path(request.form.get('directory', '')))
        if not name:
            flash('任务名称不能为空', 'danger')
            return redirect('/delete-monitor')
        ok, msg = validate_path(directory)
        if not ok:
            flash(f'监控目录无效: {msg}', 'danger')
            return redirect('/delete-monitor')
        cooldown = request.form.get('cooldown_seconds', type=int)
        max_deletes = request.form.get('max_deletes_per_run', type=int)
        if cooldown is None or cooldown < 0 or cooldown > 86400:
            flash('冷却秒数必须在 0-86400 之间', 'danger')
            return redirect('/delete-monitor')
        if max_deletes is None or max_deletes < 1 or max_deletes > 1000:
            flash('单次最大删除必须在 1-1000 之间', 'danger')
            return redirect('/delete-monitor')
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
        flash('删除监控任务已添加（定时扫描模式）', 'success')
        return redirect('/delete-monitor')

    @web_bp.route('/delete-monitor/toggle/<int:task_id>', methods=['POST'])
    def delete_monitor_toggle(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            flash('任务不存在', 'danger')
            return redirect('/delete-monitor')
        task.enabled = not task.enabled
        db.session.commit()
        flash(f'任务 {task.name} 已{"启用" if task.enabled else "禁用"}', 'success')
        return redirect('/delete-monitor')

    @web_bp.route('/delete-monitor/delete/<int:task_id>', methods=['POST'])
    def delete_monitor_delete(task_id):
        task = db.session.get(DeleteMonitorTask, task_id)
        if not task:
            flash('任务不存在', 'danger')
            return redirect('/delete-monitor')
        db.session.delete(task)
        db.session.commit()
        flash('任务已删除', 'success')
        return redirect('/delete-monitor')

    @web_bp.route('/downloader')
    def downloader_list():
        return render_template('downloader.html', downloaders=Downloader.query.all())

    @web_bp.route('/downloader/add', methods=['POST'])
    def downloader_add():
        name = (request.form.get('name') or '').strip()
        host = (request.form.get('host') or '').rstrip('/')
        port = request.form.get('port', type=int)
        if not name:
            flash('下载器名称不能为空', 'danger')
            return redirect('/downloader')
        ok, msg = validate_host(host)
        if not ok:
            flash(f'主机地址无效: {msg}', 'danger')
            return redirect('/downloader')
        if port is None or port < 1 or port > 65535:
            flash('端口号必须在 1-65535', 'danger')
            return redirect('/downloader')
        d = Downloader(name=name, type=request.form.get('type', 'qbittorrent'), host=host, port=port, username=request.form.get('username'))
        d.set_password(request.form.get('password'))
        db.session.add(d)
        db.session.commit()
        flash('下载器已添加', 'success')
        return redirect('/downloader')

    @web_bp.route('/downloader/test/<int:downloader_id>', methods=['POST'])
    def downloader_test(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            flash('下载器不存在', 'danger')
            return redirect('/downloader')
        torrents = list_torrents(d)
        flash(f'连接成功，种子数: {len(torrents)}', 'success') if torrents is not None else flash('连接失败，请检查配置', 'danger')
        return redirect('/downloader')

    @web_bp.route('/downloader/toggle/<int:downloader_id>', methods=['POST'])
    def downloader_toggle(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            flash('下载器不存在', 'danger')
            return redirect('/downloader')
        d.enabled = not d.enabled
        db.session.commit()
        return redirect('/downloader')

    @web_bp.route('/downloader/delete/<int:downloader_id>', methods=['POST'])
    def downloader_delete(downloader_id):
        d = db.session.get(Downloader, downloader_id)
        if not d:
            flash('下载器不存在', 'danger')
            return redirect('/downloader')
        db.session.delete(d)
        db.session.commit()
        return redirect('/downloader')

    @web_bp.route('/notifier')
    def notifier_list():
        return render_template('notifier.html', notifiers=Notifier.query.all())

    @web_bp.route('/notifier/add', methods=['POST'])
    def notifier_add():
        name = (request.form.get('name') or '').strip()
        api_key = (request.form.get('api_key') or '').strip()
        if not name or not api_key:
            flash('通知器名称和API Key不能为空', 'danger')
            return redirect('/notifier')
        n = Notifier(name=name, type=request.form.get('type', 'telegram'), api_key=api_key, chat_id=request.form.get('chat_id'))
        db.session.add(n)
        db.session.commit()
        flash('通知器已添加', 'success')
        return redirect('/notifier')

    @web_bp.route('/notifier/test/<int:notifier_id>', methods=['POST'])
    def notifier_test(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            flash('通知器不存在', 'danger')
            return redirect('/notifier')
        ok = send_telegram_notification(n, 'Hardlink Manager 测试通知')
        flash('发送成功' if ok else '发送失败', 'success' if ok else 'danger')
        return redirect('/notifier')

    @web_bp.route('/notifier/toggle/<int:notifier_id>', methods=['POST'])
    def notifier_toggle(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            flash('通知器不存在', 'danger')
            return redirect('/notifier')
        n.enabled = not n.enabled
        db.session.commit()
        return redirect('/notifier')

    @web_bp.route('/notifier/delete/<int:notifier_id>', methods=['POST'])
    def notifier_delete(notifier_id):
        n = db.session.get(Notifier, notifier_id)
        if not n:
            flash('通知器不存在', 'danger')
            return redirect('/notifier')
        db.session.delete(n)
        db.session.commit()
        return redirect('/notifier')

    @web_bp.route('/logs')
    def logs_list():
        return render_template('logs.html', logs=OperationLog.query.order_by(OperationLog.created_at.desc()).all())

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
        for key in ['log_retention_days', 'auto_clean_logs', 'default_extensions', 'default_exclude_dirs', 'delete_files_with_torrent', 'delete_delay_seconds', 'notify_on_hardlink', 'notify_on_delete', 'allowed_roots']:
            val = request.form.get(key)
            if val is not None:
                set_config(key, val.strip() if isinstance(val, str) else val)
        flash('设置已保存', 'success')
        return redirect('/settings')

    @web_bp.route('/cron')
    def cron_list():
        return render_template('cron.html', jobs=CronJob.query.all(), hardlink_tasks=HardlinkTask.query.all(), delete_tasks=DeleteMonitorTask.query.all())

    @web_bp.route('/cron/add', methods=['POST'])
    def cron_add():
        name = (request.form.get('name') or '').strip()
        task_type = (request.form.get('task_type') or '').strip()
        target_id = request.form.get('target_id', type=int)
        cron_expression = (request.form.get('custom_cron') or request.form.get('cron_expression') or '').strip()
        if not name:
            flash('任务名称不能为空', 'danger')
            return redirect('/cron')
        if not validate_cron_expression(cron_expression):
            flash('Cron 表达式格式错误', 'danger')
            return redirect('/cron')
        allowed_types = {'batch_hardlink', 'delete_scan', 'backfill_mapping', 'clean_logs', 'clean_cache'}
        if task_type not in allowed_types:
            flash('不支持的任务类型', 'danger')
            return redirect('/cron')
        if task_type == 'batch_hardlink' and (not target_id or not db.session.get(HardlinkTask, target_id)):
            flash('请选择有效的硬链接任务', 'danger')
            return redirect('/cron')
        if task_type == 'delete_scan' and (not target_id or not db.session.get(DeleteMonitorTask, target_id)):
            flash('请选择有效的删除监控任务', 'danger')
            return redirect('/cron')
        if task_type in {'clean_logs', 'clean_cache', 'backfill_mapping'}:
            target_id = None
        c = CronJob(name=name, task_type=task_type, target_id=target_id, cron_expression=cron_expression, description=request.form.get('description'))
        db.session.add(c)
        db.session.commit()
        update_cron_job(c.id)
        flash('定时任务已添加', 'success')
        return redirect('/cron')

    @web_bp.route('/cron/toggle/<int:job_id>', methods=['POST'])
    def cron_toggle(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            flash('定时任务不存在', 'danger')
            return redirect('/cron')
        c.enabled = not c.enabled
        db.session.commit()
        update_cron_job(c.id)
        return redirect('/cron')

    @web_bp.route('/cron/delete/<int:job_id>', methods=['POST'])
    def cron_delete(job_id):
        c = db.session.get(CronJob, job_id)
        if not c:
            flash('定时任务不存在', 'danger')
            return redirect('/cron')
        db.session.delete(c)
        db.session.commit()
        return redirect('/cron')

    return web_bp
