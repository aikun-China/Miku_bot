"""
MikuBot 缓存管理模块
====================
提供多层缓存系统，支持：
- 内存缓存（进程内）
- TTL 过期机制
- 缓存统计和监控
- 缓存预热

使用示例：
```python
from utils.cache_manager import CacheManager, CacheType

# 基本操作
await CacheManager.set("user:123", {"name": "小明"}, expire=3600)
data = await CacheManager.get("user:123")
exists = await CacheManager.exists("user:123")
await CacheManager.delete("user:123")

# 批量操作
await CacheManager.set_many({"k1": "v1", "k2": "v2"}, expire=1800)
keys = await CacheManager.keys("user:*")

# 缓存统计
stats = await CacheManager.get_stats()

# 缓存装饰器
@CacheManager.cached(key="user:{user_id}", expire=600)
async def get_user_info(user_id: str):
    return await db.fetch_user(user_id)

# 缓存预热
await CacheManager.warm_up("popular_users", load_func)
```

缓存类型（CacheType）：
- USER: 用户数据缓存
- GROUP: 群组数据缓存
- CONFIG: 配置缓存
- GOODS: 商品缓存
- CUSTOM: 自定义缓存
"""

import asyncio
import logging
import time
from typing import Any, Optional, Callable, TypeVar, Generic
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
from collections import OrderedDict

logger = logging.getLogger("miku.cache")


# ============================================================
# 缓存类型枚举
# ============================================================
class CacheType(Enum):
    """缓存类型"""
    USER = "user"
    GROUP = "group"
    CONFIG = "config"
    GOODS = "goods"
    BAN = "ban"
    CUSTOM = "custom"


# ============================================================
# 缓存条目
# ============================================================
@dataclass
class CacheEntry:
    """缓存条目"""
    value: Any
    expire_at: float  # 过期时间戳，0 表示永不过期
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0

    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.expire_at == 0:
            return False
        return time.time() > self.expire_at

    def get_ttl(self) -> int:
        """获取剩余 TTL（秒）"""
        if self.expire_at == 0:
            return -1
        return max(0, int(self.expire_at - time.time()))


