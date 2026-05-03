from pathlib import Path


def scan_backfill_rows(rows, downloader_resolver, list_torrents, log_operation):
    """Pure service: backfill torrent hash for mapping rows."""
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
            src = Path(row.source_path)
            dst = Path(row.dest_path)
            probe = src if src.exists() else dst
            if not probe.exists() or not probe.is_file():
                skipped += 1
                continue

            candidates = []
            file_name = probe.name.lower()
            file_parent = str(probe.parent).lower()
            for torrent in torrents:
                torrent_name = str(torrent.get('name', '')).lower()
                save_path = str(torrent.get('save_path', '')).lower()
                content_path = str(torrent.get('content_path', '')).lower()
                if file_name and file_name in torrent_name and (file_parent in save_path or file_parent in content_path):
                    candidates.append(torrent)

            if len(candidates) == 1:
                row.torrent_hash = candidates[0].get('hash')
                row.downloader_id = downloader.id
                matched += 1
                log_operation('backfill_matched', 'FileLinkMap', row.id, probe.name, f'hash={row.torrent_hash}')
            elif len(candidates) > 1:
                conflicts += 1
                log_operation('backfill_conflict', 'FileLinkMap', row.id, probe.name, f'candidates={len(candidates)}', False)
            else:
                skipped += 1

    return matched, conflicts, skipped
