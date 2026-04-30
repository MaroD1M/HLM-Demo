import os
import threading
import asyncio
from datetime import datetime, UTC, timedelta
from pathlib import Path
from flask import Flask, render_template, request, redirect, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import requests
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler

def get_encryption_key():
    secret_key = app.config.get('SECRET_KEY', 'default-secret-key-for-dev-only')
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(secret_key.encode())
    return digest.finalize()[:32]

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key-for-dev-only')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hardlink_manager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
scheduler = BackgroundScheduler(timezone='UTC')
bcrypt = Bcrypt(app)

observers = {}
observer_lock = threading.Lock()

class HardlinkTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    source_dir = db.Column(db.String(500), nullable=False)
    dest_dir = db.Column(db.String(500), nullable=False)
    extensions = db.Column(db.String(500), default='.mkv,.mp4,.avi,.mov,.wmv,.flv')
    exclude_dirs = db.Column(db.String(500), default='sample,subs')
    create_folder = db.Column(db.Boolean, default=True)
    use_cache = db.Column(db.Boolean, default=True)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def get_extensions_list(self):
        if not self.extensions:
            return []
        return [e.strip().lower() for e in self.extensions.split(',')]

    def get_exclude_dirs_list(self):
        if not self.exclude_dirs:
            return []
        return [d.strip().lower() for d in self.exclude_dirs.split(',')]

class DeleteMonitorTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    directory = db.Column(db.String(500), nullable=False)
    downloader_id = db.Column(db.Integer, db.ForeignKey('downloader.id'))
    notifier_id = db.Column(db.Integer, db.ForeignKey('notifier.id'))
    events = db.Column(db.String(100), default='unlink,unlinkDir')
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    downloader = db.relationship('Downloader', backref=db.backref('delete_tasks', lazy=True))
    notifier = db.relationship('Notifier', backref=db.backref('delete_tasks', lazy=True))

class Downloader(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), default='qbittorrent')
    host = db.Column(db.String(200), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(100))
    encrypted_password = db.Column(db.String(500))
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def set_password(self, password):
        if password:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            import os
            iv = os.urandom(16)
            cipher = Cipher(algorithms.AES(get_encryption_key()), modes.CBC(iv), backend=default_backend())
            encryptor = cipher.encryptor()
            padding = 16 - (len(password) % 16)
            password_padded = password + chr(padding) * padding
            encrypted = encryptor.update(password_padded.encode()) + encryptor.finalize()
            self.encrypted_password = (iv + encrypted).hex()

    def get_password(self):
        if not self.encrypted_password:
            return None
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        data = bytes.fromhex(self.encrypted_password)
        iv = data[:16]
        encrypted = data[16:]
        cipher = Cipher(algorithms.AES(get_encryption_key()), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        password_padded = decryptor.update(encrypted) + decryptor.finalize()
        padding = ord(password_padded[-1:])
        return password_padded[:-padding].decode()

class Notifier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), default='telegram')
    api_key = db.Column(db.String(500), nullable=False)
    chat_id = db.Column(db.String(100))
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

class HardlinkCache(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_path = db.Column(db.String(1000), nullable=False, unique=True)
    dest_path = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

class OperationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    operation_type = db.Column(db.String(50), nullable=False)
    target_type = db.Column(db.String(50))
    target_id = db.Column(db.Integer)
    target_name = db.Column(db.String(200))
    message = db.Column(db.String(1000))
    success = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

class AppConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(1000))
    description = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

class CronJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    task_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.Integer)
    cron_expression = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    PRESET_CRON_EXPRESSIONS = {
        '每分钟': '* * * * *',
        '每5分钟': '*/5 * * * *',
        '每10分钟': '*/10 * * * *',
        '每15分钟': '*/15 * * * *',
        '每30分钟': '*/30 * * * *',
        '每小时': '0 * * * *',
        '每2小时': '0 */2 * * *',
        '每6小时': '0 */6 * * *',
        '每天凌晨2点': '0 2 * * *',
        '每天早上6点': '0 6 * * *',
        '每天中午12点': '0 12 * * *',
        '每周一凌晨2点': '0 2 * * 1',
        '每月1号凌晨2点': '0 2 1 * *',
    }

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
        config = AppConfig(key=key, value=value, description=description)
        db.session.add(config)
    db.session.commit()

