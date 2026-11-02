# HLM-Demo 技术交接文档

版本：`v0.5.2`  
数据库 schema：`v17`  
文档更新时间：2026-11-01  
适用对象：后续维护者、部署者、测试者、代码接手人

---

## 1. 文档定位

本文档回答四个问题：

1. 这个项目完整规划是什么？
2. 当前版本已经完成了什么？
3. 还有哪些方案没有完成？
4. 后续接手后应该优先做什么？

配套文档：

- [README.md](README.md)：项目简介与快速开始
- [DEPLOYMENT.md](DEPLOYMENT.md)：部署、升级、备份、反向代理、镜像构建
- [USER_GUIDE.md](USER_GUIDE.md)：页面操作指南与界面预览
- [ARCHITECTURE.md](ARCHITECTURE.md)：架构与关键流程
- [CHANGELOG.md](CHANGELOG.md)：版本变更历史

---

## 2. 项目定位

HLM-Demo 是一个面向 NAS / 群晖 / 自建媒体服务器的硬链接自动化管理器。

核心目标：

- 下载目录完成后自动创建硬链接到媒体库。
- 源/目标任意一侧删除后执行可控的联动处理。
- 支持 qBittorrent 种子识别、删种、通知。
- 减少误删风险：dry-run、cross-seed、回收站、冷却、阈值、待确认。
- 单容器开箱即用，同时支持 web / worker / scheduler 分离部署。
- 保留本地静态资源，适合离线或内网环境。

非目标：

- 不做完整的 Sonarr/Radarr 替代品。
- 不做下载器本身。
- 不做媒体播放器。
- 不以多租户 SaaS 为当前目标。
- 不为了“现代前端”引入 React/Vue SPA 复杂度。

---

## 3. 用户与场景

### 典型用户

- 群晖/NAS 用户，使用 qBittorrent 下载影视。
- 媒体库由 Emby/Jellyfin/Plex/Kodi 读取。
- 希望下载完成后自动硬链接，节省空间。
- 希望删除时能联动清理，但担心误删。
- 有一定 Docker 使用能力，但不是专业开发者。

### 典型流程

```text
qBittorrent 下载完成
  -> /media/downloads/movie
  -> HLM 创建硬链接
  -> /media/library/movie
  -> 媒体服务器扫描
  -> 用户删除下载文件或库文件
  -> HLM 执行删除联动 / 待确认 / 回收站
```

---

## 4. 完整项目规划

### 4.1 产品层

| 模块 | 目标 | 当前状态 |
|---|---|---|
| 硬链接任务 | 多任务、多目录、扩展名、排除规则 | 已完成 |
| 删除联动 | 双向删除、删种、冷却、阈值、dry-run | 已完成 |
| 误删保护 | cross-seed、待确认、根目录保护、批量熔断 | 已完成 |
| 回收站 | 移入回收站、恢复、永久删除、过期清理 | 已完成 |
| 映射回填 | 文件名+大小匹配、冲突处理、批次调优 | 已完成 |
| 下载器 | qBittorrent，分类过滤 | 已完成 qB；多客户端未完成 |
| 通知 | Telegram、Apprise | 已完成 |
| 定时任务 | cron + 独立 scheduler | 已完成 |
| API | JobQueue、指标、OpenAPI、Webhook | 已完成基础 |
| 诊断 | 数据库/日志/备份/可写性/支持包 | 已完成 |
| 安全 | 初始化令牌、管理员、2FA、CSRF、Token、路径限制 | 已完成基础 |
| UI | 服务端渲染、htmx、暗色主题、响应式 | 已完成基础 |
| 多用户/RBAC | 角色权限 | 未完成，当前单管理员 |

### 4.2 架构层

目标架构：

```text
Web (gunicorn)
  -> Flask web/API
  -> JobQueue
  -> run_key file lock

Worker
  -> JobQueue consumer
  -> hardlink/delete/backfill/backup
  -> qBittorrent/Telegram/Apprise

Scheduler
  -> cron only

SQLite
  -> WAL
  -> foreign keys
  -> schema v17
```

当前已实现：

