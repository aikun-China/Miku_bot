"""
Miku B站插件 - 凭证管理模块
管理B站登录凭证（Cookie）

统一凭证存储（数据桥接）：
  1. 兼容旧的本地 Cookie 文件（data/miku_bilibili/bili_cookies.json）
  2. 扫码登录成功后，将 SESSDATA / bili_jct / uid 同步写入 config/bot.yaml
     的 `bilibili_credential` 模块，供「查成分(ddcheck)」等插件动态读取，
     实现一次扫码、永久复用凭证。
  3. 读取凭证时优先使用 bot.yaml 中的 bilibili_credential（权威来源）。
"""

import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from curl_cffi import requests as curl_requests
from nonebot.log import logger

import yaml

from .config import COOKIE_FILE, get_request_timeout

# ── 统一凭证存储位置：config/bot.yaml 的 bilibili_credential 模块 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_YAML = PROJECT_ROOT / "config" / "bot.yaml"
CONFIG_CREDENTIAL_KEY = "bilibili_credential"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


def _read_yaml_credential() -> dict:
    """从 config/bot.yaml 读取 bilibili_credential 模块。"""
    try:
        if not CONFIG_YAML.exists():
            return {}
        data = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
        cred = data.get(CONFIG_CREDENTIAL_KEY) or {}
        return cred if isinstance(cred, dict) else {}
    except Exception as e:
        logger.warning(f"[miku_bilibili] 读取 bot.yaml 凭证失败: {e}")
        return {}


def _update_yaml_credential(cred: dict) -> None:
    """只更新 config/bot.yaml 的 bilibili_credential 模块，保留其余内容不变。

    以纯文本方式定位顶层 `bilibili_credential:` 区块并整体替换，
    避免 yaml.safe_dump 重排其它插件区内容。
    """
    try:
        CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
        lines = CONFIG_YAML.read_text(encoding="utf-8").splitlines() if CONFIG_YAML.exists() else []

        out = []
        skipping = False
        for ln in lines:
            stripped = ln.strip()
            if stripped == CONFIG_CREDENTIAL_KEY + ":" or stripped.startswith(CONFIG_CREDENTIAL_KEY + ":"):
                skipping = True
                continue
            if skipping:
                # 遇到下一个顶层键（非缩进、含冒号）则结束跳过
                if ln and not ln[0].isspace() and ":" in stripped:
                    skipping = False
                    out.append(ln)
                continue
            out.append(ln)

        # 构造新的 bilibili_credential 区块
        block = [CONFIG_CREDENTIAL_KEY + ":"]
        for k, v in cred.items():
            if v is None:
                v = ""
            # 简单标量处理（凭证均为字符串/数字），特殊字符加引号
            s = str(v)
            if any(c in s for c in ":#{}\"'\n") or s != s.strip():
                s = json.dumps(s, ensure_ascii=False)
            block.append(f"  {k}: {s}")

        if out and out[-1].strip():
            out.append("")
        text = "\n".join(out + [""] + block).rstrip() + "\n"
        CONFIG_YAML.write_text(text, encoding="utf-8")
        logger.info(f"[miku_bilibili] 凭证已同步到 {CONFIG_YAML.name} 的 {CONFIG_CREDENTIAL_KEY}")
    except Exception as e:
        logger.warning(f"[miku_bilibili] 更新 bot.yaml 凭证失败: {e}")


def get_credential() -> dict:
    """获取统一凭证 dict（优先 bot.yaml 的 bilibili_credential，回退本地 Cookie 文件）。"""
    cred = _read_yaml_credential()
    if cred:
        return cred
    # 回退：旧的本地 Cookie 文件
    return load_cookies()


def _extract_cred_fields(cookies: Dict[str, str]) -> dict:
    """从 Cookie 字典中提取统一凭证字段（SESSDATA / bili_jct / uid 等）。"""
    cred = {}
    for k in ("SESSDATA", "sessdata", "bili_jct", "DedeUserID", "buvid3", "b_nut", "buvid4"):
        if k in cookies and cookies.get(k):
            cred[k] = str(cookies[k])
    # uid 归一化：优先显式 uid，其次 DedeUserID
    uid = cred.pop("uid", None) or cred.pop("DedeUserID", None)
    if uid:
        cred["uid"] = str(uid)
    return cred


