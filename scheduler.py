"""Standalone APScheduler process for HLM-Demo cron jobs."""

#!/usr/bin/env python3
import os
import signal
import time
from datetime import UTC, datetime

os.environ.setdefault('HLM_SCHEDULER_ONLY', 'true')
os.environ.setdefault('HLM_EMBEDDED_SUPERVISOR', 'false')

from app import app, init_app, scheduler, set_config

_stop = False


def _handle_signal(_signum, _frame):
    global _stop
    _stop = True


def _set_parent_death_signal():
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').prctl(1, signal.SIGTERM)
    except Exception:
        pass


def main():
    _set_parent_death_signal()
    init_app()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if not scheduler.running:
        app.logger.warning('scheduler lock is held by another process; scheduler child exits')
        return
    with app.app_context():
        set_config('scheduler_last_seen_at', datetime.now(UTC).isoformat())
    app.logger.info('scheduler process started')
    parent_raw = str(os.environ.get('HLM_PARENT_PID', '') or '').strip()
    try:
        parent_pid = int(parent_raw) if parent_raw else None
    except Exception:
        parent_pid = None
    heartbeat_tick = 0
    while not _stop:
        if parent_pid is not None and os.getppid() != parent_pid:
            app.logger.info('parent process changed; scheduler child exits')
            break
        if heartbeat_tick % 5 == 0:
            with app.app_context():
                try:
                    set_config('scheduler_last_seen_at', datetime.now(UTC).isoformat())
                except Exception:
                    pass
        heartbeat_tick += 1
        time.sleep(2)
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    app.logger.info('scheduler process stopped')
    os._exit(0)


if __name__ == '__main__':
    main()
