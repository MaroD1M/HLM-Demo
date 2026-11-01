"""Configuration registry, defaults, scopes, and validation."""

from __future__ import annotations

import re


SETTING_SPECS = {
    'log_retention_days': {'default': '30', 'desc': '日志保留天数', 'type': 'int', 'min': 1, 'scopes': ('log_cleanup',)},
    'auto_clean_logs': {'default': 'true', 'desc': '自动清理日志', 'type': 'bool', 'scopes': ('log_cleanup',)},
    'app_log_max_mb': {'default': '10', 'desc': '应用日志单文件大小上限（MB）', 'type': 'int', 'min': 1, 'scopes': ('log_cleanup',)},
    'app_log_backup_count': {'default': '5', 'desc': '应用日志滚动保留文件数', 'type': 'int', 'min': 1, 'scopes': ('log_cleanup',)},

    'default_extensions': {'default': '.mkv,.mp4,.avi,.mov,.wmv,.flv', 'desc': '默认文件扩展名', 'type': 'text', 'scopes': ('general_template',)},
    'default_exclude_dirs': {'default': 'sample,subs', 'desc': '默认排除目录', 'type': 'text', 'scopes': ('general_template',)},

    'delete_files_with_torrent': {'default': 'false', 'desc': '删除种子时同时删除文件', 'type': 'bool', 'scopes': ('delete_notify',)},
    'notify_on_delete': {'default': 'true', 'desc': '启用删除通知', 'type': 'bool', 'scopes': ('delete_notify',)},
    'notify_on_risky_delete': {'default': 'true', 'desc': '启用疑似误删通知', 'type': 'bool', 'scopes': ('delete_notify',)},
    'delete_match_strict_mode': {'default': 'true', 'desc': '删除联动仅精确匹配自动执行', 'type': 'bool', 'scopes': ('delete_notify',)},
    'delete_protect_cross_seed': {'default': 'true', 'desc': '删除种子前检测 cross-seed 并转待确认', 'type': 'bool', 'scopes': ('delete_notify',)},
    'delete_recycle_bin_enabled': {'default': 'false', 'desc': '删除对侧文件时移入回收站', 'type': 'bool', 'scopes': ('delete_notify',)},
    'delete_recycle_bin_dir': {'default': '/app/data/recycle', 'desc': '回收站目录', 'type': 'text', 'max_length': 500, 'scopes': ('delete_notify',)},
    'delete_recycle_retention_days': {'default': '30', 'desc': '回收站保留天数', 'type': 'int', 'min': 1, 'max': 3650, 'scopes': ('delete_notify',)},
    'pending_source_guard_enabled': {'default': 'true', 'desc': '删除联动来源待判定保护开关', 'type': 'bool', 'scopes': ('delete_notify',)},
    'pending_source_guard_seconds': {'default': '900', 'desc': '删除联动来源待判定窗口（秒）', 'type': 'int', 'min': 0, 'scopes': ('delete_notify',)},
    'pending_source_warn_threshold': {'default': '200', 'desc': '系统诊断：待判定映射告警阈值', 'type': 'int', 'min': 1, 'scopes': ('delete_notify',)},
    'pending_source_log_mode': {'default': 'aggregate', 'desc': '待判定来源日志模式', 'type': 'choice', 'choices': ('aggregate', 'detail'), 'scopes': ('delete_notify',)},
    'delete_anomaly_guard_enabled': {'default': 'true', 'desc': '删除联动批量异常熔断开关', 'type': 'bool', 'scopes': ('delete_notify',)},
    'delete_anomaly_min_candidates': {'default': '50', 'desc': '删除联动熔断最小命中数', 'type': 'int', 'min': 1, 'max': 100000, 'scopes': ('delete_notify',)},
    'delete_anomaly_max_ratio': {'default': '50', 'desc': '删除联动熔断缺失比例上限（百分比）', 'type': 'int', 'min': 1, 'max': 100, 'scopes': ('delete_notify',)},

    'manual_dest_delete_delete_source': {'default': 'true', 'desc': '手动来源：删除目标后自动删除源文件', 'type': 'bool', 'scopes': ('source_policy',)},
    'manual_source_delete_delete_dest': {'default': 'true', 'desc': '手动来源：删除源文件后自动删除目标文件', 'type': 'bool', 'scopes': ('source_policy',)},
    'downloader_dest_delete_delete_source': {'default': 'true', 'desc': '下载器来源：删除目标后自动删除源文件', 'type': 'bool', 'scopes': ('source_policy',)},
    'downloader_source_delete_delete_dest': {'default': 'true', 'desc': '下载器来源：删除源文件后自动删除目标文件', 'type': 'bool', 'scopes': ('source_policy',)},
    'downloader_dest_delete_delete_torrent': {'default': 'true', 'desc': '下载器来源：删除目标后自动删种', 'type': 'bool', 'scopes': ('source_policy',)},
    'downloader_source_delete_delete_torrent': {'default': 'true', 'desc': '下载器来源：删除源后自动删种', 'type': 'bool', 'scopes': ('source_policy',)},

    'allowed_roots': {'default': '', 'desc': '允许访问的路径根目录，逗号分隔', 'type': 'text', 'max_length': 1000, 'scopes': ('network_notify',)},
    'proxy_url': {'default': '', 'desc': '统一外网代理地址', 'type': 'url', 'scopes': ('network_notify',)},
    'tg_api_base': {'default': 'https://api.telegram.org', 'desc': 'Telegram API基础地址', 'type': 'url', 'scopes': ('network_notify',)},

    'backup_dir': {'default': '/app/data/backups', 'desc': '数据库备份目录', 'type': 'text', 'max_length': 500, 'scopes': ('backup',)},
    'backup_keep_last': {'default': '7', 'desc': '数据库备份保留数量', 'type': 'int', 'min': 1, 'scopes': ('backup',)},

    'github_version_check_enabled': {'default': 'true', 'desc': '启用GitHub版本检查', 'type': 'bool', 'scopes': ('update',)},
    'github_repo': {'default': 'marod1m/HLM-Demo', 'desc': 'GitHub仓库 owner/repo', 'type': 'repo', 'max_length': 200, 'scopes': ('update',)},
    'github_api_base': {'default': 'https://api.github.com', 'desc': 'GitHub API基础地址', 'type': 'url', 'scopes': ('update',)},
    'version_check_cache_minutes': {'default': '720', 'desc': '版本检查缓存分钟数', 'type': 'int', 'min': 1, 'scopes': ('update',)},
    'critical_action_passphrase': {'default': '', 'desc': '关键操作口令（留空=不启用）', 'type': 'text', 'max_length': 300, 'scopes': ('update',)},
    'security_read_only_enabled': {'default': 'false', 'desc': '可选安全：只读模式开关', 'type': 'bool', 'scopes': ('update',)},
    'security_ip_allowlist_enabled': {'default': 'false', 'desc': '可选安全：IP 白名单开关', 'type': 'bool', 'scopes': ('update',)},
    'security_ip_allowlist': {'default': '', 'desc': '可选安全：IP 白名单列表（逗号分隔，支持 CIDR）', 'type': 'text', 'max_length': 1200, 'scopes': ('update',)},
    'security_2fa_enabled': {'default': 'false', 'desc': '已弃用：2FA 已迁移到数据库管理员账号', 'type': 'bool', 'scopes': ('update',)},
    'security_2fa_secret': {'default': '', 'desc': '已弃用：2FA 已迁移到数据库管理员账号', 'type': 'text', 'max_length': 500, 'scopes': ('update',)},
    'security_role_model_enabled': {'default': 'false', 'desc': '已弃用：当前为单管理员模型', 'type': 'bool', 'scopes': ('update',)},
    'webhook_enabled': {'default': 'false', 'desc': '可选：启用事件 Webhook', 'type': 'bool', 'scopes': ('update',)},
    'webhook_url': {'default': '', 'desc': '可选：Webhook 地址', 'type': 'url', 'max_length': 500, 'scopes': ('update',)},
    'webhook_secret': {'default': '', 'desc': '可选：Webhook 密钥', 'type': 'text', 'max_length': 500, 'scopes': ('update',)},
    'api_access_token': {'default': '', 'desc': '可选：API 访问令牌（留空=不启用）', 'type': 'text', 'max_length': 500, 'scopes': ('update',)},

    'backfill_batch_limit': {'default': '500', 'desc': '映射回填批次大小（自动调优）', 'type': 'int', 'min': 50, 'max': 3000, 'scopes': ('backfill',)},
    'backfill_max_candidates': {'default': '120', 'desc': '映射回填候选上限', 'type': 'int', 'min': 20, 'max': 500, 'scopes': ('backfill',)},
    'backfill_file_fetch_workers': {'default': '4', 'desc': '映射回填文件列表并发请求数', 'type': 'int', 'min': 1, 'max': 16, 'scopes': ('backfill',)},
    'backfill_max_failures': {'default': '2', 'desc': '映射回填失败跳过阈值', 'type': 'int', 'min': 0, 'max': 10, 'scopes': ('backfill',)},
    'backfill_path_mappings': {'default': '', 'desc': '回填路径映射，格式 /host/path=>/container/path;...', 'type': 'text', 'max_length': 4000, 'scopes': ('backfill',)},
    'backfill_failure_retention_days': {'default': '7', 'desc': '长期失败回填记录重置阈值（天）', 'type': 'int', 'min': 1, 'scopes': ('backfill',)},
    'backfill_deep_budget_seconds': {'default': '120', 'desc': '深度重试时间预算（秒）', 'type': 'int', 'min': 30, 'max': 600, 'scopes': ('backfill',)},
    'mapping_reset_cooldown_seconds': {'default': '60', 'desc': '手动重置失败计数冷却（秒）', 'type': 'int', 'min': 0, 'max': 3600, 'scopes': ('backfill',)},

    'version_check_cached_remote': {'default': '', 'desc': '版本检查缓存远端版本'},
    'version_check_cached_at': {'default': '', 'desc': '版本检查缓存检查时间'},
    'worker_last_seen_at': {'default': '', 'desc': 'worker 最近心跳时间'},
    'worker_heartbeat_ttl_seconds': {'default': '45', 'desc': 'worker 心跳有效秒数', 'type': 'int', 'min': 5, 'max': 3600},
}


