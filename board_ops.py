"""주요설비 일정 · 공지사항 · 대시보드 위젯 설정."""
from __future__ import annotations

import json
from calendar import Calendar, monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app_templates import templates
from auth import can_access_menu, can_create, can_delete, can_edit, require_login
from models import (
    AppSetting,
    Building,
    Equipment,
    Floor,
    Notice,
    ScheduleEvent,
    Site,
    User,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)
from database import get_db

router = APIRouter()
KST = ZoneInfo("Asia/Seoul")

SCHEDULE_CATEGORIES = ("긴급", "점검", "작업", "검수")
NOTICE_CATEGORIES = ("긴급", "안전", "일반")

DASH_WIDGETS: tuple[tuple[str, str], ...] = (
    ("maintenance_status", "정비 의뢰 현황"),
    ("sites_status", "사업장 현황"),
    ("energy", "에너지 / 유틸리티"),
    ("schedules", "주요 설비 일정"),
    ("notices", "공지사항"),
)
DASH_WIDGET_KEYS = [k for k, _ in DASH_WIDGETS]
DASH_WIDGET_HINTS = {
    "maintenance_status": "긴급·의뢰·해결·오늘 작업 KPI 카드",
    "sites_status": "사업장별 정비의뢰·미해결·알람 순위",
    "energy": "전력·급수·가스 등 (현재 예시 그래픽)",
    "schedules": "오늘 등록된 주요설비 일정",
    "notices": "최근 공지 목록",
}
DEFAULT_DASH_CONFIG = {
    "order": list(DASH_WIDGET_KEYS),
    "visible": {k: True for k in DASH_WIDGET_KEYS},
}


def _today_kst() -> date:
    return datetime.now(KST).date()


def _normalize_dash_config(raw: dict | None) -> dict:
    base = {
        "order": list(DEFAULT_DASH_CONFIG["order"]),
        "visible": dict(DEFAULT_DASH_CONFIG["visible"]),
    }
    if not isinstance(raw, dict):
        return base
    order = [k for k in (raw.get("order") or []) if k in DASH_WIDGET_KEYS]
    for k in DASH_WIDGET_KEYS:
        if k not in order:
            order.append(k)
    visible_raw = raw.get("visible") or {}
    visible = {
        k: bool(visible_raw.get(k, True)) if isinstance(visible_raw, dict) else True
        for k in DASH_WIDGET_KEYS
    }
    return {"order": order, "visible": visible}


async def get_dashboard_widget_config(db: AsyncSession) -> dict:
    try:
        row = await db.get(AppSetting, "dashboard.widgets")
        if row and row.value:
            return _normalize_dash_config(json.loads(row.value))
    except Exception:
        pass
    return _normalize_dash_config(None)


async def set_dashboard_widget_config(db: AsyncSession, cfg: dict) -> None:
    payload = json.dumps(_normalize_dash_config(cfg), ensure_ascii=False)
    row = await db.get(AppSetting, "dashboard.widgets")
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key="dashboard.widgets", value=payload))


