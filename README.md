# 🎬 HLM-Demo

<div align="center">

# 🔗 Hardlink Manager · 媒体硬链接自动化中心

**让下载目录自动入库，让删除联动可控可靠。**  
**同时支持日常稳定使用、版本更新与本地运维。**

![项目截图](static/images/screenshots/dashboard.png)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Web-black?style=flat-square&logo=flask)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

</div>

> 当前版本：`v0.5.0`（2026-11-01）

详细文档：

- [部署与运维指南](DEPLOYMENT.md)
- [操作指南](USER_GUIDE.md)
- [架构说明](ARCHITECTURE.md)
- [技术交接文档](TECHNICAL_HANDOVER.md)

### 界面预览

| 仪表盘 | 硬链接任务 |
|---|---|
| ![仪表盘](static/images/screenshots/dashboard.png) | ![硬链接任务](static/images/screenshots/hardlink.png) |

| 删除保护 | 映射与缓存 |
|---|---|
| ![删除保护](static/images/screenshots/delete-monitor.png) | ![映射与缓存](static/images/screenshots/mapping.png) |

| 回收站 | 系统诊断 |
|---|---|
| ![回收站](static/images/screenshots/recycle.png) | ![系统诊断](static/images/screenshots/diagnostics.png) |

---


## 🆕 最近更新说明（v0.5.0）

### 主要更新
- 数据库 schema 升级到 v17：启动时自动修复历史库缺失的 `downloader.category` / `session_ttl_seconds` 列，避免 `no such column` 导致 worker 启动失败。
- 映射回填增强：支持“源文件夹名 = 种子名”的多文件种子；深度重试忽略下载器分类过滤并取消 120 候选截断，显著提升历史映射关联成功率。
- 映射与缓存页：快速/深度重试、删除、重置失败计数后保留当前分页和筛选条件；筛选后地址栏同步更新，翻页/刷新不再丢失状态。
- 深色模式：补齐表格、卡片、表头、空状态、分页按钮等硬编码浅色背景，修复深色模式下大面积白底和低对比度问题。
- 界面与版本：侧边栏新增版本号和新版本提示；“开发与更新”简化为“安全与版本”，移除开发模式配置入口，保留版本检查、管理员安全与访问控制。
- 文档与维护：补充技术交接文档、维护注释、CI 依赖审计和界面/升级说明。

### 对普通用户的影响
- 升级方式不变：`docker compose pull && docker compose up -d --force-recreate`。
- 首次启动新版本时会自动备份数据库并执行 schema v17 迁移；无需手动改库。
- 原“开发与更新”页面改为“安全与版本”；旧版 `instance/dev_runtime.env` 仍可被启动脚本兼容读取，但新部署不再需要开发模式配置。
- 版本号显示在侧边栏底部；有 GitHub 新版本时会显示提示，可进入“安全与版本”手动检查。

---

## 🎯 适用人群

- **日常使用者**：希望开箱即用、少折腾，复制 Compose 后只改少量个人信息即可启动。
- **运维排查者**：需要日志、诊断、备份、版本更新和状态可观测性。

---

## ✨ 主要能力

- 🧠 多任务硬链接：电影/剧集/动漫等独立任务管理
- 🔁 自动化执行：手动执行 + 定时 Cron 双模式
- 🧹 删除联动：支持冷却、阈值、试运行、通知
- 🛡️ 删除保护：cross-seed 检测、可选回收站、待判定保护
- 🧵 后台任务：任务队列 + worker 服务，支持异步执行与停止请求
- 🌙 界面：响应式布局、暗色主题、htmx 局部刷新与后台任务面板
- 📦 映射/缓存管理：筛选、重试、批量清理
- 🪵 日志与诊断：方便定位配置与运行问题
- 🩺 诊断支持包：可导出脱敏后的运行诊断信息（Support Bundle）
- 💾 备份恢复增强：备份校验、恢复前预检、恢复前自动留底
- 🔌 API 与 Webhook：提供基础 JSON 接口与可选事件推送

---

## ⚠️ 重要运行规则（本版本建议）

- **不新增 Docker 环境变量作为新功能配置入口**。  
  新增运行时能力统一通过“页面配置 + 数据库配置”生效。
- `RBAC / 只读模式 / IP 白名单 / 2FA / 关键操作口令 / Webhook` 均为**可选功能**，默认关闭，不强制配置。
- Compose 里的环境变量建议保持“启动基础项”（如 `SECRET_KEY`、登录凭据、时区）即可。
- v0.5.0 起已移除“开发与更新”页面；旧版 `instance/dev_runtime.env` 仅保留兼容读取。

---

## 🧵 后台 worker 与生产运行

