# 🎬 HLM-Demo

<div align="center">

# 🔗 Hardlink Manager · 媒体硬链接自动化中心

**让下载目录自动入库，让删除联动可控可靠。**  
**面向新手开箱即用，也兼顾多任务与扩展性。**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Web-black?style=flat-square&logo=flask)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

</div>

> 当前版本：`v0.2.1`（2026-05-07）

---

## 🌟 项目亮点

- 🧠 **多任务管理**：一个系统同时管理电影、剧集、动漫等多个硬链接任务
- 🧩 **通用模板+任务覆盖**：系统设置提供默认扩展名/排除目录模板，任务页面可单独覆盖
- 📦 **单目录映射更灵活**：Compose 只映射一个媒体根目录，任务里自由配置源/目标子目录
- ⏱️ **定时 + 手动双模式**：支持 Cron 周期执行，也支持“立即执行一次”
- 🧹 **删除联动可控**：支持冷却、阈值、试运行，降低误删风险
- 🛡️ **疑似误删保护**：高风险匹配自动进入“待确认队列”，支持 Telegram 告警与手动确认
- 🔁 **来源感知双向联动**：`manual` 与 `downloader` 两类来源分流策略，支持“删源/删目标/删种”按场景独立开关
- 💾 **数据库备份**：支持脚本与 Compose profile 双备份方案
- 🔔 **通知能力**：支持 Telegram（支持统一代理与自定义 API Base）

---

## 🖼️ 界面导览（你会用到的页面）

- 🏠 **仪表盘**：整体运行状态、任务入口
- 🔗 **硬链接任务**：配置源目录/目标目录、扩展名、排除目录
- 🧹 **疑似误删处理**：处理高风险待确认删除（联动配置在硬链接任务内）
- ⏰ **Cron 调度**：统一管理定时计划
- ⚙️ **系统设置**：路径白名单、通知、日志保留、备份参数（含疑似误删通知与严格匹配开关）
- 📜 **日志页面**：查看执行结果与排错信息

---

## 🧩 目录映射最佳实践（重点）

### 推荐方式：只映射一个媒体根目录

你完全可以不在 Compose 里固定“源目录”和“目标目录”两条映射。  
**推荐只映射一个媒体总目录到容器 `/media`**，然后在程序内按任务配置子目录。

示例：
- 任务A：源 `/media/downloads/movie` -> 目标 `/media/library/movie`
- 任务B：源 `/media/downloads/tv` -> 目标 `/media/library/tv`
- 任务C：源 `/media/downloads/anime` -> 目标 `/media/library/anime`

✅ 优点：
- 新增任务不需要改 Compose
- 结构统一、维护简单
- 适合多任务长期使用

---

## 🚀 快速开始（Docker Compose）

### 1）复制配置

```bash
cp .env.example .env
```

### 2）只改关键项

编辑 `.env`，重点改：
- `SECRET_KEY`（必须）
- `APP_USERNAME` / `APP_PASSWORD`（建议）
- `MEDIA_ROOT`（必须，宿主机媒体总目录）

### 3）初始化（推荐）

```bash
./scripts/bootstrap.sh
```

### 4）启动

```bash
docker compose up -d
```

### 5）访问

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://你的主机IP:5000`

---

## 🧪 拿来即用 Compose 示例（小白友好版）

> 你只需要改 4 处：密钥、账号、密码、媒体目录。

