# 🎬 HLM-Demo

<div align="center">

# 🔗 Hardlink Manager · 媒体硬链接自动化中心

**让下载目录自动入库，让删除联动可控可靠。**  
**同时支持日常稳定使用与开发调试。**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Web-black?style=flat-square&logo=flask)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

</div>

> 当前版本：`v0.2.11`（2026-05-21）

---


## 🆕 最近更新说明（v0.2.11）

### 主要更新
- 任务执行链路增强：新增执行 ID（execution_id）贯穿任务执行与操作日志，便于按单次任务追踪问题。
- 日志页面增强：支持按执行 ID 筛选，并在常用快捷筛选与分页场景保留执行 ID 参数。
- 仪表盘新增运行质量指标：近 20 次成功率、平均耗时、P95 耗时与失败类型聚合。
- 数据库迁移能力模块化：迁移逻辑下沉到 `core/services/migration_service.py`，应用层职责更清晰。
- 回填/删除链路查询优化：补充索引与筛选优化，降低中等数据量下的查询压力。
- 工程体验优化：`Makefile` 默认 `test` 改为跑 `tests/` 全量回归。

### 对普通用户的影响
- 日常部署方式不变，仍是开箱即用。
- 只需修改少量基础信息即可运行：`SECRET_KEY`、登录账号密码、媒体目录映射。
- 不使用开发模式时可保持默认关闭，不影响常规功能。

---

## 🎯 适用人群

- **日常使用者**：希望开箱即用、少折腾，复制 Compose 后只改少量个人信息即可启动。
- **开发调试者**：希望重启即拉取最新代码，减少频繁打包构建成本。

---

## ✨ 主要能力

- 🧠 多任务硬链接：电影/剧集/动漫等独立任务管理
- 🔁 自动化执行：手动执行 + 定时 Cron 双模式
- 🧹 删除联动：支持冷却、阈值、试运行、通知
- 🛡️ 待判定保护：减少映射重建窗口期误删风险
- 📦 映射/缓存管理：筛选、重试、批量清理
- 🪵 日志与诊断：方便定位配置与运行问题

---

## 🗺️ 页面说明（建议先读）

- 🏠 **仪表盘**：总览状态、近期执行、快捷入口
- 🔗 **硬链接任务**：源目录/目标目录/扩展名/删除联动
- 🧹 **疑似误删处理**：处理待确认的风险删除记录
- 🧭 **映射与缓存**：查看来源、重试关联、批量管理
- ⏰ **定时任务**：统一调度入口
- ⚙️ **系统设置**：白名单、通知、版本检查、备份
- 🔍 **系统诊断/日志**：问题排查入口

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
      APP_PASSWORD: "123456"
      TZ: "Asia/Shanghai"
      PYTHONUNBUFFERED: "1"

    volumes:
      - ./data/instance:/app/instance
      - ./data/backups:/app/data/backups
      - ./data/logs:/app/data/logs
      - /你的媒体目录:/media
```

#### 可选增强（进阶用户）

如果你明确知道自己在做什么，再加这些：
- `healthcheck`：用于平台健康状态探测。
- `logging`：自定义日志滚动策略。

> 对群晖用户：默认建议先**不加 logging 配置**，优先使用容器管理器的默认日志展示，避免出现“看不到日志”或日志行为不符合预期。


### 第 3 步：启动

```bash
docker compose up -d
```

### 第 4 步：访问

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://你的主机IP:5000`

### 第 5 步：首次建议操作

1. 先创建一个测试硬链接任务，验证路径与命名规则。  
2. 确认无误后再创建生产目录任务。  
3. 删除联动先开启“试运行”，观察日志后再切正式执行。

---

## 🧪 开发调试部署（页面配置优先）

适用于“经常改代码、需要快速验证”的场景。

### 配置优先级（重要）

- **页面配置（系统设置 -> 开发模式）优先**
- **Compose 环境变量仅作为启动兜底**
- 生效顺序：**页面配置（`instance/dev_runtime.env`） > `APP_DEV_*` 兜底**
- 保存开发模式设置后，容器下次手动重启时会加载页面配置。

建议保留最小兜底变量，避免首次启动或页面配置异常时出现“无代理无法拉取”。

### 开发模式最小兜底 Compose 示例

```yaml
services:
  hlm:
    image: ghcr.io/marod1m/hlm-demo:latest
    container_name: hlm-demo-dev
    restart: unless-stopped
    network_mode: bridge

    ports:
      - "5000:5000"

    environment:
      SECRET_KEY: "请改成一个长随机字符串"
      APP_USERNAME: "admin"
      APP_PASSWORD: "123456"
      TZ: "Asia/Shanghai"
      PYTHONUNBUFFERED: "1"

      # ===== 开发模式兜底（建议保留） =====
      APP_DEV_MODE: "true"

      # 代理兜底（容器内生效，避免首次启动拉取失败）
      # 推荐：host.docker.internal:7890 或宿主机局域网IP:7890
      APP_DEV_PROXY_URL: "http://host.docker.internal:7890"
      APP_DEV_NO_PROXY: "localhost,127.0.0.1,::1"

    volumes:
      - ./data/instance:/app/instance
      - ./data/backups:/app/data/backups
      - ./data/logs:/app/data/logs
      - ./data/devsrc:/app-devsrc
      - /你的媒体目录:/media
```

### 页面里配置的推荐项（系统设置 -> 开发模式）

建议在页面中维护这些项（更友好，也更安全）：

- `dev_auto_pull`
- `dev_git_repo`
- `dev_git_branch`
- `dev_auto_pip_sync`
- `dev_pip_sync_timeout`
- `dev_git_token`（脱敏显示，留空保持，支持显式清空）
- `dev_proxy_url`
- `dev_no_proxy`

### 首次开箱流程（开发模式）

1. 用上面的最小兜底 Compose 启动容器。  
2. 打开“系统设置 -> 开发模式”，填写仓库/分支/自动拉取等参数并保存。  
3. 保存开发模式设置后，手动重启容器；重启后优先加载 `instance/dev_runtime.env` 页面配置。

### 代理说明（避免踩坑）

- 容器内不要把代理写成 `127.0.0.1`（那是容器自己）。
- 推荐使用：
  - `host.docker.internal:端口`，或
  - 宿主机局域网 IP:端口

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

## 🔐 登录机制（务必理解）

- 同时设置账号和密码：启用登录页
- 任一为空：不强制登录
- 修改账号密码：改 Compose 后重启容器
- 账号密码不写入数据库

---

## 🧯 常见问题

### Q1：为什么我访问 `/login` 会回首页？
因为你没有同时设置 `APP_USERNAME` 和 `APP_PASSWORD`。

### Q2：为什么自动拉取没生效？
优先检查“系统设置 -> 开发模式”里的页面配置：
1. `dev_mode=true`
2. `dev_auto_pull=true`
3. `dev_git_repo` 地址可访问

若页面未配置，再检查 Compose 兜底变量是否正确。

### Q3：代理已经配置，为什么还拉不到？
你可能用了 `127.0.0.1`。容器里请改为：
- `host.docker.internal:端口` 或
- 宿主机局域网 IP:端口

### Q4：导入/导出会泄露关键口令吗？
导出会隐藏关键口令；导入仅覆盖支持的配置项。

---

## 🧑‍💻 本地开发（非 Docker）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

---

## 📦 项目结构

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
static/
scripts/
docker-compose.yml
docker-compose.prod.yml
```
