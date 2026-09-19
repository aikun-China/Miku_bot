"""
表情包系统 - 从leekchat适配
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot.log import logger

from ..config import get_config

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MEME_ROOT = PLUGIN_ROOT / "resources" / "meme"
ALLOWED_SUFFIX = {".gif", ".png", ".jpg", ".jpeg", ".webp"}

_STICKER_INTENT_LINE_RE = __import__("re").compile(r"^\s*\[\]\s*$", __import__("re").MULTILINE)


@dataclass
class StickerResult:
    cleaned_text: str
    success: bool
    emoji_path: str | None = None


class EmojiAgent:
    def __init__(self) -> None:
        self._characters: list[str] | None = None
        self._files_by_character: dict[str, list[Path]] | None = None

    def _ensure_index(self) -> None:
        if self._characters is not None:
            return
        chars: list[str] = []
        files_map: dict[str, list[Path]] = {}
        if MEME_ROOT.is_dir():
            for entry in sorted(MEME_ROOT.iterdir()):
                if not entry.is_dir():
                    continue
                files = [p for p in entry.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIX]
                if files:
                    chars.append(entry.name)
                    files_map[entry.name] = files
        self._characters = chars
        self._files_by_character = files_map

    def get_available_characters(self) -> list[str]:
        self._ensure_index()
        return list(self._characters or [])

    def has_available_emojis(self, characters: list[str] | None) -> bool:
        available = self.get_available_characters()
        if not available:
            return False
        if not characters:
            return True
        return any(c in available for c in characters)

    def list_candidate_paths(self, characters: list[str] | None, limit: int = 32) -> list[Path]:
        self._ensure_index()
        available = self.get_available_characters()
        if characters:
            pool = [c for c in characters if c in available]
        else:
            pool = available
        paths: list[Path] = []
        for char in pool:
            for p in (self._files_by_character or {}).get(char, []):
                paths.append(p)
                if len(paths) >= limit:
                    return paths
        return paths

    async def process_sticker_response(
        self,
        text: str,
        ctx: dict | None = None,
    ) -> StickerResult:
        if not text or "[]" not in text:
            return StickerResult(cleaned_text=text, success=False)
        emoji_cfg_enabled = bool(get_config("emoji_enabled", False))
        if not emoji_cfg_enabled:
            return StickerResult(cleaned_text=text, success=False)

        characters = get_config("emoji_characters", []) or []
        if isinstance(characters, str):
            characters = [c.strip() for c in characters.split(",") if c.strip()]

        candidates = self.list_candidate_paths(characters or None)
        if not candidates:
            return StickerResult(cleaned_text=text, success=False)

        chosen = await self._select_by_ai(candidates, text)
        if chosen is None:
            chosen = random.choice(candidates)
        cleaned = _STICKER_INTENT_LINE_RE.sub("", text).strip()
        return StickerResult(cleaned_text=cleaned, success=True, emoji_path=str(chosen))

    async def _select_by_ai(self, candidates: list[Path], text: str) -> Path | None:
        listing = "\n".join(f"- [{i}] {p.parent.name}/{p.name}" for i, p in enumerate(candidates))
        prompt = (
            "Pick ONE sticker/emoji from the candidates below that best fits the mood of the "
            "assistant's reply. Reply as JSON: {\"index\": <int>, \"reason\": \"<short>\"}.\n\n"
            f"--- REPLY TEXT ---\n{text}\n\n--- CANDIDATES ---\n{listing}"
        )
        try:
            from ..data_source import _call_ai_api
            messages = [{"role": "user", "content": prompt}]
            resp_text = await _call_ai_api(messages, temperature=0.2, max_tokens=80)
            data = json.loads((resp_text or "{}").strip())
            idx = int(data.get("index", -1))
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except Exception as e:
            logger.warning(f"[EmojiAgent] select failed: {e}")
        return None
