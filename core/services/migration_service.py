from datetime import datetime, UTC
from pathlib import Path


class MigrationService:
    def __init__(self, db, logger, instance_path_getter, app_version_getter, get_config, run_sqlite_backup):
        self.db = db
        self.logger = logger
        self.instance_path_getter = instance_path_getter
        self.app_version_getter = app_version_getter
        self.get_config = get_config
        self.run_sqlite_backup = run_sqlite_backup

    def _ensure_schema_meta_table(self):
        self.db.session.execute(self.db.text("CREATE TABLE IF NOT EXISTS schema_meta (key VARCHAR(100) PRIMARY KEY, value VARCHAR(200))"))

    def get_schema_meta_value(self, key, default=''):
        self._ensure_schema_meta_table()
        row = self.db.session.execute(self.db.text("SELECT value FROM schema_meta WHERE key=:k"), {'k': key}).fetchone()
        return str(row[0]) if row and row[0] is not None else str(default)

    def set_schema_meta_value(self, key, value):
        self._ensure_schema_meta_table()
        self.db.session.execute(self.db.text("DELETE FROM schema_meta WHERE key=:k"), {'k': key})
        self.db.session.execute(self.db.text("INSERT INTO schema_meta(key, value) VALUES (:k, :v)"), {'k': key, 'v': str(value)})

    def delete_schema_meta_key(self, key):
        self._ensure_schema_meta_table()
        self.db.session.execute(self.db.text("DELETE FROM schema_meta WHERE key=:k"), {'k': key})

    def _get_schema_version(self):
        try:
            return int(self.get_schema_meta_value('db_schema_version', '0') or '0')
        except Exception:
            return 0

    def _set_schema_version(self, version):
        self.set_schema_meta_value('db_schema_version', str(version))

    def _migration_v1(self):
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
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))

    def _migration_v2(self):
        self.db.session.execute(self.db.text("CREATE TABLE IF NOT EXISTS delete_pending_action (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, file_map_id INTEGER NOT NULL, deleted_path VARCHAR(1000) NOT NULL, torrent_hash VARCHAR(64), match_by VARCHAR(50) DEFAULT 'no_match', status VARCHAR(20) DEFAULT 'pending', reason VARCHAR(500), created_at DATETIME, confirmed_at DATETIME)"))

    def _migration_v3(self):
        needed = {
            'downloader': {'proxy_url': 'ALTER TABLE downloader ADD COLUMN proxy_url VARCHAR(300)'},
            'notifier': {'proxy_url': 'ALTER TABLE notifier ADD COLUMN proxy_url VARCHAR(300)'},
            'file_link_map': {'source_type': "ALTER TABLE file_link_map ADD COLUMN source_type VARCHAR(20) DEFAULT 'manual'"},
        }
        for table, fields in needed.items():
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))
        self.db.session.execute(self.db.text("UPDATE file_link_map SET source_type = CASE WHEN torrent_hash IS NOT NULL AND TRIM(torrent_hash) <> '' THEN 'downloader' ELSE 'manual' END WHERE source_type IS NULL OR TRIM(source_type) = ''"))

    def _migration_v4(self):
        needed = {
            'delete_monitor_task': {
                'notify_on_delete': 'ALTER TABLE delete_monitor_task ADD COLUMN notify_on_delete BOOLEAN DEFAULT 1',
                'notify_on_risky_delete': 'ALTER TABLE delete_monitor_task ADD COLUMN notify_on_risky_delete BOOLEAN DEFAULT 1',
            },
        }
        for table, fields in needed.items():
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))

    def _migration_v5(self):
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_created_at ON operation_log(created_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_type ON operation_log(operation_type)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_success_created ON operation_log(success, created_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_created_at ON file_link_map(created_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_hardlink_cache_created_at ON hardlink_cache(created_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_delete_pending_status_created ON delete_pending_action(status, created_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_job_execution_started_at ON job_execution_log(started_at)'))

    def _migration_v6(self):
        needed = {
            'file_link_map': {
                'backfill_fail_count': 'ALTER TABLE file_link_map ADD COLUMN backfill_fail_count INTEGER DEFAULT 0',
                'backfill_last_attempt_at': 'ALTER TABLE file_link_map ADD COLUMN backfill_last_attempt_at DATETIME',
            },
        }
        for table, fields in needed.items():
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))
        self.db.session.execute(self.db.text("CREATE INDEX IF NOT EXISTS idx_file_link_map_backfill_fail_count ON file_link_map(backfill_fail_count)"))

    def _migration_v7(self):
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
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))

    def _migration_v8(self):
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_backfill_lookup ON file_link_map(torrent_hash, deleted_at, backfill_fail_count, downloader_id)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_source_state ON file_link_map(source_type, deleted_at, last_seen_at)'))

    def _migration_v9(self):
        needed = {'downloader': {'session_ttl_seconds': 'ALTER TABLE downloader ADD COLUMN session_ttl_seconds INTEGER'}}
        for table, fields in needed.items():
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))

    def _migration_v10(self):
        needed = {'operation_log': {'execution_id': 'ALTER TABLE operation_log ADD COLUMN execution_id INTEGER'}}
        for table, fields in needed.items():
            existing = {row[1] for row in self.db.session.execute(self.db.text(f'PRAGMA table_info({table})')).fetchall()}
            for col, sql in fields.items():
                if col not in existing:
                    self.db.session.execute(self.db.text(sql))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_operation_log_execution_id ON operation_log(execution_id)'))

    def _migration_v11(self):
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_task_deleted ON file_link_map(task_id, deleted_at)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_deleted_source ON file_link_map(deleted_at, source_path)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_deleted_dest ON file_link_map(deleted_at, dest_path)'))
        self.db.session.execute(self.db.text('CREATE INDEX IF NOT EXISTS idx_file_link_map_backfill_scan ON file_link_map(torrent_hash, deleted_at, downloader_id, created_at)'))

    def ensure_compat_columns(self):
        target = 11
        current = self._get_schema_version()
        if current > target:
            self.logger.warning('db schema version %s is newer than app target %s; running in compatibility mode', current, target)
            return
        migrations = {
            1: self._migration_v1, 2: self._migration_v2, 3: self._migration_v3, 4: self._migration_v4, 5: self._migration_v5,
            6: self._migration_v6, 7: self._migration_v7, 8: self._migration_v8, 9: self._migration_v9, 10: self._migration_v10, 11: self._migration_v11,
        }
        for version in range(current + 1, target + 1):
            migrations[version]()
            self._set_schema_version(version)
            self.logger.info('db migration applied: v%s', version)
        self.db.session.commit()

    def pre_migration_backup_if_needed(self, target_schema):
        current_version = self.app_version_getter()
        last_version = self.get_schema_meta_value('last_app_version', '')
        current_schema = self._get_schema_version()
        if current_schema >= target_schema:
            return True, '当前数据库结构已是目标版本，无需升级前备份'
        if last_version == current_version:
            return True, '当前版本已执行过启动流程，无需重复升级前备份'
        backup_done_for = self.get_schema_meta_value('pre_migration_backup_for_version', '')
        backup_path_done = self.get_schema_meta_value('pre_migration_backup_path', '')
        if backup_done_for == current_version and backup_path_done:
            return True, f'已存在本版本升级前备份: {backup_path_done}'

        db_file = Path(self.instance_path_getter()) / 'hardlink_manager.db'
        if not db_file.exists():
            return True, '数据库文件尚不存在，跳过升级前备份'

        backup_base = (self.get_config('backup_dir', '/app/data/backups') or '/app/data/backups').strip()
        backup_dir = str(Path(backup_base) / 'migration-pre')
        keep_last = int(self.get_config('backup_keep_last', '7') or '7')
        ok, msg, backup_path = self.run_sqlite_backup(str(db_file), backup_dir, keep_last=max(1, keep_last))
        if not ok:
            self.set_schema_meta_value('last_migration_status', 'backup_failed')
            self.set_schema_meta_value('last_migration_error', msg)
            self.db.session.commit()
            return False, msg

        stamped = datetime.now(UTC).isoformat()
        self.set_schema_meta_value('pre_migration_backup_for_version', current_version)
        self.set_schema_meta_value('pre_migration_backup_path', backup_path or '-')
        self.set_schema_meta_value('pre_migration_backup_at', stamped)
        self.set_schema_meta_value('last_migration_status', 'backup_ok')
        self.delete_schema_meta_key('last_migration_error')
        self.db.session.commit()
        self.logger.info('pre migration backup created for %s: %s', current_version, backup_path)
        return True, f'升级前备份完成: {backup_path}'
