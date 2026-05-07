from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app as app_module


def _row(source_type='manual', torrent_hash=None, downloader_id=None, created_at=None):
    return SimpleNamespace(
        source_type=source_type,
        torrent_hash=torrent_hash,
        downloader_id=downloader_id,
        created_at=created_at or datetime.now(UTC),
        backfill_fail_count=2,
        backfill_last_attempt_at=datetime.now(UTC),
    )


def test_effective_source_type_pending_window_without_match(monkeypatch):
    row = _row(source_type='manual', created_at=datetime.now(UTC) - timedelta(seconds=60))

    def fake_get_config(key, default=None):
        if key == 'pending_source_guard_enabled':
            return 'true'
        if key == 'pending_source_guard_seconds':
            return '900'
        return default

    monkeypatch.setattr(app_module, 'get_config', fake_get_config)
    monkeypatch.setattr(app_module, 'try_match_torrent_by_mapping_or_name', lambda *_: (None, 'no_match'))

    source_type, pending_hash, pending_match = app_module._effective_source_type_for_delete(
        row, '/tmp/a.mkv', downloader=SimpleNamespace(id=1)
    )

    assert source_type == 'pending'
    assert pending_hash is None
    assert pending_match == 'no_match'


def test_effective_source_type_pending_window_mapping_hash_promotes(monkeypatch):
    row = _row(source_type='pending', created_at=datetime.now(UTC) - timedelta(seconds=10))

    def fake_get_config(key, default=None):
        if key == 'pending_source_guard_enabled':
            return 'true'
        if key == 'pending_source_guard_seconds':
            return '900'
        return default

    monkeypatch.setattr(app_module, 'get_config', fake_get_config)
    monkeypatch.setattr(app_module, 'try_match_torrent_by_mapping_or_name', lambda *_: ('hash123', 'mapping_hash'))

    downloader = SimpleNamespace(id=99)
    source_type, pending_hash, pending_match = app_module._effective_source_type_for_delete(
        row, '/tmp/a.mkv', downloader=downloader
    )

    assert source_type == 'downloader'
    assert pending_hash == 'hash123'
    assert pending_match == 'mapping_hash'
    assert row.source_type == 'downloader'
    assert row.downloader_id == 99
    assert row.torrent_hash == 'hash123'
    assert row.backfill_fail_count == 0
    assert row.backfill_last_attempt_at is None


def test_effective_source_type_old_manual_stays_manual(monkeypatch):
    row = _row(source_type='manual', created_at=datetime.now(UTC) - timedelta(seconds=3600))

    def fake_get_config(key, default=None):
        if key == 'pending_source_guard_enabled':
            return 'true'
        if key == 'pending_source_guard_seconds':
            return '900'
        return default

    monkeypatch.setattr(app_module, 'get_config', fake_get_config)

    source_type, pending_hash, pending_match = app_module._effective_source_type_for_delete(
        row, '/tmp/a.mkv', downloader=SimpleNamespace(id=1)
    )

    assert source_type == 'manual'
    assert pending_hash is None
    assert pending_match is None