def log_operation(operation_type, target_type=None, target_id=None, target_name=None, message=None, success=True):
    log = OperationLog(
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        message=message,
        success=success
    )
    db.session.add(log)
    db.session.commit()

def send_telegram_notification(notifier, message):
    try:
        async def send():
            bot = Bot(token=notifier.api_key)
            await bot.send_message(chat_id=notifier.chat_id, text=message)
        asyncio.run(send())
        return True
    except Exception as e:
        app.logger.error(f"Failed to send Telegram notification: {e}")
        return False

def get_qbittorrent_torrents(downloader):
    try:
        url = f"{downloader.host}:{downloader.port}/api/v2/torrents/info"
        session = requests.Session()
        if downloader.username and downloader.encrypted_password:
            login_url = f"{downloader.host}:{downloader.port}/api/v2/auth/login"
            session.post(login_url, data={
                'username': downloader.username,
                'password': downloader.get_password()
            })
        response = session.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        app.logger.error(f"Failed to get qBittorrent torrents: {e}")
        return []

def delete_qbittorrent_torrent(downloader, torrent_hash):
    try:
        url = f"{downloader.host}:{downloader.port}/api/v2/torrents/delete"
        session = requests.Session()
        if downloader.username and downloader.encrypted_password:
            login_url = f"{downloader.host}:{downloader.port}/api/v2/auth/login"
            session.post(login_url, data={
                'username': downloader.username,
                'password': downloader.get_password()
            })
        delete_files = get_config('delete_files_with_torrent', 'false') == 'true'
        response = session.post(url, data={
            'hashes': torrent_hash,
            'deleteFiles': 'true' if delete_files else 'false'
        })
        response.raise_for_status()
        return True
    except Exception as e:
        app.logger.error(f"Failed to delete qBittorrent torrent: {e}")
        return False

class HardlinkHandler(FileSystemEventHandler):
    def __init__(self, task, app_instance):
        self.task = task
        self.app = app_instance

    def on_created(self, event):
        if event.is_directory:
            return
        
        with self.app.app_context():
            file_path = Path(event.src_path)
            file_ext = file_path.suffix.lower()
            
            if file_ext not in self.task.get_extensions_list():
                return
            
            for exclude_dir in self.task.get_exclude_dirs_list():
                if exclude_dir.lower() in str(file_path.parent).lower():
                    return
            
            if self.task.use_cache:
                cache_entry = HardlinkCache.query.filter_by(source_path=str(file_path)).first()
                if cache_entry:
                    app.logger.info(f"File already hard-linked: {file_path}")
                    return
            
            try:
                dest_path = Path(self.task.dest_dir)
                if self.task.create_folder:
                    dest_path = dest_path / file_path.parent.name
                    dest_path.mkdir(parents=True, exist_ok=True)
                
                dest_file = dest_path / file_path.name
                
                if dest_file.exists():
                    dest_file.unlink()
                
                os.link(file_path, dest_file)
                
                if self.task.use_cache:
                    cache_entry = HardlinkCache(source_path=str(file_path), dest_path=str(dest_file))
                    db.session.add(cache_entry)
                    db.session.commit()
                
                log_operation('hardlink_created', 'HardlinkTask', self.task.id, self.task.name, 
                            f"Created hard link: {file_path} -> {dest_file}")
                
                if get_config('notify_on_hardlink', 'false') == 'true' and self.task.enabled:
                    notifier = Notifier.query.filter_by(enabled=True).first()
                    if notifier:
                        send_telegram_notification(notifier, f"硬链接已创建\n{file_path}\n-> {dest_file}")
                
            except Exception as e:
                app.logger.error(f"Failed to create hard link: {e}")
                log_operation('hardlink_failed', 'HardlinkTask', self.task.id, self.task.name, 
                            f"Failed to create hard link: {e}", success=False)

