# Hardlink Manager

一个功能强大的硬链接管理工具，支持自动化硬链接创建、删除监控联动下载器任务删除，提供友好的Web界面管理。

## 功能特性

### 硬链接管理
- 监控自定义目录，自动为新文件创建硬链接
- 支持自定义目标目录
- 可选择是否为硬链接文件创建文件夹结构
- 支持常见媒体文件扩展名过滤（.mkv, .mp4, .avi, .mov等）
- 支持自定义排除目录（如sample, subs）
- 缓存记录已硬链接的文件，防止重复创建

### 删除监控
- 监控目录删除事件
- 联动删除qBittorrent任务
- 支持文件删除和目录删除事件监控
- 可配置删除延迟，防止误删
- 支持删除种子时同时删除文件

### 通知系统
- Telegram通知支持
- 硬链接创建通知
- 删除事件通知

### 定时任务
- 支持 cron 表达式设置执行计划
- 提供常用时间计划预设（每分钟、每小时、每天等）
- 支持自定义 cron 表达式
- 可执行任务类型：批量创建硬链接、清理日志、清理缓存

### 安全特性
- 下载器密码使用AES加密存储（基于SECRET_KEY）
- 完整的操作日志记录
- 删除操作需要确认

## 技术栈

- Python 3.11+
- Flask 3.1+
- Flask-SQLAlchemy 3.1+
- Flask-Bcrypt 1.0+
- cryptography 42+
- Watchdog 6.0+
- requests 2.33+
- python-telegram-bot 22.7+
- APScheduler 3.10+
- Bootstrap 5

## 快速开始

### 使用Docker

```bash
docker run -d \
  -p 5000:5000 \
  -v /path/to/data:/app \
  -e SECRET_KEY=your-secret-key-here \
  --name hardlink-manager \
  your-docker-username/hardlink-manager:latest
```

### 手动安装

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export SECRET_KEY=your-secret-key-here

# 启动应用
python app.py
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| SECRET_KEY | Flask会话和密码安全密钥 | default-secret-key-for-dev-only |

### 应用设置

通过Web界面的设置页面可以配置：

- 日志保留天数
- 自动清理日志
- 默认文件扩展名
- 默认排除目录
- 删除种子时是否同时删除文件
- 删除确认延迟（秒）
- 硬链接创建通知开关
- 删除通知开关

## API接口

### 健康检查

```
GET /api/health
```

### 任务状态

```
GET /api/tasks/status
```

## 项目结构

```
.
├── app.py                 # 主应用文件
├── requirements.txt       # 依赖列表
├── Dockerfile             # Docker构建文件
├── .github/
│   └── workflows/
│       └── docker-build.yml  # GitHub Actions工作流
└── templates/             # HTML模板
    ├── dashboard.html     # 仪表盘
    ├── hardlink.html      # 硬链接管理
    ├── delete_monitor.html # 删除监控
    ├── downloader.html    # 下载器管理
    ├── notifier.html      # 通知器管理
    ├── cron.html          # 定时任务管理
    ├── logs.html          # 操作日志
    └── settings.html      # 设置页面
```

## 使用说明

1. **添加下载器**: 在下载器管理页面添加qBittorrent连接信息
2. **添加通知器**: 在通知器管理页面添加Telegram Bot信息
3. **创建硬链接任务**: 设置源目录、目标目录和过滤规则
4. **创建删除监控任务**: 设置监控目录并关联下载器和通知器

## 安全注意事项

1. **SECRET_KEY**: 务必在生产环境中设置一个安全的随机密钥
2. **数据库备份**: 定期备份`hardlink_manager.db`数据库文件
3. **访问控制**: 建议通过反向代理（如Nginx）添加访问控制
4. **敏感信息**: 不要在代码中硬编码敏感信息

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！