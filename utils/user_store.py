"""
MikuBot 用户数据存储 + 经济系统

用 JSON 文件保存每个用户的：
  - total_checkin_days  累计签到天数
  - coins               金币（整数）
  - favor               好感度（浮点数，0-100，精度 2 位小数）
  - last_first_time     当日首次签到时间 "HH:MM:SS"
  - last_checkin_date   "YYYY-MM-DD"  最近一次签到日期
  - history             历史签到日期列表 ["YYYY-MM-DD", ...]

  - items               道具背包  {"double_favor_card": 2, ...}
  - active_buffs        激活中的 buff  {"next_double_favor": true, ...}
  - stats               累计统计  {"total_coins_earned": ..., ...}

文件位置：data/users/{user_id}.json

======== 未来商店插件对接接口（稳定，可直接调用） ========
  - add_item(user_id, item_key, count=1)        -> 增加道具
  - use_item(user_id, item_key, count=1)        -> 消耗道具（失败抛异常）
  - get_items(user_id)                           -> 返回 dict
  - add_coins(user_id, amount)                   -> 加/减金币，返回新余额
  - get_balance(user_id)                         -> 当前金币
  - set_buff(user_id, buff_key, value=True)      -> 设置 buff
  - consume_buff(user_id, buff_key)              -> 消耗一次 buff，返回是否曾激活
  - has_buff(user_id, buff_key)                  -> 是否有该 buff
"""

import json
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "users"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# uid 全局计数器：按顺序为首次访问的用户分配 uid（000000001 起）
UID_COUNTER_FILE = DATA_DIR / "_uid_counter.json"

# 全局写锁：保护所有用户数据的读写操作，防止并发竞态
_user_lock = threading.Lock()

# UID 计数器专用锁
_uid_lock = threading.Lock()

# ---------- 签到奖励配置（集中在这里，方便后续调参） ----------
COIN_MIN = 1       # 每次签到获得的金币下限
COIN_MAX = 20      # 每次签到获得的金币上限
FAVOR_MIN = 0.10   # 每次签到获得的好感下限
FAVOR_MAX = 1.00   # 每次签到获得的好感上限
FAVOR_CAP = 100.0  # 好感度总上限
DOUBLE_FAVOR_PROB = 0.03   # 触发双倍好感的基础概率（3%）

# 道具名常量 —— 未来商店插件只需 import 这些常量即可
ITEM_DOUBLE_FAVOR = "double_favor_card"   # 双倍好感卡：下次签到好感 100% 双倍
BUFF_NEXT_DOUBLE_FAVOR = "next_double_favor"   # 激活后的 buff 名


def _user_path(user_id) -> Path:
    return DATA_DIR / f"{user_id}.json"


def _next_uid() -> str:
    """
    原子读取并递增 uid 全局计数器。
    返回格式："000000001"（9 位前导零）。
    计数器文件若不存在则从 1 开始。
    """
    import os
    with _uid_lock:
        if UID_COUNTER_FILE.exists():
            try:
                cur = int(json.loads(UID_COUNTER_FILE.read_text(encoding="utf-8")))
            except Exception:
                cur = 1
        else:
            cur = 1
        new_val = cur + 1
        tmp = UID_COUNTER_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new_val, ensure_ascii=False), encoding="utf-8")
        if os.name == "nt":
            # Windows 下 Path.replace 也可用，统一走 replace
            tmp.replace(UID_COUNTER_FILE)
        else:
            tmp.replace(UID_COUNTER_FILE)
        return f"{cur:09d}"


