"""Persistent group styles and daily summary schedules."""

import json
import os
import time
from pathlib import Path
from typing import Any

from nonebot.log import logger

_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "miku_summary_group" / "state.json"
_state: dict[str, Any] = {"global_style": None, "groups": {}, "schedules": {}}


def load_state() -> dict[str, Any]:
    global _state
    if not _STATE_PATH.exists():
        return _state
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("顶层数据不是对象")
        _state = {
            "global_style": data.get("global_style"),
            "groups": data.get("groups") if isinstance(data.get("groups"), dict) else {},
            "schedules": data.get("schedules") if isinstance(data.get("schedules"), dict) else {},
        }
    except (OSError, json.JSONDecodeError, ValueError) as e:
        backup = _STATE_PATH.with_name(f"state.corrupted-{int(time.time())}.json")
        try:
            os.replace(_STATE_PATH, backup)
        except OSError as backup_error:
            logger.error(f"[miku_summary_group] 状态文件损坏且备份失败: {backup_error}")
        logger.error(f"[miku_summary_group] 状态文件读取失败，原文件已尝试备份: {e}")
        _state = {"global_style": None, "groups": {}, "schedules": {}}
    return _state


def save_state() -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STATE_PATH.with_suffix(".json.tmp")
    try:
        temp_path.write_text(
            json.dumps(_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, _STATE_PATH)
    except OSError as e:
        logger.error(f"[miku_summary_group] 状态文件保存失败: {e}")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def get_style(group_id: int) -> str | None:
    groups = _state["groups"]
    item = groups.get(str(group_id), {})
    if isinstance(item, dict) and item.get("style"):
        return str(item["style"])
    style = _state.get("global_style")
    return str(style) if style else None


def get_group_style(group_id: int) -> str | None:
    item = _state["groups"].get(str(group_id), {})
    if isinstance(item, dict) and item.get("style"):
        return str(item["style"])
    return None


def set_global_style(style: str | None) -> None:
    _state["global_style"] = style
    save_state()


def set_group_style(group_id: int, style: str | None) -> None:
    groups = _state["groups"]
    key = str(group_id)
    item = groups.setdefault(key, {})
    if style:
        item["style"] = style
    else:
        item.pop("style", None)
    if not item:
        groups.pop(key, None)
    save_state()


def schedules() -> dict[str, dict[str, Any]]:
    return _state["schedules"]


def set_schedule(schedule_id: str, spec: dict[str, Any] | None) -> None:
    if spec is None:
        _state["schedules"].pop(schedule_id, None)
    else:
        _state["schedules"][schedule_id] = spec
    save_state()


load_state()
