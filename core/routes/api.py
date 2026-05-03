from datetime import datetime, UTC
from flask import Blueprint, jsonify

api_bp = Blueprint('api_bp', __name__)


def init_api_routes(HardlinkTask, DeleteMonitorTask):
    @api_bp.get('/api/health')
    def api_health():
        return jsonify({'status': 'ok', 'timestamp': datetime.now(UTC).isoformat()})

    @api_bp.get('/api/tasks/status')
    def api_tasks_status():
        return jsonify({
            'hardlink_tasks': [{'id': t.id, 'name': t.name, 'enabled': t.enabled} for t in HardlinkTask.query.all()],
            'delete_tasks': [{'id': t.id, 'name': t.name, 'enabled': t.enabled} for t in DeleteMonitorTask.query.all()],
        })

    return api_bp
