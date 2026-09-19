"""
MikuBot 商品管理模块
====================
提供商品和商店系统的基础功能，支持：
- 商品 CRUD
- 商品分类/分区
- 库存管理
- 每日限购
- 折扣系统
- 商品购买（集成金币系统）

使用示例：
```python
from utils.goods_manager import GoodsManager, Goods, PurchaseResult

# 添加商品
uuid = await GoodsManager.add_goods(
    name="强化石",
    price=100,
    description="用于装备强化",
    stock=50,
    daily_limit=5,
    partition="道具",
)

# 获取商品
goods = await GoodsManager.get_goods("强化石")

# 购买商品
result = await GoodsManager.purchase(
    user_id="123456",
    goods_name="强化石",
    quantity=3,
)
if result.success:
    print(f"购买成功！花费 {result.total_price} 金币")
else:
    print(f"购买失败: {result.message}")

# 修改商品
await GoodsManager.update_goods(
    "强化石",
    price=80,
    stock=100,
)

# 删除商品
await GoodsManager.delete_goods("强化石")

# 获取商品列表
all_goods = await GoodsManager.get_all_goods()
道具 = await GoodsManager.get_goods_by_partition("道具")

# 获取销售排行榜
top = await GoodsManager.get_top_selling(limit=10)
```

商品分区（partition）：
- 用于商品分类展示，如"道具"、"称号"、"特权"等
"""

import json
import logging
import time
import uuid
from datetime import datetime, date
from typing import Optional, Any
from dataclasses import dataclass

from utils.database import Database, GoodsModel, ShopPurchaseLogModel, UserModel, get_db
from utils.cache_manager import CacheManager, CacheType
from utils.user_manager import UserManager
from utils.gold_manager import GoldManager, GoldHandle

logger = logging.getLogger("miku.goods")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class Goods:
    """商品数据结构"""
    uuid: str
    name: str
    price: int
    description: str
    discount: float
    stock: int  # -1 表示无限
    daily_limit: int  # 0 表示不限购
    is_passive: bool
    partition: Optional[str]
    icon: Optional[str]
    created_at: str

    @property
    def is_unlimited_stock(self) -> bool:
        """是否无限库存"""
        return self.stock == -1

    @property
    def final_price(self) -> int:
        """折后价格"""
        return int(self.price * self.discount)

    @classmethod
    def from_row(cls, row: dict) -> "Goods":
        """从数据库行创建 Goods"""
        return cls(
            uuid=row["uuid"],
            name=row["name"],
            price=row["price"],
            description=row["description"] or "",
            discount=row["discount"] or 1.0,
            stock=row["stock"] or -1,
            daily_limit=row["daily_limit"] or 0,
            is_passive=bool(row["is_passive"]),
            partition=row["partition"],
            icon=row["icon"],
            created_at=row["created_at"],
        )


@dataclass
class PurchaseResult:
    """购买结果"""
    success: bool
    goods_name: str
    quantity: int
    unit_price: int
    total_price: int
    message: str
    remaining_gold: Optional[int] = None

    @property
    def gold_spent(self) -> int:
        """花费金币"""
        return self.total_price if self.success else 0


# ============================================================
# 异常类
# ============================================================
class GoodsException(Exception):
    """商品相关异常"""
    pass


class GoodsNotFound(GoodsException):
    """商品不存在"""
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"商品不存在: {name}")


class GoodsOutOfStock(GoodsException):
    """商品库存不足"""
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"商品库存不足: {name}")


class GoodsPurchaseLimitExceeded(GoodsException):
    """超过每日购买限制"""
    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = limit
        super().__init__(f"已达到每日购买限制 ({limit})")


