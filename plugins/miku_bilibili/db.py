"""
Miku B站插件 - SQLite数据库操作模块
订阅数据存储和管理
"""

import sqlite3
import time
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from nonebot.log import logger

from .config import DB_PATH
from .model import SubscribeItem, SubscriptionTarget, SubType


def _get_db_path() -> Path:
    """获取数据库路径"""
    return DB_PATH


def get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_conn()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                name TEXT DEFAULT '',
                face TEXT DEFAULT '',
                room_id TEXT DEFAULT '',
                enable_dynamic INTEGER DEFAULT 1,
                enable_video INTEGER DEFAULT 1,
                enable_live INTEGER DEFAULT 1,
                at_all_dynamic INTEGER DEFAULT 0,
                at_all_video INTEGER DEFAULT 0,
                at_all_live INTEGER DEFAULT 0,
                last_dynamic_ts INTEGER DEFAULT 0,
                last_video_bvid TEXT DEFAULT '',
                last_video_created INTEGER DEFAULT 0,
                last_live_status INTEGER DEFAULT 0,
                last_season_ep_id INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                UNIQUE(sub_type, target_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_id INTEGER NOT NULL,
                target_id TEXT NOT NULL,
                created_at REAL DEFAULT 0,
                FOREIGN KEY (sub_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
                UNIQUE(sub_id, target_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_id TEXT PRIMARY KEY,
                auto_parse INTEGER DEFAULT 1,
                updated_at REAL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sub_targets_target_id 
            ON subscription_targets(target_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sub_targets_sub_id 
            ON subscription_targets(sub_id)
        """)

        conn.commit()
        logger.info("[miku_bilibili] 数据库初始化完成")
    except Exception as e:
        logger.error(f"[miku_bilibili] 数据库初始化失败: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# 订阅 CRUD 操作
# ============================================================

def _row_to_subscribe_item(row: sqlite3.Row) -> SubscribeItem:
    """将数据库行转换为SubscribeItem"""
    return SubscribeItem(
        id=row["id"],
        sub_type=SubType(row["sub_type"]),
        target_id=row["target_id"],
        name=row["name"] or "",
        face=row["face"] or "",
        room_id=row["room_id"] or "",
        enable_dynamic=bool(row["enable_dynamic"]),
        enable_video=bool(row["enable_video"]),
        enable_live=bool(row["enable_live"]),
        at_all_dynamic=bool(row["at_all_dynamic"]),
        at_all_video=bool(row["at_all_video"]),
        at_all_live=bool(row["at_all_live"]),
        last_dynamic_ts=row["last_dynamic_ts"] or 0,
        last_video_bvid=row["last_video_bvid"] or "",
        last_video_created=row["last_video_created"] or 0,
        last_live_status=row["last_live_status"] or 0,
        last_season_ep_id=row["last_season_ep_id"] or 0,
        created_at=row["created_at"] or 0.0,
        updated_at=row["updated_at"] or 0.0,
    )


def get_subscription_by_id(sub_id: int) -> Optional[SubscribeItem]:
    """根据ID获取订阅"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?",
            (sub_id,)
        ).fetchone()
        if row:
            return _row_to_subscribe_item(row)
        return None
    finally:
        conn.close()


def get_subscription_by_target(sub_type: SubType, target_id: str) -> Optional[SubscribeItem]:
    """根据类型和目标ID获取订阅"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE sub_type = ? AND target_id = ?",
            (sub_type.value, target_id)
        ).fetchone()
        if row:
            return _row_to_subscribe_item(row)
        return None
    finally:
        conn.close()


def get_all_subscriptions() -> List[SubscribeItem]:
    """获取所有订阅"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        return [_row_to_subscribe_item(row) for row in rows]
    finally:
        conn.close()


def create_subscription(item: SubscribeItem) -> int:
    """创建订阅，返回订阅ID"""
    conn = get_conn()
    now = time.time()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO subscriptions (
                sub_type, target_id, name, face, room_id,
                enable_dynamic, enable_video, enable_live,
                at_all_dynamic, at_all_video, at_all_live,
                last_dynamic_ts, last_video_bvid, last_video_created,
                last_live_status, last_season_ep_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.sub_type.value, item.target_id, item.name, item.face, item.room_id,
            int(item.enable_dynamic), int(item.enable_video), int(item.enable_live),
            int(item.at_all_dynamic), int(item.at_all_video), int(item.at_all_live),
            item.last_dynamic_ts, item.last_video_bvid, item.last_video_created,
            item.last_live_status, item.last_season_ep_id,
            now, now
        ))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        existing = get_subscription_by_target(item.sub_type, item.target_id)
        if existing:
            return existing.id
        raise
    finally:
        conn.close()


def update_subscription(item: SubscribeItem) -> bool:
    """更新订阅"""
    conn = get_conn()
    now = time.time()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE subscriptions SET
                name = ?, face = ?, room_id = ?,
                enable_dynamic = ?, enable_video = ?, enable_live = ?,
                at_all_dynamic = ?, at_all_video = ?, at_all_live = ?,
                last_dynamic_ts = ?, last_video_bvid = ?, last_video_created = ?,
                last_live_status = ?, last_season_ep_id = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            item.name, item.face, item.room_id,
            int(item.enable_dynamic), int(item.enable_video), int(item.enable_live),
            int(item.at_all_dynamic), int(item.at_all_video), int(item.at_all_live),
            item.last_dynamic_ts, item.last_video_bvid, item.last_video_created,
            item.last_live_status, item.last_season_ep_id,
            now, item.id
        ))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_subscription(sub_id: int) -> bool:
    """删除订阅（同时级联删除目标关系）"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ============================================================
# 订阅目标关系操作
# ============================================================

def get_target_subscriptions(target_id: str) -> List[SubscribeItem]:
    """获取指定目标（群/私聊）的所有订阅"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT s.* FROM subscriptions s
            INNER JOIN subscription_targets st ON s.id = st.sub_id
            WHERE st.target_id = ?
            ORDER BY s.id
        """, (target_id,)).fetchall()
        return [_row_to_subscribe_item(row) for row in rows]
    finally:
        conn.close()


def add_subscription_target(sub_id: int, target_id: str) -> bool:
    """添加订阅目标关系"""
    conn = get_conn()
    now = time.time()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO subscription_targets (sub_id, target_id, created_at)
            VALUES (?, ?, ?)
        """, (sub_id, target_id, now))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def remove_subscription_target(sub_id: int, target_id: str) -> bool:
    """移除订阅目标关系"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM subscription_targets 
            WHERE sub_id = ? AND target_id = ?
        """, (sub_id, target_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_subscription_targets(sub_id: int) -> List[str]:
    """获取订阅的所有目标ID"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT target_id FROM subscription_targets WHERE sub_id = ?",
            (sub_id,)
        ).fetchall()
        return [row["target_id"] for row in rows]
    finally:
        conn.close()


def clean_orphaned_subscriptions() -> int:
    """清理没有目标关系的孤立订阅"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM subscriptions 
            WHERE id NOT IN (SELECT DISTINCT sub_id FROM subscription_targets)
        """)
        count = cursor.rowcount
        conn.commit()
        if count > 0:
            logger.info(f"[miku_bilibili] 清理了 {count} 个孤立订阅")
        return count
    finally:
        conn.close()


# ============================================================
# 群组设置操作
# ============================================================

def is_group_auto_parse_enabled(group_id: str) -> bool:
    """检查群组是否启用被动解析（默认启用）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT auto_parse FROM group_settings WHERE group_id = ?",
            (group_id,)
        ).fetchone()
        if row:
            return bool(row["auto_parse"])
        return True
    finally:
        conn.close()


def set_group_auto_parse(group_id: str, enabled: bool) -> None:
    """设置群组被动解析开关"""
    conn = get_conn()
    now = time.time()
    try:
        conn.execute("""
            INSERT INTO group_settings (group_id, auto_parse, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                auto_parse = excluded.auto_parse,
                updated_at = excluded.updated_at
        """, (group_id, int(enabled), now))
        conn.commit()
    finally:
        conn.close()


def get_group_setting(group_id: str, key: str, default=None):
    """获取群组设置（通用接口，目前支持 enabled/auto_parse）"""
    if key in ("enabled", "auto_parse"):
        return is_group_auto_parse_enabled(group_id)
    return default


def set_group_setting(group_id: str, key: str, value) -> None:
    """设置群组设置（通用接口）"""
    if key in ("enabled", "auto_parse"):
        set_group_auto_parse(group_id, bool(value))
