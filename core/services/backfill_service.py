from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _norm(v):
    return str(v or '').strip().lower()


def _basename(v):
    return Path(str(v or '')).name.lower()




def _safe_normalize_path(normalize_path, value):
    if not value:
        return ''
    if not normalize_path:
        return _norm(value)
    try:
        return _norm(normalize_path(value))
    except Exception:
        return _norm(value)


def _score_candidate(file_name, source_parent, torrent, normalize_path=None):
    t_name = _norm(torrent.get('name', ''))
    save_path = _safe_normalize_path(normalize_path, torrent.get('save_path', ''))
    content_path = _safe_normalize_path(normalize_path, torrent.get('content_path', ''))

    name_hit = bool(file_name and file_name in t_name)
    path_hit = bool(source_parent and (source_parent in save_path or source_parent in content_path))

    score = 0
    if path_hit:
        score += 60
    if name_hit:
        score += 35

    # Tie-breaker: prefer more specific (deeper) path evidence.
    path_depth = 0
    if path_hit:
        path_depth = max(save_path.count('/'), content_path.count('/'))
        score += min(path_depth, 10)

    return score, name_hit, path_hit, path_depth


def _candidate_hashes(candidate_torrents):
    hashes = []
    for torrent in candidate_torrents or []:
        th = str(torrent.get('hash') or '').strip()
        if th:
            hashes.append(th)
    return hashes


def _prefetch_torrent_files(downloader, candidate_torrents, list_torrent_files, files_cache, max_workers=4):
    all_hashes = _candidate_hashes(candidate_torrents)
    missing = [th for th in all_hashes if th not in files_cache]
    if not missing:
        return {'requested': 0, 'cache_hits': len(all_hashes)}

    workers = max(1, min(int(max_workers or 1), len(missing)))
    if workers <= 1:
        for th in missing:
            files_cache[th] = list_torrent_files(downloader, th)
        return {'requested': len(missing), 'cache_hits': len(all_hashes) - len(missing)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(list_torrent_files, downloader, th): th for th in missing}
        for fut in as_completed(future_map):
            th = future_map[fut]
            try:
                files_cache[th] = fut.result()
            except Exception:
                files_cache[th] = None
    return {'requested': len(missing), 'cache_hits': len(all_hashes) - len(missing)}


def _match_by_filelist(row, downloader, candidate_torrents, list_torrent_files, files_cache=None, exact_cache=None):
    """Strong match: basename + file_size against qb torrent file list.

    Returns tuple: (hash or None, reason)
    """
    base = _basename(row.source_path) or _basename(row.dest_path)
    if not base or row.file_size in (None, 0):
        return None, 'missing_basename_or_size'

    if files_cache is None:
        files_cache = {}
    if exact_cache is None:
        exact_cache = {}

    key = (base, int(row.file_size or 0), tuple(sorted(str(t.get('hash') or '').strip() for t in candidate_torrents if str(t.get('hash') or '').strip())))
    if key in exact_cache:
        return exact_cache[key]

    winners = []
    for torrent in candidate_torrents:
        th = str(torrent.get('hash') or '').strip()
        if not th:
            continue
        if th in files_cache:
            files = files_cache[th]
        else:
            files = list_torrent_files(downloader, th)
            files_cache[th] = files
        if files is None:
            continue
        for f in files:
            fname = _basename(f.get('name') or f.get('path') or '')
            try:
                fsize = int(f.get('size') or 0)
            except Exception:
                fsize = 0
            if fname == base and fsize == int(row.file_size or 0):
                winners.append(th)
                break

    uniq = sorted(set(winners))
    if len(uniq) == 1:
        result = (uniq[0], 'filelist_exact')
        exact_cache[key] = result
        return result
    if len(uniq) > 1:
        result = (None, f'filelist_conflict:{len(uniq)}')
        exact_cache[key] = result
        return result
    result = (None, 'filelist_no_match')
    exact_cache[key] = result
    return result


