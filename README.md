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

> 当前版本：`v0.2.4`（2026-05-07）

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

> 下面这份可以直接用。你只需要改这几项：
> 1) `SECRET_KEY`  2) `APP_USERNAME`  3) `APP_PASSWORD`  4) `/你的媒体目录`

```yaml
services:
  hlm:
    image: ghcr.io/marod1m/hlm-demo:latest
    container_name: hlm-demo
    restart: unless-stopped
    network_mode: bridge

    ports:
      - "5000:5000"

    environment:
      # 必改：应用密钥（建议至少32位随机字符串）
      SECRET_KEY: "请改成一个长随机字符串"

      # 建议：登录凭据（两项都填写才启用登录）
      APP_USERNAME: "admin"
      APP_PASSWORD: "123456"

      # 可选：时区/请求超时
      TZ: "Asia/Shanghai"
      REQUEST_TIMEOUT_SECONDS: "10"

      # 可选：日志行为
      ACCESS_LOG_ENABLED: "true"
      APP_LOG_MAX_MB: "10"
      APP_LOG_BACKUP_COUNT: "5"

      # 建议保留
      PYTHONUNBUFFERED: "1"

    volumes:
      # 必须：数据库与运行状态（不要删）
      - ./data/instance:/app/instance

      # 建议：数据库备份目录
      - ./data/backups:/app/data/backups

      # 建议：应用日志目录
      - ./data/logs:/app/data/logs

      # 必改：把左边改为你自己的媒体根目录
      - /你的媒体目录:/media

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

## 🧪 开发调试部署（重启自动拉代码）

适用于你这种“经常改代码、想快速验证”的场景。

### 行为说明

- `APP_DEV_MODE=true` 且 `APP_DEV_AUTO_PULL=true` 时：
  - 容器重启后自动拉取仓库最新代码
  - 可选：`requirements.txt` 变化时自动同步依赖
- 代理是容器内代理：**不要填 `127.0.0.1`**

### 开发调试 Compose 示例

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

      # ===== 开发模式 =====
      APP_DEV_MODE: "true"
      APP_DEV_AUTO_PULL: "true"
      APP_DEV_GIT_REPO: "https://github.com/MaroD1M/HLM-Demo.git"
      APP_DEV_GIT_BRANCH: "master"

      # requirements 变更自动同步（可选）
      APP_DEV_AUTO_PIP_SYNC: "true"
      APP_DEV_PIP_SYNC_TIMEOUT: "120"

      # 私有仓库可用（公开仓库留空）
      APP_DEV_GIT_TOKEN: ""

      # 开发代理（容器内生效）
      # 推荐：host.docker.internal:7890 或宿主机局域网IP:7890
      APP_DEV_PROXY_URL: ""
      APP_DEV_NO_PROXY: "localhost,127.0.0.1,::1"

    volumes:
      - ./data/instance:/app/instance
      - ./data/backups:/app/data/backups
      - ./data/logs:/app/data/logs
      - ./data/devsrc:/app-devsrc
      - /你的媒体目录:/media
```

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
检查三项：
1. `APP_DEV_MODE=true`
2. `APP_DEV_AUTO_PULL=true`
3. `APP_DEV_GIT_REPO` 地址可访问

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