def load_cookies() -> Dict[str, str]:
    """加载 Cookie。优先读 bot.yaml 的 bilibili_credential，回退本地 Cookie 文件。"""
    cred = _read_yaml_credential()
    if cred:
        cookies = {}
        # 将凭证还原成 Cookie 字典（SESSDATA 大小写兼容）
        for k, v in cred.items():
            if k == "uid":
                cookies["DedeUserID"] = str(v)
            elif k in ("SESSDATA", "sessdata"):
                cookies.setdefault("SESSDATA", str(v))
            elif k == "bili_jct":
                cookies["bili_jct"] = str(v)
            else:
                cookies[k] = str(v)
        if cookies.get("SESSDATA"):
            return cookies
    # 回退：本地 Cookie 文件
    if not COOKIE_FILE.exists():
        return {}
    try:
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        if isinstance(cookies, dict) and cookies:
            return cookies
    except Exception as e:
        logger.warning(f"[miku_bilibili] 读取Cookie文件失败: {e}")
    return {}


def save_cookies(cookies: Dict[str, str]) -> None:
    """保存Cookie。同时写入本地文件与 bot.yaml 的 bilibili_credential。"""
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("[miku_bilibili] Cookie已保存到本地文件")
    except Exception as e:
        logger.warning(f"[miku_bilibili] 保存Cookie文件失败: {e}")

    # 同步统一凭证到 bot.yaml（数据桥接，供 ddcheck 等读取）
    cred = _extract_cred_fields(cookies)
    if cred:
        _update_yaml_credential(cred)


def clear_cookies() -> None:
    """清除Cookie：同时清理本地文件与 bot.yaml 的 bilibili_credential。"""
    try:
        if COOKIE_FILE.exists():
            COOKIE_FILE.unlink()
            logger.info("[miku_bilibili] Cookie文件已清除")
    except Exception as e:
        logger.warning(f"[miku_bilibili] 清除Cookie文件失败: {e}")

    try:
        _update_yaml_credential({})
    except Exception as e:
        logger.warning(f"[miku_bilibili] 清除 bot.yaml 凭证失败: {e}")


def is_logged_in() -> bool:
    """检查是否已登录（有SESSDATA）"""
    cookies = load_cookies()
    return bool(cookies.get("SESSDATA") or cookies.get("sessdata"))


def get_cookies_dict() -> Dict[str, str]:
    """获取Cookie字典"""
    return load_cookies()


def get_cookies_str() -> str:
    """获取Cookie字符串（用于请求头）"""
    cookies = load_cookies()
    if not cookies:
        return ""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def get_headers_with_cookie() -> Dict[str, str]:
    """获取带Cookie的请求头"""
    headers = dict(_HEADERS)
    cookie_str = get_cookies_str()
    if cookie_str:
        headers["Cookie"] = cookie_str
    return headers


async def check_login_status() -> tuple[bool, str]:
    """检查登录状态，返回 (is_logged_in, username)。凭证失效时自动清除。"""
    try:
        timeout = get_request_timeout()
        async with curl_requests.AsyncSession(
            impersonate="chrome131", timeout=timeout, headers=get_headers_with_cookie()
        ) as client:
            r = await client.get("https://api.bilibili.com/x/web-interface/nav")
            data = r.json()
            if data.get("code") == 0:
                is_login = data.get("data", {}).get("isLogin", False)
                uname = data.get("data", {}).get("uname", "")
                if not is_login:
                    clear_cookies()
                return is_login, uname
            if data.get("code") == -101:
                clear_cookies()
                logger.warning("[miku_bilibili] 凭证未登录(-101)，已清除")
            return False, ""
    except Exception as e:
        logger.warning(f"[miku_bilibili] 检查登录状态失败: {e}")
        return False, ""


def get_wbi_keys() -> tuple[str, str]:
    """获取WBI签名密钥（同步版本，带缓存）"""
    cache_file = COOKIE_FILE.parent / "wbi_keys.json"
    import time as _time

    try:
        if cache_file.exists():
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if _time.time() - cache_data.get("update_time", 0) < 3600:
                return cache_data.get("img_key", ""), cache_data.get("sub_key", "")
    except Exception:
        pass

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=get_headers_with_cookie()
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                wbi_img = data["data"]["wbi_img"]
                img_url = wbi_img["img_url"]
                sub_url = wbi_img["sub_url"]
                img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
                sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
                try:
                    cache_file.write_text(json.dumps({
                        "img_key": img_key,
                        "sub_key": sub_key,
                        "update_time": _time.time()
                    }, ensure_ascii=False))
                except Exception:
                    pass
                return img_key, sub_key
    except Exception as e:
        logger.warning(f"[miku_bilibili] 获取WBI密钥失败: {e}")
    return "", ""