class DeleteHandler(FileSystemEventHandler):
    def __init__(self, task, app_instance):
        self.task = task
        self.app = app_instance

    def on_deleted(self, event):
        if event.is_directory and 'unlinkDir' not in self.task.events:
            return
        if not event.is_directory and 'unlink' not in self.task.events:
            return
        
        with self.app.app_context():
            deleted_path = Path(event.src_path)
            
            if self.task.downloader:
                torrents = get_qbittorrent_torrents(self.task.downloader)
                for torrent in torrents:
                    torrent_name = torrent.get('name', '')
                    if deleted_path.name in torrent_name or str(deleted_path.parent) in torrent.get('save_path', ''):
                        delete_delay = int(get_config('delete_delay_seconds', '0'))
                        if delete_delay > 0:
                            import time
                            time.sleep(delete_delay)
                        
                        success = delete_qbittorrent_torrent(self.task.downloader, torrent['hash'])
                        
                        if success:
                            log_operation('torrent_deleted', 'DeleteMonitorTask', self.task.id, self.task.name,
                                        f"Deleted torrent: {torrent_name} (triggered by: {deleted_path})")
                            
                            if get_config('notify_on_delete', 'false') == 'true' and self.task.notifier:
                                send_telegram_notification(self.task.notifier, 
                                                          f"种子已删除\n{torrent_name}\n触发源: {deleted_path}")
                        else:
                            log_operation('torrent_delete_failed', 'DeleteMonitorTask', self.task.id, self.task.name,
                                        f"Failed to delete torrent: {torrent_name}", success=False)

def start_hardlink_observer(hardlink_task):
    with observer_lock:
        if f"hardlink_{hardlink_task.id}" in observers:
            observers[f"hardlink_{hardlink_task.id}"].stop()
        
        event_handler = HardlinkHandler(hardlink_task, app)
        observer = Observer()
        observer.schedule(event_handler, hardlink_task.source_dir, recursive=True)
        observer.start()
        observers[f"hardlink_{hardlink_task.id}"] = observer
        app.logger.info(f"Started hardlink observer for task: {hardlink_task.name}")

def start_delete_observer(delete_task):
    with observer_lock:
        if f"delete_{delete_task.id}" in observers:
            observers[f"delete_{delete_task.id}"].stop()
        
        event_handler = DeleteHandler(delete_task, app)
        observer = Observer()
        observer.schedule(event_handler, delete_task.directory, recursive=True)
        observer.start()
        observers[f"delete_{delete_task.id}"] = observer
        app.logger.info(f"Started delete observer for task: {delete_task.name}")

def stop_observer(observer_key):
    with observer_lock:
        if observer_key in observers:
            observers[observer_key].stop()
            observers[observer_key].join()
            del observers[observer_key]

def batch_create_hardlinks(task_id):
    with app.app_context():
        task = HardlinkTask.query.get(task_id)
        if not task:
            return False, "任务不存在"
        
        try:
            source_path = Path(task.source_dir)
            count = 0
            
            for file_path in source_path.rglob('*'):
                if file_path.is_file():
                    file_ext = file_path.suffix.lower()
                    if file_ext not in task.get_extensions_list():
                        continue
                    
                    excluded = False
                    for exclude_dir in task.get_exclude_dirs_list():
                        if exclude_dir.lower() in str(file_path.parent).lower():
                            excluded = True
                            break
                    if excluded:
                        continue
                    
                    if task.use_cache:
                        cache_entry = HardlinkCache.query.filter_by(source_path=str(file_path)).first()
                        if cache_entry:
                            continue
                    
                    dest_path = Path(task.dest_dir)
                    if task.create_folder:
                        dest_path = dest_path / file_path.parent.name
                        dest_path.mkdir(parents=True, exist_ok=True)
                    
                    dest_file = dest_path / file_path.name
                    
                    if dest_file.exists():
                        dest_file.unlink()
                    
                    os.link(file_path, dest_file)
                    
                    if task.use_cache:
                        cache_entry = HardlinkCache(source_path=str(file_path), dest_path=str(dest_file))
                        db.session.add(cache_entry)
                    
                    count += 1
            
            db.session.commit()
            log_operation('batch_hardlink', 'HardlinkTask', task.id, task.name, 
                        f"Batch created {count} hard links")
            return True, f"成功创建 {count} 个硬链接"
        except Exception as e:
            log_operation('batch_hardlink_failed', 'HardlinkTask', task.id, task.name, 
                        f"Batch hardlink failed: {e}", success=False)
            return False, str(e)

