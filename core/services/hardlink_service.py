import os
import hashlib
from datetime import datetime, UTC
from pathlib import Path


def is_file_stable(file_path: Path, min_age_seconds: int):
    stat = file_path.stat()
    now = datetime.now(UTC).timestamp()
    return now - stat.st_mtime >= max(min_age_seconds, 0)


def file_key_of(file_path: Path):
    stat = file_path.stat()
    seed = f"{file_path.name}|{stat.st_size}|{int(stat.st_mtime)}"
    digest = hashlib.sha1(seed.encode()).hexdigest()
    inode = str(getattr(stat, 'st_ino', 0))
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    return digest, inode, stat.st_size, mtime


def create_hardlink_for_file(task, file_path, cache_model, map_model, db, safe_unlink):
    # When cache is enabled, treat source_path as processed until user clears cache manually.
    cache = cache_model.query.filter_by(source_path=str(file_path)).first() if task.use_cache else None
    if task.use_cache and cache:
        return False, '命中缓存，已跳过'

    exts = task.get_extensions_list()
    if file_path.suffix.lower() not in exts:
        return False, '扩展名不匹配'
    if file_path.suffix.lower() in task.get_exclude_extensions_list():
        return False, '命中扩展名黑名单'
    for ex_dir in task.get_exclude_dirs_list():
        if ex_dir in str(file_path.parent).lower():
            return False, '命中排除目录'
    if not is_file_stable(file_path, task.min_file_age_seconds or 0):
        return False, '文件还在写入中'

    dest_root = Path(task.dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    source_root = Path(task.source_dir)
    if task.create_folder:
        # If file is directly under source root, create a same-name folder in destination.
        folder_name = file_path.stem if file_path.parent == source_root else file_path.parent.name
        dest_dir = dest_root / folder_name
    else:
        dest_dir = dest_root
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / file_path.name

    if dest_file.exists():
        if dest_file.samefile(file_path):
            return False, '硬链接已存在'

        # Only replace destination files that are already managed by this app.
        managed = (
            cache_model.query.filter_by(dest_path=str(dest_file)).first() is not None
            or map_model.query.filter_by(dest_path=str(dest_file)).first() is not None
        )
        if not managed:
            return False, '目标存在同名文件且非系统托管，已跳过以防误删'

        safe_unlink(dest_file)

    os.link(file_path, dest_file)
    key, inode, fsize, mtime = file_key_of(file_path)

    if cache:
        cache.dest_path = str(dest_file)
    else:
        db.session.add(cache_model(source_path=str(file_path), dest_path=str(dest_file)))

    mapping = map_model.query.filter_by(source_path=str(file_path)).first()
    if not mapping:
        mapping = map_model(task_id=task.id, source_path=str(file_path), dest_path=str(dest_file))
        db.session.add(mapping)

    mapping.dest_path = str(dest_file)
    mapping.source_inode = inode
    mapping.file_size = fsize
    mapping.mtime = mtime
    mapping.file_key = key
    mapping.last_seen_at = datetime.now(UTC)
    mapping.deleted_at = None

    return True, f'{file_path} -> {dest_file}'
