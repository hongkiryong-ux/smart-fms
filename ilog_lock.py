"""점검일지 편집 잠금 (한 파일 · 한 편집자)."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting, User

# heartbeat 없이 이 시간이 지나면 잠금 만료 (브라우저 강제 종료 대비)
LOCK_TTL_SEC = 90
LOCK_KEY_PREFIX = "ilog.lock."


def _lock_key(file_id: int) -> str:
    return f"{LOCK_KEY_PREFIX}{int(file_id)}"


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).rstrip("Z")
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _parse_lock(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _is_stale(lock: dict, *, now: datetime | None = None) -> bool:
    now = now or _now()
    hb = _parse_iso(lock.get("heartbeat_at") or lock.get("acquired_at"))
    if hb is None:
        return True
    return hb < (now - timedelta(seconds=LOCK_TTL_SEC))


def display_name(user: User | None, *, qr_eq_code: str | None = None) -> str:
    if user is not None:
        return (user.name or user.username or f"user-{user.id}").strip() or f"user-{user.id}"
    code = (qr_eq_code or "").strip()
    return f"QR:{code}" if code else "QR 사용자"


def session_lock_key(request) -> str:
    """브라우저 세션별 잠금 식별자 (비로그인 QR 포함)."""
    sid = request.session.get("ilog_lock_sid")
    if not sid:
        sid = secrets.token_hex(16)
        request.session["ilog_lock_sid"] = sid
    return str(sid)


def _same_session(lock: dict, session_key: str) -> bool:
    return str(lock.get("session_key") or "") == str(session_key or "")


def _same_user(lock: dict, user: User | None) -> bool:
    if user is None:
        return False
    uid = lock.get("user_id")
    if uid is None:
        return False
    try:
        return int(uid) == int(user.id)
    except (TypeError, ValueError):
        return False


async def get_lock(db: AsyncSession, file_id: int) -> dict | None:
    row = await db.get(AppSetting, _lock_key(file_id))
    if not row or not row.value:
        return None
    lock = _parse_lock(row.value)
    if not lock:
        return None
    if _is_stale(lock):
        return None
    return lock


async def _write_lock(db: AsyncSession, file_id: int, payload: dict) -> None:
    key = _lock_key(file_id)
    raw = json.dumps(payload, ensure_ascii=False)
    row = await db.get(AppSetting, key)
    if row:
        row.value = raw
    else:
        db.add(AppSetting(key=key, value=raw))


async def clear_lock(db: AsyncSession, file_id: int) -> None:
    key = _lock_key(file_id)
    row = await db.get(AppSetting, key)
    if row:
        await db.delete(row)


async def acquire_lock(
    db: AsyncSession,
    *,
    file_id: int,
    user: User | None,
    session_key: str,
    name: str,
) -> tuple[bool, dict | None]:
    """
    잠금 획득 시도.
    Returns: (ok, blocking_lock_or_None)
    - ok True: 획득/갱신 성공
    - ok False: 다른 사용자가 잠금 중 → blocking_lock 정보
    """
    now = _now()
    row = await db.get(AppSetting, _lock_key(file_id))
    existing = _parse_lock(row.value if row else None)

    if existing and not _is_stale(existing, now=now):
        if _same_session(existing, session_key):
            existing["name"] = name
            existing["user_id"] = int(user.id) if user is not None else None
            existing["heartbeat_at"] = _iso(now)
            await _write_lock(db, file_id, existing)
            return True, existing
        # 같은 로그인 사용자가 새 탭/기기에서 열면 기존 세션 잠금을 인수
        if _same_user(existing, user):
            existing["name"] = name
            existing["session_key"] = session_key
            existing["user_id"] = int(user.id) if user is not None else None
            existing["acquired_at"] = _iso(now)
            existing["heartbeat_at"] = _iso(now)
            await _write_lock(db, file_id, existing)
            return True, existing
        return False, existing

    payload = {
        "user_id": int(user.id) if user is not None else None,
        "session_key": session_key,
        "name": name,
        "acquired_at": _iso(now),
        "heartbeat_at": _iso(now),
    }
    await _write_lock(db, file_id, payload)
    return True, payload


async def heartbeat_lock(
    db: AsyncSession,
    *,
    file_id: int,
    user: User | None,
    session_key: str,
) -> bool:
    lock = await get_lock(db, file_id)
    if not lock:
        return False
    if not _same_session(lock, session_key):
        return False
    lock["heartbeat_at"] = _iso(_now())
    await _write_lock(db, file_id, lock)
    return True


async def release_lock(
    db: AsyncSession,
    *,
    file_id: int,
    user: User | None,
    session_key: str,
) -> bool:
    row = await db.get(AppSetting, _lock_key(file_id))
    if not row or not row.value:
        return True
    lock = _parse_lock(row.value)
    if not lock:
        await db.delete(row)
        return True
    if _is_stale(lock) or _same_session(lock, session_key):
        await db.delete(row)
        return True
    return False
