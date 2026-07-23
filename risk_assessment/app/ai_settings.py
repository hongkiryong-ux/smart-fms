"""AI 제공자 설정 저장·로드"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.runtime_paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "ai_settings.json"
PROFILE_DIR = DATA_DIR / "browser_profiles"

PROVIDERS = {
    "chatgpt_api": "ChatGPT (API 키)",
    "gemini_api": "Gemini (API 키)",
    "chatgpt_web": "ChatGPT (웹 로그인)",
    "gemini_web": "Gemini (웹 로그인)",
}


@dataclass
class AISettings:
    provider: str = "chatgpt_api"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    web_logged_in: bool = False
    web_logged_in_provider: str = ""

    @classmethod
    def load(cls) -> AISettings:
        data: dict = {}
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        settings = cls(
            provider=data.get("provider", "chatgpt_api"),
            openai_api_key=data.get("openai_api_key", ""),
            openai_model=data.get("openai_model", "gpt-4o"),
            gemini_api_key=data.get("gemini_api_key", ""),
            gemini_model=data.get("gemini_model", "gemini-2.0-flash"),
            web_logged_in=bool(data.get("web_logged_in", False)),
            web_logged_in_provider=data.get("web_logged_in_provider", ""),
        )
        if not settings.openai_api_key:
            settings.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if settings.openai_model == "gpt-4o":
            settings.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
        if not settings.gemini_api_key:
            settings.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if settings.gemini_model == "gemini-2.0-flash":
            env_model = os.getenv("GEMINI_MODEL", "").strip()
            if env_model:
                settings.gemini_model = env_model
        env_provider = os.getenv("AI_PROVIDER", "").strip()
        if env_provider in PROVIDERS:
            settings.provider = env_provider
        return settings

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def profile_path(self) -> Path:
        name = "chatgpt" if self.provider.startswith("chatgpt") else "gemini"
        return PROFILE_DIR / name

    def mark_web_logged_in(self) -> None:
        self.web_logged_in = True
        self.web_logged_in_provider = self.provider
        self.save()

    def clear_web_login(self) -> None:
        self.web_logged_in = False
        self.web_logged_in_provider = ""
        self.save()

    def web_session_valid(self) -> bool:
        if not self.provider.endswith("_web"):
            return False
        return self.web_logged_in and self.web_logged_in_provider == self.provider

    def provider_label(self) -> str:
        return PROVIDERS.get(self.provider, self.provider)
