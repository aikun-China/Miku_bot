"""
Miku AI 会话记忆后端
基于JSON文件的持久化聊天记录
好感度统一由 utils.user_store 管理，避免双写不一致
"""

import json
import time
import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Optional
from nonebot.log import logger

from .config import get_config, PROJECT_ROOT

# 导入 user_store 作为好感度唯一数据源
sys.path.insert(0, str(PROJECT_ROOT))
from utils.user_store import get_favor, add_favor as _us_add_favor


def _get_history_path() -> Path:
    path_str = str(get_config("history_file", "data/ai_chat_history.json") or "data/ai_chat_history.json")
    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


class ChatHistoryManager:
    """聊天记录管理器"""
    
    _instance = None
    _history: Dict[str, List[Dict]] = {}
    _save_lock: Optional[asyncio.Lock] = None
    _dirty = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance
    
    def _load(self):
        """从文件加载历史记录"""
        path = _get_history_path()
        self._history = {}
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._history = data.get("history", {})
                # 旧版好感度数据：迁移到 user_store（仅一次）
                old_favor = data.get("favor", {})
                migrated = 0
                for uid, fav in old_favor.items():
                    try:
                        cur = get_favor(uid)
                        if cur == 0.0 and float(fav) > 0:
                            _us_add_favor(uid, float(fav))
                            migrated += 1
                    except Exception:
                        pass
                if migrated > 0:
                    logger.info(f"[miku_ai] 已迁移 {migrated} 条旧好感度数据到 user_store")
                logger.info(f"[miku_ai] 聊天记录已加载，{len(self._history)} 个会话")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                logger.info("[miku_ai] 聊天记录文件不存在，将创建新的")
        except Exception as e:
            logger.warning(f"[miku_ai] 加载聊天记录失败: {e}")
    
    async def save(self):
        """异步保存到文件"""
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()
        async with self._save_lock:
            await asyncio.to_thread(self._save_sync)
    
    def _save_sync(self):
        path = _get_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 只保存历史记录，好感度由 user_store 统一管理
            data = {"history": self._history}
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)
            self._dirty = False
            logger.debug("[miku_ai] 聊天记录已保存")
        except Exception as e:
            logger.warning(f"[miku_ai] 保存聊天记录失败: {e}")
    
    def get_session_id(self, user_id: str, group_id: str = "") -> str:
        if group_id:
            return f"group_{group_id}"
        return f"private_{user_id}"
    
    def get_history(self, session_id: str) -> List[Dict]:
        return self._history.get(session_id, [])
    
    def append_message(self, session_id: str, role: str, content: str,
                       user_id: str = "", user_name: str = "",
                       images: list = None, is_group: bool = False):
        """追加消息到历史记录"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": int(time.time()),
            "user_id": user_id,
            "user_name": user_name,
        }
        if images:
            msg["images"] = images
        if session_id not in self._history:
            self._history[session_id] = []
        self._history[session_id].append(msg)
        
        max_count = int(get_config("group_history_max", 1000) if is_group else get_config("private_history_max", 500))
        if len(self._history[session_id]) > max_count:
            self._history[session_id] = self._history[session_id][-max_count:]
        
        self._dirty = True
    
    def get_raw_messages(self, session_id: str, limit: int = 50) -> List[Dict]:
        """获取原始消息列表（用于leekchat风格动态上下文构建）"""
        history = self.get_history(session_id)
        return history[-limit:] if len(history) > limit else list(history)

    def get_context_messages(self, session_id: str, is_group: bool = False,
                             system_prompt: str = "", max_context: int = 0,
                             bot_nickname: str = "") -> List[Dict]:
        """获取用于发送给AI的上下文消息，群聊中附带用户名便于AI区分对话者"""
        history = self.get_history(session_id)
        
        if max_context <= 0:
            max_context = int(get_config("group_context_max", 25) if is_group else get_config("private_context_max", 30))
        
        context = []
        recent = history[-max_context:] if len(history) > max_context else history
        
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            images = msg.get("images", [])
            user_name = msg.get("user_name", "")
            
            # 群聊中用户消息附带用户名，让AI知道是谁说的
            if is_group and role == "user" and user_name:
                if isinstance(content, str) and content:
                    content = f"[{user_name}]: {content}"
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            part["text"] = f"[{user_name}]: {part['text']}"
            
            # 群聊中机器人消息用昵称
            if is_group and role == "assistant" and bot_nickname:
                if isinstance(content, str) and content:
                    content = f"[{bot_nickname}]: {content}"
            
            if images and role == "user":
                if isinstance(content, list):
                    context.append({"role": role, "content": content})
                else:
                    text_part = [{"type": "text", "text": content}] if content else []
                    image_parts = [
                        {"type": "image_url", "image_url": {"url": img_url}}
                        for img_url in images
                    ]
                    context.append({"role": role, "content": text_part + image_parts})
            else:
                context.append({"role": role, "content": content})
        
        if system_prompt:
            context.insert(0, {"role": "system", "content": system_prompt})
        
        return context
    
    def clear_history(self, session_id: str):
        if session_id in self._history:
            del self._history[session_id]
            self._dirty = True
    
    def get_favor(self, user_id: str) -> float:
        """获取好感度（代理到 user_store，保证数据一致性）"""
        try:
            return get_favor(user_id)
        except Exception as e:
            logger.warning(f"[miku_ai] 获取好感度失败: {e}")
            return 0.0
    
    def set_favor(self, user_id: str, value: float):
        """设置好感度（代理到 user_store，保证数据一致性）"""
        try:
            from utils.user_store import set_favor as _us_set_favor
            _us_set_favor(user_id, value)
        except Exception as e:
            logger.warning(f"[miku_ai] 设置好感度失败: {e}")
    
    def add_favor(self, user_id: str, delta: float) -> float:
        """增加好感度（代理到 user_store，保证数据一致性）"""
        try:
            return _us_add_favor(user_id, delta)
        except Exception as e:
            logger.warning(f"[miku_ai] 增加好感度失败: {e}")
            return 0.0
