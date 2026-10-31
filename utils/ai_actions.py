"""
AI 插件动作注册中心
====================
插件把自己的能力注册成「可被 AI 自主判断执行的动作」。

设计原则：
1. **AI 只做决策，执行永远发生在插件注册的 handler 里**。AI 输出
   ``{"action": "music.play", "args": {"song": "春日影"}}``，代码再调用对应 handler，
   不存在「AI 直接执行代码」这回事。
2. **显式注册才可见**。没有注册的动作 AI 永远看不到，天然是白名单，
   管理类危险动作（重启 / 更新 / 广播 / 踢人）不要注册。
3. **规则优先，AI 兜底**。每个动作可以带 ``patterns``（正则）和 ``keywords``（关键词），
   命中规则就直接执行，零延迟零成本；只有规则没命中时才让 AI 判断。
4. **handler 不依赖 matcher**。handler 通过 ``ctx.emit()`` 发消息，
   由路由层用 ``bot.send(event, ...)`` 实现，避免 ``matcher.finish()`` 抛出的
   ``FinishedException`` 打断调用方 matcher。

handler 返回值约定（dict）
--------------------------
- ``{"status": "ok"}``                  已经通过 ctx.emit 发完消息
- ``{"status": "ok", "reply": "文本"}``  由路由层补发这句文本（handler 自己没发）
- ``{"status": "tool", "text": "事实块"}`` tool 模式：交给 AI 组织成自然回复
- ``{"status": "ask", "question": "..."}`` 缺参数追问，动作挂起等用户补充
- ``{"status": "error", "reply": "..."}`` 失败，路由层发这句提示
- ``{"status": "skip"}``                 放弃处理，退回普通聊天
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

# ── handler 返回状态常量 ──
STATUS_OK = "ok"
STATUS_TOOL = "tool"
STATUS_ASK = "ask"
STATUS_ERROR = "error"
STATUS_SKIP = "skip"

_VALID_MODES = ("action", "tool")
_VALID_SCOPES = ("all", "private", "group")

# 参数字符串最大长度，防止模型把整段聊天塞进参数
_DEFAULT_MAX_LEN = 120

# 命令前缀（与项目 COMMAND_START 对齐，去掉后再做规则匹配）
_PREFIX_RE = re.compile(r"^\s*[/!！]+\s*")


@dataclass
class ActionContext:
    """一次动作执行的上下文。

    handler 通过它拿到事件、参数和发送能力，不直接依赖 NoneBot matcher。
    """

    bot: Any
    event: Any
    action: str
    plugin: str
    text: str                       # 去掉 @ 与命令前缀后的纯文本
    args: Dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    group_id: str = ""
    is_group: bool = False
    _send: Optional[Callable[[Any], Awaitable[None]]] = None

    # ── 发消息 ──
    async def emit(self, msg: Any) -> None:
        """发送一条消息（文本 / MessageSegment 均可）。"""
        if self._send is None:
            raise RuntimeError(f"[ai_actions] 动作 {self.action} 缺少发送通道")
        await self._send(msg)

    # ── 便捷属性 ──
    @property
    def nickname(self) -> str:
        sender = getattr(self.event, "sender", None)
        return str(getattr(sender, "nickname", "") or self.user_id)

    def arg(self, name: str, default: Any = None) -> Any:
        value = self.args.get(name, default)
        return default if value is None or value == "" else value

    def arg_str(self, name: str, default: str = "") -> str:
        value = self.args.get(name)
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def arg_int(self, name: str, default: int = 0) -> int:
        try:
            return int(str(self.args.get(name, default)).strip())
        except (TypeError, ValueError):
            return default


@dataclass
class ActionSpec:
    """一个可被 AI 调用的动作定义。"""

    name: str                                   # 唯一动作名，如 music.play
    plugin: str                                 # 所属插件，如 miku_music
    description: str                            # 给 AI 看的一句话说明
    handler: Callable[[ActionContext], Awaitable[Any]]
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    examples: List[str] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    mode: str = "action"                        # action=插件自己发；tool=返回数据给 AI
    scope: str = "all"                          # all / private / group
    admin_only: bool = False
    enabled: bool = True
    order: int = 99
    plugin_display: str = ""                    # 可读名字，仅用于日志
    confirm: bool = False                       # 执行前必须由用户回复「确认」
    confirm_text: str = ""                      # 自定义确认问句

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"[ai_actions] {self.name} 的 mode 非法: {self.mode}")
        if self.scope not in _VALID_SCOPES:
            raise ValueError(f"[ai_actions] {self.name} 的 scope 非法: {self.scope}")
        for pattern in self.patterns:
            re.compile(pattern)  # 提前暴露写错的正则

    # ── 参数描述 ──
    def short_params(self) -> str:
        if not self.params:
            return "无参数"
        parts = []
        for key, meta in self.params.items():
            meta = meta or {}
            desc = str(meta.get("desc", "") or "")
            label = f"{key}"
            if meta.get("required"):
                label += "(必填"
            else:
                label += "(可选"
            if desc:
                label += f",{desc}"
            enum = meta.get("enum")
            if enum:
                label += f",取值:{'/'.join(str(e) for e in enum)}"
            label += ")"
            parts.append(label)
        return ", ".join(parts)

    def rule_keywords(self) -> List[str]:
        """用于「是否需要路由」预筛的关键词（含 patterns 里提取的固定词）。"""
        words = [str(k).strip() for k in self.keywords if str(k).strip()]
        if not words:
            for example in self.examples[:3]:
                if 1 <= len(example) <= 8:
                    words.append(example)
        return words


# ── 注册表 ──
_ACTIONS: Dict[str, ActionSpec] = {}


def register_ai_action(
    name: str,
    plugin: str,
    description: str,
    handler: Callable[[ActionContext], Awaitable[Any]],
    params: Optional[Dict[str, Dict[str, Any]]] = None,
    examples: Optional[Sequence[str]] = None,
    patterns: Optional[Sequence[str]] = None,
    keywords: Optional[Sequence[str]] = None,
    mode: str = "action",
    scope: str = "all",
    admin_only: bool = False,
    enabled: bool = True,
    order: int = 99,
    plugin_display: str = "",
    confirm: bool = False,
    confirm_text: str = "",
) -> ActionSpec:
    """注册一个 AI 可调用动作。重复注册同名动作会覆盖旧定义（并打日志）。"""
    spec = ActionSpec(
        name=str(name).strip(),
        plugin=str(plugin).strip(),
        description=str(description).strip(),
        handler=handler,
        params=dict(params or {}),
        examples=[str(e) for e in (examples or [])],
        patterns=[str(p) for p in (patterns or [])],
        keywords=[str(k) for k in (keywords or [])],
        mode=mode,
        scope=scope,
        admin_only=admin_only,
        enabled=enabled,
        order=order,
        plugin_display=plugin_display or str(plugin),
        confirm=confirm,
        confirm_text=str(confirm_text or ""),
    )
    if not spec.name:
        raise ValueError("[ai_actions] 动作名不能为空")
    _ACTIONS[spec.name] = spec
    return spec


def unregister_ai_action(name: str) -> None:
    _ACTIONS.pop(str(name), None)


def get_ai_actions() -> Dict[str, ActionSpec]:
    """返回已注册动作的副本（按 order 排序的 dict）。"""
    ordered = sorted(_ACTIONS.values(), key=lambda s: (s.order, s.name))
    return {spec.name: spec for spec in ordered}


def get_ai_action(name: str) -> Optional[ActionSpec]:
    return _ACTIONS.get(str(name))


def clear_ai_actions() -> None:
    """仅供测试使用。"""
    _ACTIONS.clear()


def strip_prefix(text: str) -> str:
    """去掉命令前缀和首尾空白。"""
    return _PREFIX_RE.sub("", str(text or "")).strip()


def match_rules(
    text: str,
    specs: Optional[Sequence[ActionSpec]] = None,
) -> Optional[Tuple[ActionSpec, Dict[str, str]]]:
    """用动作自带的正则/关键词做零成本匹配，命中返回 (spec, args)。

    先按 ``order`` 升序尝试正则（命名组即参数），再尝试关键词（仅零参数动作）。
    """
    body = strip_prefix(text)
    if not body:
        return None
    candidates = list(specs) if specs is not None else list(get_ai_actions().values())

    for spec in candidates:
        if not spec.patterns:
            continue
        for pattern in spec.patterns:
            try:
                m = re.match(pattern, body, re.IGNORECASE | re.DOTALL)
            except re.error:
                continue
            if not m:
                continue
            args = {k: v for k, v in m.groupdict().items() if v is not None}
            return spec, args

    for spec in candidates:
        if spec.params or not spec.keywords:
            continue  # 有关键词但需要参数时，必须靠正则或 AI
        for kw in spec.keywords:
            if kw and kw in body:
                return spec, {}
    return None


def has_rule_match(text: str, specs: Optional[Sequence[ActionSpec]] = None) -> bool:
    return match_rules(text, specs) is not None


def needs_routing(
    text: str,
    specs: Optional[Sequence[ActionSpec]] = None,
) -> bool:
    """快速预筛：这句话是否可能命中某个动作（正则或关键词），用于省掉 AI 调用。"""
    body = strip_prefix(text)
    if not body:
        return False
    candidates = list(specs) if specs is not None else list(get_ai_actions().values())
    for spec in candidates:
        for kw in spec.rule_keywords():
            if kw and kw in body:
                return True
        for pattern in spec.patterns:
            try:
                if re.match(pattern, body, re.IGNORECASE | re.DOTALL):
                    return True
            except re.error:
                continue
    return False


def validate_args(
    spec: ActionSpec, args: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """按参数定义清洗并校验，返回 (clean_args, missing_required)。

    - 未声明的参数直接丢弃（防止模型塞乱七八糟的键）
    - 字符串去空白、截断到 120 字符
    - enum 只保留合法值，非法值当作缺失处理
    - 整数尽量转换，转不了当作缺失
    """
    clean: Dict[str, Any] = {}
    missing: List[str] = []

    for key, meta in (spec.params or {}).items():
        meta = meta or {}
        raw = args.get(key)
        required = bool(meta.get("required"))
        ptype = str(meta.get("type", "string") or "string")

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                missing.append(key)
            continue

        if ptype == "int":
            try:
                clean[key] = int(str(raw).strip())
            except (TypeError, ValueError):
                if required:
                    missing.append(key)
            continue

        text = str(raw).strip()
        enum = meta.get("enum")
        if enum:
            matched = None
            for item in enum:
                if text == str(item):
                    matched = str(item)
                    break
            if matched is None:
                # 允许「包含」匹配，如「高音质」→ 高清
                for item in enum:
                    if str(item) and str(item) in text:
                        matched = str(item)
                        break
            if matched is None:
                if required:
                    missing.append(key)
                continue
            text = matched

        max_len = int(meta.get("max_len", _DEFAULT_MAX_LEN) or _DEFAULT_MAX_LEN)
        clean[key] = text[:max_len]

    return clean, missing


def build_ask_text(spec: ActionSpec, missing: List[str]) -> str:
    """为缺失参数生成追问文案。优先使用参数自定义的 ask。"""
    if not missing:
        return ""
    parts = []
    for key in missing:
        meta = (spec.params or {}).get(key) or {}
        ask = str(meta.get("ask", "") or "").strip()
        if ask:
            parts.append(ask)
        else:
            desc = str(meta.get("desc", "") or key)
            parts.append(f"{desc}是什么呢？")
    return " ".join(parts)


def format_catalog(specs: Sequence[ActionSpec], max_examples: int = 3) -> str:
    """把动作清单序列化成给 AI 看的文本。"""
    lines: List[str] = []
    for spec in specs:
        line = f"- {spec.name}｜{spec.description}｜参数: {spec.short_params()}"
        if spec.examples:
            examples = "、".join(spec.examples[:max_examples])
            line += f"｜示例: {examples}"
        lines.append(line)
    return "\n".join(lines)


def all_keywords(specs: Sequence[ActionSpec]) -> List[str]:
    """汇总所有动作的预筛关键词。"""
    words: List[str] = []
    for spec in specs:
        for kw in spec.rule_keywords():
            if kw and kw not in words:
                words.append(kw)
    return words


__all__ = [
    "ActionContext",
    "ActionSpec",
    "STATUS_OK",
    "STATUS_TOOL",
    "STATUS_ASK",
    "STATUS_ERROR",
    "STATUS_SKIP",
    "register_ai_action",
    "unregister_ai_action",
    "get_ai_actions",
    "get_ai_action",
    "clear_ai_actions",
    "strip_prefix",
    "match_rules",
    "has_rule_match",
    "needs_routing",
    "validate_args",
    "build_ask_text",
    "format_catalog",
    "all_keywords",
]