_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 20, 56, 34, 22, 11,
    25, 4, 44, 21, 54, 23, 36, 62, 51, 1, 6, 57, 60, 30, 59
]


def _get_mixin_key(img_key: str, sub_key: str) -> str:
    raw_key = img_key + sub_key
    return "".join(raw_key[i] for i in _MIXIN_KEY_ENC_TAB[:32])


def sign_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """WBI签名"""
    import hashlib
    import time
    mixin_key = _get_mixin_key(img_key, sub_key)
    params = params.copy()
    params["wts"] = int(time.time())
    params = {k: "".join(filter(lambda c: c not in "!'()*", str(v))) for k, v in params.items()}
    sorted_params = sorted(params.items())
    query = "&".join(f"{k}={v}" for k, v in sorted_params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


async def generate_qrcode() -> Tuple[str, str]:
    """
    生成B站登录二维码
    返回: (二维码URL, qrcode_key)
    """
    try:
        timeout = get_request_timeout()
        async with curl_requests.AsyncSession(
            impersonate="chrome131", timeout=timeout, headers=dict(_HEADERS), allow_redirects=True
        ) as client:
            r = await client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            )
            data = r.json()
            if data.get("code") == 0:
                url = data["data"]["url"]
                qrcode_key = data["data"]["qrcode_key"]
                logger.info(f"[miku_bilibili] 二维码生成成功: {url[:60]}...")
                return url, qrcode_key
            else:
                logger.warning(f"[miku_bilibili] 二维码生成失败: {data}")
    except Exception as e:
        logger.warning(f"[miku_bilibili] 生成二维码异常: {e}")
    return "", ""


async def poll_qrcode_status(qrcode_key: str) -> Tuple[int, str, Dict[str, str]]:
    """
    轮询二维码登录状态
    返回: (状态码, 消息, cookies字典)
    状态码: 0=未扫码, 1=已扫码未确认, 2=登录成功, 3=已过期, 4=已取消
    """
    cookies_dict: Dict[str, str] = {}
    try:
        timeout = get_request_timeout()
        async with curl_requests.AsyncSession(
            impersonate="chrome131",
            timeout=timeout,
            headers=dict(_HEADERS),
            allow_redirects=True,
        ) as client:
            r = await client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": qrcode_key},
            )
            data = r.json()
            code = data.get("code", -1)
            message = data.get("message", "")
            
            if code != 0:
                logger.warning(f"[miku_bilibili] 轮询状态错误 code={code}: {message}")
                return -1, message, cookies_dict
            
            data_obj = data.get("data", {})
            status_code = data_obj.get("code", -1)
            status_msg = data_obj.get("message", "")
            
            if status_code == 0:
                raw_cookies = data_obj.get("raw_cookies", None)
                if raw_cookies and isinstance(raw_cookies, dict):
                    for k, v in raw_cookies.items():
                        if v is not None:
                            cookies_dict[k] = str(v)
                
                url = data_obj.get("url", "")
                if url and not cookies_dict:
                    try:
                        r2 = await client.get(url)
                        for c in r2.cookies:
                            cookies_dict[c.name] = c.value
                    except Exception as e:
                        logger.debug(f"[miku_bilibili] 通过URL获取Cookie失败: {e}")
                
                if not cookies_dict and r.cookies:
                    for c in r.cookies:
                        cookies_dict[c.name] = c.value
                
                logger.info(f"[miku_bilibili] 登录成功! 获得 {len(cookies_dict)} 个Cookie")
                return 2, status_msg, cookies_dict
            elif status_code == 86101:
                return 0, status_msg, cookies_dict
            elif status_code == 86090:
                return 1, status_msg, cookies_dict
            elif status_code == 86038:
                return 3, status_msg, cookies_dict
            elif status_code == 86091:
                return 4, status_msg, cookies_dict
            else:
                logger.debug(f"[miku_bilibili] 未知状态码: {status_code}, {status_msg}")
                return status_code, status_msg, cookies_dict
    except Exception as e:
        logger.warning(f"[miku_bilibili] 轮询二维码状态异常: {e}")
        return -1, str(e), cookies_dict


def cookies_str_to_dict(cookies_str: str) -> Dict[str, str]:
    """将Cookie字符串转换为字典"""
    cookies = {}
    if not cookies_str:
        return cookies
    try:
        items = cookies_str.split(";")
        for item in items:
            if "=" not in item:
                continue
            item = item.strip()
            key, value = item.split("=", 1)
            cookies[key.strip()] = value.strip()
    except Exception:
        pass
    return cookies
