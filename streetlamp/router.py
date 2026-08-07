"""Smart FMS 가로등 QR 정비 의뢰 라우터."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from io import BytesIO
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import (
    can_access_equipment_pm,
    can_access_menu,
    can_create,
    can_delete,
    can_edit,
    effective_menu_access,
    require_login,
    MENU_ITEMS,
    ROLE_LABELS,
)
from database import AsyncSessionLocal, get_db
from streetlamp.models import Lamp, MaintenanceRequest, RequestStatus, RequestType
from streetlamp.reporting import RequestStatusLabel, RequestTypeLabel, build_xlsx_bytes, run_daily_report_pipeline
from streetlamp.settings_service import ensure_default_settings, get_all_settings_map, get_setting, set_setting
from streetlamp.sms_notify import run_test_sms_pipeline, send_new_request_sms_alert

router = APIRouter()
templates = Jinja2Templates(directory="templates")
KST = ZoneInfo("Asia/Seoul")


def _fmt_kst(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return ""
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(KST).strftime(fmt)


templates.env.filters["fmt_kst"] = _fmt_kst
templates.env.filters["fmt_kst_date"] = lambda value: _fmt_kst(value, "%Y-%m-%d")
templates.env.filters["urlencode_path"] = lambda value: quote(str(value), safe="")
templates.env.globals["user_can_create"] = can_create
templates.env.globals["user_can_edit"] = can_edit
templates.env.globals["user_can_delete"] = can_delete
templates.env.globals["user_can_access_menu"] = can_access_menu
templates.env.globals["user_can_access_equipment_pm"] = can_access_equipment_pm
templates.env.globals["user_menu_access"] = effective_menu_access
templates.env.globals["menu_items"] = MENU_ITEMS
templates.env.globals["role_labels"] = ROLE_LABELS
templates.env.globals["fmt_kst"] = _fmt_kst
templates.env.globals["fmt_kst_date"] = lambda value: _fmt_kst(value, "%Y-%m-%d")
templates.env.globals["fmt_file_size"] = lambda n: "" if n is None else str(n)


def admin_paths() -> dict[str, str]:
    """가로등 관리 경로 — 인증은 Smart FMS /admin/login 계정만 사용."""
    base = "/admin/streetlamp"
    return {
        "path_requests_list": f"{base}/requests",
        "path_requests_export": f"{base}/requests/export",
        "path_requests_remove": f"{base}/requests/delete",
        "path_qr_zip": f"{base}/qr-zip",
        "path_settings": f"{base}/settings",
        "path_settings_test_email": f"{base}/settings/test-email",
        "path_settings_test_sms": f"{base}/settings/test-sms",
        "path_logout": "/admin/logout",
    }


def _check_access(user) -> None:
    if not can_access_menu(user, "streetlamp"):
        raise HTTPException(403, "가로등 관리 메뉴 접근 권한이 없습니다.")


def _ctx(request: Request, user, **kwargs) -> dict:
    return {"request": request, "user": user, "is_guest": not can_edit(user), **admin_paths(), **kwargs}


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    """Starlette 1.x: TemplateResponse(request, name, context)."""
    return templates.TemplateResponse(request, name, context, status_code=status_code)


async def _lamp(db: AsyncSession, code: str) -> Lamp | None:
    result = await db.execute(select(Lamp).where(Lamp.code == code.strip()))
    lamp = result.scalar_one_or_none()
    return lamp if lamp or not code.isdigit() else await db.get(Lamp, int(code))


async def _auto_import() -> None:
    from streetlamp.import_lamps_from_csv import import_lamps_if_needed
    try:
        await import_lamps_if_needed()
    except Exception as exc:
        print(f"[lamp-import] automatic import failed: {exc}", flush=True)


@router.get("/lamp/{lamp_code:path}")
async def lamp_detail(request: Request, lamp_code: str, db: AsyncSession = Depends(get_db)):
    await _auto_import()
    lamp = await _lamp(db, lamp_code)
    if not lamp:
        raise HTTPException(404, "가로등을 찾을 수 없습니다.")
    return _render(request, "streetlamp/lamp_detail.html", {
        "lamp": lamp, "lamp_code": lamp_code, "request_types": RequestType,
    })


@router.post("/lamp/{lamp_code:path}/request")
async def create_request(
    request: Request, lamp_code: str, name: str = Form(""), phone: str = Form(""),
    request_type: str = Form(""), content: str = Form(""), db: AsyncSession = Depends(get_db),
):
    lamp = await _lamp(db, lamp_code)
    if not lamp:
        raise HTTPException(404, "가로등을 찾을 수 없습니다.")
    name, phone, content = name.strip(), phone.strip(), content.strip()
    error, type_value = "", None
    if not (phone.isdigit() and len(phone) == 4):
        error = "전화번호 끝 4자리를 숫자로 입력해 주세요."
    try:
        type_value = RequestType(request_type)
    except ValueError:
        error = error or "정비 유형을 선택해 주세요."
    if error:
        return _render(request, "streetlamp/lamp_detail.html", {
            "lamp": lamp, "lamp_code": lamp_code, "request_types": RequestType,
            "form_error": error, "name_input": name, "phone_input": phone,
            "request_type_input": request_type, "content_input": content,
        }, status_code=422)
    row = MaintenanceRequest(lamp_id=lamp.id, name=name or "익명", phone=phone, request_type=type_value,
                             content=content or None, status=RequestStatus.received)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await send_new_request_sms_alert(db, req_id=row.id, lamp_id=lamp.id, lamp_code=lamp.code,
                                     name=row.name, phone=row.phone, request_type=row.request_type,
                                     content=row.content or "")
    return _render(request, "streetlamp/request_submitted.html",
                   {"lamp_id": lamp.code or lamp.id})


@router.get("/status")
async def status_form(request: Request):
    return _render(request, "streetlamp/status_check.html", {
        "results": [], "name_input": "", "phone_input": "",
        "RequestStatusLabel": RequestStatusLabel, "RequestTypeLabel": RequestTypeLabel,
    })


@router.post("/status")
async def status_check(request: Request, name: str = Form(""), phone: str = Form(""),
                       db: AsyncSession = Depends(get_db)):
    name, phone = name.strip(), phone.strip()
    rows, error = [], ""
    if not (phone.isdigit() and len(phone) == 4):
        error = "전화번호 끝 4자리를 숫자 4자리로 입력해 주세요."
    else:
        clauses = [MaintenanceRequest.phone == phone]
        if name:
            clauses.append(MaintenanceRequest.name == name)
        result = await db.execute(select(MaintenanceRequest).where(and_(*clauses))
                                  .options(selectinload(MaintenanceRequest.lamp))
                                  .order_by(MaintenanceRequest.created_at.desc()))
        rows = list(result.scalars())
        error = "" if rows else "일치하는 접수 내역이 없습니다."
    return _render(request, "streetlamp/status_check.html", {
        "results": rows, "error": error, "name_input": name, "phone_input": phone,
        "RequestStatusLabel": RequestStatusLabel, "RequestTypeLabel": RequestTypeLabel,
    })


def _filters(**values) -> tuple[list, dict[str, str]]:
    clauses = []
    def kst_date(raw: str, end=False):
        try:
            local = datetime.combine(date.fromisoformat(raw), time.max if end else time.min, KST)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None
    if value := kst_date(values["date_from"]): clauses.append(MaintenanceRequest.created_at >= value)
    if value := kst_date(values["date_to"], True): clauses.append(MaintenanceRequest.created_at <= value)
    if value := values["lamp_id"].strip():
        clauses.append(or_(Lamp.code.ilike(f"%{value}%"), MaintenanceRequest.lamp_id.cast(String).ilike(f"%{value}%")))
    if values["request_type"] in {x.value for x in RequestType}: clauses.append(MaintenanceRequest.request_type == RequestType(values["request_type"]))
    if values["filter_status"] in {x.value for x in RequestStatus}: clauses.append(MaintenanceRequest.status == RequestStatus(values["filter_status"]))
    for column, key in ((MaintenanceRequest.name, "name"), (MaintenanceRequest.phone, "phone"), (MaintenanceRequest.content, "content")):
        if value := values[key].strip(): clauses.append(column.ilike(f"%{value}%"))
    if value := values["q"].strip():
        like = f"%{value}%"
        clauses.append(or_(MaintenanceRequest.name.ilike(like), MaintenanceRequest.phone.ilike(like),
                           MaintenanceRequest.content.ilike(like), Lamp.code.ilike(like),
                           MaintenanceRequest.lamp_id.cast(String).ilike(like)))
    return clauses, values


async def _admin_requests_select(db: AsyncSession, clauses: list) -> list[MaintenanceRequest]:
    q = select(MaintenanceRequest).outerjoin(Lamp)
    if clauses:
        q = q.where(*clauses)
    result = await db.execute(
        q.options(selectinload(MaintenanceRequest.lamp)).order_by(MaintenanceRequest.created_at.desc())
    )
    return list(result.scalars().unique())


@router.get("/admin/streetlamp/requests")
async def admin_requests(request: Request, date_from: str = "", date_to: str = "", lamp_id: str = "",
                         request_type: str = "", name: str = "", phone: str = "", content: str = "",
                         q: str = "", filter_status: str = "", user=Depends(require_login),
                         db: AsyncSession = Depends(get_db)):
    _check_access(user)
    clauses, values = _filters(date_from=date_from, date_to=date_to, lamp_id=lamp_id, request_type=request_type,
                               name=name, phone=phone, content=content, q=q, filter_status=filter_status)
    return _render(request, "streetlamp/admin_requests.html", _ctx(
        request, user, requests_list=await _admin_requests_select(db, clauses), RequestType=RequestType,
        RequestStatus=RequestStatus, RequestTypeLabel=RequestTypeLabel, RequestStatusLabel=RequestStatusLabel,
        export_qs=urlencode({k: v for k, v in values.items() if v}),
        request_type_filter=values.get("request_type", ""),
        status_filter=values.get("filter_status", ""),
        **values))


@router.post("/admin/streetlamp/requests")
async def admin_request_save(mr_id: int = Form(...), mr_status: str = Form(...), mr_work_memo: str = Form(""),
                             user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    row = await db.get(MaintenanceRequest, mr_id)
    if not row:
        return RedirectResponse(f"{admin_paths()['path_requests_list']}?flash=nosuchrequest", 303)
    try: row.status = RequestStatus(mr_status)
    except ValueError: raise HTTPException(422, "유효하지 않은 상태입니다.")
    row.work_memo = mr_work_memo.strip() or None
    row.completed_at = datetime.utcnow() if row.status == RequestStatus.done else None
    await db.commit()
    return RedirectResponse(f"{admin_paths()['path_requests_list']}?flash=saved", 303)


@router.post("/admin/streetlamp/requests/delete")
async def admin_request_delete(mr_id: int = Form(...), user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    if not can_edit(user): raise HTTPException(403, "삭제 권한이 없습니다.")
    if row := await db.get(MaintenanceRequest, mr_id):
        await db.delete(row); await db.commit()
    return RedirectResponse(f"{admin_paths()['path_requests_list']}?flash=saved", 303)


@router.get("/admin/streetlamp/requests/export")
async def admin_export(date_from: str = "", date_to: str = "", lamp_id: str = "", request_type: str = "",
                       name: str = "", phone: str = "", content: str = "", q: str = "", filter_status: str = "",
                       user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    clauses, _ = _filters(date_from=date_from, date_to=date_to, lamp_id=lamp_id, request_type=request_type,
                          name=name, phone=phone, content=content, q=q, filter_status=filter_status)
    payload, filename = build_xlsx_bytes(await _admin_requests_select(db, clauses))
    return StreamingResponse(BytesIO(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/admin/streetlamp/qr-zip")
async def admin_qr_zip(request: Request, user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    """등록된 가로등 전체 QR PNG를 ZIP으로 다운로드."""
    _check_access(user)
    await _auto_import()
    from streetlamp.qr_generate import build_qr_zip_bytes

    result = await db.execute(select(Lamp).order_by(Lamp.code, Lamp.id))
    lamps = list(result.scalars().all())
    codes: list[str] = []
    for lamp in lamps:
        code = (lamp.code or "").strip() or str(lamp.id)
        codes.append(code)
    if not codes:
        raise HTTPException(404, "등록된 가로등이 없습니다. 먼저 가로등 데이터를 이관하거나 CSV를 임포트하세요.")
    try:
        payload = build_qr_zip_bytes(codes, request)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    stamp = datetime.now(KST).strftime("%Y%m%d")
    filename = quote(f"streetlamp_QR_all_{stamp}.zip")
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


async def _settings_page(request: Request, user, db: AsyncSession, saved=False, notice=""):
    return _render(request, "streetlamp/admin_settings.html", _ctx(
        request, user, settings=await get_all_settings_map(db), saved=saved, notice=notice,
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/") or os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/"),
        cron_secret_set=bool(os.environ.get("CRON_SECRET", "").strip())))


@router.get("/admin/streetlamp/settings")
async def admin_settings(request: Request, user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    if not can_edit(user): raise HTTPException(403, "설정 수정 권한이 없습니다.")
    await ensure_default_settings(db); await db.commit()
    return await _settings_page(request, user, db)


@router.post("/admin/streetlamp/settings")
async def save_settings(request: Request, report_email: str = Form(""), report_hour_kst: str = Form("16"),
                        report_minute_kst: str = Form("0"), keep_alive_minutes: str = Form("0"),
                        alert_sms_phones: str = Form(""), alert_sms_enabled: str | None = Form(None),
                        use_internal_daily_scheduler: str | None = Form(None), user=Depends(require_login),
                        db: AsyncSession = Depends(get_db)):
    _check_access(user)
    if not can_edit(user): raise HTTPException(403, "설정 수정 권한이 없습니다.")
    for key, value in {"report_email": report_email.strip(), "report_hour_kst": report_hour_kst.strip(),
                       "report_minute_kst": report_minute_kst.strip(), "keep_alive_minutes": keep_alive_minutes.strip(),
                       "alert_sms_phones": alert_sms_phones.strip(), "alert_sms_enabled": "1" if alert_sms_enabled else "0",
                       "use_internal_daily_scheduler": "1" if use_internal_daily_scheduler else "0"}.items():
        await set_setting(db, key, value)
    await db.commit()
    if scheduler := getattr(request.app.state, "scheduler", None):
        from streetlamp.jobs import reschedule_daily_report_job
        await reschedule_daily_report_job(scheduler)
    return await _settings_page(request, user, db, saved=True)


@router.post("/admin/streetlamp/settings/test-email")
async def test_email(request: Request, user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    if not can_edit(user): raise HTTPException(403, "설정 수정 권한이 없습니다.")
    return await _settings_page(request, user, db, notice=await run_daily_report_pipeline(db, await get_setting(db, "report_email")))


@router.post("/admin/streetlamp/settings/test-sms")
async def test_sms(request: Request, user=Depends(require_login), db: AsyncSession = Depends(get_db)):
    _check_access(user)
    if not can_edit(user): raise HTTPException(403, "설정 수정 권한이 없습니다.")
    return await _settings_page(request, user, db, notice=await run_test_sms_pipeline(db))


def _cron_secret(secret: str):
    if not os.environ.get("CRON_SECRET", "").strip() or secret != os.environ["CRON_SECRET"]:
        raise HTTPException(403, "잘못된 cron secret입니다.")


@router.get("/cron/streetlamp/daily-report")
async def cron_daily_report(secret: str = Query(...)):
    _cron_secret(secret)
    async with AsyncSessionLocal() as db:
        return {"ok": True, "message": await run_daily_report_pipeline(db, await get_setting(db, "report_email"))}


@router.get("/cron/streetlamp/import-lamps")
async def cron_import_lamps(secret: str = Query(...)):
    _cron_secret(secret)
    from streetlamp.import_lamps_from_csv import import_lamps
    return {"ok": True, "added": await import_lamps()}
