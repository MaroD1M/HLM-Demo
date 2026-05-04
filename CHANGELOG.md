# Changelog

## [Unreleased]

### Added
- Telegram 通知新增代理配置：`tg_proxy_url`（支持 `http://127.0.0.1:7890`）。
- Telegram 通知新增 API Base 配置：`tg_api_base`。
- 新增统一 UI 基座模板 `templates/base.html`。
- 新增最小冒烟测试：`tests/test_routes_smoke.py`。
- 新增路由依赖对象：`core/deps.py`。

### Changed
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
- 路径校验与白名单策略（`allowed_roots`）保持启用。
- CSRF 防护保持启用。
