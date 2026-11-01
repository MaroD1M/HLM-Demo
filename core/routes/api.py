"""JSON API, background job API, metrics, and OpenAPI endpoints."""

from datetime import datetime, UTC
from flask import Blueprint, Response, jsonify, request

from core.services.webhook_service import dispatch_webhook

api_bp = Blueprint('api_bp', __name__)


def _job_payload(row):
    return {
        'id': row.id,
        'run_key': row.run_key,
        'name': row.job_name,
        'type': row.job_type,
        'target_id': row.target_id,
        'source': row.source,
        'status': row.status,
        'stop_requested': bool(row.stop_requested),
        'worker_id': row.worker_id,
        'message': row.message,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'started_at': row.started_at.isoformat() if row.started_at else None,
        'finished_at': row.finished_at.isoformat() if row.finished_at else None,
    }


def init_api_routes(HardlinkTask, DeleteMonitorTask, AppConfig=None, OperationLog=None, db=None, get_config=None, get_release_info=None, get_running_executions_snapshot=None, logger=None, JobQueue=None, enqueue_job=None, request_stop_queued_job=None, started_at=None):
    @api_bp.post('/api/v1/jobs')
    def api_create_job():
        if not JobQueue or not enqueue_job:
            return jsonify({'ok': False, 'message': 'job queue unavailable'}), 503
        payload = request.get_json(silent=True) or {}
        job_type = str(payload.get('type') or '').strip().lower()
        target_id = payload.get('target_id')
        if target_id in ('', None):
            target_id = None
        row, created, message = enqueue_job(job_type, target_id=target_id, source='api', payload=payload, priority=payload.get('priority', 0), max_retries=payload.get('max_retries', 0))
        if not row:
            return jsonify({'ok': False, 'message': message}), 400
        return jsonify({'ok': True, 'created': created, 'message': message, 'job': _job_payload(row)}), (201 if created else 200)

    @api_bp.get('/api/v1/jobs')
    def api_list_jobs():
        if not JobQueue:
            return jsonify({'ok': False, 'message': 'job queue unavailable'}), 503
        rows = JobQueue.query.order_by(JobQueue.id.desc()).limit(100).all()
        return jsonify({'ok': True, 'items': [_job_payload(row) for row in rows]})

    @api_bp.get('/api/v1/jobs/<int:job_id>')
    def api_get_job(job_id):
        if not JobQueue:
            return jsonify({'ok': False, 'message': 'job queue unavailable'}), 503
        row = db.session.get(JobQueue, job_id) if db else None
        if not row:
            return jsonify({'ok': False, 'message': 'job not found'}), 404
        return jsonify({'ok': True, 'job': _job_payload(row)})

    @api_bp.post('/api/v1/jobs/<int:job_id>/stop')
    def api_stop_job(job_id):
        if not request_stop_queued_job:
            return jsonify({'ok': False, 'message': 'job queue unavailable'}), 503
        ok, message = request_stop_queued_job(job_id)
        return jsonify({'ok': ok, 'message': message}), (200 if ok else 400)

    @api_bp.get('/api/v1/metrics')
    def api_metrics():
        lines = []
        hardlink_count = HardlinkTask.query.count() if HardlinkTask else 0
        delete_count = DeleteMonitorTask.query.count() if DeleteMonitorTask else 0
        lines.append(f'hlm_tasks_total{{type="hardlink"}} {hardlink_count}')
        lines.append(f'hlm_tasks_total{{type="delete_monitor"}} {delete_count}')
        if JobQueue and db is not None:
            for status, count in db.session.query(JobQueue.status, db.func.count(JobQueue.id)).group_by(JobQueue.status).all():
                lines.append(f'hlm_job_queue{{status="{status or "unknown"}"}} {count}')
        if OperationLog is not None:
            lines.append(f'hlm_operations_total {OperationLog.query.count()}')
        running = get_running_executions_snapshot() if get_running_executions_snapshot else []
        lines.append(f'hlm_running_jobs {len(running)}')
        if started_at is not None:
            lines.append(f'hlm_uptime_seconds {max(0, int((datetime.now(UTC) - started_at).total_seconds()))}')
        return Response('\n'.join(lines) + '\n', mimetype='text/plain; version=0.0.4')

    @api_bp.get('/api/v1/openapi.json')
    def api_openapi():
        spec = {
            'openapi': '3.0.3',
            'info': {'title': 'HLM API', 'version': '1.0.0'},
            'paths': {
                '/api/health': {'get': {'summary': 'Health check'}},
                '/api/tasks/status': {'get': {'summary': 'Task enable status'}},
                '/api/runtime/summary': {'get': {'summary': 'Runtime summary'}},
                '/api/v1/jobs': {
                    'get': {'summary': 'List jobs'},
                    'post': {'summary': 'Queue a job', 'requestBody': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {'type': {'type': 'string', 'enum': ['hardlink', 'delete', 'backfill', 'backup']}, 'target_id': {'type': 'integer'}}}}}}},
                },
                '/api/v1/jobs/{job_id}': {'get': {'summary': 'Get job status', 'parameters': [{'name': 'job_id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}]}},
                '/api/v1/jobs/{job_id}/stop': {'post': {'summary': 'Request job stop', 'parameters': [{'name': 'job_id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}]}},
                '/api/v1/metrics': {'get': {'summary': 'Prometheus metrics'}},
            },
            'components': {'securitySchemes': {'ApiToken': {'type': 'apiKey', 'in': 'header', 'name': 'X-API-Token'}}},
        }
        return jsonify(spec)

    @api_bp.get('/api/version')
    def api_version():
        if not get_release_info:
            return jsonify({'ok': False, 'message': 'version info unavailable'}), 503
        release = get_release_info(allow_network=False)
        return jsonify({
            'ok': True,
            'local_version': release.get('local_version', '-'),
            'remote_version': release.get('remote_version', '-'),
            'has_update': bool(release.get('has_update')),
            'message': release.get('message', '-'),
            'checked_at': release.get('checked_at', '-'),
            'release': release,
        })

    @api_bp.get('/api/health')
    def api_health():
        db_ok = True
        if db is not None:
            try:
                db.session.execute(db.text('SELECT 1')).fetchone()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                db_ok = False
        payload = {
            'ok': db_ok,
            'status': 'ok' if db_ok else 'degraded',
            'message': 'ok' if db_ok else 'database unavailable',
            'timestamp': datetime.now(UTC).isoformat(),
        }
        return jsonify(payload), (200 if db_ok else 503)

    @api_bp.get('/api/tasks/status')
    def api_tasks_status():
        return jsonify({
            'hardlink_tasks': [{'id': t.id, 'name': t.name, 'enabled': t.enabled} for t in HardlinkTask.query.all()],
            'delete_tasks': [{'id': t.id, 'name': t.name, 'enabled': t.enabled} for t in DeleteMonitorTask.query.all()],
        })

    @api_bp.get('/api/runtime/summary')
    def api_runtime_summary():
        if not AppConfig or not OperationLog or not get_config or not get_release_info or not get_running_executions_snapshot:
            return jsonify({'ok': False, 'message': 'runtime summary unavailable'}), 503
        settings = {c.key: c.value for c in AppConfig.query.all()}
        release = get_release_info(allow_network=False)
        running = get_running_executions_snapshot()
        recent_logs = OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all()
        return jsonify({
            'ok': True,
            'release': release,
            'settings': {
                'read_only': settings.get('security_read_only_enabled', 'false'),
                'ip_allowlist': settings.get('security_ip_allowlist_enabled', 'false'),
                'webhook_enabled': settings.get('webhook_enabled', 'false'),
            },
            'running_jobs': len(running),
            'recent_logs': [{'id': r.id, 'type': r.operation_type, 'ok': r.success, 'message': r.message} for r in recent_logs],
        })

    @api_bp.post('/api/webhooks/test')
    def api_webhook_test():
        if not get_config:
            return jsonify({'ok': False, 'message': 'config unavailable'}), 503
        payload = request.get_json(silent=True) or {}
        ok, detail = dispatch_webhook('api_webhook_test', payload, get_config, logger=logger)
        return jsonify({'ok': ok, 'message': detail}), (200 if ok else 400)

    return api_bp