def _load(user_id) -> dict:
    path = _user_path(user_id)
    is_new_user = not path.exists()
    data = None
    if not is_new_user:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = None

    if not isinstance(data, dict):
        data = {}

    # 初始化/补齐字段（兼容老版本数据升级到新字段）
    data.setdefault("user_id", str(user_id))
    data.setdefault("nickname", "")     # 用户昵称（每次聊天自动更新）
    data.setdefault("first_seen", "")   # 首次见面（聊天）时间 "YYYY-MM-DD HH:MM:SS"
    data.setdefault("last_seen", "")    # 最近一次聊天时间 "YYYY-MM-DD HH:MM:SS"
    data.setdefault("chat_count", 0)    # 累计聊天次数（和 AI 对话的次数）
    data.setdefault("total_checkin_days", 0)
    data.setdefault("coins", 0)
    data.setdefault("favor", 0.0)
    data.setdefault("last_first_time", "")
    data.setdefault("last_checkin_date", "")
    data.setdefault("history", [])
    data.setdefault("items", {})
    data.setdefault("active_buffs", {})
    data.setdefault("stats", {
        "total_coins_earned": 0,
        "total_favor_earned": 0.0,
        "double_favor_triggered": 0,
    })
    # 金币强制为整数，好感度为浮点数（防止类型污染）
    try:
        data["coins"] = int(data["coins"])
    except Exception:
        data["coins"] = 0
    try:
        data["favor"] = float(data["favor"])
    except Exception:
        data["favor"] = 0.0

    # 新用户：立即分配 UID 并写入磁盘，确保任意插件首次访问即建档
    if is_new_user:
        data["uid"] = _next_uid()
        data["first_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save(user_id, data)
    elif not data.get("uid"):
        # 老用户但 UID 未分配（兼容历史数据）
        data["uid"] = _next_uid()
        _save(user_id, data)

    return data


def _save(user_id, data: dict) -> None:
    path = _user_path(user_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ============================================================
# 通用查询
# ============================================================
def get_user(user_id) -> dict:
    with _user_lock:
        return _load(user_id)


def get_balance(user_id) -> int:
    with _user_lock:
        return _load(user_id).get("coins", 0)


def get_favor(user_id) -> float:
    with _user_lock:
        return round(float(_load(user_id).get("favor", 0.0)), 2)


def get_items(user_id) -> dict:
    with _user_lock:
        return dict(_load(user_id).get("items", {}))


# ============================================================
# 用户记忆（聊天相关）
# ============================================================
def update_chat_memory(user_id, nickname: str = "") -> dict:
    """更新用户聊天记忆：昵称、首次见面时间、最后聊天时间、聊天次数。
    返回更新后的用户数据。
    """
    with _user_lock:
        data = _load(user_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 更新昵称（如果有新昵称就覆盖）
        if nickname and nickname.strip():
            data["nickname"] = str(nickname).strip()

        # 首次见面时间（只设置一次）
        if not data.get("first_seen"):
            data["first_seen"] = now

        # 最后聊天时间（每次都更新）
        data["last_seen"] = now

        # 累计聊天次数 +1
        data["chat_count"] = int(data.get("chat_count", 0)) + 1

        _save(user_id, data)
        return data


def get_user_info(user_id) -> dict:
    """获取用户完整信息（包括记忆字段）。"""
    with _user_lock:
        return _load(user_id)


def get_nickname(user_id) -> str:
    """获取用户昵称，优先从用户记忆中取。"""
    with _user_lock:
        data = _load(user_id)
        return data.get("nickname", "") or str(user_id)


# ============================================================
# 金币 / 好感度 增减（商店插件会用到）
# ============================================================
def add_coins(user_id, amount: int) -> int:
    """增加（正数）或扣除（负数）金币。返回新余额。"""
    with _user_lock:
        data = _load(user_id)
        data["coins"] = max(0, int(data.get("coins", 0)) + int(amount))
        stats = data.setdefault("stats", {})
        if amount > 0:
            stats["total_coins_earned"] = int(stats.get("total_coins_earned", 0)) + int(amount)
        _save(user_id, data)
        return data["coins"]


def set_coins(user_id, amount: int) -> int:
    """直接设置金币数量。返回新余额。"""
    with _user_lock:
        data = _load(user_id)
        data["coins"] = max(0, int(amount))
        _save(user_id, data)
        return data["coins"]


def add_favor(user_id, amount: float) -> float:
    with _user_lock:
        data = _load(user_id)
        cur = float(data.get("favor", 0.0)) + float(amount)
        cur = max(0.0, min(FAVOR_CAP, cur))
        data["favor"] = round(cur, 2)
        stats = data.setdefault("stats", {})
        if amount > 0:
            stats["total_favor_earned"] = round(
                float(stats.get("total_favor_earned", 0.0)) + float(amount), 2
            )
        _save(user_id, data)
        return data["favor"]


def set_favor(user_id, amount: float) -> float:
    """直接设置好感度。返回新好感度。"""
    with _user_lock:
        data = _load(user_id)
        cur = max(0.0, min(FAVOR_CAP, float(amount)))
        data["favor"] = round(cur, 2)
        _save(user_id, data)
        return data["favor"]


def reset_user(user_id) -> None:
    """重置用户所有数据（金币、好感、签到、道具、buff、统计）。"""
    with _user_lock:
        data = _load(user_id)
        data["coins"] = 0
        data["favor"] = 0.0
        data["total_checkin_days"] = 0
        data["items"] = {}
        data["active_buffs"] = {}
        data["stats"] = {
            "total_coins_earned": 0,
            "total_favor_earned": 0.0,
            "double_favor_triggered": 0,
        }
        _save(user_id, data)


# ============================================================
# 道具 / buff 接口（商店插件核心接口）
# ============================================================
def add_item(user_id, item_key: str, count: int = 1) -> dict:
    """增加指定数量的道具，返回更新后的 items 字典。"""
    with _user_lock:
        data = _load(user_id)
        items = data.setdefault("items", {})
        items[item_key] = int(items.get(item_key, 0)) + int(count)
        if items[item_key] < 0:
            items[item_key] = 0
        _save(user_id, data)
        return dict(items)


def use_item(user_id, item_key: str, count: int = 1) -> bool:
    """
    消耗指定数量的道具。
    成功返回 True；数量不足时返回 False，不做修改。
    """
    with _user_lock:
        data = _load(user_id)
        items = data.setdefault("items", {})
        cur = int(items.get(item_key, 0))
        if cur < int(count):
            return False
        items[item_key] = cur - int(count)
        if items[item_key] <= 0:
            del items[item_key]
        _save(user_id, data)
        return True


def set_buff(user_id, buff_key: str, value=True) -> None:
    with _user_lock:
        data = _load(user_id)
        buffs = data.setdefault("active_buffs", {})
        if value:
            buffs[buff_key] = True
        else:
            buffs.pop(buff_key, None)
        _save(user_id, data)


def has_buff(user_id, buff_key: str) -> bool:
    with _user_lock:
        data = _load(user_id)
        return bool(data.get("active_buffs", {}).get(buff_key, False))


def consume_buff(user_id, buff_key: str) -> bool:
    """
    若该 buff 激活，则消耗掉它并返回 True；否则返回 False。
    典型用法：签到时判断是否有 next_double_favor，有则消耗并触发双倍。
    """
    with _user_lock:
        data = _load(user_id)
        buffs = data.setdefault("active_buffs", {})
        if buffs.get(buff_key):
            buffs.pop(buff_key, None)
            _save(user_id, data)
            return True
        return False


# ============================================================
# 签到核心逻辑
# ============================================================
def do_checkin(
    user_id,
    coins_min: Optional[int] = None,
    coins_max: Optional[int] = None,
    favor_min: Optional[float] = None,
    favor_max: Optional[float] = None,
    double_favor_prob: Optional[float] = None,
) -> dict:
    """
    执行签到。数值参数允许从外部覆盖默认值（YAML 配置等）。

    参数:
        coins_min / coins_max: 本次签到可获得的金币范围（整数）
        favor_min / favor_max: 本次签到可获得的好感范围（浮点数）
        double_favor_prob:     双倍好感随机触发概率（0-1，例如 0.03）

    返回结果字典：
    {
        "is_first_today": bool,
        "today_first_time": "HH:MM:SS",
        "total_days": int,
        "coins": int,                  # 当前金币余额
        "favor": float,                # 当前好感度
        "coins_added": int,            # 本次获得的金币
        "favor_added": float,          # 本次获得的好感（可能已被双倍）
        "favor_base": float,           # 原始随机值（便于展示双倍效果）
        "double_favor": bool,          # 本次是否触发了双倍好感
        "double_reason": str,          # "card" / "lucky" / ""
        "date": "YYYY-MM-DD",
        "next_double_favor_buff": bool,
        "double_favor_card_count": int,
    }
    """
    # —— 参数解析 & 回退到默认常量 ——
    c_min = int(coins_min) if coins_min is not None else COIN_MIN
    c_max = int(coins_max) if coins_max is not None else COIN_MAX
    if c_min > c_max:
        c_min, c_max = c_max, c_min
    f_min = float(favor_min) if favor_min is not None else FAVOR_MIN
    f_max = float(favor_max) if favor_max is not None else FAVOR_MAX
    if f_min > f_max:
        f_min, f_max = f_max, f_min
    d_prob = float(double_favor_prob) if double_favor_prob is not None else DOUBLE_FAVOR_PROB
    if d_prob < 0.0:
        d_prob = 0.0
    if d_prob > 1.0:
        d_prob = 1.0

    with _user_lock:
        data = _load(user_id)
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().strftime("%H:%M:%S")

        is_first_today = data.get("last_checkin_date") != today

        coins_added = 0
        favor_added = 0.0
        favor_base = 0.0
        double_favor = False
        double_reason = ""

        if is_first_today:
            # —— 金币：随机整数（范围来自 YAML 配置或默认值）——
            coins_added = random.randint(c_min, c_max)

            # —— 好感：随机浮点数（范围来自 YAML 配置或默认值）——
            favor_base = round(random.uniform(f_min, f_max), 2)
            favor_added = favor_base

            # —— 双倍好感判定：优先消耗卡片激活的 buff，其次 概率随机 ——
            buffs = data.setdefault("active_buffs", {})
            if buffs.get(BUFF_NEXT_DOUBLE_FAVOR, False):
                # 由商店道具激活的 100% 必双倍
                buffs.pop(BUFF_NEXT_DOUBLE_FAVOR, None)
                double_favor = True
                double_reason = "card"
                favor_added = round(favor_base * 2, 2)
            elif random.random() < d_prob:
                # 欧皇概率触发（默认 3%）
                double_favor = True
                double_reason = "lucky"
                favor_added = round(favor_base * 2, 2)

            # 写入数据
            data["total_checkin_days"] = int(data.get("total_checkin_days", 0)) + 1
            data["coins"] = int(data.get("coins", 0)) + coins_added
            cur_favor = float(data.get("favor", 0.0)) + favor_added
            data["favor"] = round(min(FAVOR_CAP, cur_favor), 2)
            data["last_first_time"] = now
            data["last_checkin_date"] = today
            data.setdefault("history", []).append(today)

            # 统计
            stats = data.setdefault("stats", {})
            stats["total_coins_earned"] = int(stats.get("total_coins_earned", 0)) + coins_added
            stats["total_favor_earned"] = round(
                float(stats.get("total_favor_earned", 0.0)) + favor_added, 2
            )
            if double_favor:
                stats["double_favor_triggered"] = int(stats.get("double_favor_triggered", 0)) + 1

            _save(user_id, data)

        # —— 返回结果（无论是否首次）——
        items = data.get("items", {})
        buffs = data.get("active_buffs", {})
        return {
            "is_first_today": is_first_today,
            "today_first_time": data.get("last_first_time", now),
            "total_days": data.get("total_checkin_days", 0),
            "coins": data.get("coins", 0),
            "favor": round(float(data.get("favor", 0.0)), 2),
            "coins_added": coins_added,
            "favor_added": favor_added,
            "favor_base": favor_base,
            "double_favor": double_favor,
            "double_reason": double_reason,
            "date": today,
            "next_double_favor_buff": bool(buffs.get(BUFF_NEXT_DOUBLE_FAVOR, False)),
            "double_favor_card_count": int(items.get(ITEM_DOUBLE_FAVOR, 0)),
            "uid": data.get("uid", ""),
        }


# ============================================================
# 商店：使用双倍好感卡（给未来商店插件直接调用的高层接口）
# ============================================================
def use_double_favor_card(user_id) -> bool:
    """
    使用一张「双倍好感卡」：下次签到好感 100% 双倍。
    - 成功：扣一张卡并激活 next_double_favor buff，返回 True
    - 没有卡：返回 False
    - 已有同样 buff（已激活未用）：返回 False，避免重复覆盖
    """
    with _user_lock:
        data = _load(user_id)
        buffs = data.setdefault("active_buffs", {})
        if buffs.get(BUFF_NEXT_DOUBLE_FAVOR, False):
            return False   # 已有同样 buff，别叠加浪费
        items = data.setdefault("items", {})
        if int(items.get(ITEM_DOUBLE_FAVOR, 0)) <= 0:
            return False
        items[ITEM_DOUBLE_FAVOR] = int(items[ITEM_DOUBLE_FAVOR]) - 1
        if items[ITEM_DOUBLE_FAVOR] <= 0:
            del items[ITEM_DOUBLE_FAVOR]
        buffs[BUFF_NEXT_DOUBLE_FAVOR] = True
        _save(user_id, data)
        return True
