from __future__ import annotations

from datetime import datetime, UTC
from hashlib import sha256
from pathlib import Path
import json
import shutil
import sqlite3


BACKUP_PREFIX = 'hardlink_manager-'


def _backup_manifest_path(backup_file: Path):
    return backup_file.with_suffix(backup_file.suffix + '.json')


def _checksum_file(path_obj: Path):
    h = sha256()
    with path_obj.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build_backup_manifest(db_path: str, backup_file: Path, keep_last: int):
    src = Path(db_path)
    try:
        size = backup_file.stat().st_size
    except Exception:
        size = 0
    return {
        '__meta': {
            'format_version': 1,
            'kind': 'sqlite_backup',
            'created_at': datetime.now(UTC).isoformat(),
        },
        'backup': {
            'source_path': str(src),
            'backup_path': str(backup_file),
            'backup_name': backup_file.name,
            'backup_size': size,
            'backup_sha256': _checksum_file(backup_file),
            'keep_last': int(max(1, keep_last)),
        },
    }


def verify_backup_integrity(backup_file: str | Path):
    path_obj = Path(backup_file)
    manifest_path = _backup_manifest_path(path_obj)
    if not path_obj.exists() or not path_obj.is_file():
        return False, f'备份文件不存在: {path_obj}'
    if not manifest_path.exists() or not manifest_path.is_file():
        return False, '备份清单缺失'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8') or '{}')
    except Exception:
        return False, '备份清单格式错误'
    backup = manifest.get('backup') if isinstance(manifest, dict) else None
    if not isinstance(backup, dict):
        return False, '备份清单缺少 backup 段'
    recorded = str(backup.get('backup_sha256') or '').strip()
    if not recorded:
        return False, '备份清单缺少校验值'
    actual = _checksum_file(path_obj)
    if actual != recorded:
        return False, '备份校验失败'
    return True, '备份校验通过'


def run_sqlite_backup(db_path: str, backup_dir: str, keep_last: int = 7):
    src = Path(db_path)
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists() or not src.is_file():
        return False, f'数据库文件不存在: {src}', None

    ts = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    backup_file = target_dir / f'{BACKUP_PREFIX}{ts}.db'

    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(backup_file))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    manifest = build_backup_manifest(str(src), backup_file, keep_last)
    _backup_manifest_path(backup_file).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    if keep_last > 0:
        backups = sorted(target_dir.glob(f'{BACKUP_PREFIX}*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep_last:]:
            _backup_manifest_path(old).unlink(missing_ok=True)
            old.unlink(missing_ok=True)

    return True, f'备份完成: {backup_file.name}', str(backup_file)


def list_backup_files(backup_dir: str):
    root = Path(backup_dir)
    if not root.exists() or not root.is_dir():
        return []
    items = []
    for path_obj in sorted(root.glob(f'{BACKUP_PREFIX}*.db'), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest_path = _backup_manifest_path(path_obj)
        ok, msg = verify_backup_integrity(path_obj)
        is_legacy_without_manifest = (msg == '备份清单缺失')
        items.append({
            'path': str(path_obj),
            'name': path_obj.name,
            'size': path_obj.stat().st_size if path_obj.exists() else 0,
            'manifest': str(manifest_path) if manifest_path.exists() else '',
            'ok': ok,
            'message': msg,
            'legacy_without_manifest': is_legacy_without_manifest,
            'created_at': datetime.fromtimestamp(path_obj.stat().st_mtime, tz=UTC).isoformat() if path_obj.exists() else '',
        })
    return items


def restore_sqlite_backup(db_path: str, backup_file: str, *, create_fallback_backup: bool = True, fallback_backup_dir: str | None = None, keep_last: int = 7):
    src = Path(backup_file)
    dst = Path(db_path)
    ok, msg = verify_backup_integrity(src)
    if not ok:
        return False, f'无法恢复：{msg}'
    if not src.exists() or not src.is_file():
        return False, f'备份文件不存在: {src}'
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dst.with_suffix(dst.suffix + '.restore.tmp')
    try:
        if create_fallback_backup and dst.exists() and dst.is_file():
            fallback_dir = Path(fallback_backup_dir or (dst.parent / 'restore-preflight'))
            fallback_dir.mkdir(parents=True, exist_ok=True)
            run_sqlite_backup(str(dst), str(fallback_dir), keep_last=max(1, int(keep_last or 1)))
        shutil.copy2(src, temp_path)
        temp_path.replace(dst)
    finally:
        temp_path.unlink(missing_ok=True)
    return True, f'恢复完成: {src.name}'
