"""
Miku AI 表情包库管理
基于SQLite的表情包去重和复用
"""

import json
import time
import sqlite3
from pathlib import Path
from typing import Optional, Dict
from nonebot.log import logger

from .config import get_config, PROJECT_ROOT


def _get_emoji_db_path() -> Path:
    path_str = str(get_config("emoji_library_path", "data/emoji_library.db") or "data/emoji_library.db")
    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


class EmojiLibrary:
    """表情包库SQLite管理"""
    
    _conn: Optional[sqlite3.Connection] = None
    _hash_cache: Dict[str, dict] = {}
    _hash_cache_times: Dict[str, int] = {}
    
    @classmethod
    def init_db(cls):
        """初始化表情包库数据库"""
        db_path = _get_emoji_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emoji_library (
                image_hash TEXT PRIMARY KEY,
                first_seen INTEGER NOT NULL,
                total_uses INTEGER DEFAULT 1,
                ocr_text TEXT DEFAULT '',
                character TEXT DEFAULT '',
                emotion_tag TEXT DEFAULT '',
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                is_emoji INTEGER DEFAULT 0,
                ai_description TEXT DEFAULT '',
                last_updated INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        cls._conn = conn
        logger.info(f"[miku_ai] 表情包库已加载: {db_path}")
        return conn
    
    @classmethod
    def get_db(cls) -> sqlite3.Connection:
        """获取数据库连接，懒加载"""
        if cls._conn is None:
            return cls.init_db()
        return cls._conn
    
    @classmethod
    def query_by_hash(cls, image_hash: str) -> Optional[Dict]:
        """根据感知哈希查询表情包库记录"""
        if not image_hash:
            return None
        now = int(time.time())
        cache_ttl_hours = int(get_config("emoji_hash_window_hours", 24) or 24)
        cache_ttl = cache_ttl_hours * 3600
        
        if image_hash in cls._hash_cache:
            cache_time = cls._hash_cache_times.get(image_hash, 0)
            if now - cache_time < cache_ttl:
                cached = cls._hash_cache[image_hash]
                desc = cached.get("description", "")
                if desc and "识别失败" in desc:
                    return None
                return cached
        
        try:
            db = cls.get_db()
            row = db.execute(
                "SELECT * FROM emoji_library WHERE image_hash = ?",
                (image_hash,)
            ).fetchone()
            if row:
                result = dict(row)
                result["tags"] = json.loads(result["tags"]) if result.get("tags") else []
                result["is_emoji"] = bool(result["is_emoji"])
                desc = result.get("description", "")
                if desc and "识别失败" in desc:
                    return None
                db.execute(
                    "UPDATE emoji_library SET total_uses = total_uses + 1 WHERE image_hash = ?",
                    (image_hash,)
                )
                db.commit()
                result["total_uses"] = result.get("total_uses", 0) + 1
                cls._hash_cache[image_hash] = result
                cls._hash_cache_times[image_hash] = now
                if len(cls._hash_cache) > 500:
                    sorted_times = sorted(cls._hash_cache_times.items(), key=lambda x: x[1])
                    for h, _ in sorted_times[:250]:
                        cls._hash_cache.pop(h, None)
                        cls._hash_cache_times.pop(h, None)
                return result
        except Exception as e:
            logger.warning(f"[miku_ai] 查询表情包库失败: {e}")
        return None
    
    @classmethod
    def upsert(cls, image_hash: str, info: Dict):
        """插入或更新表情包库记录"""
        if not image_hash:
            return
        now = int(time.time())
        try:
            db = cls.get_db()
            existing = db.execute(
                "SELECT total_uses FROM emoji_library WHERE image_hash = ?",
                (image_hash,)
            ).fetchone()
            tags_json = json.dumps(info.get("tags", []), ensure_ascii=False)
            if existing:
                db.execute("""
                    UPDATE emoji_library SET
                        ocr_text = ?, character = ?, emotion_tag = ?,
                        description = ?, tags = ?, is_emoji = ?,
                        ai_description = ?, last_updated = ?
                    WHERE image_hash = ?
                """, (
                    info.get("ocr_text", ""),
                    info.get("character", ""),
                    info.get("emotion_tag", ""),
                    info.get("description", ""),
                    tags_json,
                    1 if info.get("is_emoji", False) else 0,
                    info.get("ai_description", ""),
                    now,
                    image_hash,
                ))
            else:
                db.execute("""
                    INSERT INTO emoji_library
                        (image_hash, first_seen, total_uses, ocr_text, character,
                         emotion_tag, description, tags, is_emoji, ai_description, last_updated)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    image_hash, now,
                    info.get("ocr_text", ""),
                    info.get("character", ""),
                    info.get("emotion_tag", ""),
                    info.get("description", ""),
                    tags_json,
                    1 if info.get("is_emoji", False) else 0,
                    info.get("ai_description", ""),
                    now,
                ))
            db.commit()
            info["total_uses"] = (existing["total_uses"] + 1) if existing else 1
            info["first_seen"] = now
            info["last_updated"] = now
            cls._hash_cache[image_hash] = info
            cls._hash_cache_times[image_hash] = now
        except Exception as e:
            logger.warning(f"[miku_ai] 更新表情包库失败: {e}")
    
    @classmethod
    def cleanup_old(cls):
        """清理冷门表情包：total_uses=1 且 first_seen 超过 N 天"""
        try:
            db = cls.get_db()
            now = int(time.time())
            cleanup_days = int(get_config("emoji_cleanup_days", 90) or 90)
            threshold = now - cleanup_days * 24 * 3600
            cursor = db.execute(
                "SELECT COUNT(*) as cnt FROM emoji_library WHERE total_uses = 1 AND first_seen < ?",
                (threshold,)
            ).fetchone()
            count = cursor["cnt"] if cursor else 0
            if count > 0:
                db.execute(
                    "DELETE FROM emoji_library WHERE total_uses = 1 AND first_seen < ?",
                    (threshold,)
                )
                db.commit()
                to_remove = []
                for h, t in cls._hash_cache_times.items():
                    if t < threshold:
                        to_remove.append(h)
                for h in to_remove:
                    cls._hash_cache.pop(h, None)
                    cls._hash_cache_times.pop(h, None)
                logger.info(f"[miku_ai] 表情包库清理完成，删除 {count} 条冷门记录")
        except Exception as e:
            logger.warning(f"[miku_ai] 表情包库清理异常: {e}")
