from datetime import datetime, UTC
from pathlib import Path
import sqlite3


def run_sqlite_backup(db_path: str, backup_dir: str, keep_last: int = 7):
    src = Path(db_path)
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists() or not src.is_file():
        return False, f'数据库文件不存在: {src}', None

    ts = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    backup_file = target_dir / f'hardlink_manager-{ts}.db'

    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(backup_file))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    if keep_last > 0:
        backups = sorted(target_dir.glob('hardlink_manager-*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[keep_last:]:
            old.unlink(missing_ok=True)

    return True, f'备份完成: {backup_file.name}', str(backup_file)
