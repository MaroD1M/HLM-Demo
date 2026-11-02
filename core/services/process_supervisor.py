"""Embedded supervisor for worker and scheduler child processes."""

import atexit
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None


class ProcessSupervisor:
    """Runs worker/scheduler children inside the single-container deployment."""

    CHILDREN = ('worker', 'scheduler')

    def __init__(self, app, db, get_config, set_config, logger):
        self.app = app
        self.db = db
        self.get_config = get_config
        self.set_config = set_config
        self.logger = logger
        self.root = Path(app.root_path)
        self.instance_path = Path(app.instance_path)
        self._thread = None
        self._stop_event = threading.Event()
        self._lock_handle = None
        self._leader = False
        self._started = False
        self._children = {}
        self._last_nonce = dict.fromkeys(self.CHILDREN, '')
        self._last_start = dict.fromkeys(self.CHILDREN, 0.0)
        self._last_error = dict.fromkeys(self.CHILDREN, '')
        self._state = dict.fromkeys(self.CHILDREN, 'unknown')

    def enabled(self):
        if self.app.config.get('WORKER_MODE'):
            return False
        if str(os.environ.get('HLM_EMBEDDED_SUPERVISOR', 'true')).lower() in {'0', 'false', 'no', 'off'}:
            return False
        if str(os.environ.get('HLM_SCHEDULER_ONLY', 'false')).lower() == 'true':
            return False
        return True

    def start(self):
        if not self.enabled() or self._started:
            return
        self._started = True
        atexit.register(self.shutdown)
        self._thread = threading.Thread(target=self._run, name='hlm-process-supervisor', daemon=True)
        self._thread.start()
        self.logger.info('embedded process supervisor started')

    def shutdown(self):
        self._stop_event.set()
        for name in self.CHILDREN:
            self._stop_child(name, final=True)
        if self._lock_handle is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._lock_handle.close()
            except Exception:
                pass
            self._lock_handle = None

    def _acquire_lock(self):
        if fcntl is None:
            return True
        try:
            self.instance_path.mkdir(parents=True, exist_ok=True)
            handle = (self.instance_path / 'embedded_supervisor.lock').open('w', encoding='utf-8')
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_handle = handle
            return True
        except BlockingIOError:
            return False
        except Exception as exc:
            self.logger.warning('embedded supervisor lock unavailable: %s', exc)
            return True

    def _run(self):
        while not self._stop_event.is_set():
            if not self._leader:
                if not self._acquire_lock():
                    self._stop_event.wait(5)
                    continue
                self._leader = True
                self.logger.info('embedded process supervisor acquired leadership')
            try:
                self._tick()
            except Exception as exc:
                self.logger.warning('embedded process supervisor tick failed: %s', exc)
            self._stop_event.wait(2)
        self.shutdown()

    def _tick(self):
        with self.app.app_context():
            now = datetime.now(UTC).isoformat()
            for name in self.CHILDREN:
                desired = str(self.get_config(f'{name}_desired_state', 'running') or 'running').strip().lower()
                nonce = str(self.get_config(f'{name}_control_nonce', '') or '').strip()
                if nonce and nonce != self._last_nonce[name]:
                    self._last_nonce[name] = nonce
                    if desired == 'running':
                        self._restart_child(name)
                    else:
                        self._stop_child(name)
                if desired == 'stopped':
                    self._stop_child(name)
                else:
                    self._ensure_child(name)
                self._write_state(name)
            self.set_config('supervisor_last_seen_at', now, commit=False)
            self.db.session.commit()
            self.db.session.remove()

    def _child_env(self, name):
        env = os.environ.copy()
        env['HLM_INSTANCE_PATH'] = str(self.instance_path)
        env['HLM_EMBEDDED_SUPERVISOR'] = 'false'
        env['HLM_PARENT_PID'] = str(os.getpid())
        env['PYTHONUNBUFFERED'] = '1'
        if name == 'worker':
            env['HLM_WORKER'] = 'true'
            env['HLM_DISABLE_SCHEDULER'] = 'true'
            env.pop('HLM_SCHEDULER_ONLY', None)
        else:
            env['HLM_WORKER'] = 'false'
            env['HLM_DISABLE_SCHEDULER'] = 'false'
            env['HLM_SCHEDULER_ONLY'] = 'true'
        return env

    def _heartbeat_online(self, name):
        raw = str(self.get_config(f'{name}_last_seen_at', '') or '').strip()
        if not raw:
            return False
        try:
            seen = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=UTC)
            try:
                ttl = int(self.get_config('worker_heartbeat_ttl_seconds', '45') or '45')
            except Exception:
                ttl = 45
            return (datetime.now(UTC) - seen.astimezone(UTC)).total_seconds() <= max(5, ttl)
        except Exception:
            return False

    def _ensure_child(self, name):
        proc = self._children.get(name)
        if proc is not None and proc.poll() is None:
            self._state[name] = 'running'
            return
        if proc is not None:
            code = proc.poll()
            self._last_error[name] = f'进程已退出（code={code}）'
            self._children.pop(name, None)
            self._state[name] = 'error'
            if self._heartbeat_online(name):
                self._state[name] = 'external'
                self._last_error[name] = ''
                return
        now = time.monotonic()
        if now - self._last_start[name] < 5:
            return
        self._last_start[name] = now
        script = self.root / ('worker.py' if name == 'worker' else 'scheduler.py')
        if not script.exists():
            self._last_error[name] = f'脚本不存在: {script}'
            self._state[name] = 'error'
            return
        try:
            self._state[name] = 'starting'
            proc = subprocess.Popen(
                [sys.executable, '-u', str(script)],
                cwd=str(self.root),
                env=self._child_env(name),
            )
            self._children[name] = proc
            self._state[name] = 'running'
            self._last_error[name] = ''
            self.logger.info('embedded %s started pid=%s', name, proc.pid)
        except Exception as exc:
            self._last_error[name] = str(exc)
            self._state[name] = 'error'
            self.logger.warning('embedded %s start failed: %s', name, exc)

    def _stop_child(self, name, final=False):
        proc = self._children.get(name)
        if proc is None:
            if not final:
                self._state[name] = 'stopped'
            return
        if proc.poll() is None:
            self._state[name] = 'stopping'
            try:
                with self.app.app_context():
                    self._write_state(name)
                    self.db.session.commit()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            except Exception:
                pass
        self._children.pop(name, None)
        self._state[name] = 'stopped'
        if not final:
            self.logger.info('embedded %s stopped', name)

    def _restart_child(self, name):
        self._stop_child(name)
        self._last_start[name] = 0.0
        self._ensure_child(name)

    def _write_state(self, name):
        proc = self._children.get(name)
        pid = str(proc.pid) if proc is not None and proc.poll() is None else ''
        self.set_config(f'{name}_pid', pid, commit=False)
        self.set_config(f'{name}_manager_state', self._state[name], commit=False)
        self.set_config(f'{name}_manager_error', self._last_error[name], commit=False)
        self.set_config(f'{name}_manager_updated_at', datetime.now(UTC).isoformat(), commit=False)

    def status(self):
        return {
            'enabled': self.enabled(),
            'leader': bool(self._leader),
            'worker': dict(self._child_status('worker')),
            'scheduler': dict(self._child_status('scheduler')),
        }

    def _child_status(self, name):
        proc = self._children.get(name)
        return {
            'desired': str(self.get_config(f'{name}_desired_state', 'running') or 'running'),
            'state': self._state[name],
            'pid': proc.pid if proc is not None and proc.poll() is None else None,
            'error': self._last_error[name],
        }
