from __future__ import annotations

from ipaddress import ip_address, ip_network


READ_ONLY_BLOCKED_PREFIXES = (
    'web_bp.hardlink_',
    'web_bp.delete_monitor_',
    'web_bp.downloader_',
    'web_bp.notifier_',
    'web_bp.mapping_',
    'web_bp.cron_',
    'web_bp.settings_',
)

READ_ONLY_BLOCKED_EXACT = {
    'logout',
}


def parse_csv_items(raw: str | None):
    return [part.strip() for part in str(raw or '').split(',') if part.strip()]


def is_ip_allowed(client_ip: str, allowlist_raw: str | None):
    rules = parse_csv_items(allowlist_raw)
    if not rules:
        return True
    try:
        ip_obj = ip_address(client_ip)
    except Exception:
        return False

    for rule in rules:
        try:
            if '/' in rule:
                if ip_obj in ip_network(rule, strict=False):
                    return True
            elif ip_obj == ip_address(rule):
                return True
        except Exception:
            continue
    return False


def is_read_only_forbidden_endpoint(endpoint: str | None):
    ep = str(endpoint or '')
    if ep in READ_ONLY_BLOCKED_EXACT:
        return True
    return any(ep.startswith(prefix) for prefix in READ_ONLY_BLOCKED_PREFIXES)