- 生产入口使用 gunicorn（默认 1 worker、8 threads）。
- `worker.py` 负责消费后台任务队列；Compose 已提供 `worker` 服务。
- `scheduler.py` 独立运行 cron 调度；Compose 已提供 `scheduler` 服务，web 默认关闭进程内 scheduler。
- worker 会定期写入心跳；检测到活跃 worker 时，硬链接页面“立即执行”会自动转为后台队列，否则回退为同步执行。
- 同一 run_key 增加跨进程文件锁；web/worker 同时触发同一任务时只允许一个进程执行。
- 多 worker 场景建议保持 gunicorn worker 数为 1，后续可迁移到完全 DB 驱动的任务队列。
- `HLM_WORKER=true` 时进程不会启动 scheduler。

---

## 🗺️ 页面说明（建议先读）

- 🏠 **仪表盘**：总览状态、近期执行、快捷入口
- 🔗 **硬链接任务**：源目录/目标目录/扩展名/删除联动
- 🧹 **疑似误删处理**：处理待确认的风险删除记录
- 🧭 **映射与缓存**：查看来源、重试关联、批量管理
- **回收站**：查看、恢复或永久删除被联动移入回收站的文件
- ⏰ **定时任务**：统一调度入口
- ⚙️ **系统设置**：白名单、通知、版本检查、备份
- 🔍 **系统诊断/日志**：问题排查入口

---

## 🔁 备份与恢复（推荐操作顺序）

1. 进入“系统诊断 -> 备份与恢复”，先查看备份状态与校验结果。  
2. 若要恢复，先点“预览”确认该备份可恢复。  
3. 再执行“恢复”（如配置了关键口令需输入）。  
4. 系统会在恢复前自动对当前数据库做一份留底备份（`restore-preflight` 目录）。  

说明：
- 恢复流程包含路径合法性检查（防止路径越界）与备份完整性校验。
- 支持包导出和配置导出均会对敏感字段做脱敏处理（`***`）。
- 若未显式配置 `SECRET_KEY`，系统会自动生成 `instance/secret_key`；迁移到新容器时请同时保留该文件，否则已加密的下载器密码、通知器令牌和敏感配置将无法解密。
- SQLite 已启用 WAL 模式；请将 `data/instance` 放在本地磁盘/本地卷，不要放在 NFS/SMB 等网络文件系统上。
- 生产 Compose 可使用备份 profile：`docker compose -f docker-compose.prod.yml --profile backup run --rm backup`。

---

## 🔐 可选安全策略（默认关闭）

- 只读模式：限制高风险写操作入口。
- IP 白名单：支持单 IP 与 CIDR。
- 2FA：数据库管理员账号支持 TOTP 动态验证码，可在“安全与版本 -> 管理员安全”中启用。
- RBAC：当前为单管理员模型，未提供多角色权限。
- Cross-seed 保护：删除种子前检测同内容的其他种子，命中后转待确认。
- 回收站：可选将对侧文件移入回收目录，避免直接永久删除。
- 通知：支持 Telegram 和 Apprise（Discord/Gotify/Slack/邮件等，取决于 Apprise URL）。
- 关键操作口令：用于恢复、批量高风险操作等二次确认。

这些策略均可单独启用/关闭，不影响未启用场景的基础使用流程。

---

## 🔌 API 与自动化入口（概览）

当前可用的基础 JSON 接口：

- `GET /api/health`：健康检查
- `GET /api/tasks/status`：任务开关状态
- `GET /api/runtime/summary`：运行摘要（配置状态、近期日志、运行任务）
- `POST /api/webhooks/test`：Webhook 测试（需先在设置页启用并配置 URL）
- `GET /diagnostics/backfill-metrics`：自动关联指标
- `GET /diagnostics/support-bundle?format=zip|json`：导出诊断支持包（脱敏）
- `GET /api/v1/openapi.json`：OpenAPI 概览
- `GET /api/v1/metrics`：Prometheus 文本指标
- `POST /api/v1/jobs`：创建后台任务
- `GET /api/v1/jobs` / `GET /api/v1/jobs/{id}`：查询后台任务
- `POST /api/v1/jobs/{id}/stop`：请求停止后台任务

> 设置 `api_access_token` 后，除 `/api/health` 外的所有 `/api/*` 请求都需要携带请求头 `X-API-Token`。

---

## 🚀 一键部署（推荐：日常稳定使用）

### 第 1 步：创建目录

```bash
mkdir -p hlm-demo && cd hlm-demo
```

### 第 2 步：创建 `docker-compose.yml`

> 下面这份是**普通用户最简版**，开箱即用。你只需要改这几项：
> 1) `SECRET_KEY`  2) `APP_USERNAME`  3) `APP_PASSWORD`  4) `/你的媒体目录`

