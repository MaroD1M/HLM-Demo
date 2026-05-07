# 🎬 HLM-Demo

<div align="center">

# 🔗 Hardlink Manager · 媒体硬链接自动化中心

**让下载目录自动入库，让删除联动可控可靠。**  
**兼顾日常自用部署与二次开发调试。**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Web-black?style=flat-square&logo=flask)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

</div>

> 当前版本：`v0.2.4`（2026-05-07）

---

## ✨ 你能得到什么

- 🧠 **多任务管理**：电影/剧集/动漫等可以分任务并行管理
- 🔁 **硬链接自动化**：定时或手动执行，减少重复操作
- 🧹 **删除联动可控**：冷却、阈值、试运行、通知，降低误删风险
- 🛡️ **待判定保护**：映射重建窗口期保守处理，避免误删种
- 📦 **映射与缓存面板**：支持筛选、重试、批量清理
- 🪵 **日志 + 诊断**：出问题更容易定位

---

## 🗺️ 页面导览（第一次使用建议先看）

- 🏠 **仪表盘**：看全局状态、近期执行、快捷入口
- 🔗 **硬链接任务**：设置源目录、目标目录、扩展名、删除联动
- 🧹 **疑似误删处理**：处理高风险待确认记录
- 🧭 **映射与缓存**：查看映射关系、重试关联、清理缓存
- ⏰ **定时任务**：统一调度执行
- ⚙️ **系统设置**：路径白名单、通知、版本检查、备份参数
- 🔍 **系统诊断 / 日志**：排查配置和运行问题

---

## 🚀 快速开始（适合日常自用）

下面这份 Compose 可以直接用。  
你只需要改 **4 处**：

1. `SECRET_KEY`  
2. `APP_USERNAME`  
3. `APP_PASSWORD`  
4. 宿主机媒体目录（`/你的媒体目录`）

### 1）新建项目目录并进入

```bash
mkdir -p hlm-demo && cd hlm-demo
```

### 2）创建 `docker-compose.yml`

把下面内容完整复制到 `docker-compose.yml`：

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
      # 必改：应用密钥（请换成你自己的随机字符串）
      SECRET_KEY: "请改成一个长随机字符串"

      # 建议：登录账号密码（两项都填写才启用登录）
      APP_USERNAME: "admin"
      APP_PASSWORD: "123456"

      # 可选：时区
      TZ: "Asia/Shanghai"

      # 可选：请求超时（秒）
      REQUEST_TIMEOUT_SECONDS: "10"

      # 可选：日志控制
      ACCESS_LOG_ENABLED: "true"
      APP_LOG_MAX_MB: "10"
      APP_LOG_BACKUP_COUNT: "5"

      # 固定建议
      PYTHONUNBUFFERED: "1"

    volumes:
      # 数据库与运行状态（必须保留）
      - ./data/instance:/app/instance

      # 备份目录（建议保留）
      - ./data/backups:/app/data/backups

      # 应用日志目录（建议保留）
      - ./data/logs:/app/data/logs

      # 必改：把左侧路径改成你自己的媒体根目录
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

### 3）启动

```bash
docker compose up -d
```

### 4）访问

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://你的主机IP:5000`

---

## 🧪 开发调试部署（适合需要频繁改代码）

这个模式适合你“重启就拉最新代码”的调试场景，不用每次都打版本构建镜像。

### 开发模式关键点

- 开启后，容器重启会自动拉取指定仓库/分支最新代码
- 可选：`requirements.txt` 变化时自动同步依赖
- 可选：通过代理拉取 GitHub（容器内代理，**不要写 127.0.0.1**）

### 开发模式 Compose 示例

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

      # requirements 变化时自动安装依赖
      APP_DEV_AUTO_PIP_SYNC: "true"
      APP_DEV_PIP_SYNC_TIMEOUT: "120"

      # 私有仓库可用（公开仓库可留空）
      APP_DEV_GIT_TOKEN: ""

      # 代理（容器内生效）
      # 建议填 host.docker.internal:7890 或宿主机局域网IP:7890
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

## 📁 路径填写规则（非常重要）

页面里填写路径时，填的是**容器路径**，不是宿主机路径。

- ✅ 正确：`/media/downloads/movie`
- ❌ 错误：`/volume1/media/downloads/movie`

---

## 🧩 推荐任务模板（可直接照抄）

- 电影任务：
  - 源目录：`/media/downloads/movie`
  - 目标目录：`/media/library/movie`

- 剧集任务：
  - 源目录：`/media/downloads/tv`
  - 目标目录：`/media/library/tv`

- 动漫任务：
  - 源目录：`/media/downloads/anime`
  - 目标目录：`/media/library/anime`

---

## 🔐 登录与账号密码机制

- 同时设置 `APP_USERNAME` 和 `APP_PASSWORD`：启用登录页
- 任一为空：不强制登录，直接进入主界面
- 修改账号密码：改 Compose 里的环境变量后重启容器即可
- 账号密码**不写入数据库**

---

## 🛡️ 生产使用建议

- 使用强随机 `SECRET_KEY`
- 仅在内网开放端口
- 定期备份 `./data/instance` 与 `./data/backups`
- 删除联动建议保持保守策略（严格匹配 + 风险通知开启）

---

## ❓常见问题

### Q1：为什么访问 `/login` 会跳回首页？
因为没同时设置账号和密码，系统默认不强制登录。

### Q2：自动拉取代码没生效？
检查是否同时满足：
- `APP_DEV_MODE=true`
- `APP_DEV_AUTO_PULL=true`
- `APP_DEV_GIT_REPO` 正确

### Q3：我配了代理还是拉不下来？
确认代理地址是容器可访问地址，别写 `127.0.0.1`。  
建议改为 `host.docker.internal:端口` 或宿主机局域网 IP。

### Q4：导入/导出配置会不会包含口令？
导出会隐藏关键口令；导入只覆盖支持的配置项。

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
