from datetime import datetime, UTC
from pathlib import Path
import os


def _within(path_text, root_text):
    try:
        p = str(Path(path_text).resolve(strict=False))
        r = str(Path(root_text).resolve(strict=False))
        return os.path.commonpath([p, r]) == r
    except Exception:
        return False


def _resolve_deleted_path(row, monitor_root):
    src_path = str(row.source_path or '')
    dst_path = str(row.dest_path or '')
    src_exists = Path(src_path).exists()
    dst_exists = Path(dst_path).exists()

    watches_src = bool(monitor_root) and _within(src_path, monitor_root)
    watches_dst = bool(monitor_root) and _within(dst_path, monitor_root)
    if not watches_src and not watches_dst:
        return None

    if watches_src and not src_exists:
        return src_path
    if watches_dst and not dst_exists:
        return dst_path
    return ''


def scan_delete_rows(task, rows, try_match, delete_torrent, log_operation, get_config, send_notification, create_pending_action):
    now = datetime.now(UTC)
    hits = []
    monitor_root = (task.directory or '').rstrip('/')

    for row in rows:
        deleted_path = _resolve_deleted_path(row, monitor_root)
        if deleted_path is None:
            continue
        if deleted_path == '':
            row.last_seen_at = now
            continue

        if (now - (row.last_seen_at or now)).total_seconds() < task.cooldown_seconds:
            continue

        row.deleted_at = now
        hits.append((row, deleted_path))

    if len(hits) > task.max_deletes_per_run:
        log_operation('delete_guard_blocked', 'DeleteMonitorTask', task.id, task.name, f'本轮命中 {len(hits)} 超过阈值 {task.max_deletes_per_run}', False)
        return False, 0, len(hits), 0

    deleted_torrents = 0
    pending_count = 0
    strict_mode = get_config('delete_match_strict_mode', 'true') == 'true'
    notify_risky = get_config('notify_on_risky_delete', 'true') == 'true'

    for row, deleted_path_str in hits:
        deleted_path = Path(deleted_path_str)
        if not task.downloader:
            log_operation('delete_detected_no_downloader', 'DeleteMonitorTask', task.id, task.name, f'检测到删除: {deleted_path_str}')
            continue

        torrent_hash, match_by = try_match(deleted_path, task.downloader)
        if not torrent_hash:
            log_operation('torrent_match_miss', 'DeleteMonitorTask', task.id, task.name, f'未匹配到种子: {deleted_path_str}')
            continue

        risky_match = match_by in {'name_match', 'path_match'}
        if strict_mode and risky_match:
            pending_count += 1
            create_pending_action(task, row, deleted_path_str, torrent_hash, match_by, '疑似误删风险，需人工确认')
            log_operation('torrent_delete_pending', 'DeleteMonitorTask', task.id, task.name, f'已加入待确认: {torrent_hash}, by={match_by}, file={deleted_path_str}', False)
            if notify_risky and task.notifier:
                send_notification(task.notifier, f'疑似误删风险，已转人工确认\n任务: {task.name}\n匹配: {match_by}\n路径: {deleted_path_str}\n种子: {torrent_hash}')
            continue

        if task.dry_run:
            log_operation('torrent_delete_dry_run', 'DeleteMonitorTask', task.id, task.name, f'dry-run 删除 {torrent_hash}, by={match_by}')
            continue

        ok = delete_torrent(task.downloader, torrent_hash)
        if ok:
            row.torrent_hash = torrent_hash
            deleted_torrents += 1
            log_operation('torrent_deleted', 'DeleteMonitorTask', task.id, task.name, f'删除种子 {torrent_hash}, by={match_by}, file={deleted_path_str}')
            if get_config('notify_on_delete', 'true') == 'true' and task.notifier:
                send_notification(task.notifier, f'删除联动成功\n任务: {task.name}\n种子: {torrent_hash}\n匹配: {match_by}')
        else:
            log_operation('torrent_delete_failed', 'DeleteMonitorTask', task.id, task.name, f'删除失败 {torrent_hash}', False)

    return True, deleted_torrents, len(hits), pending_count
