# Hardlink Manager

Hardlink Manager 是一个面向媒体下载场景的“定时扫描 + 安全联动”工具。它用于将下载目录中的媒体文件硬链接到目标目录，并在文件删除后联动处理下载器任务。

## 核心能力

- 任务“立即执行一次”
  - 硬链接任务、删除联动任务、Cron 计划任务均支持手动立即触发
  - 执行过程记录到任务执行历史（含状态、耗时、消息）

- 定时扫描硬链接（不依赖实时监听）
  - 支持扩展名白名单、黑名单
  - 支持排除目录
  - 支持单文件自动创建同名目录
  - 支持“最小文件年龄”避免处理下载中的文件
- 删除联动
  - 定时扫描删除事件（通过映射差异识别）
  - 支持冷却时间、单次删除阈值、Dry Run
  - 支持联动删除 qBittorrent 任务（可选删除文件）
- 映射回填
  - 定时回填 `文件 -> torrent hash` 映射，提升删除联动准确率
- 新增数据库备份任务：支持 Cron 定时备份与立即备份
- 多下载器配置（当前主实现 qBittorrent）
- Telegram 通知
  - 支持代理地址（如 `http://127.0.0.1:7890`）
  - 支持自定义 Telegram API Base
- 操作日志与系统配置

## 架构概览

```text
app.py                  # 应用入口、初始化、调度
core/
  deps.py               # 路由依赖对象
  routes/
    web.py              # 页面与表单路由
    api.py              # API路由
  services/
    hardlink_service.py # 硬链接扫描与建链
    delete_service.py   # 删除联动扫描
    backfill_service.py # 映射回填
templates/
  base.html             # 统一UI基座
  *.html                # 业务页面
```

## 快速开始

### 1) 安装依赖（推荐虚拟环境）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 2) 启动服务

```bash
.venv/bin/python app.py
```

默认监听 `0.0.0.0:5000`。

## 配置说明

### 环境变量

- `SECRET_KEY`：应用密钥（生产环境务必修改）
- `APP_USERNAME` / `APP_PASSWORD`：可选基础认证
- `REQUEST_TIMEOUT_SECONDS`：请求超时（默认 10）

### Web 设置项（重点）

- `allowed_roots`：允许访问根目录白名单（逗号分隔）
- `delete_files_with_torrent`：删种时是否删文件
- `delete_delay_seconds`：删除冷却基准
- `notify_on_hardlink` / `notify_on_delete`：通知开关
- `tg_proxy_url`：Telegram 代理（如 `http://127.0.0.1:7890`）
- `tg_api_base`：Telegram API 地址（默认 `https://api.telegram.org`）
- `backup_dir`：数据库备份目录（默认 `/app/data/backups`）
- `backup_keep_last`：备份保留份数（默认 `7`）

## API

- `GET /api/health`
- `GET /api/tasks/status`

## 测试与自检

```bash
.venv/bin/python -m py_compile app.py core/deps.py core/routes/*.py core/services/*.py
.venv/bin/python -m pytest -q tests/test_routes_smoke.py
```

## 兼容与安全说明

- 数据库使用 SQLite，启动时会执行轻量兼容迁移（新增字段）
- 所有 POST 接口启用 CSRF 校验
- 路径参数要求绝对路径，并可用 `allowed_roots` 进一步收敛访问范围

## 后续建议

- 将 `downloader` 抽象进一步扩展到 Transmission / aria2
- 增加“回填冲突人工确认”页面
- 执行历史增加按任务/状态筛选与导出


## 常用命令

```bash
make check        # 编译+测试+冒烟
make dev          # 启动服务
make format       # 代码格式化（需 black/isort）
make clean-cache  # 清理缓存
```


## Docker Compose 部署（推荐）

### 1) 准备配置文件

```bash
cp .env.example .env
```

编辑 `.env`：
- 必填：`SECRET_KEY`
- 建议：`APP_USERNAME` / `APP_PASSWORD`
- 媒体路径：`DOWNLOADS_PATH`、`LIBRARY_PATH`

### 2) 启动

```bash
docker compose up -d
```

### 3) 验证

```bash
docker compose ps
docker compose logs -f hlm
```

访问：`http://<你的IP>:${APP_PORT:-5000}`

### 持久化说明

本项目关键状态已持久化：
- `./data/instance -> /app/instance`：SQLite 数据库与运行状态
- `./data/backups -> /app/data/backups`：数据库备份文件
- `DOWNLOADS_PATH -> /downloads`：下载目录（任务源）
- `LIBRARY_PATH -> /library`：媒体库目录（任务目标）

容器重建后，配置和任务不会因数据库丢失而丢失。



## 生产部署（安全加固版）

项目提供 `docker-compose.prod.yml`，默认开启：
- 只读根文件系统（`read_only: true`）
- `tmpfs /tmp`
- `no-new-privileges`
- `cap_drop: ALL`
- 健康检查与日志轮转

### 1) 准备生产配置

```bash
cp .env.prod.example .env
# 编辑 .env，至少填写 SECRET_KEY / DOWNLOADS_PATH / LIBRARY_PATH
```

### 2) 启动

```bash
docker compose -f docker-compose.prod.yml up -d
```

### 3) 备份数据库

方式A（主机脚本）：
```bash
./scripts/backup.sh
```

方式B（Compose profile）：
```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm backup
```

默认保留最近 7 份备份。

### 4) 初始化辅助

```bash
./scripts/bootstrap.sh
```

会自动创建必要目录并检查 compose 配置。



## 依赖安全维护

项目已启用 Dependabot（`.github/dependabot.yml`）：
- 每周检查 Python 依赖（pip）
- 每周检查 GitHub Actions 依赖

建议流程：
1. 收到依赖升级 PR 后先执行 `make check`
2. 优先合并安全修复 PR
3. 对大版本升级先走预发验证



### 自动合并策略

已配置 Dependabot 自动合并策略：
- `pip` 与 `github-actions` 的 **patch/minor** 版本自动 squash 合并
- **major** 版本仍需人工审阅

对应文件：
- `.github/dependabot.yml`
- `.github/workflows/dependabot-automerge.yml`