```yaml
services:
  hlm:
    image: ghcr.io/marod1m/hlm-demo:latest
    container_name: hlm-demo
    restart: unless-stopped

    # 显式使用 bridge 网络，便于访问 Telegram 等外部服务
    network_mode: bridge

    # 端口映射：宿主机5000 -> 容器5000
    ports:
      - "5000:5000"

    environment:
      # 必填：请改成你自己的强随机密钥
      SECRET_KEY: "请改成很长很随机的字符串"

      # 建议：开启基础登录认证
      APP_USERNAME: "admin"
      APP_PASSWORD: "123456"

      # 可选：请求超时与时区
      REQUEST_TIMEOUT_SECONDS: 10
      TZ: "Asia/Shanghai"

    volumes:
      # 必须：数据库与运行状态（不要删除）
      - ./data/instance:/app/instance

      # 建议：数据库备份目录
      - ./data/backups:/app/data/backups

      # 必填：把左边路径改成你的宿主机媒体总目录
      - /你的宿主机媒体总目录:/media

    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

### ✅ 启动命令

> 说明：删除联动配置已并入“硬链接任务”，风险处理页面现在仅用于“疑似误删待确认处理”。

```bash
docker compose up -d
docker compose ps
docker compose logs -f hlm
```

---

## 🧰 群晖（Synology）图形界面部署（Container Manager）

> 适合不想用命令行的用户。

### 步骤 1：创建项目目录

在群晖共享文件夹里创建一个目录，例如：
- `/volume1/docker/hlm-demo`

并在里面新建子目录：
- `data/instance`
- `data/backups`

### 步骤 2：准备 Compose 文件

在该目录创建 `docker-compose.yml`，粘贴上面的“一键示例”。

把这行改成你的真实媒体目录：
- `- /volume1/media:/media`

### 步骤 3：在群晖图形界面导入项目

- 打开 **Container Manager**
- 进入 **项目（Project）**
- 点击 **新增（Create）**
- 选择你刚才的 `docker-compose.yml`
- 点击部署

### 步骤 4：访问并创建任务

部署成功后访问：
- `http://群晖IP:5000`

在页面创建任务时填写容器内路径：
- 源：`/media/downloads`
- 目标：`/media/library`

> 注意：页面里填的是容器路径（`/media/...`），不是宿主机路径（`/volume1/...`）。

---

## ⚙️ 生产部署（加固版）

使用 `docker-compose.prod.yml` 可获得更安全默认值：

- `read_only: true`
- `tmpfs: /tmp`
- `no-new-privileges`
- `cap_drop: ALL`

### 启动命令

```bash
cp .env.prod.example .env
# 修改 SECRET_KEY 和 MEDIA_ROOT
docker compose -f docker-compose.prod.yml up -d
```

### 触发数据库备份

```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm backup
```

---

## 💽 持久化与数据说明

这些目录会长期保存，容器重建也不会丢：

- `./data/instance -> /app/instance`：数据库与运行状态
- `./data/backups -> /app/data/backups`：数据库备份文件
- `${MEDIA_ROOT} -> /media`：媒体总目录（任务源/目标都在这里按子目录区分）

---

## 🧭 新手任务模板（直接抄）

### 电影任务
- 名称：`电影入库`
- 源目录：`/media/downloads/movie`
- 目标目录：`/media/library/movie`

### 剧集任务
- 名称：`剧集入库`
- 源目录：`/media/downloads/tv`
- 目标目录：`/media/library/tv`

### 动漫任务
- 名称：`动漫入库`
- 源目录：`/media/downloads/anime`
- 目标目录：`/media/library/anime`

---

## 🛡️ 安全建议（务必执行）

- 把 `SECRET_KEY` 改成强随机字符串
- 建议设置 `APP_USERNAME` / `APP_PASSWORD`
- 尽量只在内网开放端口
- 建议定期备份数据库并做异地备份

---

## 🔔 通知文案与日志排查

### 来源感知联动说明（新增）

系统会给每条映射标记来源类型：
- `manual`：手动复制/移动到源目录形成的映射
- `downloader`：可关联到下载器种子（含回填命中）的映射

在 **设置 -> 来源感知联动策略** 中可分别控制：
- 删目标后是否删源
- 删源后是否删目标
- downloader 来源是否执行删种（并保留疑似误删待确认机制）

> 推荐默认：manual 只做文件联动，不碰种子；downloader 才允许删种。

### Telegram 通知大致长这样

- 测试通知：`Hardlink Manager 测试通知`
- 删除联动成功：

```text
删除联动成功
任务: <任务名>
种子: <torrent hash>
匹配: <匹配方式>
```

> 说明：当前“硬链接成功”默认走操作日志记录，删除联动在启用通知时会推送到 Telegram。

### 疑似误删通知（新增）

当删除联动命中高风险匹配（如 `name_match` / `path_match`）时，系统默认不会立即删种，而是：

