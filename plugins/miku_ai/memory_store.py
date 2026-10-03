"""
Miku AI 长期记忆存储（自研轻量 SQLite 实现）

命名空间分层（物理隔离，杜绝群聊/私聊记忆串味）：
  - group:{gid}            群公共记忆（管理员/群事件相关）
  - group:{gid}:user:{uid} 群内个人画像（某人在某群的偏好/信息）
  - user:{uid}             私聊个人记忆（跨群跟随用户）

特性：中文 bigram 关键词检索 + 短语命中加权 + 新近度加成、
     TTL 过期、内容归一化去重、每命名空间容量上限。
所有键均为 QQ 号/群号，绝不以昵称为键。
"""

import re
import time
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from nonebot.log import logger

from .config import get_config, PROJECT_ROOT

# 记忆内容写入前剔除控制字符与多余空白
_WS_RE = re.compile(r"\s+")
# 归一化去重用：去掉所有空白与常见标点差异
_NORM_RE = re.compile(r"[\s，。！!？?、,.;；:：~～「」『』\"'`()（）\[\]【】]+")


def _db_path() -> Path:
    p = Path(str(get_config("memory_db_path", "data/ai_memory.db") or "data/ai_memory.db"))
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def _ttl_seconds() -> int:
    days = int(get_config("memory_ttl_days", 0) or 0)
    return days * 86400


def _max_per_namespace() -> int:
    return max(10, int(get_config("memory_max_per_namespace", 200) or 200))


def normalize(text: str) -> str:
    """归一化：去空白/标点、统一小写，用于去重判定"""
    return _NORM_RE.sub("", (text or "")).lower()


