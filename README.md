# MikuBot

基于 **NoneBot2** 框架的 QQ 群机器人。

## 功能特性

- **截图引擎**：使用系统 **Edge** 浏览器渲染 HTML 模板生成图片（Playwright `channel="msedge"`），无需下载 Chromium
- **YAML 配置管理**：`config/bot.yaml` 统一管理所有插件配置，支持热加载
- **依赖自动检测**：启动前自动检测 `.venv` 依赖，缺失则自动安装
- **首次配置向导**：终端交互式输入管理员 QQ 号和密码
- **自定义日志系统**：ANSI 高亮、消息截断、心跳过滤、文件轮转
- **版本管理 & 自动更新**：从 GitHub 检查新版本，一键下载更新并重启

---

## 项目结构

```
miku_bot/
├── bot.py                          # 入口
│   ├── 自定义日志系统（ANSI 高亮、消息截断、心跳过滤）
│   ├── 插件关键词自动发现
│   ├── 交互式配置向导（首次启动）
│   ├── 依赖检测（utils.check_deps.check_all）
│   └── 缓存清理任务
├── start.bat                       # Windows 启动脚本
├── start.sh                        # Linux/Mac/Git Bash 启动脚本
├── .env                            # 环境变量（SUPERUSERS、WEBUI_PASSWORD）
├── .env.dev / .env.prod            # 开发/生产环境
├── requirements.txt                # Python 依赖
├── pyproject.toml                  # NoneBot 项目配置
│
├── plugins/                         # 已激活插件
│   └── miku_admin/                  # 管理员插件
│       ├── 重启 Bot
│       ├── 配置检查（查看 SUPERUSERS、密码、端口、已注册插件）
│       ├── 刷新配置（热加载 bot.yaml）
│       └── 启动通知（私聊超级用户）
│
├── plugins_index/                   # 插件索引目录（存放所有可选插件，按需启用）
│
├── utils/                           # 底层工具
│   ├── screenshot.py                # 截图引擎（Edge / HTML→图片）
│   ├── check_deps.py                # 依赖检测 + 自动安装
│   ├── deps_config.py               # 依赖配置（从 requirements.txt 解析）
│   ├── config_manager.py            # YAML 配置管理（bot.yaml）
│   ├── version_manager.py           # 版本管理（GitHub 版本检查）
│   ├── updater.py                   # 自动更新（下载 + 覆盖 + 重启）
│   ├── html_render.py               # HTML 模板渲染
│   ├── cache_cleanup.py             # 缓存图片定时清理
│   └── templates/                   # HTML 模板
│       ├── ping.html                # 状态卡片
│       └── weather.html             # 天气卡片
│
├── version.json                     # 版本信息（自动维护）
│
├── config/                          # 配置文件
│   └── bot.yaml                     # 插件统一配置（YAML 格式）
├── data/                            # 数据目录
│   └── images/                      # 截图输出
├── logs/                            # 日志目录
│   └── bot_YYYY-MM-DD.log           # 按天轮转日志
└── ...
```

---

## 快速启动

### 前提

- 系统已安装 **Microsoft Edge**（Windows 10/11 默认已安装）
- 系统已安装 **Python 3.10+**

### 方式一：双击 start.bat（Windows）

```
D:\qqbot\miku_bot\start.bat
```

脚本自动完成：
1. 检测 `.venv` 虚拟环境
2. 检测依赖（缺失则自动 `pip install`）
3. 检测 Edge 浏览器
4. 启动 Bot

首次启动会弹出配置向导：

```
==================================================
         MikuBot 首次配置向导
==================================================
[步骤 1/2] 设置管理员 QQ 号
  请输入你的 QQ 号: 123456789
  [OK] 管理员 QQ 号已设置为: 123456789

[步骤 2/2] 设置 WebUI 登录密码
  WebUI 直接密码登录，不需要账号/用户名
  请输入密码: MySecret
  [OK] WebUI 密码已设置
```

### 方式二：命令行启动

```bash
# Windows cmd
cd D:\qqbot\miku_bot
.venv\Scripts\python.exe bot.py

# Git Bash
cd /d/qqbot/miku_bot
bash start.sh
```

---

## 配置管理

### 双配置体系

| 文件 | 用途 | 修改方式 |
|------|------|----------|
| `.env` | 环境变量（SUPERUSERS、WEBUI_PASSWORD、端口） | 首次启动向导自动填写，或手动编辑 |
| `config/bot.yaml` | 插件运行时配置（按插件分区） | 发送「刷新配置」热加载，或手动编辑后 reload |

### 插件配置注册（代码示例）

```python
from utils.config_manager import config_manager

# 插件首次加载时注册自己的配置区
_ADMIN_TEMPLATE = (
    "\n"
    "miku_admin:\n"
    "  notify_on_start: true\n"
    "  admin_commands:\n"
    "  - 重启\n"
    "  - 配置检查\n"
)

config_manager.register_plugin(
    "miku_admin",
    defaults={"notify_on_start": True, "admin_commands": ["重启", "配置检查"]},
    template_str=_ADMIN_TEMPLATE,
    description="管理员插件配置",
)

# 读取配置
cfg = config_manager.get_plugin_config("miku_admin")
notify = cfg.get("notify_on_start", True)

# 修改并保存
config_manager.set("miku_admin", "notify_on_start", False)
```

