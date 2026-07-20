# main.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
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
from database import AsyncSessionLocal, Base, engine, get_db
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


def _fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def _status_label(status: WorkOrderStatus | str) -> str:
    labels = {
        "received": "접수",
        "assigned": "배정",
        "in_progress": "작업중",
        "completed": "완료",
        "verified": "검수",
        "closed": "종료",
    }
    key = status.value if isinstance(status, WorkOrderStatus) else str(status)
    return labels.get(key, key)


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
    last_err: Exception | None = None
    for attempt in range(1, 6):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSessionLocal() as session:
                await seed_if_empty(session)
            print(f"[startup] DB ready (attempt {attempt})", flush=True)
            break
        except Exception as e:
            last_err = e
            print(f"[startup] DB init failed ({attempt}/5): {e}", flush=True)
            if attempt < 5:
                import asyncio

                await asyncio.sleep(3)
    else:
        raise last_err  # type: ignore[misc]
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
templates.env.globals.update(
    fmt_kst=_fmt_kst,
    role_labels=ROLE_LABELS,
    wo_status_label=_status_label,
    d1_status_label=_d1_status_label,
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
    site_count = (await db.execute(select(func.count(Site.id)))).scalar() or 0
    building_count = (await db.execute(select(func.count(Building.id)))).scalar() or 0
    equipment_count = (await db.execute(select(func.count(Equipment.id)))).scalar() or 0
    wo_total = (await db.execute(select(func.count(WorkOrder.id)))).scalar() or 0
    wo_progress = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.status.in_([WorkOrderStatus.in_progress, WorkOrderStatus.assigned])
            )
        )
    ).scalar() or 0
    wo_done = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.status.in_([WorkOrderStatus.completed, WorkOrderStatus.closed])
            )
        )
    ).scalar() or 0
    wo_urgent = (
        await db.execute(
            select(func.count(WorkOrder.id)).where(WorkOrder.priority == "high")
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
        select(Site).options(selectinload(Site.buildings)).order_by(Site.name)
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
    if not building:
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


@app.get("/admin/equipment")
async def equipment_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .options(
            selectinload(Equipment.zone)
            .selectinload(Zone.floor)
            .selectinload(Floor.building)
            .selectinload(Building.site),
            selectinload(Equipment.equipment_type),
        )
        .order_by(Equipment.code)
    )
    equipment = result.scalars().all()
    zones = (await db.execute(select(Zone).order_by(Zone.name))).scalars().all()
    types = (await db.execute(select(EquipmentType).order_by(EquipmentType.name))).scalars().all()
    templates_list = (
        await db.execute(select(EquipmentTemplate).order_by(EquipmentTemplate.name))
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "equipment.html",
        {
            "user": user,
            "equipment": equipment,
            "zones": zones,
            "types": types,
            "templates": templates_list,
        },
    )


@app.post("/admin/equipment")
async def equipment_create(
    zone_id: int = Form(...),
    code: str = Form(...),
    name: str = Form(...),
    equipment_type_id: int = Form(0),
    template_id: int = Form(0),
    manufacturer: str = Form(""),
    model: str = Form(""),
    manager_name: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    tpl_id = template_id if template_id > 0 else None
    type_id = equipment_type_id if equipment_type_id > 0 else None

    eq = Equipment(
        zone_id=zone_id,
        code=code.strip(),
        name=name.strip(),
        equipment_type_id=type_id,
        template_id=tpl_id,
        manufacturer=manufacturer,
        model=model,
        manager_name=manager_name,
    )

    if tpl_id:
        tpl = await db.get(EquipmentTemplate, tpl_id)
        if tpl:
            eq.manufacturer = eq.manufacturer or tpl.manufacturer
            eq.model = eq.model or tpl.model
            for item in tpl.consumables or []:
                db.add(
                    Consumable(
                        equipment=eq,
                        name=item.get("name", "소모품"),
                        replace_interval_days=item.get("interval_days"),
                        replace_interval_hours=item.get("interval_hours"),
                    )
                )
            db.add(
                PMSchedule(
                    equipment=eq,
                    title=f"{eq.name} 정기점검",
                    checklist=tpl.pm_items or [],
                    custom_days=tpl.pm_cycle_days,
                )
            )

    db.add(eq)
    await db.commit()
    return RedirectResponse("/admin/equipment", status_code=303)


@app.get("/admin/equipment/{eq_id}")
async def equipment_detail(
    eq_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Equipment)
        .where(Equipment.id == eq_id)
        .options(
            selectinload(Equipment.zone).selectinload(Zone.floor).selectinload(Floor.building),
            selectinload(Equipment.consumables),
            selectinload(Equipment.pm_schedules),
            selectinload(Equipment.work_orders),
            selectinload(Equipment.equipment_type),
            selectinload(Equipment.template),
        )
    )
    eq = result.scalar_one_or_none()
    if not eq:
        raise HTTPException(404)
    base_url = os.environ.get("PUBLIC_BASE_URL", str(request.base_url).rstrip("/"))
    return templates.TemplateResponse(
        request,
        "equipment_detail.html",
        {"user": user, "eq": eq, "qr_url": f"{base_url}/eq/{eq.code}"},
    )


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
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    db.add(EquipmentType(name=name.strip()))
    await db.commit()
    return RedirectResponse("/admin/templates", status_code=303)


# ── Work Orders (CMMS) ─────────────────────────────────────────────────


@app.get("/admin/work-orders")
async def work_orders_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    orders = (
        await db.execute(
            select(WorkOrder)
            .options(selectinload(WorkOrder.equipment), selectinload(WorkOrder.partner))
            .order_by(WorkOrder.created_at.desc())
        )
    ).scalars().all()
    equipment = (await db.execute(select(Equipment).order_by(Equipment.code))).scalars().all()
    partners = (await db.execute(select(Partner).where(Partner.is_active == True))).scalars().all()
    return templates.TemplateResponse(
        request,
        "work_orders.html",
        {
            "user": user,
            "orders": orders,
            "equipment": equipment,
            "partners": partners,
        },
    )


@app.post("/admin/work-orders")
async def work_order_create(
    title: str = Form(...),
    equipment_id: int = Form(0),
    description: str = Form(""),
    priority: str = Form("normal"),
    assignee_name: str = Form(""),
    partner_id: int = Form(0),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    wo = WorkOrder(
        title=title.strip(),
        description=description,
        priority=priority,
        assignee_name=assignee_name,
        equipment_id=equipment_id if equipment_id > 0 else None,
        partner_id=partner_id if partner_id > 0 else None,
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
    return templates.TemplateResponse(
        request, "work_order_detail.html", {"user": user, "wo": wo}
    )


@app.post("/admin/work-orders/{wo_id}/status")
async def work_order_status(
    wo_id: int,
    status: str = Form(...),
    action: str = Form(""),
    cause: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    wo = await db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404)
    wo.status = WorkOrderStatus(status)
    if action:
        wo.action = action
    if cause:
        wo.cause = cause
    if status in ("completed", "closed"):
        wo.completed_at = datetime.utcnow()
    await db.commit()
    return RedirectResponse(f"/admin/work-orders/{wo_id}", status_code=303)


# ── D-1 Plans ─────────────────────────────────────────────────────────


@app.get("/admin/d1")
async def d1_list(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
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
    sites = (await db.execute(select(Site))).scalars().all()
    buildings = (await db.execute(select(Building))).scalars().all()
    equipment = (await db.execute(select(Equipment))).scalars().all()
    partners = (await db.execute(select(Partner).where(Partner.is_active == True))).scalars().all()
    return templates.TemplateResponse(
        request,
        "d1_plans.html",
        {
            "user": user,
            "plans": plans,
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
    partners = (await db.execute(select(Partner).order_by(Partner.name))).scalars().all()
    return templates.TemplateResponse(
        request, "partners.html", {"user": user, "partners": partners}
    )


@app.post("/admin/partners")
async def partner_create(
    name: str = Form(...),
    code: str = Form(...),
    contact_name: str = Form(""),
    phone: str = Form(""),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        Partner(name=name.strip(), code=code.strip(), contact_name=contact_name, phone=phone)
    )
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
