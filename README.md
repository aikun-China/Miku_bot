# 🎵 MikuBot — 初音未来 QQ 机器人

基于 **NoneBot2** + **OneBot V11** 的 QQ 机器人，架构参考 [zhenxun_bot](https://github.com/zhenxun-org/zhenxun_bot)。

> **核心特色**：内置 Playwright 浏览器截图引擎，所有插件通过 HTML 模板渲染成图片回复，告别纯文字排版。

---

## 📁 项目结构

```
miku_bot/
├── bot.py                              # 入口（日志/调度/插件加载）
├── start.bat                           # Windows 一键启动
├── .env / .env.dev / .env.prod        # 基础环境变量（host/port/管理员）
├── requirements.txt                    # Python 依赖
├── pyproject.toml                      # 项目元数据
├── .gitignore                          # Git 忽略规则
│
├── config/
│   └── bot.yaml                        # ⭐ 所有插件的统一配置文件
│                                        #   （首次加载时由各插件自动写入模板）
├── plugins/                            # 插件目录（每个插件都自带 YAML 模板）
│   ├── miku_admin/                      # 👑 管理员功能（重启/配置检查/刷新配置）
│   ├── miku_basic/                      # 🎀 基础功能（ping=图片卡片）
│   ├── miku_screenshot/                 # 🖼️ 网页截图（带 URL 黑名单）
│   ├── miku_weather/                    # 🌤️ 和风天气查询（需 API_KEY + API_HOST）
│   └── miku_checkin/                    # 📅 每日签到（金币/好感度/双倍概率）
├── utils/                              # 底层工具
│   ├── config_manager.py                # ⭐ 配置管理（插件注册/热加载）
│   ├── screenshot.py                    # ⭐ 截图引擎（HTML / URL → 图片）
│   ├── html_render.py                   # ⭐ HTML 模板渲染
│   ├── cache_cleanup.py                 # ⭐ 缓存图片自动清理（每天 00:00）
│   ├── user_store.py                    # 签到用户数据
│   ├── anime_quotes.py                  # 随机语录
│   ├── bg_helper.py                     # 底图 / 资源查找
│   └── templates/                       # HTML 模板 + 底图
│       ├── ping.html                    # 信息卡片
│       ├── weather.html                 # 天气卡片
│       ├── checkin.html                 # 签到卡片
│       └── *.png                        # 卡片底图与素材
│
├── data/                               # 运行时数据（不会提交到 Git）
│   ├── images/                          # 截图产物
│   ├── screenshots/                     # 插件截图输出
│   └── users/                           # 用户签到 JSON
└── logs/                               # 日志（不会提交到 Git）
    └── bot_YYYY-MM-DD.log
```

---

## 🚀 快速启动

### 1. 创建虚拟环境并安装依赖

```bash
cd D:\qqbot\miku_bot
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 安装 Chromium（必须）

截图引擎需要 Playwright 的 Chromium（约 180MB）：

```bash
.venv\Scripts\python.exe -m playwright install chromium
```

> 如果你有 `chrome-win64.zip`，也可以手动解压到 `chrome-win64/` 并在 `utils/screenshot.py` 中指定 `executable_path`。

### 3. 配置（两个层级）

| 文件 | 存什么 | 是否需手动填写 |
|---|---|---|
| `.env` | 管理员 QQ / WebUI 密码 / host / port / log level | **是**，只改一次 |
| `config/bot.yaml` | 所有插件的运行参数（API Key、开关、超时……） | **首次启动由各插件自动生成模板**，之后按需改 |

**`.env` 最少需要填写**：

```ini
# ./.env
ENVIRONMENT=dev
DRIVER=~fastapi
HOST=127.0.0.1
PORT=3108
LOG_LEVEL=INFO
FASTAPI_RELOAD=false

# 管理员 QQ 号（可多个）
SUPERUSERS=["你的QQ号"]

# WebUI 登录密码（未来功能）
WEBUI_PASSWORD="随便一个安全密码"

COMMAND_START=["/", "", "!", "！"]
NICKNAME=["Miku", "miku", "初音", "初音未来"]
```

**`config/bot.yaml`**：首次启动时，每个插件会调用 `config_manager.register_plugin(...)` 把自己带注释的模板追加到文件末尾。你也可以直接复制下面示例作为初始模板：

```yaml
bot:
  superusers:
  - '你的QQ号'
  webui_password: 随便一个安全密码
  log_level: INFO
  log_to_file: true
  log_file_level: INFO
  log_rotation: 1 day
  log_retention: 30 days
  log_max_size: 0
  nickname:
  - Miku
  - 初音
  host: 127.0.0.1
  port: 3108

miku_admin:
  # Bot 上线时是否私聊通知超级用户（true/false）
  notify_on_start: true
  # 管理员指令触发词列表
  admin_commands:
  - 重启
  - 配置检查
  - 刷新配置
# …… 其他插件的配置由各插件首次运行时自动写入
```

> ⚠️ **关于和风天气**：`miku_weather` 需要你自己去 [dev.qweather.com](https://dev.qweather.com/) 申请 `API_KEY`，并在你账户里的专属 `API_HOST`（例如 `devapi.qweather.com` 或 `xxx.re.qweatherapi.com`）填到 `bot.yaml` 的相应字段。

### 4. 启动 Bot

```bash
# 方式 1：双击
start.bat

# 方式 2：命令行
.venv\Scripts\python.exe bot.py
```

启动后会看到：
```
[INFO] nonebot | Succeeded to import "plugins.miku_admin" ...
[INFO] nonebot | OneBot V11 | Running on 127.0.0.1:3108
```

### 5. 连接 QQ 客户端（协议端）

在 **NapCat / LLOneBot / Lagrange.OneBot** 等协议端里配置**反向 WebSocket**：

```
ws://127.0.0.1:3108/onebot/v11/ws
```

> 协议端的安装与 QQ 登录不在本项目覆盖范围。

---

## 📋 插件指令清单

| 插件 | 指令 | 权限 | 输出 | 说明 |
|---|---|---|---|---|
| `miku_admin` | `配置检查` | 超级用户 | 文字 | 验证配置/管理员/已注册插件 |
| `miku_admin` | `刷新配置` | 超级用户 | 文字 | 从 bot.yaml 热重新加载（不改代码） |
| `miku_admin` | `重启` | 超级用户 | 文字 | 自动重启整个 Bot 进程 |
| `miku_basic` | `ping` / `信息` / `状态` | 所有人 | **图片** | 在线状态 + 昵称 + 响应时间 |
| `miku_screenshot` | `截图 <网址>` | 所有人（可配置仅管理员） | **图片** | 网页整页截图（含黑名单） |
| `miku_weather` | `天气 <城市>` | 所有人 | **图片** | 和风天气 API 实时天气 |
| `miku_checkin` | `签到` / `每日签到` | 所有人 | **图片** | 金币 + 好感度 + 随机概率双倍 |

> 💡 所有带注释的默认配置都放在 **插件自身** 的 `__init__.py` 中（变量名形如 `_XXX_TEMPLATE`），首次加载会被追加到 `config/bot.yaml`。

---

## 🖼️ 新插件开发（3 步走）

假设你要新增一个 `miku_xxx` 插件：

### 1. `plugins/miku_xxx/__init__.py`

```python
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment
from utils.config_manager import config_manager
from utils.html_render import render_template
from utils.screenshot import screenshot_html

# ⭐ 在这里把「带注释的 YAML 模板」写好
_XXX_TEMPLATE = (
    "\n"
    "miku_xxx:\n"
    "  # 本插件的开关（true/false）\n"
    "  enabled: true\n"
    "  # 请求超时（毫秒）\n"
    "  timeout: 10000\n"
)

_cfg = config_manager.register_plugin(
    "miku_xxx",
    defaults={"enabled": True, "timeout": 10000},
    template_str=_XXX_TEMPLATE,   # ⭐ 关键：插件自己提供模板字符串
    description="xxx 插件配置",
)

cmd = on_command("xxx", priority=5, block=True)

@cmd.handle()
async def _():
    html = render_template("xxx", title="Hello Miku")
    img_path = await screenshot_html(html, width=600, height=300)
    await cmd.finish(MessageSegment.image(str(img_path)))
```

### 2. `utils/templates/xxx.html`（可选，纯文字则不需要）

```html
<!doctype html>
<html><head><meta charset="utf-8"></head>
<body>
  <h1>{title}</h1>
</body></html>
```

### 3. 重启 Bot

`config_manager` 会自动：
- 在 `config/bot.yaml` 末尾追加 `miku_xxx:` 区块（含注释）
- 读取 defaults → 供插件代码使用
- 之后 **只在内存补齐** 新字段，不再覆盖你的手工修改

---

## 🧹 自动清理缓存

由 `utils/cache_cleanup.py` 实现，启动时会通过 APScheduler 注册**定时任务**：
- 默认清理时间：`00:00`（可在 `config/bot.yaml` 的 `cache_cleanup.cleanup_times` 里改）
- 清理目录：`data/screenshots/` 等
- 保留时长：`max_age_hours`，`0` 表示全部清理

---

## 🚫 安全

- 管理员身份依赖 `.env` 的 `SUPERUSERS`
- `miku_admin` 指令通过 `nonebot.permission.SUPERUSER` 校验
- 截图插件带 `url_blacklist` 正则黑名单（默认禁止 `localhost`、`127.0.0.1`、`192.168.*` 等内网）
- 建议把 `.env`、`config/bot.yaml`（含 API Key 与实机 QQ号）放在 `.gitignore` 或本地未跟踪的副本中

---

## 📌 Git 提交参考清单

**✅ 应该提交（源码/模板）**：
```
.gitignore
README.md
start.bat
requirements.txt
pyproject.toml
.env          # 可以提交空模板，真号与密码用 .env.local 覆盖
.env.dev
.env.prod
bot.py
config/bot.yaml        # 只提交"空模板"；真实 API Key 与管理员号请另存
plugins/**/*           # 全部插件（除 __pycache__）
utils/**/*             # 全部工具 + templates/HTML+底图
```

**❌ 不应该提交（运行时产物/个人数据）**：
```
.venv/                 # Python 虚拟环境
__pycache__/           # 字节码
*.pyc
logs/                  # 运行日志
data/                  # 截图/用户签到数据
chrome-win64.zip       # 浏览器包（playwright 会自己装）
chromium/ / chrome/    # 浏览器缓存
.vscode/ .idea/        # 个人 IDE 配置
config/.first_run      # 首次运行标记
.env.local             # 个人私密环境变量
```

---

## 🛠️ 后续计划

- [x] 统一 YAML 配置 + 插件首次加载自动模板追加
- [x] 自动清理缓存图片
- [x] 和风天气接入
- [x] 每日签到（金币 / 好感度 / 双倍概率）
- [ ] WebUI 管理面板
- [ ] AI 对话卡片（LLM + HTML 渲染）
- [ ] 插件市场（热加载 / 卸载）

---

> 🎵 初音未来，永远相伴！✨
