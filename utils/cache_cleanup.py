"""
MikuBot 缓存图片自动清理
================================
- 每天定时清理 data/screenshots 等目录下的缓存图片
- 使用 APScheduler AsyncIOScheduler，支持多个时间点
- 支持配置清理目录、扩展名、最大保留时间

用法（在 bot.py 启动时调用）:
    from utils.cache_cleanup import start_cleanup
    start_cleanup(nonebot.get_driver())
"""

import re
import time
import os
import sys
from pathlib import Path

from nonebot import get_driver
from nonebot.log import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config_manager import config_manager


# ======================================================================
# 【核心】插件配置注册（模板字符串放在插件自身/模块里）
# ======================================================================
_CLEANUP_TEMPLATE = (
    "\n"
    "cache_cleanup:\n"
    "  # 是否启用自动清理（true/false）\n"
    "  enabled: true\n"
    "  # 每日清理时间（HH:MM 格式，24 小时制，支持多个时间点）\n"
    "  cleanup_times:\n"
    "  - 00:00\n"
    "  # 需要清理的缓存目录列表（相对于项目根目录）\n"
    "  cache_dirs:\n"
    "  - data/screenshots\n"
    "  # 缓存文件最长保留小时数（0 表示每次都清理所有文件）\n"
    "  max_age_hours: 0\n"
    "  # 要清理的文件扩展名列表\n"
    "  file_extensions:\n"
    "  - .png\n"
    "  - .jpg\n"
    "  - .jpeg\n"
    "  # 是否在日志中输出详细清理信息（true/false）\n"
    "  verbose: true\n"
)

_cfg = config_manager.register_plugin(
    "cache_cleanup",
    defaults={
        "enabled": True,
        "cleanup_times": ["00:00"],
        "cache_dirs": ["data/screenshots", "data/images"],
        "max_age_hours": 0,
        "file_extensions": [".png", ".jpg", ".jpeg"],
        "verbose": True,
    },
    template_str=_CLEANUP_TEMPLATE,
    description="缓存图片自动清理配置",
)

def _normalize_list(value, default):
    """
    将配置值规范化为 list。
    - 已是 list/tuple：直接转 list 返回
    - 字符串：按逗号分割（兼容 "00:00" 单值或 "00:00, 06:00" 多值）
    - None/其它：返回 default
    防止 YAML 误把单个值写成字符串时被 list(str) 拆成字符序列。
    """
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple)):
        return [v for v in value]
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return [p for p in parts if p]
    return list(default)


ENABLED = bool(_cfg.get("enabled", True))
CLEANUP_TIMES = _normalize_list(_cfg.get("cleanup_times"), ["00:00"])
CACHE_DIRS = _normalize_list(_cfg.get("cache_dirs"), ["data/screenshots"])
MAX_AGE_HOURS = int(_cfg.get("max_age_hours", 0) or 0)
FILE_EXTENSIONS = _normalize_list(_cfg.get("file_extensions"), [".png"])
VERBOSE = bool(_cfg.get("verbose", True))


# ======================================================================
# 工具函数
# ======================================================================

_time_re = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_time(t: str) -> tuple[int, int] | None:
    """解析 "HH:MM" 为 (hour, minute)，非法返回 None"""
    if not t:
        return None
    m = _time_re.match(str(t).strip())
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return (h, mi)


# ======================================================================
# 清理逻辑
# ======================================================================

def _do_cleanup() -> None:
    """执行一次缓存清理。同步函数（文件 IO 不需要 asyncio）。"""
    now = time.time()
    total_found = 0
    total_removed = 0
    total_errors = 0

    for rel_dir in CACHE_DIRS:
        base_dir = (Path(__file__).resolve().parent.parent / rel_dir).resolve()
        if not base_dir.exists():
            if VERBOSE:
                logger.info(f"[cache_cleanup] 目录不存在，跳过: {base_dir}")
            continue
        if not base_dir.is_dir():
            logger.warning(f"[cache_cleanup] 路径不是目录，跳过: {base_dir}")
            continue

        # 遍历目录（不递归子目录；如需递归可改成 rglob）
        for item in base_dir.iterdir():
            if not item.is_file():
                continue
            if item.suffix.lower() not in FILE_EXTENSIONS:
                continue

            total_found += 1

            # 按文件最后修改时间判断是否超过 max_age_hours
            if MAX_AGE_HOURS > 0:
                try:
                    age_hours = (now - item.stat().st_mtime) / 3600.0
                    if age_hours < MAX_AGE_HOURS:
                        if VERBOSE:
                            logger.debug(
                                f"[cache_cleanup] 保留（{age_hours:.1f}h < {MAX_AGE_HOURS}h）: {item.name}"
                            )
                        continue
                except OSError as e:
                    total_errors += 1
                    logger.warning(f"[cache_cleanup] 读取文件时间失败: {item.name} ({e})")
                    continue

            # 执行删除
            try:
                os.remove(item)
                total_removed += 1
                if VERBOSE:
                    logger.info(f"[cache_cleanup] 已删除: {item.name}")
            except Exception as e:
                total_errors += 1
                logger.warning(f"[cache_cleanup] 删除失败: {item.name} ({e})")

    # 汇总信息（无论 verbose 如何都打印一条）
    logger.info(
        f"[cache_cleanup] 清理完成，扫描 {total_found} 个文件，"
        f"成功删除 {total_removed} 个，失败 {total_errors} 个"
    )


