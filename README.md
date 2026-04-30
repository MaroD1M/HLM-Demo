# 🚀 Hardlink Manager

> 硬链接管理神器，让文件管理变得超级简单！✨

---

## 🎯 功能亮点

### 🔗 硬链接管理
- 👀 **智能监控**：自动监控目录，新文件秒级创建硬链接
- 🎯 **精准过滤**：支持自定义扩展名过滤（.mkv, .mp4, .avi等）
- 📂 **智能分类**：自动创建文件夹结构，文件管理更有序
- 🚫 **智能排除**：自动跳过sample、subs等目录
- 🧠 **智能缓存**：记忆已处理文件，杜绝重复操作

### 🗑️ 删除监控
- 🔄 **联动删除**：监控删除事件，自动联动删除 qBittorrent 任务
- ⏱️ **防误删延迟**：可配置延迟，给你后悔的机会
- 📦 **文件清理**：支持删除种子时同步删除文件

### 🔔 通知系统
- 📱 **Telegram 通知**：硬链接创建、删除事件实时推送
- 🔔 **及时提醒**：不错过任何重要操作

### ⏰ 定时任务
- ⚙️ **Cron 支持**：灵活的定时执行计划
- ⚡ **快速预设**：每分钟、每小时、每天一键配置
- 🧹 **自动维护**：定期清理日志和缓存

### 🔒 安全特性
- 🔐 **AES 加密**：密码安全存储
- 📝 **完整日志**：所有操作有迹可循
- 🚨 **删除确认**：双重确认防止误操作

---

## 🛠️ 技术栈

| 技术 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 核心语言 |
| Flask | 3.1+ | Web框架 |
| SQLAlchemy | 3.1+ | 数据库 ORM |
| Watchdog | 6.0+ | 文件系统监控 |
| APScheduler | 3.10+ | 定时任务 |
| Bootstrap | 5.x | 现代 UI |
| Font Awesome | 6.x | 精美图标 |

---

## 🚀 快速开始

### 🐳 使用 Docker（推荐）

```bash
docker run -d \
  -p 5000:5000 \
  -v /path/to/data:/app \
  -e SECRET_KEY=your-secret-key-here \
  --name hardlink-manager \
  your-docker-username/hardlink-manager:latest
```

### 🖥️ 手动安装

```bash
# 克隆项目
git clone https://github.com/your-repo/hardlink-manager.git
cd hardlink-manager

# 安装依赖（建议使用虚拟环境）
pip install -r requirements.txt

# 设置密钥（重要！）
export SECRET_KEY=your-super-secret-key

# 启动应用
python app.py
```

---

## ⚙️ 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SECRET_KEY` | 安全密钥（务必修改！） | `default-secret-key-for-dev-only` |

### Web 界面设置

通过设置页面可以轻松配置：
- 📊 日志保留天数
- 🧹 自动清理日志开关
- 📁 默认文件扩展名
- 🚫 默认排除目录
- ⚠️ 删除确认延迟

---

## 🌐 API 接口

### 健康检查
```http
GET /api/health
```

### 任务状态
```http
GET /api/tasks/status
```

---

## 📁 项目结构

```
hardlink-manager/
├── app.py                 # 🎯 主应用文件
├── requirements.txt       # 📦 依赖列表
├── Dockerfile             # 🐳 Docker构建配置
├── .github/workflows/
│   └── docker-build.yml   # 🚀 CI/CD工作流
└── templates/             # 🎨 HTML模板
    ├── dashboard.html     # 📊 仪表盘
    ├── hardlink.html      # 🔗 硬链接管理
    ├── delete_monitor.html# 🗑️ 删除监控
    ├── downloader.html    # 📥 下载器管理
    ├── notifier.html      # 🔔 通知器管理
    ├── cron.html          # ⏰ 定时任务
    ├── logs.html          # 📝 操作日志
    └── settings.html      # ⚙️ 设置页面
```

---

## 📖 使用指南

1. **添加下载器** 📥
   - 进入下载器管理页面
   - 填写 qBittorrent 连接信息

2. **添加通知器** 🔔
   - 进入通知器管理页面
   - 添加 Telegram Bot 信息

3. **创建硬链接任务** 🔗
   - 设置源目录、目标目录
   - 配置过滤规则

4. **创建删除监控** 🗑️
   - 设置监控目录
   - 关联下载器和通知器

---

## 🔒 安全小贴士

1. **🔑 SECRET_KEY**：生产环境务必使用强随机密钥
2. **💾 备份数据库**：定期备份 `hardlink_manager.db`
3. **🚪 访问控制**：建议使用 Nginx 反向代理添加认证
4. **🚫 敏感信息**：不要在代码中硬编码密码

---

## 📝 更新日志

### v1.0.0 🎉
- ✨ 初始版本发布
- 🔗 硬链接自动创建
- 🗑️ 删除监控联动
- 🔔 Telegram 通知
- ⏰ 定时任务

---

## 📄 许可证

MIT License - 自由使用，欢迎贡献！

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！🎉

如果你喜欢这个项目，别忘了给个 ⭐ 哦！

---

*Made with ❤️ for file management lovers*