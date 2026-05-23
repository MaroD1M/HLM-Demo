from __future__ import annotations

from typing import Iterable


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

