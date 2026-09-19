"""
MikuBot 金币管理模块
====================
提供金币变动的详细日志记录和统计功能。

主要功能：
- 金币变动日志记录
- 金币变动统计查询
- 金币预扣机制（用于插件执行中途可能失败的情况）

使用示例：
```python
from utils.gold_manager import GoldManager, GoldHandle

# 记录金币变动
await GoldManager.log_gold(
    user_id="123456",
    amount=100,
    balance=500,
    handle_type=GoldHandle.EARN,
    source="sign",
    memo="每日签到"
)

# 查询金币变动历史
history = await GoldManager.get_history("123456", limit=10)

# 统计用户金币收支
stats = await GoldManager.get_user_stats("123456")

# 预扣金币（用于需要回滚的场景）
reservation = await GoldManager.reserve("123456", 100, GoldHandle.SHOPPING, "miku_shop")
try:
    # 执行业务逻辑
    await deliver_goods("123456", "item_001")
    await reservation.commit()  # 确认扣款
except Exception as e:
    await reservation.release()  # 回滚
```

金币处理类型（GoldHandle）：
- EARN: 获得金币（签到、奖励等）
- SPEND: 消费金币（购买商品）
- REDUCE: 扣除金币（管理员操作、惩罚）
- TRANSFER_IN: 转入
- TRANSFER_OUT: 转出
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Optional
from dataclasses import dataclass

from utils.database import Database, GoldLogModel, UserModel, get_db

logger = logging.getLogger("miku.gold")


# ============================================================
# 金币处理类型枚举
# ============================================================
class GoldHandle(Enum):
    """金币处理类型"""
    EARN = "EARN"           # 获得金币
    SPEND = "SPEND"        # 消费金币
    REDUCE = "REDUCE"      # 扣除金币（管理员操作）
    TRANSFER_IN = "TIN"     # 转入
    TRANSFER_OUT = "TOUT"   # 转出


# ============================================================
# 预扣记录数据结构
# ============================================================
@dataclass
class GoldReservation:
    """
    金币预扣记录

    用于插件执行中途可能失败的情况。
    先预扣金币，业务成功后 commit() 确认，失败时 release() 回滚。
    """
    user_id: str
    amount: int
    handle_type: GoldHandle
    source: str
    memo: str
    _committed: bool = False
    _released: bool = False

    async def commit(self):
        """确认扣款，永久生效"""
        if self._committed or self._released:
            return

        self._committed = True
        logger.debug(f"[GoldReservation] 确认扣款: {self.user_id} -{self.amount}")

    async def release(self):
        """回滚扣款，退还金币"""
        if self._released or self._committed:
            return

        db = await get_db()

        # 返还金币
        await db.execute(
            "UPDATE users SET gold = gold + ?, total_earned = total_earned + ? "
            "WHERE user_id = ?",
            (self.amount, self.amount, self.user_id)
        )

        # 记录返还日志
        await log_gold(
            user_id=self.user_id,
            amount=self.amount,
            balance=0,  # 不记录余额，因为已经返回
            handle_type=GoldHandle.EARN,
            source="rollback",
            memo=f"回滚: {self.source} - {self.memo}",
        )

        self._released = True
        logger.debug(f"[GoldReservation] 回滚扣款: {self.user_id} +{self.amount}")


# ============================================================
# 金币日志记录
# ============================================================
async def log_gold(user_id: str, amount: int, balance: int,
                  handle_type: GoldHandle, source: str,
                  memo: str = "") -> int:
    """
    记录金币变动日志

    参数:
        user_id: 用户ID
        amount: 变动数量（正数表示获得，负数表示支出）
        balance: 变动后余额
        handle_type: 处理类型
        source: 来源（插件名）
        memo: 备注

    返回:
        int: 日志记录ID
    """
    db = await get_db()

    # 转换枚举为字符串
    if isinstance(handle_type, GoldHandle):
        handle_str = handle_type.value
    else:
        handle_str = str(handle_type)

    now = datetime.now().isoformat()

    log_id = await db.insert(GoldLogModel, {
        "user_id": user_id,
        "amount": amount,
        "balance": balance,
        "handle_type": handle_str,
        "source": source,
        "memo": memo,
        "created_at": now,
    })

    return log_id


# ============================================================
# 金币管理器
# ============================================================
class GoldManager:
    """
    金币管理器

    提供金币的高级操作，包括：
    - 日志记录
    - 历史查询
    - 统计分析
    - 预扣机制
    """

    # 日志保留天数（默认30天）
    LOG_RETENTION_DAYS = 30

    @classmethod
    async def log_gold(cls, user_id: str, amount: int, balance: int,
                      handle_type: GoldHandle, source: str,
                      memo: str = "") -> int:
        """记录金币变动（类方法包装）"""
        return await log_gold(user_id, amount, balance, handle_type, source, memo)

    @classmethod
    async def reserve(cls, user_id: str, amount: int,
                     handle_type: GoldHandle, source: str,
                     memo: str = "") -> GoldReservation:
        """
        预扣金币

        用于插件执行中途可能失败的情况。
        先预扣金币，业务成功后 commit() 确认，失败时 release() 回滚。

        参数:
            user_id: 用户ID
            amount: 预扣数量
            handle_type: 处理类型
            source: 来源（插件名）
            memo: 备注

        返回:
            GoldReservation: 预扣记录对象

        使用示例：
        ```python
        reservation = await GoldManager.reserve(
            user_id, price, GoldHandle.SPEND, "miku_shop", "购买商品"
        )
        try:
            await deliver_goods(user_id, goods_name)
            await reservation.commit()
        except Exception:
            await reservation.release()
        ```
        """
        if amount <= 0:
            raise ValueError("预扣金币数量必须为正数")

        db = await get_db()

        # 原子预扣 + 守卫条件
        row = await db.fetchone(
            "UPDATE users SET gold = gold - ? "
            "WHERE user_id = ? AND gold >= ? "
            "RETURNING gold",
            (amount, user_id, amount)
        )

        if not row:
            from utils.user_manager import InsufficientGold
            user = await db.select_one(UserModel, "user_id = ?", (user_id,))
            current = user["gold"] if user else 0
            raise InsufficientGold(required=amount, actual=current)

        # 记录预扣日志
        new_balance = row[0]
        await cls.log_gold(
            user_id=user_id,
            amount=-amount,
            balance=new_balance,
            handle_type=handle_type,
            source=source,
            memo=f"[预扣] {memo}" if memo else "[预扣]",
        )

        logger.debug(f"[GoldManager] 预扣金币: {user_id} -{amount} (余额: {new_balance})")

        return GoldReservation(
            user_id=user_id,
            amount=amount,
            handle_type=handle_type,
            source=source,
            memo=memo,
        )

    @classmethod
    async def get_history(cls, user_id: str, limit: int = 20,
                         offset: int = 0,
                         source: str = "") -> list[dict]:
        """
        获取用户金币变动历史

        参数:
            user_id: 用户ID
            limit: 返回数量
            offset: 偏移量
            source: 按来源筛选（空字符串表示全部）

        返回:
            list[dict]: 金币变动记录列表
        """
        db = await get_db()

        if source:
            where = "user_id = ? AND source = ?"
            params = (user_id, source, limit, offset)
            count_where = "user_id = ? AND source = ?"
            count_params = (user_id, source)
        else:
            where = "user_id = ?"
            params = (user_id, limit, offset)
            count_where = "user_id = ?"
            count_params = (user_id,)

        # 查询记录
        rows = await db.fetchall(
            f"SELECT * FROM gold_log WHERE {count_where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params
        )

        return [dict(row) for row in rows]

    @classmethod
    async def get_user_stats(cls, user_id: str) -> dict:
        """
        获取用户金币统计

        返回:
            dict: 包含 total_earned, total_spent, net 的统计
        """
        db = await get_db()

        row = await db.fetchone(
            "SELECT total_earned, total_spent FROM users WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            return {
                "total_earned": 0,
                "total_spent": 0,
                "net": 0,
            }

        earned = row["total_earned"] or 0
        spent = row["total_spent"] or 0

        return {
            "total_earned": earned,
            "total_spent": spent,
            "net": earned - spent,
        }

    @classmethod
    async def get_daily_stats(cls, user_id: str,
                              date: str = "") -> dict:
        """
        获取用户每日金币统计

        参数:
            user_id: 用户ID
            date: 日期（YYYY-MM-DD格式），空表示今天

        返回:
            dict: 每日统计
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        db = await get_db()

        rows = await db.fetchall(
            "SELECT handle_type, SUM(ABS(amount)) as total "
            "FROM gold_log "
            "WHERE user_id = ? AND created_at LIKE ? "
            "GROUP BY handle_type",
            (user_id, f"{date}%")
        )

        stats = {
            "date": date,
            "earned": 0,
            "spent": 0,
            "records": {},
        }

        for row in rows:
            handle = row["handle_type"]
            total = row["total"]
            stats["records"][handle] = total

            if handle in ("EARN", "TRANSFER_IN"):
                stats["earned"] += total
            else:
                stats["spent"] += total

        return stats

    @classmethod
    async def get_source_stats(cls, user_id: str,
                               limit: int = 10) -> list[dict]:
        """
        获取用户按来源统计的金币变动

        参数:
            user_id: 用户ID
            limit: 返回数量

        返回:
            list[dict]: 来源统计列表
        """
        db = await get_db()

        rows = await db.fetchall(
            "SELECT source, handle_type, SUM(ABS(amount)) as total, COUNT(*) as count "
            "FROM gold_log "
            "WHERE user_id = ? "
            "GROUP BY source, handle_type "
            "ORDER BY total DESC "
            "LIMIT ?",
            (user_id, limit)
        )

        return [dict(row) for row in rows]

    @classmethod
    async def cleanup_old_logs(cls, days: int = None) -> int:
        """
        清理旧的金币日志

        参数:
            days: 保留天数，默认 LOG_RETENTION_DAYS

        返回:
            int: 删除的记录数
        """
        if days is None:
            days = cls.LOG_RETENTION_DAYS

        db = await get_db()

        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        cursor = await db.execute(
            "DELETE FROM gold_log WHERE created_at < ?",
            (cutoff,)
        )

        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"[GoldManager] 清理旧金币日志: {deleted} 条")

        return deleted

    @classmethod
    async def get_top_spenders(cls, limit: int = 10,
                               days: int = 0) -> list[dict]:
        """
        获取消费排行榜

        参数:
            limit: 返回数量
            days: 统计天数（0表示全部时间）

        返回:
            list[dict]: 消费排行榜
        """
        db = await get_db()

        if days > 0:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            rows = await db.fetchall(
                f"SELECT user_id, SUM(ABS(amount)) as total_spent "
                f"FROM gold_log "
                f"WHERE created_at >= ? AND amount < 0 "
                f"GROUP BY user_id "
                f"ORDER BY total_spent DESC "
                f"LIMIT ?",
                (cutoff, limit)
            )
        else:
            rows = await db.fetchall(
                "SELECT user_id, total_spent "
                "FROM users "
                "ORDER BY total_spent DESC "
                "LIMIT ?",
                (limit,)
            )

        return [dict(row) for row in rows]

    @classmethod
    async def get_top_earners(cls, limit: int = 10,
                             days: int = 0) -> list[dict]:
        """
        获取赚钱排行榜

        参数:
            limit: 返回数量
            days: 统计天数（0表示全部时间）

        返回:
            list[dict]: 赚钱排行榜
        """
        db = await get_db()

        if days > 0:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            rows = await db.fetchall(
                f"SELECT user_id, SUM(ABS(amount)) as total_earned "
                f"FROM gold_log "
                f"WHERE created_at >= ? AND amount > 0 "
                f"GROUP BY user_id "
                f"ORDER BY total_earned DESC "
                f"LIMIT ?",
                (cutoff, limit)
            )
        else:
            rows = await db.fetchall(
                "SELECT user_id, total_earned "
                "FROM users "
                "ORDER BY total_earned DESC "
                "LIMIT ?",
                (limit,)
            )

        return [dict(row) for row in rows]


# ============================================================
# 便捷函数
# ============================================================
async def log(user_id: str, amount: int, balance: int,
             handle_type: GoldHandle, source: str,
             memo: str = "") -> int:
    """记录金币变动"""
    return await GoldManager.log_gold(user_id, amount, balance, handle_type, source, memo)


async def reserve(user_id: str, amount: int,
                 handle_type: GoldHandle, source: str,
                 memo: str = "") -> GoldReservation:
    """预扣金币"""
    return await GoldManager.reserve(user_id, amount, handle_type, source, memo)