async def cleanup_cache() -> None:
    """异步版本的清理（供 scheduler 或手动触发）。"""
    try:
        # 文件 IO 放在线程里跑，避免阻塞事件循环
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _do_cleanup)
    except Exception as e:
        logger.exception(f"[cache_cleanup] 清理任务异常: {e}")


# ======================================================================
# scheduler 管理
# ======================================================================

_scheduler = None


def start_cleanup(driver=None) -> None:
    """
    启动定时清理任务。推荐在 bot.py 启动时调用一次：
        from utils.cache_cleanup import start_cleanup
        start_cleanup(nonebot.get_driver())

    如果 APScheduler 未安装，会打印一条警告但不阻止 Bot 启动。
    使用 driver 的 bot_connect 事件延迟启动，确保事件循环已运行。
    """
    global _scheduler

    if not ENABLED:
        logger.info("[cache_cleanup] 已在配置中禁用，不启动清理任务")
        return

    # 校验并规范化清理时间
    valid_times: list[tuple[int, int]] = []
    for t in CLEANUP_TIMES:
        parsed = _parse_time(t)
        if parsed is None:
            logger.warning(f"[cache_cleanup] 跳过非法的时间配置: {t!r}")
        else:
            valid_times.append(parsed)

    if not valid_times:
        logger.warning("[cache_cleanup] 没有合法的清理时间，任务未启动")
        return

    # 校验目录
    valid_dirs = [str(d).strip() for d in CACHE_DIRS if str(d).strip()]
    if not valid_dirs:
        logger.warning("[cache_cleanup] 没有配置任何清理目录，任务未启动")
        return

    # 检查 APScheduler 是否已安装
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "[cache_cleanup] APScheduler 未安装，缓存清理任务已跳过。"
            "请执行: .venv\\Scripts\\python.exe -m pip install APScheduler"
        )
        return

    def _do_start():
        """实际启动 scheduler（在事件循环运行后调用）"""
        global _scheduler
        if _scheduler is not None and _scheduler.running:
            logger.warning("[cache_cleanup] scheduler 已在运行，不重复启动")
            return

        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        for hour, minute in valid_times:
            scheduler.add_job(
                cleanup_cache,
                trigger=CronTrigger(hour=hour, minute=minute),
                id=f"cache_cleanup_{hour:02d}{minute:02d}",
                name=f"缓存清理 {hour:02d}:{minute:02d}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )

        scheduler.start()
        _scheduler = scheduler

        # 信息打印
        time_strs = [f"{h:02d}:{m:02d}" for h, m in sorted(valid_times)]
        logger.info(
            f"[cache_cleanup] 缓存清理已启动，每日时间: {', '.join(time_strs)}；"
            f"目录: {', '.join(valid_dirs)}；"
            f"扩展名: {', '.join(FILE_EXTENSIONS)}；"
            f"最大保留: {'不限制' if MAX_AGE_HOURS <= 0 else f'{MAX_AGE_HOURS} 小时'}"
        )

    # 如果传入了 driver，在 bot 连接后启动（确保事件循环已运行）
    if driver is not None:
        @driver.on_bot_connect
        async def _on_bot_connect(bot):
            _do_start()

        @driver.on_shutdown
        async def _on_shutdown():
            global _scheduler
            if _scheduler and _scheduler.running:
                logger.info("[cache_cleanup] 正在停止清理调度器")
                try:
                    _scheduler.shutdown(wait=False)
                except Exception:
                    pass
                _scheduler = None
    else:
        # 没有 driver 时尝试直接启动（可能失败，调用者需捕获异常）
        _do_start()


def stop_cleanup() -> None:
    """停止清理调度器（一般不需要手动调用）。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning(f"[cache_cleanup] 停止 scheduler 异常: {e}")
        finally:
            _scheduler = None
            logger.info("[cache_cleanup] 清理调度器已停止")
