"""
MikuBot 数据库初始化模块
========================
在 Bot 启动时初始化数据库和缓存。

自动执行：
1. 初始化 SQLite 数据库，创建表结构
2. 初始化缓存系统
3. 清理过期的黑名单记录
4. 清理旧的金币日志
"""

import asyncio
import logging

logger = logging.getLogger("miku.init")


async def initialize_database():
    """初始化数据库"""
    try:
        # 导入数据库模块
        from utils.database import Database

        # 获取数据库实例（会自动创建表）
        db = await Database.get_instance()
        logger.info("[Init] 数据库初始化完成")

        return db
    except Exception as e:
        logger.error(f"[Init] 数据库初始化失败: {e}")
        raise


async def initialize_cache():
    """初始化缓存系统"""
    try:
        from utils.cache_manager import CacheManager

        await CacheManager.initialize(
            max_size=5000,
            default_ttl=3600,
        )
        logger.info("[Init] 缓存系统初始化完成")

        # 启动缓存统计任务
        asyncio.create_task(_cache_stats_loop())

    except Exception as e:
        logger.error(f"[Init] 缓存初始化失败: {e}")
        raise


async def cleanup_expired_data():
    """清理过期数据"""
    try:
        # 清理过期黑名单
        try:
            from utils.ban_manager import BanManager
            cleaned_bans = await BanManager.cleanup_expired()
            if cleaned_bans > 0:
                logger.info(f"[Init] 清理过期黑名单: {cleaned_bans} 条")
        except Exception as e:
            logger.warning(f"[Init] 清理黑名单失败: {e}")

        # 清理旧金币日志
        try:
            from utils.gold_manager import GoldManager
            cleaned_gold = await GoldManager.cleanup_old_logs(days=30)
            if cleaned_gold > 0:
                logger.info(f"[Init] 清理旧金币日志: {cleaned_gold} 条")
        except Exception as e:
            logger.warning(f"[Init] 清理金币日志失败: {e}")

    except Exception as e:
        logger.warning(f"[Init] 清理过期数据时出错: {e}")


async def initialize_goods():
    """初始化默认商品"""
    try:
        from utils.goods_manager import GoodsManager

        await GoodsManager.initialize_default_goods()
        logger.info("[Init] 默认商品初始化完成")

    except Exception as e:
        logger.warning(f"[Init] 初始化商品失败: {e}")


async def _cache_stats_loop():
    """定期打印缓存统计"""
    try:
        from utils.cache_manager import CacheManager

        while True:
            await asyncio.sleep(3600)  # 每小时打印一次
            stats = CacheManager.get_stats()
            if stats.get("initialized"):
                logger.debug(
                    f"[Cache] 统计: size={stats['size']}, "
                    f"hits={stats['hits']}, hit_rate={stats['hit_rate']}%"
                )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[Cache] 统计任务出错: {e}")


async def initialize_all():
    """
    初始化所有模块

    按依赖顺序初始化：
    1. 数据库
    2. 缓存
    3. 清理过期数据
    4. 默认商品
    """
    logger.info("[Init] 开始初始化...")

    # 1. 数据库
    await initialize_database()

    # 2. 缓存
    await initialize_cache()

    # 3. 清理过期数据
    await cleanup_expired_data()

    # 4. 默认商品
    await initialize_goods()

    logger.info("[Init] 初始化完成！")


# ============================================================
# Bot 启动时自动调用
# ============================================================
def register_init_hook():
    """
    注册初始化钩子，在 Bot 启动时自动调用

    在 bot.py 中调用此函数：
    ```python
    from utils.db_init import register_init_hook
    register_init_hook()
    ```
    """
    try:
        from nonebot import get_driver

        driver = get_driver()

        @driver.on_startup
        async def _init():
            await initialize_all()

        @driver.on_shutdown
        async def _shutdown():
            try:
                from utils.database import Database
                await Database.close()
                logger.info("[Init] 数据库连接已关闭")
            except Exception as e:
                logger.warning(f"[Init] 关闭数据库失败: {e}")

        logger.info("[Init] 初始化钩子已注册")

    except ImportError:
        logger.warning("[Init] 无法注册钩子（nonebot 未加载）")


# ============================================================
# 便捷函数
# ============================================================
async def init():
    """初始化所有模块"""
    await initialize_all()
