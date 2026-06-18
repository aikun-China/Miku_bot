# 初音未来 MikuBot

基于 **NoneBot2** 框架的 QQ 群机器人，参考 [zhenxun-org](https://github.com/zhenxun-org) 项目架构。

**核心特色**：内置浏览器截图引擎，所有插件可渲染 HTML 模板生成图片回复，告别纯文字排版。

---

## 项目结构

```
miku_bot/
├── bot.py                          # 入口（启动前自动检测依赖 + 交互配置）
├── start.bat                       # Windows 启动脚本（推荐双击）
├── start.sh                        # Linux/Mac/Git Bash 启动脚本
├── start.ps1                       # PowerShell 启动脚本
├── .env                            # 环境配置（首次启动会引导填写）
├── .env.dev / .env.prod            # 环境配置（开发/生产）
├── requirements.txt                # Python 依赖清单
├── pyproject.toml                  # NoneBot 项目配置
├── README.md                       # 本文件
│
├── plugins/                         # 插件目录
│   ├── miku_admin/                  # 管理员功能（重启、配置检查）
│   ├── miku_basic/                  # 基础功能（ping 输出图片卡片）
│   ├── miku_screenshot/             # 网页截图
│   └── miku_weather/                # 天气示例（图片卡片）
│
├── utils/                           # 底层工具
│   ├── check_deps.py                # 依赖检测 + 自动安装
│   ├── config_manager.py            # 配置管理（启动检查 + WebUI 密码验证）
│   ├── screenshot.py                # 截图引擎（HTML/URL → 图片）
│   ├── html_render.py               # HTML 模板渲染
│   └── templates/                   # HTML 模板
│       ├── ping.html                # 状态卡片模板
│       └── weather.html             # 天气卡片模板
│
├── data/                            # 数据目录
│   ├── images/                      # 截图输出
│   └── screenshots/                 # 网页截图缓存
│
├── config/                          # 配置文件
└── logs/                            # 日志目录
```

---

## 快速启动

### 方式一：双击 start.bat（Windows 推荐）

```
D:\qqbot\miku_bot\start.bat
```

脚本会自动完成：
1. 检测 `.venv` 虚拟环境
2. 检测依赖（缺失则自动 `pip install`）
3. 检测浏览器（缺失则自动下载，**见下方浏览器下载加速**）
4. 首次启动交互式配置（输入管理员 QQ 号和密码）
5. 启动 Bot

### 方式二：命令行启动

```bash
# Windows cmd
D:
cd \qqbot\miku_bot
.venv\Scripts\python.exe bot.py

# Git Bash
cd /d/qqbot/miku_bot
bash start.sh

# PowerShell
cd D:\qqbot\miku_bot
.venv\Scripts\python.exe bot.py
```

---

## 首次配置（交互式向导）

首次启动时，Bot 会检测到 `.env` 中的默认值，自动弹出终端配置向导：

```
==================================================
         MikuBot 首次配置向导
==================================================
  首次启动需要配置管理员信息，请按提示输入
==================================================

[步骤 1/2] 设置管理员 QQ 号
  只有该 QQ 号才能执行「重启」等管理员指令
  请输入你的 QQ 号: 123456789
  [OK] 管理员 QQ 号已设置为: 123456789

[步骤 2/2] 设置 WebUI 登录密码
  WebUI 直接密码登录，不需要账号/用户名
  请输入密码 (直接回车使用默认 miku8888): MySecret
  [OK] WebUI 密码已设置

[OK] 配置已自动保存，现在继续启动 Bot...
```

配置自动写入 `.env`，无需手动编辑文件。

### 手动修改配置

如需修改，编辑 `.env`：

```ini
# 管理员 QQ 号（用于 QQ 端管理指令）
SUPERUSERS=["你的QQ号"]

# WebUI 登录密码（只验证密码，不需要用户名）
WEBUI_PASSWORD="你的密码"
```

修改后重启 Bot 生效。

---

## 浏览器下载加速（Chromium）

Playwright 首次需要下载 Chromium（约 180MB），默认从国外服务器下载较慢。

### 方法一：自动国内镜像（已内置）

`utils/check_deps.py` 已自动设置国内镜像：

```python
os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright/"
```

启动时自动使用国内镜像下载。

### 方法二：手动下载 + 本地解压

如果自动下载仍慢，可手动下载浏览器包放到项目目录：

1. 下载对应版本的 `chrome-win64.zip`（约 40MB 压缩包）
2. 放到 `miku_bot/chrome-win64.zip` 或 `miku_bot/data/browsers/chrome-win64.zip`
3. 启动时自动检测并解压，跳过下载

### 方法三：跳过浏览器安装（截图功能不可用）

如果不需要截图功能，浏览器安装失败不会阻塞 Bot 启动。Bot 会提示：

```
[WARN] 浏览器下载失败，截图功能将不可用
```

其他功能（文字回复、管理员指令）正常运行。

---

## 可用指令

| 插件 | 指令 | 权限 | 输出 | 说明 |
|------|------|------|------|------|
| `miku_admin` | `配置检查` | 超级用户 | 文字 | 查看配置状态 |
| `miku_admin` | `重启` | 超级用户 | 文字 | 重启 Bot（execv 替换进程） |
| `miku_basic` | `ping` / `信息` / `状态` | 所有人 | **图片** | 状态卡片（HTML 截图） |
| `miku_screenshot` | `截图` + 网址 | 所有人 | **图片** | 截取任意网页 |
| `miku_weather` | `天气` + 城市 | 所有人 | **图片** | 天气卡片（演示数据） |

---

## 截图引擎：插件开发指南

所有插件生成图片统一走这套流程，3 行代码搞定：

```python
from utils.html_render import render_template
from utils.screenshot import screenshot_html
from nonebot.adapters.onebot.v11 import MessageSegment

# 1. 渲染 HTML 模板
html = render_template("模板名", title="标题", content="内容...")

# 2. 截图 → 图片文件
img_path = await screenshot_html(html, width=600, height=400)

# 3. 发送图片
await cmd.finish(MessageSegment.image(str(img_path)))
```

### 新增模板

1. 在 `utils/templates/` 下新建 `.html` 文件
2. 用 `{变量名}` 作为占位符
3. 插件里 `render_template("模板名", 变量=值)` 填充

### 底层 API

```python
from utils.screenshot import screenshot_url, screenshot_html

# 截网页
img = await screenshot_url("https://bilibili.com", full_page=True)

# 截 HTML 字符串
img = await screenshot_html("<h1>Hello</h1>", width=500, height=300)
```

---

## 连接 QQ

在协议端（NapCat / LLOneBot / Lagrange）配置反向 WebSocket：

```
ws://127.0.0.1:3108/onebot/v11/ws
```

---

## 安全配置

### 管理员体系

| 配置项 | 作用 | 验证方式 |
|--------|------|----------|
| `SUPERUSERS` | QQ 端管理员权限 | QQ 号身份 |
| `WEBUI_PASSWORD` | WebUI 登录密码 | 只验证密码（无用户名） |

### 管理员指令

- **`重启`**：Bot 回复"正在重启"，然后 `os.execv` 替换当前进程，重新加载所有配置和插件
- **`配置检查`**：查看 SUPERUSERS、WEBUI_PASSWORD、监听端口等状态

### WebUI 登录（未来开发）

```
登录方式：只输入密码（不需要账号/用户名）
验证接口：config_manager.verify_webui_password(password)
```

---

## 分发部署

项目文件夹（含 `.venv`）可直接打包发给其他人：

1. 所有依赖安装在项目目录 `.venv` 中，不依赖系统 Python
2. 双击 `start.bat` 即可自动检测并启动
3. 首次启动会弹出交互式配置向导

```bash
# 打包时包含
miku_bot/
├── .venv/                  # 便携 Python + 所有依赖
├── start.bat               # 启动脚本
├── bot.py
├── requirements.txt
├── .env                    # 配置文件（首次启动会引导修改）
├── plugins/
├── utils/
└── ...
```

---

## 后续开发

- [ ] 接入真实天气 API（和风天气 / OpenWeatherMap）
- [ ] 接入 AI 对话，渲染对话卡片
- [ ] 数据库 + 签到/好感度系统（图片进度条）
- [ ] WebUI 管理面板（密码登录）
- [ ] 插件市场（参考 zhenxun）

---

> 初音未来，永远相伴！
