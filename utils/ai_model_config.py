"""Global AI model configuration and migration from the legacy plugin section."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any, Dict

import yaml

from nonebot.log import logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
BOT_CONFIG_FILE = CONFIG_DIR / "bot.yaml"
AI_MODEL_CONFIG_FILE = CONFIG_DIR / "ai_models.yaml"
BACKUP_DIR = CONFIG_DIR / "backups"

AI_MODEL_DEFAULTS: Dict[str, Any] = {
    "ai_platforms": [],
}
# 旧版本地模型配置键：仅用于从 bot.yaml/ai_models.yaml 迁移到 ai_platforms，运行时不再使用
LEGACY_LOCAL_KEYS = ("local_base_url", "local_model", "local_api_key")
AI_MODEL_CONFIG_KEYS = frozenset(AI_MODEL_DEFAULTS) | frozenset(LEGACY_LOCAL_KEYS)
_AI_MODEL_CONFIG_HEADER = (
    "# 全局 AI 模型连接配置，与 miku_ai 插件参数分离。\n"
    "# ai_platforms 按顺序故障转移；model 用于多模态，text_model 留空时沿用 model。\n"
    "# 平台字段：name / api_key / base_url / model / text_model\n"
    "#\n"
    "# - name: 示例云端平台\n"
    "#   api_key: 'sk-xxx'\n"
    "#   base_url: https://api.example.com/v1\n"
    "#   model: vision-model\n"
    "#   text_model: ''\n"
    "#\n"
    "# 本地 OpenAI 兼容服务（Ollama / LM Studio 等）也作为普通平台配置，api_key 可留空：\n"
    "# - name: 本地模型\n"
    "#   api_key: ''\n"
    "#   base_url: http://localhost:11434/v1\n"
    "#   model: qwen2.5:7b\n"
    "#   text_model: ''\n"
)

_LOCAL_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")


def _is_local_base_url(url: Any) -> bool:
    text = str(url or "").strip().lower()
    return any(marker in text for marker in _LOCAL_HOST_MARKERS)


def _validate_ai_model_config(data: Any) -> Dict[str, Any]:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("AI 模型配置根节点必须是 YAML 对象")

    config = deepcopy(AI_MODEL_DEFAULTS)
    config.update(data)
    platforms = config.get("ai_platforms")
    if not isinstance(platforms, list) or any(not isinstance(item, dict) for item in platforms):
        raise ValueError("ai_platforms 必须是对象数组")
    return config


def parse_ai_model_config(content: str) -> Dict[str, Any]:
    """Parse and validate editable YAML content for the global AI model config."""
    try:
        data = yaml.safe_load(content) if content.strip() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 格式错误: {exc}") from exc
    return _validate_ai_model_config(data)


def get_ai_model_config() -> Dict[str, Any]:
    """Read the latest global model configuration from disk."""
    if not AI_MODEL_CONFIG_FILE.exists():
        return deepcopy(AI_MODEL_DEFAULTS)
    content = AI_MODEL_CONFIG_FILE.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(content) if content.strip() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{AI_MODEL_CONFIG_FILE} YAML 格式错误: {exc}") from exc
    return _validate_ai_model_config(data)


def default_ai_model_config_text() -> str:
    """Return a starter document for the global AI model configuration editor."""
    return _AI_MODEL_CONFIG_HEADER + yaml.safe_dump(
        deepcopy(AI_MODEL_DEFAULTS),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )


def dump_ai_model_config(data: Dict[str, Any]) -> str:
    validated = _validate_ai_model_config(data)
    return _AI_MODEL_CONFIG_HEADER + yaml.safe_dump(
        validated,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )


def save_ai_model_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, back up, and atomically save the global AI model configuration."""
    validated = _validate_ai_model_config(data)
    AI_MODEL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if AI_MODEL_CONFIG_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(AI_MODEL_CONFIG_FILE, BACKUP_DIR / f"{AI_MODEL_CONFIG_FILE.name}.{timestamp}")

    temp_file = AI_MODEL_CONFIG_FILE.with_suffix(".yaml.tmp")
    try:
        temp_file.write_text(dump_ai_model_config(validated), encoding="utf-8")
        temp_file.replace(AI_MODEL_CONFIG_FILE)
    finally:
        if temp_file.exists():
            temp_file.unlink()
    return validated