def run_cron_job(job_id):
    with app.app_context():
        job = CronJob.query.get(job_id)
        if not job or not job.enabled:
            return
        
        try:
            if job.task_type == 'batch_hardlink':
                task = HardlinkTask.query.get(job.target_id)
                if task:
                    batch_create_hardlinks(task.id)
                    log_operation('cron_executed', 'CronJob', job.id, job.name, 
                                f"Executed batch hardlink for task: {task.name}")
            
            elif job.task_type == 'clean_logs':
                retention_days = int(get_config('log_retention_days', '30'))
                cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)
                OperationLog.query.filter(OperationLog.created_at < cutoff_date).delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, 
                            f"Cleaned logs older than {retention_days} days")
            
            elif job.task_type == 'clean_cache':
                HardlinkCache.query.delete()
                db.session.commit()
                log_operation('cron_executed', 'CronJob', job.id, job.name, 
                            "Cleared hardlink cache")
            
        except Exception as e:
            log_operation('cron_failed', 'CronJob', job.id, job.name, 
                        f"Cron job failed: {e}", success=False)

def start_cron_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
    
    for job in CronJob.query.filter_by(enabled=True).all():
        scheduler.add_job(
            run_cron_job,
            'cron',
            id=f'cron_{job.id}',
            args=[job.id],
            minute=job.cron_expression.split()[0],
            hour=job.cron_expression.split()[1],
            day=job.cron_expression.split()[2],
            month=job.cron_expression.split()[3],
            day_of_week=job.cron_expression.split()[4]
        )
    
    scheduler.start()

def update_cron_job(job_id):
    job = CronJob.query.get(job_id)
    if not job:
        return
    
    job_key = f'cron_{job_id}'
    
    if scheduler.get_job(job_key):
        scheduler.remove_job(job_key)
    
    if job.enabled:
        scheduler.add_job(
            run_cron_job,
            'cron',
            id=job_key,
            args=[job_id],
            minute=job.cron_expression.split()[0],
            hour=job.cron_expression.split()[1],
            day=job.cron_expression.split()[2],
            month=job.cron_expression.split()[3],
            day_of_week=job.cron_expression.split()[4]
        )

def validate_path(path):
    if not path:
        return False, "路径不能为空"
    if '..' in path or '~' in path or '\\' in path:
        return False, "非法路径字符"
    return True, ""

def validate_host(host):
    if not host:
        return False, "主机地址不能为空"
    import re
    if not re.match(r'^https?://[a-zA-Z0-9.-]+(:\d+)?/?$', host):
        return False, "无效的主机地址格式"
    return True, ""

@app.route('/')
def dashboard():
    hardlink_tasks = HardlinkTask.query.all()
    delete_tasks = DeleteMonitorTask.query.all()
    downloaders = Downloader.query.all()
    notifiers = Notifier.query.all()
    cron_jobs = CronJob.query.all()
    recent_logs = OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all()
    
    running_count = sum(1 for t in hardlink_tasks if t.enabled) + sum(1 for t in delete_tasks if t.enabled)
    
    return render_template('dashboard.html', 
                        hardlink_tasks=hardlink_tasks,
                        delete_tasks=delete_tasks,
                        downloaders=downloaders,
                        notifiers=notifiers,
                        cron_jobs=cron_jobs,
                        recent_logs=recent_logs,
                        running_count=running_count,
                        total_tasks=len(hardlink_tasks) + len(delete_tasks),
                        hardlink_count=len(hardlink_tasks),
                        delete_count=len(delete_tasks),
                        downloader_count=len(downloaders),
                        notifier_count=len(notifiers))

@app.route('/hardlink')
def hardlink_list():
    tasks = HardlinkTask.query.all()
    default_extensions = get_config('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv')
    return render_template('hardlink.html', tasks=tasks, default_extensions=default_extensions)

