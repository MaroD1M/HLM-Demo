from types import SimpleNamespace

from core.services.backfill_service import scan_backfill_rows


class Row:
    def __init__(self, row_id, source_path, dest_path, file_size, downloader_id=None):
        self.id = row_id
        self.source_path = source_path
        self.dest_path = dest_path
        self.file_size = file_size
        self.downloader_id = downloader_id
        self.torrent_hash = None
        self.source_type = 'manual'


def test_backfill_prefers_source_and_ignores_create_folder_shape(tmp_path):
    src_dir = tmp_path / 'src'
    dst_dir = tmp_path / 'dst' / 'a'
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    src = src_dir / 'a.mkv'
    dst = dst_dir / 'a.mkv'  # create_folder=true style
    data = b'x' * 1234
    src.write_bytes(data)
    dst.write_bytes(data)

    row = Row(1, str(src), str(dst), len(data), downloader_id=1)
    dl = SimpleNamespace(id=1, enabled=True, type='qbittorrent')

    torrents = [
        {'hash': 'goodhash', 'name': 'a.mkv', 'save_path': str(src_dir), 'content_path': str(src_dir / 'a.mkv')},
    ]

    def downloader_resolver(_):
        return dl

    def list_torrents(_):
        return torrents

    def list_torrent_files(_, th):
        if th == 'goodhash':
            return [{'name': 'a.mkv', 'size': len(data)}]
        return []

    logs = []

    def log_operation(*args):
        logs.append(args)

    matched, conflicts, skipped = scan_backfill_rows(
        [row], downloader_resolver, list_torrents, list_torrent_files, log_operation
    )

    assert (matched, conflicts, skipped) == (1, 0, 0)
    assert row.torrent_hash == 'goodhash'
    assert row.source_type == 'downloader'
    assert any(item[0] == 'backfill_matched' for item in logs)


def test_backfill_conflict_when_multiple_hashes_match_same_file(tmp_path):
    src_dir = tmp_path / 'src'
    src_dir.mkdir(parents=True)
    src = src_dir / 'a.mkv'
    data = b'y' * 50
    src.write_bytes(data)

    row = Row(2, str(src), str(tmp_path / 'dst' / 'a.mkv'), len(data), downloader_id=1)
    dl = SimpleNamespace(id=1, enabled=True, type='qbittorrent')

    torrents = [
        {'hash': 'h1', 'name': 'a.mkv', 'save_path': str(src_dir), 'content_path': ''},
        {'hash': 'h2', 'name': 'a.mkv', 'save_path': str(src_dir), 'content_path': ''},
    ]

    def downloader_resolver(_):
        return dl

    def list_torrents(_):
        return torrents

    def list_torrent_files(_, th):
        return [{'name': 'a.mkv', 'size': len(data)}] if th in {'h1', 'h2'} else []

    logs = []

    def log_operation(*args):
        logs.append(args)

    matched, conflicts, skipped = scan_backfill_rows(
        [row], downloader_resolver, list_torrents, list_torrent_files, log_operation
    )

    assert (matched, conflicts, skipped) == (0, 1, 0)
    assert row.torrent_hash is None
    assert any(item[0] == 'backfill_conflict' for item in logs)


def test_backfill_path_mapping_improves_candidate_selection(tmp_path):
    src_dir = tmp_path / 'host' / 'downloads' / 'show'
    src_dir.mkdir(parents=True)
    src = src_dir / 'a.mkv'
    data = b'z' * 256
    src.write_bytes(data)

    row = Row(3, str(src), str(tmp_path / 'dst' / 'a.mkv'), len(data), downloader_id=1)
    dl = SimpleNamespace(id=1, enabled=True, type='qbittorrent')

    torrents = [
        {'hash': 'bad', 'name': 'a.mkv', 'save_path': '/other/path', 'content_path': '/other/path/a.mkv'},
        {'hash': 'good', 'name': 'a.mkv', 'save_path': '/mnt/media/downloads/show', 'content_path': '/mnt/media/downloads/show/a.mkv'},
    ]

    def downloader_resolver(_):
        return dl

    def list_torrents(_):
        return torrents

    calls = []

    def list_torrent_files(_, th):
        calls.append(th)
        if th == 'good':
            return [{'name': 'a.mkv', 'size': len(data)}]
        return []

    logs = []

    def log_operation(*args):
        logs.append(args)

    def normalize_path(v):
        text = str(v or '').replace('\\\\', '/').replace('\\', '/')
        low = text.lower()
        prefix = str(tmp_path / 'host').replace('\\\\', '/').replace('\\', '/').lower()
        if low.startswith(prefix):
            return '/mnt/media' + text[len(prefix):]
        return text

    matched, conflicts, skipped = scan_backfill_rows(
        [row], downloader_resolver, list_torrents, list_torrent_files, log_operation, normalize_path=normalize_path
    )

    assert (matched, conflicts, skipped) == (1, 0, 0)
    assert row.torrent_hash == 'good'
    assert calls[0] == 'good'
    assert any(item[0] == 'backfill_matched' for item in logs)