def scan_backfill_rows(rows, downloader_resolver, list_torrents, list_torrent_files, log_operation, should_stop=None, max_failures=2, allow_global_fallback=True, max_candidates=120, file_fetch_workers=4, normalize_path=None):
    """Backfill torrent hash for mapping rows.

    Strategy:
    1) Source path as primary evidence. Destination path only as weak fallback.
    2) Strong match requires basename + file_size from qb /torrents/files.
    3) create_folder 产生的目标目录结构不作为强匹配依据，避免 a/a.mkv 误导。
    """
    matched = 0
    conflicts = 0
    skipped = 0
    total_candidates = 0
    total_filelist_requested = 0
    total_filelist_cache_hits = 0

    groups = {}
    for row in rows:
        groups.setdefault(row.downloader_id, []).append(row)

    for downloader_id, items in groups.items():
        files_cache = {}
        exact_cache = {}
        downloader = downloader_resolver(downloader_id)
        if not downloader:
            skipped += len(items)
            continue

        torrents = list_torrents(downloader)
        if torrents is None:
            skipped += len(items)
            continue

        for row in items:
            if should_stop and should_stop():
                log_operation('backfill_stopped', 'FileLinkMap', None, '映射回填', '收到停止指令，已中止本轮回填', False)
                total_rows = len(rows or [])
                avg_candidates = (total_candidates / max(1, total_rows)) if total_rows else 0
                log_operation('backfill_metrics', 'FileLinkMap', None, '映射回填', f'rows={total_rows};matched={matched};conflicts={conflicts};skipped={skipped};avg_candidates={avg_candidates:.2f};filelist_requested={total_filelist_requested};filelist_cache_hits={total_filelist_cache_hits}')
                return matched, conflicts, skipped

            src = Path(row.source_path or '')
            dst = Path(row.dest_path or '')

            use_src = src.exists() and src.is_file()
            use_dst = (not use_src) and dst.exists() and dst.is_file()
            if not use_src and not use_dst:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, row.source_path, 'source/dest 都不存在', False)
                continue

            probe = src if use_src else dst
            file_name = _norm(probe.name)
            source_parent = _safe_normalize_path(normalize_path, src.parent if use_src else '')

            candidates = []
            matched_hash = None
            reason = 'candidate_empty'
            for torrent in torrents:
                score, name_hit, path_hit, _depth = _score_candidate(
                    file_name,
                    source_parent,
                    torrent,
                    normalize_path=normalize_path,
                )
                if name_hit or path_hit:
                    t = dict(torrent)
                    t['_score'] = score
                    candidates.append(t)

            fallback_mode = False
            if not candidates:
                if allow_global_fallback:
                    fallback_mode = True
                    candidates = torrents
                else:
                    matched_hash = None
                    reason = 'candidate_empty_skip_fast'
                    candidates = []

            if candidates:
                candidates.sort(key=lambda t: (int(t.get('_score', 0)), str(t.get('hash') or '')), reverse=True)

            if candidates and max_candidates and len(candidates) > int(max_candidates):
                candidates = candidates[:int(max_candidates)]

            if candidates:
                total_candidates += len(candidates)
                fetch_stats = _prefetch_torrent_files(downloader, candidates, list_torrent_files, files_cache, max_workers=file_fetch_workers) or {}
                total_filelist_requested += int(fetch_stats.get('requested', 0) or 0)
                total_filelist_cache_hits += int(fetch_stats.get('cache_hits', 0) or 0)
                matched_hash, reason = _match_by_filelist(row, downloader, candidates, list_torrent_files, files_cache=files_cache, exact_cache=exact_cache)

            if fallback_mode and reason == 'filelist_no_match':
                reason = 'candidate_empty_fallback_no_match'
            if matched_hash:
                row.torrent_hash = matched_hash
                row.downloader_id = downloader.id
                row.source_type = 'downloader'
                row.backfill_fail_count = 0
                row.backfill_last_attempt_at = None
                matched += 1
                if fallback_mode and reason == 'filelist_exact':
                    reason = 'candidate_empty_fallback_matched'
                log_operation('backfill_matched', 'FileLinkMap', row.id, probe.name, f'hash={matched_hash};reason={reason}')
                continue

            fail_count = int(getattr(row, 'backfill_fail_count', 0) or 0) + 1
            row.backfill_fail_count = fail_count
            row.backfill_last_attempt_at = __import__('datetime').datetime.now(__import__('datetime').UTC)

            if fail_count > max_failures:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, probe.name, f'{reason};skip_permanent_after={max_failures}', False)
                continue

            if reason.startswith('filelist_conflict'):
                conflicts += 1
                log_operation('backfill_conflict', 'FileLinkMap', row.id, probe.name, reason, False)
            else:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, probe.name, reason, False)

    total_rows = len(rows or [])
    avg_candidates = (total_candidates / max(1, total_rows)) if total_rows else 0
    log_operation('backfill_metrics', 'FileLinkMap', None, '映射回填', f'rows={total_rows};matched={matched};conflicts={conflicts};skipped={skipped};avg_candidates={avg_candidates:.2f};filelist_requested={total_filelist_requested};filelist_cache_hits={total_filelist_cache_hits}')
    return matched, conflicts, skipped