@app.route('/hardlink/add', methods=['POST'])
def hardlink_add():
    name = request.form.get('name')
    source_dir = request.form.get('source_dir')
    dest_dir = request.form.get('dest_dir')
    extensions = request.form.get('extensions', '.mkv,.mp4,.avi,.mov')
    exclude_dirs = request.form.get('exclude_dirs', 'sample,subs')
    create_folder = request.form.get('create_folder') == 'on'
    use_cache = request.form.get('use_cache') == 'on'
    
    if not name:
        flash('任务名称不能为空', 'danger')
        return redirect('/hardlink')
    
    valid, msg = validate_path(source_dir)
    if not valid:
        flash(f'源目录无效: {msg}', 'danger')
        return redirect('/hardlink')
    
    valid, msg = validate_path(dest_dir)
    if not valid:
        flash(f'目标目录无效: {msg}', 'danger')
        return redirect('/hardlink')
    
    new_task = HardlinkTask(
        name=name,
        source_dir=source_dir,
        dest_dir=dest_dir,
        extensions=extensions,
        exclude_dirs=exclude_dirs,
        create_folder=create_folder,
        use_cache=use_cache
    )
    
    db.session.add(new_task)
    db.session.commit()
    
    if new_task.enabled:
        start_hardlink_observer(new_task)
    
    log_operation('hardlink_task_created', 'HardlinkTask', new_task.id, new_task.name)
    flash('硬链接任务已添加', 'success')
    return redirect('/hardlink')

@app.route('/hardlink/toggle/<int:task_id>')
def hardlink_toggle(task_id):
    task = HardlinkTask.query.get(task_id)
    if not task:
        flash('任务不存在', 'danger')
        return redirect('/hardlink')
    
    task.enabled = not task.enabled
    db.session.commit()
    
    if task.enabled:
        start_hardlink_observer(task)
        log_operation('hardlink_task_started', 'HardlinkTask', task.id, task.name)
        flash(f'任务 {task.name} 已启动', 'success')
    else:
        stop_observer(f"hardlink_{task.id}")
        log_operation('hardlink_task_stopped', 'HardlinkTask', task.id, task.name)
        flash(f'任务 {task.name} 已停止', 'warning')
    
    return redirect('/hardlink')

@app.route('/hardlink/delete/<int:task_id>')
def hardlink_delete(task_id):
    task = HardlinkTask.query.get(task_id)
    if not task:
        flash('任务不存在', 'danger')
        return redirect('/hardlink')
    
    stop_observer(f"hardlink_{task.id}")
    HardlinkCache.query.filter(HardlinkCache.source_path.like(f"{task.source_dir}%")).delete()
    
    db.session.delete(task)
    db.session.commit()
    
    log_operation('hardlink_task_deleted', 'HardlinkTask', task_id, task.name)
    flash(f'任务 {task.name} 已删除', 'success')
    return redirect('/hardlink')

@app.route('/hardlink/batch/<int:task_id>')
def hardlink_batch(task_id):
    success, message = batch_create_hardlinks(task_id)
    if success:
        flash(message, 'success')
    else:
        flash(f'批量创建失败: {message}', 'danger')
    return redirect('/hardlink')

@app.route('/hardlink/execute/<int:task_id>')
def hardlink_execute(task_id):
    success, message = batch_create_hardlinks(task_id)
    if success:
        flash(message, 'success')
    else:
        flash(f'执行失败: {message}', 'danger')
    return redirect('/hardlink')

@app.route('/hardlink/cache/clear/<int:task_id>')
def hardlink_clear_cache(task_id):
    task = HardlinkTask.query.get(task_id)
    if not task:
        flash('任务不存在', 'danger')
        return redirect('/hardlink')
    
    HardlinkCache.query.filter(HardlinkCache.source_path.like(f"{task.source_dir}%")).delete()
    db.session.commit()
    log_operation('hardlink_cache_cleared', 'HardlinkTask', task.id, task.name)
    flash(f'任务 {task.name} 的缓存已清除', 'success')
    return redirect('/hardlink')

@app.route('/delete-monitor')
def delete_monitor_list():
    tasks = DeleteMonitorTask.query.all()
    downloaders = Downloader.query.all()
    notifiers = Notifier.query.all()
    return render_template('delete_monitor.html', tasks=tasks, downloaders=downloaders, notifiers=notifiers)

