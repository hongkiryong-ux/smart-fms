# access_log.py — 관리자 접속(로그인·로그아웃) 이력
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AccessLog

KST = ZoneInfo("Asia/Seoul")

EVENT_LABELS: dict[str, str] = {
    "login_success": "로그인 성공",
    "login_fail": "로그인 실패",
    "logout": "로그아웃",
}

DETAIL_LABELS: dict[str, str] = {
    "invalid_credentials": "아이디/비밀번호 오류",
    "inactive": "비활성 계정",
    "pending_approval": "승인 대기",
}


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def client_user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:500]


async def record_access_log(
    db: AsyncSession,
    *,
    event_type: str,
    username: str,
    request: Request,
    success: bool = False,
    user_id: int | None = None,
    display_name: str | None = None,
    detail: str | None = None,
) -> None:
    row = AccessLog(
        event_type=event_type,
        username=(username or "").strip()[:64] or "-",
        user_id=user_id,
        display_name=(display_name or "")[:100] or None,
        ip_address=client_ip(request) or None,
        user_agent=client_user_agent(request) or None,
        success=success,
        detail=(detail or "")[:200] or None,
    )
    db.add(row)
    await db.commit()


def _kst_day_bounds(date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None]:
    start = end = None
    if date_from:
        try:
            d = date.fromisoformat(date_from.strip())
            start = (
                datetime(d.year, d.month, d.day, tzinfo=KST)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        except ValueError:
            pass
    if date_to:
        try:
            d = date.fromisoformat(date_to.strip())
            end = (
                datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=KST)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        except ValueError:
            pass
    return start, end


def _fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


async def query_access_logs(
    db: AsyncSession,
    *,
    q: str = "",
    event: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    per_page = max(10, min(per_page, 200))
    page = max(1, page)
    stmt = select(AccessLog)
    count_stmt = select(func.count()).select_from(AccessLog)

    event_key = (event or "").strip()
    if event_key and event_key in EVENT_LABELS:
        stmt = stmt.where(AccessLog.event_type == event_key)
        count_stmt = count_stmt.where(AccessLog.event_type == event_key)

    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        cond = or_(
            AccessLog.username.ilike(like),
            AccessLog.display_name.ilike(like),
            AccessLog.ip_address.ilike(like),
            AccessLog.user_agent.ilike(like),
            AccessLog.detail.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    start, end = _kst_day_bounds(date_from, date_to)
    if start is not None:
        stmt = stmt.where(AccessLog.created_at >= start)
        count_stmt = count_stmt.where(AccessLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AccessLog.created_at <= end)
        count_stmt = count_stmt.where(AccessLog.created_at <= end)

    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages

    rows = (
        await db.execute(
            stmt.order_by(AccessLog.created_at.desc(), AccessLog.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    items = []
    for row in rows:
        detail = row.detail or ""
        items.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "event_label": EVENT_LABELS.get(row.event_type, row.event_type),
                "username": row.username,
                "display_name": row.display_name or "",
                "ip_address": row.ip_address or "",
                "user_agent": row.user_agent or "",
                "success": bool(row.success),
                "detail": detail,
                "detail_label": DETAIL_LABELS.get(detail, detail),
                "created_at": _fmt_kst(row.created_at),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }
