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
