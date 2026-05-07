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

> 当前版本：`v0.2.4`（2026-05-07）

---

## 🌟 核心能力

- 多任务硬链接（电影/剧集/动漫等可并行管理）
- 删除联动（冷却、阈值、试运行、通知）
- 待判定来源保护（避免映射重建窗口期误判）
- 映射与缓存管理（批量操作、重试回填、失败计数）
- 版本检查与数据库迁移（支持升级前备份）
- 本地静态资源（无外链 CSS/字体依赖）

---

## 🖼️ 页面说明

- 仪表盘：总览状态、近期执行、快捷入口
- 硬链接任务：新建/编辑任务与删除联动策略
- 疑似误删处理：处理高风险待确认删除
- 映射与缓存：回填、筛选、批量清理
- 定时任务：统一调度管理
- 系统设置：路径白名单、代理、通知、版本检查等
- 系统诊断/日志：排查运行与配置问题

---

## 🚀 快速开始（Docker Compose）

### 1）复制配置

```bash
cp .env.example .env
```

### 2）修改关键项

- `SECRET_KEY`（必填）
- `MEDIA_ROOT`（必填，宿主机媒体根目录）
- `APP_USERNAME` / `APP_PASSWORD`（可选；两者都设置才启用登录）
- `APP_DEV_MODE` / `APP_DEV_AUTO_PULL`（可选；开发模式自动拉取）
- `APP_DEV_PROXY_URL`（可选；开发模式拉取/依赖同步代理，容器内生效）

> 登录机制说明：
> - 同时设置 `APP_USERNAME` 与 `APP_PASSWORD`：启用登录页认证
> - 任一为空：不强制登录，直接进入主界面
> - 修改账号密码只需改环境变量并重启容器，不写入数据库

### 3）初始化并启动

```bash
./scripts/bootstrap.sh
docker compose up -d
```

### 4）访问

- 本机：`http://127.0.0.1:5000`
- 局域网：`http://你的主机IP:5000`

---

## 🧩 目录映射最佳实践

推荐只映射一个媒体总目录到容器 `/media`，任务里按子目录拆分：

- 电影：`/media/downloads/movie -> /media/library/movie`
- 剧集：`/media/downloads/tv -> /media/library/tv`
- 动漫：`/media/downloads/anime -> /media/library/anime`

优点：新增任务不改 Compose、结构统一、维护简单。

---

## 🛡️ 安全与运维建议

- 使用强随机 `SECRET_KEY`
- 建议内网访问，避免公网裸露
- 定期备份 `./data/instance` 与 `./data/backups`
- 风险场景建议保留：
  - `delete_match_strict_mode=true`
  - `notify_on_risky_delete=true`

---

## 🧯 常见问题

### Q1：为什么我访问不到登录页？
未同时设置 `APP_USERNAME` 与 `APP_PASSWORD` 时，系统默认不强制登录。

### Q2：忘记账号密码怎么办？
直接修改环境变量中的 `APP_USERNAME` / `APP_PASSWORD`，重启后生效。

### Q3：页面里应该填宿主机路径还是容器路径？
填容器路径（例如 `/media/...`），不是宿主机路径（例如 `/volume1/...`）。

### Q4：出现“待判定”是什么意思？
表示来源尚在回填确认窗口，删除联动会保守处理，避免误删。

---

## 🧑‍💻 本地开发

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
static/
scripts/
docker-compose.yml
docker-compose.prod.yml
```
