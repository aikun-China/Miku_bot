"""
MikuBot 统一配置管理器（YAML 版本）

架构：
  1. config/bot.yaml —— 所有插件共享的配置文件，按插件名分区
  2. 插件首次加载时，调用 config_manager.register_plugin(...) 注册自己的默认配置
  3. 若 YAML 中不存在该插件区：以「带详细注释的模板」追加到 YAML 文件末尾
     若 YAML 中已有该插件区：仅在内存中补齐缺失字段，**不重写文件**（避免丢注释）
  4. 读取/修改统一通过 config_manager.get_plugin_config() / get() / set()

典型用法（插件内）：
    from utils.config_manager import config_manager

    config_manager.register_plugin(
        "miku_weather",
        defaults={
            "API_KEY": "",
            "DEFAULT_CITY": "北京",
            "API_HOST": "devapi.qweather.com",
            "lang": "zh",
            "response_style": "card",
        },
        description="天气查询插件配置",
    )

    cfg = config_manager.get_plugin_config("miku_weather")
    city = cfg["DEFAULT_CITY"]

配置优先级：
  config/bot.yaml 中实际值 > register_plugin 提供的 defaults
"""

from pathlib import Path
import copy
import threading
from typing import Any, Dict, List, Optional

from nonebot import get_driver
from nonebot.log import logger

import yaml


# ─── 路径 ───
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "bot.yaml"
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


