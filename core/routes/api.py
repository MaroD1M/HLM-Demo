from datetime import datetime, UTC
from flask import Blueprint, jsonify, request

from core.services.webhook_service import dispatch_webhook

api_bp = Blueprint('api_bp', __name__)


def init_api_routes(HardlinkTask, DeleteMonitorTask, AppConfig=None, OperationLog=None, db=None, get_config=None, get_release_info=None, get_running_executions_snapshot=None, logger=None):
    @api_bp.get('/api/health')
    def api_health():
        return jsonify({'ok': True, 'status': 'ok', 'message': 'ok', 'timestamp': datetime.now(UTC).isoformat()})

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
        release = get_release_info()
        running = get_running_executions_snapshot()
        recent_logs = OperationLog.query.order_by(OperationLog.created_at.desc()).limit(10).all()
        return jsonify({
            'ok': True,
            'release': release,
            'settings': {
                'dev_mode': settings.get('dev_mode', 'false'),
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
