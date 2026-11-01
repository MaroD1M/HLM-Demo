"""Audit message helpers and credential URL redaction."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit


def build_kv_message(**fields) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        parts.append(f'{key}={text}')
    return ';'.join(parts)


def summarize_changed_keys(keys: Iterable[str], limit: int = 12) -> str:
    items = [str(k).strip() for k in keys if str(k or '').strip()]
    if not items:
        return '-'
    if len(items) <= limit:
        return ','.join(items)
    return ','.join(items[:limit]) + f',...(+{len(items) - limit})'



def redact_url_credentials(value):
    text = str(value or '')
    if '://' not in text:
        return text
    try:
        parts = urlsplit(text)
        if not parts.username and not parts.password:
            return text
        host = parts.hostname or ''
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        if parts.port:
            host = f'{host}:{parts.port}'
        netloc = '***@' + host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return text
