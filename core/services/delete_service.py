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


def _as_aware_utc(dt_obj):
    if not dt_obj:
        return None
    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=UTC)
    return dt_obj.astimezone(UTC)


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


def _safe_unlink_file(path_text):
    try:
        p = Path(path_text)
        if p.exists() and p.is_file():
            p.unlink()
            return True
    except Exception:
        return False
    return False


def _policy_enabled(get_config, key, default='false'):
    return str(get_config(key, default)).lower() == 'true'


def _notify_enabled(get_config, source_type):
    # global switch first, then source-specific switch.
    if not _policy_enabled(get_config, 'notify_on_delete', 'true'):
        return False
    if source_type == 'manual':
        return _policy_enabled(get_config, 'manual_notify_on_delete', 'true')
    return _policy_enabled(get_config, 'downloader_notify_on_delete', 'true')


def _task_notify_on_delete_enabled(task, get_config, source_type):
    task_switch = getattr(task, 'notify_on_delete', True)
    return bool(task_switch) and _notify_enabled(get_config, source_type)


def _task_notify_on_risky_enabled(task, get_config):
    global_switch = _policy_enabled(get_config, 'notify_on_risky_delete', 'true')
    task_switch = bool(getattr(task, 'notify_on_risky_delete', True))
    return global_switch and task_switch


