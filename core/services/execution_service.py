from contextvars import ContextVar
from datetime import datetime
from threading import Lock


class ExecutionManager:
    def __init__(self, db, job_execution_model, utc_now):
        self.db = db
        self.job_execution_model = job_execution_model
        self.utc_now = utc_now
        self.run_lock = Lock()
        self.running_meta = {}
        self.stop_requested_keys = set()
        self.running_keys = set()
        self.current_execution_id = ContextVar('current_execution_id', default=None)

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
