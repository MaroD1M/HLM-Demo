# HLM-Demo 部署与运维指南（v0.4.0）

本文档面向生产/实测部署，使用 Compose 启动 web、worker、scheduler 三个核心服务。

配套文档：

- [操作指南](USER_GUIDE.md)
- [架构说明](ARCHITECTURE.md)

## 1. 前置条件

- Docker Engine 24+ 与 Docker Compose v2。
- 建议本地磁盘/本地卷存放 `data/instance`，不要放 NFS/SMB。
- 媒体目录与下载目录必须能映射到同一容器路径体系下。
- 硬链接要求源目录和目标目录位于同一文件系统。

## 2. 目录结构

```text
hlm-demo/
  docker-compose.yml
  .env
  data/
    instance/
    backups/
    logs/
    recycle/
    media/
```

创建目录：

```bash
mkdir -p hlm-demo/data/{instance,backups,logs,recycle,media}
cd hlm-demo
```

## 3. 环境变量

复制示例：

```bash
cp /path/to/HLM-Demo/.env.example .env
```

至少修改：

```env
APP_PORT=5000
SECRET_KEY=请替换为长随机字符串
APP_USERNAME=
APP_PASSWORD=
MEDIA_ROOT=/你的媒体根目录
TZ=Asia/Shanghai
HLM_ALLOW_NO_AUTH=false
```

说明：

- `APP_USERNAME` / `APP_PASSWORD` 同时填写时使用环境变量账号。
- 两者留空时首次访问进入 `/setup`，需要初始化令牌。
- `SECRET_KEY` 建议显式设置；未设置时会持久化生成到 `instance/secret_key`。
- `SESSION_COOKIE_SECURE=true` 仅在 HTTPS 反向代理后启用。
- `TRUST_PROXY_HEADERS=true` 仅在可信反向代理后启用。

## 4. 启动

```bash
docker compose pull
docker compose up -d
```

服务角色：

| 服务 | 作用 |
|---|---|
| `hlm` | gunicorn web + API |
| `worker` | 消费 JobQueue，执行硬链接/删除/回填/备份 |
| `scheduler` | 独立 cron 调度 |
| `backup` | 可选备份 profile |

查看状态：

```bash
docker compose ps
docker compose logs -f hlm
docker compose logs -f worker
docker compose logs -f scheduler
```

健康检查：

```bash
curl http://127.0.0.1:5000/api/health
```

## 5. 首次初始化

1. 打开 `http://主机IP:5000`，会跳转 `/setup`。
2. 读取初始化令牌：

```bash
cat data/instance/setup_token.txt
```

3. 输入令牌，创建管理员用户名和密码。
4. 登录后在“开发与更新 -> 管理员安全”可改密、启用 TOTP 2FA。

如果明确接受无认证访问，可在 `.env` 设置：

```env
HLM_ALLOW_NO_AUTH=true
```

不建议在公网或不信任局域网中这样配置。

## 6. 反向代理示例（Nginx）

```nginx
server {
    listen 443 ssl http2;
    server_name hlm.example.com;

    ssl_certificate     /etc/letsencrypt/live/hlm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hlm.example.com/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

对应 `.env`：

```env
SESSION_COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
```

## 7. 备份与恢复

应用内：

- 系统诊断 -> 备份与恢复。
- 恢复前会校验 manifest 和 SHA256。
- 恢复时暂停 scheduler、检查运行任务、释放数据库连接。
- 恢复后建议重启容器。

命令行备份：

```bash
HLM_DB_PATH=./data/instance/hardlink_manager.db \
HLM_BACKUP_DIR=./data/backups \
bash scripts/backup.sh
```

Compose profile：

```bash
docker compose -f docker-compose.prod.yml --profile backup run --rm backup
```

注意：

- 备份文件权限为 `600`。
- 若使用自动生成的 `SECRET_KEY`，请同时保留 `instance/secret_key`，否则恢复后无法解密凭据。

## 8. 升级

```bash
docker compose pull
docker compose up -d
```

启动流程会：

1. 检查数据库 schema。
2. 需要升级时先备份到 `data/backups/migration-pre/`。
3. 执行内建迁移到 v16。
4. 修复越界映射、孤立待确认记录。
5. 迁移旧凭据加密格式。

升级前建议手动备份数据库与 `instance/secret_key`。

## 9. 开发/调试部署

开发模式通过页面“系统设置 -> 开发模式”控制，保存后重启容器生效。

开发模式会：

- 拉取配置的 Git 仓库/分支。
- `reset --hard` + `clean -fd` 强制对齐远端。
- 可选同步 requirements。
- 使用 `/app/instance` 作为统一实例目录。

## 10. API 快速验证

```bash
curl http://127.0.0.1:5000/api/health
curl http://127.0.0.1:5000/api/v1/openapi.json
curl http://127.0.0.1:5000/api/v1/metrics
```

如果设置了 `api_access_token`：

```bash
curl -H "X-API-Token: 你的令牌" http://127.0.0.1:5000/api/v1/jobs
```

创建后台任务：

```bash
curl -X POST http://127.0.0.1:5000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -H 'X-API-Token: 你的令牌' \
  -d '{"type":"hardlink","target_id":1}'
```

## 11. 常见问题

### 页面一直跳转 `/setup`
- 未创建管理员，且 `HLM_ALLOW_NO_AUTH=false`。
- 读取 `data/instance/setup_token.txt` 完成初始化。

### 手动执行没有进入后台
- worker 心跳不在有效期内。
- 检查 `docker compose ps` 和 worker 日志。
- 无 worker 时会回退为同步执行。

### cron 不执行
- 检查 `scheduler` 服务是否运行。
- web 生产模式默认关闭进程内 scheduler。

### 备份恢复后凭据无法解密
- 恢复时未保留对应 `SECRET_KEY` 或 `instance/secret_key`。

### SQLite database is locked
- 确认 `data/instance` 位于本地文件系统。
- 不要将 instance 放到 NFS/SMB。
- 保持 `GUNICORN_WORKERS=1`。

## 12. 测试

```bash
make check
make coverage
```

非 root 环境建议额外验证：

```bash
pytest -q tests
```

## 13. 构建镜像

本地构建：

```bash
bash scripts/build_image.sh
```

或手动：

```bash
docker build -t ghcr.io/marod1m/hlm-demo:v0.4.0 .
docker tag ghcr.io/marod1m/hlm-demo:v0.4.0 ghcr.io/marod1m/hlm-demo:latest
```

推送：

```bash
docker push ghcr.io/marod1m/hlm-demo:v0.4.0
docker push ghcr.io/marod1m/hlm-demo:latest
```

或打 tag 触发 GitHub Actions：

```bash
git tag v0.4.0
git push origin v0.4.0
```

CI 会构建 `linux/amd64` 与 `linux/arm64` 镜像并推送到 GHCR。