```yaml
services:
  hlm:
    image: ghcr.io/marod1m/hlm-demo:latest
    container_name: hlm-demo
    restart: unless-stopped

    ports:
      - "5000:5000"

    environment:
      SECRET_KEY: "请改成一个长随机字符串"
      APP_USERNAME: "admin"
      APP_PASSWORD: "请改成一个强密码"
      TZ: "Asia/Shanghai"
      PYTHONUNBUFFERED: "1"

    volumes:
      - ./data/instance:/app/instance
      - ./data/backups:/app/data/backups
      - ./data/logs:/app/data/logs
      - /你的媒体目录:/media
```

### 第 3 步：启动

```bash
docker compose up -d
```

### 第 4 步：访问

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://你的主机IP:5000`
- 首次访问会进入初始化页，要求输入初始化令牌并创建唯一管理员账号。令牌默认写入 `data/instance/setup_token.txt`（容器内 `/app/instance/setup_token.txt`），也可通过 `HLM_SETUP_TOKEN` 指定。如明确接受无认证访问，可设置 `HLM_ALLOW_NO_AUTH=true`。

### 第 5 步：首次建议操作

1. 先创建一个测试硬链接任务，验证路径与命名规则。  
2. 确认无误后再创建生产目录任务。  
3. 新建任务的删除联动默认开启“试运行”，观察日志并确认无误后再关闭测试模式。

---

## 🧪 开发调试（v0.5.0 起已简化）

v0.5.0 起不再提供“系统设置 -> 开发模式”的页面入口，普通部署不需要配置开发模式。

- 已有旧版 `instance/dev_runtime.env` 的实例仍会被启动脚本兼容读取。
- 新部署建议直接使用镜像更新流程；开发调试请在本地仓库运行或自行构建镜像。
- 旧版 `dev_*` 配置项不再在界面中展示，相关后端兼容逻辑保留。

---

## 📁 路径规则（最容易填错）

页面中的路径必须是**容器路径**：

- ✅ `/media/downloads/movie`
- ❌ `/volume1/media/downloads/movie`

---

## 🧩 任务模板（可直接照抄）

- 电影：`/media/downloads/movie -> /media/library/movie`
- 剧集：`/media/downloads/tv -> /media/library/tv`
- 动漫：`/media/downloads/anime -> /media/library/anime`

---

## 🔐 登录机制与安全建议（务必理解）

- 环境变量同时设置 `APP_USERNAME` / `APP_PASSWORD`：使用环境变量账号登录
- 未设置环境变量账号：首次访问会要求创建数据库管理员账号
- 明确需要无认证模式时：设置 `HLM_ALLOW_NO_AUTH=true`；**不建议在局域网/公网直接暴露无认证实例**
- 数据库管理员可在页面修改密码并启用 TOTP 2FA；环境变量账号需修改 Compose 后重启容器
- 数据库管理员密码只保存哈希
- 未配置 `SECRET_KEY` 时，系统会自动生成并持久化到 `instance/secret_key`
- 部署在 HTTPS 反向代理后时建议设置 `SESSION_COOKIE_SECURE=true`
- 仅在受信任反向代理后设置 `TRUST_PROXY_HEADERS=true`，否则会信任可伪造的 `X-Forwarded-For`
- 删除联动默认建议先使用“仅记录不执行”模式；该模式不会删除文件、不会删除种子、也不会消费待处理记录

---

## 🧯 常见问题

### Q1：为什么我访问 `/login` 会自动跳转？

- 未配置环境变量账号且未创建数据库管理员时，会跳转到首次初始化页 `/setup`。
- 已设置 `HLM_ALLOW_NO_AUTH=true` 且未启用登录时，会直接回到首页。

### Q2：如何更新到新版本？
```bash
docker compose pull
docker compose up -d --force-recreate
```
启动时会自动备份数据库并升级 schema；v17 会修复历史库缺失的下载器列。

### Q3：升级后页面版本号没变？
- 确认拉取的是新镜像：`docker compose pull` 后强制重建容器。
- 检查侧边栏底部版本号；如果仍显示旧版本，可能是镜像缓存或未重建成功。
- 版本检查依赖 GitHub API；内网环境可在“安全与版本”手动检查或忽略。

### Q4：导入/导出会泄露关键口令吗？
导出会隐藏关键口令；导入仅覆盖支持的配置项。

---

## 🧑‍💻 本地开发（非 Docker）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python app.py
# 可选：代码检查
make lint
# 可选：覆盖率
make coverage
# 可选：依赖安全检查
make audit
```

---

## 📦 项目结构

```text
app.py
worker.py
wsgi.py
core/
  models.py
  deps.py
  routes/
    web.py
    api.py
  services/
    config_service.py
    audit_service.py
    execution_service.py
    migration_service.py
    hardlink_service.py
    delete_service.py
    backfill_service.py
    backup_service.py
    operation_guard_service.py
    diagnostics_service.py
    security_service.py
    webhook_service.py
    runtime_config_service.py
templates/
static/
scripts/
docker-compose.yml
docker-compose.prod.yml
```