async def load_today_schedules(db: AsyncSession, day: date | None = None, limit: int = 8) -> list[dict]:
    day = day or _today_kst()
    rows = (
        await db.execute(
            select(ScheduleEvent)
            .where(
                ScheduleEvent.is_active == True,  # noqa: E712
                ScheduleEvent.event_date == day,
            )
            .options(selectinload(ScheduleEvent.site))
            .order_by(ScheduleEvent.event_time.asc(), ScheduleEvent.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "category": r.category or "작업",
            "event_time": r.event_time or "",
            "location": r.location
            or ((r.site.name if r.site else "") or ""),
            "site_name": (r.site.name if r.site else "") or "",
        }
        for r in rows
    ]


async def load_recent_notices(db: AsyncSession, limit: int = 6) -> list[dict]:
    rows = (
        await db.execute(
            select(Notice)
            .where(Notice.is_active == True)  # noqa: E712
            .order_by(Notice.is_pinned.desc(), Notice.published_at.desc(), Notice.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    out = []
    for r in rows:
        pub = r.published_at or r.created_at
        out.append(
            {
                "id": r.id,
                "title": r.title,
                "category": r.category or "일반",
                "date_label": pub.strftime("%m/%d") if pub else "",
                "is_pinned": bool(r.is_pinned),
            }
        )
    return out


async def load_site_status(db: AsyncSession, limit: int = 5) -> list[dict]:
    """문제(미해결 정비)·정비의뢰 많은 사업장 순."""
    # 사업장명 매칭 → 대시보드 썸네일 (부분 일치)
    site_photos = (
        ("광양운영", "/static/img/sites/gwangyang-ops.png"),
        ("해수담수", "/static/img/sites/desalination.png"),
        ("담수", "/static/img/sites/desalination.png"),
        ("RIST", "/static/img/sites/rist.png"),
        ("rist", "/static/img/sites/rist.png"),
        ("미세먼지", "/static/img/sites/rist.png"),
        ("광양", "/static/img/sites/gwangyang-ops.png"),
    )

    def _photo_for(name: str) -> str | None:
        n = (name or "").strip()
        n_lower = n.lower()
        for key, url in site_photos:
            if key.lower() in n_lower:
                return url
        return None

    open_st = (
        WorkOrderStatus.received,
        WorkOrderStatus.assigned,
        WorkOrderStatus.in_progress,
    )
    sites = (
        await db.execute(select(Site).where(Site.is_active == True).order_by(Site.name))  # noqa: E712
    ).scalars().all()
    result: list[dict] = []
    for s in sites:
        buildings = (
            await db.execute(
                select(func.count(Building.id)).where(
                    Building.site_id == s.id,
                    Building.is_active == True,  # noqa: E712
                )
            )
        ).scalar() or 0
        equipment = (
            await db.execute(
                select(func.count(Equipment.id))
                .join(Zone, Equipment.zone_id == Zone.id)
                .join(Floor, Zone.floor_id == Floor.id)
                .join(Building, Floor.building_id == Building.id)
                .where(
                    Building.site_id == s.id,
                    Equipment.is_active == True,  # noqa: E712
                    Building.is_active == True,  # noqa: E712
                )
            )
        ).scalar() or 0
        wo_open = (
            await db.execute(
                select(func.count(WorkOrder.id)).where(
                    WorkOrder.site_id == s.id,
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(open_st),
                )
            )
        ).scalar() or 0
        wo_urgent = (
            await db.execute(
                select(func.count(WorkOrder.id)).where(
                    WorkOrder.site_id == s.id,
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(open_st),
                    WorkOrder.priority == "high",
                )
            )
        ).scalar() or 0
        wo_all_open_like = int(wo_open)
        status = "주의" if (wo_urgent > 0 or wo_open >= 3) else "정상"
        result.append(
            {
                "id": s.id,
                "name": s.name,
                "photo_url": _photo_for(s.name),
                "buildings": int(buildings),
                "equipment": int(equipment),
                "requests": wo_all_open_like,
                "unresolved": int(wo_open),
                "alarms": int(wo_urgent),
                "status": status,
                "score": int(wo_urgent) * 10 + int(wo_open),
            }
        )
    result.sort(key=lambda x: (-x["score"], -x["requests"], x["name"]))
    for i, item in enumerate(result[:limit], start=1):
        item["rank"] = i
    return result[:limit]


def _require_menu(user: User, key: str) -> None:
    if not can_access_menu(user, key):
        raise HTTPException(403, "메뉴 권한이 없습니다.")


# ── 주요설비 일정 ─────────────────────────────────────────


@router.get("/admin/schedules")
async def schedules_page(
    request: Request,
    year: int | None = Query(None),
    month: int | None = Query(None),
    day: str | None = Query(None),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "schedules")
    today = _today_kst()
    y = year or today.year
    m = month or today.month
    if m < 1 or m > 12:
        raise HTTPException(400, "잘못된 월")
    selected = today
    if day:
        try:
            selected = date.fromisoformat(day)
            y, m = selected.year, selected.month
        except ValueError:
            selected = today

    cal = Calendar(firstweekday=6)  # 일요일 시작
    weeks = cal.monthdatescalendar(y, m)
    month_start = date(y, m, 1)
    month_end = date(y, m, monthrange(y, m)[1])

    events = (
        await db.execute(
            select(ScheduleEvent)
            .where(
                ScheduleEvent.is_active == True,  # noqa: E712
                ScheduleEvent.event_date >= month_start,
                ScheduleEvent.event_date <= month_end,
            )
            .options(selectinload(ScheduleEvent.site))
            .order_by(ScheduleEvent.event_date, ScheduleEvent.event_time.asc(), ScheduleEvent.id.asc())
        )
    ).scalars().all()

    by_day: dict[str, list] = {}
    for e in events:
        key = e.event_date.isoformat()
        by_day.setdefault(key, []).append(e)

    day_events = [
        e for e in events if e.event_date == selected
    ]
    sites = (
        await db.execute(select(Site).where(Site.is_active == True).order_by(Site.name))  # noqa: E712
    ).scalars().all()

    prev_m = m - 1
    prev_y = y
    if prev_m < 1:
        prev_m, prev_y = 12, y - 1
    next_m = m + 1
    next_y = y
    if next_m > 12:
        next_m, next_y = 1, y + 1

    return templates.TemplateResponse(
        request,
        "schedules.html",
        {
            "user": user,
            "year": y,
            "month": m,
            "today": today,
            "selected": selected,
            "weeks": weeks,
            "by_day": by_day,
            "day_events": day_events,
            "sites": sites,
            "categories": SCHEDULE_CATEGORIES,
            "prev_y": prev_y,
            "prev_m": prev_m,
            "next_y": next_y,
            "next_m": next_m,
            "can_create": can_create(user),
            "can_edit": can_edit(user),
            "can_delete": can_delete(user),
        },
    )


@router.post("/admin/schedules")
async def schedules_create(
    request: Request,
    title: str = Form(...),
    category: str = Form("작업"),
    event_date: str = Form(...),
    event_time: str = Form(""),
    location: str = Form(""),
    site_id: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "schedules")
    if not can_create(user):
        raise HTTPException(403, "등록 권한이 없습니다.")
    try:
        d = date.fromisoformat(event_date.strip())
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류") from None
    cat = category.strip() if category.strip() in SCHEDULE_CATEGORIES else "작업"
    sid = int(site_id) if site_id.strip().isdigit() else None
    ev = ScheduleEvent(
        title=title.strip()[:300],
        category=cat,
        event_date=d,
        event_time=(event_time.strip()[:10] or None),
        location=(location.strip()[:200] or None),
        site_id=sid,
        description=(description.strip() or None),
        created_by=user.name or user.username,
    )
    db.add(ev)
    await db.commit()
    return RedirectResponse(
        f"/admin/schedules?year={d.year}&month={d.month}&day={d.isoformat()}&flash=created",
        status_code=303,
    )


@router.post("/admin/schedules/{event_id}/delete")
async def schedules_delete(
    event_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "schedules")
    if not can_delete(user):
        raise HTTPException(403, "삭제 권한이 없습니다.")
    ev = await db.get(ScheduleEvent, event_id)
    if not ev:
        raise HTTPException(404)
    d = ev.event_date
    ev.is_active = False
    await db.commit()
    return RedirectResponse(
        f"/admin/schedules?year={d.year}&month={d.month}&day={d.isoformat()}&flash=deleted",
        status_code=303,
    )


# ── 공지사항 ──────────────────────────────────────────────


@router.get("/admin/notices")
async def notices_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "notices")
    rows = (
        await db.execute(
            select(Notice)
            .where(Notice.is_active == True)  # noqa: E712
            .order_by(Notice.is_pinned.desc(), Notice.published_at.desc(), Notice.id.desc())
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "notices.html",
        {
            "user": user,
            "notices": rows,
            "categories": NOTICE_CATEGORIES,
            "can_create": can_create(user),
            "can_edit": can_edit(user),
            "can_delete": can_delete(user),
        },
    )


@router.post("/admin/notices")
async def notices_create(
    title: str = Form(...),
    category: str = Form("일반"),
    body: str = Form(""),
    is_pinned: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "notices")
    if not can_create(user):
        raise HTTPException(403, "등록 권한이 없습니다.")
    cat = category.strip() if category.strip() in NOTICE_CATEGORIES else "일반"
    db.add(
        Notice(
            title=title.strip()[:300],
            category=cat,
            body=(body.strip() or None),
            is_pinned=is_pinned in ("1", "on", "true", "True"),
            published_at=datetime.utcnow(),
            created_by=user.name or user.username,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/notices?flash=created", status_code=303)


@router.post("/admin/notices/{notice_id}/delete")
async def notices_delete(
    notice_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "notices")
    if not can_delete(user):
        raise HTTPException(403, "삭제 권한이 없습니다.")
    row = await db.get(Notice, notice_id)
    if not row:
        raise HTTPException(404)
    row.is_active = False
    await db.commit()
    return RedirectResponse("/admin/notices?flash=deleted", status_code=303)


# ── 대시보드 설정 ─────────────────────────────────────────


@router.get("/admin/dashboard/settings")
async def dashboard_settings_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    cfg = await get_dashboard_widget_config(db)
    widgets = [
        {
            "key": key,
            "label": label,
            "hint": DASH_WIDGET_HINTS.get(key, ""),
            "visible": cfg["visible"].get(key, True),
            "order": cfg["order"].index(key) + 1 if key in cfg["order"] else 99,
        }
        for key, label in DASH_WIDGETS
    ]
    widgets.sort(key=lambda x: x["order"])
    return templates.TemplateResponse(
        request,
        "dashboard_settings.html",
        {
            "user": user,
            "widgets": widgets,
            "can_edit": can_edit(user),
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/admin/dashboard/settings")
async def dashboard_settings_save(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if not can_edit(user):
        raise HTTPException(403, "수정 권한이 없습니다.")
    form = await request.form()
    order_raw = str(form.get("order") or "")
    order = [k.strip() for k in order_raw.split(",") if k.strip() in DASH_WIDGET_KEYS]
    for k in DASH_WIDGET_KEYS:
        if k not in order:
            order.append(k)
    visible = {}
    for k in DASH_WIDGET_KEYS:
        visible[k] = form.get(f"visible_{k}") in ("1", "on", "true", "True")
    await set_dashboard_widget_config(db, {"order": order, "visible": visible})
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=saved", status_code=303)


@router.post("/admin/dashboard/settings/reset")
async def dashboard_settings_reset(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if not can_edit(user):
        raise HTTPException(403, "수정 권한이 없습니다.")
    await set_dashboard_widget_config(db, DEFAULT_DASH_CONFIG)
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=reset", status_code=303)
