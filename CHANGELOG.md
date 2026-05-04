# Changelog

## [Unreleased]

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
