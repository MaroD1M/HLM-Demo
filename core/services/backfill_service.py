from pathlib import Path


def _norm(v):
    return str(v or '').strip().lower()


def _basename(v):
    return Path(str(v or '')).name.lower()


def _match_by_filelist(row, downloader, candidate_torrents, list_torrent_files):
    """Strong match: basename + file_size against qb torrent file list.

    Returns tuple: (hash or None, reason)
    """
    base = _basename(row.source_path) or _basename(row.dest_path)
    if not base or row.file_size in (None, 0):
        return None, 'missing_basename_or_size'

    winners = []
    for torrent in candidate_torrents:
        th = str(torrent.get('hash') or '').strip()
        if not th:
            continue
        files = list_torrent_files(downloader, th)
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
        return uniq[0], 'filelist_exact'
    if len(uniq) > 1:
        return None, f'filelist_conflict:{len(uniq)}'
    return None, 'filelist_no_match'


def scan_backfill_rows(rows, downloader_resolver, list_torrents, list_torrent_files, log_operation):
    """Backfill torrent hash for mapping rows.

    Strategy:
    1) Source path as primary evidence. Destination path only as weak fallback.
    2) Strong match requires basename + file_size from qb /torrents/files.
    3) create_folder 产生的目标目录结构不作为强匹配依据，避免 a/a.mkv 误导。
    """
    matched = 0
    conflicts = 0
    skipped = 0

    groups = {}
    for row in rows:
        groups.setdefault(row.downloader_id, []).append(row)

    for downloader_id, items in groups.items():
        downloader = downloader_resolver(downloader_id)
        if not downloader:
            skipped += len(items)
            continue

        torrents = list_torrents(downloader)
        if torrents is None:
            skipped += len(items)
            continue

        for row in items:
            src = Path(row.source_path or '')
            dst = Path(row.dest_path or '')

            # Source-first: if source exists, do not let destination directory shape dominate.
            use_src = src.exists() and src.is_file()
            use_dst = (not use_src) and dst.exists() and dst.is_file()
            if not use_src and not use_dst:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, row.source_path, 'source/dest 都不存在', False)
                continue

            probe = src if use_src else dst
            file_name = _norm(probe.name)
            source_parent = _norm(src.parent if use_src else '')

            # First-pass candidate narrowing (cheap)
            candidates = []
            for torrent in torrents:
                t_name = _norm(torrent.get('name', ''))
                save_path = _norm(torrent.get('save_path', ''))
                content_path = _norm(torrent.get('content_path', ''))

                # Primary candidate condition: basename appears in torrent name.
                # Secondary path hint only uses source parent (if source exists).
                name_hit = bool(file_name and file_name in t_name)
                path_hit = bool(source_parent and (source_parent in save_path or source_parent in content_path))
                if name_hit or path_hit:
                    candidates.append(torrent)

            if not candidates:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, probe.name, '候选种子为空', False)
                continue

            # Strong decision by torrent file list exactness.
            matched_hash, reason = _match_by_filelist(row, downloader, candidates, list_torrent_files)
            if matched_hash:
                row.torrent_hash = matched_hash
                row.downloader_id = downloader.id
                row.source_type = 'downloader'
                matched += 1
                log_operation('backfill_matched', 'FileLinkMap', row.id, probe.name, f'hash={matched_hash};reason={reason}')
                continue

            if reason.startswith('filelist_conflict'):
                conflicts += 1
                log_operation('backfill_conflict', 'FileLinkMap', row.id, probe.name, reason, False)
            else:
                skipped += 1
                log_operation('backfill_skipped', 'FileLinkMap', row.id, probe.name, reason, False)

    return matched, conflicts, skipped
