# jobs.py — 일일 리포트 스케줄 + 재등록
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import AsyncSessionLocal
from streetlamp.reporting import run_daily_report_pipeline
from streetlamp.settings_service import get_setting


def _truthy(s: str) -> bool:
    return (s or "").strip().lower() in ("1", "true", "yes", "on")


async def job_daily_report() -> None:
    async with AsyncSessionLocal() as session:
        to_email = await get_setting(session, "report_email")
        msg = await run_daily_report_pipeline(session, to_email)
    print(f"[daily-report] {msg}")


async def reschedule_daily_report_job(scheduler: AsyncIOScheduler) -> None:
    """DB 설정을 읽어 매일 메일 작업을 등록/해제합니다."""
    async with AsyncSessionLocal() as session:
        use = await get_setting(session, "use_internal_daily_scheduler")
        try:
            h = int(await get_setting(session, "report_hour_kst") or "16")
            m = int(await get_setting(session, "report_minute_kst") or "0")
        except ValueError:
            h, m = 16, 0

    h = max(0, min(23, h))
    m = max(0, min(59, m))

    try:
        scheduler.remove_job("daily_report")
    except Exception:
        pass

    if not _truthy(use):
        return

    scheduler.add_job(
        job_daily_report,
        CronTrigger(hour=h, minute=m, timezone=ZoneInfo("Asia/Seoul")),
        id="daily_report",
        replace_existing=True,
    )


def public_base_url_for_ping() -> str:
    return (
        os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        or ""
    )