### 热加载

在 QQ 中发送（超级用户）：

```
刷新配置
```

Bot 重新读取 `config/bot.yaml`，无需重启。

---

## 可用指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `重启` | 超级用户 | `os.execv` 替换当前进程，重新加载所有配置和插件 |
| `配置检查` | 超级用户 | 查看 SUPERUSERS、WEBUI_PASSWORD、监听端口、已注册插件配置区 |
| `刷新配置` | 超级用户 | 热加载 `config/bot.yaml`，不重启 Bot |
| `检查更新` | 超级用户 | 从 GitHub 检查是否有新版本 |
| `立即更新` | 超级用户 | 下载最新代码并自动重启 Bot |

---

## 截图引擎

使用系统 **Edge** 浏览器（Playwright `channel="msedge"`），无需下载 Chromium。

### 代码示例

```python
from utils.html_render import render_template
from utils.screenshot import screenshot_html
from nonebot.adapters.onebot.v11 import MessageSegment

# 渲染 HTML 模板
html = render_template("ping", nickname="小明", user_id=123)

# Edge 截图 → 图片
img_path = await screenshot_html(html, width=600, height=400)

# 发送图片
await cmd.finish(MessageSegment.image(str(img_path)))
```

### 截图 API

```python
from utils.screenshot import screenshot_url, screenshot_html

# 截网页（Edge 渲染）
img = await screenshot_url("https://bilibili.com", full_page=True)

# 截 HTML 字符串（Edge 渲染）
img = await screenshot_html("<h1>Hello</h1>", width=500, height=300)
```

### 缓存机制

- 相同 HTML 内容 60 秒内直接复用图片，避免重复渲染
- 环境变量 `MIKU_SCREEN_CACHE` 控制缓存时间（默认 60 秒）
- 环境变量 `MIKU_DPR` 控制渲染倍率（默认 1.5）

---

## 日志系统

Bot 启动时自动替换 NoneBot 默认日志为自定义格式：

- **ANSI 高亮**：时间（暗绿）、等级（彩色）、模块名（青）、指令关键词（亮蓝）
- **消息截断**：过长 dict 事件自动截断到 300 字符，保留关键信息
- **心跳过滤**：自动丢弃 `notice.notify.input_status` 等高频事件
- **文件轮转**：按天保存到 `logs/bot_YYYY-MM-DD.log`，保留 30 天

---

## 连接 QQ

在协议端（NapCat / LLOneBot / Lagrange）配置反向 WebSocket：

```
ws://127.0.0.1:3108/onebot/v11/ws
```

---

## 版本管理与自动更新

### 检查更新

在 QQ 中发送（超级用户）：

```
检查更新
```

Bot 自动连接 GitHub 检查最新版本：

```
🎵 发现新版本！
━━━━━━━━━━━━
当前版本: 0.1.0
最新版本: 0.2.0 (abc12345)
更新时间: 2026-06-20T10:00:00
更新说明: 新增天气插件、优化截图速度...
━━━━━━━━━━━━
发送「立即更新」开始下载更新
```

如果已是最新：

```
🎵 MikuBot 已是最新版
━━━━━━━━━━━━
当前版本: 0.1.0
Commit: abc12345
更新时间: 2026-06-18T12:00:00
上次检查: 2026-06-19T08:00:00
━━━━━━━━━━━━
仓库: https://github.com/aikun-China/Miku_bot
```

### 立即更新

```
立即更新
```

Bot 自动下载最新代码并重启：

```
🎵 更新完成！
━━━━━━━━━━━━
更新文件: bot.py, requirements.txt
更新目录: utils, plugins
━━━━━━━━━━━━
🎵 Miku 正在重启应用更新...
```

### 更新策略

- **保留用户数据**：`.env`、`config/`、`data/`、`logs/`、`.venv/` 不会被覆盖
- **更新核心代码**：`bot.py`、`utils/`、`plugins/`、`start.bat` 等
- **更新后自动重启**：下载完成后自动 `execv` 重启 Bot

### 版本信息文件

`version.json`（自动维护）：

```json
{
  "version": "0.1.0",
  "commit_hash": "abc12345",
  "update_time": "2026-06-18T12:00:00",
  "github_repo": "aikun-China/Miku_bot",
  "check_interval_hours": 24,
  "last_check_time": "2026-06-19T08:00:00"
}
```

---

## 分发部署

项目文件夹（含 `.venv`）可直接打包发给其他人：

1. 所有依赖安装在项目目录 `.venv` 中，不依赖系统 Python
2. 双击 `start.bat` 即可自动检测并启动
3. 首次启动会弹出交互式配置向导
4. 截图使用系统 Edge，无需额外下载浏览器

---

## 开发计划

- [ ] 插件索引市场（plugins_index 扩展）
- [ ] 接入 AI 对话，渲染对话卡片
- [ ] 数据库 + 签到/好感度系统
- [ ] WebUI 管理面板（密码登录）