def scan_delete_rows(task, rows, try_match, delete_torrent, log_operation, get_config, send_notification, create_pending_action, should_stop=None):
    now = datetime.now(UTC)
    hits = []
    monitor_root = (task.directory or '').rstrip('/')

    for row in rows:
        if should_stop and should_stop():
            log_operation('delete_scan_stopped', 'DeleteMonitorTask', task.id, task.name, '收到停止指令，已中止本轮删除联动', False)
            break
        deleted_path = _resolve_deleted_path(row, monitor_root)
        if deleted_path is None:
            continue
        if deleted_path == '':
            row.last_seen_at = now
            continue

        last_seen_at = _as_aware_utc(row.last_seen_at) or now
        if (now - last_seen_at).total_seconds() < task.cooldown_seconds:
            continue

        row.deleted_at = now
        hits.append((row, deleted_path))

    if len(hits) > task.max_deletes_per_run:
        log_operation('delete_guard_blocked', 'DeleteMonitorTask', task.id, task.name, f'本轮命中 {len(hits)} 超过阈值 {task.max_deletes_per_run}', False)
        return False, 0, len(hits), 0

    deleted_torrents = 0
    pending_count = 0
    strict_mode = _policy_enabled(get_config, 'delete_match_strict_mode', 'true')
    notify_risky = _task_notify_on_risky_enabled(task, get_config)

    for row, deleted_path_str in hits:
        if should_stop and should_stop():
            log_operation('delete_scan_stopped', 'DeleteMonitorTask', task.id, task.name, '收到停止指令，已中止本轮删除联动', False)
            break
        src = str(row.source_path or '')
        dst = str(row.dest_path or '')
        source_type = (row.source_type or 'manual').strip().lower()
        if source_type not in {'manual', 'downloader'}:
            source_type = 'manual'
        # 历史数据兜底：已有下载器关联信息时，按下载器来源处理。
        if source_type == 'manual' and (((row.torrent_hash or '').strip()) or row.downloader_id):
            source_type = 'downloader'

        # Determine which side was deleted and optionally remove the counterpart file.
        deleted_is_source = bool(src and deleted_path_str == src)
        if deleted_is_source:
            counterpart = dst
            policy_key = 'manual_source_delete_delete_dest' if source_type == 'manual' else 'downloader_source_delete_delete_dest'
            action_label = 'source_deleted'
        else:
            counterpart = src
            policy_key = 'manual_dest_delete_delete_source' if source_type == 'manual' else 'downloader_dest_delete_delete_source'
            action_label = 'dest_deleted'

        counterpart_removed = None
        if counterpart and _policy_enabled(get_config, policy_key, 'true'):
            counterpart_removed = _safe_unlink_file(counterpart)
            if counterpart_removed:
                log_operation('linked_file_deleted', 'FileLinkMap', row.id, task.name, f'{action_label} -> delete_counterpart: {counterpart}')
            else:
                log_operation('linked_file_delete_skip', 'FileLinkMap', row.id, task.name, f'{action_label} -> counterpart_missing_or_failed: {counterpart}')

        # Manual source: never delete torrent task, but should still notify if enabled.
        if source_type == 'manual':
            log_operation('delete_detected_manual', 'DeleteMonitorTask', task.id, task.name, f'手动来源删除: {deleted_path_str}')
            if task.notifier and _task_notify_on_delete_enabled(task, get_config, source_type):
                msg = f'📌 删除联动提醒（手动来源）\n任务：{task.name}\n触发路径：{deleted_path_str}\n对侧处理：'
                if counterpart:
                    msg += f"已删除({counterpart})" if counterpart_removed else f"未删除/不存在({counterpart})"
                else:
                    msg += '无'
                send_notification(task.notifier, msg)
            continue

        # Downloader source: optional torrent deletion.
        delete_torrent_key = 'downloader_source_delete_delete_torrent' if deleted_is_source else 'downloader_dest_delete_delete_torrent'
        if not _policy_enabled(get_config, delete_torrent_key, 'true'):
            log_operation('torrent_delete_disabled', 'DeleteMonitorTask', task.id, task.name, f'策略已禁用删种: {deleted_path_str}')
            continue

        if not task.downloader:
            log_operation('delete_detected_no_downloader', 'DeleteMonitorTask', task.id, task.name, f'检测到下载器来源删除但任务未关联下载器: {deleted_path_str}', False)
            continue

        torrent_hash = (row.torrent_hash or '').strip()
        match_by = 'mapping_hash'
        if not torrent_hash:
            torrent_hash, match_by = try_match(Path(deleted_path_str), task.downloader)

        if not torrent_hash:
            log_operation('torrent_match_miss', 'DeleteMonitorTask', task.id, task.name, f'未匹配到种子: {deleted_path_str}')
            continue

        risky_match = match_by in {'name_match', 'path_match'}
        if strict_mode and risky_match:
            pending_count += 1
            create_pending_action(task, row, deleted_path_str, torrent_hash, match_by, '疑似误删风险，需人工确认')
            log_operation('torrent_delete_pending', 'DeleteMonitorTask', task.id, task.name, f'已加入待确认: {torrent_hash}, by={match_by}, file={deleted_path_str}', False)
            if notify_risky and task.notifier:
                send_notification(task.notifier, f'⚠️ 疑似误删，已进入待确认\n任务：{task.name}\n匹配方式：{match_by}\n路径：{deleted_path_str}\n种子：{torrent_hash}')
            continue

        if task.dry_run:
            log_operation('torrent_delete_dry_run', 'DeleteMonitorTask', task.id, task.name, f'dry-run 删除 {torrent_hash}, by={match_by}')
            continue

        ok = delete_torrent(task.downloader, torrent_hash)
        if ok:
            row.torrent_hash = torrent_hash
            deleted_torrents += 1
            log_operation('torrent_deleted', 'DeleteMonitorTask', task.id, task.name, f'删除种子 {torrent_hash}, by={match_by}, file={deleted_path_str}')
            if task.notifier and _task_notify_on_delete_enabled(task, get_config, source_type):
                send_notification(task.notifier, f'✅ 联动完成（下载器来源）\n任务：{task.name}\n种子：{torrent_hash}\n匹配方式：{match_by}\n触发路径：{deleted_path_str}')
        else:
            log_operation('torrent_delete_failed', 'DeleteMonitorTask', task.id, task.name, f'删除失败 {torrent_hash}', False)

    return True, deleted_torrents, len(hits), pending_count
