"""주요설비 일정 · 공지사항 · 대시보드 위젯 설정."""
from __future__ import annotations

import base64
import json
import time
from calendar import Calendar, monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
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
    UserRole,
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
    ("energy", "주택변전소 전력사용량(전일 기준. 사용량 Trend 최근 7일)"),
    ("schedules", "주요 설비 일정"),
    ("notices", "공지사항"),
)
DASH_WIDGET_KEYS = [k for k, _ in DASH_WIDGETS]
DASH_WIDGET_HINTS = {
    "maintenance_status": "긴급·의뢰·해결·오늘 작업 KPI 카드 (전체 폭)",
    "sites_status": "사업장별 시설·설비·정비의뢰·미해결·완료 카드 (좌측)",
    "energy": "No.1~3 TR 전일 사용량·부하율·최근 7일 그래프 (좌측)",
    "schedules": "오늘 등록된 주요설비 일정 (우측)",
    "notices": "최근 공지 목록 (우측)",
}
SITE_METRIC_KEYS: tuple[str, ...] = (
    "buildings",
    "equipment",
    "requests",
    "unresolved",
    "completed",
)
SITE_METRIC_LABELS: dict[str, str] = {
    "buildings": "시설",
    "equipment": "설비",
    "requests": "정비의뢰",
    "unresolved": "미해결",
    "completed": "완료",
}
DEFAULT_DASH_CONFIG = {
    "order": list(DASH_WIDGET_KEYS),
    "visible": {k: True for k in DASH_WIDGET_KEYS},
    "site_metrics": {k: True for k in SITE_METRIC_KEYS},
}
GWANGYANG_FACILITIES_SETTING_KEY = "dashboard.gwangyang_public_facilities"
GWANGYANG_EXCEL_MAX_BYTES = 5 * 1024 * 1024


