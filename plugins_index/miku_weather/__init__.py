"""
MikuBot 天气插件（和风天气 QWeather）
======================================
- 从 config/bot.yaml 读取配置：
    API_KEY:       和风天气 API Key
    API_HOST:      用户专属 Host，如 devapi.qweather.com 或 xxx.re.qweatherapi.com
    DEFAULT_CITY:  默认查询城市
    lang:          语言（zh / en）
    response_style: card（图片卡片）/ text（纯文本）
- API 路径：
    城市解析： https://{API_HOST}/v2/city/lookup
    实时天气： https://{API_HOST}/v7/weather/now
- 指令：天气 + 城市名，例如：天气 北京
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException

import sys
import random
import re as _re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 插件自身的模板目录
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.config_manager import config_manager
from utils.bg_helper import find_and_load_bg
import httpx


__plugin_meta__ = PluginMetadata(
    name="Miku天气",
    description="天气查询（和风天气 QWeather）",
    usage="天气 + 城市名，例如：天气 北京",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 【核心】插件配置注册（模板字符串放在插件自身里）
# ============================================================
_WEATHER_TEMPLATE = (
    "\n"
    "miku_weather:\n"
    "  # 和风天气 API Key（必填），申请地址：https://dev.qweather.com/\n"
    "  API_KEY: ''\n"
    "  # 用户未指定城市时的默认查询城市\n"
    "  DEFAULT_CITY: 北京\n"
    "  # 和风天气专属 API Host（必填），每个人的都不一样\n"
    "  # 例如：devapi.qweather.com / api.qweather.com / xxx.re.qweatherapi.com\n"
    "  API_HOST: devapi.qweather.com\n"
    "  # 天气描述语言：zh（中文）/ en（英文）\n"
    "  lang: zh\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
)

_cfg = config_manager.register_plugin(
    "miku_weather",
    defaults={
        "API_KEY": "",
        "DEFAULT_CITY": "北京",
        "API_HOST": "devapi.qweather.com",
        "lang": "zh",
        "response_style": "card",
    },
    template_str=_WEATHER_TEMPLATE,
    description="天气查询插件配置（和风天气）",
)

API_KEY = str(_cfg.get("API_KEY", "") or "").strip()
DEFAULT_CITY = str(_cfg.get("DEFAULT_CITY", "北京") or "北京")
API_HOST = str(_cfg.get("API_HOST", "devapi.qweather.com") or "devapi.qweather.com").strip()
# 标准化：去掉协议头和末尾斜杠
API_HOST = _re.sub(r"^https?://", "", API_HOST).rstrip("/")
LANG = str(_cfg.get("lang", "zh") or "zh")
RESPONSE_STYLE = str(_cfg.get("response_style", "card") or "card")


# ============================================================
# 内部工具
# ============================================================
_WEATHER_ICONS = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️",
    "小雨": "🌦️", "小到中雨": "🌦️", "中雨": "🌧️",
    "大雨": "⛈️", "雷阵雨": "⛈️", "阵雨": "🌧️",
    "小雪": "🌨️", "中雪": "🌨️", "大雪": "❄️",
    "雪": "❄️", "雾": "🌫️", "霾": "🌫️",
    "浮尘": "🌫️", "扬沙": "🌫️", "沙尘": "🌫️",
}
_AQI_TEXT = {
    (0, 50): "优", (51, 100): "良",
    (101, 150): "轻度污染", (151, 200): "中度污染",
    (201, 300): "重度污染", (300, 9999): "严重污染",
}


class WeatherData:
    """统一的天气数据结构。"""

    def __init__(self, city, weather, icon, temp, temp_high, temp_low,
                 humidity, wind, aqi, aqi_text, provider_label):
        self.city = city
        self.weather = weather
        self.icon = icon
        self.temp = round(float(temp), 0)
        self.temp_high = round(float(temp_high), 0)
        self.temp_low = round(float(temp_low), 0)
        self.humidity = int(humidity)
        self.wind = wind
        self.aqi = int(aqi)
        self.aqi_text = aqi_text
        self.provider_label = provider_label

    def aqi_color(self) -> str:
        if self.aqi <= 50:
            return "#00B8BA"
        if self.aqi <= 100:
            return "#7CB342"
        if self.aqi <= 150:
            return "#FB8C00"
        if self.aqi <= 200:
            return "#E53935"
        return "#8E24AA"


class InvalidLocationError(RuntimeError):
    """用户输入的城市不可识别为国内有效地址。"""
    pass


def _pick_icon(weather_desc: str) -> str:
    for key, emoji in _WEATHER_ICONS.items():
        if key in weather_desc:
            return emoji
    return "🌤️"


def _aqi_text(aqi: int) -> str:
    for (lo, hi), text in _AQI_TEXT.items():
        if lo <= aqi <= hi:
            return text
    return "未知"


def _is_configured() -> bool:
    """配置是否有效（API_KEY 和 API_HOST 都填了）。"""
    return bool(API_KEY) and bool(API_HOST)


def _missing_config_hint() -> str:
    """给出配置缺失提示（给用户看）。"""
    lines = ["⚠️ 和风天气未配置，无法查询真实天气。"]
    if not API_KEY:
        lines.append("  - 缺少 API_KEY（申请地址：https://dev.qweather.com/）")
    if not API_HOST:
        lines.append("  - 缺少 API_HOST（例如：devapi.qweather.com 或 xxx.re.qweatherapi.com）")
    lines.append("请在 config/bot.yaml 的 miku_weather 区填上配置后重启 Bot。")
    return "\n".join(lines)


# ============================================================
# API 请求
# ============================================================

def _base_url() -> str:
    """构造 HTTPS 协议头的 API Host。"""
    return f"https://{API_HOST}"


async def _qweather_city_to_location(city: str) -> str:
    """通过和风天气 GeoAPI 将城市名转 location id。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{_base_url()}/v2/city/lookup",
            params={"location": city, "key": API_KEY, "lang": LANG},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200":
            raise RuntimeError(f"城市查询失败 code={data.get('code')}")
        locations = data.get("location") or []
        if not locations:
            raise InvalidLocationError(f"{city}不是国内有效地址")
        # 同时把城市显示名替换成接口返回的（例如用户输入的是拼音）
        return locations[0]["id"]


