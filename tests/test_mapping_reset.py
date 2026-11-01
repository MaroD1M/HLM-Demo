import uuid

import app as app_module
from core.models import HardlinkTask, FileLinkMap, OperationLog


def _client_with_csrf():
    client = app_module.app.test_client()
    token = 'mapping-reset-test-token'
    with client.session_transaction() as sess:
        sess['_csrf_token'] = token
    return client, token


def test_mapping_reset_cooldown_bulk_and_page_clamp():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        old_cooldown = app_module.get_config('mapping_reset_cooldown_seconds', '60')
        app_module.set_config('mapping_reset_cooldown_seconds', '60')
        task = HardlinkTask(name=f'reset-{marker}', source_dir=f'/tmp/src-{marker}', dest_dir=f'/tmp/dst-{marker}', extensions='.mkv')
        app_module.db.session.add(task)
        app_module.db.session.flush()
        rows = []
        for index in (1, 2):
            row = FileLinkMap(
                task_id=task.id,
                source_path=f'/tmp/src-{marker}/file-{index}.mkv',
                dest_path=f'/tmp/dst-{marker}/file-{index}.mkv',
                file_size=1,
                source_type='pending',
                backfill_fail_count=3,
            )
            app_module.db.session.add(row)
            rows.append(row)
        app_module.db.session.commit()
        task_id = task.id
        row_ids = [row.id for row in rows]

    client, token = _client_with_csrf()
    try:
        response = client.post(
            f'/mapping/link/reset-fail/{row_ids[0]}',
            data={'csrf_token': token},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert response.status_code == 200

        response = client.post(
            f'/mapping/link/reset-fail/{row_ids[0]}',
            data={'csrf_token': token},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert response.status_code == 429
        assert '秒后再试' in response.get_json()['message']

        text = client.get(f'/mapping?page=99&q=file-1&panel_view=mapping').get_data(as_text=True)
        assert '第 1 / 1 页' in text

        response = client.post(
            '/mapping/link/reset-fail-bulk',
            data={'csrf_token': token, 'map_ids': [str(row_ids[0]), str(row_ids[1])]},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert response.status_code == 200
        message = response.get_json()['message']
        assert '已重置 1 条' in message
        assert '冷却' in message
    finally:
        with app_module.app.app_context():
            FileLinkMap.query.filter(FileLinkMap.id.in_(row_ids)).delete(synchronize_session=False)
            HardlinkTask.query.filter_by(id=task_id).delete(synchronize_session=False)
            OperationLog.query.filter(OperationLog.target_id.in_(row_ids), OperationLog.operation_type == 'mapping_backfill_fail_reset').delete(synchronize_session=False)
            app_module.set_config('mapping_reset_cooldown_seconds', old_cooldown)
            app_module.db.session.commit()
