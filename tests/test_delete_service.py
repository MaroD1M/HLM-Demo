from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from core.services.delete_service import scan_delete_rows


def _mk_task(tmp_path: Path, cooldown=120, max_per_run=2):
    return SimpleNamespace(
        id=1,
        name='t',
        directory=str(tmp_path),
        cooldown_seconds=cooldown,
        max_deletes_per_run=max_per_run,
        dry_run=True,
        notifier=None,
        notify_on_delete=True,
        notify_on_risky_delete=True,
        downloader=None,
    )


def _mk_row(row_id: int, src: Path, dst: Path, last_seen_at):
    return SimpleNamespace(
        id=row_id,
        source_path=str(src),
        dest_path=str(dst),
        last_seen_at=last_seen_at,
        deleted_at=None,
        source_type='manual',
        torrent_hash='',
        downloader_id=None,
    )


def _deps():
    logs = []

    def log_operation(op, *_args):
        logs.append(op)

    return (
        lambda _path, _downloader: (None, 'no_match'),
        lambda _downloader, _hash: True,
        log_operation,
        lambda _k, d=None: d,
        lambda _n, _m: None,
        lambda *_a, **_k: None,
        logs,
    )


def test_scan_delete_rows_only_marks_truncated_hits_deleted(tmp_path):
    task = _mk_task(tmp_path, max_per_run=2)
    now = datetime.now(UTC) - timedelta(seconds=600)
    rows = []
    for i in range(4):
        src = tmp_path / f's{i}.mkv'
        dst = tmp_path / f'd{i}.mkv'
        src.write_text('x', encoding='utf-8')
        dst.write_text('x', encoding='utf-8')
        src.unlink()  # simulate source deleted
        rows.append(_mk_row(i + 1, src, dst, now))

    try_match, delete_torrent, log_operation, get_config, send_notification, create_pending_action, logs = _deps()
    ok, _deleted, total_hits, _pending = scan_delete_rows(
        task,
        rows,
        try_match,
        delete_torrent,
        log_operation,
        get_config,
        send_notification,
        create_pending_action,
    )

    assert ok is True
    assert total_hits == 4
    assert sum(1 for r in rows if r.deleted_at is not None) == 2
    assert 'delete_guard_truncated' in logs


def test_scan_delete_rows_first_missing_sets_last_seen_without_deleting(tmp_path):
    task = _mk_task(tmp_path, cooldown=120, max_per_run=5)
    src = tmp_path / 'a.mkv'
    dst = tmp_path / 'b.mkv'
    src.write_text('x', encoding='utf-8')
    dst.write_text('x', encoding='utf-8')
    src.unlink()  # simulate source deleted
    row = _mk_row(1, src, dst, last_seen_at=None)

    try_match, delete_torrent, log_operation, get_config, send_notification, create_pending_action, _logs = _deps()
    ok, _deleted, total_hits, _pending = scan_delete_rows(
        task,
        [row],
        try_match,
        delete_torrent,
        log_operation,
        get_config,
        send_notification,
        create_pending_action,
    )

    assert ok is True
    assert total_hits == 0
    assert row.last_seen_at is not None
    assert row.deleted_at is None
