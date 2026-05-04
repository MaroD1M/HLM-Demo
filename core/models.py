from datetime import datetime, UTC
import base64
import hashlib
import os
from core.extensions import db


class HardlinkTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    source_dir = db.Column(db.String(500), nullable=False)
    dest_dir = db.Column(db.String(500), nullable=False)
    extensions = db.Column(db.String(500), default='.mkv,.mp4,.avi,.mov,.wmv,.flv')
    exclude_extensions = db.Column(db.String(500), default='')
    exclude_dirs = db.Column(db.String(500), default='sample,subs')
    create_folder = db.Column(db.Boolean, default=True)
    use_cache = db.Column(db.Boolean, default=True)
    min_file_age_seconds = db.Column(db.Integer, default=300)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def get_extensions_list(self):
        raw = self.extensions or '.mkv,.mp4,.avi,.mov,.wmv,.flv'
        return [e.strip().lower() for e in raw.split(',') if e.strip()]

    def get_exclude_extensions_list(self):
        return [e.strip().lower() for e in (self.exclude_extensions or '').split(',') if e.strip()]

    def get_exclude_dirs_list(self):
        return [d.strip().lower() for d in (self.exclude_dirs or '').split(',') if d.strip()]


class DeleteMonitorTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    directory = db.Column(db.String(500), nullable=False)
    downloader_id = db.Column(db.Integer, db.ForeignKey('downloader.id'))
    notifier_id = db.Column(db.Integer, db.ForeignKey('notifier.id'))
    events = db.Column(db.String(100), default='unlink')
    cooldown_seconds = db.Column(db.Integer, default=120)
    max_deletes_per_run = db.Column(db.Integer, default=20)
    dry_run = db.Column(db.Boolean, default=False)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


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

    @staticmethod
    def _key_bytes():
        seed = (os.environ.get('APP_ENCRYPTION_KEY') or os.environ.get('SECRET_KEY') or 'default-secret-key-for-dev-only').encode('utf-8')
        return hashlib.sha256(seed).digest()

    @classmethod
    def _encrypt_text(cls, raw):
        data = raw.encode('utf-8')
        key = cls._key_bytes()
        xored = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
        return 'enc:' + base64.urlsafe_b64encode(xored).decode('utf-8')

    @classmethod
    def _decrypt_text(cls, token):
        if not token.startswith('enc:'):
            return token
        payload = token[4:]
        data = base64.urlsafe_b64decode(payload.encode('utf-8'))
        key = cls._key_bytes()
        raw = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
        return raw.decode('utf-8')

    def set_password(self, password):
        raw = (password or '').strip()
        if not raw:
            self.encrypted_password = None
            return
        self.encrypted_password = self._encrypt_text(raw)

    def get_password(self):
        value = self.encrypted_password or ''
        if not value:
            return ''
        try:
            return self._decrypt_text(value)
        except Exception:
            # Backward compatibility for legacy plain-text values.
            return value


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


class FileLinkMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('hardlink_task.id'), nullable=False)
    source_path = db.Column(db.String(1000), nullable=False, unique=True)
    dest_path = db.Column(db.String(1000), nullable=False)
    source_inode = db.Column(db.String(64))
    file_size = db.Column(db.BigInteger)
    mtime = db.Column(db.DateTime)
    file_key = db.Column(db.String(128), index=True)
    downloader_id = db.Column(db.Integer, db.ForeignKey('downloader.id'))
    torrent_hash = db.Column(db.String(64), index=True)
    last_seen_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    deleted_at = db.Column(db.DateTime)
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




class JobExecutionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(150), nullable=False)
    job_type = db.Column(db.String(80), nullable=False)
    source = db.Column(db.String(30), default='manual')
    target_id = db.Column(db.Integer)
    status = db.Column(db.String(20), default='running')
    message = db.Column(db.String(1000))
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    finished_at = db.Column(db.DateTime)
    duration_ms = db.Column(db.Integer)

class DeletePendingAction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('delete_monitor_task.id'), nullable=False, index=True)
    file_map_id = db.Column(db.Integer, db.ForeignKey('file_link_map.id'), nullable=False, index=True)
    deleted_path = db.Column(db.String(1000), nullable=False)
    torrent_hash = db.Column(db.String(64))
    match_by = db.Column(db.String(50), default='no_match')
    status = db.Column(db.String(20), default='pending', index=True)
    reason = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    confirmed_at = db.Column(db.DateTime)



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
