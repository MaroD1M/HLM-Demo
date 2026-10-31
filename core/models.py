from datetime import datetime, UTC
import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from core.extensions import db

ENCRYPTED_PREFIX = 'fernet:'
LEGACY_ENCRYPTED_PREFIX = 'enc:'
_KEY_CANDIDATE_CACHE = None


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
    # Built-in delete-linkage options (replacing standalone delete-monitor task for normal usage)
    monitor_source_delete = db.Column(db.Boolean, default=True)
    monitor_dest_delete = db.Column(db.Boolean, default=True)
    delete_downloader_id = db.Column(db.Integer, db.ForeignKey('downloader.id'))
    delete_notifier_id = db.Column(db.Integer, db.ForeignKey('notifier.id'))
    delete_cooldown_seconds = db.Column(db.Integer, default=120)
    delete_max_deletes_per_run = db.Column(db.Integer, default=20)
    delete_dry_run = db.Column(db.Boolean, default=False)
    delete_notify_on_delete = db.Column(db.Boolean, default=True)
    delete_notify_on_risky_delete = db.Column(db.Boolean, default=True)
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

    delete_downloader = db.relationship('Downloader', foreign_keys=[delete_downloader_id], lazy='joined')
    delete_notifier = db.relationship('Notifier', foreign_keys=[delete_notifier_id], lazy='joined')


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
    notify_on_delete = db.Column(db.Boolean, default=True)
    notify_on_risky_delete = db.Column(db.Boolean, default=True)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    downloader = db.relationship('Downloader', foreign_keys=[downloader_id], lazy='joined')
    notifier = db.relationship('Notifier', foreign_keys=[notifier_id], lazy='joined')


class Downloader(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), default='qbittorrent')
    host = db.Column(db.String(200), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(100))
    encrypted_password = db.Column(db.String(500))
    proxy_url = db.Column(db.String(300))
    category = db.Column(db.String(100), default='')
    session_ttl_seconds = db.Column(db.Integer)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    @staticmethod
    def _key_candidates():
        global _KEY_CANDIDATE_CACHE
        if _KEY_CANDIDATE_CACHE is not None:
            return _KEY_CANDIDATE_CACHE
        legacy = 'default-secret-key-for-dev-only'
        seeds = []
        file_secret = ''
        try:
            secret_file = Path(os.environ.get('HLM_INSTANCE_PATH') or 'instance') / 'secret_key'
            if secret_file.exists():
                file_secret = secret_file.read_text(encoding='utf-8').strip()
        except Exception:
            file_secret = ''
        for value in (os.environ.get('APP_ENCRYPTION_KEY'), os.environ.get('SECRET_KEY'), file_secret, legacy):
            text = (value or '').strip()
            if text and text not in seeds:
                seeds.append(text)
        _KEY_CANDIDATE_CACHE = [hashlib.sha256(seed.encode('utf-8')).digest() for seed in seeds]
        return _KEY_CANDIDATE_CACHE

    @classmethod
    def _key_bytes(cls):
        return cls._key_candidates()[0]

    @staticmethod
    def _fernet_for(key_bytes):
        return Fernet(base64.urlsafe_b64encode(key_bytes))

    @classmethod
    def _legacy_decrypt_text(cls, token, key_bytes=None):
        payload = token[len(LEGACY_ENCRYPTED_PREFIX):]
        data = base64.urlsafe_b64decode(payload.encode('utf-8'))
        key = key_bytes or cls._key_bytes()
        raw = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
        return raw.decode('utf-8')

    @classmethod
    def _encrypt_text(cls, raw):
        token = cls._fernet_for(cls._key_bytes()).encrypt(raw.encode('utf-8')).decode('utf-8')
        return ENCRYPTED_PREFIX + token

    @classmethod
    def _decrypt_text(cls, token):
        if token.startswith(ENCRYPTED_PREFIX):
            payload = token[len(ENCRYPTED_PREFIX):].encode('utf-8')
            for key_bytes in cls._key_candidates():
                try:
                    return cls._fernet_for(key_bytes).decrypt(payload).decode('utf-8')
                except (InvalidToken, ValueError):
                    continue
            raise ValueError('stored credential cannot be decrypted with the current key')
        if token.startswith(LEGACY_ENCRYPTED_PREFIX):
            last_error = None
            for key_bytes in cls._key_candidates():
                try:
                    return cls._legacy_decrypt_text(token, key_bytes)
                except Exception as exc:
                    last_error = exc
            raise ValueError('legacy credential cannot be decrypted') from last_error
        return token

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
            if value.startswith((ENCRYPTED_PREFIX, LEGACY_ENCRYPTED_PREFIX)):
                return ''
            return value


class Notifier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), default='telegram')
    api_key = db.Column(db.String(500), nullable=False)
    chat_id = db.Column(db.String(100))
    proxy_url = db.Column(db.String(300))
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def set_api_key(self, api_key):
        raw = (api_key or '').strip()
        if not raw:
            self.api_key = ''
            return
        if raw.startswith((ENCRYPTED_PREFIX, LEGACY_ENCRYPTED_PREFIX)):
            self.api_key = raw
            return
        self.api_key = Downloader._encrypt_text(raw)

    def get_api_key(self):
        value = self.api_key or ''
        if not value:
            return ''
        try:
            return Downloader._decrypt_text(value)
        except Exception:
            return ''


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
    source_type = db.Column(db.String(20), default='manual', index=True)
    last_seen_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    deleted_at = db.Column(db.DateTime)
    backfill_fail_count = db.Column(db.Integer, default=0, index=True)
    backfill_last_attempt_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class OperationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    operation_type = db.Column(db.String(50), nullable=False)
    target_type = db.Column(db.String(50))
    target_id = db.Column(db.Integer)
    execution_id = db.Column(db.Integer, index=True)
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



class JobQueue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_key = db.Column(db.String(200), index=True, nullable=False)
    job_name = db.Column(db.String(150), nullable=False)
    job_type = db.Column(db.String(80), nullable=False)
    target_id = db.Column(db.Integer)
    source = db.Column(db.String(30), default='manual')
    payload_json = db.Column(db.Text)
    status = db.Column(db.String(20), default='queued', index=True)
    priority = db.Column(db.Integer, default=0)
    retry_count = db.Column(db.Integer, default=0)
    max_retries = db.Column(db.Integer, default=0)
    stop_requested = db.Column(db.Boolean, default=False)
    worker_id = db.Column(db.String(100))
    lease_owner = db.Column(db.String(100))
    lease_expires_at = db.Column(db.DateTime)
    message = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class RecycleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_path = db.Column(db.String(1000), nullable=False)
    recycled_path = db.Column(db.String(1000), nullable=False, unique=True)
    size = db.Column(db.BigInteger, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    restored_at = db.Column(db.DateTime)


class AdminUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    totp_secret = db.Column(db.String(500))
    totp_enabled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def set_totp_secret(self, secret):
        raw = (secret or '').strip()
        if not raw:
            self.totp_secret = None
            return
        if raw.startswith((ENCRYPTED_PREFIX, LEGACY_ENCRYPTED_PREFIX)):
            self.totp_secret = raw
            return
        self.totp_secret = Downloader._encrypt_text(raw)

    def get_totp_secret(self):
        value = self.totp_secret or ''
        if not value:
            return ''
        try:
            return Downloader._decrypt_text(value)
        except Exception:
            return ''


class AppConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(1000))
    description = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class AppConfigSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    created_by = db.Column(db.String(100))
    note = db.Column(db.String(500))


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
