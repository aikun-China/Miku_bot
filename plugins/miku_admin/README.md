# Miku 管理员插件（miku_admin）

只有 `SUPERUSER`（在 `.env` 中配置的 QQ 号）才能使用本插件的指令。

## 指令

| 指令 | 别名 | 权限 | 说明 |
|---|---|---|---|
| `重启` | `restart / reload` | SUPERUSER | 重启整个 Bot 进程 |
| `配置检查` | `检查配置 / checkconfig` | SUPERUSER | 打印 SUPERUSERS、WebUI 密码、监听端口、已注册插件区等 |
| `刷新配置` | `reload_config / reloadcfg` | SUPERUSER | 重新加载 `config/bot.yaml`（不重启 Bot） |

## 启动通知

Bot 启动并成功连接到 QQ 协议端后，会自动向**所有 `SUPERUSER`** 发一条私聊消息：

```
🎵 MikuBot 已启动！
监听端口：3108
已注册插件：miku_admin、miku_basic、miku_weather ...
发送「配置检查」查看状态
```

可以在 `config/bot.yaml` 中把 `miku_admin.notify_on_start: false` 关掉。

## 工作原理

- **重启**：调用 `os.execv(sys.executable, [sys.executable] + sys.argv)`，用同一进程位重启整个 Python 程序（Windows 上等同于启动新进程替换原进程）。
- **配置检查**：从 `utils/config_manager.py` 读取 `superusers / webui_password / host / port / registered_plugins()` 并整理成文本消息。
- **刷新配置**：调用 `config_manager.reload()`，`reload` 会从 `config/bot.yaml` 重新读取并合并 `defaults` 到内存，但**不会重写文件**（避免破坏你手工加的注释）。
- **启动通知**：在 `driver.on_bot_connect` 回调里逐个向 `SUPERUSER` 发私聊。

## 配置区

首次加载插件会在 `config/bot.yaml` 自动追加：

```yaml
miku_admin:
  # Bot 上线时是否私聊通知超级用户（true/false）
  notify_on_start: true
  # 管理员指令触发词列表
  admin_commands:
  - 重启
  - 配置检查
  - 刷新配置
```

> 这些 key 仅作「文档用途」，真正的指令/别名是写死在代码里的（方便做权限/去重）。

## 如何配置管理员

编辑项目根目录下的 `.env` 文件：

```dotenv
SUPERUSERS=["你的QQ号"]
```

修改后**重启 Bot** 生效。

## 引用文件

- [utils/config_manager.py](../../utils/config_manager.py) — 配置读取/热加载
