import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from nonebot.log import logger
from pathlib import Path
import re
import sys
import os

# ═══════════════════════════════════════════════════════════════
# 🔵 日志系统（参考 真寻bot 风格 + NoneBot 默认格式）
#    全程使用 ANSI 转义码，避免 loguru 标记嵌套解析问题
# ═══════════════════════════════════════════════════════════════

# ANSI 颜色常量（终端直接识别，不经过 loguru Colorizer 解析
_RESET     = "\033[0m"       # 重置
_GRAY     = "\033[90m"    # 灰（次要信息）
_BOLD     = "\033[1m"         # 加粗
# 等级色
_RED      = "\033[91m"       # 红（ERROR）
_GREEN    = "\033[92m"      # 绿（SUCCESS）
_YELLOW   = "\033[93m"     # 黄（WARNING）
_BLUE     = "\033[94m"       # 亮蓝（INFO / 插件关键词）
_PURPLE   = "\033[95m"      # 紫（CRITICAL）
_CYAN     = "\033[96m"      # 青（模块名）
_TIME_COL = "\033[32m"       # 时间：暗绿
_DIM      = "\033[2m"        # 弱化

# ── 自动发现的指令关键词集合（插件加载完成后填充） ──
_AUTO_KEYWORDS: set = set()
_MODULE_NAMES: set = set()

# ── 消息超过此长度自动截断（字符数（避免大段 dict 刷屏））
_MAX_MSG_LEN = 300


def _safe_print(msg: str):
    """安全打印，避免编码错误"""
    try:
        print(msg)
    except UnicodeEncodeError:
        safe = msg.encode("utf-8", errors="replace").decode("utf-8")
        try:
            print(safe)
        except Exception:
            pass


def _safe_re_search(pattern: str, text: str):
    """re.search 的安全版本，永远不会抛错。"""
    try:
        return re.search(pattern, text)
    except re.error:
        return None


def _truncate_msg(msg: str) -> str:
    """对消息体做截断。过长事件/大段 dict 简化为关键字摘要。

    规则：
      0) notice.* 类心跳事件 — 只保留前缀「OneBot V11 xxx | [notice.xxx]:」
      1) 含大量花括号的 message_sent 类事件 — 提取 message_type / raw_message / group_name
      2) 其它超长消息 — 截断到 _MAX_MSG_LEN
    """
    if not msg:
        return msg

    try:
        # 🔴 规则 0：notice 心跳事件 — 直接截断到前缀，彻底不展开 dict
        #    例如：notice.notify.input_status / notice.friend_add
        if "[notice." in msg or "'notice_type':" in msg:
            end_marker = msg.find("]:")
            if end_marker > 0:
                return msg[: end_marker + 2]
            return msg[:80] + ("..." if len(msg) > 80 else "")

        # 规则 1：大段 dict 消息（message_sent / message.group.* 等）
        if "'raw_message':" in msg or "'message_type':" in msg:
            message_type = ""
            raw_message = ""
            group_name = ""

            mt = _safe_re_search(r"'message_type':\s*'([^']+)'", msg)
            rm = _safe_re_search(r"'raw_message':\s*'([^']*)'", msg)
            gn = _safe_re_search(r"'group_name':\s*'([^']*)'", msg)

            if mt:
                message_type = mt.group(1)
            if rm:
                raw_message = rm.group(1)
            if gn:
                group_name = gn.group(1)

            if message_type or raw_message or group_name:
                parts = []
                if message_type:
                    parts.append(message_type)
                if group_name:
                    parts.append(f"群：{group_name}")
                if raw_message:
                    if len(raw_message) > 100:
                        raw_message = raw_message[:100] + "..."
                    parts.append(f"消息：'{raw_message}'")

                first_colon = msg.find("]:")
                if first_colon > 0:
                    prefix = msg[: first_colon + 2]
                    return prefix + " " + " ".join(parts)
                first_colon = msg.find(":")
                if first_colon > 0:
                    prefix = msg[: first_colon + 1]
                    return prefix + " " + " ".join(parts)
                return " ".join(parts)

        # 规则 2：常规超长截断
        if len(msg) > _MAX_MSG_LEN:
            return msg[:_MAX_MSG_LEN] + "..."

        return msg

    except Exception:
        if len(msg) > _MAX_MSG_LEN:
            try:
                return msg[:_MAX_MSG_LEN] + "..."
            except Exception:
                pass
        return msg