# ============================================================
# LRU 内存缓存
# ============================================================
class LRUCache:
    """
    LRU（最近最少使用）缓存实现

    特性：
    - 自动过期检查
    - LRU 淘汰策略
    - 线程安全
    - 统计信息
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
        }

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        async with self._lock:
            if key not in self._cache:
                self._stats["misses"] += 1
                return None

            entry = self._cache[key]

            # 检查过期
            if entry.is_expired():
                del self._cache[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return None

            # 移到末尾（最近使用）
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._stats["hits"] += 1

            return entry.value

    async def set(self, key: str, value: Any, expire: int = None):
        """设置缓存值"""
        if expire is None:
            expire = self.default_ttl

        expire_at = time.time() + expire if expire > 0 else 0

        async with self._lock:
            # 如果已存在，更新值
            if key in self._cache:
                self._cache[key].value = value
                self._cache[key].expire_at = expire_at
                self._cache.move_to_end(key)
                return

            # 如果达到最大容量，淘汰最旧的
            while len(self._cache) >= self.max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._stats["evictions"] += 1

            # 添加新条目
            self._cache[key] = CacheEntry(value=value, expire_at=expire_at)

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def exists(self, key: str) -> bool:
        """检查缓存是否存在且未过期"""
        value = await self.get(key)
        return value is not None

    async def clear(self):
        """清空所有缓存"""
        async with self._lock:
            self._cache.clear()

    async def keys(self, pattern: str = "*") -> list[str]:
        """获取匹配的键列表"""
        import fnmatch
        async with self._lock:
            # 清理过期项
            expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired_keys:
                del self._cache[k]
                self._stats["expirations"] += 1

            # 匹配模式
            if pattern == "*":
                return list(self._cache.keys())
            return [k for k in self._cache.keys() if fnmatch.fnmatch(k, pattern)]

    async def size(self) -> int:
        """获取缓存大小"""
        async with self._lock:
            return len(self._cache)

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0

        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate * 100, 2),
            "evictions": self._stats["evictions"],
            "expirations": self._stats["expirations"],
        }

    async def cleanup_expired(self) -> int:
        """清理过期缓存，返回清理数量"""
        async with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired_keys:
                del self._cache[k]
            self._stats["expirations"] += len(expired_keys)
            return len(expired_keys)


# ============================================================
# 缓存管理器
# ============================================================
class CacheManager:
    """
    缓存管理器

    提供统一的缓存访问接口，支持：
    - 多类型缓存隔离
    - 全局缓存键前缀
    - 统计信息收集
    - 缓存装饰器
    """

    # 全局缓存实例
    _cache: LRUCache = None
    _initialized = False

    # 缓存配置
    DEFAULT_MAX_SIZE = 5000
    DEFAULT_TTL = 3600

    # 键前缀
    PREFIX = "miku"

    @classmethod
    async def initialize(cls, max_size: int = None, default_ttl: int = None):
        """初始化缓存"""
        if cls._initialized:
            return

        if max_size is None:
            max_size = cls.DEFAULT_MAX_SIZE
        if default_ttl is None:
            default_ttl = cls.DEFAULT_TTL

        cls._cache = LRUCache(max_size=max_size, default_ttl=default_ttl)
        cls._initialized = True

        logger.info(f"[CacheManager] 初始化完成: max_size={max_size}, default_ttl={default_ttl}")

        # 启动定期清理任务
        asyncio.create_task(cls._cleanup_loop())

    @classmethod
    async def _cleanup_loop(cls):
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟清理一次
                if cls._cache:
                    cleaned = await cls._cache.cleanup_expired()
                    if cleaned > 0:
                        logger.debug(f"[CacheManager] 清理过期缓存: {cleaned} 条")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CacheManager] 清理缓存失败: {e}")

    @classmethod
    def _make_key(cls, key: str, cache_type: CacheType = None) -> str:
        """生成缓存键"""
        if cache_type:
            return f"{cls.PREFIX}:{cache_type.value}:{key}"
        return f"{cls.PREFIX}:{key}"

    # ============================================================
    # 基础操作
    # ============================================================
    @classmethod
    async def get(cls, key: str, cache_type: CacheType = None,
                 default: Any = None) -> Any:
        """
        获取缓存值

        参数:
            key: 缓存键
            cache_type: 缓存类型（用于添加前缀）
            default: 默认值

        返回:
            缓存值或默认值
        """
        if not cls._initialized:
            await cls.initialize()

        full_key = cls._make_key(key, cache_type)
        value = await cls._cache.get(full_key)
        return value if value is not None else default

    @classmethod
    async def set(cls, key: str, value: Any,
                 expire: int = None,
                 cache_type: CacheType = None):
        """
        设置缓存值

        参数:
            key: 缓存键
            value: 缓存值
            expire: 过期时间（秒），0 表示永不过期
            cache_type: 缓存类型
        """
        if not cls._initialized:
            await cls.initialize()

        full_key = cls._make_key(key, cache_type)
        await cls._cache.set(full_key, value, expire)

    @classmethod
    async def delete(cls, key: str, cache_type: CacheType = None) -> bool:
        """
        删除缓存

        参数:
            key: 缓存键
            cache_type: 缓存类型

        返回:
            bool: 是否删除成功
        """
        if not cls._initialized:
            await cls.initialize()

        full_key = cls._make_key(key, cache_type)
        return await cls._cache.delete(full_key)

    @classmethod
    async def exists(cls, key: str, cache_type: CacheType = None) -> bool:
        """检查缓存是否存在"""
        if not cls._initialized:
            await cls.initialize()

        full_key = cls._make_key(key, cache_type)
        return await cls._cache.exists(full_key)

    @classmethod
    async def clear(cls, cache_type: CacheType = None):
        """
        清空缓存

        参数:
            cache_type: 缓存类型（空表示清空所有）
        """
        if not cls._initialized:
            await cls.initialize()

        if cache_type:
            pattern = f"{cls.PREFIX}:{cache_type.value}:*"
        else:
            pattern = f"{cls.PREFIX}:*"

        keys = await cls._cache.keys(pattern)
        for key in keys:
            await cls._cache.delete(key)

        logger.debug(f"[CacheManager] 清空缓存: {len(keys)} 条")

    @classmethod
    async def keys(cls, pattern: str = "*",
                  cache_type: CacheType = None) -> list[str]:
        """
        获取匹配的缓存键

        参数:
            pattern: 匹配模式
            cache_type: 缓存类型

        返回:
            list[str]: 匹配的键列表
        """
        if not cls._initialized:
            await cls.initialize()

        if cache_type:
            full_pattern = f"{cls.PREFIX}:{cache_type.value}:{pattern}"
        else:
            full_pattern = f"{cls.PREFIX}:{pattern}"

        keys = await cls._cache.keys(full_pattern)

        # 移除前缀
        prefix = f"{cls.PREFIX}:"
        return [k[len(prefix):] for k in keys]

    @classmethod
    async def get_many(cls, keys: list[str],
                      cache_type: CacheType = None) -> dict[str, Any]:
        """
        批量获取缓存

        参数:
            keys: 缓存键列表
            cache_type: 缓存类型

        返回:
            dict: 存在的缓存键值对
        """
        result = {}
        for key in keys:
            value = await cls.get(key, cache_type)
            if value is not None:
                result[key] = value
        return result

    @classmethod
    async def set_many(cls, mapping: dict[str, Any],
                      expire: int = None,
                      cache_type: CacheType = None):
        """
        批量设置缓存

        参数:
            mapping: 键值对字典
            expire: 过期时间
            cache_type: 缓存类型
        """
        for key, value in mapping.items():
            await cls.set(key, value, expire, cache_type)

    # ============================================================
    # 统计信息
    # ============================================================
    @classmethod
    def get_stats(cls) -> dict:
        """获取缓存统计信息"""
        if not cls._initialized:
            return {"initialized": False}

        return {
            "initialized": True,
            **cls._cache.get_stats(),
        }

    @classmethod
    def reset_stats(cls):
        """重置统计信息"""
        if cls._cache:
            cls._cache._stats = {
                "hits": 0,
                "misses": 0,
                "evictions": 0,
                "expirations": 0,
            }

    # ============================================================
    # 缓存装饰器
    # ============================================================
    @classmethod
    def cached(cls, key: str, expire: int = 3600,
              cache_type: CacheType = None,
              unless: Callable = None):
        """
        缓存装饰器

        使用示例：
        ```python
        @CacheManager.cached(key="user:{user_id}", expire=600)
        async def get_user(user_id: str):
            return await db.fetch_user(user_id)
        ```

        参数:
            key: 缓存键模板，支持 {param} 格式化
            expire: 过期时间
            cache_type: 缓存类型
            unless: 条件函数，返回 True 时跳过缓存
        """
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # 构建缓存键
                cache_key = key.format(*args, **kwargs)

                # 检查 unless 条件
                if unless and unless(*args, **kwargs):
                    return await func(*args, **kwargs)

                # 尝试从缓存获取
                cached = await cls.get(cache_key, cache_type)
                if cached is not None:
                    logger.debug(f"[CacheManager] 缓存命中: {cache_key}")
                    return cached

                # 执行函数
                result = await func(*args, **kwargs)

                # 缓存结果
                if result is not None:
                    await cls.set(cache_key, result, expire, cache_type)

                return result

            return wrapper
        return decorator

    # ============================================================
    # 缓存预热
    # ============================================================
    @classmethod
    async def warm_up(cls, name: str,
                     load_func: Callable,
                     keys: list[str] = None,
                     expire: int = 3600,
                     cache_type: CacheType = None):
        """
        缓存预热

        参数:
            name: 预热任务名称
            load_func: 加载函数，接收键列表，返回字典
            keys: 要预热的键列表
            expire: 过期时间
            cache_type: 缓存类型
        """
        if not keys:
            logger.debug(f"[CacheManager] 预热跳过（无键）: {name}")
            return

        logger.info(f"[CacheManager] 开始预热: {name}, 数量={len(keys)}")

        # 分批加载，避免一次性加载太多
        batch_size = 100
        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]
            try:
                data = await load_func(batch_keys)
                if data:
                    await cls.set_many(data, expire, cache_type)
            except Exception as e:
                logger.error(f"[CacheManager] 预热失败: {name}, 错误: {e}")

        logger.info(f"[CacheManager] 预热完成: {name}")


# ============================================================
# 便捷函数
# ============================================================
async def get(key: str, cache_type: CacheType = None,
             default: Any = None) -> Any:
    """获取缓存"""
    return await CacheManager.get(key, cache_type, default)


async def set(key: str, value: Any,
             expire: int = None,
             cache_type: CacheType = None):
    """设置缓存"""
    await CacheManager.set(key, value, expire, cache_type)


async def delete(key: str, cache_type: CacheType = None) -> bool:
    """删除缓存"""
    return await CacheManager.delete(key, cache_type)


async def clear(cache_type: CacheType = None):
    """清空缓存"""
    await CacheManager.clear(cache_type)