def _bigrams(text: str) -> set:
    """中文字符 bigram 集合（零依赖的轻量中文检索方案）"""
    t = normalize(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


class MemoryStore:
    """长期记忆 SQLite 存储（同步 sqlite3，与 emoji_library 同款模式）"""

    _conn: Optional[sqlite3.Connection] = None

    @classmethod
    def init_db(cls):
        db_path = _db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL,
                content TEXT NOT NULL,
                norm TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                weight REAL DEFAULT 1.0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                expires_at INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ns ON memories(namespace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ns_norm ON memories(namespace, norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_exp ON memories(expires_at)")
        conn.commit()
        cls._conn = conn
        removed = cls._purge_expired()
        logger.info(f"[miku_ai] 长期记忆库已加载: {db_path}（过期清理 {removed} 条）")
        return conn

    @classmethod
    def get_db(cls) -> sqlite3.Connection:
        if cls._conn is None:
            return cls.init_db()
        return cls._conn

    # ── 写入 ──

    @classmethod
    def remember(cls, namespace: str, content: str, source: str = "manual",
                 ttl_seconds: int = 0) -> bool:
        """写入/更新一条记忆；同命名空间内容归一化去重（命中则刷新时间与权重）"""
        content = _WS_RE.sub(" ", (content or "").strip())
        if not namespace or not content:
            return False
        conn = cls.get_db()
        now = int(time.time())
        if ttl_seconds <= 0:
            ttl_seconds = _ttl_seconds()
        expires = now + ttl_seconds if ttl_seconds > 0 else 0
        norm = normalize(content)
        row = conn.execute(
            "SELECT id FROM memories WHERE namespace=? AND norm=?", (namespace, norm)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE memories SET content=?, updated_at=?, expires_at=?, "
                "weight=MIN(weight+0.5, 5.0) WHERE id=?",
                (content, now, expires, row["id"]),
            )
            conn.commit()
            return True
        conn.execute(
            "INSERT INTO memories(namespace, content, norm, source, weight, created_at, "
            "updated_at, expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (namespace, content, norm, source, 1.0, now, now, expires),
        )
        cls._enforce_capacity(conn, namespace)
        conn.commit()
        return True

    @classmethod
    def _enforce_capacity(cls, conn: sqlite3.Connection, namespace: str):
        cap = _max_per_namespace()
        over = conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE namespace=?", (namespace,)
        ).fetchone()["c"]
        if over <= cap:
            return
        conn.execute(
            "DELETE FROM memories WHERE namespace=? AND id IN ("
            "  SELECT id FROM memories WHERE namespace=?"
            "  ORDER BY weight ASC, updated_at ASC LIMIT ?)",
            (namespace, namespace, over - cap),
        )

    # ── 删除 ──

    @classmethod
    def forget(cls, namespace: str, keyword: str) -> int:
        """按内容子串匹配删除（同时匹配归一化形式），返回删除条数"""
        kw = (keyword or "").strip()
        if not namespace or not kw:
            return 0
        conn = cls.get_db()
        norm_kw = normalize(kw)
        cur = conn.execute(
            "DELETE FROM memories WHERE namespace=? AND (content LIKE ? OR norm LIKE ?)",
            (namespace, f"%{kw}%", f"%{norm_kw}%"),
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    @classmethod
    def forget_namespace(cls, namespace: str) -> int:
        """清空整个命名空间"""
        conn = cls.get_db()
        cur = conn.execute("DELETE FROM memories WHERE namespace=?", (namespace,))
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    @classmethod
    def _purge_expired(cls) -> int:
        conn = cls.get_db()
        now = int(time.time())
        cur = conn.execute(
            "DELETE FROM memories WHERE expires_at>0 AND expires_at<=?", (now,)
        )
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ── 检索 ──

    @classmethod
    def recall(cls, namespaces: List[str], query: str, top_k: int = 0) -> List[Dict]:
        """
        按命名空间列表检索相关记忆（优先级即传入顺序，越靠前权重越高）。
        打分 = 短语命中(+3) + bigram 重叠率 + 新近度加成；TTL 过滤顺带清理。
        返回 [{content, namespace, score, updated_at}]，按分数降序。
        """
        if not namespaces:
            return []
        conn = cls.get_db()
        cls._purge_expired()
        q_norm = normalize(query)
        q_bi = _bigrams(query)
        placeholders = ",".join("?" * len(namespaces))
        rows = conn.execute(
            f"SELECT namespace, content, weight, updated_at FROM memories "
            f"WHERE namespace IN ({placeholders}) ORDER BY updated_at DESC LIMIT 500",
            tuple(namespaces),
        ).fetchall()
        if not rows:
            return []
        now = int(time.time())
        ns_rank = {ns: i for i, ns in enumerate(namespaces)}
        scored: List[Dict] = []
        for r in rows:
            content = r["content"]
            score = 0.0
            if q_norm and q_norm in normalize(content):
                score += 3.0
            c_bi = _bigrams(content)
            if q_bi and c_bi:
                overlap = len(q_bi & c_bi) / max(1, min(len(q_bi), 12))
                score += overlap * 4.0
            if score <= 0:
                continue
            # 新近度：30 天内线性加成，最高 +1
            days_old = max(0, now - r["updated_at"]) / 86400
            score += max(0.0, 1.0 - days_old / 30.0)
            score *= r["weight"]
            # 命名空间优先级微调（用户画像 > 群公共）
            score += (len(namespaces) - ns_rank.get(r["namespace"], 0)) * 0.01
            if score >= 1.0:
                scored.append({
                    "content": content,
                    "namespace": r["namespace"],
                    "score": round(score, 3),
                    "updated_at": r["updated_at"],
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        if top_k <= 0:
            top_k = max(1, int(get_config("memory_recall_top_k", 4) or 4))
        return scored[:top_k]

    # ── 查看 ──

    @classmethod
    def list_memories(cls, namespace: str, limit: int = 30) -> List[Dict]:
        conn = cls.get_db()
        cls._purge_expired()
        rows = conn.execute(
            "SELECT content, source, weight, created_at, updated_at FROM memories "
            "WHERE namespace=? ORDER BY updated_at DESC LIMIT ?",
            (namespace, max(1, limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def count(cls, namespace: Optional[str] = None) -> int:
        conn = cls.get_db()
        if namespace:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM memories WHERE namespace=?", (namespace,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()
        return row["c"]


def build_namespaces(user_id: str, group_id: str = "") -> List[str]:
    """按场景构造命名空间列表（私聊：个人；群聊：个人画像 > 群公共）"""
    uid = str(user_id or "").strip()
    gid = str(group_id or "").strip()
    if gid:
        return [f"group:{gid}:user:{uid}", f"group:{gid}"] if uid else [f"group:{gid}"]
    return [f"user:{uid}"] if uid else []
