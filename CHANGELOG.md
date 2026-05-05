# Changelog

## [v0.0.11] - 2026-05-05

### Fixed
- 修复编辑弹窗频繁闪动与无法关闭问题：将定时任务等面板中的弹窗结构统一移出表格循环区域，避免无效 DOM 导致的 Bootstrap Modal 状态异常。
- 修复任务编辑交互稳定性：弹窗提交后可正常关闭，不再需要整页刷新。

### Changed
- 模板结构统一重构：`downloader/notifier/delete_monitor/logs/mapping` 页面改为可维护的多行语义化结构，降低后续改动引发回归的概率。
- 新增弹窗命名统一：新增窗口 ID 改为语义化唯一命名（如 `addDownloaderModal`、`addNotifierModal`、`addDeleteMonitorModal`）。
- 日志与映射页面增强空状态提示与排版一致性，长文本展示与页面可读性进一步优化。

## [v0.0.10] - 2026-05-05

### Added
- 设置页、硬链接页、下载器页、通知器页、删除联动页新增信息提示图标与悬浮说明，降低新手理解成本。
- 映射页新增映射记录操作：支持“重试关联”与“删除映射记录”（仅删除数据库映射，不删除真实文件）。
- 长文本自动换行能力增强：路径、Hash、日志消息等超长内容可完整显示，便于排障。

### Changed
- 映射与缓存页面中文化与可读性优化：来源类型改为“手动文件/下载器文件”，并增加关联状态直观展示。
- 设置页重构为分区卡片样式，保存设置与检查更新按钮职责明确且互不影响。
- 代理相关提示文案统一为“留空=使用系统设置中的统一代理”，避免配置歧义。
- 默认统一外网代理地址模板统一为 `http://127.0.0.1:7890`，与设置页示例保持一致。

### Fixed
- 修复待确认筛选值兼容问题：页面显示“映射 hash”，提交值保持 `mapping_hash`，避免筛选失效。
- 修复部分中英文术语混用导致的认知不一致（如 Torrent/Chat ID/Dry Run 等）。
- 修复 README 文案替换遗留问题，确保说明文本与实际配置项一致。

## [v0.0.9] - 2026-05-05

### Added
- 新增来源感知双向联动架构：`FileLinkMap.source_type` 区分 `manual/downloader`，删除联动按来源执行不同策略。
- 设置页新增 6 个联动开关：手动来源与下载器来源分别控制“删源/删目标/删种”。
- 映射页新增来源类型展示与筛选（全部/manual/downloader），便于排查联动行为。
- 新增版本信息能力：`VERSION` 本地版本文件、设置页 GitHub 最新版本检查与手动检查入口（代理统一由设置页配置）。
- 新增应用日志滚动分割：支持按大小分割与保留份数，减少磁盘占用和日志读取卡顿。

### Changed
- 硬链接页与系统设置统一为“模板+任务覆盖”模式：默认扩展名/排除目录仅作为新建任务默认值。
- 代理配置统一：移除分散代理配置项，Telegram 与 GitHub 均使用设置页 `proxy_url`。
- 删除联动核心服务重构：先处理文件对侧联动，再按来源决定是否删种；manual 来源默认不删种。
- 回填逻辑增强：命中下载器后自动将映射来源升级为 `downloader`。
- 启动兼容迁移增强：自动补齐 `file_link_map.source_type` 列并回填历史数据。
- 设置页与后端配置对齐：移除未接线的 `delete_delay_seconds`、`notify_on_hardlink`，避免“可配置但不生效”。
- 自动清理日志开关生效：关闭 `auto_clean_logs` 后，Cron 日志清理将记录跳过而不再执行。

### Fixed
- 修复硬链接任务开关失效：`/hardlink/toggle` 双重取反导致状态不变。
- 移除“重启自动更新”能力，避免不同部署环境下不确定性与误更新风险。
- 修复默认排除目录未生效：任务新增/修改时空值将正确回退到 `default_exclude_dirs`。

## [v0.0.8] - 2026-05-04

### Fixed
- 修复删除联动异常：`DeleteMonitorTask` 补充 `downloader/notifier` 关系，解决 `object has no attribute downloader` 报错。
- 修复删除联动时区异常：统一兼容 naive/aware datetime，解决 `can't subtract offset-naive and offset-aware datetimes`。
- 修复前端交互卡死：AJAX 成功后强制清理 modal 遮罩和 `modal-open` 状态，避免页面“不可点击”。

### Changed
- 增强容器日志可见性：日志强制输出到 stdout，并增加启动日志标记，便于群晖容器日志排查。
- 删除联动目录匹配改为规范路径判断，避免 `/media/a` 误匹配 `/media/ab`。
- 硬链接覆盖策略加固：仅允许覆盖系统托管目标文件，非托管同名文件直接跳过，防止误删。

## [v0.0.7] - 2026-05-04

