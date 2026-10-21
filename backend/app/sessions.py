"""Thread-safe in-memory session store with TTL and LRU eviction."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class Session:
    session_id: str
    filename: str
    sheets: Dict[str, pd.DataFrame]
    active_sheet: str
    profile: Dict[str, Any]
    context: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)

    @property
    def df(self) -> pd.DataFrame:
        return self.sheets[self.active_sheet]

    def touch(self) -> None:
        self.last_access = time.time()


class SessionStore:
    """LRU-bounded session store with TTL-based expiry."""

    def __init__(self, ttl_seconds: int = 7200, max_sessions: int = 200) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._lock = threading.RLock()
        self._sessions: "OrderedDict[str, Session]" = OrderedDict()

    def create(
        self, filename: str, sheets: Dict[str, pd.DataFrame], profile: Dict[str, Any]
    ) -> Session:
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        session = Session(
            session_id=session_id,
            filename=filename,
            sheets=sheets,
            active_sheet=next(iter(sheets)),
            profile=profile,
        )
        with self._lock:
            self._evict_expired_unlocked()
            while len(self._sessions) >= self._max:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if time.time() - session.last_access > self._ttl:
                self._sessions.pop(session_id, None)
                return None
            session.touch()
            self._sessions.move_to_end(session_id)
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def active_count(self) -> int:
        with self._lock:
            self._evict_expired_unlocked()
            return len(self._sessions)

    def cleanup_expired(self) -> int:
        with self._lock:
            return self._evict_expired_unlocked()

    def _evict_expired_unlocked(self) -> int:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items() if now - s.last_access > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)