def migrate_local_model_to_platforms() -> bool:
    """把旧版 local_base_url/local_model/local_api_key 迁移为 ai_platforms 中的普通平台。

    迁移后本地模型与云端平台完全同构：可在 WebUI「AI平台列表」中编辑/排序/删除，
    并参与按顺序故障转移。迁移只执行一次，完成后删除旧键，避免删除平台后被重新加回。
    """
    if not AI_MODEL_CONFIG_FILE.exists():
        return False
    try:
        config = get_ai_model_config()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logger.warning(f"[AI 配置] 读取 ai_models.yaml 失败，跳过本地模型迁移: {exc}")
        return False

    if not any(key in config for key in LEGACY_LOCAL_KEYS):
        return False

    local_url = str(config.get("local_base_url", "") or "").strip().rstrip("/")
    local_model = str(config.get("local_model", "") or "").strip()
    local_key = str(config.get("local_api_key", "") or "")
    platforms = [dict(p) for p in (config.get("ai_platforms") or []) if isinstance(p, dict)]

    if local_url and local_model and not any(
        _is_local_base_url(p.get("base_url")) for p in platforms
    ):
        platforms.append({
            "name": "本地模型",
            "api_key": local_key,
            "base_url": local_url,
            "model": local_model,
            "text_model": "",
        })

    for key in LEGACY_LOCAL_KEYS:
        config.pop(key, None)
    config["ai_platforms"] = platforms

    try:
        save_ai_model_config(config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logger.warning(f"[AI 配置] 本地模型迁移保存失败: {exc}")
        return False
    logger.info(
        "[AI 配置] 已将本地模型迁移到 ai_platforms，可在 WebUI「AI平台列表」中编辑"
    )
    return True


def _remove_legacy_ai_settings(content: str) -> str:
    root = yaml.compose(content)
    if not isinstance(root, yaml.MappingNode):
        return content

    ai_section = None
    for key_node, value_node in root.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "miku_ai":
            ai_section = value_node
            break
    if not isinstance(ai_section, yaml.MappingNode):
        return content

    lines = content.splitlines(keepends=True)
    entries = []
    for key_node, value_node in ai_section.value:
        if isinstance(key_node, yaml.ScalarNode):
            entries.append((key_node, value_node))

    removed_lines = set()
    for key_node, value_node in entries:
        if key_node.value not in AI_MODEL_CONFIG_KEYS:
            continue
        key_line = key_node.start_mark.line
        preceding_field_end = ai_section.start_mark.line
        for prior_key, prior_value in entries:
            if prior_key.start_mark.line >= key_line:
                break
            preceding_field_end = max(preceding_field_end, prior_value.end_mark.line)

        comment_start = key_line
        cursor = key_line - 1
        while cursor > preceding_field_end:
            stripped = lines[cursor].strip()
            if stripped and not stripped.startswith("#"):
                break
            comment_start = cursor
            cursor -= 1

        end_line = min(value_node.end_mark.line, len(lines) - 1)
        removed_lines.update(range(comment_start, end_line + 1))

    return "".join(line for index, line in enumerate(lines) if index not in removed_lines)


def migrate_legacy_ai_config() -> bool:
    """Move model connection settings out of bot.yaml once, preserving the other YAML text."""
    if not BOT_CONFIG_FILE.exists():
        return False

    content = BOT_CONFIG_FILE.read_text(encoding="utf-8")
    try:
        document = yaml.safe_load(content) if content.strip() else {}
    except yaml.YAMLError as exc:
        logger.warning(f"[AI 配置] bot.yaml 格式无效，暂不迁移旧模型配置: {exc}")
        return False

    if not isinstance(document, dict):
        return False
    ai_section = document.get("miku_ai")
    if not isinstance(ai_section, dict):
        return False
    legacy = {key: ai_section[key] for key in AI_MODEL_CONFIG_KEYS if key in ai_section}
    if not legacy:
        return False

    if AI_MODEL_CONFIG_FILE.exists():
        current = get_ai_model_config()
        target_config = current
    else:
        target_config = deepcopy(AI_MODEL_DEFAULTS)
        target_config.update(legacy)
        _validate_ai_model_config(target_config)

    updated_content = _remove_legacy_ai_settings(content)
    if AI_MODEL_CONFIG_FILE.exists():
        # Validate the existing file before removing the legacy source.
        get_ai_model_config()
    else:
        AI_MODEL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = AI_MODEL_CONFIG_FILE.with_suffix(".yaml.tmp")
        try:
            temp_file.write_text(
                dump_ai_model_config(target_config),
                encoding="utf-8",
            )
            temp_file.replace(AI_MODEL_CONFIG_FILE)
        finally:
            if temp_file.exists():
                temp_file.unlink()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(BOT_CONFIG_FILE, BACKUP_DIR / f"bot.yaml.{timestamp}")
    BOT_CONFIG_FILE.write_text(updated_content, encoding="utf-8")
    logger.info("[AI 配置] 已清理旧版模型项，统一使用 config/ai_models.yaml")
    return True


LEGACY_AI_CONFIG_MIGRATED = migrate_legacy_ai_config()
LOCAL_MODEL_MIGRATED = migrate_local_model_to_platforms()
