from __future__ import annotations

from pathlib import Path


def _safe_resolve(path_value: str | None):
    raw = str(path_value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except Exception:
        return Path(raw)


def run_preflight_checks(kind: str, task=None, get_config=None, db_path: str | None = None):
    kind = str(kind or '').strip()
    issues = []
    warnings = []
    details = {}

    if kind == 'hardlink' and task is not None:
        src = _safe_resolve(getattr(task, 'source_dir', ''))
        dst = _safe_resolve(getattr(task, 'dest_dir', ''))
        details['source_dir'] = str(src) if src else ''
        details['dest_dir'] = str(dst) if dst else ''
        if not src or not src.exists():
            issues.append('源目录不存在')
        if not dst or not dst.exists():
            issues.append('目标目录不存在')
        if src and dst and str(src) == str(dst):
            issues.append('源目录与目标目录不能相同')
        if getattr(task, 'delete_dry_run', False):
            warnings.append('当前任务启用了仅记录模式')

        if get_config is not None and str(get_config('security_read_only_enabled', 'false')).lower() == 'true':
            warnings.append('系统处于只读模式，写入操作将被拦截')

    elif kind == 'backup':
        db_file = _safe_resolve(db_path or 'hardlink_manager.db')
        details['db_path'] = str(db_file) if db_file else ''
        if not db_file or not db_file.exists():
            issues.append('数据库文件不存在')
        else:
            warnings.append(f'将备份到数据库同级或配置目录，当前库文件: {db_file.name}')

    elif kind == 'delete_pending_bulk':
        warnings.append('批量删种会逐项执行，不可撤销')
        if get_config is not None and str(get_config('security_read_only_enabled', 'false')).lower() == 'true':
            warnings.append('只读模式开启时，批量确认/驳回仍可能被拦截')

    return {
        'ok': not issues,
        'kind': kind,
        'issues': issues,
        'warnings': warnings,
        'details': details,
    }


def build_operation_preview(kind: str, task=None, get_config=None, db_path: str | None = None):
    report = run_preflight_checks(kind, task=task, get_config=get_config, db_path=db_path)
    preview = {'kind': report['kind'], 'summary': '', 'items': []}

    if kind == 'hardlink' and task is not None:
        preview['summary'] = f"任务 {getattr(task, 'name', '-')}: {getattr(task, 'source_dir', '')} -> {getattr(task, 'dest_dir', '')}"
        preview['items'] = [
            f"源目录: {getattr(task, 'source_dir', '')}",
            f"目标目录: {getattr(task, 'dest_dir', '')}",
            f"扩展名: {getattr(task, 'extensions', '')}",
            f"排除目录: {getattr(task, 'exclude_dirs', '')}",
            f"仅记录模式: {'是' if getattr(task, 'delete_dry_run', False) else '否'}",
        ]
    elif kind == 'backup':
        preview['summary'] = '数据库备份'
        preview['items'] = [
            f"数据库文件: {report['details'].get('db_path') or '-'}",
            '操作结果: 创建 SQLite 备份文件',
        ]
    elif kind == 'delete_pending_bulk':
        preview['summary'] = '批量处理待确认删种'
        preview['items'] = ['按选中项逐条执行确认或驳回', '该操作会逐条写入审计日志']

    return {**report, **preview}


def apply_bulk_decision(items, action: str, decide_func):
    action = str(action or '').strip()
    if action not in {'confirm', 'reject'}:
        return False, '无效的批量操作', 0, 0

    done = 0
    failed = 0
    for item in items or []:
        ok = bool(decide_func(item, action))
        if ok:
            done += 1
        else:
            failed += 1
    return failed == 0, f'批量处理完成：成功 {done}，失败 {failed}', done, failed