def _highlight_message(msg: str) -> str:
    """对消息做关键字高亮（用 ANSI 码）。

    先截断，再高亮，最后转义大括号（`{` → `{{`，`}` → `}}`），
    确保任何残留的 string-format 处理器不会把消息里的 `{'key': ...}` 当成占位符。
    """
    if not msg:
        return msg
    try:
        # 1) 先截断长消息
        msg = _truncate_msg(msg)

        # 2) module=plugins.x.y —— 亮蓝
        try:
            msg = re.sub(
                r"(module\s*=\s*)([\w.]+)",
                lambda m: f"{_BLUE}{m.group(1)}{m.group(2)}{_RESET}",
                msg,
            )
        except re.error:
            pass

        # 3) plugins.xxx —— 亮蓝
        try:
            msg = re.sub(
                r"(\bplugins\.[\w.]+)",
                lambda m: f"{_BLUE}{m.group(1)}{_RESET}",
                msg,
            )
        except re.error:
            pass

        # 4) Matcher(...) —— 加粗亮蓝
        if "Matcher(" in msg:
            try:
                msg = re.sub(
                    r"(Matcher\([^)]*\))",
                    lambda m: f"{_BOLD}{_BLUE}{m.group(1)}{_RESET}",
                    msg,
                )
            except re.error:
                pass

        # 5) CMD[xxx] —— 指令名高亮
        try:
            msg = re.sub(
                r"(CMD\[)([^\]]+)(\])",
                lambda m: f"{_BOLD}{_BLUE}{m.group(1)}{m.group(2)}{m.group(3)}{_RESET}",
                msg,
            )
        except re.error:
            pass

        # 6) 自动发现的插件指令关键词
        if _AUTO_KEYWORDS:
            for kw in sorted(_AUTO_KEYWORDS, key=len, reverse=True):
                if not kw or len(kw) < 2:
                    continue
                try:
                    pattern = re.compile(rf"\b({re.escape(kw)})\b")
                    msg = pattern.sub(f"{_BOLD}{_BLUE}\\1{_RESET}", msg)
                except re.error:
                    continue

        # 🔴 关键：转义所有 { }，防止任何残留 handler 的 format_map 把
        #    {'time': ...} 当成 Python 格式字符串占位符 → KeyError
        #    注意：必须放在最后一步，防止前面的 sub 破坏转义
        msg = msg.replace("{", "{{").replace("}", "}}")

        return msg
    except Exception:
        # 终极兜底：截断 + 转义，绝不崩
        try:
            if len(msg) > _MAX_MSG_LEN:
                msg = msg[:_MAX_MSG_LEN] + "..."
            return msg.replace("{", "{{").replace("}", "}}")
        except Exception:
            return msg or ""


def _level_style(name: str) -> str:
    """根据日志等级返回对应的 ANSI 色。"""
    return {
        "TRACE":    _GRAY,
        "DEBUG":    _CYAN,
        "INFO":     _BLUE,
        "SUCCESS":  _GREEN,
        "WARNING":  _YELLOW,
        "ERROR":    _RED,
        "CRITICAL": f"{_BOLD}{_RED}",
    }.get(name.upper(), _BLUE)


