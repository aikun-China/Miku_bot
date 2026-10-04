# MikuBot

基于 **NoneBot2** 框架的 QQ 群机器人。

## 功能特性

- **截图引擎**：使用系统 **Edge** 浏览器渲染 HTML 模板生成图片（Playwright `channel="msedge"`），无需下载 Chromium
- **YAML 配置管理**：`config/bot.yaml` 管理插件配置，`config/ai_models.yaml` 独立管理全局 AI 模型连接
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
├── requirements.txt                # Python 依赖
├── pyproject.toml                  # NoneBot 项目配置
├── uv.lock                         # 锁定的依赖版本
│
├── plugins/                         # 全部插件（管理、娱乐、订阅等）
│   └── miku_admin/                  # 管理员插件
│       ├── 重启 Bot
│       ├── 配置检查（查看 SUPERUSERS、密码、端口、已注册插件）
│       ├── 刷新配置（热加载 bot.yaml）
│       └── 启动通知（私聊超级用户）
│
├── utils/                           # 底层工具
│   ├── screenshot.py                # 截图引擎（Edge / HTML→图片）
│   ├── check_deps.py                # 依赖检测 + 自动安装
│   ├── deps_config.py               # 依赖配置（从 requirements.txt 解析）
│   ├── config_manager.py            # YAML 配置管理（bot.yaml）
│   ├── ai_model_config.py           # 全局 AI 模型配置及旧配置迁移
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
├── config/                          # 本地运行配置（首次启动时生成，不提交）
├── data/                            # 本地用户数据和缓存（不提交）
├── logs/                            # 本地运行日志（不提交）
└── ...
```

---

## 快速启动

### 前提

- 系统已安装 **Microsoft Edge**（Windows 10/11 默认已安装）
- 系统已安装 **Python 3.10+**
- 已安装 [uv](https://docs.astral.sh/uv/)

### 方式一：运行 start.bat（Windows）

在仓库根目录双击 `start.bat`，或在终端中运行：

```bat
.\start.bat
```

脚本自动完成：
1. 使用 `uv sync` 按 `uv.lock` 安装依赖
2. 检测 Edge 浏览器
3. 启动 Bot

首次启动会弹出配置向导，管理员 QQ 号和 WebUI 密码由你在本机输入：

```
==================================================
         MikuBot 首次配置向导
==================================================
[步骤 1/2] 设置管理员 QQ 号
  请输入你的 QQ 号: <你的 QQ 号>
  [OK] 管理员 QQ 号已设置

[步骤 2/2] 设置 WebUI 登录密码
  WebUI 直接密码登录，不需要账号/用户名
  请输入密码: <你设置的密码>
  [OK] WebUI 密码已设置
```

### 方式二：命令行启动

```bash
# Windows cmd / PowerShell，在仓库根目录运行
uv run python bot.py

# Linux / macOS / Git Bash，在仓库根目录运行
bash start.sh
```

---

## 配置管理

### 双配置体系

| 文件 | 用途 | 修改方式 |
|------|------|----------|
| `.env` | 本地环境变量（SUPERUSERS、WEBUI_PASSWORD、端口） | 首次启动向导创建；不要提交到 Git |
| `config/bot.yaml` | 插件运行时配置（按插件分区） | 首次运行时生成；发送「刷新配置」热加载 |
| `config/ai_models.yaml` | 全局 AI 平台列表及本地模型连接配置 | 在管理后台配置；API Key 只保存在本地，不要提交到 Git |

这些文件包含本机设置，仓库不会提供真实配置。旧版 `miku_ai.ai_platforms` 和 `local_*` 配置会在首次加载时自动迁移到 `config/ai_models.yaml`，其余插件设置仍保留在 `bot.yaml`。
AI聊天插件设置中的平台列表只显示平台名称；点击名称可在二级弹窗中查看/修改 API Key、Base URL、多模态模型及纯文本模型，也可新增或删除平台。

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

从 GitHub 获取代码后，在仓库根目录运行 `start.bat`（Windows）或 `bash start.sh`（Linux/macOS/Git Bash）。启动脚本会根据 `uv.lock` 创建本机虚拟环境并安装依赖；首次启动时按向导配置本机账号和密码。不要分发或提交 `.env`、`config/` 中的本机配置、Cookie 或 API Key。

---

## 贡献

欢迎通过 GitHub Issues 反馈问题，或提交 Pull Request。
