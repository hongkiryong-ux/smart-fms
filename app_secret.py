"""세션·자동로그인·메일 링크 서명용 비밀키 (공개된 기본값 사용 금지)."""
from __future__ import annotations

import hashlib
import os

_PUBLIC_DEFAULT = "change_this_secret_in_prod"


def _is_production() -> bool:
    return os.environ.get("RENDER", "").lower() in ("true", "1", "yes")


def _resolve() -> str:
    key = (os.environ.get("APP_SECRET_KEY") or "").strip()
    if key and key != _PUBLIC_DEFAULT:
        return key
    if not _is_production():
        return _PUBLIC_DEFAULT
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("운영 환경에는 APP_SECRET_KEY 환경변수가 필요합니다.")
    print(
        "[security] APP_SECRET_KEY 미설정 — DATABASE_URL 기반 임시 키 사용. Render 환경변수에 APP_SECRET_KEY를 설정하세요.",
        flush=True,
    )
    return hashlib.sha256(("smart-fms-secret|" + db_url).encode()).hexdigest()


APP_SECRET_KEY = _resolve()