@app.route('/delete-monitor/add', methods=['POST'])
def delete_monitor_add():
    name = request.form.get('name')
    directory = request.form.get('directory')
    downloader_id = request.form.get('downloader_id')
    notifier_id = request.form.get('notifier_id')
    events = ','.join(request.form.getlist('events'))
    
    new_task = DeleteMonitorTask(
        name=name,
        directory=directory,
        downloader_id=downloader_id if downloader_id else None,
        notifier_id=notifier_id if notifier_id else None,
        events=events or 'unlink,unlinkDir'
    )
    
    db.session.add(new_task)
    db.session.commit()
    
    if new_task.enabled:
        start_delete_observer(new_task)
    
    log_operation('delete_task_created', 'DeleteMonitorTask', new_task.id, new_task.name)
    flash('删除监控任务已添加', 'success')
    return redirect('/delete-monitor')

@app.route('/delete-monitor/toggle/<int:task_id>')
def delete_monitor_toggle(task_id):
    task = DeleteMonitorTask.query.get(task_id)
    if not task:
        flash('任务不存在', 'danger')
        return redirect('/delete-monitor')
    
    task.enabled = not task.enabled
    db.session.commit()
    
    if task.enabled:
        start_delete_observer(task)
        log_operation('delete_task_started', 'DeleteMonitorTask', task.id, task.name)
        flash(f'任务 {task.name} 已启动', 'success')
    else:
        stop_observer(f"delete_{task.id}")
        log_operation('delete_task_stopped', 'DeleteMonitorTask', task.id, task.name)
        flash(f'任务 {task.name} 已停止', 'warning')
    
    return redirect('/delete-monitor')

@app.route('/delete-monitor/delete/<int:task_id>')
def delete_monitor_delete(task_id):
    task = DeleteMonitorTask.query.get(task_id)
    if not task:
        flash('任务不存在', 'danger')
        return redirect('/delete-monitor')
    
    stop_observer(f"delete_{task.id}")
    
    db.session.delete(task)
    db.session.commit()
    
    log_operation('delete_task_deleted', 'DeleteMonitorTask', task_id, task.name)
    flash(f'任务 {task.name} 已删除', 'success')
    return redirect('/delete-monitor')

@app.route('/downloader')
def downloader_list():
    downloaders = Downloader.query.all()
    return render_template('downloader.html', downloaders=downloaders)

@app.route('/downloader/add', methods=['POST'])
def downloader_add():
    name = request.form.get('name')
    downloader_type = request.form.get('type', 'qbittorrent')
    host = request.form.get('host')
    port = request.form.get('port', type=int)
    username = request.form.get('username')
    password = request.form.get('password')
    
    if not name:
        flash('下载器名称不能为空', 'danger')
        return redirect('/downloader')
    
    if not host:
        flash('主机地址不能为空', 'danger')
        return redirect('/downloader')
    
    valid, msg = validate_host(host)
    if not valid:
        flash(f'主机地址无效: {msg}', 'danger')
        return redirect('/downloader')
    
    if port is None or port < 1 or port > 65535:
        flash('端口号必须在 1-65535 之间', 'danger')
        return redirect('/downloader')
    
    new_downloader = Downloader(
        name=name,
        type=downloader_type,
        host=host,
        port=port,
        username=username
    )
    new_downloader.set_password(password)
    
    db.session.add(new_downloader)
    db.session.commit()
    
    log_operation('downloader_created', 'Downloader', new_downloader.id, new_downloader.name)
    flash('下载器已添加', 'success')
    return redirect('/downloader')

@app.route('/downloader/toggle/<int:downloader_id>')
def downloader_toggle(downloader_id):
    downloader = Downloader.query.get(downloader_id)
    if not downloader:
        flash('下载器不存在', 'danger')
        return redirect('/downloader')
    
    downloader.enabled = not downloader.enabled
    db.session.commit()
    
    log_operation('downloader_toggled', 'Downloader', downloader.id, downloader.name,
                f"状态: {'已启用' if downloader.enabled else '已禁用'}")
    flash(f'下载器 {downloader.name} 已{"启用" if downloader.enabled else "禁用"}', 'success')
    return redirect('/downloader')

@app.route('/downloader/delete/<int:downloader_id>')
def downloader_delete(downloader_id):
    downloader = Downloader.query.get(downloader_id)
    if not downloader:
        flash('下载器不存在', 'danger')
        return redirect('/downloader')
    
    db.session.delete(downloader)
    db.session.commit()
    
    log_operation('downloader_deleted', 'Downloader', downloader_id, downloader.name)
    flash(f'下载器 {downloader.name} 已删除', 'success')
    return redirect('/downloader')

