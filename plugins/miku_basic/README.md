# Miku 基础信息插件（miku_basic）

最基础的「心跳」插件。用一张图片卡片告诉用户：Bot 还活着，并且知道你是谁。

## 指令

| 指令 | 别名 | 说明 |
|---|---|---|
| `信息` | `ping / 状态 / info` | 返回一张初音蓝主题的状态卡片（图片） |

## 输出示例

发送 `ping`，返回一张包含：
- 🎵 调用者昵称
- 🆔 QQ 号
- ✅ 运行正常
- ⏱ 响应时间（毫秒）

的图片卡片；如果浏览器（Chromium）不可用，则退化为纯文本消息。

## 工作原理

1. 调用 `utils/html_render.py → render_template("ping", ...)` 把变量塞进 HTML 模板
2. 调用 `utils/screenshot.py → screenshot_html(html)` 让 Playwright 渲染成 PNG
3. 通过 `MessageSegment.image()` 发回用户
4. 任何环节失败 → 自动降级成纯文本消息

## 配置区

首次加载插件会在 `config/bot.yaml` 自动追加：

```yaml
miku_basic:
  # Bot 在「信息/ping」指令中显示的名称
  bot_name: MikuBot
  # 是否在信息卡片中显示系统运行状态（true/false）
  show_system_info: true
  # 响应风格：card（图片卡片）/ text（纯文本）
  response_style: card
```

## 引用文件

- [utils/html_render.py](../../utils/html_render.py) — HTML 模板渲染
- [utils/screenshot.py](../../utils/screenshot.py) — Playwright 截图
- [utils/templates/ping.html](../../utils/templates/ping.html) — 图片卡片模板
