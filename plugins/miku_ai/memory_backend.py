"""
Miku AI 会话记忆后端（SQLite 版）
聊天记录持久化：单文件 SQLite，支持 message_id 去重与被动记录增强（upsert）
好感度统一由 utils.user_store 管理，避免双写不一致
"""

import json
import time
import sys
from pathlib import Path
from typing import Dict, List, Optional

import sqlite3
from nonebot.log import logger

from .config import get_config, PROJECT_ROOT

# 导入 user_store 作为好感度唯一数据源
sys.path.insert(0, str(PROJECT_ROOT))
from utils.user_store import get_favor, add_favor as _us_add_favor


def _get_db_path() -> Path:
    p = Path(str(get_config("history_db_path", "data/ai_chat_history.db") or "data/ai_chat_history.db"))
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def _dump_content(content) -> str:
    """content 可能为 str（纯文本）或 list（多模态），list 序列化为 JSON 存储"""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _load_content(raw: str):
    """读取时还原多模态 content（JSON 数组/对象），失败则按纯文本处理"""
    if raw and raw[0] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


class ChatHistoryManager:
    """聊天记录管理器（SQLite）"""

    _instance = None
    _conn: Optional[sqlite3.Connection] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id TEXT DEFAULT '',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                user_name TEXT DEFAULT '',
                images TEXT DEFAULT '',
                is_group INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id)")
        # 部分唯一索引：仅非空 message_id 参与去重（AI 回复/系统消息 message_id 为空可重复）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_dedup ON messages(session_id, message_id) "
            "WHERE message_id != ''"
        )
        conn.commit()
        self._conn = conn
        total = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        logger.info(f"[miku_ai] 聊天记录库已加载: {db_path}（共 {total} 条）")

    # ── 兼容旧接口：save 系列在 SQLite 即时落盘模式下为轻量维护操作 ──

    async def save(self):
        self._checkpoint()

    async def save_history_async(self):
        self._checkpoint()

    def _checkpoint(self):
        try:
            if self._conn is not None:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.debug(f"[miku_ai] WAL checkpoint 跳过: {e}")

    def get_session_id(self, user_id: str, group_id: str = "") -> str:
        if group_id:
            return f"group_{group_id}"
        return f"private_{user_id}"

    def _trim(self, session_id: str, is_group: bool):
        max_count = int(get_config("group_history_max", 1000) if is_group else get_config("private_history_max", 500))
        over = self._conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()["c"]
        if over > max_count:
            self._conn.execute(
                "DELETE FROM messages WHERE session_id=? AND id IN ("
                "  SELECT id FROM messages WHERE session_id=? ORDER BY id ASC LIMIT ?)",
                (session_id, session_id, over - max_count),
            )

    def append_message(self, session_id: str, role: str, content,
                       user_id: str = "", user_name: str = "",
                       images: list = None, is_group: bool = False,
                       message_id: str = "", timestamp: int = 0) -> bool:
        """
        追加消息；message_id 非空时按 (session_id, message_id) upsert：
        已存在（被动钩子先记录的原始消息）→ 更新内容/用户名（AI 识图增强后回写）
        不存在 → 插入。message_id 为空（assistant 回复等）→ 直接插入。
        返回是否新插入（False=更新或忽略）。
        """
        conn = self._conn
        now = int(timestamp or time.time())
        content_str = _dump_content(content)
        images_str = json.dumps(images, ensure_ascii=False) if images else ""
        mid = str(message_id or "").strip()
        if mid:
            existing = conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND message_id=?",
                (session_id, mid),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE messages SET content=?, user_name=?, images=?, created_at=? WHERE id=?",
                    (content_str, user_name or "", images_str, now, existing["id"]),
                )
                conn.commit()
                return False
        conn.execute(
            "INSERT INTO messages(session_id, message_id, role, content, user_id, user_name, "
            "images, is_group, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, mid, role, content_str, user_id or "", user_name or "",
             images_str, 1 if is_group else 0, now),
        )
        self._trim(session_id, is_group)
        conn.commit()
        return True

    def get_history(self, session_id: str, exclude_message_id: str = "") -> List[Dict]:
        rows = self._conn.execute(
            "SELECT message_id, role, content, user_id, user_name, images, created_at "
            "FROM messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        exclude = str(exclude_message_id or "").strip()
        history = []
        for r in rows:
            if exclude and r["message_id"] == exclude:
                continue
            msg = {
                "role": r["role"],
                "content": _load_content(r["content"]),
                "timestamp": r["created_at"],
                "user_id": r["user_id"],
                "user_name": r["user_name"],
            }
            if r["message_id"]:
                msg["message_id"] = r["message_id"]
            if r["images"]:
                try:
                    msg["images"] = json.loads(r["images"])
                except Exception:
                    pass
            history.append(msg)
        return history

    def get_raw_messages(self, session_id: str, limit: int = 50,
                         exclude_message_id: str = "") -> List[Dict]:
        """获取原始消息列表（用于动态上下文构建）"""
        history = self.get_history(session_id, exclude_message_id=exclude_message_id)
        return history[-limit:] if len(history) > limit else list(history)

    def get_context_messages(self, session_id: str, is_group: bool = False,
                             system_prompt: str = "", max_context: int = 0,
                             bot_nickname: str = "",
                             exclude_message_id: str = "") -> List[Dict]:
        """获取用于发送给AI的上下文消息，群聊中附带用户名便于AI区分对话者"""
        history = self.get_history(session_id, exclude_message_id=exclude_message_id)

        # 时间窗口过滤（0=禁用）：只保留最近N分钟内的消息，避免隔夜旧话题进入AI上下文
        window_minutes = int(get_config("context_time_window_minutes", 0) or 0)
        if window_minutes > 0:
            cutoff = time.time() - window_minutes * 60
            history = [m for m in history if m.get("timestamp", 0) >= cutoff]

        if max_context <= 0:
            max_context = int(get_config("group_context_max", 25) if is_group else get_config("private_context_max", 30))

        context = []
        recent = history[-max_context:] if len(history) > max_context else history

        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            user_name = msg.get("user_name", "")

            # 群聊中用户消息附带用户名，让AI知道是谁说的
            if is_group and role == "user" and user_name:
                if isinstance(content, str) and content:
                    content = f"[{user_name}]: {content}"
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            part["text"] = f"[{user_name}]: {part['text']}"

            # 群聊中机器人消息用昵称
            if is_group and role == "assistant" and bot_nickname:
                if isinstance(content, str) and content:
                    content = f"[{bot_nickname}]: {content}"

            # 历史消息不再重放图片：QQ 图片直链几分钟后即失效，重放过期 URL 会导致
            # API 报 1210「图片输入格式/解析错误」。图片内容在保存时已以文字描述
            # （表情包识别结果/[图片] 占位）并入 content，此处仅保留文本
            context.append({"role": role, "content": content})

        if system_prompt:
            context.insert(0, {"role": "system", "content": system_prompt})

        return context

    def clear_history(self, session_id: str):
        self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        self._conn.commit()

    def session_ids(self) -> List[str]:
        rows = self._conn.execute("SELECT DISTINCT session_id FROM messages").fetchall()
        return [r["session_id"] for r in rows]

    def get_favor(self, user_id: str) -> float:
        """获取好感度（代理到 user_store，保证数据一致性）"""
        try:
            return get_favor(user_id)
        except Exception as e:
            logger.warning(f"[miku_ai] 获取好感度失败: {e}")
            return 0.0

    def set_favor(self, user_id: str, value: float):
        """设置好感度（代理到 user_store，保证数据一致性）"""
        try:
            from utils.user_store import set_favor as _us_set_favor
            _us_set_favor(user_id, value)
        except Exception as e:
            logger.warning(f"[miku_ai] 设置好感度失败: {e}")

    def add_favor(self, user_id: str, delta: float) -> float:
        """增加好感度（代理到 user_store，保证数据一致性）"""
        try:
            return _us_add_favor(user_id, delta)
        except Exception as e:
            logger.warning(f"[miku_ai] 增加好感度失败: {e}")
            return 0.0


def get_history_manager() -> ChatHistoryManager:
    """获取聊天记录管理器单例"""
    return ChatHistoryManager()
