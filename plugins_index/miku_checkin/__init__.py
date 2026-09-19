"""
Miku 签到插件
================
- 响应风格：图片卡片（**不发送文字**）
- 可配置：金币范围、好感度范围、双倍好感概率、卡片尺寸
- 指令：签到 / checkin / 每日签到 / 打卡 / 每日打卡
- 数据保存：data/users/{user_id}.json
- 底图：插件目录下 templates/checkin_bg.png
- 标题图：插件目录下 templates/checkin_miku.png
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

import sys
import base64
import mimetypes
from pathlib import Path

# 项目根目录：向上两级（用于导入 utils）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 插件自身的模板目录（新位置）
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.user_store import do_checkin
from utils.anime_quotes import random_quote
from utils.bg_helper import find_and_load_bg
from utils.config_manager import config_manager


# ============================================================
# 配置（首次加载自动注册到 config/bot.yaml；模板字符串放在插件自身里）
# ============================================================
_CHECKIN_TEMPLATE = (
    "\n"
    "miku_checkin:\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
    "  # 每次签到获得的金币范围（最小-最大，整数）\n"
    "  coins_min: 1\n"
    "  coins_max: 20\n"
    "  # 每次签到获得的好感度范围（最小-最大，保留 2 位小数）\n"
    "  favor_min: 0.1\n"
    "  favor_max: 1.0\n"
    "  # 「双倍好感」幸运触发概率（0-1，例如 0.03 = 3%）\n"
    "  double_favor_prob: 0.03\n"
    "  # 图片卡片宽度（像素）—— 仅 response_style=card 时生效\n"
    "  card_width: 720\n"
    "  # 图片卡片高度（像素）—— 仅 response_style=card 时生效\n"
    "  card_height: 1150\n"
)

_cfg = config_manager.register_plugin(
    "miku_checkin",
    defaults={
        "response_style": "card",
        "coins_min": 1,
        "coins_max": 20,
        "favor_min": 0.10,
        "favor_max": 1.00,
        "double_favor_prob": 0.03,
        "card_width": 720,
        "card_height": 1150,
    },
    template_str=_CHECKIN_TEMPLATE,
    description="每日签到插件配置",
)
COINS_MIN = int(_cfg.get("coins_min", 1) or 1)
COINS_MAX = int(_cfg.get("coins_max", 20) or 20)
FAVOR_MIN = float(_cfg.get("favor_min", 0.10) or 0.10)
FAVOR_MAX = float(_cfg.get("favor_max", 1.00) or 1.00)
DOUBLE_FAVOR_PROB = float(_cfg.get("double_favor_prob", 0.03) or 0.03)
CARD_WIDTH = int(_cfg.get("card_width", 720) or 720)
CARD_HEIGHT = int(_cfg.get("card_height", 1150) or 1150)


# ============================================================
# 指令
# ============================================================
checkin_cmd = on_command(
    "签到",
    aliases={"checkin", "每日签到", "打卡", "每日打卡"},
    priority=10,
    block=True,
)


# ============================================================
# 辅助：路径解析
# ============================================================
def _load_bg_data_uri() -> str:
    """底图：扫描插件自身的 templates/ 目录。"""
    return find_and_load_bg("checkin", TEMPLATES_DIR)


def _load_title_icon() -> str:
    """
    标题区的初音图片（350×382 透明 PNG）。
    路径：TEMPLATES_DIR / checkin_miku.png
    找不到时返回空字符串，模板中会隐藏图片。
    返回完整的 <img> 标签，模板直接插入。
    """
    for name in ["checkin_miku.png", "checkin_miku.jpg", "miku.png"]:
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
                logger.warning(f"[签到] 读取标题图片失败：{p} - {e}")
    logger.warning(
        f"[签到] 未找到标题图片，请把 PNG 图片保存到："
        f"{TEMPLATES_DIR / 'checkin_miku.png'}"
    )
    return ""


def _date_cn(date_str: str) -> str:
    """把 '2025-06-19' 转成 '2025年6月19日 星期四'。"""
    try:
        from datetime import date as _date
        y, m, d = date_str.split("-")
        dt = _date(int(y), int(m), int(d))
        week_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{y}年{int(m)}月{int(d)}日 {week_cn[int(dt.weekday())]}"
    except Exception:
        return date_str


def _fmt_favor(value: float) -> str:
    return f"{float(value):.2f}"


def _nickname(event) -> str:
    try:
        if event.sender and event.sender.nickname:
            return event.sender.nickname
    except Exception:
        pass
    return str(event.get_user_id())


# ============================================================
# 奖励区 HTML（插入到卡片主区域）
# ============================================================
def _build_reward_html(result: dict) -> str:
    is_first = result["is_first_today"]
    coins_added = result["coins_added"]
    favor_added = result["favor_added"]
    favor_base = result.get("favor_base", 0.0)
    double_favor = result.get("double_favor", False)

    favor_added_s = _fmt_favor(favor_added)
    favor_base_s = _fmt_favor(favor_base) if favor_base else "0.00"

    if is_first:
        main = (
            '<div class="reward">'
            f"🎉 签到成功！&nbsp;金币 <b>+{coins_added}</b>&nbsp;"
            f"&nbsp;好感度 <b>+{favor_added_s}</b>"
            "</div>"
        )
        if double_favor:
            main += (
                '<div class="bonus-card">'
                f"✦ 使用双倍好感卡：{favor_base_s} × 2 = {favor_added_s}"
                "</div>"
            )
        # buff / 卡状态（低调小字）
        parts = []
        if result.get("next_double_favor_buff"):
            parts.append("🛡 下次签到好感必双倍")
        if result.get("double_favor_card_count", 0) > 0:
            parts.append(f"🎫 双倍好感卡 × {result['double_favor_card_count']}")
        if parts:
            main += '<div class="sub-info">' + "　".join(parts) + "</div>"
    else:
        main = (
            '<div class="reward-hint">'
            f"今天已经签到过啦～&nbsp;金币 {result['coins']}&nbsp;"
            f"好感度 {_fmt_favor(result['favor'])}"
            "</div>"
        )
        parts = []
        if result.get("next_double_favor_buff"):
            parts.append("🛡 下次签到好感必双倍")
        if result.get("double_favor_card_count", 0) > 0:
            parts.append(f"🎫 双倍好感卡 × {result['double_favor_card_count']}")
        if parts:
            main += '<div class="sub-info">' + "　".join(parts) + "</div>"

    return main


# ============================================================
# 签到主逻辑
# ============================================================
@checkin_cmd.handle()
async def _checkin_handle(bot, event):
    user_id = str(event.get_user_id())

    # 1) 更新签到数据、计算奖励
    try:
        result = do_checkin(
            user_id,
            coins_min=COINS_MIN,
            coins_max=COINS_MAX,
            favor_min=FAVOR_MIN,
            favor_max=FAVOR_MAX,
            double_favor_prob=DOUBLE_FAVOR_PROB,
        )
    except Exception as e:
        logger.exception(f"[签到] 用户数据处理失败：{e}")
        return

    # 2) 随机语录
    quote_text, quote_source = random_quote()

    # 3) 生成图片卡片（不发送文字）
    try:
        bg_uri = _load_bg_data_uri()
        title_icon = _load_title_icon()
        reward_html = _build_reward_html(result)

        html = render_template(
            "checkin",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            title_icon=title_icon,
            date_cn=_date_cn(result["date"]),
            nickname=_nickname(event),
            user_id=user_id,
            uid=result.get("uid", ""),
            total_days=result["total_days"],
            coins=result["coins"],
            favor=_fmt_favor(result["favor"]),
            reward_html=reward_html,
            today_first_time=result["today_first_time"],
            quote=quote_text,
            quote_source=quote_source,
        )

        img_path = await screenshot_html(html, width=CARD_WIDTH, height=CARD_HEIGHT)
        await checkin_cmd.finish(MessageSegment.image(to_image_uri(str(img_path))))
    except FinishedException:
        # finish() 会抛这个异常结束处理，属于正常流程，不是错误
        pass
    except Exception as e:
        logger.exception(f"[签到] 生成图片失败：{e}")
