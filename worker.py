"""Background worker for HLM-Demo JobQueue tasks."""

#!/usr/bin/env python3
import argparse
import os
import socket
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault('HLM_WORKER', 'true')

from app import EXEC_MGR, JobQueue, app, claim_next_job, db, execute_queued_job, finish_job, init_app, set_config


def _monitor_stop(job_id, run_key, done_event):
    while not done_event.wait(1.0):
        with app.app_context():
            try:
                db.session.remove()
                row = db.session.get(JobQueue, job_id)
                if row and row.stop_requested:
                    EXEC_MGR.request_stop_by_run_key(run_key)
                    return
            except Exception:
                db.session.rollback()


def heartbeat_loop(stop_event):
    """Periodically record worker liveness for the web process."""
    while not stop_event.wait(10):
        with app.app_context():
            try:
                set_config('worker_last_seen_at', datetime.now(UTC).isoformat())
            except Exception:
                db.session.rollback()


def reconcile_stale_jobs(grace_seconds=3600):
    """Requeue or fail jobs whose worker lease or start time has expired."""
    cutoff = datetime.now(UTC) - timedelta(seconds=max(60, int(grace_seconds)))
    with app.app_context():
        now = datetime.now(UTC)
        stale = JobQueue.query.filter(
            JobQueue.status == 'running',
            (JobQueue.lease_expires_at.is_not(None) & (JobQueue.lease_expires_at < now)) | (JobQueue.started_at < cutoff),
        ).all()
        for row in stale:
            row.lease_owner = None
            row.lease_expires_at = None
            if int(row.retry_count or 0) < int(row.max_retries or 0):
                row.retry_count = int(row.retry_count or 0) + 1
                row.status = 'queued'
                row.started_at = None
                row.finished_at = None
                row.message = f'worker 中断，第 {row.retry_count} 次重试排队'
            else:
                row.status = 'failed'
                row.message = 'worker 重启或执行超时，任务状态已修正'
                row.finished_at = now
        if stale:
            db.session.commit()
            app.logger.warning('reconciled stale queued jobs: %s', len(stale))
        return len(stale)


def process_one(worker_id):
    """Claim and execute at most one queued job."""
    with app.app_context():
        row = claim_next_job(worker_id)
        if not row:
            return False
        job_id = row.id
        run_key = row.run_key
        if row.stop_requested:
            row.status = 'cancelled'
            row.message = '任务在开始前被取消'
            row.finished_at = datetime.now(UTC)
            db.session.commit()
            return True
        done_event = threading.Event()
        monitor = threading.Thread(target=_monitor_stop, args=(job_id, run_key, done_event), daemon=True)
        monitor.start()
        try:
            ok, message = execute_queued_job(row)
        except Exception as exc:
            db.session.rollback()
            ok, message = False, f'执行异常: {exc}'
        finally:
            done_event.set()
            monitor.join(timeout=2)
        finish_job(row, ok, message)
        return True


def main():
    parser = argparse.ArgumentParser(description='HLM background worker')
    parser.add_argument('--once', action='store_true', help='process at most one queued job then exit')
    parser.add_argument('--poll-interval', type=float, default=2.0)
    args = parser.parse_args()

    init_app()
    reconcile_stale_jobs()
    with app.app_context():
        set_config('worker_last_seen_at', datetime.now(UTC).isoformat())
    worker_id = f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    stop_event = threading.Event()
    heartbeat = threading.Thread(target=heartbeat_loop, args=(stop_event,), daemon=True)
    heartbeat.start()
    app.logger.info('worker started id=%s', worker_id)
    try:
        while True:
            try:
                worked = process_one(worker_id)
                if args.once:
                    return
                if not worked:
                    time.sleep(max(0.2, args.poll_interval))
            except KeyboardInterrupt:
                app.logger.info('worker interrupted')
                return
            except Exception as exc:
                app.logger.exception('worker loop error: %s', exc)
                time.sleep(max(1.0, args.poll_interval))
    finally:
        stop_event.set()


if __name__ == '__main__':
    main()
