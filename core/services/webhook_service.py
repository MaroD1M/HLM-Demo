from __future__ import annotations

import json
from urllib import request as urlrequest


def dispatch_webhook(event_type, payload, get_config, logger=None):
    enabled = str(get_config('webhook_enabled', 'false') or 'false').lower() == 'true'
    url = (get_config('webhook_url', '') or '').strip()
    if not enabled or not url:
        return False, 'webhook disabled'

    headers = {'Content-Type': 'application/json'}
    secret = (get_config('webhook_secret', '') or '').strip()
    if secret:
        headers['X-HLM-Webhook-Secret'] = secret

    body = json.dumps({
        'event_type': event_type,
        'payload': payload,
    }, ensure_ascii=False).encode('utf-8')

    req = urlrequest.Request(url, data=body, headers=headers, method='POST')
    try:
        with urlrequest.urlopen(req, timeout=5) as resp:
            status = getattr(resp, 'status', 200) or 200
        return True, f'HTTP {status}'
    except Exception as exc:
        if logger:
            logger.warning('webhook dispatch failed event=%s err=%s', event_type, exc)
        return False, str(exc)
