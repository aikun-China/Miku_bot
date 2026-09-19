"""
Miku 商店插件
=============
- 用户指令：
    购买 <内容/编号> [数量]      扣金币，购买商品
    商店                          查看所有商品
    商店列表                      查看所有商品
- 管理员指令（NoneBot SUPERUSER）：
    商店上传 <内容> <描述> <定价>   添加新商品（自动按顺序编号）
    商店更改 <内容/编号> <字段> <值> 修改商品（字段=内容/描述/定价）
    商店删除 <内容/编号>            删除商品
- 数据保存：data/shop.json
- 响应风格：图片卡片（658×987，双行显示内容）
- 滤镜和显示效果参考签到插件（青绿色调 + 毛玻璃 + 渐变叠加）
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.event import Event
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from nonebot.log import logger

import sys
import json
import base64
import mimetypes
import types
import importlib
import inspect
import threading
from pathlib import Path

# 项目根目录：向上两级（用于导入 utils）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 导入菜单注册
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

# 插件自身的模板目录
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.user_store import (
    get_balance,
    add_coins,
    add_item,
    get_items,
    use_item,
    ITEM_DOUBLE_FAVOR,
)
from utils.bg_helper import find_and_load_bg, load_bg_as_data_uri
from utils.config_manager import config_manager
from utils.avatar_cache import get_avatar_data_uri


# ============================================================
# 配置（首次加载自动注册到 config/bot.yaml）
# ============================================================
_SHOP_TEMPLATE = (
    "\n"
    "miku_shop:\n"
    "  # 是否启用商店功能\n"
    "  enabled: true\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
    "  # 商店卡片宽度（像素）\n"
    "  card_width: 658\n"
    "  # 商店卡片高度（像素）—— 按商品数自动：基础 380 + 每个商品 110\n"
    "  card_height: 987\n"
)

_cfg = config_manager.register_plugin(
    "miku_shop",
    defaults={
        "enabled": True,
        "response_style": "card",
        "card_width": 658,
        "card_height": 987,
    },
    template_str=_SHOP_TEMPLATE,
    description="商店插件配置（商品管理 / 购买扣金币）",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_shop", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_shop", key, default)


# ============================================================
# 数据存储：data/shop.json
# ============================================================
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SHOP_FILE = DATA_DIR / "shop.json"

# 商店数据读写锁，防止并发竞态
_shop_lock = threading.Lock()


def _load_shop() -> dict:
    """加载商品列表。结构：{ "next_id": int, "items": [ {id, name, desc, price, sold} ] }"""
    if not SHOP_FILE.exists():
        return {"next_id": 1, "items": []}
    try:
        data = json.loads(SHOP_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"next_id": 1, "items": []}
        data.setdefault("next_id", 1)
        data.setdefault("items", [])
        if not isinstance(data["items"], list):
            data["items"] = []
        return data
    except Exception as e:
        logger.warning(f"[商店] 读取 shop.json 失败，重置：{e}")
        return {"next_id": 1, "items": []}


def _save_shop(data: dict) -> None:
    SHOP_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_item(query: str):
    """按编号或名称查找商品。返回 (index, item) 或 (None, None)。"""
    if query is None:
        return None, None
    q = str(query).strip()
    if not q:
        return None, None
    data = _load_shop_with_prefab()
    items = data.get("items", [])

    # 编号（纯数字）
    if q.isdigit():
        idx = int(q) - 1
        if 0 <= idx < len(items):
            return idx, items[idx]
        return None, None

    # 名称匹配（忽略大小写 / 空格）
    qn = q.replace(" ", "").replace("\u3000", "").lower()
    for i, it in enumerate(items):
        name = str(it.get("name", "")).replace(" ", "").replace("\u3000", "").lower()
        if name == qn:
            return i, it
    # 模糊
    for i, it in enumerate(items):
        name = str(it.get("name", "")).replace(" ", "").replace("\u3000", "").lower()
        if qn in name:
            return i, it
    return None, None


# ============================================================
# HTML 转义
# ============================================================
def _html_escape(s) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ============================================================
# 底图（658 × 987）和标题图标
# ============================================================
def _load_bg_data_uri() -> str:
    """底图：扫描插件自身的 templates/ 目录。"""
    return find_and_load_bg("shop", TEMPLATES_DIR)


def _load_title_icon() -> str:
    """标题区初音图片。"""
    for name in ["shop_icon.png", "shop_miku.png", "miku.png"]:
        p = TEMPLATES_DIR / name
        if p.is_file():
            try:
                mime, _ = mimetypes.guess_type(p.name)
                if not mime:
                    mime = "image/png"
                raw = p.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                return (
                    f'<img class="title-icon" '
                    f'src="data:{mime};base64,{b64}" '
                    f'alt="Miku">'
                )
            except Exception as e:
                logger.warning(f"[商店] 读取标题图片失败：{p} - {e}")
    return ""


# ============================================================
# 用户昵称
# ============================================================
def _nickname(event) -> str:
    try:
        if event.sender and event.sender.nickname:
            return event.sender.nickname
    except Exception:
        pass
    return str(event.get_user_id())


def _parse_args(msg: str, cmd: str) -> str:
    """从消息中去除命令前缀，返回参数。"""
    if msg.startswith(cmd):
        return msg[len(cmd):].strip()
    return ""


# ============================================================
# 商品列表 HTML 构建
# ============================================================
def _build_items_html(items: list) -> str:
    """双行显示：第 1 行 = 编号 + 名称 + 价格，第 2 行 = 描述。"""
    if not items:
        return '<div class="empty">商店暂无商品</div>'

    parts = []
    for i, it in enumerate(items, start=1):
        name = _html_escape(it.get("name", "未命名"))
        desc = _html_escape(it.get("desc", "（暂无描述）"))
        price = int(it.get("price", 0) or 0)
        sold = int(it.get("sold", 0) or 0)

        parts.append(
            f'<div class="item">'
            f'  <div class="item-row item-row-1">'
            f'    <span class="item-index">#{i:02d}</span>'
            f'    <span class="item-name">{name}</span>'
            f'    <span class="item-price">{price} 金币</span>'
            f'  </div>'
            f'  <div class="item-row item-row-2">'
            f'    <span class="item-desc">{desc}</span>'
            f'  </div>'
            f'  <div class="item-sold">已售 {sold}</div>'
            f'</div>'
        )
    return "\n".join(parts)


# ============================================================
# 预制商品自动检测与导入
# ============================================================
# 其他插件可以通过以下方式注册预制商品：
# 1. 在插件目录下创建 shop_items.json 文件
# 2. 或在 __init__.py 中定义 SHOP_ITEMS 变量
# 
# 格式要求：
# [
#   {"name": "商品名", "desc": "描述", "price": 100, "plugin": "插件名"},
#   {"name": "另一个商品", "desc": "描述", "price": 200, "plugin": "插件名"}
# ]


def _scan_plugin_shop_items() -> list:
    """扫描所有插件目录，收集预制商品。"""
    items = []
    plugins_dir = PROJECT_ROOT / "plugins"
    if not plugins_dir.exists():
        return items

    for plugin_dir in plugins_dir.iterdir():
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name == "miku_shop":
            continue

        # 方式1：读取 shop_items.json
        json_file = plugin_dir / "shop_items.json"
        if json_file.exists():
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("name") and item.get("price"):
                            items.append({
                                "name": str(item["name"]),
                                "desc": str(item.get("desc", "")),
                                "price": int(item["price"]) if item.get("price") else 0,
                                "plugin": str(item.get("plugin", plugin_dir.name)),
                            })
            except Exception as e:
                logger.warning(f"[商店] 读取 {json_file} 失败：{e}")
                continue
        
        # 方式2：尝试导入插件并读取 SHOP_ITEMS 变量
        try:
            plugin_name = plugin_dir.name
            module_path = f"plugins.{plugin_name}"
            if module_path in sys.modules:
                mod = sys.modules[module_path]
            else:
                spec = importlib.util.spec_from_file_location(
                    plugin_name, str(plugin_dir / "__init__.py")
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules.setdefault("nonebot", types.ModuleType("nonebot"))
                    sys.modules.setdefault("nonebot.log", types.ModuleType("nonebot.log"))
                    sys.modules["nonebot.log"].logger = type("L", (), {
                        "warning": lambda *a, **k: None,
                        "info": lambda *a, **k: None,
                    })()
                    spec.loader.exec_module(mod)
            
            if hasattr(mod, "SHOP_ITEMS"):
                shop_items = getattr(mod, "SHOP_ITEMS")
                if isinstance(shop_items, list):
                    for item in shop_items:
                        if isinstance(item, dict) and item.get("name") and item.get("price"):
                            items.append({
                                "name": str(item["name"]),
                                "desc": str(item.get("desc", "")),
                                "price": int(item["price"]) if item.get("price") else 0,
                                "plugin": str(item.get("plugin", plugin_dir.name)),
                            })
            if hasattr(mod, "SHOP_ITEM_USE_HANDLERS"):
                handlers = getattr(mod, "SHOP_ITEM_USE_HANDLERS")
                if isinstance(handlers, dict):
                    for item_name, handler in handlers.items():
                        register_shop_item_use_effect(item_name, handler)
        except Exception as e:
            logger.debug(f"[商店] 导入 {plugin_dir.name} 失败：{e}")
            continue

    return items


def _merge_prefab_items(items: list) -> list:
    """合并本地商店商品与插件预制商品，按名称去重。"""
    if not isinstance(items, list):
        items = []

    merged = list(items)
    existing_names = set(
        _normalize_special_item_name(it.get("name", ""))
        for it in merged
        if isinstance(it, dict)
    )

    for item in _scan_plugin_shop_items():
        if not isinstance(item, dict):
            continue
        normalized_name = _normalize_special_item_name(item.get("name", ""))
        if not normalized_name or normalized_name in existing_names:
            continue
        merged.append(item)
        existing_names.add(normalized_name)

    return merged


def _load_shop_with_prefab() -> dict:
    """加载商品列表，并自动导入其他插件的预制商品。"""
    data = _load_shop()
    data["items"] = _merge_prefab_items(data.get("items", []))
    data["next_id"] = len(data["items"]) + 1
    return data


def _normalize_special_item_name(name: str) -> str:
    if not name:
        return ""
    return "".join(ch for ch in str(name).strip().lower() if ch.isalnum())


_SPECIAL_ITEM_KEYS = {
    _normalize_special_item_name("双倍好感卡"): ITEM_DOUBLE_FAVOR,
}


_USE_ITEM_EFFECT_HANDLERS = {}


def register_shop_item_use_effect(name, handler):
    """注册商品使用效果。"""
    if not callable(handler):
        return False
    normalized = _normalize_special_item_name(name)
    if not normalized:
        return False
    _USE_ITEM_EFFECT_HANDLERS[normalized] = handler
    return True


def _get_shop_item_use_effect_handler(name: str):
    normalized = _normalize_special_item_name(name)
    return _USE_ITEM_EFFECT_HANDLERS.get(normalized)


def _call_shop_item_use_effect_handler(handler, bot, event, user_id, name, qty, owned, key, phase=None, remaining=None):
    sig = inspect.signature(handler)
    kwargs = {}
    params = sig.parameters
    if "bot" in params:
        kwargs["bot"] = bot
    if "event" in params:
        kwargs["event"] = event
    if "user_id" in params:
        kwargs["user_id"] = user_id
    if "name" in params:
        kwargs["name"] = name
    if "qty" in params:
        kwargs["qty"] = qty
    if "owned" in params:
        kwargs["owned"] = owned
    if "key" in params:
        kwargs["key"] = key
    if "phase" in params:
        kwargs["phase"] = phase
    if "remaining" in params:
        kwargs["remaining"] = remaining
    return handler(**kwargs)


async def _send_item_use_effect_result(bot, event, result):
    if isinstance(result, str):
        await bot.send(event, result)
        return True
    if not isinstance(result, dict):
        return False

    sent = False
    text = result.get("text")
    send_list = result.get("send") or []
    image = result.get("image")
    video = result.get("video")
    if text:
        await bot.send(event, text)
        sent = True
    for msg in send_list:
        await bot.send(event, msg)
        sent = True
    if image:
        await bot.send(event, MessageSegment.image(image))
        sent = True
    if video:
        await bot.send(event, MessageSegment.video(video))
        sent = True
    return sent


def _special_item_key(name: str):
    normalized = _normalize_special_item_name(name)
    if not normalized:
        return None
    return _SPECIAL_ITEM_KEYS.get(normalized)


def _resolved_item_key(idx: int, name: str) -> str:
    special_key = _special_item_key(name)
    if special_key:
        return special_key
    return _item_key(idx, name)


# ============================================================
# 指令
# ============================================================
buy_cmd = on_command(
    "购买",
    aliases={"buy"},
    priority=10,
    block=True,
)

shop_cmd = on_command(
    "商店",
    aliases={"shop", "商城"},
    priority=10,
    block=True,
)

shoplist_cmd = on_command(
    "商店列表",
    aliases={"商品列表", "shop_list"},
    priority=10,
    block=True,
)

shop_upload_cmd = on_command(
    "商店上传",
    aliases={"上架", "商品上传"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)

shop_edit_cmd = on_command(
    "商店更改",
    aliases={"商品更改", "商品修改"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)

shop_delete_cmd = on_command(
    "商店删除",
    aliases={"商品删除", "下架"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)

# 「使用」—— 消耗用户背包中的商店道具
use_cmd = on_command(
    "使用",
    aliases={"use", "使用道具"},
    priority=10,
    block=True,
)

# 「查询」—— 查询用户拥有的某商品数量
query_cmd = on_command(
    "查询",
    aliases={"商品查询", "背包", "shop_query"},
    priority=10,
    block=True,
)


# ============================================================
# 商店卡片（图片）
# ============================================================
async def _render_shop_card(items: list, title: str = "MIKU 商店") -> str:
    """渲染商店列表卡片并返回图片路径。"""
    bg_uri = _load_bg_data_uri()
    title_icon = _load_title_icon()
    items_html = _build_items_html(items)

    html = render_template(
        "shop",
        TEMPLATES_DIR,
        bg_data_uri=bg_uri,
        title_icon=title_icon,
        title=title,
        items_html=items_html,
        total=len(items),
    )

    # 高度按商品数自动计算：基础 380 + 每个商品 110（最小 400）
    n = len(items)
    auto_height = max(400, 380 + n * 110)
    height = int(_conf("card_height", auto_height) or auto_height)
    if height == 987 and n != 6:
        # 默认值且商品数不对，按自动
        height = auto_height

    img_path = await screenshot_html(
        html,
        width=int(_conf("card_width", 658)),
        height=height,
    )
    return str(img_path)


# ============================================================
# 处理：购买
# ============================================================
@buy_cmd.handle()
async def _handle_buy(bot, event: Event):
    if not _is_enabled():
        return

    msg = _parse_args(str(event.get_message()).strip(), "购买")
    # 兼容「购买 双倍好感卡」「购买 1」「购买 双倍好感卡*3」「购买 1 3」
    query, qty = _parse_qty(msg, default=1)

    if not query:
        await buy_cmd.finish(
            "❓ 用法：购买 <内容/编号> [数量]\n"
            "　　示例：购买 双倍好感卡\n"
            "　　示例：购买 1\n"
            "　　示例：购买 双倍好感卡*3"
        )
        return

    if qty <= 0:
        await buy_cmd.finish("❌ 数量必须大于 0")
        return
    if qty > 999:
        await buy_cmd.finish("❌ 单次最多购买 999 个")
        return

    idx, item = _find_item(query)
    if item is None:
        await buy_cmd.finish(f"❌ 未找到商品「{query}」\n　　可发送「商店」查看商品列表")
        return

    name = item.get("name", "未命名")
    price = int(item.get("price", 0) or 0)
    total = price * qty

    user_id = str(event.get_user_id())
    balance = get_balance(user_id)

    if balance < total:
        await buy_cmd.finish(
            f"❌ 金币不足！\n"
            f"　　需要：{total} 金币\n"
            f"　　当前：{balance} 金币\n"
            f"　　可发送「签到」获取金币"
        )
        return

    # 扣金币 + 加道具
    new_balance = add_coins(user_id, -total)
    item_key = _resolved_item_key(idx, name)
    add_item(user_id, item_key, qty)

    # 更新销量
    with _shop_lock:
        data = _load_shop_with_prefab()
        if 0 <= idx < len(data["items"]):
            data["items"][idx]["sold"] = int(data["items"][idx].get("sold", 0)) + qty
            _save_shop(data)

    # 道具背包摘要
    items = get_items(user_id)
    owned = items.get(item_key, 0)

    await buy_cmd.finish(
        f"购买{name}*{qty}成功\n"
        f"你的{name}剩余{owned}个"
    )


# ============================================================
# 处理：使用（消耗用户背包中的商店道具）
# ============================================================
@use_cmd.handle()
async def _handle_use(bot, event: Event):
    if not _is_enabled():
        return

    raw_msg = str(event.get_message()).strip()
    msg = _parse_args(raw_msg, "使用道具")
    if not msg:
        msg = _parse_args(raw_msg, "使用")
    if not msg:
        msg = _parse_args(raw_msg, "use")
    query, qty = _parse_qty(msg, default=1)

    if not query:
        await use_cmd.finish(
            "❓ 用法：使用/使用道具 <内容/编号> [数量]\n"
            "　　示例：使用 双倍好感卡\n"
            "　　示例：使用道具 改名卡*2\n"
            "　　示例：使用 1"
        )
        return

    if qty <= 0:
        await use_cmd.finish("❌ 数量必须大于 0")
        return
    if qty > 999:
        await use_cmd.finish("❌ 单次最多使用 999 个")
        return

    user_id = str(event.get_user_id())
    idx, name, owned, key = _find_user_item(user_id, query)
    if name is None:
        await use_cmd.finish(
            f"❌ 未找到商品「{query}」\n　　可发送「商店」查看商品列表"
        )
        return

    if owned <= 0:
        await use_cmd.finish(
            f"❌ 你还没有「{name}」\n　　可发送「购买 {name}」获取"
        )
        return

    if owned < qty:
        await use_cmd.finish(
            f"❌ 「{name}」数量不足！\n"
            f"　　需要：{qty} 个\n"
            f"　　当前：{owned} 个"
        )
        return

    remaining_after_consume = max(0, owned - qty)
    handler = _get_shop_item_use_effect_handler(name)
    consume = True
    result = None
    if handler:
        result = _call_shop_item_use_effect_handler(
            handler,
            bot=bot,
            event=event,
            user_id=user_id,
            name=name,
            qty=qty,
            owned=owned,
            key=key,
            remaining=remaining_after_consume,
        )
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, dict) and "consume" in result:
            consume = bool(result.get("consume"))

    if consume:
        success = use_item(user_id, key, qty)
        if not success:
            await use_cmd.finish("❌ 扣除失败，请稍后重试")
            return

    if result is not None:
        sent = await _send_item_use_effect_result(bot, event, result)
        if sent:
            return

    if consume:
        await use_cmd.finish(
            f"使用{name}*{qty}成功\n"
            f"你的{name}剩余{remaining_after_consume}个"
        )
        return

    await use_cmd.finish("❌ 无法使用该道具")


# ============================================================
# 处理：查询（查询用户拥有的某商品数量）
# ============================================================
@query_cmd.handle()
async def _handle_query(bot, event: Event):
    if not _is_enabled():
        return

    msg = _parse_args(str(event.get_message()).strip(), "查询")
    query, qty = _parse_qty(msg, default=1)

    if not query:
        await query_cmd.finish(
            "❓ 用法：查询 <内容/编号>\n"
            "　　示例：查询 改名卡\n"
            "　　示例：查询 1"
        )
        return

    user_id = str(event.get_user_id())
    idx, name, owned, key = _find_user_item(user_id, query)
    if name is None:
        await query_cmd.finish(
            f"❌ 未找到商品「{query}」\n　　可发送「商店」查看商品列表"
        )
        return

    await query_cmd.finish(f"你的{name}剩余{owned}个")


def _safe_key(s: str) -> str:
    """把商品名转成英数下划线，作为 item key 的一部分。"""
    out = []
    for ch in str(s):
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    k = "".join(out).strip("_").lower()
    return k or "item"


def _item_key(idx: int, name: str) -> str:
    """根据商品在列表中的位置和名称生成唯一的背包 key。"""
    return f"shop_{idx + 1}_{_safe_key(name)}"


def _find_user_item(user_id: str, query: str):
    """
    在用户背包中查找道具。返回 (idx, name, owned, item_key) 或 (None, None, 0, None)。
    逻辑：
      1. 优先按商品「编号」定位 → 查该商品 key
      2. 编号不存在则按「名称」匹配 → 找名字相同的 key
    """
    idx, item = _find_item(query)
    if item is None:
        return None, None, 0, None

    name = str(item.get("name", "未命名"))
    normal_key = _item_key(idx, name)
    special_key = _special_item_key(name)

    items = get_items(user_id)
    if special_key and int(items.get(special_key, 0)) > 0:
        return idx, name, int(items.get(special_key, 0)), special_key

    owned = int(items.get(normal_key, 0))
    return idx, name, owned, normal_key


def _parse_qty(text: str, default: int = 1) -> tuple:
    """
    从「数量后缀」解析出 (目标字符串, 数量)。
    支持：
      使用 改名卡            -> ("改名卡", 1)
      使用 改名卡 3          -> ("改名卡", 3)
      使用 改名卡*3          -> ("改名卡", 3)
      使用 改名卡 *3         -> ("改名卡", 3)
      使用 1*2               -> ("1", 2)
    """
    if text is None:
        return "", default
    s = str(text).strip()
    if not s:
        return "", default

    # 1) 先找 * 分隔（带或不带空格）
    if "*" in s:
        head, _, tail = s.partition("*")
        head = head.strip()
        tail = tail.strip()
        try:
            n = int(tail) if tail else default
        except ValueError:
            return s, default
        return head, max(1, n)

    # 2) 空格分隔且最后一段是纯数字
    parts = s.rsplit(maxsplit=1)
    if len(parts) == 2 and parts[1].isdigit():
        try:
            return parts[0].strip(), max(1, int(parts[1]))
        except ValueError:
            return s, default

    return s, default


# ============================================================
# 处理：商店 / 商店列表
# ============================================================
@shop_cmd.handle()
async def _handle_shop(bot, event: Event):
    await _send_shop_list(shop_cmd, title="MIKU 商店")


@shoplist_cmd.handle()
async def _handle_shoplist(bot, event: Event):
    await _send_shop_list(shoplist_cmd, title="MIKU 商品列表")


async def _send_shop_list(matcher, title: str):
    if not _is_enabled():
        return

    data = _load_shop_with_prefab()
    items = data.get("items", [])

    if _conf("response_style") == "text":
        if not items:
            await matcher.finish("🛒 商店暂无商品")
        lines = ["🛒 MIKU 商店", "─" * 30]
        for i, it in enumerate(items, start=1):
            lines.append(
                f"[{i}] {it.get('name', '未命名')} - "
                f"{int(it.get('price', 0))} 金币"
            )
        lines.append("─" * 30)
        lines.append("💡 发送「购买 <编号/名称> [数量]」下单")
        await matcher.finish("\n".join(lines))
        return

    try:
        img_path = await _render_shop_card(items, title=title)
        await matcher.finish(MessageSegment.image(to_image_uri(img_path)))
    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"[商店] 生成图片失败：{e}")
        if not items:
            await matcher.finish("🛒 商店暂无商品")
        lines = ["🛒 MIKU 商店", "─" * 30]
        for i, it in enumerate(items, start=1):
            lines.append(
                f"[{i}] {it.get('name', '未命名')} - "
                f"{int(it.get('price', 0))} 金币"
            )
        await matcher.finish("\n".join(lines))


# ============================================================
# 处理：商店上传（管理员）
# ============================================================
@shop_upload_cmd.handle()
async def _handle_upload(bot, event: Event):
    if not _is_enabled():
        return

    msg = str(event.get_message()).strip()
    # 先去除命令前缀
    cmd_aliases = ["商店上传", "上架", "商品上传"]
    for cmd in cmd_aliases:
        if msg.startswith(cmd):
            msg = msg[len(cmd):].strip()
            break
    # 用法：商店上传 <内容> <描述> <定价>
    args = msg.split(maxsplit=2)
    if len(args) < 3:
        await shop_upload_cmd.finish(
            "❓ 用法：商店上传 <内容> <描述> <定价>\n"
            "　　示例：商店上传 双倍好感卡 下次签到好感 100% 双倍 200"
        )
        return

    name = args[0].strip()
    # 描述和定价之间用最后一个空格分隔
    rest = args[2]
    # 定价 = 最后一段（整数）
    rest_parts = rest.rsplit(maxsplit=1)
    if len(rest_parts) != 2:
        await shop_upload_cmd.finish(
            "❓ 定价必须是整数\n"
            "　　示例：商店上传 双倍好感卡 下次签到好感 100% 双倍 200"
        )
        return

    desc = rest_parts[0].strip()
    try:
        price = int(rest_parts[1])
    except ValueError:
        await shop_upload_cmd.finish(f"❌ 定价必须是整数：{rest_parts[1]}")
        return

    if not name:
        await shop_upload_cmd.finish("❌ 商品名不能为空")
        return
    if price < 0:
        await shop_upload_cmd.finish("❌ 定价不能为负数")
        return

    # 在锁内完成数据操作
    upload_ok = False
    dup_msg = ""
    item_count = 0
    with _shop_lock:
        data = _load_shop_with_prefab()
        # 名称重复检查
        for it in data["items"]:
            if str(it.get("name", "")).strip() == name:
                dup_msg = f"❌ 商品「{name}」已存在（编号 #{data['items'].index(it) + 1}）\n　　请用「商店更改」修改"
                break
        if not dup_msg:
            new_id = int(data.get("next_id", 1))
            new_item = {
                "id": new_id,
                "name": name,
                "desc": desc,
                "price": price,
                "sold": 0,
            }
            data["items"].append(new_item)
            data["next_id"] = new_id + 1
            _save_shop(data)
            item_count = len(data["items"])
            upload_ok = True

    if dup_msg:
        await shop_upload_cmd.finish(dup_msg)
        return

    if upload_ok:
        await shop_upload_cmd.finish(
            f"✅ 上架成功！\n"
            f"　　编号：#{item_count:02d}\n"
            f"　　名称：{name}\n"
            f"　　描述：{desc}\n"
            f"　　定价：{price} 金币"
        )


# ============================================================
# 处理：商店更改（管理员）
# ============================================================
@shop_edit_cmd.handle()
async def _handle_edit(bot, event: Event):
    if not _is_enabled():
        return

    msg = str(event.get_message()).strip()
    # 先去除命令前缀
    cmd_aliases = ["商店更改", "商品更改", "商品修改"]
    for cmd in cmd_aliases:
        if msg.startswith(cmd):
            msg = msg[len(cmd):].strip()
            break
    # 用法：商店更改 <内容/编号> <字段> <新值>
    # 字段=内容/描述/定价
    parts = msg.split(maxsplit=2)
    if len(parts) < 3:
        await shop_edit_cmd.finish(
            "❓ 用法：商店更改 <内容/编号> <字段> <新值>\n"
            "　　字段：内容 / 描述 / 定价\n"
            "　　示例：商店更改 1 定价 300\n"
            "　　示例：商店更改 双倍好感卡 描述 永久双倍"
        )
        return

    query = parts[0].strip()
    field_raw = parts[1].strip()
    value = parts[2].strip()

    # 字段归一化
    field_map = {
        "内容": "name", "名称": "name", "名字": "name", "name": "name",
        "描述": "desc", "说明": "desc", "desc": "desc",
        "定价": "price", "价格": "price", "金币": "price", "price": "price",
    }
    field = field_map.get(field_raw)
    if not field:
        await shop_edit_cmd.finish(
            f"❌ 未知字段「{field_raw}」\n"
            f"　　支持：内容 / 描述 / 定价"
        )
        return

    idx, item = _find_item(query)
    if item is None:
        await shop_edit_cmd.finish(f"❌ 未找到商品「{query}」")
        return

    # 在锁内完成数据操作
    edit_error = ""
    old_val = None
    new_val = None
    item_name = ""
    with _shop_lock:
        data = _load_shop_with_prefab()
        if idx < 0 or idx >= len(data["items"]):
            edit_error = f"❌ 未找到商品「{query}」"
        else:
            if field == "price":
                try:
                    new_val = int(value)
                except ValueError:
                    edit_error = f"❌ 定价必须是整数：{value}"
                if not edit_error and new_val < 0:
                    edit_error = "❌ 定价不能为负数"
            else:
                if field == "name":
                    # 名称不能与其它商品重复
                    for j, other in enumerate(data["items"]):
                        if j == idx:
                            continue
                        if str(other.get("name", "")).strip() == value:
                            edit_error = f"❌ 名称「{value}」已被其他商品占用"
                            break
                new_val = value

            if not edit_error:
                old_val = data["items"][idx].get(field)
                data["items"][idx][field] = new_val
                item_name = data["items"][idx].get("name", "未命名")
                _save_shop(data)

    if edit_error:
        await shop_edit_cmd.finish(edit_error)
        return

    pretty_field = {"name": "内容", "desc": "描述", "price": "定价"}.get(field, field)
    pretty_old = old_val if old_val is not None else "（空）"
    pretty_new = new_val

    await shop_edit_cmd.finish(
        f"✅ 修改成功！\n"
        f"　　商品：{item_name}（#{idx + 1:02d}）\n"
        f"　　字段：{pretty_field}\n"
        f"　　原值：{pretty_old}\n"
        f"　　新值：{pretty_new}"
    )


# ============================================================
# 处理：商店删除（管理员）
# ============================================================
@shop_delete_cmd.handle()
async def _handle_delete(bot, event: Event):
    if not _is_enabled():
        return

    msg = str(event.get_message()).strip()
    # 先去除命令前缀
    cmd_aliases = ["商店删除", "商品删除", "下架"]
    for cmd in cmd_aliases:
        if msg.startswith(cmd):
            msg = msg[len(cmd):].strip()
            break
    if not msg:
        await shop_delete_cmd.finish(
            "❓ 用法：商店删除 <内容/编号>\n"
            "　　示例：商店删除 1\n"
            "　　示例：商店删除 双倍好感卡"
        )
        return

    idx, item = _find_item(msg)
    if item is None:
        await shop_delete_cmd.finish(f"❌ 未找到商品「{msg}」")
        return

    # 在锁内完成数据操作
    removed_name = ""
    remaining_count = 0
    found = False
    with _shop_lock:
        data = _load_shop_with_prefab()
        if 0 <= idx < len(data["items"]):
            removed = data["items"].pop(idx)
            removed_name = removed.get("name", "未命名")
            remaining_count = len(data["items"])
            _save_shop(data)
            found = True

    if not found:
        await shop_delete_cmd.finish(f"❌ 未找到商品「{msg}」")
        return

    await shop_delete_cmd.finish(
        f"✅ 已下架：{removed_name}\n"
        f"　　当前商品数：{remaining_count}"
    )


# ─── 菜单注册 ───
register_plugin_info(
    "miku_shop",
    name="商店",
    icon="🛒",
    order=16,
    description="浏览 / 购买商品，支持管理员上架、改价、改描述",
    commands=["商店", "商店列表", "购买", "使用", "查询", "商店上传", "商店更改", "商店删除"],
    usage="""📖 用户指令：
　　商店 / 商店列表                  查看所有商品（图片卡片）
　　购买 <内容/编号> [数量]            下单购买
　　　　示例：购买 改名卡
　　　　示例：购买 1
　　　　示例：购买 改名卡*3
　　使用 <内容/编号> [数量]            消耗背包中的商品
　　　　示例：使用 改名卡
　　　　示例：使用 改名卡*2
　　查询 <内容/编号>                  查看背包中某商品的数量
　　　　示例：查询 改名卡
　　　　示例：查询 1

🔧 管理员指令（仅 SUPERUSER）：
　　商店上传 <内容> <描述> <定价>       上架新商品（自动编号）
　　　　示例：商店上传 双倍好感卡 下次签到 100% 双倍 200
　　商店更改 <内容/编号> <字段> <新值>  修改商品
　　　　字段：内容 / 描述 / 定价
　　　　示例：商店更改 1 定价 300
　　商店删除 <内容/编号>                下架商品
　　　　示例：商店删除 1

💰 货币与道具：
　　- 购买时自动扣金币（不足则提示）
　　- 道具自动入背包，使用时自动扣减
　　- 反馈格式：购买xx*x成功 / 使用xx*x成功 / 你的xx剩余x个"""
)
