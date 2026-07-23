"""개발·PyInstaller 배포 공통 경로 (실행 파일 옆 data/prompts 사용)"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """쓰기 가능한 앱 루트 — 배포 시 exe가 있는 폴더."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = app_root()
DATA_DIR = APP_ROOT / "data"
PROMPTS_DIR = APP_ROOT / "prompts"
OUTPUT_DIR = APP_ROOT / "output"


def prompt_path(name: str) -> Path:
    return PROMPTS_DIR / name


def ensure_runtime_dirs() -> None:
    """배포 PC 최초 실행 시 폴더·기본 설정 파일 준비."""
    for d in (DATA_DIR, OUTPUT_DIR, DATA_DIR / "browser_profiles"):
        d.mkdir(parents=True, exist_ok=True)

    settings = DATA_DIR / "ai_settings.json"
    if not settings.exists():
        settings.write_text(
            '{\n'
            '  "provider": "chatgpt_api",\n'
            '  "openai_api_key": "",\n'
            '  "openai_model": "gpt-4o",\n'
            '  "gemini_api_key": "",\n'
            '  "gemini_model": "gemini-2.0-flash",\n'
            '  "web_logged_in": false,\n'
            '  "web_logged_in_provider": ""\n'
            "}\n",
            encoding="utf-8",
        )

    prefs = DATA_DIR / "ui_prefs.json"
    if not prefs.exists():
        prefs.write_text(
            '{"department":"","section":"","assessor":"","apply_type":""}\n',
            encoding="utf-8",
        )

    user_presets = DATA_DIR / "user_presets.json"
    if not user_presets.exists():
        user_presets.write_text('{"presets": []}\n', encoding="utf-8")
