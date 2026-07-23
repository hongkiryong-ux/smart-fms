# main.py
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    ROLE_LABELS,
    get_current_user,
    hash_password,
    require_login,
    verify_password,
)
from database import AsyncSessionLocal, Base, engine, get_db, ensure_schema_updates
from init_data import seed_if_empty
from models import (
    Building,
    Consumable,
    D1Plan,
    D1Status,
    Equipment,
    EquipmentTemplate,
    EquipmentType,
    Floor,
    InventoryItem,
    MaintenanceRecord,
    Partner,
    PMSchedule,
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)

KST = ZoneInfo("Asia/Seoul")


def _today_kst() -> date:
    return datetime.now(KST).date()


def _fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_kst_date(dt: datetime | date | None) -> str:
    """등록일 등 연-월-일만 표시."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def _status_label(status: WorkOrderStatus | str) -> str:
    labels = {
        "received": "정비의뢰",
        "assigned": "정비의뢰",
        "in_progress": "정비진행",
        "completed": "정비완료",
        "verified": "정비완료",
        "closed": "정비완료",
    }
    key = status.value if isinstance(status, WorkOrderStatus) else str(status)
    return labels.get(key, key)


def _wo_process_step(status: WorkOrderStatus | str) -> int:
    """1=정비의뢰, 2=정비중, 3=정비완료."""
    key = status.value if isinstance(status, WorkOrderStatus) else str(status)
    if key in ("completed", "verified", "closed"):
        return 3
    if key == "in_progress":
        return 2
    return 1


async def _ensure_maintenance_history(db: AsyncSession, wo: WorkOrder) -> None:
    """정비완료 시 설비 정비이력 자동 등록 (중복 방지)."""
    if not wo.equipment_id:
        return
    existing = (
        await db.execute(
            select(MaintenanceRecord).where(MaintenanceRecord.work_order_id == wo.id)
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        MaintenanceRecord(
            equipment_id=wo.equipment_id,
            work_order_id=wo.id,
            title=wo.title,
            work_date=(wo.completed_at or datetime.utcnow()).date(),
            worker_name=wo.assignee_name,
            cause=wo.cause,
            action=wo.action,
            parts_used=wo.parts_used,
            work_hours=wo.work_hours,
            cost=wo.cost,
            is_manual=False,
        )
    )


def _d1_status_label(status: D1Status) -> str:
    return {
        D1Status.draft: "작성중",
        D1Status.review: "검토",
        D1Status.approved: "승인",
        D1Status.jsa_pending: "JSA 대기",
        D1Status.tbm_pending: "TBM 대기",
        D1Status.permit_pending: "작업허가 대기",
        D1Status.in_progress: "작업중",
        D1Status.completed: "완료",
    }.get(status, status.value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """DB 초기화 실패해도 앱은 기동시켜 health check / 재시도 가능하게 함."""
    import os as _os

    _os.environ.setdefault("LAW_WEB_SEARCH", "0")
    last_err: Exception | None = None
    for attempt in range(1, 6):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await ensure_schema_updates()
            async with AsyncSessionLocal() as session:
                await seed_if_empty(session)
            print(f"[startup] DB ready (attempt {attempt})", flush=True)
            last_err = None
            break
        except Exception as e:
            last_err = e
            print(f"[startup] DB init failed ({attempt}/5): {e}", flush=True)
            if attempt < 5:
                import asyncio

                await asyncio.sleep(3)
    if last_err is not None:
        # 배포(health check)가 막히지 않도록 예외를 삼키고 기동 계속
        print(
            f"[startup] WARNING: continuing without full DB init: {last_err}",
            flush=True,
        )
    yield


app = FastAPI(title="POSCO WIDE Smart FMS", lifespan=lifespan)

SECRET_KEY = os.environ.get("APP_SECRET_KEY", "change_this_secret_in_prod")
_session_kw: dict = {"secret_key": SECRET_KEY, "same_site": "lax"}
if os.environ.get("RENDER", "").lower() in ("true", "1", "yes") or os.environ.get(
    "COOKIE_HTTPS_ONLY", ""
).lower() in ("1", "true", "yes"):
    _session_kw["https_only"] = True
app.add_middleware(SessionMiddleware, **_session_kw)

try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except ImportError:
    pass

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

from equipment_schema import field_value, get_category_fields, list_display_fields, NAME_KEYS

templates.env.globals["field_value"] = field_value
templates.env.globals["name_fields"] = set(NAME_KEYS)
templates.env.globals.update(
    fmt_kst=_fmt_kst,
    fmt_kst_date=_fmt_kst_date,
    role_labels=ROLE_LABELS,
    wo_status_label=_status_label,
    wo_process_step=_wo_process_step,
    d1_status_label=_d1_status_label,
)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    # 미로그인 → 로그인 페이지로 이동
    if exc.status_code in (401, 303) and (
        exc.detail == "login_required"
        or (exc.headers or {}).get("Location") == "/admin/login"
        or (exc.headers or {}).get("X-Redirect") == "/admin/login"
    ):
        return RedirectResponse("/admin/login", status_code=303)
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    import traceback

    # HTTPException은 전용 핸들러로
    if isinstance(exc, HTTPException):
        return await _http_exception_handler(request, exc)

    print(f"[error] {request.method} {request.url.path}: {exc}", flush=True)
    traceback.print_exc()
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept or str(request.url.path).startswith("/admin"):
        try:
            return templates.TemplateResponse(
                request,
                "error.html",
                {
                    "user": None,
                    "status_code": 500,
                    "message": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                    "detail": str(exc)[:500],
                },
                status_code=500,
            )
        except Exception:
            pass
    return JSONResponse(
        {"detail": "internal_error", "message": str(exc)[:300]},
        status_code=500,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "smart-fms"}


# ── Auth ──────────────────────────────────────────────────────────────


@app.get("/admin/login")
async def admin_login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/admin/dashboard", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": request.query_params.get("error")}
    )


@app.post("/admin/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.username == username.strip(), User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse("/admin/login?error=1", status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse("/admin/dashboard", status_code=303)


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


# ── Dashboard ─────────────────────────────────────────────────────────


@app.get("/admin/dashboard")
async def dashboard(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    # 활성 사업장/건물/설비만 실시간 집계 (추가·삭제 시 자동 반영)
    site_count = (
        await db.execute(select(func.count(Site.id)).where(Site.is_active == True))
    ).scalar() or 0
    building_count = (
        await db.execute(
            select(func.count(Building.id))
            .join(Site, Building.site_id == Site.id)
            .where(Building.is_active == True, Site.is_active == True)
        )
    ).scalar() or 0
    equipment_count = (
        await db.execute(
            select(func.count(Equipment.id))
            .join(Zone, Equipment.zone_id == Zone.id)
            .join(Floor, Zone.floor_id == Floor.id)
            .join(Building, Floor.building_id == Building.id)
            .join(Site, Building.site_id == Site.id)
            .where(
                Equipment.is_active == True,
                Building.is_active == True,
                Site.is_active == True,
            )
        )
    ).scalar() or 0
    wo_total = (await db.execute(select(func.count(WorkOrder.id)))).scalar() or 0
    wo_progress = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.status.in_(
                    [WorkOrderStatus.received, WorkOrderStatus.assigned, WorkOrderStatus.in_progress]
                )
            )
        )
    ).scalar() or 0
    wo_done = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.status.in_(
                    [
                        WorkOrderStatus.completed,
                        WorkOrderStatus.verified,
                        WorkOrderStatus.closed,
                    ]
                )
            )
        )
    ).scalar() or 0
    wo_urgent = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.priority == "high",
                WorkOrder.status.in_(
                    [
                        WorkOrderStatus.received,
                        WorkOrderStatus.assigned,
                        WorkOrderStatus.in_progress,
                    ]
                ),
            )
        )
    ).scalar() or 0
    pm_due = (
        await db.execute(
            select(func.count(PMSchedule.id)).where(
                PMSchedule.next_due <= date.today(), PMSchedule.is_active == True
            )
        )
    ).scalar() or 0
    consumable_due = (
        await db.execute(
            select(func.count(Consumable.id)).where(
                Consumable.next_replace <= date.today()
            )
        )
    ).scalar() or 0
    d1_tomorrow = (
        await db.execute(
            select(func.count(D1Plan.id)).where(D1Plan.work_date == date.today())
        )
    ).scalar() or 0

    recent_wo = (
        await db.execute(
            select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(5)
        )
    ).scalars().all()
    upcoming_pm = (
        await db.execute(
            select(PMSchedule)
            .where(PMSchedule.is_active == True)
            .order_by(PMSchedule.next_due.asc())
            .limit(5)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "kpi": {
                "sites": site_count,
                "buildings": building_count,
                "equipment": equipment_count,
                "wo_total": wo_total,
                "wo_progress": wo_progress,
                "wo_done": wo_done,
                "wo_urgent": wo_urgent,
                "pm_due": pm_due,
                "consumable_due": consumable_due,
                "d1_today": d1_tomorrow,
            },
            "recent_wo": recent_wo,
            "upcoming_pm": upcoming_pm,
        },
    )


# ── Sites & Hierarchy ─────────────────────────────────────────────────


@app.get("/admin/sites")
async def sites_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Site)
        .where(Site.is_active == True)
        .options(selectinload(Site.buildings))
        .order_by(Site.name)
    )
    sites = result.scalars().all()
    return templates.TemplateResponse(
        request, "sites.html", {"user": user, "sites": sites}
    )


@app.post("/admin/sites")
async def site_create(
    name: str = Form(...),
    code: str = Form(...),
    address: str = Form(""),
    manager_name: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    site = Site(name=name.strip(), code=code.strip(), address=address, manager_name=manager_name)
    db.add(site)
    await db.commit()
    return RedirectResponse("/admin/sites", status_code=303)


@app.get("/admin/sites/{site_id}/edit")
async def site_edit_page(
    site_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site or not site.is_active:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "site_edit.html", {"user": user, "site": site}
    )


@app.post("/admin/sites/{site_id}/edit")
async def site_edit(
    site_id: int,
    name: str = Form(...),
    code: str = Form(...),
    address: str = Form(""),
    manager_name: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site or not site.is_active:
        raise HTTPException(404)
    site.name = name.strip()
    site.code = code.strip()
    site.address = address
    site.manager_name = manager_name
    await db.commit()
    return RedirectResponse("/admin/sites", status_code=303)


@app.post("/admin/sites/{site_id}/delete")
async def site_delete(
    site_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site:
        raise HTTPException(404)
    site.is_active = False
    result = await db.execute(select(Building).where(Building.site_id == site_id))
    for b in result.scalars().all():
        b.is_active = False
    await db.commit()
    return RedirectResponse("/admin/sites", status_code=303)


@app.get("/admin/buildings/{building_id}")
async def building_detail(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Building)
        .where(Building.id == building_id)
        .options(
            selectinload(Building.site),
            selectinload(Building.floors).selectinload(Floor.zones),
        )
    )
    building = result.scalar_one_or_none()
    if not building or not building.is_active:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "building_detail.html", {"user": user, "building": building}
    )


@app.post("/admin/buildings")
async def building_create(
    site_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    db.add(Building(site_id=site_id, name=name.strip(), code=code.strip()))
    await db.commit()
    return RedirectResponse("/admin/sites", status_code=303)


@app.get("/admin/buildings/{building_id}/edit")
async def building_edit_page(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Building)
        .where(Building.id == building_id)
        .options(selectinload(Building.site))
    )
    building = result.scalar_one_or_none()
    if not building or not building.is_active:
        raise HTTPException(404)
    sites = (
        await db.execute(select(Site).where(Site.is_active == True).order_by(Site.name))
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "building_edit.html",
        {"user": user, "building": building, "sites": sites},
    )


@app.post("/admin/buildings/{building_id}/edit")
async def building_edit(
    building_id: int,
    site_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    manager_name: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    building = await db.get(Building, building_id)
    if not building or not building.is_active:
        raise HTTPException(404)
    building.site_id = site_id
    building.name = name.strip()
    building.code = code.strip()
    building.manager_name = manager_name
    await db.commit()
    return RedirectResponse(f"/admin/buildings/{building_id}", status_code=303)


@app.post("/admin/buildings/{building_id}/delete")
async def building_delete(
    building_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    building = await db.get(Building, building_id)
    if not building:
        raise HTTPException(404)
    building.is_active = False
    await db.commit()
    return RedirectResponse("/admin/sites", status_code=303)


@app.post("/admin/floors")
async def floor_create(
    building_id: int = Form(...),
    name: str = Form(...),
    level: int = Form(1),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    db.add(Floor(building_id=building_id, name=name.strip(), level=level))
    await db.commit()
    return RedirectResponse(f"/admin/buildings/{building_id}", status_code=303)


@app.post("/admin/zones")
async def zone_create(
    floor_id: int = Form(...),
    building_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    db.add(Zone(floor_id=floor_id, name=name.strip(), code=code))
    await db.commit()
    return RedirectResponse(f"/admin/buildings/{building_id}", status_code=303)


# ── Equipment ─────────────────────────────────────────────────────────

async def _building_categories(db: AsyncSession, building_id: int) -> list[str]:
    """건물별 엑셀 시트(카테고리) 목록."""
    rows = await db.execute(
        select(Equipment.category)
        .join(Zone)
        .join(Floor)
        .where(Floor.building_id == building_id, Equipment.is_active == True)
        .distinct()
        .order_by(Equipment.category)
    )
    return [r[0] for r in rows.all() if r[0]]


async def _building_category_counts(
    db: AsyncSession, building_id: int
) -> dict[str, int]:
    count_q = await db.execute(
        select(Equipment.category, func.count(Equipment.id))
        .join(Zone)
        .join(Floor)
        .where(Floor.building_id == building_id, Equipment.is_active == True)
        .group_by(Equipment.category)
    )
    return {cat: cnt for cat, cnt in count_q.all() if cat}


@app.get("/admin/equipment")
async def equipment_list(
    request: Request,
    building_id: int | None = None,
    category: str | None = None,
    error: str | None = None,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    buildings = (
        await db.execute(
            select(Building)
            .where(Building.is_active == True)
            .options(selectinload(Building.site))
            .order_by(Building.name)
        )
    ).scalars().all()

    selected_building = None
    categories: list[str] = []
    category_counts: dict[str, int] = {}
    equipment: list = []
    zones = []
    sheet_fields: list[str] = []
    list_columns: list[str] = []

    if building_id:
        selected_building = await db.get(Building, building_id)
        if selected_building and not selected_building.is_active:
            selected_building = None
        if selected_building:
            category_counts = await _building_category_counts(db, building_id)
            categories = sorted(category_counts.keys())

            if category and category in categories:
                try:
                    result = await db.execute(
                        select(Equipment)
                        .join(Zone)
                        .join(Floor)
                        .where(
                            Floor.building_id == building_id,
                            Equipment.category == category,
                            Equipment.is_active == True,
                        )
                        .options(
                            selectinload(Equipment.zone)
                            .selectinload(Zone.floor)
                            .selectinload(Floor.building),
                            selectinload(Equipment.equipment_type),
                            selectinload(Equipment.work_orders),
                            selectinload(Equipment.maintenance_records),
                        )
                        .order_by(Equipment.code)
                    )
                    equipment = result.scalars().all()
                except Exception as e:
                    print(f"[equipment_list] fallback load: {e}", flush=True)
                    await db.rollback()
                    result = await db.execute(
                        select(Equipment)
                        .join(Zone)
                        .join(Floor)
                        .where(
                            Floor.building_id == building_id,
                            Equipment.category == category,
                            Equipment.is_active == True,
                        )
                        .options(
                            selectinload(Equipment.zone)
                            .selectinload(Zone.floor)
                            .selectinload(Floor.building),
                            selectinload(Equipment.work_orders),
                        )
                        .order_by(Equipment.code)
                    )
                    equipment = result.scalars().all()

                # 해당 건물 구역만
                zones = (
                    await db.execute(
                        select(Zone)
                        .join(Floor)
                        .where(Floor.building_id == building_id)
                        .order_by(Zone.name)
                    )
                ).scalars().all()
                sheet_fields = get_category_fields(category, equipment)
                list_columns = list_display_fields(category, equipment)

    return templates.TemplateResponse(
        request,
        "equipment.html",
        {
            "user": user,
            "buildings": buildings,
            "selected_building": selected_building,
            "building_id": building_id,
            "category": category if category in categories else None,
            "categories": categories,
            "category_counts": category_counts,
            "equipment": equipment,
            "zones": zones,
            "sheet_fields": sheet_fields,
            "list_columns": list_columns,
            "error": error,
        },
    )


@app.post("/admin/equipment")
async def equipment_create(
    request: Request,
    zone_id: int = Form(...),
    code: str = Form(""),
    category: str = Form(""),
    building_id: int = Form(0),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote
    from sqlalchemy.exc import IntegrityError
    from equipment_schema import (
        merge_extra_for_save,
        parse_extra_form,
        resolve_core_fields,
    )
    from excel_import import _equipment_code

    form = await request.form()
    extra = parse_extra_form(form)
    cat = category.strip() if category else "기타"
    code_val = code.strip()
    name_val, manufacturer, model, serial_no = resolve_core_fields(extra)
    bld_id = building_id if building_id > 0 else 0

    def _list_url(error: str | None = None) -> str:
        if bld_id:
            url = f"/admin/equipment?building_id={bld_id}&category={quote(cat)}"
        else:
            url = "/admin/equipment"
        if error:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}error={quote(error)}"
        return url

    if not name_val:
        return RedirectResponse(_list_url("구분/명칭을 입력하세요."), status_code=303)
    if zone_id <= 0:
        return RedirectResponse(_list_url("구역을 선택하세요."), status_code=303)

    zone = await db.get(Zone, zone_id)
    if not zone:
        return RedirectResponse(_list_url("선택한 구역이 없습니다."), status_code=303)

    if not code_val and bld_id:
        building = await db.get(Building, bld_id)
        if building:
            count = (
                await db.execute(
                    select(func.count(Equipment.id))
                    .join(Zone)
                    .join(Floor)
                    .where(Floor.building_id == bld_id, Equipment.category == cat)
                )
            ).scalar() or 0
            code_val = _equipment_code(building.code, cat, count + 1, name_val)

    if not code_val:
        return RedirectResponse(_list_url("코드를 입력하세요."), status_code=303)

    extra = merge_extra_for_save(extra, name_val, manufacturer, model, serial_no)

    existing = (
        await db.execute(select(Equipment).where(Equipment.code == code_val))
    ).scalar_one_or_none()

    try:
        if existing and existing.is_active:
            return RedirectResponse(
                _list_url(f"이미 사용 중인 코드입니다: {code_val}"),
                status_code=303,
            )

        if existing and not existing.is_active:
            eq = existing
            eq.is_active = True
            eq.zone_id = zone_id
            eq.name = name_val
            eq.category = cat
            eq.manufacturer = manufacturer or None
            eq.model = model or None
            eq.serial_no = serial_no or None
            eq.extra_data = extra
            eq.status = "normal"
        else:
            eq = Equipment(
                zone_id=zone_id,
                code=code_val,
                name=name_val,
                category=cat,
                manufacturer=manufacturer or None,
                model=model or None,
                serial_no=serial_no or None,
                extra_data=extra,
            )
            db.add(eq)

        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            _list_url(f"등록 실패: 코드 중복 또는 DB 제약 오류 ({code_val})"),
            status_code=303,
        )
    except Exception as e:
        await db.rollback()
        print(f"[equipment_create] error: {e}", flush=True)
        return RedirectResponse(
            _list_url(f"등록 실패: {e}"),
            status_code=303,
        )

    return RedirectResponse(_list_url(), status_code=303)


@app.get("/admin/equipment/import")
async def equipment_import_page(
    request: Request,
    building_id: int | None = None,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    buildings = (
        await db.execute(
            select(Building)
            .where(Building.is_active == True)
            .options(selectinload(Building.site))
            .order_by(Building.name)
        )
    ).scalars().all()
    selected = await db.get(Building, building_id) if building_id else None
    return templates.TemplateResponse(
        request,
        "equipment_import.html",
        {
            "user": user,
            "buildings": buildings,
            "selected_building": selected,
            "message": message,
            "error": error,
        },
    )


@app.post("/admin/equipment/import")
async def equipment_import_upload(
    request: Request,
    file: UploadFile = File(...),
    building_id: int = Form(...),
    replace: str = Form("0"),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote
    import tempfile
    from excel_import import import_excel_to_building

    building = await db.get(Building, building_id)
    if not building or not building.is_active:
        return RedirectResponse(
            "/admin/equipment/import?error=" + quote("건물을 찾을 수 없습니다."),
            status_code=303,
        )

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xls", ".xlsx"):
        return RedirectResponse(
            f"/admin/equipment/import?building_id={building_id}&error="
            + quote("xls 또는 xlsx 파일만 업로드할 수 있습니다."),
            status_code=303,
        )

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        stats = await import_excel_to_building(
            db,
            building.name,
            tmp_path,
            replace=replace == "1",
        )
        msg = f"시트 {stats['sheets']}개 · 신규 {stats['created']}건 · 갱신 {stats['updated']}건"
        return RedirectResponse(
            f"/admin/equipment/import?building_id={building_id}&message={quote(msg)}",
            status_code=303,
        )
    except Exception as e:
        print(f"[equipment_import] error: {e}", flush=True)
        return RedirectResponse(
            f"/admin/equipment/import?building_id={building_id}&error={quote(str(e))}",
            status_code=303,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.get("/admin/equipment/export/{building_id}")
async def equipment_export(
    building_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote
    from excel_import import export_building_excel

    building = await db.get(Building, building_id)
    if not building:
        raise HTTPException(404)

    result = await db.execute(
        select(Equipment)
        .join(Zone)
        .join(Floor)
        .where(Floor.building_id == building_id, Equipment.is_active == True)
        .options(selectinload(Equipment.maintenance_records))
        .order_by(Equipment.category, Equipment.code)
    )
    items = result.scalars().unique().all()

    by_sheet: dict[str, list] = {}
    for eq in items:
        by_sheet.setdefault(eq.category or "기타", []).append(eq)

    data = export_building_excel(building.name, by_sheet)
    fname = quote(f"{building.name}_설비현황.xlsx")
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@app.post("/admin/equipment/bulk-import")
async def equipment_bulk_import(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """네트워크 공유 또는 data 폴더에서 일괄 import."""
    from urllib.parse import quote
    from excel_import import import_from_directory

    candidates = [
        Path(r"\\poscowide1\홍기룡\202010 설비현황"),
        Path("data/excel"),
        Path("data"),
    ]
    directory = next((p for p in candidates if p.is_dir()), None)
    if not directory:
        return RedirectResponse(
            "/admin/equipment/import?error=" + quote("import 대상 폴더를 찾을 수 없습니다."),
            status_code=303,
        )

    try:
        results = await import_from_directory(db, directory, replace=True)
        msg = (
            f"건물 {results['buildings']}개 · 신규 {results['total_created']}건 · "
            f"갱신 {results['total_updated']}건"
        )
        if results["errors"]:
            msg += f" · 오류 {len(results['errors'])}건"
        return RedirectResponse(
            f"/admin/equipment/import?message={quote(msg)}",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            f"/admin/equipment/import?error={quote(str(e))}",
            status_code=303,
        )


@app.get("/admin/equipment/{eq_id}")
async def equipment_detail(
    eq_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.id == eq_id, Equipment.is_active == True)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building),
            selectinload(Equipment.consumables),
            selectinload(Equipment.pm_schedules),
            selectinload(Equipment.work_orders),
            selectinload(Equipment.maintenance_records),
            selectinload(Equipment.equipment_type),
            selectinload(Equipment.template),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404)
    base_url = os.environ.get("PUBLIC_BASE_URL", str(request.base_url).rstrip("/"))
    sheet_fields = get_category_fields(eq.category, [eq])
    history = sorted(
        eq.maintenance_records or [],
        key=lambda r: (r.work_date or date.min, r.id),
        reverse=True,
    )
    open_orders = [
        wo
        for wo in (eq.work_orders or [])
        if wo.status
        not in (WorkOrderStatus.completed, WorkOrderStatus.verified, WorkOrderStatus.closed)
    ]
    return templates.TemplateResponse(
        request,
        "equipment_detail.html",
        {
            "user": user,
            "eq": eq,
            "qr_url": f"{base_url}/eq/{eq.code}",
            "sheet_fields": sheet_fields,
            "history": history,
            "open_orders": open_orders,
        },
    )


@app.get("/admin/equipment/{eq_id}/popup")
async def equipment_popup(
    eq_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.id == eq_id, Equipment.is_active == True)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building),
            selectinload(Equipment.work_orders),
            selectinload(Equipment.maintenance_records),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404)
    sheet_fields = get_category_fields(eq.category, [eq])
    history = sorted(
        eq.maintenance_records or [],
        key=lambda r: (r.work_date or date.min, r.id),
        reverse=True,
    )[:10]
    open_orders = [
        wo
        for wo in (eq.work_orders or [])
        if wo.status
        not in (WorkOrderStatus.completed, WorkOrderStatus.verified, WorkOrderStatus.closed)
    ]
    return templates.TemplateResponse(
        request,
        "partials/equipment_popup.html",
        {
            "user": user,
            "eq": eq,
            "sheet_fields": sheet_fields,
            "history": history,
            "open_orders": open_orders,
        },
    )


@app.post("/admin/equipment/{eq_id}/maintenance-request")
async def equipment_maintenance_request(
    eq_id: int,
    title: str = Form(""),
    description: str = Form(""),
    priority: str = Form("normal"),
    assignee_name: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    eq = (
        await db.execute(
            select(Equipment)
            .where(Equipment.id == eq_id, Equipment.is_active == True)
            .options(
                selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building)
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(404)

    site_id = None
    if eq.zone and eq.zone.floor and eq.zone.floor.building:
        site_id = eq.zone.floor.building.site_id

    wo_title = title.strip() or f"[정비의뢰] {eq.code} {eq.name}"
    wo = WorkOrder(
        title=wo_title,
        description=description.strip() or f"{eq.category} 설비 정비의뢰",
        priority=priority,
        assignee_name=assignee_name.strip() or None,
        equipment_id=eq.id,
        site_id=site_id,
        status=WorkOrderStatus.received,
        work_type="정비",
    )
    db.add(wo)
    await db.commit()
    await db.refresh(wo)
    return RedirectResponse(f"/admin/work-orders/{wo.id}", status_code=303)


@app.post("/admin/equipment/{eq_id}/history")
async def equipment_history_create(
    eq_id: int,
    title: str = Form(...),
    work_date: str = Form(...),
    worker_name: str = Form(""),
    cause: str = Form(""),
    action: str = Form(""),
    parts_used: str = Form(""),
    note: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    eq = await db.get(Equipment, eq_id)
    if not eq or not eq.is_active:
        raise HTTPException(404)
    try:
        wd = date.fromisoformat(work_date)
    except ValueError:
        wd = date.today()
    db.add(
        MaintenanceRecord(
            equipment_id=eq_id,
            title=title.strip(),
            work_date=wd,
            worker_name=worker_name.strip() or None,
            cause=cause.strip() or None,
            action=action.strip() or None,
            parts_used=parts_used.strip() or None,
            note=note.strip() or None,
            is_manual=True,
        )
    )
    await db.commit()
    return RedirectResponse(f"/admin/equipment/{eq_id}", status_code=303)


@app.get("/admin/equipment/{eq_id}/edit")
async def equipment_edit_page(
    eq_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.id == eq_id, Equipment.is_active == True)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building),
            selectinload(Equipment.equipment_type),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404)

    building_id = eq.zone.floor.building_id if eq.zone and eq.zone.floor else 0
    zones = []
    if building_id:
        zones = (
            await db.execute(
                select(Zone)
                .join(Floor)
                .where(Floor.building_id == building_id)
                .order_by(Zone.name)
            )
        ).scalars().all()
    bld_cats = await _building_categories(db, building_id) if building_id else []
    if eq.category and eq.category not in bld_cats:
        bld_cats = [eq.category] + bld_cats
    sheet_fields = get_category_fields(eq.category, [eq])
    field_values = {f: field_value(eq, f) for f in sheet_fields}
    return templates.TemplateResponse(
        request,
        "equipment_edit.html",
        {
            "user": user,
            "eq": eq,
            "zones": zones,
            "categories": bld_cats or [eq.category],
            "building_id": building_id,
            "sheet_fields": sheet_fields,
            "field_values": field_values,
            "category": eq.category,
        },
    )


@app.post("/admin/equipment/{eq_id}/edit")
async def equipment_edit(
    eq_id: int,
    request: Request,
    zone_id: int = Form(...),
    code: str = Form(...),
    category: str = Form(""),
    status: str = Form("normal"),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from equipment_schema import merge_extra_for_save, parse_extra_form, resolve_core_fields

    eq = await db.get(Equipment, eq_id)
    if not eq or not eq.is_active:
        raise HTTPException(404)

    form = await request.form()
    extra = parse_extra_form(form)
    cat = category.strip() if category else eq.category
    name_val, manufacturer, model, serial_no = resolve_core_fields(extra, eq.name)

    if not name_val:
        name_val = eq.name

    extra = merge_extra_for_save(extra, name_val, manufacturer, model, serial_no)

    eq.zone_id = zone_id
    eq.code = code.strip()
    eq.name = name_val
    eq.category = cat
    eq.manufacturer = manufacturer or None
    eq.model = model or None
    eq.serial_no = serial_no or None
    eq.extra_data = extra
    eq.status = status
    await db.commit()

    building_id = 0
    zone = await db.get(Zone, zone_id)
    if zone:
        floor = await db.get(Floor, zone.floor_id)
        if floor:
            building_id = floor.building_id
    if building_id:
        return RedirectResponse(
            f"/admin/equipment?building_id={building_id}&category={cat}",
            status_code=303,
        )
    return RedirectResponse(f"/admin/equipment/{eq_id}", status_code=303)


@app.post("/admin/equipment/{eq_id}/delete")
async def equipment_delete(
    eq_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.id == eq_id)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404)
    building_id = eq.zone.floor.building_id if eq.zone and eq.zone.floor else 0
    cat = eq.category
    eq.is_active = False
    await db.commit()
    if building_id:
        return RedirectResponse(
            f"/admin/equipment?building_id={building_id}&category={cat}",
            status_code=303,
        )
    return RedirectResponse("/admin/equipment", status_code=303)


# ── Equipment Templates ───────────────────────────────────────────────


@app.get("/admin/templates")
async def templates_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    tpls = (
        await db.execute(
            select(EquipmentTemplate)
            .options(selectinload(EquipmentTemplate.equipment_type))
            .order_by(EquipmentTemplate.name)
        )
    ).scalars().all()
    types = (await db.execute(select(EquipmentType).order_by(EquipmentType.name))).scalars().all()
    return templates.TemplateResponse(
        request, "templates.html", {"user": user, "templates": tpls, "types": types}
    )


@app.post("/admin/templates")
async def template_create(
    equipment_type_id: int = Form(...),
    name: str = Form(...),
    manufacturer: str = Form(""),
    model: str = Form(""),
    pm_items: str = Form(""),
    consumables: str = Form(""),
    pm_cycle_days: int = Form(30),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    pm_list = [x.strip() for x in pm_items.split("\n") if x.strip()]
    cons_list = []
    for line in consumables.split("\n"):
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if parts:
            item = {"name": parts[0]}
            if len(parts) > 1:
                try:
                    item["interval_days"] = int(parts[1])
                except ValueError:
                    pass
            cons_list.append(item)

    db.add(
        EquipmentTemplate(
            equipment_type_id=equipment_type_id,
            name=name.strip(),
            manufacturer=manufacturer,
            model=model,
            pm_items=pm_list,
            consumables=cons_list,
            pm_cycle_days=pm_cycle_days,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/templates", status_code=303)


@app.post("/admin/equipment-types")
async def equipment_type_create(
    name: str = Form(...),
    category: str = Form("설비"),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    cat = category.strip() if category else "기타"
    db.add(EquipmentType(name=name.strip(), category=cat))
    await db.commit()
    return RedirectResponse("/admin/templates", status_code=303)


# ── Work Orders (CMMS) ─────────────────────────────────────────────────


@app.get("/admin/work-orders")
async def work_orders_list(
    request: Request,
    q: str = "",
    status: list[str] = Query(default=[]),
    priority: str = "",
    date_from: str = "",
    date_to: str = "",
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    q_val = (q or "").strip()
    # 확인란 다중선택 + 콤마 구분(리다이렉트 호환)
    allowed_status = {"received", "in_progress", "completed"}
    status_vals: list[str] = []
    seen_status: set[str] = set()
    for raw in status or []:
        for part in str(raw).split(","):
            key = part.strip()
            if key in allowed_status and key not in seen_status:
                seen_status.add(key)
                status_vals.append(key)
    priority_val = (priority or "").strip()
    date_from_val = (date_from or "").strip()
    date_to_val = (date_to or "").strip()

    stmt = (
        select(WorkOrder)
        .outerjoin(Equipment, WorkOrder.equipment_id == Equipment.id)
        .options(selectinload(WorkOrder.equipment), selectinload(WorkOrder.partner))
    )
    filters = []

    if q_val:
        like = f"%{q_val}%"
        filters.append(
            or_(
                WorkOrder.title.ilike(like),
                WorkOrder.description.ilike(like),
                WorkOrder.action.ilike(like),
                WorkOrder.assignee_name.ilike(like),
                Equipment.code.ilike(like),
                Equipment.name.ilike(like),
            )
        )

    if status_vals:
        status_conds = []
        if "received" in status_vals:
            status_conds.append(
                WorkOrder.status.in_(
                    [WorkOrderStatus.received, WorkOrderStatus.assigned]
                )
            )
        if "in_progress" in status_vals:
            status_conds.append(WorkOrder.status == WorkOrderStatus.in_progress)
        if "completed" in status_vals:
            status_conds.append(
                WorkOrder.status.in_(
                    [
                        WorkOrderStatus.completed,
                        WorkOrderStatus.verified,
                        WorkOrderStatus.closed,
                    ]
                )
            )
        if status_conds:
            filters.append(or_(*status_conds))

    if priority_val in ("normal", "high"):
        filters.append(WorkOrder.priority == priority_val)

    if date_from_val:
        try:
            filters.append(
                WorkOrder.created_at
                >= datetime.fromisoformat(date_from_val).replace(tzinfo=None)
            )
        except ValueError:
            pass
    if date_to_val:
        try:
            # 종료일 포함 (다음날 0시 미만)
            end = datetime.fromisoformat(date_to_val).replace(tzinfo=None)
            filters.append(WorkOrder.created_at < end.replace(hour=23, minute=59, second=59, microsecond=999999))
        except ValueError:
            pass

    if filters:
        stmt = stmt.where(and_(*filters))

    orders = (
        await db.execute(stmt.order_by(WorkOrder.created_at.desc()))
    ).scalars().unique().all()

    partners = (
        await db.execute(
            select(Partner).where(Partner.is_active == True).order_by(Partner.name)
        )
    ).scalars().all()

    buildings = (
        await db.execute(
            select(Building)
            .join(Site, Building.site_id == Site.id)
            .where(Building.is_active == True, Site.is_active == True)
            .order_by(Building.name)
        )
    ).scalars().all()
    equipment = (
        await db.execute(
            select(Equipment)
            .where(Equipment.is_active == True)
            .options(
                selectinload(Equipment.zone)
                .selectinload(Zone.floor)
                .selectinload(Floor.building)
            )
            .order_by(Equipment.code)
        )
    ).scalars().all()
    # 건물별 설비 옵션용 building_id 매핑
    equipment_opts = []
    for eq in equipment:
        bld_id = 0
        if eq.zone and eq.zone.floor and eq.zone.floor.building:
            bld_id = eq.zone.floor.building.id
        equipment_opts.append(
            {
                "id": eq.id,
                "code": eq.code,
                "name": eq.name,
                "category": eq.category or "",
                "building_id": bld_id,
            }
        )
    return templates.TemplateResponse(
        request,
        "work_orders.html",
        {
            "user": user,
            "orders": orders,
            "partners": partners,
            "buildings": buildings,
            "equipment_opts": equipment_opts,
            "equipment_opts_json": json.dumps(equipment_opts, ensure_ascii=False),
            "filters": {
                "q": q_val,
                "status": ",".join(status_vals),
                "statuses": status_vals,
                "priority": priority_val,
                "date_from": date_from_val,
                "date_to": date_to_val,
            },
            "result_count": len(orders),
        },
    )


@app.post("/admin/work-orders")
async def work_order_create(
    equipment_id: int = Form(...),
    description: str = Form(...),
    priority: str = Form("normal"),
    assignee_name: str = Form(""),
    scheduled_date: str = Form(""),
    partner_id: int = Form(0),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if equipment_id <= 0:
        return RedirectResponse("/admin/work-orders?error=eq&open=1", status_code=303)

    desc = description.strip()
    if not desc:
        return RedirectResponse("/admin/work-orders?error=desc&open=1", status_code=303)

    eq = (
        await db.execute(
            select(Equipment)
            .where(Equipment.id == equipment_id, Equipment.is_active == True)
            .options(
                selectinload(Equipment.zone)
                .selectinload(Zone.floor)
                .selectinload(Floor.building)
            )
        )
    ).scalar_one_or_none()
    if not eq:
        return RedirectResponse("/admin/work-orders?error=eq&open=1", status_code=303)

    site_id = None
    if eq.zone and eq.zone.floor and eq.zone.floor.building:
        site_id = eq.zone.floor.building.site_id

    sched = None
    if scheduled_date.strip():
        try:
            sched = date.fromisoformat(scheduled_date.strip())
        except ValueError:
            sched = None

    partner_fk = None
    if partner_id and partner_id > 0:
        partner = await db.get(Partner, partner_id)
        if partner and partner.is_active:
            partner_fk = partner.id

    title = f"[정비의뢰] {eq.code} {eq.name}"
    wo = WorkOrder(
        title=title,
        description=desc,
        priority=priority if priority in ("normal", "high") else "normal",
        assignee_name=assignee_name.strip() or None,
        equipment_id=eq.id,
        site_id=site_id,
        partner_id=partner_fk,
        scheduled_date=sched,
        status=WorkOrderStatus.received,
        work_type="정비",
    )
    db.add(wo)
    await db.commit()
    return RedirectResponse("/admin/work-orders", status_code=303)


@app.get("/admin/work-orders/{wo_id}")
async def work_order_detail(
    wo_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    wo = (
        await db.execute(
            select(WorkOrder)
            .where(WorkOrder.id == wo_id)
            .options(selectinload(WorkOrder.equipment), selectinload(WorkOrder.partner))
        )
    ).scalar_one_or_none()
    if not wo:
        raise HTTPException(404)
    partners = (
        await db.execute(
            select(Partner).where(Partner.is_active == True).order_by(Partner.name)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "work_order_detail.html",
        {
            "user": user,
            "wo": wo,
            "partners": partners,
            "process_step": _wo_process_step(wo.status),
        },
    )


@app.post("/admin/work-orders/{wo_id}/status")
async def work_order_status(
    wo_id: int,
    status: str = Form(...),
    action: str = Form(""),
    cause: str = Form(""),
    assignee_name: str = Form(""),
    partner_id: int = Form(0),
    scheduled_date: str = Form(""),
    redirect: str = Form(""),
    q: str = Form(""),
    filter_status: str = Form(""),
    filter_priority: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import urlencode

    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404)

    # 3단계 프로세스만 허용
    allowed = {"received", "in_progress", "completed"}
    if status not in allowed:
        status = "received"

    wo.status = WorkOrderStatus(status)
    wo.action = action.strip() or None
    if cause.strip():
        wo.cause = cause.strip()
    if assignee_name.strip():
        wo.assignee_name = assignee_name.strip()

    # 협력사 지정/해제
    if partner_id and partner_id > 0:
        partner = await db.get(Partner, partner_id)
        wo.partner_id = partner.id if partner and partner.is_active else None
    else:
        wo.partner_id = None

    # 정비 예정일
    if scheduled_date.strip():
        try:
            wo.scheduled_date = date.fromisoformat(scheduled_date.strip())
        except ValueError:
            pass
    else:
        wo.scheduled_date = None

    if status == "completed":
        wo.completed_at = datetime.utcnow()
        await _ensure_maintenance_history(db, wo)
    elif status != "completed":
        # 완료가 아니면 완료시각 유지/해제 — 재진행 시 완료시각 비움
        if status in ("received", "in_progress"):
            wo.completed_at = None

    await db.commit()
    if redirect == "list":
        params = {
            k: v
            for k, v in {
                "q": q.strip(),
                "status": filter_status.strip(),
                "priority": filter_priority.strip(),
                "date_from": date_from.strip(),
                "date_to": date_to.strip(),
            }.items()
            if v
        }
        qs = f"?{urlencode(params)}" if params else ""
        return RedirectResponse(f"/admin/work-orders{qs}", status_code=303)
    return RedirectResponse(f"/admin/work-orders/{wo_id}", status_code=303)


@app.post("/admin/work-orders/{wo_id}/advance")
async def work_order_advance(
    wo_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """다음 단계로 진행: 정비의뢰 → 정비중 → 정비완료."""
    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404)
    step = _wo_process_step(wo.status)
    if step == 1:
        wo.status = WorkOrderStatus.in_progress
    elif step == 2:
        wo.status = WorkOrderStatus.completed
        wo.completed_at = datetime.utcnow()
        await _ensure_maintenance_history(db, wo)
    await db.commit()
    return RedirectResponse(f"/admin/work-orders/{wo_id}", status_code=303)


# ── D-1 Plans ─────────────────────────────────────────────────────────


def _risk_page_context(user, **extra):
    import os

    from risk_assessment import list_majors, list_presets

    majors = list_majors()
    presets = list_presets()
    major_id_map = {m["name"]: m["id"] for m in majors if m.get("name")}
    ctx = {
        "user": user,
        "majors": majors,
        "presets": presets,
        "presets_json": json.dumps(presets, ensure_ascii=False),
        "major_id_map_json": json.dumps(major_id_map, ensure_ascii=False),
        "ai_ready": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "use_ai": False,
        "five_m": {},
        "meta": {},
        "selected_major": "",
        "preset_name": "",
        "preset_name_json": '""',
        "work_name": "",
        "error_msg": "",
        "command_result": "",
    }
    ctx.update(extra)
    if "preset_name" in extra or "preset_name_json" not in extra:
        ctx["preset_name_json"] = json.dumps(ctx.get("preset_name") or "", ensure_ascii=False)
    return ctx


@app.get("/admin/risk-assessment")
async def risk_assessment_page(
    request: Request,
    user: User = Depends(require_login),
):
    try:
        return templates.TemplateResponse(
            request, "risk_assessment.html", _risk_page_context(user)
        )
    except Exception as e:
        print(f"[risk] page failed: {e}", flush=True)
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "user": user,
                "status_code": 500,
                "message": "위험성평가 화면을 불러오지 못했습니다.",
                "detail": str(e)[:500],
            },
            status_code=500,
        )


@app.post("/admin/risk-assessment/assess")
async def risk_assessment_run(
    request: Request,
    work_name: str = Form(...),
    Man: str = Form(""),
    Machine: str = Form(""),
    Material: str = Form(""),
    Method: str = Form(""),
    Management: str = Form(""),
    Environment: str = Form(""),
    major_name: str = Form(""),
    preset_name: str = Form(""),
    use_ai: str = Form("0"),
    department: str = Form(""),
    evaluator: str = Form(""),
    assessment_no: str = Form(""),
    apply_type: str = Form("정기평가"),
    user: User = Depends(require_login),
):
    from risk_assessment import assess, get_preset

    five_m = {
        "Man": Man.strip(),
        "Machine": Machine.strip(),
        "Material": Material.strip(),
        "Method": Method.strip(),
        "Management": Management.strip(),
        "Environment": Environment.strip(),
    }
    # 프리셋만 고르고 5M이 비면 자동 채움
    if preset_name.strip() and not any(five_m.values()):
        p = get_preset(name=preset_name.strip())
        if p and p.get("five_m_one_e"):
            five_m = {k: (p["five_m_one_e"].get(k) or "") for k in five_m}

    meta = {
        "department": department.strip(),
        "evaluator": evaluator.strip() or user.name,
        "assessment_no": assessment_no.strip(),
        "apply_type": apply_type.strip() or "정기평가",
    }
    try:
        result = assess(
            work_name=work_name.strip(),
            five_m=five_m,
            use_ai=(use_ai == "1"),
            major_name=major_name.strip(),
            meta=meta,
        )
    except Exception as e:
        print(f"[risk] assess failed: {e}", flush=True)
        return templates.TemplateResponse(
            request,
            "risk_assessment.html",
            _risk_page_context(
                user,
                work_name=work_name.strip(),
                five_m=five_m,
                meta=meta,
                selected_major=major_name.strip(),
                preset_name=preset_name.strip(),
                use_ai=(use_ai == "1"),
                error_msg=f"평가 중 오류: {e}",
            ),
            status_code=500,
        )

    # 세션에 결과 보관 (HTML/Excel 내보내기·추가명령용)
    request.session["risk_last"] = {
        "work_name": result["work_name"],
        "report_text": result["report_text"],
        "five_m": five_m,
        "major_name": major_name.strip(),
        "meta": {**(meta or {}), "mode": result["mode"]},
        "rows": result["rows"],
    }

    return templates.TemplateResponse(
        request,
        "risk_assessment.html",
        _risk_page_context(
            user,
            work_name=result["work_name"],
            five_m=five_m,
            meta=meta,
            selected_major=major_name.strip(),
            preset_name=preset_name.strip() or result["work_name"],
            use_ai=(result["mode"] == "ai"),
            form_rows=result["form_rows"],
            result_rows=result["rows"],
            report_text=result["report_text"],
            mode_label=result["mode_label"],
            error_msg=result.get("error") or "",
        ),
    )


@app.post("/admin/risk-assessment/export-html")
async def risk_assessment_export_html(
    request: Request,
    work_name: str = Form(""),
    report_text: str = Form(""),
    user: User = Depends(require_login),
):
    from datetime import datetime as _dt
    from html import escape
    from io import BytesIO
    from urllib.parse import quote

    last = request.session.get("risk_last") or {}
    job = (work_name or last.get("work_name") or "위험성평가").strip()
    body = (report_text or last.get("report_text") or "").strip()
    if not body:
        return RedirectResponse("/admin/risk-assessment", status_code=303)

    html_body = escape(body).replace("\n", "<br>\n")
    stamp = _dt.now().strftime("%Y%m%d_%H%M")
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>P-WIDE 위험성평가 - {escape(job)}</title>
<style>
body {{ font-family: 'Malgun Gothic', sans-serif; max-width: 1200px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
h1 {{ color: #2b5797; border-bottom: 2px solid #2b5797; padding-bottom: 0.5rem; }}
</style>
</head>
<body>
<h1>P-WIDE 위험성평가 도우미 V3</h1>
<p><strong>작업/설비:</strong> {escape(job)}</p>
<p><strong>생성일시:</strong> {_dt.now():%Y-%m-%d %H:%M:%S}</p>
<hr>
<div>{html_body}</div>
</body>
</html>"""
    filename = quote(f"{job}_{stamp}.html")
    return StreamingResponse(
        BytesIO(html.encode("utf-8")),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/admin/risk-assessment/export-excel")
async def risk_assessment_export_excel(
    request: Request,
    user: User = Depends(require_login),
):
    from datetime import datetime as _dt
    from io import BytesIO
    from urllib.parse import quote

    from risk_assessment.web_bridge import assess, export_excel_bytes

    last = request.session.get("risk_last") or {}
    job = (last.get("work_name") or "위험성평가").strip()
    rows = last.get("rows") or []
    meta = dict(last.get("meta") or {})

    # 세션에 rows가 없으면(쿠키 용량 등) 동일 조건으로 재평가
    if not rows and last.get("five_m") is not None:
        rebuilt = assess(
            job,
            last.get("five_m") or {},
            use_ai=False,
            major_name=last.get("major_name") or "",
            meta=meta,
        )
        rows = rebuilt.get("rows") or []
        job = rebuilt.get("work_name") or job

    if not rows:
        return RedirectResponse("/admin/risk-assessment", status_code=303)

    try:
        data = export_excel_bytes(job, rows, meta)
    except Exception:
        return RedirectResponse("/admin/risk-assessment", status_code=303)

    stamp = _dt.now().strftime("%Y%m%d_%H%M")
    filename = quote(f"{job}_{stamp}.xlsx")
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/admin/risk-assessment/command")
async def risk_assessment_command(
    request: Request,
    command_num: int = Form(...),
    work_name: str = Form(...),
    major_name: str = Form(""),
    Man: str = Form(""),
    Machine: str = Form(""),
    Material: str = Form(""),
    Method: str = Form(""),
    Management: str = Form(""),
    Environment: str = Form(""),
    report_text: str = Form(""),
    user_question: str = Form(""),
    user: User = Depends(require_login),
):
    from risk_assessment.web_bridge import run_additional

    five_m = {
        "Man": Man.strip(),
        "Machine": Machine.strip(),
        "Material": Material.strip(),
        "Method": Method.strip(),
        "Management": Management.strip(),
        "Environment": Environment.strip(),
    }
    last = request.session.get("risk_last") or {}
    try:
        result_text = run_additional(
            command_num,
            work_name.strip(),
            five_m,
            report_text or last.get("report_text") or "",
            major_name=major_name.strip(),
            user_question=user_question.strip(),
        )
    except Exception as e:
        result_text = f"추가 명령 실행 오류: {e}"

    # 직전 평가 결과가 있으면 함께 다시 표시
    form_rows = None
    mode_label = ""
    if last.get("work_name"):
        try:
            from risk_assessment import assess

            again = assess(
                work_name=last["work_name"],
                five_m=last.get("five_m") or five_m,
                use_ai=False,
                major_name=last.get("major_name") or major_name,
                meta=last.get("meta") or {},
            )
            form_rows = again["form_rows"]
            mode_label = again["mode_label"]
            report_text = again["report_text"]
        except Exception:
            report_text = last.get("report_text") or report_text
    else:
        report_text = report_text

    return templates.TemplateResponse(
        request,
        "risk_assessment.html",
        _risk_page_context(
            user,
            work_name=work_name.strip(),
            five_m=five_m,
            meta=last.get("meta") or {},
            selected_major=major_name.strip(),
            preset_name=work_name.strip(),
            form_rows=form_rows,
            report_text=report_text,
            mode_label=mode_label or "로컬 전용",
            command_result=result_text,
        ),
    )


@app.get("/admin/d1")
async def d1_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    today = _today_kst()
    tomorrow = today + timedelta(days=1)
    open_statuses = [
        WorkOrderStatus.received,
        WorkOrderStatus.assigned,
        WorkOrderStatus.in_progress,
    ]

    plans = (
        await db.execute(
            select(D1Plan)
            .options(
                selectinload(D1Plan.site),
                selectinload(D1Plan.building),
                selectinload(D1Plan.equipment),
                selectinload(D1Plan.partner),
            )
            .order_by(D1Plan.work_date.desc())
        )
    ).scalars().all()

    today_plans = [p for p in plans if p.work_date == today]
    tomorrow_plans = [p for p in plans if p.work_date == tomorrow]

    wo_rows = (
        await db.execute(
            select(WorkOrder)
            .where(
                WorkOrder.scheduled_date.in_([today, tomorrow]),
                WorkOrder.status.in_(open_statuses),
            )
            .options(
                selectinload(WorkOrder.equipment),
                selectinload(WorkOrder.partner),
            )
            .order_by(WorkOrder.priority.desc(), WorkOrder.id.asc())
        )
    ).scalars().unique().all()
    today_works = [w for w in wo_rows if w.scheduled_date == today]
    tomorrow_works = [w for w in wo_rows if w.scheduled_date == tomorrow]

    sites = (
        await db.execute(select(Site).where(Site.is_active == True).order_by(Site.name))
    ).scalars().all()
    buildings = (
        await db.execute(
            select(Building).where(Building.is_active == True).order_by(Building.name)
        )
    ).scalars().all()
    equipment = (
        await db.execute(
            select(Equipment).where(Equipment.is_active == True).order_by(Equipment.code)
        )
    ).scalars().all()
    partners = (
        await db.execute(
            select(Partner).where(Partner.is_active == True).order_by(Partner.name)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "d1_plans.html",
        {
            "user": user,
            "plans": plans,
            "today": today,
            "tomorrow": tomorrow,
            "today_plans": today_plans,
            "tomorrow_plans": tomorrow_plans,
            "today_works": today_works,
            "tomorrow_works": tomorrow_works,
            "sites": sites,
            "buildings": buildings,
            "equipment": equipment,
            "partners": partners,
        },
    )


@app.post("/admin/d1")
async def d1_create(
    work_date: date = Form(...),
    title: str = Form(...),
    site_id: int = Form(0),
    building_id: int = Form(0),
    equipment_id: int = Form(0),
    work_content: str = Form(""),
    work_time: str = Form(""),
    partner_id: int = Form(0),
    worker_count: int = Form(1),
    is_urgent: bool = Form(False),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    plan = D1Plan(
        work_date=work_date,
        title=title.strip(),
        site_id=site_id if site_id > 0 else None,
        building_id=building_id if building_id > 0 else None,
        equipment_id=equipment_id if equipment_id > 0 else None,
        work_content=work_content,
        work_time=work_time,
        partner_id=partner_id if partner_id > 0 else None,
        worker_count=worker_count,
        is_urgent=is_urgent,
        created_by=user.name,
        status=D1Status.draft,
        jsa_data={
            "hazards": ["감전", "추락", "협착", "화재", "고소작업"],
            "controls": [],
        },
        tbm_data={"ppe": ["안전모", "안전화"], "tools_checked": False},
        permit_data={"type": "일반작업", "approved": False},
    )
    db.add(plan)
    await db.commit()
    return RedirectResponse("/admin/d1", status_code=303)


@app.get("/admin/d1/{plan_id}")
async def d1_detail(
    plan_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    plan = (
        await db.execute(
            select(D1Plan)
            .where(D1Plan.id == plan_id)
            .options(
                selectinload(D1Plan.site),
                selectinload(D1Plan.building),
                selectinload(D1Plan.equipment),
                selectinload(D1Plan.partner),
            )
        )
    ).scalar_one_or_none()
    if not plan:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "d1_detail.html", {"user": user, "plan": plan}
    )


@app.post("/admin/d1/{plan_id}/advance")
async def d1_advance(
    plan_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    plan = await db.get(D1Plan, plan_id)
    if not plan:
        raise HTTPException(404)
    flow = [
        D1Status.draft,
        D1Status.review,
        D1Status.approved,
        D1Status.jsa_pending,
        D1Status.tbm_pending,
        D1Status.permit_pending,
        D1Status.in_progress,
        D1Status.completed,
    ]
    try:
        idx = flow.index(plan.status)
        if idx < len(flow) - 1:
            plan.status = flow[idx + 1]
            if plan.status == D1Status.permit_pending:
                plan.permit_no = f"WP-{plan.id:05d}-{date.today().strftime('%Y%m%d')}"
            if plan.status == D1Status.completed:
                plan.completed_at = datetime.utcnow()
    except ValueError:
        pass
    await db.commit()
    return RedirectResponse(f"/admin/d1/{plan_id}", status_code=303)


# ── PM & Inventory & Partners ─────────────────────────────────────────


@app.get("/admin/pm")
async def pm_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    schedules = (
        await db.execute(
            select(PMSchedule)
            .options(selectinload(PMSchedule.equipment))
            .order_by(PMSchedule.next_due.asc().nullslast())
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "pm.html",
        {"user": user, "schedules": schedules, "today": date.today()},
    )


@app.get("/admin/inventory")
async def inventory_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    items = (
        await db.execute(select(InventoryItem).order_by(InventoryItem.code))
    ).scalars().all()
    return templates.TemplateResponse(
        request, "inventory.html", {"user": user, "items": items}
    )


@app.get("/admin/partners")
async def partners_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    partners = (
        await db.execute(
            select(Partner)
            .where(Partner.is_active == True)
            .order_by(Partner.name)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "partners.html", {"user": user, "partners": partners}
    )


@app.post("/admin/partners")
async def partner_create(
    name: str = Form(...),
    code: str = Form(...),
    contact_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    contract_end: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    code_val = code.strip()
    name_val = name.strip()
    if not name_val or not code_val:
        return RedirectResponse("/admin/partners?error=required", status_code=303)

    existing = (
        await db.execute(select(Partner).where(Partner.code == code_val))
    ).scalar_one_or_none()
    if existing and existing.is_active:
        return RedirectResponse("/admin/partners?error=code", status_code=303)
    if existing and not existing.is_active:
        existing.is_active = True
        existing.name = name_val
        existing.contact_name = contact_name.strip() or None
        existing.phone = phone.strip() or None
        existing.email = email.strip() or None
        existing.contract_end = (
            date.fromisoformat(contract_end) if contract_end.strip() else None
        )
        await db.commit()
        return RedirectResponse("/admin/partners", status_code=303)

    end_date = None
    if contract_end.strip():
        try:
            end_date = date.fromisoformat(contract_end.strip())
        except ValueError:
            end_date = None

    db.add(
        Partner(
            name=name_val,
            code=code_val,
            contact_name=contact_name.strip() or None,
            phone=phone.strip() or None,
            email=email.strip() or None,
            contract_end=end_date,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/partners", status_code=303)


@app.get("/admin/partners/{partner_id}/edit")
async def partner_edit_page(
    partner_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    partner = await db.get(Partner, partner_id)
    if not partner or not partner.is_active:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "partner_edit.html", {"user": user, "partner": partner}
    )


@app.post("/admin/partners/{partner_id}/edit")
async def partner_edit(
    partner_id: int,
    name: str = Form(...),
    code: str = Form(...),
    contact_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    contract_end: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    partner = await db.get(Partner, partner_id)
    if not partner or not partner.is_active:
        raise HTTPException(404)

    code_val = code.strip()
    name_val = name.strip()
    if not name_val or not code_val:
        return RedirectResponse(
            f"/admin/partners/{partner_id}/edit?error=required", status_code=303
        )

    dup = (
        await db.execute(
            select(Partner).where(Partner.code == code_val, Partner.id != partner_id)
        )
    ).scalar_one_or_none()
    if dup and dup.is_active:
        return RedirectResponse(
            f"/admin/partners/{partner_id}/edit?error=code", status_code=303
        )

    end_date = None
    if contract_end.strip():
        try:
            end_date = date.fromisoformat(contract_end.strip())
        except ValueError:
            end_date = None

    partner.name = name_val
    partner.code = code_val
    partner.contact_name = contact_name.strip() or None
    partner.phone = phone.strip() or None
    partner.email = email.strip() or None
    partner.contract_end = end_date
    await db.commit()
    return RedirectResponse("/admin/partners", status_code=303)


@app.post("/admin/partners/{partner_id}/delete")
async def partner_delete(
    partner_id: int,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    partner = await db.get(Partner, partner_id)
    if not partner:
        raise HTTPException(404)
    partner.is_active = False
    await db.commit()
    return RedirectResponse("/admin/partners", status_code=303)


# ── QR / Mobile Equipment View ────────────────────────────────────────


@app.get("/eq/{code}")
async def equipment_mobile(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.code == code)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building),
            selectinload(Equipment.consumables),
            selectinload(Equipment.pm_schedules),
            selectinload(Equipment.work_orders),
            selectinload(Equipment.equipment_type),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404, detail="설비를 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request, "mobile_equipment.html", {"eq": eq}
    )


@app.get("/")
async def root():
    return RedirectResponse("/admin/dashboard", status_code=303)