def _as_bool(value: Any, default: bool = True) -> bool:
    """대시보드 visible 값 파싱 (문자/숫자 False가 True로 바뀌지 않게)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in ("1", "true", "on", "yes", "y"):
        return True
    if s in ("0", "false", "off", "no", "n", ""):
        return False
    return default


def _today_kst() -> date:
    return datetime.now(KST).date()


def _normalize_dash_config(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {
            "order": list(DEFAULT_DASH_CONFIG["order"]),
            "visible": dict(DEFAULT_DASH_CONFIG["visible"]),
            "site_metrics": dict(DEFAULT_DASH_CONFIG["site_metrics"]),
        }
    order = [k for k in (raw.get("order") or []) if k in DASH_WIDGET_KEYS]
    for k in DASH_WIDGET_KEYS:
        if k not in order:
            order.append(k)
    visible_raw = raw.get("visible") if isinstance(raw.get("visible"), dict) else {}
    visible = {}
    for k in DASH_WIDGET_KEYS:
        if k in visible_raw:
            visible[k] = _as_bool(visible_raw.get(k), False)
        else:
            visible[k] = True
    metrics_raw = raw.get("site_metrics") if isinstance(raw.get("site_metrics"), dict) else {}
    site_metrics = {}
    for k in SITE_METRIC_KEYS:
        if k in metrics_raw:
            site_metrics[k] = _as_bool(metrics_raw.get(k), False)
        else:
            site_metrics[k] = True
    return {"order": order, "visible": visible, "site_metrics": site_metrics}


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


SITE_STATUS_ORDER_KEY = "dashboard.site_status_order"
SITE_THUMB_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
SITE_THUMB_EXTS = frozenset(SITE_THUMB_MIME.keys()) | {".pdf"}


def _site_thumb_url_key(site_id: int) -> str:
    return f"dashboard.site_thumb.{int(site_id)}"


def _site_thumb_blob_key(site_id: int) -> str:
    return f"dashboard.site_thumb.blob.{int(site_id)}"


def _site_thumb_file_url(site_id: int, version: str | None = None) -> str:
    v = version or str(int(time.time()))
    return f"/admin/dashboard/site-thumb/{int(site_id)}?v={v}"


def _normalize_site_status_order(raw) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            sid = int(item)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


async def get_site_status_order(db: AsyncSession) -> list[int]:
    try:
        row = await db.get(AppSetting, SITE_STATUS_ORDER_KEY)
        if row and row.value:
            return _normalize_site_status_order(json.loads(row.value))
    except Exception:
        pass
    return []


async def set_site_status_order(db: AsyncSession, site_ids: list[int]) -> None:
    payload = json.dumps(_normalize_site_status_order(site_ids), ensure_ascii=False)
    row = await db.get(AppSetting, SITE_STATUS_ORDER_KEY)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=SITE_STATUS_ORDER_KEY, value=payload))


async def load_site_thumb_blob(db: AsyncSession, site_id: int) -> tuple[bytes, str] | None:
    row = await db.get(AppSetting, _site_thumb_blob_key(site_id))
    if not row or not (row.value or "").strip():
        return None
    try:
        data = json.loads(row.value)
        raw = base64.b64decode(data.get("b64") or "")
        mime = str(data.get("mime") or "image/jpeg")
        if not raw:
            return None
        return raw, mime
    except Exception:
        return None


async def get_site_status_custom_photo(db: AsyncSession, site_id: int) -> str | None:
    """대시보드 설정에서 올린 개별 썸네일 URL."""
    blob = await load_site_thumb_blob(db, site_id)
    if blob is not None:
        row = await db.get(AppSetting, _site_thumb_url_key(site_id))
        version = None
        if row and row.value and "v=" in (row.value or ""):
            version = row.value.rsplit("v=", 1)[-1].split("&")[0]
        return _site_thumb_file_url(site_id, version)
    row = await db.get(AppSetting, _site_thumb_url_key(site_id))
    if row and (row.value or "").strip():
        return row.value.strip()
    return None


async def save_site_status_photo_upload(
    db: AsyncSession, site_id: int, content: bytes, suffix: str
) -> str:
    suffix = (suffix or "").lower()
    if suffix not in SITE_THUMB_EXTS:
        raise ValueError("지원하지 않는 파일 형식입니다.")
    raw = content
    mime = SITE_THUMB_MIME.get(suffix, "image/jpeg")
    if suffix == ".pdf":
        import tempfile

        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "in.pdf"
            jpg_path = Path(tmp) / "out.jpg"
            pdf_path.write_bytes(content)
            doc = pymupdf.open(str(pdf_path))
            try:
                if doc.page_count < 1:
                    raise ValueError("PDF에 페이지가 없습니다.")
                pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
                pix.save(str(jpg_path))
            finally:
                doc.close()
            raw = jpg_path.read_bytes()
            mime = "image/jpeg"

    payload = json.dumps(
        {"mime": mime, "b64": base64.b64encode(raw).decode("ascii")},
        ensure_ascii=False,
    )
    blob_key = _site_thumb_blob_key(site_id)
    blob_row = await db.get(AppSetting, blob_key)
    if blob_row is None:
        db.add(AppSetting(key=blob_key, value=payload))
    else:
        blob_row.value = payload

    url = _site_thumb_file_url(site_id)
    url_key = _site_thumb_url_key(site_id)
    url_row = await db.get(AppSetting, url_key)
    if url_row is None:
        db.add(AppSetting(key=url_key, value=url))
    else:
        url_row.value = url
    await db.commit()
    return url


def _default_site_photo(name: str) -> str | None:
    site_photos = (
        ("광양운영", "/static/img/sites/gwangyang-ops.png"),
        ("해수담수", "/static/img/sites/desalination.png"),
        ("담수", "/static/img/sites/desalination.png"),
        ("RIST", "/static/img/sites/rist.png"),
        ("rist", "/static/img/sites/rist.png"),
        ("미세먼지", "/static/img/sites/rist.png"),
        ("광양", "/static/img/sites/gwangyang-ops.png"),
    )
    n = (name or "").strip()
    n_lower = n.lower()
    for key, url in site_photos:
        if key.lower() in n_lower:
            return url
    return None


def _sort_site_status_items(items: list[dict], order_ids: list[int] | None = None) -> list[dict]:
    """수동 순서 우선, 나머지는 점수·의뢰·이름 순."""
    if not items:
        return []
    score_key = lambda x: (-x["score"], -x["requests"], x["name"])
    if not order_ids:
        return sorted(items, key=score_key)
    by_id = {int(x["id"]): x for x in items}
    ordered: list[dict] = []
    for sid in order_ids:
        item = by_id.pop(int(sid), None)
        if item is not None:
            ordered.append(item)
    rest = sorted(by_id.values(), key=score_key)
    return ordered + rest


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
                "body": r.body or "",
                "date_label": pub.strftime("%m/%d") if pub else "",
                "is_pinned": bool(r.is_pinned),
            }
        )
    return out


_STATUS_LABELS = {
    "received": "접수",
    "assigned": "배정",
    "in_progress": "진행중",
    "completed": "완료",
    "verified": "검수",
    "closed": "종료",
}


async def load_recent_work_orders(db: AsyncSession, limit: int = 6) -> list[dict]:
    """대시보드 '최근 정비현황'용 — 기존 정비의뢰 데이터 재사용."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    rows = (
        await db.execute(
            select(WorkOrder)
            .where(WorkOrder.is_active == True)  # noqa: E712
            .order_by(WorkOrder.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    kst = ZoneInfo("Asia/Seoul")
    out = []
    for r in rows:
        st = r.status.value if hasattr(r.status, "value") else str(r.status or "")
        ts = getattr(r, "completed_at", None) or getattr(r, "created_at", None)
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(kst)
            else:
                ts = ts.astimezone(kst)
            time_label = ts.strftime("%m/%d %H:%M")
        else:
            time_label = ""
        out.append(
            {
                "id": r.id,
                "title": r.title or "정비의뢰",
                "status": st,
                "status_label": _STATUS_LABELS.get(st, st or "-"),
                "priority": (r.priority or "normal"),
                "time_label": time_label,
                "url": f"/admin/work-orders/{r.id}",
            }
        )
    return out


async def load_site_status(db: AsyncSession, limit: int | None = None) -> list[dict]:
    """등록된 활성 사업장 전체 현황 (문제·정비의뢰 많은 순)."""
    open_st = (
        WorkOrderStatus.received,
        WorkOrderStatus.assigned,
        WorkOrderStatus.in_progress,
    )
    done_st = (
        WorkOrderStatus.completed,
        WorkOrderStatus.verified,
        WorkOrderStatus.closed,
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
        wo_total = (
            await db.execute(
                select(func.count(WorkOrder.id)).where(
                    WorkOrder.site_id == s.id,
                    WorkOrder.is_active == True,  # noqa: E712
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
        wo_done = (
            await db.execute(
                select(func.count(WorkOrder.id)).where(
                    WorkOrder.site_id == s.id,
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(done_st),
                    WorkOrder.completion_approval_pending.is_not(True),
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
        from gwangyang_facilities import GWANGYANG_FACILITIES_PATH, is_gwangyang_ops_site

        gy = is_gwangyang_ops_site(s.name)
        custom_photo = await get_site_status_custom_photo(db, int(s.id))
        result.append(
            {
                "id": s.id,
                "name": s.name,
                "photo_url": custom_photo or _default_site_photo(s.name),
                "buildings": int(buildings),
                "equipment": int(equipment),
                "requests": int(wo_total),
                "unresolved": int(wo_open),
                "completed": int(wo_done),
                "score": int(wo_urgent) * 10 + int(wo_open),
                "is_gwangyang_ops": gy,
                "facilities_url": GWANGYANG_FACILITIES_PATH if gy else None,
            }
        )
    order_ids = await get_site_status_order(db)
    result = _sort_site_status_items(result, order_ids)
    if limit is not None and limit > 0:
        result = result[: int(limit)]
    for i, item in enumerate(result, start=1):
        item["rank"] = i
    return result


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

    parsed_day: date | None = None
    if day:
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            parsed_day = None

    # year/month가 명시되면 그 달력을 유지한다 (이전/다음 이동).
    # day만 있으면 해당 날짜의 연·월로 이동한다.
    if year is not None or month is not None:
        y = year if year is not None else (parsed_day.year if parsed_day else today.year)
        m = month if month is not None else (parsed_day.month if parsed_day else today.month)
    else:
        base = parsed_day or today
        y, m = base.year, base.month

    if m < 1 or m > 12:
        raise HTTPException(400, "잘못된 월")

    if parsed_day and parsed_day.year == y and parsed_day.month == m:
        selected = parsed_day
    elif today.year == y and today.month == m:
        selected = today
    else:
        selected = date(y, m, 1)

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


@router.post("/admin/notices/{notice_id}/edit")
async def notices_update(
    notice_id: int,
    title: str = Form(...),
    category: str = Form("일반"),
    body: str = Form(""),
    is_pinned: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_menu(user, "notices")
    if not can_edit(user):
        raise HTTPException(403, "수정 권한이 없습니다.")
    row = await db.get(Notice, notice_id)
    if not row or not row.is_active:
        raise HTTPException(404)
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(400, "공지 제목을 입력하세요.")
    row.title = clean_title[:300]
    row.category = (
        category.strip() if category.strip() in NOTICE_CATEGORIES else "일반"
    )
    row.body = body.strip() or None
    row.is_pinned = is_pinned in ("1", "on", "true", "True")
    await db.commit()
    return RedirectResponse("/admin/notices?flash=updated", status_code=303)


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


async def _load_effective_gwangyang_facilities(db: AsyncSession) -> dict:
    from gwangyang_facilities import load_gwangyang_facilities

    row = await db.get(AppSetting, GWANGYANG_FACILITIES_SETTING_KEY)
    if row and row.value:
        try:
            payload = json.loads(row.value)
            if isinstance(payload, dict) and isinstance(payload.get("left"), list):
                return payload
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return load_gwangyang_facilities()


@router.get("/admin/dashboard/gwangyang-facilities")
async def gwangyang_facilities_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    return templates.TemplateResponse(
        request,
        "gwangyang_facilities.html",
        {
            "user": user,
            "data": await _load_effective_gwangyang_facilities(db),
            "can_import": can_edit(user),
        },
    )


@router.get("/admin/dashboard/gwangyang-facilities/export")
async def gwangyang_facilities_export(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from gwangyang_facilities import export_gwangyang_facilities_xlsx

    content = export_gwangyang_facilities_xlsx(
        await _load_effective_gwangyang_facilities(db)
    )
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="gwangyang_public_facilities.xlsx"'
        },
    )


@router.post("/admin/dashboard/gwangyang-facilities/import")
async def gwangyang_facilities_import(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from gwangyang_facilities import (
        GWANGYANG_FACILITIES_PATH,
        import_gwangyang_facilities_xlsx,
    )

    if not can_edit(user):
        raise HTTPException(403, "가져오기 권한이 없습니다.")
    form = await request.form(
        max_files=1,
        max_fields=5,
        max_part_size=GWANGYANG_EXCEL_MAX_BYTES,
    )
    upload = form.get("excel_file")
    filename = str(getattr(upload, "filename", "") or "")
    if not upload or not filename.lower().endswith(".xlsx"):
        return RedirectResponse(
            f"{GWANGYANG_FACILITIES_PATH}?error={quote('.xlsx 파일을 선택하세요.')}",
            status_code=303,
        )
    content = await upload.read()
    if not content or len(content) > GWANGYANG_EXCEL_MAX_BYTES:
        return RedirectResponse(
            f"{GWANGYANG_FACILITIES_PATH}?error={quote('엑셀 파일은 5MB 이하만 가져올 수 있습니다.')}",
            status_code=303,
        )
    try:
        payload, imported_count = import_gwangyang_facilities_xlsx(content)
    except ValueError as exc:
        return RedirectResponse(
            f"{GWANGYANG_FACILITIES_PATH}?error={quote(str(exc))}",
            status_code=303,
        )

    row = await db.get(AppSetting, GWANGYANG_FACILITIES_SETTING_KEY)
    encoded = json.dumps(payload, ensure_ascii=False)
    if row:
        row.value = encoded
    else:
        db.add(AppSetting(key=GWANGYANG_FACILITIES_SETTING_KEY, value=encoded))
    await db.commit()
    return RedirectResponse(
        f"{GWANGYANG_FACILITIES_PATH}?flash=imported&count={imported_count}",
        status_code=303,
    )


def _require_dashboard_admin(user: User) -> None:
    if user.role != UserRole.system_admin:
        raise HTTPException(403, "시스템관리자만 대시보드 설정에 접근할 수 있습니다.")


@router.get("/admin/dashboard/settings")
async def dashboard_settings_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
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
    site_metrics = [
        {
            "key": key,
            "label": SITE_METRIC_LABELS[key],
            "visible": cfg["site_metrics"].get(key, True),
        }
        for key in SITE_METRIC_KEYS
    ]
    site_order_items = await load_site_status(db)
    layout_meta = {
        "wide": {
            "label": "통합 운영 홈",
            "desc": "환영 배너·정비 요약·사업장 현황·일정·공지·전력·바로가기 통합 화면",
        },
        "ops": {
            "label": "운영 보드",
            "desc": "기존 운영 대시보드 화면으로 돌아갑니다",
        },
        "gallery": {
            "label": "시설 갤러리",
            "desc": "건물 사진 그리드",
        },
        "bento": {
            "label": "한눈에 보기",
            "desc": "주요 건물·할 일 요약",
        },
    }
    current_layout = "ops"
    try:
        row = await db.get(AppSetting, "dashboard.layout")
        val = (row.value or "").strip() if row else ""
        if val in layout_meta:
            current_layout = val
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "dashboard_settings.html",
        {
            "user": user,
            "widgets": widgets,
            "site_metrics": site_metrics,
            "can_edit": True,
            "is_admin": True,
            "site_order_items": site_order_items,
            "layout_meta": layout_meta,
            "current_layout": current_layout,
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/admin/dashboard/settings")
async def dashboard_settings_save(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
    form = await request.form()
    order_raw = str(form.get("order") or "")
    order = [k.strip() for k in order_raw.split(",") if k.strip() in DASH_WIDGET_KEYS]
    for k in DASH_WIDGET_KEYS:
        if k not in order:
            order.append(k)
    visible = {}
    for k in DASH_WIDGET_KEYS:
        # Starlette Form: getlist 마지막 값 사용 (hidden+checkbox 패턴 대비)
        vals = form.getlist(f"visible_{k}") if hasattr(form, "getlist") else []
        if vals:
            visible[k] = _as_bool(vals[-1], False)
        else:
            visible[k] = form.get(f"visible_{k}") in ("1", "on", "true", "True")
    site_metrics = {}
    for k in SITE_METRIC_KEYS:
        vals = form.getlist(f"site_metric_{k}") if hasattr(form, "getlist") else []
        if vals:
            site_metrics[k] = _as_bool(vals[-1], False)
        else:
            site_metrics[k] = form.get(f"site_metric_{k}") in ("1", "on", "true", "True")
    await set_dashboard_widget_config(
        db, {"order": order, "visible": visible, "site_metrics": site_metrics}
    )
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=saved", status_code=303)


@router.post("/admin/dashboard/settings/reset")
async def dashboard_settings_reset(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
    await set_dashboard_widget_config(db, DEFAULT_DASH_CONFIG)
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=reset", status_code=303)


@router.post("/admin/dashboard/settings/site-order")
async def dashboard_site_order_save(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
    form = await request.form()
    order_raw = str(form.get("site_order") or "")
    ids: list[int] = []
    for part in order_raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    await set_site_status_order(db, ids)
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=site_order_saved", status_code=303)


@router.post("/admin/dashboard/settings/site-order/reset")
async def dashboard_site_order_reset(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
    await set_site_status_order(db, [])
    await db.commit()
    return RedirectResponse("/admin/dashboard/settings?flash=site_order_reset", status_code=303)


@router.get("/admin/dashboard/site-thumb/{site_id}")
async def dashboard_site_thumb_file(
    site_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site or not site.is_active:
        raise HTTPException(404, detail="사업장을 찾을 수 없습니다.")
    blob = await load_site_thumb_blob(db, site_id)
    if blob is None:
        raise HTTPException(404, detail="등록된 사진이 없습니다.")
    content, mime = blob
    return Response(
        content=content,
        media_type=mime or "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/admin/dashboard/settings/site-thumb/{site_id}")
async def dashboard_site_thumb_upload(
    site_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    _require_dashboard_admin(user)
    site = await db.get(Site, site_id)
    if not site or not site.is_active:
        raise HTTPException(404, detail="사업장을 찾을 수 없습니다.")
    filename = (file.filename or "").strip()
    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix not in SITE_THUMB_EXTS:
        raise HTTPException(400, detail="jpg·png·webp·gif·pdf만 업로드할 수 있습니다.")
    content = await file.read()
    if not content:
        raise HTTPException(400, detail="빈 파일입니다.")
    try:
        url = await save_site_status_photo_upload(db, site_id, content, suffix)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    return JSONResponse({"ok": True, "image": url, "site_id": site_id})