def _log_format(record: dict) -> str:
    """自定义日志 format 函数。

    关键改动：colorize=False，自己注入 ANSI 码，绕过 loguru Colorizer 递归解析
    格式：  MM-DD HH:MM:SS [LEVEL  ] module | 消息
    """
    try:
        time_str = record["time"].strftime("%m-%d %H:%M:%S")
        level_name = record["level"].name
        level_color = _level_style(level_name)
        module_name = record["name"]
        message = _highlight_message(record["message"])

        return (
            f"{_TIME_COL}{time_str}{_RESET} "
            f"[{level_color}{level_name:<7}{_RESET}] "
            f"{_CYAN}{module_name}{_RESET} | "
            f"{message}\n"
        )
    except Exception:
        # 终极兜底：无论任何异常，都用最朴素的方式打印，绝不崩溃
        try:
            time_str = record.get("time") or ""
            if hasattr(time_str, "strftime"):
                time_str = time_str.strftime("%m-%d %H:%M:%S")
            level_name = record["level"].name if "level" in record else "INFO"
            module_name = record.get("name", "unknown")
            message = str(record.get("message", ""))
            if len(message) > _MAX_MSG_LEN:
                message = message[:_MAX_MSG_LEN] + "..."
            return f"{time_str} [{level_name:<7}] {module_name} | {message}\n"
        except Exception:
            try:
                return f"LOG | {str(record.get('message',''))[:200]}\n"
            except Exception:
                return "LOG_FORMAT_ERROR\n"


_DISPLAY_LOG_LEVEL = 20  # INFO 及以上显示（=20）
# 通过 .env 或 YAML 可以调整显示等级
try:
    import os as _os
    _env_level = str(_os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    _level_map = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "SUCCESS": 25,
                  "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    if _env_level in _level_map:
        _DISPLAY_LOG_LEVEL = _level_map[_env_level]
except Exception:
    pass


def _log_filter(record: dict) -> bool:
    """过滤器：按日志等级显示，并丢弃心跳类事件，截断过长消息。"""
    try:
        # 1) 等级不够直接丢掉（level < _DISPLAY_LOG_LEVEL 的不显示）
        if record["level"].no < _DISPLAY_LOG_LEVEL:
            return False

        # 2) 显式丢弃：QQ 协议端的纯心跳/输入状态等事件
        msg = str(record.get("message", ""))
        if any(kw in msg for kw in (
            "[notice.notify.input_status]",
            "[notice.notify.group_admin_approve]",
            "[notice.notify.essence]",
            "[notice.notify.group_ban]",
        )):
            return False

        # 3) 过长的 dict 事件消息（>200 字符且含内部字段）也丢掉
        if len(msg) > 200 and ("'self_id':" in msg or "'post_type':" in msg or "'notice_type':" in msg):
            return False

        return True
    except Exception:
        # 任何异常都放行（宁显示不错失）
        return True


def _apply_logging():
    """彻底清除现有 handler 并重新应用我们的日志格式。

    会在两处调用：
      1) 模块首次加载时
      2) nonebot.init() 之后（因为 nonebot 会调用 _configure_logging() 重新配置 handler）
    """
    try:
        # 🔴 关键：循环移除所有 handler，包括 nonebot 可能添加的
        _removed = 0
        for _ in range(100):
            try:
                logger.remove()  # 不带参数 = 删除最近一个
                _removed += 1
            except Exception:
                break
        # 第二层保险：直接遍历内部 handlers
        try:
            _handler_ids = list(logger._core.handlers.keys())
            for _hid in _handler_ids:
                try:
                    logger.remove(_hid)
                    _removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        _safe_print(f"[OK] 已重置日志系统（清理 {_removed} 个旧 handler）")
    except Exception as _e:
        _safe_print(f"[WARN] 清除旧日志 handler 失败: {_e}")

    # 🔵 终端输出（我们的格式）
    logger.add(
        sys.stdout,
        level=0,  # 过滤器里再判断等级
        diagnose=False,
        colorize=False,
        filter=_log_filter,
        format=_log_format,
    )

    # 🔵 文件日志（按天轮转）
    try:
        _log_dir = Path(__file__).resolve().parent / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            _log_dir / "bot_{time:YYYY-MM-DD}.log",
            level=10,  # DEBUG 起写入文件
            diagnose=False,
            colorize=False,
            encoding="utf-8",
            enqueue=True,
            rotation="1 day",
            retention="30 days",
        )
    except Exception as _e:
        _safe_print(f"[WARN] 文件日志初始化失败: {_e}")


