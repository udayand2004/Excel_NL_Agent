"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

_env_file = os.getenv("EXCEL_AGENT_ENV", "").strip()
if _env_file:
    load_dotenv(dotenv_path=_env_file)
else:
    load_dotenv()

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"


def _csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ollama_host() -> str:
    return os.getenv("OLLAMA_HOST", "127.0.0.1:11434").strip() or "127.0.0.1:11434"


def _ollama_base_url(probe: bool) -> str:
    host = _ollama_host()
    if host.startswith(("http://", "https://")):
        base = host.rstrip("/")
        return base if base.endswith("/v1") else f"{base}/v1"
    hostname, _, port = host.partition(":")
    try:
        if probe:
            import socket

            with socket.create_connection((hostname, int(port or "11434")), timeout=0.3):
                pass
        return f"http://{host}/v1"
    except (OSError, ValueError):
        return ""


def _default_base_url() -> str:
    """Explicit OPENAI_BASE_URL wins. Otherwise probe local Ollama as a free fallback."""
    explicit = os.getenv("OPENAI_BASE_URL", "").strip()
    if explicit:
        return explicit
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return _ollama_base_url(probe=True)
    return ""


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip() or "ollama"
    )
    openai_base_url: str = field(default_factory=_default_base_url)
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_base_url: str = field(
        default_factory=lambda: os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL).strip()
        or DEFAULT_GROQ_BASE_URL
    )
    groq_model: str = field(
        default_factory=lambda: os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip()
        or DEFAULT_GROQ_MODEL
    )
    ollama_host: str = field(default_factory=_ollama_host)
    ollama_base_url: str = field(default_factory=lambda: _ollama_base_url(probe=False))
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
        or "qwen2.5:7b"
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "").strip()
        or (os.getenv("OLLAMA_MODEL", "qwen2.5:7b") if not os.getenv("OPENAI_API_KEY") else "gpt-4o-mini")
    )
    temperature_code: float = field(
        default_factory=lambda: float(os.getenv("TEMPERATURE_CODE", "0.1"))
    )
    temperature_text: float = field(
        default_factory=lambda: float(os.getenv("TEMPERATURE_TEXT", "0.4"))
    )
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "25")))
    session_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("SESSION_TTL_SECONDS", "7200"))
    )
    max_sessions: int = field(default_factory=lambda: int(os.getenv("MAX_SESSIONS", "200")))
    code_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("CODE_TIMEOUT_SECONDS", "10"))
    )
    max_code_retries: int = field(default_factory=lambda: int(os.getenv("MAX_CODE_RETRIES", "2")))
    cors_origins: List[str] = field(
        default_factory=lambda: _csv_env(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5500,http://127.0.0.1:5500",
        )
    )
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    @property
    def llm_provider(self) -> str:
        if self.groq_api_key:
            return "groq"
        if self.openai_base_url and "11434" in self.openai_base_url:
            return "ollama"
        if self.openai_api_key.lower() == "ollama":
            return "ollama"
        if self.openai_api_key:
            return "openai"
        if self.openai_base_url:
            return "openai-compatible"
        return "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
