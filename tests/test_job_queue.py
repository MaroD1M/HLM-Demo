import json
import uuid

import app as app_module
from core.models import JobQueue


def test_enqueue_backfill_single_job_with_deep_payload():
    target_id = int(uuid.uuid4().hex[:8], 16)
    with app_module.app.app_context():
        row, created, message = app_module.enqueue_job(
            'backfill_single',
            target_id=target_id,
            payload={'deep_retry': True},
            job_name='单条映射回填（深度重试）',
        )
        assert row is not None
        assert created is True
        assert message == '已加入队列'
        assert row.run_key == f'backfill_single:{target_id}'
        assert row.job_type == 'backfill_single'
        assert json.loads(row.payload_json)['deep_retry'] is True
        job_id = row.id
        JobQueue.query.filter_by(id=job_id).delete(synchronize_session=False)
        app_module.db.session.commit()


def test_job_status_batch_endpoint_returns_requested_jobs():
    target_a = int(uuid.uuid4().hex[:8], 16)
    target_b = int(uuid.uuid4().hex[:8], 16)
    with app_module.app.app_context():
        job_a, _created_a, _msg_a = app_module.enqueue_job('backfill_single', target_id=target_a, payload={'deep_retry': False})
        job_b, _created_b, _msg_b = app_module.enqueue_job('backfill_single', target_id=target_b, payload={'deep_retry': True})
        job_ids = [job_a.id, job_b.id]
    try:
        client = app_module.app.test_client()
        response = client.get(f'/api/v1/jobs/status?ids={job_ids[0]},{job_ids[1]}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is True
        returned = {int(job['id']) for job in data['jobs']}
        assert returned == {job_ids[0], job_ids[1]}
    finally:
        with app_module.app.app_context():
            JobQueue.query.filter(JobQueue.id.in_(job_ids)).delete(synchronize_session=False)
            app_module.db.session.commit()