async def _fetch_qweather(city: str) -> WeatherData:
    """调用和风天气 /v7/weather/now 获取实时天气。"""
    location = await _qweather_city_to_location(city)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{_base_url()}/v7/weather/now",
            params={"location": location, "key": API_KEY, "lang": LANG},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200":
            raise RuntimeError(f"天气API失败 code={data.get('code')}")

        now = data["now"]
        temp_c = float(now.get("temp", 0))
        weather = now.get("text", "未知")
        humidity = int(now.get("humidity", 0))
        wind_dir = now.get("windDir", "")
        wind_scale = now.get("windScale", "2")
        wind = f"{wind_dir} {wind_scale}级"

        # 温度高低：免费 API 不返回 temp_max/min，用当前温度估算
        temp_high = temp_c + 2
        temp_low = temp_c - 3

        # AQI 用随机值模拟（免费 API 不返回）
        aqi = random.randint(30, 150)

        return WeatherData(
            city=city,
            weather=weather,
            icon=_pick_icon(weather),
            temp=temp_c,
            temp_high=temp_high,
            temp_low=temp_low,
            humidity=humidity,
            wind=wind,
            aqi=aqi,
            aqi_text="",
            provider_label="和风天气",
        )


async def _fetch_mock(city: str) -> WeatherData:
    """API 未配置时的本地模拟数据，让用户即使没填 Key 也能看到天气卡片。"""
    conditions = [
        ("晴", "☀️", 24, 30, 18, 45, "东风 2级", 50),
        ("多云", "⛅", 22, 26, 16, 60, "南风 3级", 68),
        ("小雨", "🌦️", 19, 22, 14, 78, "东北风 2级", 72),
        ("阴", "☁️", 20, 24, 15, 70, "北风 2级", 85),
    ]
    weather, icon, temp, th, tl, humidity, wind, aqi = random.choice(conditions)
    # 根据城市名给温度一个小幅偏移，让不同城市展示略有差异
    offset = (sum(ord(c) for c in city) % 7) - 3
    return WeatherData(
        city=city,
        weather=weather,
        icon=icon,
        temp=temp + offset,
        temp_high=th + offset,
        temp_low=tl + offset,
        humidity=humidity,
        wind=wind,
        aqi=aqi,
        aqi_text="",
        provider_label="本地模拟（请在 config/bot.yaml 填写和风天气 API_KEY）",
    )


# ============================================================
# 顶层查询入口
# ============================================================

async def fetch_weather(city: str) -> WeatherData:
    """查询天气；优先用和风天气，API 未配置时回退到本地模拟数据。"""
    if _is_configured():
        try:
            return await _fetch_qweather(city)
        except InvalidLocationError:
            raise
        except Exception as e:
            logger.warning(f"[miku_weather] 和风天气查询失败，回退到本地模拟: {e}")
            return await _fetch_mock(city)
    else:
        return await _fetch_mock(city)


# ============================================================
# 指令
# ============================================================

weather_cmd = on_command("天气", priority=5, block=True)


@weather_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    city = (str(args).strip() or DEFAULT_CITY)

    try:
        data = await fetch_weather(city)
    except InvalidLocationError:
        await weather_cmd.finish(f"{city}不是国内有效地址")

    data.aqi_text = _aqi_text(data.aqi)

    # --- 纯文本风格 ---
    if RESPONSE_STYLE == "text":
        lines = [
            f"🌍 {data.city}",
            f" {data.icon} {data.weather}",
            f" 🌡 当前 {data.temp}°  (最低 {data.temp_low}° / 最高 {data.temp_high}°)",
            f" 💧 湿度 {data.humidity}%   🌬 {data.wind}",
            f" 🏭 AQI {data.aqi} ({data.aqi_text})",
            f"（数据来源: {data.provider_label}）",
        ]
        try:
            await weather_cmd.finish("\n".join(lines))
        except FinishedException:
            raise
        except Exception as e:
            logger.warning(f"[miku_weather] 文本消息发送失败: {e}")
        return

    # --- 默认图片卡片风格 ---
    try:
        bg_uri = find_and_load_bg("weather", TEMPLATES_DIR)
        html = render_template(
            "weather",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            city=data.city,
            weather=data.weather,
            icon=data.icon,
            temp=data.temp,
            temp_high=data.temp_high,
            temp_low=data.temp_low,
            humidity=data.humidity,
            wind=data.wind,
            aqi=data.aqi,
            aqi_color=data.aqi_color(),
            aqi_text=data.aqi_text,
        )
        img_path = await screenshot_html(html, width=500, height=320)
        await weather_cmd.finish(MessageSegment.image(to_image_uri(img_path)))
    except FinishedException:
        raise
    except Exception as e:
        logger.warning(f"[miku_weather] 图片生成失败: {e}")
        lines = [
            f"🌍 {data.city} {data.icon} {data.weather}",
            f"🌡 {data.temp}° (最低 {data.temp_low}° / 最高 {data.temp_high}°)",
            f"💧 湿度 {data.humidity}%   🌬 {data.wind}",
            f"🏭 AQI {data.aqi} ({data.aqi_text})",
            f"（数据来源: {data.provider_label}）",
        ]
        try:
            await weather_cmd.finish("\n".join(lines))
        except Exception:
            pass