1. 写入删除联动页面的“待确认（疑似误删）”列表
2. 按设置项 `notify_on_risky_delete=true` 发送 Telegram 告警
3. 由你手动点击“确认删除”或“驳回”

建议保持以下配置：
- `delete_match_strict_mode=true`（严格模式，推荐）
- `notify_on_risky_delete=true`（风险提醒开启，推荐）

### 容器日志里现在能看到什么

已增强为可在 `docker logs` 中看到：
- 请求访问日志（访问了哪个路径、来自哪个 IP）
- 鉴权失败日志（401）
- 任务操作日志（成功/失败、目标对象、消息）

常用排查命令：

```bash
docker compose logs -f hlm
```

如果你要看数据库里的历史操作，页面里也可以查看：
- `日志` 页面（操作日志）
- 仪表盘中的“近期执行记录”

---

## 🔍 常见问题 FAQ

### Q1：一个目录映射真的能支持多个任务吗？
能。你只要把任务拆成不同子目录即可。

### Q2：为什么我填宿主机路径不生效？
因为页面里需要填容器路径，例如 `/media/xxx`。

### Q3：提示路径不允许怎么办？
检查系统设置里的 `allowed_roots`，把 `/media` 加进去。

### Q4：我想要双目录映射可以吗？
可以。`docker-compose.yml` 里保留了注释示例，可按需启用。

---

## 🧑‍💻 开发调试（非 Docker）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

---

## 📁 项目结构

```text
app.py
core/
  models.py
  deps.py
  routes/
    web.py
    api.py
  services/
    hardlink_service.py
    delete_service.py
    backfill_service.py
    backup_service.py
templates/
scripts/
docker-compose.yml
docker-compose.prod.yml
```

---


### Q5：为什么出现“待确认（疑似误删）”列表？
说明本次删除联动命中的是风险匹配（名称/路径相似），系统已拦截自动删种。
请到“删除联动”页面手动确认或驳回，避免误删。

### Q6：如何关闭人工确认、恢复旧行为？
可以在“系统设置”把 `delete_match_strict_mode` 改为 `false`。
但不建议关闭，除非你非常确定命名规则稳定、不会混淆。


## 🧯 细节修正（2026-05-05）

- ✅ 硬链接任务启用/禁用已修复，切换立即生效。
- ✅ 设置中的 `default_exclude_dirs` 已真正接入任务新增/编辑默认值。
- ✅ `auto_clean_logs` 关闭后，定时日志清理会自动跳过并写入操作日志。
- ℹ️ 为避免误导，已移除未接线配置项 `delete_delay_seconds` 与 `notify_on_hardlink`。


## 🪵 日志分割与磁盘保护

- 应用日志新增滚动分割：默认单文件 `10MB`，保留 `5` 份。
- 可通过系统设置或环境变量调整：`APP_LOG_MAX_MB`、`APP_LOG_BACKUP_COUNT`。
- Compose 已新增 `./data/logs:/app/data/logs` 持久化映射，避免容器重建丢失日志。

## 🛰️ 版本检查与自动更新（可选）

- 设置页“版本与更新”可显示本地版本（`VERSION`）与 GitHub 最新 Release 版本。
- 支持手动“立即检查新版本”，代理统一在设置页“统一外网代理地址”配置。
- 不再提供重启自动更新，避免环境不确定性导致异常。


## 🗃️ 数据库升级与降级兼容策略

- 当前使用**版本化迁移**（`schema_meta.db_schema_version`），启动时自动按版本增量升级（幂等）。
- ✅ 支持向前升级：旧数据库可直接启动新版本，程序会自动补齐新增表/字段。
- ⚠️ 不支持自动反向降级（不会自动删字段/回滚结构），避免误删数据。
- 推荐降级流程：
  1. 升级前先做数据库备份；
  2. 若需回退版本，恢复升级前备份库；
  3. 再启动旧版本程序。
- 可在“系统诊断”页面查看当前 `db_schema_version` 与目标版本。

- 映射回填采用“源路径优先 + basename+文件大小”强匹配；`create_folder` 产生的目标目录层级不会作为强匹配依据，避免 `a.mkv -> a/a.mkv` 场景误判。
