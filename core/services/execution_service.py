"""Cross-process-aware execution guard and execution logging."""

import hashlib
from contextvars import ContextVar
from pathlib import Path
from threading import Lock

try:
    import fcntl
except ImportError:
    fcntl = None


class ExecutionManager:
    def __init__(self, db, job_execution_model, utc_now, lock_dir_getter=None):
        self.db = db
        self.job_execution_model = job_execution_model
        self.utc_now = utc_now
        self.run_lock = Lock()
        self.running_meta = {}
        self.stop_requested_keys = set()
        self.running_keys = set()
        self.current_execution_id = ContextVar('current_execution_id', default=None)
        self.lock_dir_getter = lock_dir_getter
        self.file_locks = {}

    def current_execution(self):
        return self.current_execution_id.get()

    def is_stop_requested(self, run_key):
        with self.run_lock:
            return run_key in self.stop_requested_keys

    def request_stop_by_execution(self, execution_id):
        with self.run_lock:
            for key, meta in self.running_meta.items():
                if meta.get('execution_id') == execution_id:
                    self.stop_requested_keys.add(key)
                    return True, meta
        return False, None

    def request_stop_by_run_key(self, run_key):
        with self.run_lock:
            if run_key in self.running_keys:
                self.stop_requested_keys.add(run_key)
                return True
        return False

    def get_running_executions_snapshot(self):
        with self.run_lock:
            return [dict(v) for _, v in self.running_meta.items()]

    def is_run_key_active(self, run_key):
        with self.run_lock:
            return run_key in self.running_keys

    def get_run_meta(self, run_key):
        with self.run_lock:
            meta = self.running_meta.get(run_key)
            return dict(meta) if meta else None

    def _start_execution(self, job_name, job_type, source='manual', target_id=None):
        started_at = self.utc_now()
        record = self.job_execution_model(
            job_name=job_name,
            job_type=job_type,
            source=source,
            target_id=target_id,
            status='running',
            started_at=started_at,
        )
        self.db.session.add(record)
        self.db.session.commit()
        return record, started_at

    def _finish_execution(self, record, started_at, ok, message):
        finished_at = self.utc_now()
        record.status = 'success' if ok else 'failed'
        record.message = message
        record.finished_at = finished_at
        record.duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        self.db.session.commit()

    def execute_with_guard(self, run_key, job_name, job_type, runner, log_operation, source='manual', target_id=None):
        """Run a job under in-memory and cross-process run-key locks."""
        with self.run_lock:
            if run_key in self.running_keys:
                meta = self.running_meta.get(run_key, {})
                started = meta.get('started_at')
                if started:
                    elapsed = int((self.utc_now() - started).total_seconds())
                    return False, f'任务正在执行中（已运行 {elapsed} 秒），请稍后重试'
                return False, '任务正在执行中，请稍后重试'
            self.running_keys.add(run_key)
            self.stop_requested_keys.discard(run_key)

        file_lock = None
        if fcntl is not None and self.lock_dir_getter is not None:
            try:
                lock_dir = Path(self.lock_dir_getter())
                lock_dir.mkdir(parents=True, exist_ok=True)
                lock_name = hashlib.sha256(run_key.encode('utf-8')).hexdigest()
                lock_path = lock_dir / f'{lock_name}.lock'
                file_lock = lock_path.open('w', encoding='utf-8')
                fcntl.flock(file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file_locks[run_key] = file_lock
            except BlockingIOError:
                if file_lock:
                    file_lock.close()
                with self.run_lock:
                    self.running_keys.discard(run_key)
                return False, '同任务正在其他进程中执行，请稍后重试'
            except Exception:
                if file_lock:
                    file_lock.close()
                file_lock = None

        record = None
        started_at = None
        token = None
        try:
            record, started_at = self._start_execution(job_name, job_type, source=source, target_id=target_id)
            token = self.current_execution_id.set(record.id)
            with self.run_lock:
                self.running_meta[run_key] = {
                    'run_key': run_key,
                    'execution_id': record.id,
                    'job_name': job_name,
                    'job_type': job_type,
                    'source': source,
                    'target_id': target_id,
                    'started_at': started_at,
                }

            ok, message = runner(lambda: self.is_stop_requested(run_key))
            self._finish_execution(record, started_at, ok, message)
            return ok, message
        except Exception as exc:
            self.db.session.rollback()
            err = f'执行异常: {exc}'
            if record and started_at:
                try:
                    self._finish_execution(record, started_at, False, err)
                except Exception:
                    self.db.session.rollback()
            log_operation('job_execute_failed', 'Job', target_id, job_name, err, False)
            return False, err
        finally:
            if token is not None:
                self.current_execution_id.reset(token)
            with self.run_lock:
                self.running_keys.discard(run_key)
                self.running_meta.pop(run_key, None)
                self.stop_requested_keys.discard(run_key)
                held_lock = self.file_locks.pop(run_key, None)
            if held_lock is not None:
                try:
                    fcntl.flock(held_lock.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                held_lock.close()
