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
    digest = hashlib.sha256(seed.encode()).hexdigest()
    inode = str(getattr(stat, 'st_ino', 0))
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    return digest, inode, stat.st_size, mtime


def create_hardlink_for_file(task, file_path, cache_model, map_model, db, safe_unlink):
    # Cache is keyed by source path; verify the existing link before treating it as processed.
    cache = cache_model.query.filter_by(source_path=str(file_path)).first() if task.use_cache else None
    if task.use_cache and cache:
        mapping = map_model.query.filter_by(source_path=str(file_path)).first()
        if mapping:
            try:
                dest_path = Path(mapping.dest_path) if mapping.dest_path else None
                if dest_path is None or not dest_path.exists():
                    return False, '命中缓存，已跳过'
                if dest_path.samefile(file_path):
                    return False, '命中缓存，已跳过'
            except Exception:
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

    existing_mapping = map_model.query.filter_by(source_path=str(file_path)).first()
    if existing_mapping and existing_mapping.task_id != task.id:
        return False, '源文件已被其他硬链接任务管理，已跳过'

    key, inode, fsize, mtime = file_key_of(file_path)

    def _persist_link(cache_obj, mapping_obj):
        if cache_obj:
            cache_obj.dest_path = str(dest_file)
        else:
            db.session.add(cache_model(source_path=str(file_path), dest_path=str(dest_file)))

        is_new_mapping = mapping_obj is None
        previous_inode = str(mapping_obj.source_inode) if mapping_obj and mapping_obj.source_inode else ''
        if is_new_mapping:
            mapping_obj = map_model(task_id=task.id, source_path=str(file_path), dest_path=str(dest_file))
            db.session.add(mapping_obj)

        mapping_obj.dest_path = str(dest_file)
        mapping_obj.source_inode = inode
        mapping_obj.file_size = fsize
        mapping_obj.mtime = mtime
        mapping_obj.file_key = key
        mapping_obj.last_seen_at = datetime.now(UTC)
        mapping_obj.deleted_at = None
        if is_new_mapping:
            mapping_obj.source_type = 'pending'
        elif previous_inode and previous_inode != str(inode):
            mapping_obj.source_type = 'pending'
            mapping_obj.torrent_hash = None
            mapping_obj.downloader_id = None
            mapping_obj.backfill_fail_count = 0
            mapping_obj.backfill_last_attempt_at = None
        elif not (mapping_obj.source_type or '').strip():
            mapping_obj.source_type = 'pending'
        return mapping_obj

    if dest_file.exists():
        if dest_file.samefile(file_path):
            _persist_link(cache, existing_mapping)
            return True, '硬链接已存在，已补录映射'

        # Only replace a destination that this task already owns; never overwrite another mapping's target.
        owned_cache = cache_model.query.filter_by(source_path=str(file_path)).first()
        managed = existing_mapping is not None and existing_mapping.dest_path == str(dest_file)
        if not managed and owned_cache is not None and owned_cache.dest_path == str(dest_file):
            managed = True
        occupies_other = (
            map_model.query.filter(map_model.dest_path == str(dest_file), map_model.source_path != str(file_path)).first() is not None
            or cache_model.query.filter(cache_model.dest_path == str(dest_file), cache_model.source_path != str(file_path)).first() is not None
        )
        if occupies_other and not managed:
            return False, '目标路径已被其他映射占用，已跳过以防误删'
        if not managed:
            return False, '目标存在同名文件且非系统托管，已跳过以防误删'

        safe_unlink(dest_file)

    os.link(file_path, dest_file)
    _persist_link(cache, existing_mapping)
    return True, f'{file_path} -> {dest_file}'
