"""
MikuBot 用户管理模块
====================
提供用户数据的 CRUD 操作，包括：
- 用户创建/获取
- 用户信息更新
- 签到管理
- 道具背包管理

使用示例：
```python
from utils.user_manager import UserManager, UserData

# 获取或创建用户
user = await UserManager.get_user("123456")

# 获取用户金币
gold = await UserManager.get_gold("123456")

# 添加金币
await UserManager.add_gold("123456", 100, "每日签到")

# 扣减金币（原子操作）
ok = await UserManager.reduce_gold("123456", 50, "购买商品")
if not ok:
    print("金币不足")

# 获取用户道具
props = await UserManager.get_props("123456")
```

继承自数据库模块的金币管理，提供更高级的用户操作接口。
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from utils.database import Database, UserModel, get_db

logger = logging.getLogger("miku.user")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class UserData:
    """用户数据结构"""
    user_id: str
    gold: int
    props: dict
    sign_count: int
    last_sign_time: Optional[str]
    total_spent: int
    total_earned: int
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "UserData":
        """从数据库行创建 UserData"""
        return cls(
            user_id=row["user_id"],
            gold=row["gold"],
            props=json.loads(row["props"]) if row["props"] else {},
            sign_count=row["sign_count"],
            last_sign_time=row["last_sign_time"],
            total_spent=row["total_spent"],
            total_earned=row["total_earned"],
            created_at=row["created_at"],
        )


# ============================================================
# 异常类
# ============================================================
class UserException(Exception):
    """用户相关异常基类"""
    pass


class InsufficientGold(UserException):
    """金币不足异常"""
    def __init__(self, required: int, actual: int):
        self.required = required
        self.actual = actual
        super().__init__(f"金币不足：需要 {required}，当前 {actual}")


class UserNotFound(UserException):
    """用户不存在异常"""
    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"用户不存在: {user_id}")


class PropNotFound(UserException):
    """道具不足或不存在异常"""
    def __init__(self, prop_name: str):
        self.prop_name = prop_name
        super().__init__(f"道具不存在或数量不足: {prop_name}")


# ============================================================
# 用户管理器
# ============================================================
class UserManager:
    """
    用户管理器

    提供用户数据的统一访问接口，包括：
    - 用户 CRUD
    - 金币管理（原子操作）
    - 道具背包管理
    - 签到管理
    """

    # 默认初始金币
    DEFAULT_GOLD = 100

    # 默认每日签到金币
    DEFAULT_SIGN_GOLD = 10

    # 每日签到金币配置（可通过配置覆盖）
    _sign_gold: Optional[int] = None

    @classmethod
    def set_sign_gold(cls, amount: int):
        """设置每日签到金币"""
        cls._sign_gold = amount

    @classmethod
    def _get_sign_gold(cls) -> int:
        """获取每日签到金币"""
        if cls._sign_gold is not None:
            return cls._sign_gold

        # 从配置读取
        try:
            from utils.config_manager import config_manager
            gold = config_manager.get("miku_checkin", "daily_gold", cls.DEFAULT_SIGN_GOLD)
            cls._sign_gold = int(gold) if gold else cls.DEFAULT_SIGN_GOLD
        except Exception:
            cls._sign_gold = cls.DEFAULT_SIGN_GOLD

        return cls._sign_gold

    # ============================================================
    # 基础用户操作
    # ============================================================
    @classmethod
    async def get_user(cls, user_id: str) -> UserData:
        """
        获取用户数据，不存在则创建

        参数:
            user_id: 用户ID

        返回:
            UserData: 用户数据对象
        """
        db = await get_db()

        # 尝试获取现有用户
        row = await db.select_one(UserModel, "user_id = ?", (user_id,))

        if row:
            return UserData.from_row(row)

        # 创建新用户
        now = datetime.now().isoformat()
        await db.insert(UserModel, {
            "user_id": user_id,
            "gold": cls.DEFAULT_GOLD,
            "props": "{}",
            "sign_count": 0,
            "last_sign_time": None,
            "total_spent": 0,
            "total_earned": cls.DEFAULT_GOLD,
            "created_at": now,
        })

        logger.info(f"[UserManager] 创建新用户: {user_id}")

        return UserData(
            user_id=user_id,
            gold=cls.DEFAULT_GOLD,
            props={},
            sign_count=0,
            last_sign_time=None,
            total_spent=0,
            total_earned=cls.DEFAULT_GOLD,
            created_at=now,
        )

    @classmethod
    async def get_user_or_none(cls, user_id: str) -> Optional[UserData]:
        """获取用户数据，不存在则返回 None"""
        db = await get_db()
        row = await db.select_one(UserModel, "user_id = ?", (user_id,))
        return UserData.from_row(row) if row else None

    @classmethod
    async def user_exists(cls, user_id: str) -> bool:
        """检查用户是否存在"""
        db = await get_db()
        return await db.exists(UserModel, "user_id = ?", (user_id,))

    @classmethod
    async def get_all_users(cls, limit: int = 0, offset: int = 0) -> list[UserData]:
        """获取所有用户"""
        db = await get_db()
        rows = await db.select_all(UserModel, order_by="created_at DESC",
                                   limit=limit, offset=offset)
        return [UserData.from_row(row) for row in rows]

    @classmethod
    async def get_user_count(cls) -> int:
        """获取用户总数"""
        db = await get_db()
        return await db.count(UserModel)

    # ============================================================
    # 金币操作
    # ============================================================
    @classmethod
    async def get_gold(cls, user_id: str) -> int:
        """获取用户金币数量"""
        user = await cls.get_user(user_id)
        return user.gold

    @classmethod
    async def set_gold(cls, user_id: str, amount: int, source: str = "system"):
        """
        设置用户金币（直接设置，非增减）

        参数:
            user_id: 用户ID
            amount: 金币数量
            source: 来源
        """
        db = await get_db()
        await db.update(UserModel, {"gold": amount}, "user_id = ?", (user_id,))
        logger.debug(f"[UserManager] 设置金币: {user_id} = {amount}")

    @classmethod
    async def add_gold(cls, user_id: str, amount: int, source: str = "system",
                      memo: str = ""):
        """
        添加金币（原子操作）

        参数:
            user_id: 用户ID
            amount: 添加数量（正数）
            source: 来源（插件名）
            memo: 备注
        """
        if amount <= 0:
            raise ValueError("添加金币数量必须为正数")

        db = await get_db()

        # 确保用户存在
        await cls.get_user(user_id)

        # 原子增加
        row = await db.fetchone(
            "UPDATE users SET gold = gold + ?, total_earned = total_earned + ? "
            "WHERE user_id = ? RETURNING gold",
            (amount, amount, user_id)
        )

        if row:
            new_balance = row[0]
            logger.debug(f"[UserManager] 添加金币: {user_id} +{amount} -> {new_balance}")

            # 记录日志
            from utils.gold_manager import GoldManager
            await GoldManager.log_gold(
                user_id=user_id,
                amount=amount,
                balance=new_balance,
                handle_type="EARN",
                source=source,
                memo=memo,
            )
        else:
            logger.warning(f"[UserManager] 添加金币失败，用户不存在: {user_id}")

    @classmethod
    async def reduce_gold(cls, user_id: str, amount: int, source: str = "system",
                         memo: str = "") -> bool:
        """
        扣减金币（原子操作 + 守卫条件）

        参数:
            user_id: 用户ID
            amount: 扣减数量（正数）
            source: 来源（插件名）
            memo: 备注

        返回:
            bool: 是否扣减成功（金币不足返回 False）
        """
        if amount <= 0:
            raise ValueError("扣减金币数量必须为正数")

        db = await get_db()

        # 确保用户存在
        await cls.get_user(user_id)

        # 原子扣减 + 守卫条件，防止并发超扣
        row = await db.fetchone(
            "UPDATE users SET gold = gold - ? "
            "WHERE user_id = ? AND gold >= ? "
            "RETURNING gold",
            (amount, user_id, amount)
        )

        if row:
            new_balance = row[0]
            # 更新消费总额
            await db.execute(
                "UPDATE users SET total_spent = total_spent + ? WHERE user_id = ?",
                (amount, user_id)
            )
            logger.debug(f"[UserManager] 扣减金币: {user_id} -{amount} -> {new_balance}")

            # 记录日志
            from utils.gold_manager import GoldManager
            await GoldManager.log_gold(
                user_id=user_id,
                amount=-amount,
                balance=new_balance,
                handle_type="SPEND",
                source=source,
                memo=memo,
            )
            return True
        else:
            logger.debug(f"[UserManager] 金币不足: {user_id} 需要 {amount}")
            return False

    @classmethod
    async def transfer_gold(cls, from_user: str, to_user: str, amount: int,
                           memo: str = "") -> bool:
        """
        转账金币

        参数:
            from_user: 转出用户
            to_user: 转入用户
            amount: 金额
            memo: 备注

        返回:
            bool: 是否成功
        """
        if amount <= 0:
            raise ValueError("转账金额必须为正数")

        # 扣减转出方
        success = await cls.reduce_gold(from_user, amount, "transfer", memo)
        if not success:
            return False

        # 添加接收方
        await cls.add_gold(to_user, amount, "transfer", f"来自 {from_user} 的转账")

        logger.info(f"[UserManager] 金币转账: {from_user} -> {to_user}, 金额 {amount}")
        return True

    # ============================================================
    # 签到管理
    # ============================================================
    @classmethod
    async def check_sign(cls, user_id: str) -> bool:
        """检查今日是否已签到"""
        user = await cls.get_user(user_id)
        if not user.last_sign_time:
            return False

        today = datetime.now().date().isoformat()
        last_date = user.last_sign_time.split("T")[0]
        return last_date == today

    @classmethod
    async def sign(cls, user_id: str) -> dict:
        """
        用户签到

        返回:
            dict: 签到结果，包含 is_sign（是否成功）、days（连续天数）、gold（获得金币）
        """
        db = await get_db()
        now = datetime.now()
        today = now.date().isoformat()

        # 获取当前用户
        user = await cls.get_user(user_id)

        # 检查今日是否已签到
        if user.last_sign_time:
            last_date = user.last_sign_time.split("T")[0]
            if last_date == today:
                return {
                    "success": False,
                    "is_sign": True,
                    "days": user.sign_count,
                    "gold": 0,
                    "message": "今日已签到，明天再来吧~",
                }

        # 计算连续签到天数
        if user.last_sign_time:
            last_date = datetime.fromisoformat(user.last_sign_time).date()
            diff = (now.date() - last_date).days
            if diff == 1:
                new_sign_count = user.sign_count + 1
            else:
                new_sign_count = 1
        else:
            new_sign_count = 1

        # 计算签到金币（可配置）
        sign_gold = cls._get_sign_gold()

        # 原子更新
        await db.execute(
            "UPDATE users SET "
            "last_sign_time = ?, "
            "sign_count = ?, "
            "gold = gold + ? "
            "WHERE user_id = ?",
            (now.isoformat(), new_sign_count, sign_gold, user_id)
        )

        # 记录金币日志
        from utils.gold_manager import GoldManager
        new_balance = user.gold + sign_gold
        await GoldManager.log_gold(
            user_id=user_id,
            amount=sign_gold,
            balance=new_balance,
            handle_type="EARN",
            source="sign",
            memo=f"第{new_sign_count}天签到",
        )

        logger.info(f"[UserManager] 用户签到: {user_id}, 第{new_sign_count}天, 金币+{sign_gold}")

        return {
            "success": True,
            "is_sign": False,
            "days": new_sign_count,
            "gold": sign_gold,
            "balance": new_balance,
            "message": f"签到成功！连续签到 {new_sign_count} 天，获得 {sign_gold} 金币",
        }

    # ============================================================
    # 道具背包管理
    # ============================================================
    @classmethod
    async def get_props(cls, user_id: str) -> dict:
        """获取用户道具背包"""
        user = await cls.get_user(user_id)
        return user.props.copy()

    @classmethod
    async def add_prop(cls, user_id: str, prop_name: str, quantity: int = 1) -> dict:
        """
        添加道具

        参数:
            user_id: 用户ID
            prop_name: 道具名称
            quantity: 数量
        """
        if quantity <= 0:
            raise ValueError("道具数量必须为正数")

        db = await get_db()
        user = await cls.get_user(user_id)

        # 更新道具
        props = user.props.copy()
        props[prop_name] = props.get(prop_name, 0) + quantity

        await db.update(UserModel, {"props": json.dumps(props)},
                       "user_id = ?", (user_id,))

        logger.debug(f"[UserManager] 添加道具: {user_id} +{quantity}x {prop_name}")

        return props

    @classmethod
    async def remove_prop(cls, user_id: str, prop_name: str,
                         quantity: int = 1) -> tuple[bool, dict]:
        """
        移除/使用道具

        参数:
            user_id: 用户ID
            prop_name: 道具名称
            quantity: 数量

        返回:
            tuple: (是否成功, 剩余道具)
        """
        if quantity <= 0:
            raise ValueError("道具数量必须为正数")

        db = await get_db()
        user = await cls.get_user(user_id)

        props = user.props.copy()
        current = props.get(prop_name, 0)

        if current < quantity:
            logger.debug(f"[UserManager] 道具不足: {user_id} {prop_name} 需要 {quantity} 仅有 {current}")
            return False, props

        # 扣减
        if current == quantity:
            del props[prop_name]
        else:
            props[prop_name] = current - quantity

        await db.update(UserModel, {"props": json.dumps(props)},
                       "user_id = ?", (user_id,))

        logger.debug(f"[UserManager] 使用道具: {user_id} -{quantity}x {prop_name}")

        return True, props

    @classmethod
    async def get_prop_count(cls, user_id: str, prop_name: str) -> int:
        """获取某道具数量"""
        user = await cls.get_user(user_id)
        return user.props.get(prop_name, 0)

    @classmethod
    async def has_prop(cls, user_id: str, prop_name: str, quantity: int = 1) -> bool:
        """检查用户是否有足够道具"""
        return await cls.get_prop_count(user_id, prop_name) >= quantity

    # ============================================================
    # 用户统计
    # ============================================================
    @classmethod
    async def get_rich_users(cls, limit: int = 10) -> list[dict]:
        """获取金币排行榜"""
        db = await get_db()
        rows = await db.fetchall(
            "SELECT user_id, gold, total_earned, total_spent FROM users "
            "ORDER BY gold DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in rows]

    @classmethod
    async def get_user_rank(cls, user_id: str) -> int:
        """获取用户金币排名"""
        db = await get_db()

        # 获取用户金币
        user = await cls.get_user_or_none(user_id)
        if not user:
            return -1

        # 计算排名
        row = await db.fetchone(
            "SELECT COUNT(*) + 1 as rank FROM users WHERE gold > ?",
            (user.gold,)
        )

        return row["rank"] if row else -1

    # ============================================================
    # 用户数据删除
    # ============================================================
    @classmethod
    async def delete_user(cls, user_id: str) -> bool:
        """删除用户数据（谨慎使用）"""
        db = await get_db()
        count = await db.delete(UserModel, "user_id = ?", (user_id,))
        logger.warning(f"[UserManager] 删除用户: {user_id}")
        return count > 0


# ============================================================
# 便捷函数
# ============================================================
async def get_user(user_id: str) -> UserData:
    """获取用户数据"""
    return await UserManager.get_user(user_id)


async def get_gold(user_id: str) -> int:
    """获取用户金币"""
    return await UserManager.get_gold(user_id)


async def add_gold(user_id: str, amount: int, source: str = "system"):
    """添加金币"""
    return await UserManager.add_gold(user_id, amount, source)


async def reduce_gold(user_id: str, amount: int, source: str = "system") -> bool:
    """扣减金币"""
    return await UserManager.reduce_gold(user_id, amount, source)
