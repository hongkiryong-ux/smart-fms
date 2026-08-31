# -*- coding: utf-8
"""main.py에 제철소본부 라우트 연동."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
text = MAIN.read_text(encoding="utf-8")

if "steelworks_hq_page" in text:
    print("already patched")
    raise SystemExit(0)

if "SteelworksHqDaily" not in text:
    text = text.replace(
        "    CcrFacilityArchive,\n    CcrFacilityDaily,",
        "    SteelworksHqArchive,\n    SteelworksHqDaily,\n"
        "    CcrFacilityArchive,\n    CcrFacilityDaily,",
    )

redirect = """    from steelworks_hq import is_steelworks_hq_building

    if is_steelworks_hq_building(building):
        return RedirectResponse(
            f"/admin/inspection-logs2/{building_id}/steelworks-hq",
            status_code=303,
        )
    from ccr_facility import is_ccr_facility_building"""
text = text.replace(
    "    from ccr_facility import is_ccr_facility_building",
    redirect,
    1,
)

if "register_swhq_scheduler" not in text:
    text = text.replace(
        "            register_ccrf_scheduler(scheduler, AsyncSessionLocal, KST)\n            scheduler.start()",
        "            register_ccrf_scheduler(scheduler, AsyncSessionLocal, KST)\n"
        "            from steelworks_hq import register_scheduler as register_swhq_scheduler\n\n"
        "            register_swhq_scheduler(scheduler, AsyncSessionLocal, KST)\n"
        "            scheduler.start()",
    )

routes = r'''
@app.get("/admin/inspection-logs2/{building_id}/steelworks-hq/qr.png")
async def steelworks_hq_qr_png(
    building_id: int,
    request: Request,
    download: int = 0,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    import re
    from urllib.parse import quote

    from steelworks_hq import is_steelworks_hq_building, qr_png_bytes, sw_hq_daily_qr_url

    building = await db.get(Building, building_id)
    if not building or not is_steelworks_hq_building(building) or not building.code:
        raise HTTPException(404)
    url = sw_hq_daily_qr_url(building.code, request)
    data = qr_png_bytes(url)
    safe = re.sub(r"[^\w가-힣.\-]+", "_", (building.code or "swhq").strip()) or "swhq"
    filename = f"{safe}_1일QR.png"
    headers = {
        "Content-Disposition": (
            f"attachment; filename*=UTF-8''{quote(filename)}"
            if download
            else f"inline; filename*=UTF-8''{quote(filename)}"
        )
    }
    return StreamingResponse(iter([data]), media_type="image/png", headers=headers)


@app.get("/admin/inspection-logs2/{building_id}/steelworks-hq")
async def steelworks_hq_page(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote
    import calendar

    from steelworks_hq import (
        build_electrical_layouts,
        compute_monthly_report,
        compute_tr_temp_monthly,
        fetch_notes_list,
        fetch_yearly_report_data,
        get_or_create_daily,
        is_steelworks_hq_building,
        load_schema,
        sw_hq_daily_qr_url,
    )
    from steelworks_hq import ensure_tables as ensure_swhq_tables

    try:
        await _ensure_inspection_log2_tables()
        await ensure_swhq_tables(engine)
    except Exception as e:
        print(f"[swhq] ensure: {e}", flush=True)

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
    if not is_steelworks_hq_building(building):
        return RedirectResponse(f"/admin/inspection-logs2/{building_id}", status_code=303)

    tab = request.query_params.get("tab") or "daily"
    today = _today_kst()
    schema = load_schema()
    electrical_layouts = build_electrical_layouts(schema)
    qr_url = sw_hq_daily_qr_url(building.code or "", request) if building.code else ""

    if tab == "monthly":
        try:
            year = int(request.query_params.get("year") or today.year)
            month = int(request.query_params.get("month") or today.month)
        except ValueError:
            year, month = today.year, today.month
        last_day = calendar.monthrange(year, month)[1]
        d_from = date(year, month, 1)
        daily_rows = (
            await db.execute(
                select(SteelworksHqDaily).where(
                    SteelworksHqDaily.building_id == building_id,
                    SteelworksHqDaily.log_date >= d_from,
                    SteelworksHqDaily.log_date <= date(year, month, last_day),
                )
            )
        ).scalars().all()
        prev_month_row = (
            await db.execute(
                select(SteelworksHqDaily).where(
                    SteelworksHqDaily.building_id == building_id,
                    SteelworksHqDaily.log_date == d_from - timedelta(days=1),
                )
            )
        ).scalar_one_or_none()
        monthly = compute_monthly_report(building_id, year, month, list(daily_rows), prev_month_row)
        monthly["transformer"] = compute_tr_temp_monthly(year, month, list(daily_rows))
        yearly = {"sections": []}
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
        monthly = {"sections": []}
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
        monthly = {"sections": []}
        yearly = {"sections": []}
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
        await db.commit()
        daily_data = daily_row.data or {}
        year, month = log_date.year, log_date.month
        monthly = {"sections": []}
        yearly = {"sections": []}
        notes_list = {"entries": []}

    return templates.TemplateResponse(
        request,
        "steelworks_hq.html",
        {
            "user": user,
            "building": building,
            "schema": schema,
            "electrical_layouts": electrical_layouts,
            "tab": tab,
            "log_date": log_date,
            "today": today,
            "year": year,
            "month": month,
            "daily_data": daily_data,
            "monthly": monthly,
            "yearly": yearly,
            "notes_list": notes_list,
            "qr_url": qr_url,
            "qr_mode": False,
            "daily_save_url": f"/admin/inspection-logs2/{building_id}/steelworks-hq/save",
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@app.post("/admin/inspection-logs2/{building_id}/steelworks-hq/save")
async def steelworks_hq_save(
    building_id: int,
    request: Request,
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from steelworks_hq import (
        get_or_create_daily,
        is_steelworks_hq_building,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
    )

    building = await db.get(Building, building_id)
    if not building or not is_steelworks_hq_building(building):
        raise HTTPException(404)
    form = await request.form()
    d = date.fromisoformat(str(form.get("log_date")))
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building_id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    row.updated_at = datetime.utcnow()
    await propagate_to_next_day(db, building_id, d, row.data)
    await db.commit()
    if request.headers.get("X-SWHQ-Autosave") == "1":
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True, "data": row.data})
    from urllib.parse import quote

    return RedirectResponse(
        f"/admin/inspection-logs2/{building_id}/steelworks-hq?tab=daily&date={d.isoformat()}&message={quote('저장되었습니다.')}",
        status_code=303,
    )


@app.post("/admin/inspection-logs2/{building_id}/steelworks-hq/close-day")
async def steelworks_hq_close_day(
    building_id: int,
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from steelworks_hq import (
        archive_daily_excel,
        get_or_create_daily,
        is_steelworks_hq_building,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
    )

    building = await db.get(Building, building_id)
    if not building or not is_steelworks_hq_building(building):
        raise HTTPException(404)
    form = await request.form()
    d = date.fromisoformat(str(form.get("log_date")))
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building_id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    await archive_daily_excel(db, building_id, d)
    tomorrow = d + timedelta(days=1)
    await propagate_to_next_day(db, building_id, d, row.data)
    await get_or_create_daily(db, building_id, tomorrow)
    await db.commit()
    return RedirectResponse(
        f"/admin/inspection-logs2/{building_id}/steelworks-hq?tab=daily&date={tomorrow.isoformat()}&message={quote('마감 완료')}",
        status_code=303,
    )


@app.get("/admin/inspection-logs2/{building_id}/steelworks-hq/export/daily")
async def steelworks_hq_export_daily(
    building_id: int,
    log_date: str = Query(...),
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from steelworks_hq import export_daily_to_excel, get_or_create_daily, is_steelworks_hq_building

    building = await db.get(Building, building_id)
    if not building or not is_steelworks_hq_building(building):
        raise HTTPException(404)
    d = date.fromisoformat(log_date)
    row = await get_or_create_daily(db, building_id, d)
    xbytes = export_daily_to_excel(row.data or {}, d)
    fname = quote(f"제철소본부_1일_{d.isoformat()}.xlsx")
    return StreamingResponse(
        BytesIO(xbytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@app.get("/swhq/{code}/daily")
async def steelworks_hq_qr_daily(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    from steelworks_hq import (
        build_electrical_layouts,
        ensure_tables as ensure_swhq_tables,
        get_building_for_qr,
        get_or_create_daily,
        load_schema,
    )

    try:
        await _ensure_inspection_log2_tables()
        await ensure_swhq_tables(engine)
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
    await db.commit()
    schema = load_schema()
    return templates.TemplateResponse(
        request,
        "steelworks_hq.html",
        {
            "user": user,
            "building": building,
            "schema": schema,
            "electrical_layouts": build_electrical_layouts(schema),
            "tab": "daily",
            "log_date": log_date,
            "today": today,
            "year": log_date.year,
            "month": log_date.month,
            "daily_data": daily_row.data or {},
            "monthly": {"sections": []},
            "yearly": {"sections": []},
            "notes_list": {"entries": []},
            "qr_mode": True,
            "daily_save_url": f"/swhq/{building.code}/daily/save",
            "qr_url": "",
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@app.post("/swhq/{code}/daily/save")
async def steelworks_hq_qr_save(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    from steelworks_hq import (
        get_building_for_qr,
        get_or_create_daily,
        merge_daily_save,
        parse_daily_form,
        propagate_to_next_day,
    )

    building = await get_building_for_qr(db, code)
    if not building:
        raise HTTPException(404)
    form = await request.form()
    d = date.fromisoformat(str(form.get("log_date")))
    posted = parse_daily_form(form)
    row = await get_or_create_daily(db, building.id, d)
    row.data = merge_daily_save(row.data or {}, posted)
    await propagate_to_next_day(db, building.id, d, row.data)
    await db.commit()
    if request.headers.get("X-SWHQ-Autosave") == "1":
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True, "data": row.data})
    return RedirectResponse(
        f"/swhq/{code}/daily?date={d.isoformat()}&message={quote('저장되었습니다.')}",
        status_code=303,
    )


'''

insert_at = text.index('@app.get("/admin/inspection-logs/{building_id}")')
text = text[:insert_at] + routes + text[insert_at:]
MAIN.write_text(text, encoding="utf-8")
print("patched")
