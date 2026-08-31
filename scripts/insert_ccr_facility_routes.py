# -*- coding: utf-8
"""main.py에 중앙관제실(설비) 라우트 연동."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
text = MAIN.read_text(encoding="utf-8")

if "ccr_facility_page" in text:
    print("already patched")
    raise SystemExit(0)

# imports
if "CcrFacilityDaily" not in text:
    text = text.replace(
        "    CentralControlRoomArchive,\n    CentralControlRoomDaily,",
        "    CcrFacilityArchive,\n    CcrFacilityDaily,\n"
        "    CentralControlRoomArchive,\n    CentralControlRoomDaily,",
    )

# redirect — facility before CCR
facility_redirect = """    from ccr_facility import is_ccr_facility_building

    if is_ccr_facility_building(building):
        return RedirectResponse(
            f"/admin/inspection-logs2/{building_id}/ccr-facility",
            status_code=303,
        )
    from central_control_room import is_central_control_room_building"""
text = text.replace(
    "    from central_control_room import is_central_control_room_building",
    facility_redirect,
    1,
)

# scheduler
if "register_ccrf_scheduler" not in text:
    text = text.replace(
        "            register_ccr_scheduler(scheduler, AsyncSessionLocal, KST)\n            scheduler.start()",
        "            register_ccr_scheduler(scheduler, AsyncSessionLocal, KST)\n"
        "            from ccr_facility import register_scheduler as register_ccrf_scheduler\n\n"
        "            register_ccrf_scheduler(scheduler, AsyncSessionLocal, KST)\n"
        "            scheduler.start()",
    )

routes = r'''
@app.get("/admin/inspection-logs2/{building_id}/ccr-facility")
async def ccr_facility_page(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote
    import calendar

    from ccr_facility import (
        compute_monthly_report,
        ensure_tables as ensure_ccrf_tables,
        fetch_notes_list,
        fetch_yearly_report_data,
        get_or_create_daily,
        is_ccr_facility_building,
        load_schema,
        sync_prev_values,
    )

    try:
        await _ensure_inspection_log2_tables()
        await ensure_ccrf_tables(engine)
    except Exception as e:
        print(f"[ccrf] ensure: {e}", flush=True)

    row = (
        await db.execute(
            select(InspectionLogBuilding2, Building)
            .join(Building, Building.id == InspectionLogBuilding2.building_id)
            .where(
                InspectionLogBuilding2.building_id == building_id,
                Building.is_active == True,  # noqa: E712
            )
        )
    ).first()
    if not row:
        return RedirectResponse(
            "/admin/inspection-logs2?error=" + quote("등록되지 않은 건물입니다."),
            status_code=303,
        )
    _, building = row
    if not is_ccr_facility_building(building):
        return RedirectResponse(f"/admin/inspection-logs2/{building_id}", status_code=303)

    tab = request.query_params.get("tab") or "daily"
    today = _today_kst()
    schema = load_schema()

    if tab == "monthly":
        try:
            year = int(request.query_params.get("year") or today.year)
            month = int(request.query_params.get("month") or today.month)
        except ValueError:
            year, month = today.year, today.month
        last_day = calendar.monthrange(year, month)[1]
        daily_rows = (
            await db.execute(
                select(CcrFacilityDaily).where(
                    CcrFacilityDaily.building_id == building_id,
                    CcrFacilityDaily.log_date >= date(year, month, 1),
                    CcrFacilityDaily.log_date <= date(year, month, last_day),
                )
            )
        ).scalars().all()
        monthly = compute_monthly_report(building_id, year, month, list(daily_rows))
        yearly = {"months": []}
        notes_list = {"entries": []}
        log_date = today
        daily_data = {}
    elif tab == "yearly":
        try:
            year = int(request.query_params.get("year") or today.year)
        except ValueError:
            year = today.year
        yearly = await fetch_yearly_report_data(db, building_id, year)
        month = today.month
        monthly = {"days": []}
        notes_list = {"entries": []}
        log_date = today
        daily_data = {}
    elif tab == "notes":
        try:
            year = int(request.query_params.get("year") or today.year)
            month = int(request.query_params.get("month") or today.month)
        except ValueError:
            year, month = today.year, today.month
        notes_list = await fetch_notes_list(db, building_id, year, month)
        monthly = {"days": []}
        yearly = {"months": []}
        log_date = today
        daily_data = {}
    else:
        tab = "daily"
        raw_date = request.query_params.get("date")
        try:
            log_date = date.fromisoformat(raw_date) if raw_date else today
        except ValueError:
            log_date = today
        daily_row = await get_or_create_daily(db, building_id, log_date)
        daily_row.data = await sync_prev_values(db, building_id, log_date, daily_row.data or {})
        await db.commit()
        daily_data = daily_row.data or {}
        year, month = log_date.year, log_date.month
        monthly = {"days": []}
        yearly = {"months": []}
        notes_list = {"entries": []}

    return templates.TemplateResponse(
        request,
        "ccr_facility.html",
        {
            "user": user,
            "building": building,
            "schema": schema,
            "tab": tab,
            "log_date": log_date,
            "today": today,
            "year": year,
            "month": month,
            "daily_data": daily_data,
            "monthly": monthly,
            "yearly": yearly,
            "notes_list": notes_list,
            "qr_mode": False,
            "daily_save_url": f"/admin/inspection-logs2/{building_id}/ccr-facility/save",
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@app.post("/admin/inspection-logs2/{building_id}/ccr-facility/save")
async def ccr_facility_save(
    building_id: int,
    request: Request,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from ccr_facility import (
        get_or_create_daily,
        is_ccr_facility_building,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
        sync_prev_values,
    )

    building = await db.get(Building, building_id)
    if not building or not is_ccr_facility_building(building):
        raise HTTPException(404)
    form = await request.form()
    raw_date = form.get("log_date")
    try:
        d = date.fromisoformat(str(raw_date))
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류")
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building_id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    row.data = await sync_prev_values(db, building_id, d, row.data)
    row.updated_at = datetime.utcnow()
    await propagate_to_next_day(db, building_id, d, row.data)
    await db.commit()
    if request.headers.get("X-CCRF-Autosave") == "1":
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True, "data": row.data})
    from urllib.parse import quote

    return RedirectResponse(
        f"/admin/inspection-logs2/{building_id}/ccr-facility?tab=daily&date={d.isoformat()}&message={quote('저장되었습니다.')}",
        status_code=303,
    )


@app.post("/admin/inspection-logs2/{building_id}/ccr-facility/close-day")
async def ccr_facility_close_day(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from ccr_facility import (
        archive_daily_excel,
        get_or_create_daily,
        is_ccr_facility_building,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
        sync_prev_values,
    )

    building = await db.get(Building, building_id)
    if not building or not is_ccr_facility_building(building):
        raise HTTPException(404)
    form = await request.form()
    try:
        d = date.fromisoformat(str(form.get("log_date")))
    except ValueError:
        return RedirectResponse(
            f"/admin/inspection-logs2/{building_id}/ccr-facility?error={quote('날짜 형식 오류')}",
            status_code=303,
        )
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building_id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    row.data = await sync_prev_values(db, building_id, d, row.data)
    await archive_daily_excel(db, building_id, d)
    tomorrow = d + timedelta(days=1)
    await propagate_to_next_day(db, building_id, d, row.data)
    await get_or_create_daily(db, building_id, tomorrow)
    await db.commit()
    return RedirectResponse(
        f"/admin/inspection-logs2/{building_id}/ccr-facility?tab=daily&date={tomorrow.isoformat()}&message={quote('마감 완료')}",
        status_code=303,
    )


@app.get("/admin/inspection-logs2/{building_id}/ccr-facility/export/daily")
async def ccr_facility_export_daily(
    building_id: int,
    log_date: str = Query(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from ccr_facility import export_daily_to_excel, get_or_create_daily, is_ccr_facility_building

    building = await db.get(Building, building_id)
    if not building or not is_ccr_facility_building(building):
        raise HTTPException(404)
    d = date.fromisoformat(log_date)
    row = await get_or_create_daily(db, building_id, d)
    xbytes = export_daily_to_excel(row.data or {}, d)
    fname = quote(f"중앙관제실설비_1일_{d.isoformat()}.xlsx")
    return StreamingResponse(
        BytesIO(xbytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@app.get("/ccrf/{code}/daily")
async def ccr_facility_qr_daily(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    from ccr_facility import (
        ensure_tables as ensure_ccrf_tables,
        get_building_for_qr,
        get_or_create_daily,
        load_schema,
        sync_prev_values,
    )

    try:
        await _ensure_inspection_log2_tables()
        await ensure_ccrf_tables(engine)
    except Exception:
        pass
    building = await get_building_for_qr(db, code)
    if not building:
        raise HTTPException(404)
    today = _today_kst()
    raw_date = request.query_params.get("date")
    try:
        log_date = date.fromisoformat(raw_date) if raw_date else today
    except ValueError:
        log_date = today
    daily_row = await get_or_create_daily(db, building.id, log_date)
    daily_row.data = await sync_prev_values(db, building.id, log_date, daily_row.data or {})
    await db.commit()
    schema = load_schema()
    return templates.TemplateResponse(
        request,
        "ccr_facility.html",
        {
            "user": user,
            "building": building,
            "schema": schema,
            "tab": "daily",
            "log_date": log_date,
            "today": today,
            "year": log_date.year,
            "month": log_date.month,
            "daily_data": daily_row.data or {},
            "monthly": {"days": []},
            "yearly": {"months": []},
            "notes_list": {"entries": []},
            "qr_mode": True,
            "daily_save_url": f"/ccrf/{building.code}/daily/save",
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@app.post("/ccrf/{code}/daily/save")
async def ccr_facility_qr_save(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from ccr_facility import (
        get_building_for_qr,
        get_or_create_daily,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
        sync_prev_values,
    )

    building = await get_building_for_qr(db, code)
    if not building:
        raise HTTPException(404)
    form = await request.form()
    d = date.fromisoformat(str(form.get("log_date")))
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building.id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    row.data = await sync_prev_values(db, building.id, d, row.data)
    await propagate_to_next_day(db, building.id, d, row.data)
    await db.commit()
    if request.headers.get("X-CCRF-Autosave") == "1":
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True, "data": row.data})
    return RedirectResponse(
        f"/ccrf/{code}/daily?date={d.isoformat()}&message={quote('저장되었습니다.')}",
        status_code=303,
    )


'''

insert_at = text.index('@app.get("/admin/inspection-logs/{building_id}")')
text = text[:insert_at] + routes + text[insert_at:]
MAIN.write_text(text, encoding="utf-8")
print("patched OK")