### Added
- 登录安全限流：同一 IP 在 1 分钟内连续认证失败超过 3 次，将封禁 30 分钟（返回 429 与 Retry-After），重启容器后自动清空封禁状态。
- 删除联动新增“疑似误删待确认队列”：风险匹配（`name_match` / `path_match`）默认转为人工确认，不再直接删种。
- 删除联动页面新增“待确认（疑似误删）”操作区，可手动“确认删除 / 驳回”。
- 系统设置新增 `notify_on_risky_delete` 与 `delete_match_strict_mode` 开关。
- 容器日志增强：新增 `auth_blocked` / `auth_failed_blocked` 记录，便于在群晖容器日志中直接排查暴力尝试与封禁行为。

### Changed
- 时区显示修复：页面时间统一按 `TZ` 渲染（默认 `Asia/Shanghai`），日志页/仪表盘/映射页不再直接显示 UTC。
- 前后端交互优化：核心操作统一 AJAX 反馈，结果在页面顶部即时提示并刷新对应面板。
- 删除任务清理逻辑修复：删除硬链接任务时，缓存清理从“前缀模糊匹配”改为“规范路径匹配”，避免误删相似目录缓存。
- 删除识别逻辑修复：按“实际消失侧（源/目标）”判断删除路径，减少误识别与漏识别。

### Fixed
- 修复定时计划修改体验与说明，新增 Cron 表达式可视化解释。
- 修复下载器测试与多项任务操作反馈不直观的问题，统一反馈与日志落盘。

## [v0.0.4] - 2026-05-04

### Changed
- Compose 默认目录映射方案升级为“单媒体根目录”模式：`MEDIA_ROOT -> /media`，任务可在程序内自由配置子目录。
- `docker-compose.yml` 与 `docker-compose.prod.yml` 增加完整中文注释，并保留双目录映射的可选注释模板。
- `.env.example` 与 `.env.prod.example` 同步为单目录映射示例，显著降低新手配置复杂度。
- `README.md` 重构为小白友好文档：新增可直接复制的 Compose 示例、群晖图形界面部署步骤、任务模板与 FAQ。
- `scripts/bootstrap.sh` 初始化提示文案更新为中文，并明确要求优先配置 `SECRET_KEY` 与 `MEDIA_ROOT`。

### Verified
- 本地 `make check` 全量通过（语法检查 + 路由测试 + 冒烟检查）。
- 依赖漏洞扫描通过：`pip-audit` 未发现已知漏洞。


### Added
- 新增任务执行历史模型 `JobExecutionLog`，记录手动/定时执行状态、耗时与结果消息。
- 新增数据库备份服务 `core/services/backup_service.py`（SQLite 原生 backup API + 保留轮转）。
- 新增删除监控“立即执行”与 Cron 任务“立即执行”入口。
- 新增 Cron 任务类型 `db_backup` 与系统默认备份计划（每 6 小时）。

- Telegram 通知新增代理配置：`tg_proxy_url`（支持 `http://127.0.0.1:7890`）。
- Telegram 通知新增 API Base 配置：`tg_api_base`。
- 新增统一 UI 基座模板 `templates/base.html`。
- 新增最小冒烟测试：`tests/test_routes_smoke.py`。
- 新增路由依赖对象：`core/deps.py`。

### Changed
- UI 统一升级为现代化侧边导航与仪表盘布局，增强移动端适配。
- Compose 持久化补充 `./data/backups:/app/data/backups`，避免备份丢失。
- 设置页新增 `backup_dir` 与 `backup_keep_last` 配置。

- 架构重构为分层：
  - `core/models.py`（模型）
  - `core/extensions.py`（扩展）
  - `core/routes/web.py`、`core/routes/api.py`（路由）
  - `core/services/*.py`（业务服务）
- 业务模式从实时监听切换为定时扫描驱动（硬链接、删除联动、回填任务）。
- 删除联动增加冷却时间、单次阈值、Dry Run 防误删策略。
- Cron 支持映射回填任务 `backfill_mapping`。
- 全量替换 SQLAlchemy `Query.get()` 为 `db.session.get(...)`。

### Removed
- 删除未接入的重复定义与冗余文件。
- 移除未使用依赖：`watchdog`、`python-telegram-bot`。

### Added
- 新增 `docker-compose.yml`（基础部署）与 `.env.example`。
- 新增 `docker-compose.prod.yml`（生产安全加固版）。
- 新增 `.env.prod.example` 生产环境变量模板。
- 新增运维脚本：`scripts/bootstrap.sh`、`scripts/backup.sh`。
- 新增运行数据目录结构：`data/instance/.gitkeep`、`data/backups/.gitkeep`。

### Changed
- README 新增基础与生产 Compose 部署文档、持久化与备份说明。
- `.gitignore` 新增运行数据与 `.env` 忽略规则，防止敏感信息误提交。

### Security
- 升级 `cryptography` 至 `46.0.7`，修复 `CVE-2026-39892`。
- 修复 `allowed_roots` 路径白名单校验边界：从字符串前缀匹配改为规范化路径包含校验，避免路径前缀绕过。
- 启动时增加默认 `SECRET_KEY` 风险告警，降低误用默认密钥风险。
- CSRF 防护保持启用。
- 新增 Dependabot 自动依赖检查（pip + GitHub Actions）。
- 新增 Dependabot 安全自动合并策略（仅 patch/minor）。
