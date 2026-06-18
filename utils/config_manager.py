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
        self._load()
        self._ensure_bot_section()

    # ============================================================
    # 文件读写
    # ============================================================

    def _load(self) -> None:
        """从 YAML 文件读取配置；文件不存在则创建空骨架并保存。"""
        with self._lock:
            if not CONFIG_FILE.exists():
                self._config = {}
                self._save_locked()
                logger.info(f"[Config] 新建配置文件: {CONFIG_FILE}")
                return

            try:
                text = CONFIG_FILE.read_text(encoding="utf-8")
                data = yaml.safe_load(text) or {}
                if not isinstance(data, dict):
                    logger.warning("[Config] YAML 根节点不是 dict，已重置为 {}")
                    data = {}
                self._config = data
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
        追加完成后会 reload 内存配置。
        """
        try:
            existing = ""
            if CONFIG_FILE.exists():
                existing = CONFIG_FILE.read_text(encoding="utf-8")
                if not existing.endswith("\n"):
                    existing += "\n"

            CONFIG_FILE.write_text(existing + template_str, encoding="utf-8")

            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                self._config = data

            logger.info(f"[Config] 插件 {plugin_name} 配置区已自动生成（带详细注释）")
        except Exception as e:
            logger.error(f"[Config] 追加插件模板失败: {e}")

    def save(self) -> None:
        """保存当前配置到 YAML（会覆盖原文件，慎用，可能丢失注释）。"""
        with self._lock:
            self._save_locked()

    def reload(self) -> None:
        """重新加载 YAML。"""
        with self._lock:
            self._load()
            self._ensure_bot_section()

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
                return list(su)
        return list(getattr(get_driver().config, "superusers", []) or [])

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
            existing = self._config.get(plugin_name)

            # 情形 1：插件区完全不存在
            if existing is None:
                self._config[plugin_name] = copy.deepcopy(defaults)
                if template_str:
                    self._append_template_locked(plugin_name, template_str)
                else:
                    # fallback：直接用 yaml.safe_dump（无注释）
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

            # 情形 2：插件区已存在 → 仅在内存中补齐缺失字段，不写回文件
            changed_keys = []
            for k, v in defaults.items():
                if k not in existing:
                    existing[k] = copy.deepcopy(v)
                    changed_keys.append(k)
            if changed_keys:
                logger.info(
                    f"[Config] 插件 {plugin_name} 内存中补齐字段: "
                    f"{', '.join(changed_keys)}（未写入 YAML，避免覆盖用户注释；"
                    f"如需写入，请手动编辑 bot.yaml 或删除该插件区后重启）"
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
