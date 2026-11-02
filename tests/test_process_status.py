from datetime import UTC, datetime

import app as app_module


def _client_with_csrf():
    client = app_module.app.test_client()
    token = 'process-status-test-token'
    with client.session_transaction() as sess:
        sess['_csrf_token'] = token
    return client, token


def test_process_status_endpoint_reports_heartbeat_and_controls():
    client = app_module.app.test_client()
    with app_module.app.app_context():
        old_worker = app_module.get_config('worker_last_seen_at', '')
        old_scheduler = app_module.get_config('scheduler_last_seen_at', '')
        app_module.set_config('worker_last_seen_at', datetime.now(UTC).isoformat())
        app_module.set_config('scheduler_last_seen_at', datetime.now(UTC).isoformat())
        app_module.set_config('worker_desired_state', 'running')
        app_module.set_config('scheduler_desired_state', 'running')
    try:
        response = client.get('/api/runtime/processes')
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is True
        assert data['embedded'] is False
        assert data['worker']['online'] is True
        assert data['scheduler']['online'] is True
    finally:
        with app_module.app.app_context():
            app_module.set_config('worker_last_seen_at', old_worker)
            app_module.set_config('scheduler_last_seen_at', old_scheduler)


def test_process_control_route_updates_desired_state():
    client, token = _client_with_csrf()
    with app_module.app.app_context():
        old_worker = app_module.get_config('worker_desired_state', 'running')
        old_scheduler = app_module.get_config('scheduler_desired_state', 'running')
    supervisor = app_module.PROCESS_SUPERVISOR
    original_enabled = supervisor.enabled
    try:
        supervisor.enabled = lambda: True
        response = client.post('/settings/processes/worker/stop', data={'csrf_token': token}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app_module.app.app_context():
            assert app_module.get_config('worker_desired_state') == 'stopped'
        response = client.post('/settings/processes/scheduler/restart', data={'csrf_token': token}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app_module.app.app_context():
            assert app_module.get_config('scheduler_desired_state') == 'running'
    finally:
        supervisor.enabled = original_enabled
        with app_module.app.app_context():
            app_module.set_config('worker_desired_state', old_worker)
            app_module.set_config('scheduler_desired_state', old_scheduler)
