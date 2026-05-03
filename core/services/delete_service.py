from datetime import datetime, UTC
from pathlib import Path


def scan_delete_rows(task, rows, try_match, delete_torrent, log_operation, get_config, send_notification):
    now = datetime.now(UTC)
    hits = []

    for row in rows:
        src_exists = Path(row.source_path).exists()
        dst_exists = Path(row.dest_path).exists()
        if src_exists or dst_exists:
            row.last_seen_at = now
            continue

        if (now - (row.last_seen_at or now)).total_seconds() < task.cooldown_seconds:
            continue

        row.deleted_at = now
        hits.append(row)

    if len(hits) > task.max_deletes_per_run:
        log_operation('delete_guard_blocked', 'DeleteMonitorTask', task.id, task.name, f'本轮命中 {len(hits)} 超过阈值 {task.max_deletes_per_run}', False)
        return False, 0, len(hits)

    deleted_torrents = 0
    for row in hits:
        deleted_path = Path(row.source_path)
        if not task.downloader:
            log_operation('delete_detected_no_downloader', 'DeleteMonitorTask', task.id, task.name, f'检测到删除: {row.source_path}')
            continue

        torrent_hash, match_by = try_match(deleted_path, task.downloader)
        if not torrent_hash:
            log_operation('torrent_match_miss', 'DeleteMonitorTask', task.id, task.name, f'未匹配到种子: {row.source_path}')
            continue

        if task.dry_run:
            log_operation('torrent_delete_dry_run', 'DeleteMonitorTask', task.id, task.name, f'dry-run 删除 {torrent_hash}, by={match_by}')
            continue

        ok = delete_torrent(task.downloader, torrent_hash)
        if ok:
            row.torrent_hash = torrent_hash
            deleted_torrents += 1
            log_operation('torrent_deleted', 'DeleteMonitorTask', task.id, task.name, f'删除种子 {torrent_hash}, by={match_by}, file={row.source_path}')
            if get_config('notify_on_delete', 'true') == 'true' and task.notifier:
                send_notification(task.notifier, f'删除联动成功\n任务: {task.name}\n种子: {torrent_hash}\n匹配: {match_by}')
        else:
            log_operation('torrent_delete_failed', 'DeleteMonitorTask', task.id, task.name, f'删除失败 {torrent_hash}', False)

    return True, deleted_torrents, len(hits)
