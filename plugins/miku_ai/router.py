"""
AI 插件路由层
==============
判断用户这句话应该交给哪个插件动作执行，而不是让 AI 直接聊天。

链路（从便宜到贵）：
1. **未完成的追问**：上一条消息缺参数，用户补一句歌名 → 直接执行；
2. **规则匹配**：动作自带的正则/关键词，命中即执行（零延迟、零成本）；
3. **AI 判定**：把已注册动作清单丢给小模型，只让它输出 JSON；
4. 都不命中 → 返回 ``none``，交给普通聊天。

安全约定：
- 只认识 ``utils.ai_actions`` 里显式注册过的动作；
- 参数按定义清洗（未声明的丢弃、超长截断、枚举校验）；
- 任何异常都不向用户抛裸错误，失败即降级为普通聊天或友好提示；
- handler 一律通过 ``ctx.emit`` 发送消息，绝不调用 matcher.finish()。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nonebot.log import logger

from utils.ai_actions import (
    ActionContext,
    ActionSpec,
    STATUS_ASK,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIP,
    STATUS_TOOL,
    build_ask_text,
    format_catalog,
    get_ai_action,
    get_ai_actions,
    match_rules,
    needs_routing,
    strip_prefix,
    validate_args,
)

from .config import get_config
from .data_source import _call_ai_api, _strip_think_blocks

# ── 待补充参数的挂起动作：(scope_key) -> {...} ──
_PENDING: Dict[str, Dict[str, Any]] = {}

# ── 待用户确认的动作（花钱/破坏性动作）：(scope_key) -> {...} ──
_CONFIRM: Dict[str, Dict[str, Any]] = {}

_YES_WORDS = {"确认", "确定", "是", "是的", "好", "好的", "嗯", "可以", "执行", "继续", "买吧", "要", "行"}
_NO_WORDS = {"取消", "不要", "不用", "算了", "不", "别", "否", "算了吧"}

# ── AI 判定缓存：(scope_key, text_hash) -> (expire_ts, decision) ──
_DECISION_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_DECISION_TTL = 300.0


@dataclass
class RouteResult:
    """路由结果。

    kind:
        none  什么都没命中，走普通聊天
        done  动作已执行完毕（消息已发出），不要再聊天
        tool  动作返回了事实数据，交给 AI 组织成自然回复
        ask   缺参数，已生成追问，本条消息到此结束
    """

    kind: str = "none"
    action: str = ""
    text: str = ""
    reply: str = ""


# ============================================================
# 配置与动作筛选
# ============================================================

def _router_enabled() -> bool:
    return bool(get_config("plugin_router_enabled", True))


def _is_command_message(body: str) -> bool:
    """这条消息是否会被现有的 on_command 命中。

    NoneBot 用全局前缀 Trie 匹配命令（命令后无需空格），这里复用同一套判定：
    命令的优先级本来就高于路由，而且同 priority 的 matcher 是并发执行的，
    如果路由层不避让，就会出现「命令执行一次 + 动作再执行一次」的重复。
    """
    try:
        from nonebot.rule import TrieRule

        raw = str(body or "").lstrip()
        if not raw:
            return False
        matched = TrieRule.prefix.longest_prefix(raw)
        # 未命中时返回的是 key=None 的具名元组，不能只判 is None
        return bool(getattr(matched, "key", None))
    except Exception:
        return False


def _keyword_only() -> bool:
    return bool(get_config("plugin_router_keyword_only", True))


def _confidence_threshold() -> float:
    try:
        return float(get_config("plugin_router_confidence", 0.75) or 0.75)
    except (TypeError, ValueError):
        return 0.75


def _ai_timeout() -> float:
    try:
        return float(get_config("plugin_router_timeout", 10) or 10)
    except (TypeError, ValueError):
        return 10.0


def _action_timeout() -> float:
    try:
        return float(get_config("plugin_router_action_timeout", 180) or 180)
    except (TypeError, ValueError):
        return 180.0


def _followup_seconds() -> int:
    try:
        return int(get_config("plugin_router_followup_seconds", 60) or 60)
    except (TypeError, ValueError):
        return 60


def _ack_enabled() -> bool:
    return bool(get_config("plugin_router_ack", False))


def _action_whitelist() -> set:
    raw = get_config("plugin_router_actions", []) or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("\n", ",").split(",")]
    return {str(x).strip() for x in raw if str(x).strip()}


def _is_superuser(event) -> bool:
    try:
        from nonebot import get_driver

        supers = set(str(x) for x in (get_driver().config.superusers or set()))
        return str(getattr(event, "user_id", "")) in supers
    except Exception:
        return False


def allowed_actions(is_group: bool = False, event: Any = None) -> List[ActionSpec]:
    """返回当前会话允许使用的动作（白名单 + scope + 权限 + enabled）。"""
    whitelist = _action_whitelist()
    sup = _is_superuser(event) if event is not None else False
    result: List[ActionSpec] = []
    for spec in get_ai_actions().values():
        if not spec.enabled:
            continue
        if whitelist and spec.name not in whitelist:
            continue
        if spec.scope == "private" and is_group:
            continue
        if spec.scope == "group" and not is_group:
            continue
        if spec.admin_only and not sup:
            continue
        result.append(spec)
    return result


# ============================================================
# 挂起动作（缺参数追问）
# ============================================================

def _scope_key(user_id: str, group_id: str) -> str:
    return f"{group_id or 'private'}:{user_id}"


def _set_pending(user_id: str, group_id: str, action: str,
                 args: Dict[str, Any], missing: str) -> None:
    _PENDING[_scope_key(user_id, group_id)] = {
        "action": action,
        "args": dict(args),
        "missing": missing,
        "expire": time.time() + _followup_seconds(),
    }


def _peek_pending(user_id: str, group_id: str) -> Optional[Dict[str, Any]]:
    key = _scope_key(user_id, group_id)
    item = _PENDING.get(key)
    if not item:
        return None
    if item.get("expire", 0) < time.time():
        _PENDING.pop(key, None)
        return None
    return item


def _clear_pending(user_id: str, group_id: str) -> None:
    _PENDING.pop(_scope_key(user_id, group_id), None)


# ============================================================
# 待确认动作（confirm=True）
# ============================================================

def _set_confirm(user_id: str, group_id: str, action: str,
                 args: Dict[str, Any], body: str) -> None:
    _CONFIRM[_scope_key(user_id, group_id)] = {
        "action": action,
        "args": dict(args),
        "text": body,
        "expire": time.time() + _followup_seconds(),
    }


def _peek_confirm(user_id: str, group_id: str) -> Optional[Dict[str, Any]]:
    key = _scope_key(user_id, group_id)
    item = _CONFIRM.get(key)
    if not item:
        return None
    if item.get("expire", 0) < time.time():
        _CONFIRM.pop(key, None)
        return None
    return item


def _clear_confirm(user_id: str, group_id: str) -> None:
    _CONFIRM.pop(_scope_key(user_id, group_id), None)


# ============================================================
# AI 判定
# ============================================================

def _decision_cache_key(user_id: str, group_id: str, text: str) -> Tuple[str, str]:
    digest = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:16]
    return _scope_key(user_id, group_id), digest


def _cache_get(user_id: str, group_id: str, text: str) -> Optional[Dict[str, Any]]:
    key = _decision_cache_key(user_id, group_id, text)
    item = _DECISION_CACHE.get(key)
    if not item:
        return None
    expire, decision = item
    if expire < time.time():
        _DECISION_CACHE.pop(key, None)
        return None
    return decision


def _cache_put(user_id: str, group_id: str, text: str, decision: Dict[str, Any]) -> None:
    if len(_DECISION_CACHE) > 500:
        _DECISION_CACHE.clear()
    _DECISION_CACHE[_decision_cache_key(user_id, group_id, text)] = (
        time.time() + _DECISION_TTL,
        decision,
    )


def _build_router_prompt(specs: Sequence[ActionSpec], text: str,
                         recent: Optional[List[str]] = None) -> str:
    catalog = format_catalog(specs)
    context_text = "\n".join(recent[-6:]) if recent else "（无）"
    names = "、".join(spec.name for spec in specs)
    return (
        "你是聊天机器人的插件调度器。判断用户最新消息是否要调用下面清单里的某个动作。\n"
        f"动作清单（只能从这些里选，合法动作名：{names}）：\n{catalog}\n\n"
        "规则：\n"
        "1. 用户只是聊天、问候、提问、表达情绪、让你讲故事/讲道理 → 选 chat；\n"
        "2. 用户明确想让你执行清单里的能力（唱歌、查天气、签到、看排行等）→ 选对应动作；\n"
        "3. 从消息里提取动作需要的参数，不要编造；用户没说的参数不要填；\n"
        "4. 不确定、或者动作和意图只是沾边 → 选 chat（宁可不做，也别乱做）；\n"
        "5. 只输出 JSON，禁止输出解释、Markdown 或多余文字。\n\n"
        '输出格式：{"action":"动作名或chat","args":{},"confidence":0.0到1.0}\n\n'
        f"【最近聊天】\n{context_text}\n\n"
        f"【用户最新消息】{text[:200]}"
    )


async def _ai_decide(specs: Sequence[ActionSpec], text: str,
                     recent: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """让小模型只输出 JSON；失败返回 None（调用方降级为普通聊天）。"""
    if not specs:
        return None
    prompt = _build_router_prompt(specs, text, recent)
    try:
        raw = await asyncio.wait_for(
            _call_ai_api(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                request_timeout=int(_ai_timeout()),
                thinking=False,
            ),
            timeout=_ai_timeout() + 3,
        )
    except asyncio.TimeoutError:
        logger.warning("[router] AI 判定超时，退回聊天")
        return None
    except Exception as e:
        logger.warning(f"[router] AI 判定失败，退回聊天: {type(e).__name__}: {e}")
        return None

    cleaned = _strip_think_blocks(str(raw or ""))
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        logger.warning(f"[router] AI 判定输出不是 JSON: {cleaned[:120]}")
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning(f"[router] AI 判定 JSON 解析失败: {cleaned[:120]}")
        return None
    if not isinstance(data, dict):
        return None

    action = str(data.get("action", "chat") or "chat").strip()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    try:
        confidence = float(data.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    return {"action": action, "args": args, "confidence": confidence}


def _recent_lines(history: Optional[List[Dict[str, Any]]]) -> List[str]:
    lines: List[str] = []
    for item in (history or [])[-6:]:
        content = item.get("content", "")
        content = content if isinstance(content, str) else str(content)
        content = re.sub(r"\s+", " ", content).strip()[:80]
        if not content:
            continue
        who = "用户" if item.get("role") == "user" else "Miku"
        lines.append(f"{who}：{content}")
    return lines


# ============================================================
# 执行
# ============================================================

def _interpret(result: Any, spec: ActionSpec, ctx: ActionContext) -> RouteResult:
    if result is None:
        return RouteResult(kind="done", action=spec.name)
    if isinstance(result, str):
        if result.strip():
            return RouteResult(kind="done", action=spec.name, reply=result.strip())
        return RouteResult(kind="done", action=spec.name)
    if not isinstance(result, dict):
        logger.warning(f"[router] 动作 {spec.name} 返回值不是 dict: {type(result).__name__}")
        return RouteResult(kind="done", action=spec.name)

    status = str(result.get("status", STATUS_OK) or STATUS_OK).lower()

    if status == STATUS_SKIP:
        return RouteResult(kind="none")
    if status == STATUS_TOOL:
        return RouteResult(kind="tool", action=spec.name, text=str(result.get("text") or ""))
    if status == STATUS_ASK:
        question = str(result.get("question") or "").strip() or "还需要补充一点信息哦～"
        return RouteResult(kind="ask", action=spec.name, text=question)
    if status == STATUS_ERROR:
        reply = str(result.get("reply") or "").strip() or "呜...操作失败了，等一下再试试吧～"
        return RouteResult(kind="done", action=spec.name, reply=reply)

    reply = str(result.get("reply") or "").strip()
    if reply and not result.get("silent"):
        return RouteResult(kind="done", action=spec.name, reply=reply)
    return RouteResult(kind="done", action=spec.name)


async def _run_action(bot: Any, event: Any, spec: ActionSpec, args: Dict[str, Any],
                      body: str, user_id: str, group_id: str, is_group: bool,
                      confirmed: bool = False) -> RouteResult:
    clean, missing = validate_args(spec, args)
    if missing:
        ask = build_ask_text(spec, missing)
        _set_pending(user_id, group_id, spec.name, clean, missing[0])
        logger.info(f"[router] 动作 {spec.name} 缺参数 {missing}，追问用户")
        return RouteResult(kind="ask", action=spec.name, text=ask)

    _clear_pending(user_id, group_id)

    # 花钱/破坏性动作：先问一句，用户回「确认」才真正执行
    if spec.confirm and not confirmed:
        _set_confirm(user_id, group_id, spec.name, clean, body)
        question = spec.confirm_text or "要现在执行吗？回复「确认」我就开始～"
        logger.info(f"[router] 动作 {spec.name} 等待用户确认: args={clean}")
        return RouteResult(kind="ask", action=spec.name, text=question)

    async def _send(msg: Any) -> None:
        try:
            await bot.send(event, msg)
        except Exception as e:
            logger.warning(f"[router] 动作 {spec.name} 发送消息失败: {type(e).__name__}: {e}")

    ctx = ActionContext(
        bot=bot,
        event=event,
        action=spec.name,
        plugin=spec.plugin,
        text=body,
        args=clean,
        user_id=user_id,
        group_id=group_id,
        is_group=is_group,
        _send=_send,
    )

    if _ack_enabled():
        await ctx.emit("好呀～稍等一下♪")

    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(spec.handler(ctx), timeout=_action_timeout())
    except asyncio.TimeoutError:
        logger.error(f"[router] 动作 {spec.name} 执行超时（>{_action_timeout():.0f}s）")
        await ctx.emit("呜...这个操作太久了，先算了吧，等一下再试试～")
        return RouteResult(kind="done", action=spec.name)
    except Exception as e:
        logger.error(f"[router] 动作 {spec.name} 执行异常: {e}\n{traceback.format_exc()}")
        await ctx.emit("呜...出了点问题，等一下再试试吧～")
        return RouteResult(kind="done", action=spec.name)

    elapsed = time.perf_counter() - started
    logger.info(f"[router] 执行动作 {spec.name} args={clean} 耗时={elapsed:.2f}s")

    routed = _interpret(result, spec, ctx)
    if routed.reply:
        await ctx.emit(routed.reply)
        routed.reply = ""
    return routed


# ============================================================
# 对外入口
# ============================================================

async def route_message(bot: Any, event: Any, text: str,
                        user_id: str, group_id: str, is_group: bool,
                        history: Optional[List[Dict[str, Any]]] = None,
                        allow_ai: bool = True) -> RouteResult:
    """完整路由：挂起补参 → 规则 → AI 判定。"""
    if not _router_enabled():
        return RouteResult()

    body = strip_prefix(text)
    if not body or len(body) > 300:
        return RouteResult()
    # 已有命令能处理的句子一律交给命令，路由不重复执行
    if _is_command_message(body):
        return RouteResult()

    specs = allowed_actions(is_group, event)
    if not specs:
        return RouteResult()

    # 0) 上一条是「待确认」动作 → 判断这句话是确认、取消，还是说别的
    waiting = _peek_confirm(user_id, group_id)
    if waiting:
        spec_c = get_ai_action(waiting.get("action", ""))
        if spec_c and spec_c.enabled:
            if body in _YES_WORDS or body.startswith("确认"):
                args = dict(waiting.get("args") or {})
                _clear_confirm(user_id, group_id)
                logger.info(f"[router] 用户确认执行 {spec_c.name}")
                return await _run_action(bot, event, spec_c, args, body,
                                         user_id, group_id, is_group, confirmed=True)
            if body in _NO_WORDS or body.startswith("取消"):
                _clear_confirm(user_id, group_id)
                logger.info(f"[router] 用户取消 {spec_c.name}")
                return RouteResult(kind="done", action=spec_c.name, reply="好的，取消啦～")
        _clear_confirm(user_id, group_id)

    # 1) 上一条在等参数 → 把这条当参数值
    pending = _peek_pending(user_id, group_id)
    if pending:
        spec = get_ai_action(pending.get("action", ""))
        if spec and spec.enabled and len(body) <= 80:
            args = dict(pending.get("args") or {})
            args[str(pending.get("missing"))] = body
            logger.info(f"[router] 补参执行 {spec.name}: {pending.get('missing')}={body[:30]}")
            return await _run_action(bot, event, spec, args, body,
                                     user_id, group_id, is_group)
        _clear_pending(user_id, group_id)

    # 2) 规则匹配（零成本）
    hit = match_rules(body, specs)
    if hit:
        spec, args = hit
        return await _run_action(bot, event, spec, args, body,
                                 user_id, group_id, is_group)

    # 3) AI 判定（可选，可预筛）
    if not allow_ai:
        return RouteResult()
    if _keyword_only() and not needs_routing(body, specs):
        return RouteResult()

    cached = _cache_get(user_id, group_id, body)
    decision = cached or await _ai_decide(specs, body, _recent_lines(history))
    if not decision:
        return RouteResult()
    if not cached:
        _cache_put(user_id, group_id, body, decision)

    action_name = str(decision.get("action", "chat") or "chat").strip()
    if action_name in ("chat", "none", ""):
        return RouteResult()
    spec = next((s for s in specs if s.name == action_name), None)
    if spec is None:
        logger.info(f"[router] AI 选了未授权动作 {action_name}，忽略")
        return RouteResult()
    if float(decision.get("confidence", 0.0) or 0.0) < _confidence_threshold():
        logger.info(
            f"[router] 动作 {action_name} 置信度 "
            f"{decision.get('confidence')} 低于阈值 {_confidence_threshold()}，忽略"
        )
        return RouteResult()

    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    logger.info(f"[router] AI 判定 action={action_name} args={args} "
                f"conf={decision.get('confidence')}")
    return await _run_action(bot, event, spec, args, body,
                             user_id, group_id, is_group)


async def route_by_rules(bot: Any, event: Any, text: str,
                         user_id: str, group_id: str, is_group: bool) -> RouteResult:
    """只走规则匹配（群聊不 @Bot 的 ambient 场景用，绝不调用 AI）。"""
    if not _router_enabled():
        return RouteResult()
    body = strip_prefix(text)
    if not body or len(body) > 300:
        return RouteResult()
    if _is_command_message(body):
        return RouteResult()
    specs = allowed_actions(is_group, event)
    if not specs:
        return RouteResult()
    hit = match_rules(body, specs)
    if not hit:
        return RouteResult()
    spec, args = hit
    return await _run_action(bot, event, spec, args, body,
                             user_id, group_id, is_group)


def has_rule_candidate(text: str, is_group: bool = False, event: Any = None) -> bool:
    """给 ambient matcher 的 rule 用：这句话是否命中某个动作的规则。"""
    if not _router_enabled():
        return False
    body = strip_prefix(text)
    if not body or len(body) > 300:
        return False
    if _is_command_message(body):
        return False
    specs = allowed_actions(is_group, event)
    if not specs:
        return False
    return match_rules(body, specs) is not None


def log_registered_actions() -> int:
    """启动时打印动作注册情况，方便排查「为什么 AI 不点歌」。"""
    actions = get_ai_actions()
    if not actions:
        logger.warning("[router] 没有任何已注册的 AI 动作，插件调度不可用")
        return 0
    grouped: Dict[str, List[str]] = {}
    for spec in actions.values():
        grouped.setdefault(spec.plugin_display or spec.plugin, []).append(spec.name)
    for plugin_name, names in grouped.items():
        logger.info(f"[router] {plugin_name}: {', '.join(names)}")
    logger.info(f"[router] 共注册 {len(actions)} 个 AI 动作")
    return len(actions)


__all__ = [
    "RouteResult",
    "route_message",
    "route_by_rules",
    "has_rule_candidate",
    "allowed_actions",
    "log_registered_actions",
]