# ── 首次应用：模块加载时先跑一次
_apply_logging()


# ═══════════════════════════════════════════════════════════════
# 🔵 插件关键词自动发现
# ═══════════════════════════════════════════════════════════════

def _collect_plugin_keywords() -> None:
    """扫描所有已注册的 Matcher，提取指令关键词供日志高亮。"""
    try:
        from nonebot.internal.matcher import matchers
    except Exception:
        return

    total = 0
    for _priority, matcher_list in matchers.items():
        for m in matcher_list:
            total += 1
            # 收集模块名
            if hasattr(m, "module_name") and m.module_name:
                _MODULE_NAMES.add(m.module_name)

            # 遍历 rule.checkers，取出规则对象
            rule = getattr(m, "rule", None)
            if rule is None:
                continue
            checkers = getattr(rule, "checkers", set())
            for dep in checkers:
                rule_obj = getattr(dep, "call", None)
                if rule_obj is None:
                    continue

                # CommandRule.cmds
                cmds = getattr(rule_obj, "cmds", None)
                if cmds:
                    for cmd_tuple in cmds:
                        if isinstance(cmd_tuple, (tuple, list)):
                            for sub in cmd_tuple:
                                if isinstance(sub, str) and sub:
                                    _AUTO_KEYWORDS.add(sub)
                        elif isinstance(cmd_tuple, str) and cmd_tuple:
                            _AUTO_KEYWORDS.add(cmd_tuple)
                    continue

                # Startswith/Endswith/FullmatchRule.msg
                msg = getattr(rule_obj, "msg", None)
                if isinstance(msg, (tuple, list)):
                    for sub in msg:
                        if isinstance(sub, str) and sub:
                            _AUTO_KEYWORDS.add(sub)
                    continue

                # KeywordsRule.keywords
                keywords = getattr(rule_obj, "keywords", None)
                if isinstance(keywords, (tuple, list)):
                    for sub in keywords:
                        if isinstance(sub, str) and sub:
                            _AUTO_KEYWORDS.add(sub)
                    continue

    # 去掉过短的关键词（<2 个字符），避免误高亮
    for kw in list(_AUTO_KEYWORDS):
        if len(kw) < 2:
            _AUTO_KEYWORDS.discard(kw)

    _safe_print(f"[OK] 扫描 {total} 个 Matcher，发现 {len(_AUTO_KEYWORDS)} 个指令关键词")
    if _AUTO_KEYWORDS:
        sample = sorted(_AUTO_KEYWORDS)[:8]
        suffix = "..." if len(_AUTO_KEYWORDS) > 8 else ""
        _safe_print(f"     示例: {', '.join(sample)}{suffix}")


# ═══════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════

# ─── 确保项目根目录在 sys.path 中 ───
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ─── Windows 终端 UTF-8 编码设置 ───
if sys.platform == "win32":
    try:
        os.system("chcp 65001 >nul")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _update_env(key: str, value: str):
    """更新 .env 文件中的配置项"""
    env_path = BASE_DIR / ".env"
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    if key == "SUPERUSERS":
        pattern = rf'{key}=\[.*?\]'
        replacement = f'{key}={value}'
    else:
        pattern = rf'{key}=".*?"'
        replacement = f'{key}="{value}"'

    if re.search(pattern, content):
        content = re.sub(pattern, replacement, content)
    else:
        content += f"\n{replacement}\n"

    env_path.write_text(content, encoding="utf-8")


