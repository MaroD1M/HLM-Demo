# HLM-Demo 架构说明（v0.4.0）

## 1. 总体结构

```text
Browser
  -> Nginx / Reverse Proxy (optional)
  -> gunicorn -> Flask web/API
        -> SQLite (WAL)
        -> JobQueue
        -> run_key file locks

worker.py
  -> JobQueue 消费
  -> 硬链接/删除/回填/备份
  -> qBittorrent / Telegram / Apprise
  -> 心跳、停止轮询、过期租约修正

scheduler.py
  -> APScheduler cron
  -> 触发任务
  -> run_key file locks
```

## 2. 代码分层

- `app.py`：应用装配、认证、安全中间件、qB 客户端、扫描业务、启动流程。
- `core/models.py`：SQLAlchemy 模型。
- `core/routes/web.py`：页面与表单。
- `core/routes/api.py`：JSON API、JobQueue API、指标、OpenAPI。
- `core/services/`：硬链接、删除、回填、执行、备份、迁移、诊断、配置、安全、Webhook。
- `worker.py`：后台任务执行进程。
- `scheduler.py`：独立 cron 调度进程。
- `wsgi.py`：gunicorn 入口。
- `templates/`、`static/`：服务端渲染页面与本地静态资源。

## 3. 数据模型

核心表：

| 表 | 作用 |
|---|---|
| `HardlinkTask` | 硬链接任务与删除联动策略 |
| `FileLinkMap` | 源/目标映射、回填来源、删除状态 |
| `HardlinkCache` | 已处理源文件缓存 |
| `Downloader` | qBittorrent 连接与分类 |
| `Notifier` | Telegram / Apprise |
| `DeletePendingAction` | 待人工确认删种 |
| `RecycleItem` | 回收站文件与恢复元数据 |
| `JobQueue` | 后台任务、优先级、重试、租约 |
| `AdminUser` | 数据库管理员、密码哈希、TOTP |
| `AppConfig` | 运行时配置 |
| `AppConfigSnapshot` | 配置快照 |
| `CronJob` | 定时任务 |
| `OperationLog` / `JobExecutionLog` | 操作审计与执行历史 |

## 4. 关键流程

### 硬链接扫描

1. 校验路径、根目录、是否重叠、是否系统目录。
2. 递归扫描源目录。
3. 扩展名、排除目录、文件稳定性校验。
4. 检查缓存有效性。
5. 创建目标目录。
6. 目标存在时校验是否系统托管。
7. `os.link`。
8. 更新缓存与映射；支持已存在硬链接的映射补录。

### 删除联动

1. 检查源/目标根目录存在。
2. 批量异常熔断。
3. 冷却、单轮上限。
4. 来源判定：downloaders / manual / pending。
5. 对侧文件删除或移入回收站。
6. Cross-seed 检测。
7. 删种前严格匹配。
8. 弱匹配转待确认。
9. 通知。

### 回填

1. 按下载器分组。
2. 文件名/路径打分。
3. qB `/torrents/files` 文件名+大小强匹配。
4. 冲突不自动选择。
5. 失败计数、批量上限、自动调优。
6. 线程池并发获取文件列表。

### 执行与队列

- web 检测 worker 心跳。
- 活跃 worker 时，手动执行自动入队。
- 无 worker 时同步回退。
- worker 通过 JobQueue 领取任务。
- 任务带优先级、重试次数、30 分钟租约。
- 同一 `run_key` 使用跨进程文件锁。
- 停止请求写入 DB；worker 监控并请求停止。

### Scheduler

- web 生产模式关闭进程内 scheduler。
- `scheduler.py` 独立运行 cron。
- 启动时从 `CronJob` 加载任务。
- 文件锁避免多进程重复 cron。

## 5. 安全模型

- 初始化令牌 + 管理员密码哈希。
- TOTP 2FA。
- Session Cookie HttpOnly / SameSite / 可选 Secure。
- CSRF。
- 可选 API Token。
- 可选 IP 白名单。
- 可选只读模式。
- 路径白名单和系统目录拒绝。
- 凭据 Fernet 加密。
- 配置导出/支持包/日志脱敏。
- setup token、回收站、恢复口令。

## 6. 部署模型

- 单镜像多进程角色：
  - web: gunicorn
  - worker: python worker.py
  - scheduler: python scheduler.py
  - backup: 可变 profile
- SQLite 本地卷 + WAL。
- 共享 instance/media/backups/recycle/logs。

## 7. 已知技术债

- `app.py` 和 `web.py` 仍较大，Blueprint 拆分未完成。
- 内建迁移而非 Alembic。
- `HardlinkCache` 与 `FileLinkMap` 职责有重叠。
- `DeleteMonitorTask` 兼容层仍存在。
- qB 客户端仍手写。
- 尚未接入 Sonarr/Radarr history。
- 停止机制未完全 DB 化。
- 覆盖率约 58%，缺少 qB mock、Playwright、多进程并发测试。

## 8. 后续演进建议

1. 所有任务统一入队 + DB 租约/停止。
2. app factory + Blueprint 拆分。
3. Alembic batch migration。
4. 合并缓存/映射模型。
5. qbittorrent-api + *arr history。
6. SSE + 全量 htmx + CSP nonce。
7. Playwright、qB mock、并发与迁移测试。
8. 非 root 容器、SBOM、Trivy、Grafana 面板。