@app.route('/downloader/test/<int:downloader_id>')
def downloader_test(downloader_id):
    downloader = Downloader.query.get(downloader_id)
    if not downloader:
        flash('下载器不存在', 'danger')
        return redirect('/downloader')
    
    torrents = get_qbittorrent_torrents(downloader)
    if torrents is not None:
        flash(f'连接成功！当前种子数: {len(torrents)}', 'success')
        log_operation('downloader_test', 'Downloader', downloader.id, downloader.name, '连接测试成功')
    else:
        flash('连接失败，请检查配置', 'danger')
        log_operation('downloader_test', 'Downloader', downloader.id, downloader.name, '连接测试失败', success=False)
    
    return redirect('/downloader')

@app.route('/notifier')
def notifier_list():
    notifiers = Notifier.query.all()
    return render_template('notifier.html', notifiers=notifiers)

@app.route('/notifier/add', methods=['POST'])
def notifier_add():
    name = request.form.get('name')
    notifier_type = request.form.get('type', 'telegram')
    api_key = request.form.get('api_key')
    chat_id = request.form.get('chat_id')
    
    new_notifier = Notifier(
        name=name,
        type=notifier_type,
        api_key=api_key,
        chat_id=chat_id
    )
    
    db.session.add(new_notifier)
    db.session.commit()
    
    log_operation('notifier_created', 'Notifier', new_notifier.id, new_notifier.name)
    flash('通知器已添加', 'success')
    return redirect('/notifier')

@app.route('/notifier/toggle/<int:notifier_id>')
def notifier_toggle(notifier_id):
    notifier = Notifier.query.get(notifier_id)
    if not notifier:
        flash('通知器不存在', 'danger')
        return redirect('/notifier')
    
    notifier.enabled = not notifier.enabled
    db.session.commit()
    
    log_operation('notifier_toggled', 'Notifier', notifier.id, notifier.name,
                f"状态: {'已启用' if notifier.enabled else '已禁用'}")
    flash(f'通知器 {notifier.name} 已{"启用" if notifier.enabled else "禁用"}', 'success')
    return redirect('/notifier')

@app.route('/notifier/delete/<int:notifier_id>')
def notifier_delete(notifier_id):
    notifier = Notifier.query.get(notifier_id)
    if not notifier:
        flash('通知器不存在', 'danger')
        return redirect('/notifier')
    
    db.session.delete(notifier)
    db.session.commit()
    
    log_operation('notifier_deleted', 'Notifier', notifier_id, notifier.name)
    flash(f'通知器 {notifier.name} 已删除', 'success')
    return redirect('/notifier')

@app.route('/notifier/test/<int:notifier_id>')
def notifier_test(notifier_id):
    notifier = Notifier.query.get(notifier_id)
    if not notifier:
        flash('通知器不存在', 'danger')
        return redirect('/notifier')
    
    success = send_telegram_notification(notifier, 'Hardlink Manager 测试通知')
    if success:
        flash('通知发送成功', 'success')
        log_operation('notifier_test', 'Notifier', notifier.id, notifier.name, '通知测试成功')
    else:
        flash('通知发送失败，请检查配置', 'danger')
        log_operation('notifier_test', 'Notifier', notifier.id, notifier.name, '通知测试失败', success=False)
    
    return redirect('/notifier')

@app.route('/logs')
def logs_list():
    logs = OperationLog.query.order_by(OperationLog.created_at.desc()).all()
    return render_template('logs.html', logs=logs)

@app.route('/logs/clear')
def logs_clear():
    OperationLog.query.delete()
    db.session.commit()
    log_operation('logs_cleared', 'System', message='操作日志已清空')
    flash('操作日志已清空', 'success')
    return redirect('/logs')

@app.route('/settings')
def settings_page():
    settings = {}
    configs = AppConfig.query.all()
    for config in configs:
        settings[config.key] = config.value
    return render_template('settings.html', settings=settings)

@app.route('/settings/save', methods=['POST'])
def settings_save():
    settings_to_save = [
        'log_retention_days',
        'auto_clean_logs',
        'default_extensions',
        'default_exclude_dirs',
        'delete_files_with_torrent',
        'delete_delay_seconds',
        'notify_on_hardlink',
        'notify_on_delete'
    ]
    
    for key in settings_to_save:
        value = request.form.get(key)
        if value is not None:
            set_config(key, value)
    
    log_operation('settings_saved', 'System', message='应用设置已更新')
    flash('设置已保存', 'success')
    return redirect('/settings')