- web / worker / scheduler 三进程拆分。
- JobQueue 基础优先级、重试、租约。
- worker 心跳。
- 跨进程 run_key 文件锁。
- gunicorn 生产入口。
- SQLite WAL。

尚未完成：

- 所有 UI 操作完全队列化。
- DB 化停止/租约/执行锁。
- app factory + 完整 Blueprint 拆分。
- Alembic 迁移。
- 模型归一。

### 4.3 运维层

| 能力 | 状态 |
|---|---|
| Docker Compose 单镜像多角色 | 已完成 |
| 首次初始化令牌 | 已完成 |
| 管理员改密 / 2FA | 已完成 |
| 备份 manifest / 恢复校验 | 已完成 |
| 恢复前留底 | 已完成 |
| 独立 scheduler | 已完成 |
| worker 心跳 | 已完成 |
| 回收站 | 已完成 |
| Prometheus 指标 | 已完成 |
| Sentry 可选 | 已完成 |
| JSON 日志 / 请求 ID | 已完成 |
| 非 root / PUID/PGID | 未完成 |
| SBOM / Trivy / 签名 | 未完成 |

### 4.4 测试层

| 类型 | 状态 |
|---|---|
| 单元/服务测试 | 已覆盖核心服务 |
| 路由烟雾测试 | 已完成 |
| 非 root 启动测试 | 已完成 |
| gunicorn 健康检查 | 已完成 |
| worker/scheduler 启动测试 | 已完成 |
| 备份脚本测试 | 已完成 |
| 覆盖率 | 58% |
| qB API mock | 未完成 |
| Playwright E2E | 未完成 |
| 多进程并发测试 | 未完成 |
| 旧 schema 全量升级测试 | 未完成 |

---

## 5. 当前已完成内容

### 5.1 v0.5.2 已发布

- GitHub Release：`v0.5.2`
- 镜像：
  - `ghcr.io/marod1m/hlm-demo:v0.5.2`
  - `ghcr.io/marod1m/hlm-demo:latest`
- 镜像 digest 以 GitHub Packages 页面为准，避免文档中的 digest 随重建失效。
- 平台：`linux/amd64`、`linux/arm64`
- GHCR 匿名拉取已验证。

### 5.1.1 v0.5.2 重点变化

- 新增批量映射重试（快速/深度），worker 在线时逐条提交 `backfill_single` 并自动刷新。
- 批量操作支持当前页和全选筛选结果；映射页默认排除软删除记录。
- 统一单条重试 run_key，修复后台深度重试停止不生效的问题。
- 任务队列显示中文类型；执行记录支持重试单条映射回填。
- 缓存视图隐藏无效的关联/来源/失败状态筛选。
- 版本检查保持手动模式，文档明确只有点击“检查更新”才访问 GitHub。

### 5.2 安全与初始化

- 初始化令牌写入 `instance/setup_token.txt`。
- 管理员密码哈希存储。
- 管理员页面改密。
- TOTP 2FA。
- 2FA 密钥加密。
- 环境变量账号兼容。
- 无认证模式需显式 `HLM_ALLOW_NO_AUTH=true`。
- CSRF、API Token、安全响应头、no-store。

### 5.3 删除安全

- dry-run 不删除、不消费记录。
- 根目录缺失保护。
- 源/目标重叠保护。
- 系统目录禁止。
- 单轮阈值。
- 批量异常熔断。
- cross-seed 检测。
- 回收站。
- 待确认。
- 跨进程 run_key 锁。

### 5.4 硬链接与映射

- 多任务、多目录。
- 扩展名/排除目录/文件年龄。
- 目标冲突保护。
- 已存在硬链接的映射补录。
- 源 inode 变化后清理旧种子关联。
- 映射/缓存管理。
- 回填匹配与冲突处理。
- 映射删除同步清理缓存。

### 5.5 执行与调度

- JobQueue。
- 优先级、重试、租约。
- worker 心跳。
- 手动任务自动入队。
- worker stale 修正。
- 独立 scheduler。
- gunicorn。
- 备份任务。
- 执行历史。

### 5.6 通知与观测

