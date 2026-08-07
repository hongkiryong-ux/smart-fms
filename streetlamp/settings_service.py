# streetlamp/settings_service.py — FMS AppSetting 공유, 키는 streetlamp. 접두사
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting

PREFIX = "streetlamp."

DEFAULT_SETTINGS: dict[str, str] = {
    "report_email": "hkr008@poscowide.com",
    "report_hour_kst": "16",
    "report_minute_kst": "0",
    "keep_alive_minutes": "0",
    "use_internal_daily_scheduler": "1",
    "alert_sms_enabled": "1",
    "alert_sms_phones": "01071704563",
}


def _sk(key: str) -> str:
    k = (key or "").strip()
    if k.startswith(PREFIX):
        return k
    return PREFIX + k


def _bare(key: str) -> str:
    k = (key or "").strip()
    return k[len(PREFIX) :] if k.startswith(PREFIX) else k


async def ensure_default_settings(session: AsyncSession) -> None:
    for key, val in DEFAULT_SETTINGS.items():
        sk = _sk(key)
        result = await session.execute(select(AppSetting).where(AppSetting.key == sk))
        row = result.scalar_one_or_none()
        if not row:
            session.add(AppSetting(key=sk, value=val))


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    sk = _sk(key)
    bare = _bare(key)
    result = await session.execute(select(AppSetting).where(AppSetting.key == sk))
    row = result.scalar_one_or_none()
    if row and row.value is not None:
        return row.value
    # 구 streetlamp 키(접두사 없음) 호환
    result2 = await session.execute(select(AppSetting).where(AppSetting.key == bare))
    row2 = result2.scalar_one_or_none()
    if row2 and row2.value is not None:
        return row2.value
    return DEFAULT_SETTINGS.get(bare, default)


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    sk = _sk(key)
    result = await session.execute(select(AppSetting).where(AppSetting.key == sk))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=sk, value=value))


async def get_all_settings_map(session: AsyncSession) -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    result = await session.execute(select(AppSetting))
    for row in result.scalars().all():
        if row.value is None:
            continue
        if row.key.startswith(PREFIX):
            out[_bare(row.key)] = row.value
        elif row.key in DEFAULT_SETTINGS:
            out.setdefault(row.key, row.value)
    return out
