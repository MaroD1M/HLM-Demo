import uuid
from datetime import UTC, datetime

import app as app_module
from core.models import FileLinkMap, HardlinkCache, HardlinkTask, JobExecutionLog, JobQueue


def _client_with_csrf():
    client = app_module.app.test_client()
    token = 'mapping-scope-test-token'
    with client.session_transaction() as sess:
        sess['_csrf_token'] = token
    return client, token


def _make_mapping(marker, index, *, deleted=False, linked=False, fail_count=0):
    task = HardlinkTask(name=f'scope-{marker}-{index}', source_dir=f'/tmp/src-{marker}', dest_dir=f'/tmp/dst-{marker}', extensions='.mkv')
    app_module.db.session.add(task)
    app_module.db.session.flush()
    row = FileLinkMap(
        task_id=task.id,
        source_path=f'/tmp/src-{marker}/scope-{index}.mkv',
        dest_path=f'/tmp/dst-{marker}/scope-{index}.mkv',
        file_size=1,
        source_type='pending',
        torrent_hash=f'hash-{marker}-{index}' if linked else None,
        backfill_fail_count=fail_count,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    app_module.db.session.add(row)
    app_module.db.session.commit()
    return task, row


def _cleanup(task_ids, row_ids):
    with app_module.app.app_context():
        app_module.db.session.rollback()
        JobQueue.query.filter(JobQueue.target_id.in_(row_ids), JobQueue.job_type == 'backfill_single').delete(synchronize_session=False)
        FileLinkMap.query.filter(FileLinkMap.id.in_(row_ids)).delete(synchronize_session=False)
        HardlinkTask.query.filter(HardlinkTask.id.in_(task_ids)).delete(synchronize_session=False)
        app_module.db.session.commit()
        app_module.db.session.remove()


def test_mapping_page_excludes_soft_deleted_and_cache_hides_mapping_filters():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        task_active, row_active = _make_mapping(marker, 1)
        task_deleted, row_deleted = _make_mapping(marker, 2, deleted=True)
        task_ids = [task_active.id, task_deleted.id]
        row_ids = [row_active.id, row_deleted.id]
    try:
        client, _ = _client_with_csrf()
        text = client.get(f'/mapping?q={marker}&hash_state=all&panel_view=mapping').get_data(as_text=True)
        assert row_active.source_path in text
        assert row_deleted.source_path not in text
        cache_text = client.get('/mapping?panel_view=cache').get_data(as_text=True)
        assert 'name="hash_state"' not in cache_text
        assert 'name="source_type"' not in cache_text
        assert 'name="fail_state"' not in cache_text
    finally:
        _cleanup(task_ids, row_ids)


def test_bulk_reset_filtered_scope_and_bulk_retry_enqueue():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        old_worker = app_module.get_config('worker_last_seen_at', '')
        old_cooldown = app_module.get_config('mapping_reset_cooldown_seconds', '60')
        app_module.set_config('worker_last_seen_at', datetime.now(UTC).isoformat())
        app_module.set_config('mapping_reset_cooldown_seconds', '0')
        task_a, row_a = _make_mapping(marker, 1, fail_count=3)
        task_b, row_b = _make_mapping(marker, 2, fail_count=3)
        task_c, row_c = _make_mapping(marker, 3, linked=True)
        task_ids = [task_a.id, task_b.id, task_c.id]
        row_ids = [row_a.id, row_b.id, row_c.id]
    try:
        client, token = _client_with_csrf()
        response = client.post('/mapping/link/reset-fail-bulk', data={
            'csrf_token': token,
            'scope': 'filtered',
            'q': marker,
            'hash_state': 'all',
            'source_type': 'all',
            'fail_state': 'all',
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app_module.app.app_context():
            assert app_module.db.session.get(FileLinkMap, row_a.id).backfill_fail_count == 0
            assert app_module.db.session.get(FileLinkMap, row_b.id).backfill_fail_count == 0

        response = client.post('/mapping/link/retry-bulk', data={
            'csrf_token': token,
            'scope': 'filtered',
            'q': marker,
            'hash_state': 'all',
            'source_type': 'all',
            'fail_state': 'all',
            'retry_mode': 'fast',
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        data = response.get_json()
        assert len(data.get('job_ids') or []) == 2
        assert '已关联已跳过' in data['message']
        with app_module.app.app_context():
            queued = JobQueue.query.filter(JobQueue.job_type == 'backfill_single', JobQueue.target_id.in_([row_a.id, row_b.id])).all()
            assert len(queued) == 2
            jobs_text = client.get('/partials/jobs').get_data(as_text=True)
            assert '单条映射回填' in jobs_text
    finally:
        with app_module.app.app_context():
            app_module.set_config('worker_last_seen_at', old_worker)
            app_module.set_config('mapping_reset_cooldown_seconds', old_cooldown)
            _cleanup(task_ids, row_ids)


def test_execution_retry_handles_single_mapping_job():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        old_worker = app_module.get_config('worker_last_seen_at', '')
        app_module.set_config('worker_last_seen_at', datetime.now(UTC).isoformat())
        task, row = _make_mapping(marker, 1)
        execution = JobExecutionLog(job_name='单条映射回填（快速重试）', job_type='backfill_mapping_single', source='manual', target_id=row.id, status='failed', started_at=datetime.now(UTC))
        app_module.db.session.add(execution)
        app_module.db.session.commit()
        task_id, row_id, execution_id = task.id, row.id, execution.id
    try:
        client, token = _client_with_csrf()
        response = client.post(f'/executions/retry/{execution_id}', data={'csrf_token': token}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app_module.app.app_context():
            queued = JobQueue.query.filter_by(job_type='backfill_single', target_id=row_id).first()
            assert queued is not None
    finally:
        with app_module.app.app_context():
            app_module.db.session.rollback()
            app_module.set_config('worker_last_seen_at', old_worker)
            JobExecutionLog.query.filter_by(id=execution_id).delete(synchronize_session=False)
        _cleanup([task_id], [row_id])


def test_fast_retry_reuses_existing_queued_single_job():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        task, row = _make_mapping(marker, 1)
        job, _created, _msg = app_module.enqueue_job('backfill_single', target_id=row.id, source='web', payload={'deep_retry': True})
        task_id, row_id, job_id = task.id, row.id, job.id
    try:
        client, token = _client_with_csrf()
        response = client.post('/mapping/link/retry', data={'csrf_token': token, 'map_id': str(row_id), 'retry_mode': 'fast'}, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        assert '已有后台重试任务' in response.get_json()['message']
    finally:
        with app_module.app.app_context():
            app_module.db.session.rollback()
            JobQueue.query.filter_by(id=job_id).delete(synchronize_session=False)
            app_module.db.session.commit()
        _cleanup([task_id], [row_id])


def test_cache_bulk_delete_filtered_scope():
    marker = uuid.uuid4().hex[:10]
    with app_module.app.app_context():
        rows = []
        for index in (1, 2):
            row = HardlinkCache(source_path=f'/tmp/cache-{marker}-{index}.mkv', dest_path=f'/tmp/dst/cache-{marker}-{index}.mkv')
            app_module.db.session.add(row)
            rows.append(row)
        app_module.db.session.commit()
        row_ids = [row.id for row in rows]
    try:
        client, token = _client_with_csrf()
        response = client.post('/mapping/cache/bulk-delete', data={
            'csrf_token': token,
            'scope': 'filtered',
            'q': marker,
            'panel_view': 'cache',
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app_module.app.app_context():
            assert HardlinkCache.query.filter(HardlinkCache.id.in_(row_ids)).count() == 0
    finally:
        with app_module.app.app_context():
            app_module.db.session.rollback()
            HardlinkCache.query.filter(HardlinkCache.id.in_(row_ids)).delete(synchronize_session=False)
            app_module.db.session.commit()
            app_module.db.session.remove()