- Telegram。
- Apprise。
- Sentry 可选。
- Prometheus 文本指标。
- OpenAPI 概览。
- 请求 ID。
- JSON 日志。
- 支持包脱敏。
- 备份 manifest + SHA256 + SQLite integrity_check。

### 5.7 UI 与文档

- 仪表盘。
- 硬链接任务。
- 疑似误删处理。
- 映射与缓存。
- 回收站。
- 定时任务。
- 下载器/通知器。
- 日志与执行。
- 设置/开发更新/诊断。
- htmx 后台任务面板。
- 暗色主题。
- 首次使用向导。
- 界面预览截图。
- 部署、操作、架构、技术交接文档。

---

## 6. 未完成方案

### 6.1 执行架构

未完成：

- 所有 UI 手动操作统一队列化。
- 删除、回填、备份的同步执行路径完全移除。
- 停止请求完全 DB 化。
- 数据库级执行租约和 worker 崩溃自动回收。
- 多 gunicorn worker 安全执行。

影响：

- 当前推荐 `GUNICORN_WORKERS=1`。
- worker 不可用时仍会同步执行。

### 6.2 应用结构

未完成：

- `app.py` / `web.py` 完整 Blueprint 拆分。
- 真正 app factory 包结构。
- 服务层接口收敛。
- RouteDeps 大对象替换。

当前：

- `app.py` 约 2700 行。
- `web.py` 约 2000 行。
- `create_app()` 只是兼容入口，不是完整工厂。

### 6.3 数据库

未完成：

- Alembic batch migration。
- `HardlinkCache` 与 `FileLinkMap` 合并。
- `DeleteMonitorTask` 兼容层移除。
- `DeletePendingAction.task_id` 指向新任务模型。
- 统一状态枚举。
- 定期 VACUUM。

### 6.4 下载器与媒体生态

未完成：

- `qbittorrent-api` 替换手写客户端。
- Transmission/Deluge/SABnzbd adapter。
- Sonarr/Radarr history API。
- Plex/Jellyfin 刷新。
- no-hardlinks 检测。
- 文件级 cross-seed 重叠检测。

### 6.5 安全

未完成：

- 2FA recovery codes。
- 登录限流持久化。
- CSRF token 轮换。
- HSTS / Secure Cookie 启动检查。
- 备份加密。
- SSRF 白名单。
- 密钥轮换工具。
- 非 root / PUID/PGID。

### 6.6 UI

未完成：

- 全量 htmx。
- 移除 CSP `unsafe-inline`。
- SSE 实时进度。
- 任务详情页。
- 回收站批量操作。
- Playwright E2E。
- i18n。
- PWA。

### 6.7 测试

未完成：

- qB API mock。
- Playwright。
- 多进程并发。
- 完整旧 schema 升级。
- 备份恢复端到端。
- 安全扫描纳入 CI。
- 覆盖率提升到 75%+。

---

## 7. 后续行动计划

### 阶段 1：稳定性与实测

目标：验证 v0.5.2 真实环境。

1. 首次初始化。
2. 2FA。
3. qB 连接。
4. 分类过滤。
5. dry-run。
6. 删除联动。
7. cross-seed。
8. 回收站恢复。
9. scheduler cron。
10. worker 心跳。
11. 备份恢复。

验收：

- 无数据丢失。
- 无误删。
- 队列/worker/scheduler 正常。
- 日志可定位问题。

### 阶段 2：执行链路统一

1. 所有任务入队。
2. DB lease / stop / retry。
3. worker 崩溃自动恢复。
4. 多 worker 安全。
5. 任务详情与实时进度。

### 阶段 3：结构重构

1. app factory。
2. Blueprint 拆分。
3. Alembic。
4. 模型归一。
5. 移除兼容层。

### 阶段 4：生态集成

1. qbittorrent-api。
2. Sonarr/Radarr history。
3. Plex/Jellyfin。
4. no-hardlinks。
5. Apprise 模板。

### 阶段 5：UI 与质量

1. 全量 htmx。
2. SSE。
3. CSP nonce。
4. Playwright。
5. qB mock。
6. 覆盖率 75%+。

---

## 8. 代码地图

