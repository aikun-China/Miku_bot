"""
MikuBot 黑名单管理模块
======================
提供用户/群组封禁管理功能，支持：
- 用户级封禁（全局）
- 群组级封禁（仅在指定群有效）
- 临时封禁（自动解封）
- 永久封禁
- 封禁权限验证

使用示例：
```python
from utils.ban_manager import BanManager, BanInfo

# 封禁用户（永久）
await BanManager.ban_user(
    user_id="123456",
    operator_id="admin",
    reason="违规行为",
    duration=-1,  # -1 表示永久
    ban_level=5,
)

# 封禁用户在指定群（7天）
await BanManager.ban_user(
    user_id="123456",
    group_id="789",
    operator_id="admin",
    reason="广告",
    duration=60 * 24 * 7,  # 7天（分钟）
    ban_level=3,
)

# 检查是否被封禁
is_banned = await BanManager.is_banned(user_id="123456")
is_banned_in_group = await BanManager.is_banned(user_id="123456", group_id="789")

# 获取剩余封禁时间（秒）
remaining = await BanManager.get_remaining_time(user_id="123456")
# 返回 -1 表示永久封禁，0 表示未被封禁

# 解封用户
await BanManager.unban(user_id="123456")

# 解封用户在指定群的封禁
await BanManager.unban(user_id="123456", group_id="789")

# 获取封禁信息
info = await BanManager.get_ban_info(user_id="123456")

# 获取所有封禁记录
all_bans = await BanManager.get_all_bans(include_expired=False)

# 清理过期封禁
cleaned = await BanManager.cleanup_expired()
```

封禁级别（ban_level）：
- 用于验证解封权限，只有 ban_level >= 操作者的 level 时才能解封
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from utils.database import Database, BanModel, get_db
from utils.cache_manager import CacheManager, CacheType

logger = logging.getLogger("miku.ban")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class BanInfo:
    """封禁信息"""
    id: int
    user_id: Optional[str]
    group_id: Optional[str]
    ban_level: int
    ban_time: int  # Unix 时间戳
    duration: int   # 持续分钟数，-1 表示永久
    reason: Optional[str]
    operator_id: str
    created_at: str

    def get_remaining_time(self) -> int:
        """
        获取剩余封禁时间（秒）

        返回:
            int: 剩余秒数，-1 表示永久封禁，0 表示已过期
        """
        if self.duration == -1:
            return -1

        elapsed = time.time() - self.ban_time
        remaining_seconds = (self.duration * 60) - elapsed

        return max(0, int(remaining_seconds))

    def is_expired(self) -> bool:
        """检查是否已过期"""
        if self.duration == -1:
            return False
        return self.get_remaining_time() == 0

    def is_permanent(self) -> bool:
        """检查是否永久封禁"""
        return self.duration == -1

    def format_duration(self) -> str:
        """格式化封禁时长"""
        if self.duration == -1:
            return "永久"

        minutes = self.duration
        if minutes < 60:
            return f"{minutes}分钟"
        elif minutes < 60 * 24:
            hours = minutes // 60
            return f"{hours}小时"
        else:
            days = minutes // (60 * 24)
            return f"{days}天"

    def format_remaining(self) -> str:
        """格式化剩余时间"""
        remaining = self.get_remaining_time()
        if remaining == -1:
            return "永久"
        if remaining == 0:
            return "已过期"

        if remaining < 60:
            return f"{remaining}秒"
        elif remaining < 3600:
            return f"{remaining // 60}分钟"
        elif remaining < 86400:
            hours = remaining // 3600
            return f"{hours}小时"
        else:
            days = remaining // 86400
            return f"{days}天"


# ============================================================
# 异常类
# ============================================================
class BanException(Exception):
    """封禁相关异常"""
    pass


class InsufficientBanLevel(BanException):
    """封禁权限不足"""
    def __init__(self, required: int, actual: int):
        self.required = required
        self.actual = actual
        super().__init__(f"权限不足：需要 {required}，当前 {actual}")


class UserAndGroupBothNone(BanException):
    """用户ID和群组ID都为空"""
    pass


# ============================================================
# 黑名单管理器
# ============================================================
class BanManager:
    """
    黑名单管理器

    提供用户/群组的封禁管理功能。
    """

    # 缓存 TTL（秒）
    CACHE_TTL = 300  # 5分钟

    # 默认封禁级别
    DEFAULT_BAN_LEVEL = 1

    @classmethod
    async def _get_cache_key(cls, user_id: str = None,
                            group_id: str = None) -> str:
        """生成缓存键"""
        parts = ["ban"]
        if user_id:
            parts.append(f"u{user_id}")
        if group_id:
            parts.append(f"g{group_id}")
        return ":".join(parts)

    @classmethod
    async def _check_cache(cls, user_id: str = None,
                          group_id: str = None) -> Optional[int]:
        """检查缓存"""
        key = await cls._get_cache_key(user_id, group_id)
        return await CacheManager.get(key, CacheType.BAN)

    @classmethod
    async def _set_cache(cls, user_id: str = None,
                        group_id: str = None,
                        remaining: int = 0):
        """设置缓存"""
        key = await cls._get_cache_key(user_id, group_id)
        # 缓存时间比实际剩余时间多一点，避免频繁查库
        cache_time = max(60, remaining // 2) if remaining > 0 else cls.CACHE_TTL
        await CacheManager.set(key, remaining, cache_time, CacheType.BAN)

    @classmethod
    async def _invalidate_cache(cls, user_id: str = None,
                                group_id: str = None):
        """使缓存失效"""
        key = await cls._get_cache_key(user_id, group_id)
        await CacheManager.delete(key, CacheType.BAN)

    # ============================================================
    # 基础操作
    # ============================================================
    @classmethod
    async def ban_user(cls, user_id: str, operator_id: str,
                      reason: str = "", duration: int = -1,
                      ban_level: int = None,
                      group_id: str = None):
        """
        封禁用户

        参数:
            user_id: 用户ID
            operator_id: 操作者ID
            reason: 封禁原因
            duration: 封禁时长（分钟），-1 表示永久
            ban_level: 封禁级别（用于验证解封权限）
            group_id: 群组ID（为空表示全局封禁）
        """
        if not user_id and not group_id:
            raise UserAndGroupBothNone()

        if ban_level is None:
            ban_level = cls.DEFAULT_BAN_LEVEL

        db = await get_db()
        now = datetime.now()
        ban_time = int(time.time())

        # 查找是否已存在
        existing = await cls._find_ban(user_id, group_id)

        if existing:
            # 更新现有记录
            await db.execute(
                "UPDATE ban_list SET "
                "ban_level = ?, ban_time = ?, duration = ?, "
                "reason = ?, operator_id = ? "
                "WHERE id = ?",
                (ban_level, ban_time, duration, reason, operator_id, existing.id)
            )
            logger.info(f"[BanManager] 更新封禁: user={user_id}, group={group_id}, "
                       f"duration={duration}分钟, reason={reason}")
        else:
            # 创建新记录
            await db.insert(BanModel, {
                "user_id": user_id,
                "group_id": group_id,
                "ban_level": ban_level,
                "ban_time": ban_time,
                "duration": duration,
                "reason": reason,
                "operator_id": operator_id,
                "created_at": now.isoformat(),
            })
            logger.info(f"[BanManager] 封禁用户: user={user_id}, group={group_id}, "
                       f"duration={duration}分钟, reason={reason}")

        # 使缓存失效
        await cls._invalidate_cache(user_id, group_id)

    @classmethod
    async def unban(cls, user_id: str = None,
                   group_id: str = None) -> bool:
        """
        解封用户/群组

        参数:
            user_id: 用户ID
            group_id: 群组ID

        返回:
            bool: 是否成功解封
        """
        if not user_id and not group_id:
            raise UserAndGroupBothNone()

        db = await get_db()

        # 查找记录
        ban_info = await cls._find_ban(user_id, group_id)
        if not ban_info:
            return False

        # 删除记录
        await db.execute("DELETE FROM ban_list WHERE id = ?", (ban_info.id,))

        logger.info(f"[BanManager] 解封: user={user_id}, group={group_id}")

        # 使缓存失效
        await cls._invalidate_cache(user_id, group_id)

        return True

    @classmethod
    async def _find_ban(cls, user_id: str = None,
                       group_id: str = None) -> Optional[BanInfo]:
        """查找封禁记录"""
        db = await get_db()

        # 构建查询条件
        conditions = []
        params = []

        if user_id:
            conditions.append("(user_id = ? OR user_id IS NULL)")
            params.append(user_id)
        else:
            conditions.append("user_id IS NULL")

        if group_id:
            conditions.append("(group_id = ? OR group_id IS NULL)")
            params.append(group_id)
        else:
            conditions.append("group_id IS NULL")

        where = " AND ".join(conditions)

        row = await db.fetchone(
            f"SELECT * FROM ban_list WHERE {where} ORDER BY created_at DESC LIMIT 1",
            tuple(params)
        )

        if not row:
            return None

        return BanInfo(
            id=row["id"],
            user_id=row["user_id"],
            group_id=row["group_id"],
            ban_level=row["ban_level"],
            ban_time=row["ban_time"],
            duration=row["duration"],
            reason=row["reason"],
            operator_id=row["operator_id"],
            created_at=row["created_at"],
        )

    # ============================================================
    # 查询操作
    # ============================================================
    @classmethod
    async def is_banned(cls, user_id: str = None,
                       group_id: str = None) -> bool:
        """
        检查是否被封禁

        参数:
            user_id: 用户ID
            group_id: 群组ID

        返回:
            bool: 是否被封禁
        """
        remaining = await cls.get_remaining_time(user_id, group_id)
        return remaining != 0

    @classmethod
    async def get_remaining_time(cls, user_id: str = None,
                                group_id: str = None) -> int:
        """
        获取剩余封禁时间

        参数:
            user_id: 用户ID
            group_id: 群组ID

        返回:
            int: 剩余秒数，-1 表示永久封禁，0 表示未被封禁
        """
        # 先检查缓存
        cached = await cls._check_cache(user_id, group_id)
        if cached is not None:
            return cached

        # 查询数据库
        ban_info = await cls._find_ban(user_id, group_id)

        if not ban_info:
            await cls._set_cache(user_id, group_id, 0)
            return 0

        remaining = ban_info.get_remaining_time()
        await cls._set_cache(user_id, group_id, remaining)

        return remaining

    @classmethod
    async def get_ban_info(cls, user_id: str = None,
                          group_id: str = None) -> Optional[BanInfo]:
        """获取封禁信息"""
        return await cls._find_ban(user_id, group_id)

    @classmethod
    async def get_all_bans(cls, include_expired: bool = False) -> list[BanInfo]:
        """
        获取所有封禁记录

        参数:
            include_expired: 是否包含已过期的记录

        返回:
            list[BanInfo]: 封禁记录列表
        """
        db = await get_db()

        rows = await db.fetchall("SELECT * FROM ban_list ORDER BY created_at DESC")

        bans = []
        for row in rows:
            ban_info = BanInfo(
                id=row["id"],
                user_id=row["user_id"],
                group_id=row["group_id"],
                ban_level=row["ban_level"],
                ban_time=row["ban_time"],
                duration=row["duration"],
                reason=row["reason"],
                operator_id=row["operator_id"],
                created_at=row["created_at"],
            )

            if include_expired or not ban_info.is_expired():
                bans.append(ban_info)

        return bans

    @classmethod
    async def get_user_bans(cls, user_id: str) -> list[BanInfo]:
        """获取用户的所有封禁记录"""
        db = await get_db()

        rows = await db.fetchall(
            "SELECT * FROM ban_list WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )

        return [
            BanInfo(
                id=row["id"],
                user_id=row["user_id"],
                group_id=row["group_id"],
                ban_level=row["ban_level"],
                ban_time=row["ban_time"],
                duration=row["duration"],
                reason=row["reason"],
                operator_id=row["operator_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @classmethod
    async def get_group_bans(cls, group_id: str) -> list[BanInfo]:
        """获取群组的所有封禁记录"""
        db = await get_db()

        rows = await db.fetchall(
            "SELECT * FROM ban_list WHERE group_id = ? ORDER BY created_at DESC",
            (group_id,)
        )

        return [
            BanInfo(
                id=row["id"],
                user_id=row["user_id"],
                group_id=row["group_id"],
                ban_level=row["ban_level"],
                ban_time=row["ban_time"],
                duration=row["duration"],
                reason=row["reason"],
                operator_id=row["operator_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ============================================================
    # 权限验证
    # ============================================================
    @classmethod
    async def can_unban(cls, operator_level: int,
                       user_id: str = None,
                       group_id: str = None) -> bool:
        """
        检查是否有权限解封

        参数:
            operator_level: 操作者的权限级别
            user_id: 用户ID
            group_id: 群组ID

        返回:
            bool: 是否有权限
        """
        ban_info = await cls._find_ban(user_id, group_id)
        if not ban_info:
            return True  # 未被封禁，可以解封

        return operator_level >= ban_info.ban_level

    # ============================================================
    # 清理操作
    # ============================================================
    @classmethod
    async def cleanup_expired(cls) -> int:
        """
        清理已过期的封禁记录

        返回:
            int: 清理的记录数
        """
        db = await get_db()
        now = time.time()

        # 删除已过期且非永久的记录
        cursor = await db.execute(
            "DELETE FROM ban_list WHERE duration != -1 AND (ban_time + duration * 60) < ?",
            (now,)
        )

        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"[BanManager] 清理过期封禁: {deleted} 条")

            # 清空封禁相关缓存
            await CacheManager.clear(CacheType.BAN)

        return deleted

    # ============================================================
    # 批量操作
    # ============================================================
    @classmethod
    async def batch_ban(cls, user_ids: list[str], operator_id: str,
                       reason: str = "", duration: int = -1):
        """批量封禁用户"""
        for user_id in user_ids:
            try:
                await cls.ban_user(user_id, operator_id, reason, duration)
            except Exception as e:
                logger.error(f"[BanManager] 批量封禁失败: {user_id}, 错误: {e}")

    @classmethod
    async def batch_unban(cls, user_ids: list[str]):
        """批量解封用户"""
        for user_id in user_ids:
            try:
                await cls.unban(user_id)
            except Exception as e:
                logger.error(f"[BanManager] 批量解封失败: {user_id}, 错误: {e}")


# ============================================================
# 便捷函数
# ============================================================
async def is_banned(user_id: str = None, group_id: str = None) -> bool:
    """检查是否被封禁"""
    return await BanManager.is_banned(user_id, group_id)


async def get_remaining_time(user_id: str = None, group_id: str = None) -> int:
    """获取剩余封禁时间"""
    return await BanManager.get_remaining_time(user_id, group_id)


async def ban(user_id: str, operator_id: str, reason: str = "",
             duration: int = -1, group_id: str = None):
    """封禁用户"""
    await BanManager.ban_user(user_id, operator_id, reason, duration, group_id=group_id)


async def unban(user_id: str = None, group_id: str = None) -> bool:
    """解封"""
    return await BanManager.unban(user_id, group_id)