@app.route('/cron')
def cron_list():
    jobs = CronJob.query.all()
    hardlink_tasks = HardlinkTask.query.all()
    return render_template('cron.html', jobs=jobs, hardlink_tasks=hardlink_tasks)

@app.route('/cron/add', methods=['POST'])
def cron_add():
    name = request.form.get('name')
    task_type = request.form.get('task_type')
    target_id = request.form.get('target_id')
    cron_expression = request.form.get('cron_expression')
    custom_cron = request.form.get('custom_cron')
    description = request.form.get('description')
    
    if custom_cron:
        cron_expression = custom_cron
    
    new_job = CronJob(
        name=name,
        task_type=task_type,
        target_id=int(target_id) if target_id else None,
        cron_expression=cron_expression,
        description=description
    )
    
    db.session.add(new_job)
    db.session.commit()
    
    if new_job.enabled:
        update_cron_job(new_job.id)
    
    log_operation('cron_created', 'CronJob', new_job.id, new_job.name)
    flash('定时任务已添加', 'success')
    return redirect('/cron')

@app.route('/cron/toggle/<int:job_id>')
def cron_toggle(job_id):
    job = CronJob.query.get(job_id)
    if not job:
        flash('定时任务不存在', 'danger')
        return redirect('/cron')
    
    job.enabled = not job.enabled
    db.session.commit()
    update_cron_job(job.id)
    
    log_operation('cron_toggled', 'CronJob', job.id, job.name,
                f"状态: {'已启用' if job.enabled else '已禁用'}")
    flash(f'定时任务 {job.name} 已{"启用" if job.enabled else "禁用"}', 'success')
    return redirect('/cron')

@app.route('/cron/delete/<int:job_id>')
def cron_delete(job_id):
    job = CronJob.query.get(job_id)
    if not job:
        flash('定时任务不存在', 'danger')
        return redirect('/cron')
    
    job_key = f'cron_{job_id}'
    if scheduler.get_job(job_key):
        scheduler.remove_job(job_key)
    
    db.session.delete(job)
    db.session.commit()
    
    log_operation('cron_deleted', 'CronJob', job_id, job.name)
    flash(f'定时任务 {job.name} 已删除', 'success')
    return redirect('/cron')

@app.route('/api/health')
def api_health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now(UTC).isoformat()})

@app.route('/api/tasks/status')
def api_tasks_status():
    hardlink_tasks = HardlinkTask.query.all()
    delete_tasks = DeleteMonitorTask.query.all()
    
    result = {
        'hardlink_tasks': [{
            'id': t.id,
            'name': t.name,
            'enabled': t.enabled
        } for t in hardlink_tasks],
        'delete_tasks': [{
            'id': t.id,
            'name': t.name,
            'enabled': t.enabled
        } for t in delete_tasks]
    }
    
    return jsonify(result)

def init_app():
    with app.app_context():
        db.create_all()
        
        default_configs = [
            ('log_retention_days', '30', '日志保留天数'),
            ('auto_clean_logs', 'true', '自动清理日志'),
            ('default_extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv', '默认文件扩展名'),
            ('default_exclude_dirs', 'sample,subs', '默认排除目录'),
            ('delete_files_with_torrent', 'false', '删除种子时同时删除文件'),
            ('delete_delay_seconds', '0', '删除确认延迟(秒)'),
            ('notify_on_hardlink', 'false', '启用硬链接创建通知'),
            ('notify_on_delete', 'true', '启用删除通知')
        ]
        
        for key, value, description in default_configs:
            if not AppConfig.query.filter_by(key=key).first():
                set_config(key, value, description)
        
        for task in HardlinkTask.query.filter_by(enabled=True).all():
            start_hardlink_observer(task)
        
        for task in DeleteMonitorTask.query.filter_by(enabled=True).all():
            start_delete_observer(task)
        
        start_cron_scheduler()

if __name__ == '__main__':
    init_app()
    app.run(host='0.0.0.0', port=5000, debug=False)