def interactive_setup():
    """终端交互式配置向导 —— 首次启动引导用户设置管理员信息。"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        _safe_print("[ERROR] 找不到 .env 文件，请确认项目完整")
        sys.exit(1)

    # 非交互式环境跳过 input()
    if not sys.stdin.isatty():
        _safe_print("[INFO] 非交互式环境，跳过终端配置。请手动编辑 .env 文件")
        return

    content = env_path.read_text(encoding="utf-8")

    # 检查 SUPERUSERS
    su_match = re.search(r'SUPERUSERS=\[(.*?)\]', content)
    su_raw = su_match.group(1) if su_match else ""
    has_default_su = not su_raw or any(s in su_raw for s in ('123456789', '10000', '123456', '0', '""'))

    # 检查 WEBUI_PASSWORD
    pw_match = re.search(r'WEBUI_PASSWORD="(.*?)"', content)
    pw_raw = pw_match.group(1) if pw_match else ""
    has_default_pw = not pw_raw or pw_raw in ('', 'miku8888', '123456', 'admin')

    if not has_default_su and not has_default_pw:
        return  # 已配置，跳过

    _safe_print("")
    _safe_print("=" * 56)
    _safe_print("         MikuBot 首次配置向导")
    _safe_print("=" * 56)
    _safe_print("  首次启动需要配置管理员信息，请按提示输入")
    _safe_print("=" * 56)

    if has_default_su:
        _safe_print("")
        _safe_print("[步骤 1/2] 设置管理员 QQ 号")
        _safe_print("  只有该 QQ 号才能执行「重启」等管理员指令")
        while True:
            try:
                qq = input("  请输入你的 QQ 号: ").strip()
            except EOFError:
                _safe_print("  [WARN] 无法读取输入，跳过配置")
                return
            if qq.isdigit() and len(qq) >= 5:
                break
            _safe_print("  [X] QQ 号格式不正确，请输入 5 位以上数字")

        _update_env("SUPERUSERS", f'["{qq}"]')
        _safe_print(f"  [OK] 管理员 QQ 号已设置为: {qq}")

    if has_default_pw:
        _safe_print("")
        _safe_print("[步骤 2/2] 设置 WebUI 登录密码")
        _safe_print("  WebUI 直接密码登录，不需要账号/用户名")
        try:
            pwd = input("  请输入密码 (直接回车使用默认 miku8888): ").strip()
        except EOFError:
            _safe_print("  [WARN] 无法读取输入，跳过配置")
            return
        if not pwd:
            pwd = "miku8888"
            _safe_print(f"  [INFO] 使用默认密码: {pwd}")
        else:
            _safe_print("  [OK] WebUI 密码已设置")

        _update_env("WEBUI_PASSWORD", pwd)

    _safe_print("")
    _safe_print("=" * 56)
    _safe_print("[OK] 配置已自动保存，现在继续启动 Bot...")
    _safe_print("=" * 56)
    _safe_print("")


if __name__ == "__main__":
    # 启动前交互式配置
    interactive_setup()

    # 依赖检测与自动安装
    try:
        from utils.check_deps import check_all
        check_all()
    except Exception as e:
        _safe_print(f"[WARN] 依赖检测失败: {e}")

    # 启动 NoneBot
    nonebot.init()
    # 🔴 关键：nonebot.init() 内部会重新配置日志（调用 _configure_logging）
    #    所以这里必须再次应用我们的日志格式，覆盖掉 nonebot 的默认 handler
    _apply_logging()
    driver = nonebot.get_driver()
    driver.register_adapter(ONEBOT_V11Adapter)

    # 配置管理器
    try:
        from utils.config_manager import config_manager
    except Exception as e:
        logger.warning(f"配置管理器加载失败: {e}")

    # 加载插件
    nonebot.load_builtin_plugins("echo")
    nonebot.load_from_toml("pyproject.toml")

    # ── 🔵 插件关键词自动发现 ──
    try:
        _collect_plugin_keywords()
    except Exception as e:
        _safe_print(f"[WARN] 插件关键词扫描失败: {e}")

    # ── 🔵 缓存图片定时清理 ──
    try:
        from utils.cache_cleanup import start_cleanup
        start_cleanup(driver)
    except Exception as e:
        logger.warning(f"缓存清理任务初始化失败: {e}")

    nonebot.run()