def get_setting_descriptor(key: str):
    spec = SETTING_SPECS.get(str(key))
    if not spec:
        return None
    return {'key': key, **spec}


def get_default_setting_value(key: str, env: dict[str, str] | None = None):
    spec = SETTING_SPECS.get(str(key))
    if not spec:
        return ''
    return str(spec['default'])


def build_default_config_rows(env: dict[str, str] | None = None):
    return [(key, get_default_setting_value(key, env=env), spec['desc']) for key, spec in SETTING_SPECS.items()]


def get_editable_setting_keys():
    return tuple(key for key, spec in SETTING_SPECS.items() if spec.get('scopes'))


def get_setting_scopes():
    scopes: dict[str, list[str]] = {}
    for key, spec in SETTING_SPECS.items():
        for scope in spec.get('scopes', ()):
            scopes.setdefault(scope, []).append(key)
    return {scope: tuple(keys) for scope, keys in scopes.items()}


def get_importable_setting_keys():
    return frozenset(get_editable_setting_keys())


def validate_setting_value(key: str, raw):
    spec = SETTING_SPECS.get(str(key))
    if not spec:
        return False, f'unknown setting: {key}'

    text = str(raw or '').strip()
    typ = spec.get('type', 'text')
    if typ == 'bool':
        if text not in {'true', 'false'}:
            return False, 'must be true or false'
        return True, text

    if typ == 'int':
        try:
            val = int(text or spec['default'])
        except Exception:
            return False, 'must be an integer'
        if 'min' in spec and val < spec['min']:
            return False, f'must be >= {spec["min"]}'
        if 'max' in spec and val > spec['max']:
            return False, f'must be <= {spec["max"]}'
        return True, str(val)

    if typ == 'choice':
        if text not in set(spec.get('choices', ())):
            choices = ', '.join(spec.get('choices', ()))
            return False, f'must be one of: {choices}'
        return True, text

    if typ == 'repo':
        is_url = text.startswith(('http://', 'https://'))
        is_slug = bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', text))
        if text and not (is_url or is_slug):
            return False, 'must be a URL or owner/repo slug'
        if len(text) > spec.get('max_length', 10**9):
            return False, f'is too long (max {spec["max_length"]})'
        return True, text

    if typ == 'branch':
        if text and not re.fullmatch(r'[A-Za-z0-9._/-]{1,120}', text):
            return False, 'contains invalid characters'
        return True, text

    if typ == 'url':
        if text and not text.startswith(('http://', 'https://')):
            return False, 'must start with http:// or https://'
        if len(text) > spec.get('max_length', 10**9):
            return False, f'is too long (max {spec["max_length"]})'
        return True, text

    if typ == 'proxy_url':
        if text and not text.startswith(('http://', 'https://', 'socks5://')):
            return False, 'must start with http://, https:// or socks5://'
        if len(text) > spec.get('max_length', 10**9):
            return False, f'is too long (max {spec["max_length"]})'
        return True, text

    if 'max_length' in spec and len(text) > spec['max_length']:
        return False, f'is too long (max {spec["max_length"]})'
    return True, text