# ============================================================
# 商品管理器
# ============================================================
class GoodsManager:
    """
    商品管理器

    提供商品管理和购买功能。
    购买时会自动调用金币系统进行扣款。
    """

    # 缓存配置
    CACHE_TTL = 300  # 5分钟
    CACHE_PREFIX = "goods"

    # 默认折扣
    DEFAULT_DISCOUNT = 1.0

    @classmethod
    async def _get_cache_key(cls, goods_name: str) -> str:
        """生成缓存键"""
        return f"{cls.CACHE_PREFIX}:{goods_name}"

    @classmethod
    async def _get_goods_cache(cls, goods_name: str) -> Optional[Goods]:
        """从缓存获取商品"""
        key = await cls._get_cache_key(goods_name)
        data = await CacheManager.get(key, CacheType.GOODS)
        if data:
            return Goods(**data)
        return None

    @classmethod
    async def _set_goods_cache(cls, goods: Goods):
        """设置商品缓存"""
        key = await cls._get_cache_key(goods.name)
        await CacheManager.set(
            key,
            {
                "uuid": goods.uuid,
                "name": goods.name,
                "price": goods.price,
                "description": goods.description,
                "discount": goods.discount,
                "stock": goods.stock,
                "daily_limit": goods.daily_limit,
                "is_passive": goods.is_passive,
                "partition": goods.partition,
                "icon": goods.icon,
                "created_at": goods.created_at,
            },
            cls.CACHE_TTL,
            CacheType.GOODS
        )

    @classmethod
    async def _invalidate_cache(cls, goods_name: str):
        """使商品缓存失效"""
        key = await cls._get_cache_key(goods_name)
        await CacheManager.delete(key, CacheType.GOODS)

    # ============================================================
    # 商品 CRUD
    # ============================================================
    @classmethod
    async def add_goods(cls, name: str, price: int, description: str = "",
                       discount: float = 1.0, stock: int = -1,
                       daily_limit: int = 0, is_passive: bool = False,
                       partition: str = None, icon: str = None) -> str:
        """
        添加商品

        参数:
            name: 商品名称
            price: 价格（原价）
            description: 描述
            discount: 折扣 (0-1)
            stock: 库存 (-1 表示无限)
            daily_limit: 每日限购 (0 表示不限购)
            is_passive: 是否被动道具
            partition: 分区/分类
            icon: 图标路径

        返回:
            str: 商品UUID
        """
        db = await get_db()
        now = datetime.now()

        # 检查是否已存在
        existing = await cls.get_goods(name)
        if existing:
            logger.warning(f"[GoodsManager] 商品已存在: {name}")
            return existing.uuid

        # 生成 UUID
        goods_uuid = str(uuid.uuid1())

        await db.insert(GoodsModel, {
            "uuid": goods_uuid,
            "name": name,
            "price": price,
            "description": description,
            "discount": discount,
            "stock": stock,
            "daily_limit": daily_limit,
            "is_passive": 1 if is_passive else 0,
            "partition": partition,
            "icon": icon,
            "created_at": now.isoformat(),
        })

        logger.info(f"[GoodsManager] 添加商品: {name}, 价格={price}, 库存={stock}")

        return goods_uuid

    @classmethod
    async def get_goods(cls, name: str) -> Optional[Goods]:
        """
        获取商品

        参数:
            name: 商品名称

        返回:
            Goods 或 None
        """
        # 先检查缓存
        cached = await cls._get_goods_cache(name)
        if cached:
            return cached

        db = await get_db()
        row = await db.select_one(GoodsModel, "name = ?", (name,))

        if not row:
            return None

        goods = Goods.from_row(row)
        await cls._set_goods_cache(goods)

        return goods

    @classmethod
    async def get_goods_by_uuid(cls, goods_uuid: str) -> Optional[Goods]:
        """通过 UUID 获取商品"""
        db = await get_db()
        row = await db.select_one(GoodsModel, "uuid = ?", (goods_uuid,))

        if not row:
            return None

        return Goods.from_row(row)

    @classmethod
    async def get_all_goods(cls, include_disabled: bool = True) -> list[Goods]:
        """获取所有商品"""
        db = await get_db()
        rows = await db.select_all(GoodsModel, order_by="created_at ASC")

        return [Goods.from_row(row) for row in rows]

    @classmethod
    async def get_goods_by_partition(cls, partition: str) -> list[Goods]:
        """获取指定分区的商品"""
        db = await get_db()
        rows = await db.select_all(
            GoodsModel,
            where="partition = ?",
            params=(partition,),
            order_by="created_at ASC"
        )

        return [Goods.from_row(row) for row in rows]

    @classmethod
    async def get_all_partitions(cls) -> list[str]:
        """获取所有分区"""
        db = await get_db()
        rows = await db.fetchall(
            "SELECT DISTINCT partition FROM goods WHERE partition IS NOT NULL ORDER BY partition"
        )

        return [row["partition"] for row in rows if row["partition"]]

    @classmethod
    async def update_goods(cls, name: str, **kwargs) -> bool:
        """
        更新商品

        参数:
            name: 商品名称
            **kwargs: 要更新的字段 (price, description, discount, stock, daily_limit, is_passive, partition, icon)
        """
        db = await get_db()

        # 检查是否存在
        goods = await cls.get_goods(name)
        if not goods:
            raise GoodsNotFound(name)

        # 过滤有效的更新字段
        allowed_fields = ["price", "description", "discount", "stock", "daily_limit", "is_passive", "partition", "icon"]
        updates = {}
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                if key == "is_passive":
                    updates[key] = 1 if value else 0
                else:
                    updates[key] = value

        if not updates:
            return False

        # 更新
        count = await db.update(GoodsModel, updates, "name = ?", (name,))

        # 使缓存失效
        await cls._invalidate_cache(name)

        logger.info(f"[GoodsManager] 更新商品: {name}, 变更: {updates}")

        return count > 0

    @classmethod
    async def delete_goods(cls, name: str) -> bool:
        """删除商品"""
        db = await get_db()

        count = await db.delete(GoodsModel, "name = ?", (name,))

        if count > 0:
            await cls._invalidate_cache(name)
            logger.info(f"[GoodsManager] 删除商品: {name}")

        return count > 0

    # ============================================================
    # 库存管理
    # ============================================================
    @classmethod
    async def check_stock(cls, name: str) -> int:
        """
        检查库存

        返回:
            int: 可用库存数量，-1 表示无限
        """
        goods = await cls.get_goods(name)
        if not goods:
            raise GoodsNotFound(name)

        return goods.stock

    @classmethod
    async def add_stock(cls, name: str, quantity: int) -> int:
        """
        添加库存

        参数:
            name: 商品名称
            quantity: 添加数量

        返回:
            int: 新的库存数量
        """
        db = await get_db()

        if quantity <= 0:
            raise ValueError("库存数量必须为正数")

        # 原子增加
        row = await db.fetchone(
            "UPDATE goods SET stock = CASE WHEN stock = -1 THEN -1 ELSE stock + ? END "
            "WHERE name = ? RETURNING stock",
            (quantity, name)
        )

        if not row:
            raise GoodsNotFound(name)

        await cls._invalidate_cache(name)
        logger.info(f"[GoodsManager] 添加库存: {name} +{quantity} -> {row[0]}")

        return row[0]

    @classmethod
    async def reduce_stock(cls, name: str, quantity: int = 1) -> int:
        """
        扣减库存

        参数:
            name: 商品名称
            quantity: 扣减数量

        返回:
            int: 剩余库存数量，-1 表示无限
        """
        db = await get_db()

        if quantity <= 0:
            raise ValueError("库存数量必须为正数")

        # 原子扣减 + 守卫条件
        row = await db.fetchone(
            "UPDATE goods SET stock = CASE WHEN stock = -1 THEN -1 ELSE "
            "CASE WHEN stock >= ? THEN stock - ? ELSE stock END END "
            "WHERE name = ? RETURNING stock",
            (quantity, quantity, name)
        )

        if not row:
            raise GoodsNotFound(name)

        remaining = row[0]

        # 检查是否库存不足（如果是有限库存）
        goods = await cls.get_goods(name)
        if goods.stock != -1 and remaining < 0:
            raise GoodsOutOfStock(name)

        await cls._invalidate_cache(name)
        logger.debug(f"[GoodsManager] 扣减库存: {name} -{quantity} -> {remaining}")

        return remaining

    # ============================================================
    # 购买系统
    # ============================================================
    @classmethod
    async def _get_daily_purchase_count(cls, user_id: str, goods_name: str) -> int:
        """获取用户今日购买数量"""
        db = await get_db()
        today = date.today().isoformat()

        row = await db.fetchone(
            "SELECT SUM(quantity) as total FROM shop_purchase_log "
            "WHERE user_id = ? AND goods_name = ? AND created_at LIKE ?",
            (user_id, goods_name, f"{today}%")
        )

        return row["total"] if row and row["total"] else 0

    @classmethod
    async def purchase(cls, user_id: str, goods_name: str,
                      quantity: int = 1) -> PurchaseResult:
        """
        购买商品

        参数:
            user_id: 用户ID
            goods_name: 商品名称
            quantity: 购买数量

        返回:
            PurchaseResult: 购买结果
        """
        # 获取商品
        goods = await cls.get_goods(goods_name)
        if not goods:
            return PurchaseResult(
                success=False,
                goods_name=goods_name,
                quantity=quantity,
                unit_price=0,
                total_price=0,
                message=f"商品「{goods_name}」不存在",
            )

        # 检查库存
        if not goods.is_unlimited_stock and goods.stock < quantity:
            return PurchaseResult(
                success=False,
                goods_name=goods_name,
                quantity=quantity,
                unit_price=goods.final_price,
                total_price=0,
                message=f"库存不足！当前库存: {goods.stock}",
            )

        # 检查每日限购
        if goods.daily_limit > 0:
            daily_count = await cls._get_daily_purchase_count(user_id, goods_name)
            if daily_count + quantity > goods.daily_limit:
                return PurchaseResult(
                    success=False,
                    goods_name=goods_name,
                    quantity=quantity,
                    unit_price=goods.final_price,
                    total_price=0,
                    message=f"已达到每日购买限制！今日已购买: {daily_count}, 限制: {goods.daily_limit}",
                )

        # 计算价格
        total_price = goods.final_price * quantity

        # 扣减金币
        ok = await UserManager.reduce_gold(user_id, total_price, "shop", f"购买 {goods_name} x{quantity}")
        if not ok:
            current_gold = await UserManager.get_gold(user_id)
            return PurchaseResult(
                success=False,
                goods_name=goods_name,
                quantity=quantity,
                unit_price=goods.final_price,
                total_price=total_price,
                message=f"金币不足！需要 {total_price}，当前 {current_gold}",
                remaining_gold=current_gold,
            )

        # 扣减库存
        if not goods.is_unlimited_stock:
            try:
                await cls.reduce_stock(goods_name, quantity)
            except GoodsOutOfStock:
                # 库存不足，回滚金币
                await UserManager.add_gold(user_id, total_price, "shop", "购买失败退款")
                return PurchaseResult(
                    success=False,
                    goods_name=goods_name,
                    quantity=quantity,
                    unit_price=goods.final_price,
                    total_price=0,
                    message="库存不足！",
                )

        # 记录购买日志
        await cls._log_purchase(user_id, goods_name, quantity, total_price)

        # 添加道具到用户背包
        await UserManager.add_prop(user_id, goods_name, quantity)

        # 获取剩余金币
        remaining = await UserManager.get_gold(user_id)

        logger.info(f"[GoodsManager] 购买成功: user={user_id}, goods={goods_name}, "
                   f"quantity={quantity}, price={total_price}")

        return PurchaseResult(
            success=True,
            goods_name=goods_name,
            quantity=quantity,
            unit_price=goods.final_price,
            total_price=total_price,
            message=f"购买成功！获得 {quantity} 个「{goods_name}」",
            remaining_gold=remaining,
        )

    @classmethod
    async def _log_purchase(cls, user_id: str, goods_name: str,
                           quantity: int, total_price: int):
        """记录购买日志"""
        db = await get_db()
        now = datetime.now()

        await db.insert(ShopPurchaseLogModel, {
            "user_id": user_id,
            "goods_name": goods_name,
            "price": total_price // quantity,
            "quantity": quantity,
            "total_price": total_price,
            "created_at": now.isoformat(),
        })

    # ============================================================
    # 统计功能
    # ============================================================
    @classmethod
    async def get_top_selling(cls, limit: int = 10,
                             days: int = 0) -> list[dict]:
        """
        获取热销商品排行

        参数:
            limit: 返回数量
            days: 统计天数（0表示全部时间）

        返回:
            list[dict]: 热销排行
        """
        db = await get_db()

        if days > 0:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            rows = await db.fetchall(
                f"SELECT goods_name, SUM(quantity) as total_quantity, "
                f"SUM(total_price) as total_revenue, COUNT(*) as order_count "
                f"FROM shop_purchase_log "
                f"WHERE created_at >= ? "
                f"GROUP BY goods_name "
                f"ORDER BY total_quantity DESC "
                f"LIMIT ?",
                (cutoff, limit)
            )
        else:
            rows = await db.fetchall(
                "SELECT goods_name, SUM(quantity) as total_quantity, "
                "SUM(total_price) as total_revenue, COUNT(*) as order_count "
                "FROM shop_purchase_log "
                "GROUP BY goods_name "
                "ORDER BY total_quantity DESC "
                "LIMIT ?",
                (limit,)
            )

        return [dict(row) for row in rows]

    @classmethod
    async def get_user_purchase_history(cls, user_id: str,
                                       limit: int = 20) -> list[dict]:
        """获取用户购买历史"""
        db = await get_db()

        rows = await db.fetchall(
            "SELECT * FROM shop_purchase_log "
            "WHERE user_id = ? "
            "ORDER BY created_at DESC "
            "LIMIT ?",
            (user_id, limit)
        )

        return [dict(row) for row in rows]

    @classmethod
    async def get_total_revenue(cls, goods_name: str = "") -> int:
        """获取销售收入"""
        db = await get_db()

        if goods_name:
            row = await db.fetchone(
                "SELECT SUM(total_price) as revenue FROM shop_purchase_log WHERE goods_name = ?",
                (goods_name,)
            )
        else:
            row = await db.fetchone(
                "SELECT SUM(total_price) as revenue FROM shop_purchase_log"
            )

        return row["revenue"] if row and row["revenue"] else 0

    # ============================================================
    # 批量操作
    # ============================================================
    @classmethod
    async def initialize_default_goods(cls):
        """初始化默认商品"""
        default_goods = [
            ("体力药水", 50, "恢复 100 点体力", 10, 5, "道具"),
            ("经验药水", 80, "获得双倍经验 1 小时", 5, 3, "道具"),
            ("幸运符", 100, "下一抽必出稀有", 3, 2, "道具"),
        ]

        for name, price, desc, stock, limit, partition in default_goods:
            existing = await cls.get_goods(name)
            if not existing:
                await cls.add_goods(
                    name=name,
                    price=price,
                    description=desc,
                    stock=stock,
                    daily_limit=limit,
                    partition=partition,
                )
                logger.info(f"[GoodsManager] 初始化默认商品: {name}")


# ============================================================
# 便捷函数
# ============================================================
async def get_goods(name: str) -> Optional[Goods]:
    """获取商品"""
    return await GoodsManager.get_goods(name)


async def purchase(user_id: str, goods_name: str, quantity: int = 1) -> PurchaseResult:
    """购买商品"""
    return await GoodsManager.purchase(user_id, goods_name, quantity)


async def add_goods(name: str, price: int, description: str = "", **kwargs) -> str:
    """添加商品"""
    return await GoodsManager.add_goods(name, price, description, **kwargs)