class ConfigManager:
    """
    YAML 配置管理器。
    提供插件级配置区的「首次加载自动注册 + 热修改 + 热保存」功能。
    关键特性：插件区首次写入采用「带注释的模板文本追加」，避免注释丢失。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._config: Dict[str, Any] = {}
        self._registered_plugins: set = set()
        self._plugin_defaults: Dict[str, Dict[str, Any]] = {}
        self._plugin_templates: Dict[str, str] = {}  # 缓存插件配置模板（含注释）
        self._load()
        self._ensure_bot_section()

    # ============================================================
    # 文件读写
    # ============================================================

    def _load(self) -> None:
        """从 YAML 文件读取配置；文件不存在或内容异常时重置为空白。"""
        with self._lock:
            if not CONFIG_FILE.exists():
                self._config = {}
                logger.info(f"[Config] 新建配置文件: {CONFIG_FILE}")
                return

            try:
                text = CONFIG_FILE.read_text(encoding="utf-8").strip()
                if not text or text == "{}":
                    self._config = {}
                    return
                data = yaml.safe_load(text) or {}
                if not isinstance(data, dict):
                    logger.warning("[Config] YAML 根节点不是 dict，已重置为 {}")
                    data = {}
                self._config = data
                # 调试：打印已读取的顶层 key
                ai_val = data.get("miku_ai", {})
                logger.info(
                    f"[Config] 读取 YAML 成功: 顶层 keys={list(data.keys())}, "
                    f"miku_ai.keys={list(ai_val.keys()) if isinstance(ai_val, dict) else 'NOT_DICT'}, "
                    f"miku_ai.cloud_api_key={repr(ai_val.get('cloud_api_key', 'MISSING'))}"
                )
            except Exception as e:
                logger.error(f"[Config] 读取 YAML 失败: {e}，已创建新配置")
                self._config = {}

    def _save_locked(self) -> None:
        """保存当前配置到 YAML（调用方必须持有锁）。"""
        try:
            text = yaml.safe_dump(
                self._config,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=1000,
            )
            CONFIG_FILE.write_text(text, encoding="utf-8")
        except Exception as e:
            logger.error(f"[Config] 写入 YAML 失败: {e}")

    def _append_template_locked(self, plugin_name: str, template_str: str) -> None:
        """
        以「原样字符串追加」方式把插件配置模板写到文件末尾。
        模板字符串由插件自己提供（放在插件 __init__.py 里），
        不经过 yaml.safe_dump，可保留所有注释。
        追加完成后会将新插件区合并到内存配置中（不覆盖已有区块）。
        """
        try:
            header = ""
            if CONFIG_FILE.exists():
                existing_raw = CONFIG_FILE.read_text(encoding="utf-8").strip()
                if not existing_raw or existing_raw == "{}":
                    header = ""
                else:
                    try:
                        parsed = yaml.safe_load(existing_raw)
                        if isinstance(parsed, dict) and parsed:
                            header = existing_raw + "\n"
                        else:
                            header = ""
                    except Exception:
                        logger.warning(
                            f"[Config] bot.yaml 原有内容解析失败，"
                            f"将以新模板重写"
                        )
                        header = ""

            CONFIG_FILE.write_text(header + template_str, encoding="utf-8")

            # 只读取刚写入的新插件区，合并到 self._config，不覆盖其它已有区块
            try:
                data = yaml.safe_load(template_str) or {}
                if isinstance(data, dict):
                    for k, v in data.items():
                        self._config[k] = copy.deepcopy(v)
            except Exception:
                pass

            logger.info(f"[Config] 插件 {plugin_name} 配置区已自动生成（带详细注释）")
        except Exception as e:
            logger.error(f"[Config] 追加插件模板失败: {e}")

    def _extract_field_blocks(self, template_str: str, plugin_name: str) -> Dict[str, str]:
        """
        从模板字符串中提取每个字段的完整文本块（包括前面的注释行）。
        返回 {字段名: 字段文本块（含缩进和注释）} 的字典。

        策略：先找到所有字段行（二级缩进的 key: value），
        然后对每个字段行，向前收集紧邻的注释行和空行作为该字段的说明。
        """
        blocks: Dict[str, str] = {}
        lines = template_str.splitlines()

        # 找到插件区起始行和结束行
        plugin_start = -1
        plugin_end = len(lines)

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{plugin_name}:"):
                plugin_start = i
                break

        if plugin_start < 0:
            return blocks

        # 找插件区结束行（下一个顶层键）
        for i in range(plugin_start + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                continue
            if not line.startswith(" ") and not line.startswith("\t") and ":" in stripped:
                plugin_end = i
                break

        # 在插件区内，收集所有字段行的行号和字段名
        field_positions: List[tuple] = []  # [(line_index, key_name), ...]

        for i in range(plugin_start + 1, plugin_end):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # 匹配二级缩进的字段行："  key: value"
            if line.startswith("  ") and not line.startswith("    "):
                match = line[2:].split(":", 1)
                if len(match) == 2:
                    key = match[0].strip()
                    if key:
                        field_positions.append((i, key))

        # 对每个字段，向前收集注释和空行
        for idx, (field_line_idx, key) in enumerate(field_positions):
            # 确定起始位置：上一个字段结束行的下一行，或插件区起始行的下一行
            if idx == 0:
                start_line = plugin_start + 1
            else:
                start_line = field_positions[idx - 1][0] + 1

            # 从 start_line 到 field_line_idx 收集所有行
            block_lines = []
            for j in range(start_line, field_line_idx + 1):
                block_lines.append(lines[j])

            # 去掉块开头的空行
            while block_lines and not block_lines[0].strip():
                block_lines.pop(0)
            # 去掉块末尾的空行
            while block_lines and not block_lines[-1].strip():
                block_lines.pop()

            if block_lines:
                blocks[key] = "\n".join(block_lines)

        return blocks

    def _find_plugin_section_end(self, text: str, plugin_name: str) -> int:
        """
        在 YAML 文本中找到插件配置区的最后一行行号（0-based，返回最后一行的索引）。
        返回 -1 表示未找到。
        """
        lines = text.splitlines()
        plugin_start = -1

        # 找到插件区起始行
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == f"{plugin_name}:" or stripped.startswith(f"{plugin_name}:"):
                plugin_start = i
                break

        if plugin_start < 0:
            return -1

        # 从起始行往下找，找到下一个顶层键或文件末尾
        # 顶层键：非空、非缩进、包含冒号
        for i in range(plugin_start + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                continue
            # 非缩进行且包含冒号 → 下一个顶层键
            if not line.startswith(" ") and not line.startswith("\t") and ":" in line:
                # 返回插件区最后一行（即前一行）
                # 往回找最后一个非空行
                end = i - 1
                while end > plugin_start and not lines[end].strip():
                    end -= 1
                return end if end > plugin_start else plugin_start

        # 到文件末尾了
        end = len(lines) - 1
        while end > plugin_start and not lines[end].strip():
            end -= 1
        return end if end > plugin_start else plugin_start

    def _append_missing_fields_locked(
        self, plugin_name: str, template_str: str, missing_keys: List[str]
    ) -> None:
        """
        将缺失字段（带注释）追加到 YAML 中已有插件区的末尾。
        只追加缺失的字段，保留原有内容和注释不变。
        """
        if not missing_keys or not template_str:
            return

        try:
            if not CONFIG_FILE.exists():
                return

            existing_text = CONFIG_FILE.read_text(encoding="utf-8")

            # 从模板中提取所有字段块
            field_blocks = self._extract_field_blocks(template_str, plugin_name)
            if not field_blocks:
                return

            # 收集需要追加的字段块文本
            append_lines = []
            for key in missing_keys:
                block = field_blocks.get(key)
                if block:
                    # 确保块前面有空行分隔
                    if append_lines:
                        append_lines.append("")
                    append_lines.append(block)

            if not append_lines:
                return

            # 找到插件区的结束位置
            end_line = self._find_plugin_section_end(existing_text, plugin_name)
            if end_line < 0:
                return

            lines = existing_text.splitlines()

            # 在 end_line 之后插入缺失字段
            new_lines = lines[: end_line + 1] + [""] + append_lines + [""] + lines[end_line + 1 :]

            new_text = "\n".join(new_lines)
            # 确保末尾有换行
            if not new_text.endswith("\n"):
                new_text += "\n"

            CONFIG_FILE.write_text(new_text, encoding="utf-8")

            logger.info(
                f"[Config] 插件 {plugin_name} 已自动补全 {len(missing_keys)} 个配置项到 YAML: "
                f"{', '.join(missing_keys)}"
            )
        except Exception as e:
            logger.error(f"[Config] 自动补全缺失字段失败: {e}")

    def save(self) -> None:
        """保存当前配置到 YAML（会覆盖原文件，慎用，可能丢失注释）。"""
        with self._lock:
            self._save_locked()

    def reload(self) -> None:
        """
        重新加载 YAML（WebUI 热更新时调用）。
        从磁盘读取最新配置后，重新合并已注册插件的默认值（仅补齐缺失字段）。
        """
        with self._lock:
            self._load()
            self._ensure_bot_section()

            # 对已注册的插件，重新合并默认值（YAML 中的值优先级最高）
            for plugin_name in list(self._registered_plugins):
                defaults = self._plugin_defaults.get(plugin_name)
                if not defaults:
                    continue

                existing = self._config.get(plugin_name)
                if existing is None:
                    # YAML 中没有该插件区 → 用 defaults 填充
                    self._config[plugin_name] = copy.deepcopy(defaults)
                    continue

                if not isinstance(existing, dict):
                    # 不是 dict 类型，重写
                    self._config[plugin_name] = copy.deepcopy(defaults)
                    continue

                # 合并缺失字段
                changed_keys = []
                for k, v in defaults.items():
                    if k not in existing:
                        existing[k] = copy.deepcopy(v)
                        changed_keys.append(k)

            logger.info(
                f"[Config] 已热更新配置文件，当前顶层 keys: "
                f"{list(self._config.keys())}"
            )

    # ============================================================
    # 全局 bot 区：与 .env 联动，提供 superusers / webui_password 等
    # ============================================================

    def _ensure_bot_section(self) -> None:
        """确保 bot 配置区存在，并与 NoneBot 的 .env 配置联动。"""
        with self._lock:
            nb_config = get_driver().config

            bot_cfg = self._config.setdefault("bot", {})
            changed = False
            # 从 .env 同步关键字段到 YAML（仅在 YAML 为空时写入，避免覆盖用户已改内容）
            if not bot_cfg.get("superusers"):
                su = list(getattr(nb_config, "superusers", []) or [])
                bot_cfg["superusers"] = su
                changed = True
            if not bot_cfg.get("webui_password"):
                pw = getattr(nb_config, "webui_password", "") or ""
                bot_cfg["webui_password"] = str(pw)
                changed = True
            if not bot_cfg.get("nickname"):
                nk = list(getattr(nb_config, "nickname", []) or [])
                bot_cfg["nickname"] = nk
                changed = True
            if not bot_cfg.get("log_level"):
                ll = getattr(nb_config, "log_level", "INFO") or "INFO"
                bot_cfg["log_level"] = str(ll)
                changed = True
            # 监听地址/端口（仅读，不写回 .env；字段缺失时才从 .env 写入）
            if "host" not in bot_cfg:
                bot_cfg["host"] = str(getattr(nb_config, "host", "127.0.0.1"))
                changed = True
            if "port" not in bot_cfg:
                bot_cfg["port"] = int(getattr(nb_config, "port", 8080))
                changed = True

            if changed:
                # 只有在 bot 区完全是新生成时才写回，避免覆盖用户手写的 bot 配置
                if not CONFIG_FILE.exists() or not self._config.get("bot"):
                    self._save_locked()

    @property
    def superusers(self) -> List[str]:
        """超级用户 QQ 号列表。优先使用 YAML 中的值，否则回退 .env SUPERUSERS。"""
        with self._lock:
            su = self._config.get("bot", {}).get("superusers") or []
            if su:
                if isinstance(su, (list, tuple)):
                    return [str(s) for s in su]
                return [str(su)]
        env_su = list(getattr(get_driver().config, "superusers", []) or [])
        return [str(s) for s in env_su]

    @property
    def webui_password(self) -> str:
        """WebUI 登录密码。优先使用 YAML 中的值，否则回退 .env WEBUI_PASSWORD。"""
        with self._lock:
            pw = self._config.get("bot", {}).get("webui_password") or ""
        if pw:
            return str(pw)
        return str(getattr(get_driver().config, "webui_password", "") or "")

    @property
    def host(self) -> str:
        with self._lock:
            return str(self._config.get("bot", {}).get("host", "127.0.0.1"))

    @property
    def port(self) -> int:
        with self._lock:
            return int(self._config.get("bot", {}).get("port", 8080))

    # ============================================================
    # 插件配置注册 / 读取 / 修改
    # ============================================================

    def register_plugin(
        self,
        plugin_name: str,
        defaults: Dict[str, Any],
        template_str: Optional[str] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        插件首次加载时注册自己的配置区。
        模板字符串 `template_str` 由插件自己提供（放在插件 __init__.py 里）。

        写入策略：
          - YAML 中 **完全不存在**该插件区 → 以 plugin 传入的带注释 `template_str`
            追加到文件末尾（不经过 yaml.safe_dump，避免丢注释）
          - YAML 中 **已存在**该插件区但字段不完整 → 仅在**内存中**补齐缺失字段，
            **不重写文件**，避免破坏用户手写的注释；若有字段被新增，会打一条 info 日志

        :param plugin_name:   插件名，例如 "miku_weather"
        :param defaults:      默认配置 dict
        :param template_str:  带注释的 YAML 模板字符串（由插件自己提供）；
                              为空时 fallback 到 yaml.safe_dump（无注释）
        :param description:   插件说明（仅用于日志提示）
        :return:              该插件当前的实际配置（已合并默认值）
        """
        with self._lock:
            # 保存插件默认值（供 reload 时合并用）
            self._plugin_defaults[plugin_name] = copy.deepcopy(defaults)
            # 缓存模板字符串（供 WebUI 读取字段注释用）
            if template_str:
                self._plugin_templates[plugin_name] = template_str

            existing = self._config.get(plugin_name)

            # 情形 1：插件区完全不存在
            if existing is None:
                self._config[plugin_name] = copy.deepcopy(defaults)
                if template_str:
                    self._append_template_locked(plugin_name, template_str)
                else:
                    logger.warning(
                        f"[Config] 插件 {plugin_name} 未提供 template_str，"
                        f"将以无注释方式写入"
                    )
                    self._save_locked()
                self._registered_plugins.add(plugin_name)
                logger.info(
                    f"[Config] 插件 {plugin_name} 已注册配置区（{len(defaults)} 项）"
                    + (f"：{description}" if description else "")
                )
                return copy.deepcopy(self._config[plugin_name])

            # 情形 2：插件区已存在 → 内存补齐 + 自动追加缺失字段到 YAML（带注释）
            changed_keys = []
            for k, v in defaults.items():
                if k not in existing:
                    existing[k] = copy.deepcopy(v)
                    changed_keys.append(k)
            if changed_keys:
                logger.info(
                    f"[Config] 插件 {plugin_name} 发现 {len(changed_keys)} 个新增字段: "
                    f"{', '.join(changed_keys)}"
                )
                # 自动将缺失字段（带注释）追加到 YAML 中该插件区的末尾
                if template_str:
                    self._append_missing_fields_locked(plugin_name, template_str, changed_keys)
                else:
                    logger.info(
                        f"[Config] 插件 {plugin_name} 未提供 template_str，"
                        f"新增字段仅在内存中生效"
                    )

            self._registered_plugins.add(plugin_name)
            return copy.deepcopy(existing)

    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """
        读取某个插件的完整配置 dict；若不存在，返回空 dict。
        建议先调用 register_plugin(...) 以保证键的完整性。
        """
        with self._lock:
            cfg = self._config.get(plugin_name)
            if cfg is None:
                return {}
            return copy.deepcopy(cfg)

    def get_field_comments(self, plugin_name: str) -> Dict[str, str]:
        """
        从插件注册时提供的模板字符串中提取每个字段的注释文本。
        返回 {字段名: 注释说明} 的字典，无注释的字段不在结果中。
        供 WebUI 在配置页面展示字段说明。
        """
        with self._lock:
            template = self._plugin_templates.get(plugin_name)
            if not template:
                return {}
            blocks = self._extract_field_blocks(template, plugin_name)
            comments: Dict[str, str] = {}
            for key, block in blocks.items():
                # 从块中提取注释行（# 开头的行），拼接为说明文本
                comment_lines = []
                for line in block.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        # 去掉 # 和前导空格
                        text = stripped.lstrip("#").strip()
                        if text:
                            comment_lines.append(text)
                if comment_lines:
                    comments[key] = "；".join(comment_lines)
            return comments

    def get(self, plugin_name: str, key: str, default: Any = None) -> Any:
        """读取插件的单个配置项。"""
        cfg = self.get_plugin_config(plugin_name)
        if isinstance(cfg, dict) and key in cfg:
            return cfg[key]
        return default

    def set(self, plugin_name: str, key: str, value: Any) -> None:
        """
        设置插件的单个配置项并立即保存到 YAML。
        注意：yaml.safe_dump 会覆盖文件，可能丢失其它注释，请谨慎调用。
        """
        with self._lock:
            cfg = self._config.setdefault(plugin_name, {})
            if not isinstance(cfg, dict):
                cfg = {}
                self._config[plugin_name] = cfg
            cfg[key] = value
            self._save_locked()

    def update_plugin(self, plugin_name: str, data: Dict[str, Any]) -> None:
        """批量更新插件配置并立即保存（会覆盖文件，慎用）。"""
        with self._lock:
            cfg = self._config.setdefault(plugin_name, {})
            if not isinstance(cfg, dict):
                cfg = {}
                self._config[plugin_name] = cfg
            cfg.update(data)
            self._save_locked()

    def remove_plugin(self, plugin_name: str) -> None:
        """删除某个插件的配置区（会覆盖文件，慎用）。"""
        with self._lock:
            if plugin_name in self._config and plugin_name != "bot":
                del self._config[plugin_name]
                self._save_locked()
                logger.info(f"[Config] 插件 {plugin_name} 配置区已移除")

    # ============================================================
    # 其它
    # ============================================================

    def verify_webui_password(self, password: str) -> bool:
        """WebUI 登录密码验证（直接比对明文）。"""
        return str(password) == self.webui_password

    def registered_plugins(self) -> List[str]:
        """列出所有已注册的插件配置区。"""
        with self._lock:
            return sorted(self._registered_plugins)


# ─── 全局单例 ───
config_manager = ConfigManager()
