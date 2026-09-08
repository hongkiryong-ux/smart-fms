"""현재 접속 사용자 heartbeat 및 명단 집계."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import AppSetting, User

PRESENCE_KEY_PREFIX = "presence.user."
ACTIVE_WINDOW_SEC = 120
KST = ZoneInfo("Asia/Seoul")


def _presence_key(user_id: int) -> str:
    return f"{PRESENCE_KEY_PREFIX}{int(user_id)}"


def _parse_seen(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        value = payload.get("last_seen") if isinstance(payload, dict) else None
        return datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


async def touch_presence(
    db: AsyncSession, user_id: int, *, path: str = ""
) -> None:
    key = _presence_key(user_id)
    payload = json.dumps(
        {
            "last_seen": datetime.utcnow().isoformat(timespec="seconds"),
            "path": (path or "")[:300],
        },
        ensure_ascii=False,
    )
    row = await db.get(AppSetting, key)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=key, value=payload))
    await db.flush()


async def remove_presence(db: AsyncSession, user_id: int) -> None:
    row = await db.get(AppSetting, _presence_key(user_id))
    if row:
        await db.delete(row)
        await db.flush()


async def list_active_users(db: AsyncSession) -> list[dict]:
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=ACTIVE_WINDOW_SEC)
    settings = (
        await db.execute(
            select(AppSetting).where(AppSetting.key.like(f"{PRESENCE_KEY_PREFIX}%"))
        )
    ).scalars().all()

    seen_by_id: dict[int, datetime] = {}
    for row in settings:
        try:
            user_id = int(row.key.removeprefix(PRESENCE_KEY_PREFIX))
        except (TypeError, ValueError):
            continue
        seen = _parse_seen(row.value)
        if seen is not None and seen >= cutoff:
            seen_by_id[user_id] = seen

    if not seen_by_id:
        return []

    users = (
        await db.execute(
            select(User)
            .where(
                User.id.in_(seen_by_id),
                User.is_active == True,  # noqa: E712
                User.is_approved == True,  # noqa: E712
            )
            .options(selectinload(User.partner))
        )
    ).scalars().all()

    result = []
    for user in users:
        seen = seen_by_id.get(user.id)
        if seen is None:
            continue
        seen_kst = seen.replace(tzinfo=timezone.utc).astimezone(KST)
        result.append(
            {
                "id": user.id,
                "name": user.name or user.username,
                "username": user.username,
                "role": user.role,
                "company": user.company_display,
                "last_seen": seen_kst.strftime("%H:%M:%S"),
                "seconds_ago": max(0, int((now - seen).total_seconds())),
            }
        )
    return sorted(result, key=lambda item: item["seconds_ago"])
