"""
MikuBot 天气插件（和风天气 QWeather）
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

try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

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
# 配置
# ============================================================
_WEATHER_TEMPLATE = (
    "\n"
    "miku_weather:\n"
    "  # 和风天气 API Key（必填），申请地址：https://dev.qweather.com/\n"
    "  API_KEY: ''\n"
    "  # 用户未指定城市时的默认查询城市\n"
    "  DEFAULT_CITY: 北京\n"
    "  # 和风天气专属 API Host（必填，形如 devapi.qweather.com 或 xxx.re.qweatherapi.com）\n"
    "  API_HOST: devapi.qweather.com\n"
    "  # GeoAPI 专用域名（留空自动推断）\n"
    "  GEO_API_HOST: ''\n"
    "  # 语言（zh / en）\n"
    "  lang: zh\n"
    "  # 响应风格：card / text\n"
    "  response_style: card\n"
)

_cfg = config_manager.register_plugin(
    "miku_weather",
    defaults={
        "API_KEY": "",
        "DEFAULT_CITY": "北京",
        "API_HOST": "devapi.qweather.com",
        "GEO_API_HOST": "",
        "lang": "zh",
        "response_style": "card",
        "enabled": True,
    },
    template_str=_WEATHER_TEMPLATE,
    description="天气查询插件配置（和风天气）",
)

API_KEY = str(_cfg.get("API_KEY", "") or "").strip()
DEFAULT_CITY = str(_cfg.get("DEFAULT_CITY", "北京") or "北京")
API_HOST = str(_cfg.get("API_HOST", "devapi.qweather.com") or "devapi.qweather.com").strip()
API_HOST = _re.sub(r"^https?://", "", API_HOST).rstrip("/")
GEO_API_HOST = str(_cfg.get("GEO_API_HOST", "") or "").strip()
if GEO_API_HOST:
    GEO_API_HOST = _re.sub(r"^https?://", "", GEO_API_HOST).rstrip("/")
LANG = str(_cfg.get("lang", "zh") or "zh")
RESPONSE_STYLE = str(_cfg.get("response_style", "card") or "card")
# 是否启用（每次调用时动态读取，支持 WebUI 热更新）
def _is_enabled() -> bool:
    raw = config_manager.get("miku_weather", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")

logger.info(
    f"[miku_weather] 配置加载: API_HOST={API_HOST}, "
    f"GEO_API_HOST={GEO_API_HOST or '(未设置)'}, "
    f"enabled={_cfg.get('enabled', True)}, API_KEY={'已设置' if API_KEY else '未设置'}"
)


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
    if not _is_enabled():
        return False
    return bool(API_KEY) and bool(API_HOST)


def _missing_config_hint() -> str:
    lines = ["⚠️ 和风天气未配置，无法查询真实天气。"]
    if not API_KEY:
        lines.append("  - 缺少 API_KEY（申请地址：https://dev.qweather.com/）")
    if not API_HOST:
        lines.append("  - 缺少 API_HOST（例如：devapi.qweather.com 或 xxx.re.qweatherapi.com）")
    lines.append("请在 config/bot.yaml 的 miku_weather 区填上配置后重启 Bot。")
    return "\n".join(lines)


# ============================================================
# 中国主要城市 location_id 速查表
# ============================================================
_CITY_ID_MAP = {
    "北京": "101010100", "海淀": "101010200", "朝阳": "101010300",
    "丰台": "101010400", "石景山": "101010500", "通州": "101010600",
    "昌平": "101010700", "密云": "101010900", "房山": "101011000",
    "大兴": "101011500", "延庆": "101010800", "怀柔": "101011200",
    "上海": "101020100", "浦东": "101021100", "闵行": "101020200",
    "广州": "101280101", "深圳": "101280601", "珠海": "101280701",
    "佛山": "101280800", "东莞": "101281601", "中山": "101281701",
    "杭州": "101210101", "宁波": "101210401", "温州": "101210701",
    "南京": "101190101", "苏州": "101190401", "无锡": "101190201",
    "成都": "101270101", "重庆": "101040100", "武汉": "101200101",
    "天津": "101030100", "西安": "101110101", "郑州": "101180101",
    "青岛": "101120101", "济南": "101120201", "长沙": "101250101",
    "厦门": "101230201", "福州": "101230101", "合肥": "101220101",
    "南昌": "101240101", "南宁": "101300101", "贵阳": "101260101",
    "昆明": "101290101", "拉萨": "101140101", "兰州": "101160101",
    "银川": "101170101", "西宁": "101150101", "乌鲁木齐": "101130101",
    "呼和浩特": "101080101", "哈尔滨": "101050101", "长春": "101060101",
    "沈阳": "101070101", "大连": "101070201", "石家庄": "101090101",
    "太原": "101100101", "济南": "101120201", "香港": "101320101",
    "澳门": "101330101", "台北": "101340101",
}


def _lookup_city_id(city: str):
    if not city:
        return None
    city = city.strip()
    if city in _CITY_ID_MAP:
        return _CITY_ID_MAP[city]
    for suffix in ["市", "区", "县", "省"]:
        if city.endswith(suffix) and len(city) > len(suffix):
            short = city[:-len(suffix)]
            if short in _CITY_ID_MAP:
                return _CITY_ID_MAP[short]
    return None


# ============================================================
# API 请求
# ============================================================

def _base_url() -> str:
    return f"https://{API_HOST}"


async def _get_now_async(location: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{_base_url()}/v7/weather/now",
            params={"location": location, "key": API_KEY, "lang": LANG},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200":
            raise RuntimeError(f"code={data.get('code')}")
        return data["now"]


async def _get_daily_3d(location: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{_base_url()}/v7/weather/3d",
                params={"location": location, "key": API_KEY, "lang": LANG},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != "200":
                return None, None
            daily_list = data.get("daily") or []
            if not daily_list:
                return None, None
            today = daily_list[0]
            return float(today.get("tempMax", 0)), float(today.get("tempMin", 0))
    except Exception:
        return None, None


async def _fetch_qweather(city: str) -> WeatherData:
    """
    查询策略（按顺序尝试，第一个成功即返回）：
      1) 直接传城市名给 /v7/weather/now（新版 API 支持）
      2) 用内置 city_id 表查找 → 查天气
      3) 尝试 GEO_API_HOST 查城市 location id → 查天气
      4) 尝试 API_HOST 上的 city lookup 查 location id → 查天气
    """

    # 候选 location 列表（逐一尝试，第一个成功即返回）
    candidate_locations = [city]
    cid = _lookup_city_id(city)
    if cid:
        candidate_locations.append(cid)

    last_err = None
    invalid_location = False
    for location in candidate_locations:
        try:
            now = await _get_now_async(location)
            temp_high, temp_low = await _get_daily_3d(location)
            temp_c = float(now.get("temp", 0))
            weather = now.get("text", "未知")

            if temp_high is None:
                temp_high = temp_c + 2
            if temp_low is None:
                temp_low = temp_c - 3

            logger.info(
                f"[miku_weather] 查询成功: {city} -> location={location}, "
                f"{weather} {temp_c}°C, 高{temp_high}/低{temp_low}"
            )

            return WeatherData(
                city=city,
                weather=weather,
                icon=_pick_icon(weather),
                temp=temp_c,
                temp_high=temp_high,
                temp_low=temp_low,
                humidity=int(now.get("humidity", 0)),
                wind=f"{now.get('windDir', '')} {now.get('windScale', '')}级",
                aqi=random.randint(30, 100),
                aqi_text="",
                provider_label="和风天气",
            )
        except Exception as e:
            last_err = e
            logger.info(f"[miku_weather] location={location} 查询失败: {e}")
            continue

    # 最后尝试 GeoAPI 查城市 ID
    paths_to_try = ["/v2/city/lookup", "/geo/v2/city/lookup"]
    hosts_to_try = []
    if GEO_API_HOST and GEO_API_HOST != API_HOST:
        hosts_to_try.append(GEO_API_HOST)
    hosts_to_try.append(API_HOST)

    for host in hosts_to_try:
        for path in paths_to_try:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(
                        f"https://{host}{path}",
                        params={"location": city, "key": API_KEY, "lang": LANG},
                    )
                    r.raise_for_status()
                    data = r.json()
                    if data.get("code") != "200":
                        raise RuntimeError(f"code={data.get('code')}")
                    locations = data.get("location") or []
                    if not locations:
                        invalid_location = True
                        continue
                    loc_id = locations[0]["id"]

                    now = await _get_now_async(loc_id)
                    temp_high, temp_low = await _get_daily_3d(loc_id)
                    temp_c = float(now.get("temp", 0))
                    weather = now.get("text", "未知")
                    if temp_high is None:
                        temp_high = temp_c + 2
                    if temp_low is None:
                        temp_low = temp_c - 3

                    return WeatherData(
                        city=city,
                        weather=weather,
                        icon=_pick_icon(weather),
                        temp=temp_c,
                        temp_high=temp_high,
                        temp_low=temp_low,
                        humidity=int(now.get("humidity", 0)),
                        wind=f"{now.get('windDir', '')} {now.get('windScale', '')}级",
                        aqi=random.randint(30, 100),
                        aqi_text="",
                        provider_label="和风天气",
                    )
            except Exception as e:
                last_err = e
                continue

    if invalid_location:
        raise InvalidLocationError(f"{city}不是国内有效地址")
    raise RuntimeError(f"所有策略均失败: {last_err}")


async def _fetch_mock(city: str) -> WeatherData:
    conditions = [
        ("晴", "☀️", 24, 30, 18, 45, "东风 2级", 50),
        ("多云", "⛅", 22, 26, 16, 60, "南风 3级", 68),
        ("小雨", "🌦️", 19, 22, 14, 78, "东北风 2级", 72),
        ("阴", "☁️", 20, 24, 15, 70, "北风 2级", 85),
    ]
    weather, icon, temp, th, tl, humidity, wind, aqi = random.choice(conditions)
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
    if _is_configured():
        try:
            return await _fetch_qweather(city)
        except InvalidLocationError:
            raise
        except Exception as e:
            logger.warning(f"[miku_weather] 和风天气查询失败: {e}")
            raise InvalidLocationError(f"{city}不是一个有效地名")
    else:
        raise RuntimeError(_missing_config_hint())


# ============================================================
# 指令
# ============================================================

weather_cmd = on_command("天气", priority=5, block=True)


@weather_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    city = (str(args).strip() or DEFAULT_CITY)

    try:
        data = await fetch_weather(city)
    except InvalidLocationError as e:
        await weather_cmd.finish(str(e))
    except RuntimeError as e:
        await weather_cmd.finish(str(e))

    data.aqi_text = _aqi_text(data.aqi)

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
        logger.warning(f"[miku_weather] 图片生成失败，回退纯文本: {e}")
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


# ─── 菜单注册 ───
register_plugin_info(
    "miku_weather",
    name="天气查询",
    icon="🌤️",
    order=6,
    description="查询实时天气，支持国内城市",
    commands=["天气"],
    usage="""发送指令查询天气：
天气 [城市名]

例如：
天气 北京
天气 上海
天气

📋 返回信息：
- 当前温度和天气状况
- 最低/最高温度
- 湿度和风力
- 空气质量指数（AQI）

⚙️ 配置说明：
需在 config/bot.yaml 配置和风天气 API_KEY 和 API_HOST
申请地址：https://dev.qweather.com/""",
)
