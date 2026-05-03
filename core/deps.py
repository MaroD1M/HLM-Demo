from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RouteDeps:
    HardlinkTask: Any
    DeleteMonitorTask: Any
    Downloader: Any
    Notifier: Any
    HardlinkCache: Any
    FileLinkMap: Any
    OperationLog: Any
    AppConfig: Any
    CronJob: Any
    db: Any

    get_config: Callable
    set_config: Callable
    log_operation: Callable
    validate_path: Callable
    validate_host: Callable
    validate_cron_expression: Callable

    scan_hardlink_task: Callable
    update_cron_job: Callable
    list_torrents: Callable
    send_telegram_notification: Callable