```text
app.py                        应用装配、认证、安全、qB、扫描、启动
worker.py                     JobQueue worker
scheduler.py                  独立 cron scheduler
wsgi.py                       gunicorn 入口
core/models.py                SQLAlchemy 模型
core/deps.py                  路由依赖对象
core/routes/web.py            页面路由
core/routes/api.py            JSON API / JobQueue / metrics / OpenAPI
core/services/hardlink_service.py
core/services/delete_service.py
core/services/backfill_service.py
core/services/execution_service.py
core/services/backup_service.py
core/services/migration_service.py
core/services/diagnostics_service.py
core/services/config_service.py
core/services/security_service.py
core/services/operation_guard_service.py
core/services/webhook_service.py
core/services/audit_service.py
templates/                    Jinja 页面与局部面板
static/css/                   设计令牌、布局、页面、暗色主题
static/vendor/                Bootstrap/FontAwesome/htmx/字体
scripts/                      启动、备份、截图、镜像构建
tests/                        回归测试
```

---

## 9. 关键数据模型

- `HardlinkTask`
- `FileLinkMap`
- `HardlinkCache`
- `Downloader`
- `Notifier`
- `DeleteMonitorTask`（兼容层）
- `DeletePendingAction`
- `RecycleItem`
- `JobQueue`
- `AdminUser`
- `AppConfig`
- `AppConfigSnapshot`
- `CronJob`
- `OperationLog`
- `JobExecutionLog`

详细字段见 `core/models.py`。

---

## 10. 接手开发步骤

### 本地启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python app.py
```

首次访问 `/setup`，读取 `instance/setup_token.txt`。

### 测试

```bash
make check
make coverage
```

### 构建镜像

```bash
bash scripts/build_image.sh
```

### 截图

```bash
bash scripts/take_screenshots.sh
```

### 发布

```bash
git add .
git commit -m "release: vX.Y.Z"
git tag vX.Y.Z
git push origin master --tags
```

GitHub Actions 会构建多架构镜像。

---

## 11. 风险登记

| 风险 | 影响 | 当前措施 | 后续 |
|---|---|---|---|
| 挂载异常 | 批量误删 | 根目录检查、熔断、回收站 | 挂载哨兵 |
| 弱匹配删种 | 误删种子 | 严格模式、cross-seed、待确认 | 文件级交叉验证 |
| 多进程执行 | 重复执行 | run_key 文件锁 | DB lease |
| SQLite 锁 | 写入阻塞 | WAL、短事务 | 队列/DB 优化 |
| 凭据泄露 | 安全风险 | Fernet、脱敏、权限 | 密钥轮换 |
| 首次暴露 | 被抢占管理员 | 初始化令牌 | 一次性 token |
| 2FA 丢失 | 无法登录 | 管理员密码 | recovery codes |
| 备份恢复 | 状态不一致 | 暂停调度、校验、重启提示 | 维护模式 |
| 大库扫描 | 超时/内存 | 分批/上限 | 后台队列/流式 |
| 覆盖率低 | 回归风险 | 109 测试 | 75%+ |

---

## 12. 交接检查清单

- [x] 版本号
- [x] 数据库 schema
- [x] 部署文档
- [x] 操作指南
- [x] 架构文档
- [x] 界面截图
- [x] 测试
- [x] CI
- [x] 镜像构建
- [x] GitHub Release
- [ ] 真实 qB 联调
- [ ] 真实 Telegram/Apprise 联调
- [ ] 真实备份恢复演练
- [ ] 多进程并发测试
- [ ] Alembic 迁移
- [ ] Blueprint 拆分
- [ ] 覆盖率 75%+

---

## 13. 维护原则

1. 删除安全优先于功能数量。
2. 先做 dry-run，再开正式删除。
3. 任何破坏性操作必须有预检、日志、回收站或待确认。
4. 不在请求线程执行长任务。
5. 不在 UI 和 API 复制业务逻辑。
6. 凭据必须加密，日志必须脱敏。
7. 数据库升级前必须备份。
8. 变更后必须跑 `make check`。
9. 新功能必须有回归测试。
10. 发布前必须更新 CHANGELOG、版本号、文档和截图。
