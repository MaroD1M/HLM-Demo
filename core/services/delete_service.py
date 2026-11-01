"""Deletion linkage, cooldown, cross-seed protection, and recycle decisions."""

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


def _prune_empty_dirs_from(file_path, roots):
    try:
        current = Path(file_path).parent
        resolved_roots = []
        for root in roots or []:
            if not str(root or '').strip():
                continue
            try:
                resolved_roots.append(Path(root).resolve(strict=False))
            except Exception:
                continue
        while True:
            try:
                current_resolved = current.resolve(strict=False)
                matched_root = None
                for root in resolved_roots:
                    if os.path.commonpath([str(current_resolved), str(root)]) == str(root):
                        matched_root = root
                        break
                if matched_root is None or current_resolved == matched_root:
                    break
                if any(current.iterdir()):
                    break
                current.rmdir()
                current = current.parent
            except Exception:
                break
    except Exception:
        return


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


def scan_delete_rows(task, rows, try_match, delete_torrent, log_operation, get_config, send_notification, create_pending_action, should_stop=None, find_cross_seed=None, recycle_file=None):
    """Process deletion-linkage rows and return (ok, deleted, hits, pending)."""
    now = datetime.now(UTC)
    candidates = []
    monitor_root = (task.directory or '').rstrip('/') or '/'

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

        last_seen_at = _as_aware_utc(row.last_seen_at)
        if last_seen_at is None:
            # First missing detection starts cooldown window now instead of skipping forever.
            row.last_seen_at = now
            continue
        if (now - last_seen_at).total_seconds() < task.cooldown_seconds:
            continue

        candidates.append((row, deleted_path))

    total_hits = len(candidates)
    if total_hits > task.max_deletes_per_run:
        log_operation('delete_guard_truncated', 'DeleteMonitorTask', task.id, task.name, f'本轮命中 {total_hits}，按阈值分批执行前 {task.max_deletes_per_run} 条，其余待下轮处理', True)
    hits = candidates[:task.max_deletes_per_run]

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
        raw_source_type = (row.source_type or '').strip().lower()
        has_hash = bool((row.torrent_hash or '').strip())
        has_downloader = bool(row.downloader_id)
        if raw_source_type == 'downloader' or (has_hash and has_downloader):
            source_type = 'downloader'
        elif raw_source_type == 'pending':
            source_type = 'pending'
            row.last_seen_at = now
            log_operation('delete_pending_source', 'FileLinkMap', row.id, task.name, f'来源待判定，暂不执行删种: path={deleted_path_str}', False)
        else:
            source_type = 'manual'

        # Determine which side was deleted and optionally remove the counterpart file.
        deleted_is_source = bool(src and deleted_path_str == src)
        policy_source_type = 'manual' if source_type == 'pending' else source_type
        if deleted_is_source:
            counterpart = dst
            policy_key = 'manual_source_delete_delete_dest' if policy_source_type == 'manual' else 'downloader_source_delete_delete_dest'
            action_label = 'source_deleted'
        else:
            counterpart = src
            policy_key = 'manual_dest_delete_delete_source' if policy_source_type == 'manual' else 'downloader_dest_delete_delete_source'
            action_label = 'dest_deleted'

        if task.dry_run:
            row.last_seen_at = now
            log_operation(
                'delete_dry_run',
                'DeleteMonitorTask',
                task.id,
                task.name,
                f'dry-run 未执行删除: {action_label}, source_type={source_type}, path={deleted_path_str}, counterpart={counterpart or "-"}',
                True,
            )
            continue

        counterpart_removed = None
        counterpart_recycled = None
        counterpart_roots = getattr(task, 'prune_roots', None)
        if counterpart and counterpart_roots and not any(_within(counterpart, root) for root in counterpart_roots if str(root or '').strip()):
            log_operation('linked_file_delete_skip', 'FileLinkMap', row.id, task.name, f'{action_label} -> counterpart_outside_roots: {counterpart}', False)
            counterpart = None
        if counterpart and _policy_enabled(get_config, policy_key, 'true'):
            if recycle_file and _policy_enabled(get_config, 'delete_recycle_bin_enabled', 'false'):
                counterpart_removed, counterpart_recycled = recycle_file(counterpart)
                if counterpart_removed:
                    log_operation('linked_file_recycled', 'FileLinkMap', row.id, task.name, f'{action_label} -> recycle_counterpart: {counterpart} -> {counterpart_recycled}')
                else:
                    log_operation('linked_file_delete_skip', 'FileLinkMap', row.id, task.name, f'{action_label} -> recycle_failed: {counterpart}')
            else:
                counterpart_removed = _safe_unlink_file(counterpart)
                if counterpart_removed:
                    log_operation('linked_file_deleted', 'FileLinkMap', row.id, task.name, f'{action_label} -> delete_counterpart: {counterpart}')
                else:
                    log_operation('linked_file_delete_skip', 'FileLinkMap', row.id, task.name, f'{action_label} -> counterpart_missing_or_failed: {counterpart}')
        if counterpart:
            prune_roots = getattr(task, 'prune_roots', None) or ([monitor_root] if monitor_root else [])
            _prune_empty_dirs_from(counterpart, prune_roots)

        # Pending/manual sources never auto-delete torrent; pending stays conservative until resolved.
        if source_type in {'manual', 'pending'}:
            row.deleted_at = now
            log_operation('delete_detected_manual', 'DeleteMonitorTask', task.id, task.name, f'手动来源删除: {deleted_path_str}')
            if task.notifier and _task_notify_on_delete_enabled(task, get_config, source_type):
                msg = f'📌 删除联动提醒（手动来源）\n任务：{task.name}\n触发路径：{deleted_path_str}\n对侧处理：'
                if counterpart:
                    if counterpart_recycled:
                        msg += f"已移入回收站({counterpart_recycled})"
                    else:
                        msg += f"已删除({counterpart})" if counterpart_removed else f"未删除/不存在({counterpart})"
                else:
                    msg += '无'
                send_notification(task.notifier, msg)
            continue

        # Downloader source: optional torrent deletion.
        delete_torrent_key = 'downloader_source_delete_delete_torrent' if deleted_is_source else 'downloader_dest_delete_delete_torrent'
        if not _policy_enabled(get_config, delete_torrent_key, 'true'):
            row.deleted_at = now
            log_operation('torrent_delete_disabled', 'DeleteMonitorTask', task.id, task.name, f'策略已禁用删种: {deleted_path_str}')
            continue

        if not task.downloader:
            row.last_seen_at = now
            log_operation('delete_detected_no_downloader', 'DeleteMonitorTask', task.id, task.name, f'检测到下载器来源删除但任务未关联下载器: {deleted_path_str}', False)
            continue

        torrent_hash = (row.torrent_hash or '').strip()
        match_by = 'mapping_hash'
        if not torrent_hash:
            torrent_hash, match_by = try_match(Path(deleted_path_str), task.downloader)

        if not torrent_hash:
            row.last_seen_at = now
            log_operation('torrent_match_miss', 'DeleteMonitorTask', task.id, task.name, f'未匹配到种子: {deleted_path_str}')
            continue

        if find_cross_seed and _policy_enabled(get_config, 'delete_protect_cross_seed', 'true'):
            related = find_cross_seed(task.downloader, torrent_hash) or []
            if related:
                row.deleted_at = now
                pending_count += 1
                sample = ', '.join(str(item.get('name') or item.get('hash') or '-') for item in related[:3])
                create_pending_action(task, row, deleted_path_str, torrent_hash, match_by, f'检测到疑似 cross-seed 关联: {sample}')
                log_operation('torrent_delete_pending', 'DeleteMonitorTask', task.id, task.name, f'cross-seed 保护，已加入待确认: {torrent_hash}, related={sample}', False)
                if notify_risky and task.notifier:
                    send_notification(task.notifier, f'⚠️ 检测到 cross-seed，已进入待确认\n任务：{task.name}\n种子：{torrent_hash}\n关联：{sample}\n路径：{deleted_path_str}')
                continue

        risky_match = match_by in {'name_match', 'path_match'}
        if strict_mode and risky_match:
            row.deleted_at = now
            pending_count += 1
            create_pending_action(task, row, deleted_path_str, torrent_hash, match_by, '疑似误删风险，需人工确认')
            log_operation('torrent_delete_pending', 'DeleteMonitorTask', task.id, task.name, f'已加入待确认: {torrent_hash}, by={match_by}, file={deleted_path_str}', False)
            if notify_risky and task.notifier:
                send_notification(task.notifier, f'⚠️ 疑似误删，已进入待确认\n任务：{task.name}\n匹配方式：{match_by}\n路径：{deleted_path_str}\n种子：{torrent_hash}')
            continue

        ok = delete_torrent(task.downloader, torrent_hash)
        if ok:
            row.torrent_hash = torrent_hash
            row.deleted_at = now
            deleted_torrents += 1
            log_operation('torrent_deleted', 'DeleteMonitorTask', task.id, task.name, f'删除种子 {torrent_hash}, by={match_by}, file={deleted_path_str}')
            if task.notifier and _task_notify_on_delete_enabled(task, get_config, source_type):
                send_notification(task.notifier, f'✅ 联动完成（下载器来源）\n任务：{task.name}\n种子：{torrent_hash}\n匹配方式：{match_by}\n触发路径：{deleted_path_str}')
        else:
            row.last_seen_at = now
            log_operation('torrent_delete_failed', 'DeleteMonitorTask', task.id, task.name, f'删除失败 {torrent_hash}', False)

    return True, deleted_torrents, total_hits, pending_count
