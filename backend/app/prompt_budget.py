"""Token budgeting, prompt trimming, and a simple per-minute rate limiter."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from typing import Deque, Dict, List, Tuple

ASCII_CHARS_PER_TOKEN = 3.0
TRUNCATION_MARKER = " … [truncated]"
MIDDLE_MARKER = "\n… [middle truncated to fit the model token limit] …\n"


def estimate_tokens(text: str) -> int:
    """Conservative estimate: ASCII ~3 chars/token, non-ASCII ~1 token/char."""
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / ASCII_CHARS_PER_TOKEN + non_ascii))


def _prefix_for_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _suffix_for_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        if estimate_tokens(text[middle:]) <= max_tokens:
            high = middle
        else:
            low = middle + 1
    return text[low:]


def truncate_text(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - len(TRUNCATION_MARKER))].rstrip() + TRUNCATION_MARKER


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    text = text or ""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    marker_tokens = estimate_tokens(TRUNCATION_MARKER)
    body = _prefix_for_tokens(text, max(1, max_tokens - marker_tokens))
    return body.rstrip() + TRUNCATION_MARKER


def trim_middle(text: str, max_tokens: int) -> str:
    """Keep the beginning and end; instructions usually live at the end."""
    text = text or ""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    marker_tokens = estimate_tokens(MIDDLE_MARKER)
    remaining = max_tokens - marker_tokens
    if remaining <= 0:
        return truncate_to_tokens(text, max_tokens)
    head_tokens = max(1, int(remaining * 0.6))
    tail_tokens = max(1, remaining - head_tokens)
    head = _prefix_for_tokens(text, head_tokens)
    tail = _suffix_for_tokens(text, tail_tokens)
    return head.rstrip() + MIDDLE_MARKER + tail.lstrip()


def format_history(
    history: List[Dict[str, str]] | None,
    current_question: str = "",
    max_tokens: int = 400,
) -> str:
    """Compact recent turns plus a digest of older user questions."""
    prior = list(history or [])
    current_question = (current_question or "").strip()
    if (
        prior
        and prior[-1].get("role") == "user"
        and (prior[-1].get("content") or "").strip() == current_question
    ):
        prior = prior[:-1]

    recent = prior[-4:]
    lines: List[str] = []
    for message in recent:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{message.get('role', 'user')}: {truncate_text(content, 220)}")

    older = [
        (message.get("content") or "").strip()
        for message in prior[:-4]
        if message.get("role") == "user" and (message.get("content") or "").strip()
    ]
    if older:
        digest = "; ".join(truncate_text(question, 80) for question in older[-6:])
        lines.append("Earlier user questions: " + digest)

    return truncate_to_tokens("\n".join(lines), max_tokens)


class TokenRateLimiter:
    """Best-effort input-token limiter for Groq's per-minute free-tier limit."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(0, int(limit))
        self.window = float(window_seconds)
        self._events: Deque[Tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def try_acquire(self, tokens: int, max_wait: float = 2.0) -> bool:
        if self.limit <= 0:
            return True
        tokens = max(1, int(tokens))
        if tokens > self.limit:
            return False

        deadline = time.monotonic() + max(0.0, max_wait)
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._events and now - self._events[0][0] >= self.window:
                    self._events.popleft()
                used = sum(event_tokens for _, event_tokens in self._events)
                if used + tokens <= self.limit:
                    self._events.append((now, tokens))
                    return True
                if not self._events:
                    self._events.append((now, tokens))
                    return True
                wait = self.window - (now - self._events[0][0]) + 0.05
            if time.monotonic() + wait > deadline:
                return False
            await asyncio.sleep(max(wait, 0.05))
