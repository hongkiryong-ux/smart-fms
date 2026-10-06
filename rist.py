# -*- coding: utf-8 -*-
"""RIST 운영일보 — 1일·월보·년보·특이사항."""
from __future__ import annotations

import io
import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ilog2_monthly import utility_month_base

from models import Building, RistArchive, RistDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "rist_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    from ilog2_labels import apply_label_overrides
    return apply_label_overrides("rist", _schema_cache)


def is_rist_building(building: Building | None) -> bool:
    if not building:
        return False
    compact = (building.name or "").replace(" ", "").upper()
    if not compact:
        return False
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").replace(" ", "").upper(),
        *(str(v).replace(" ", "").upper() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    return compact in names


def _utility_rows(schema: dict | None = None) -> list[dict]:
    schema = schema or load_schema()
    utility = schema.get("utility") or {}
    power = utility.get("power") or {}
    rows: list[dict] = []
    for band in power.get("bands") or []:
        rows.append({
            **band,
            "cat": power.get("title") or "전력",
            "building": power.get("building") or "",
            "unit": "kWh",
            "multiplier": power.get("multiplier", 1),
        })
    for meter in utility.get("meters") or []:
        rows.append({
            **meter,
            "cat": meter.get("label") or "",
            "building": "",
            "multiplier": meter.get("multiplier", 1),
        })
    return rows


def _time_block(times: list[str], field_ids: list[str]) -> dict[str, dict[str, str]]:
    return {t: {"time": "", **{fid: "" for fid in field_ids}} for t in times or ["t1"]}


def _elec_field_ids(schema: dict, building: dict) -> list[str]:
    elec = schema.get("elec") or {}
    ids = [
        f["id"] for f in elec.get("fields") or []
        if building.get("meter", True) or not f.get("meter")
    ]
    if building.get("tr"):
        ids += [f["id"] for f in elec.get("tr_fields") or []]
    return ids


def empty_daily_payload() -> dict[str, Any]:
    schema = load_schema()
    utility = {
        row["id"]: {
            "prev": "",
            "today": "",
            "daily": "",
            "monthly": "",
            "remark": "",
            "prev_manual": False,
        }
        for row in _utility_rows(schema)
    }
    elec_sec = schema.get("elec") or {}
    elec = {
        b["id"]: _time_block(b.get("times") or [], _elec_field_ids(schema, b))
        for b in elec_sec.get("buildings") or []
    }
    solar_sec = schema.get("solar") or {}
    solar_fields = [f["id"] for f in solar_sec.get("fields") or []]
    solar = {row["id"]: {fid: "" for fid in solar_fields} for row in solar_sec.get("rows") or []}
    mech_sec = schema.get("mech") or {}
    water_sec = schema.get("water") or {}
    return {
        "utility": utility,
        "elec": elec,
        "solar": solar,
        "mech": _time_block(mech_sec.get("times") or [], [f["id"] for f in mech_sec.get("fields") or []]),
        "water": _time_block(water_sec.get("times") or [], [f["id"] for f in water_sec.get("fields") or []]),
        "notes": "",
    }


def _parse_num(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def recompute_daily(
    data: dict, prev_monthly: dict[str, str] | None = None
) -> dict[str, Any]:
    out = deepcopy(empty_daily_payload())
    _deep_merge(out, data or {})
    previous = prev_monthly or {}
    for definition in _utility_rows():
        uid = definition["id"]
        row = out["utility"].setdefault(uid, {})
        prev = _parse_num(row.get("prev"))
        today = _parse_num(row.get("today"))
        multiplier = float(definition.get("multiplier") or 1)
        daily = (
            (today - prev) * multiplier
            if prev is not None and today is not None
            else None
        )
        row["daily"] = _fmt_num(daily)
        base = _parse_num(previous.get(uid))
        row["monthly"] = _fmt_num((base or 0) + daily) if daily is not None else ""
    return out


def merge_daily_save(
    existing: dict, posted: dict, prev_monthly: dict[str, str] | None = None
) -> dict:
    out = deepcopy(existing or empty_daily_payload())
    _deep_merge(out, posted or {})
    return recompute_daily(out, prev_monthly)


def parse_daily_form(form) -> dict:
    data = empty_daily_payload()
    items = form.multi_items() if hasattr(form, "multi_items") else form.items()
    for key, value in items:
        if not isinstance(key, str):
            continue
        raw = (value or "").strip() if isinstance(value, str) else str(value or "")
        parts = key.split("__")
        if key.startswith("u__") and len(parts) == 3:
            uid, field = parts[1], parts[2]
            if uid not in data["utility"] or field not in {
                "prev", "today", "remark", "prev_manual"
            }:
                continue
            data["utility"][uid][field] = raw == "1" if field == "prev_manual" else raw
        elif key.startswith("el__") and len(parts) == 4:
            bid, t, fid = parts[1], parts[2], parts[3]
            block = data["elec"].get(bid) or {}
            if t in block and fid in block[t]:
                data["elec"][bid][t][fid] = raw
        elif key.startswith("sol__") and len(parts) == 3:
            rid, fid = parts[1], parts[2]
            if rid in data["solar"] and fid in data["solar"][rid]:
                data["solar"][rid][fid] = raw
        elif key.startswith("me__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["mech"] and fid in data["mech"][t]:
                data["mech"][t][fid] = raw
        elif key.startswith("wa__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["water"] and fid in data["water"][t]:
                data["water"][t][fid] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> RistDaily | None:
    return (
        await session.execute(
            select(RistDaily).where(
                RistDaily.building_id == building_id,
                RistDaily.log_date == log_date,
            )
        )
    ).scalar_one_or_none()


async def _monthly_base(session: AsyncSession, building_id: int, log_date: date) -> dict[str, str]:
    """유틸리티 monthly_ids: 이달 1일~전날 일사용량 합계 (달이 바뀌면 0부터 다시 누계)."""
    return await utility_month_base(
        session, RistDaily, building_id, log_date,
        (load_schema().get("utility") or {}).get("monthly_ids") or [],
        recompute_daily, _parse_num, _fmt_num,
    )


PREV_LOOKBACK_DAYS = 62


def _is_prev_manual(row: dict) -> bool:
    return bool(row.get("prev_manual")) and bool(str(row.get("prev") or "").strip())


async def _latest_today_values(
    session: AsyncSession, building_id: int, log_date: date
) -> dict[str, str]:
    """항목별로 log_date 이전 가장 최근에 기록된 금일지침 (빠진 날이 있어도 이월)."""
    rows = (
        await session.execute(
            select(RistDaily)
            .where(
                RistDaily.building_id == building_id,
                RistDaily.log_date < log_date,
                RistDaily.log_date >= log_date - timedelta(days=PREV_LOOKBACK_DAYS),
            )
            .order_by(RistDaily.log_date.desc())
        )
    ).scalars().all()
    uids = [row["id"] for row in _utility_rows()]
    found: dict[str, str] = {}
    for row in rows:
        utility = (row.data or {}).get("utility") or {}
        for uid in uids:
            if uid in found:
                continue
            value = str((utility.get(uid) or {}).get("today") or "").strip()
            if value:
                found[uid] = value
        if len(found) == len(uids):
            break
    return found


async def sync_prev_values(
    session: AsyncSession, building_id: int, log_date: date, data: dict
) -> tuple[dict, bool]:
    carried = await _latest_today_values(session, building_id, log_date)
    monthly = await _monthly_base(session, building_id, log_date)
    out = merge_daily_save(empty_daily_payload(), data, monthly)
    before = deepcopy(data or {})
    for uid, row in out["utility"].items():
        if _is_prev_manual(row):
            row["prev_manual"] = True
        else:
            row["prev_manual"] = False
            row["prev"] = carried.get(uid, "")
    out = recompute_daily(out, monthly)
    return out, out != before


async def finalize_daily_save(
    session: AsyncSession,
    building_id: int,
    log_date: date,
    existing: dict,
    posted: dict,
) -> dict:
    """전일지침은 화면의 수기 플래그(prev_manual)가 있을 때만 수기값으로 유지한다.
    플래그 없이 넘어온 값(이월 전 열어 둔 화면의 빈 값 등)은 버리고 다시 이월한다."""
    monthly = await _monthly_base(session, building_id, log_date)
    posted = deepcopy(posted or {})
    for values in (posted.get("utility") or {}).values():
        values["prev_manual"] = _is_prev_manual(values)
    merged = merge_daily_save(existing, posted, monthly)
    synced, _ = await sync_prev_values(session, building_id, log_date, merged)
    return synced


async def get_or_create_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> RistDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = RistDaily(building_id=building_id, log_date=log_date, data=data)
    session.add(row)
    await session.flush()
    return row


async def propagate_to_next_day(
    session: AsyncSession, building_id: int, log_date: date, saved_data: dict
) -> None:
    await session.flush()
    next_row = (
        await session.execute(
            select(RistDaily)
            .where(RistDaily.building_id == building_id, RistDaily.log_date > log_date)
            .order_by(RistDaily.log_date)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not next_row or (next_row.log_date - log_date).days > PREV_LOOKBACK_DAYS:
        return
    synced, changed = await sync_prev_values(
        session, building_id, next_row.log_date, next_row.data or {}
    )
    if changed:
        next_row.data = synced
        next_row.updated_at = datetime.utcnow()


def compute_monthly_report(
    year: int,
    month: int,
    daily_rows: list[RistDaily],
    prev_month_last_row: RistDaily | None = None,
) -> dict[str, Any]:
    import calendar

    schema = load_schema()
    del prev_month_last_row
    monthly_ids = list((schema.get("utility") or {}).get("monthly_ids") or [])
    definitions = {row["id"]: row for row in _utility_rows(schema)}
    meters = [definitions[uid] for uid in monthly_ids if uid in definitions]
    by_date = {row.log_date: row.data or {} for row in daily_rows}
    running = {uid: 0.0 for uid in monthly_ids}
    days = []
    totals = {uid: 0.0 for uid in monthly_ids}
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        log_date = date(year, month, day)
        computed = recompute_daily(by_date.get(log_date, {}))
        values = {}
        for uid in monthly_ids:
            daily = _parse_num((computed.get("utility", {}).get(uid) or {}).get("daily"))
            if daily is not None:
                totals[uid] += daily
                running[uid] += daily
            values[uid] = {
                "daily": _fmt_num(daily),
                "monthly": _fmt_num(running[uid]) if daily is not None or running[uid] else "",
            }
        days.append({"day": day, "date": log_date.isoformat(), "utility": values})
    return {
        "year": year,
        "month": month,
        "meters": meters,
        "days": days,
        "totals": {uid: _fmt_num(value) if value else "" for uid, value in totals.items()},
    }


async def fetch_yearly_report_data(
    session: AsyncSession, building_id: int, year: int
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(RistDaily).where(
                RistDaily.building_id == building_id,
                RistDaily.log_date >= date(year, 1, 1),
                RistDaily.log_date <= date(year, 12, 31),
            )
        )
    ).scalars().all()
    monthly_ids = list((load_schema().get("utility") or {}).get("monthly_ids") or [])
    months = []
    totals = {uid: 0.0 for uid in monthly_ids}
    for month in range(1, 13):
        report = compute_monthly_report(
            year, month, [row for row in rows if row.log_date.month == month]
        )
        usage = {}
        for uid in monthly_ids:
            value = _parse_num(report["totals"].get(uid))
            if value is not None:
                totals[uid] += value
                usage[uid] = _fmt_num(value)
            else:
                usage[uid] = ""
        months.append({"month": month, "utility": usage})
    return {
        "year": year,
        "meters": [row for row in _utility_rows() if row["id"] in monthly_ids],
        "months": months,
        "totals": {uid: _fmt_num(value) if value else "" for uid, value in totals.items()},
    }


async def fetch_notes_list(
    session: AsyncSession, building_id: int, year: int, month: int
) -> dict[str, Any]:
    import calendar

    rows = (
        await session.execute(
            select(RistDaily)
            .where(
                RistDaily.building_id == building_id,
                RistDaily.log_date >= date(year, month, 1),
                RistDaily.log_date <= date(year, month, calendar.monthrange(year, month)[1]),
            )
            .order_by(RistDaily.log_date.asc())
        )
    ).scalars().all()
    entries = []
    for row in rows:
        text = str((row.data or {}).get("notes") or "").strip()
        if text:
            entries.append({
                "date": row.log_date.isoformat(),
                "day": row.log_date.day,
                "items": [{"time": "", "section": "특이사항", "text": text}],
            })
    return {"year": year, "month": month, "entries": entries}


async def get_building_for_qr(session: AsyncSession, code: str) -> Building | None:
    from models import InspectionLogBuilding2

    building = (
        await session.execute(
            select(Building)
            .join(InspectionLogBuilding2, InspectionLogBuilding2.building_id == Building.id)
            .where(
                Building.code == (code or "").strip(),
                Building.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    return building if is_rist_building(building) else None


def rist_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/rist/{(building_code or '').strip()}/daily"


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> RistArchive | None:
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(RistArchive).where(
                RistArchive.building_id == building_id,
                RistArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = export_daily_to_excel(row.data or {}, log_date)
    filename = f"RIST_운영일보_{log_date.isoformat()}.xlsx"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = RistArchive(
            building_id=building_id,
            log_date=log_date,
            original_name=filename,
            file_data=payload,
            file_size=len(payload),
        )
        session.add(archive)
    await session.flush()
    return archive


async def rollover_at_midnight(
    session: AsyncSession, building_id: int, closing_date: date
) -> None:
    row = await get_daily_row(session, building_id, closing_date)
    if row:
        await _archive_daily(session, building_id, closing_date)
    await get_or_create_daily(session, building_id, closing_date + timedelta(days=1))
    if row:
        await propagate_to_next_day(session, building_id, closing_date, row.data or {})
    await session.commit()


async def ensure_tables(engine) -> None:
    from sqlalchemy import text

    is_pg = "postgres" in str(engine.url).lower()
    async with engine.begin() as connection:
        if is_pg:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS rist_daily (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS rist_archives (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BYTEA,
                    file_size INTEGER,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    UNIQUE (building_id, log_date)
                )
            """))
        else:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS rist_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME,
                    updated_at DATETIME,
                    UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS rist_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BLOB,
                    file_size INTEGER,
                    created_at DATETIME,
                    UNIQUE (building_id, log_date)
                )
            """))


def _label(node: dict | None, key: str = "label") -> str:
    return str((node or {}).get(key) or "").strip()


def _field_header(field: dict) -> str:
    parts = [_label(field, "group"), _label(field)]
    head = " ".join(p for p in parts if p)
    unit = _label(field, "unit")
    return f"{head}({unit})" if unit else head


def _extra_sheets(schema: dict, data: dict) -> list[tuple[str, list[str], list[list[Any]]]]:
    sheets: list[tuple[str, list[str], list[list[Any]]]] = []

    elec = schema.get("elec") or {}
    tr_fields = elec.get("tr_fields") or []
    elec_fields = list(elec.get("fields") or []) + [
        {**f, "label": f"{_label(elec.get('tr'), 'title')} {_label(f)}".strip()} for f in tr_fields
    ]
    headers = ["구분", "시간"] + [_field_header(f) for f in elec_fields]
    rows: list[list[Any]] = []
    for b in elec.get("buildings") or []:
        allowed = set(_elec_field_ids(schema, b))
        block = (data.get("elec") or {}).get(b["id"]) or {}
        for idx, t in enumerate(b.get("times") or []):
            cell = block.get(t) or {}
            row_allowed = allowed
            if b.get("tr") == "single" and idx > 0:
                row_allowed = allowed - {f["id"] for f in tr_fields}
            rows.append(
                [_label(b), cell.get("time", "")]
                + [cell.get(f["id"], "") if f["id"] in row_allowed else "-" for f in elec_fields]
            )
    sheets.append((_label(elec, "title") or "전기", headers, rows))

    solar = schema.get("solar") or {}
    s_fields = solar.get("fields") or []
    headers = [_label(solar.get("location"), "title") or "위치"] + [_label(f) for f in s_fields]
    rows = []
    for row in solar.get("rows") or []:
        cell = (data.get("solar") or {}).get(row["id"]) or {}
        loc = " ".join(p for p in (_label(row, "group"), _label(row), _label(row, "sub_label")) if p)
        rows.append([loc] + [cell.get(f["id"], "") for f in s_fields])
    sheets.append((_label(solar, "title") or "태양광", headers, rows))

    for key, fallback in (("mech", "기계"), ("water", "급수")):
        sec = schema.get(key) or {}
        fields = sec.get("fields") or []
        headers = ["시간"] + [_field_header(f) for f in fields]
        block = data.get(key) or {}
        rows = []
        for t in sec.get("times") or []:
            cell = block.get(t) or {}
            rows.append([cell.get("time", "")] + [cell.get(f["id"], "") for f in fields])
        sheets.append(((_label(sec, "title") or fallback)[:31], headers, rows))
    return sheets


def _export_meters(meters: list[dict], with_cat: bool = False) -> list[dict]:
    """엑셀 항목명에 구분(전력 등)을 붙여 항목이 구분되게 함."""
    return [
        {**m, "label": " ".join(
            str(p).strip() for p in ((m.get("cat") if with_cat else ""), m.get("building"), m.get("label"))
            if str(p or "").strip()
        )}
        for m in meters
    ]


def export_daily_to_excel(data: dict, log_date: date) -> bytes:
    from inspection_log2_export import export_daily_utility_workbook

    schema = load_schema()
    data = recompute_daily(data or {})
    title = str(schema.get("title") or schema.get("building_name") or "RIST")
    return export_daily_utility_workbook(
        title=title,
        log_date=log_date,
        meter_defs=_export_meters(_utility_rows(schema)),
        utility=data.get("utility") or {},
        notes=str(data.get("notes") or ""),
        extra_sheets=_extra_sheets(schema, data),
    )


def export_monthly_to_excel(monthly_report: dict) -> bytes:
    from inspection_log2_export import export_monthly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "RIST")
    report = {**monthly_report, "meters": _export_meters(monthly_report.get("meters") or [], True)}
    return export_monthly_utility_workbook(report, title=title)


def export_yearly_to_excel(yearly_report: dict) -> bytes:
    from inspection_log2_export import export_yearly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "RIST")
    report = {**yearly_report, "meters": _export_meters(yearly_report.get("meters") or [], True)}
    return export_yearly_utility_workbook(report, title=title)


async def ensure_registered(session: AsyncSession) -> bool:
    """RIST 건물·점검일지 등록을 보장한다. 변경이 있으면 True."""
    from models import InspectionLogBuilding2, Site

    changed = False
    buildings = (
        await session.execute(select(Building).where(Building.is_active == True))  # noqa: E712
    ).scalars().all()
    building = next((b for b in buildings if is_rist_building(b)), None)
    if not building:
        site = (
            await session.execute(select(Site).where(Site.code == "RIST"))
        ).scalar_one_or_none()
        if site is None:
            site = (await session.execute(select(Site).order_by(Site.id.asc()))).scalars().first()
        if not site:
            return False
        code = "RIST"
        exists_code = (
            await session.execute(select(Building).where(Building.code == code))
        ).scalar_one_or_none()
        if exists_code:
            code = f"RIST-{site.id}"
        building = Building(
            site_id=site.id,
            name="RIST",
            code=code,
            is_active=True,
            description="RIST 운영일보",
        )
        session.add(building)
        await session.flush()
        changed = True
    linked = (
        await session.execute(
            select(InspectionLogBuilding2).where(
                InspectionLogBuilding2.building_id == building.id
            )
        )
    ).scalar_one_or_none()
    if not linked:
        session.add(InspectionLogBuilding2(building_id=building.id))
        await session.flush()
        changed = True
    return changed


def register_scheduler(scheduler, session_factory, kst) -> None:
    async def _daily_job() -> None:
        from models import InspectionLogBuilding2

        async with session_factory() as session:
            try:
                await ensure_registered(session)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                print(f"[rist] ensure_registered: {exc}", flush=True)
            rows = (
                await session.execute(
                    select(InspectionLogBuilding2.building_id, Building).join(
                        Building, Building.id == InspectionLogBuilding2.building_id
                    )
                )
            ).all()
            today = datetime.now(kst).date()
            for building_id, building in rows:
                if not is_rist_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[rist] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="rist_rollover",
        replace_existing=True,
    )


if __name__ == "__main__":
    from types import SimpleNamespace

    schema = load_schema()
    assert schema.get("building_name") == "RIST"
    assert is_rist_building(SimpleNamespace(name="RIST"))
    assert is_rist_building(SimpleNamespace(name="rist 운영일보"))
    assert not is_rist_building(SimpleNamespace(name="백운쇼핑센터"))

    payload = empty_daily_payload()
    assert {"power_mid", "power_peak", "power_off", "water", "gas"} <= set(payload["utility"])
    assert "tr1" in payload["elec"]["lab"]["t1"]
    assert "mid" not in payload["elec"]["exp"]["t1"]
    assert "comp_supply" in payload["mech"]["t1"]
    assert "fire_p" in payload["water"]["t2"]

    form = {
        "u__power_mid__prev": "100.5",
        "u__power_mid__today": "101",
        "u__water__prev": "10",
        "u__water__today": "13",
        "el__lab__t1__time": "09:00",
        "el__lab__t1__tr1": "45",
        "el__exp__t2__volt": "6.6",
        "sol__exp_bipv1__kw": "8.2",
        "me__t1__comp_supply": "7",
        "wa__t2__fire_level": "90",
        "notes": "점검완료",
    }
    parsed = recompute_daily(parse_daily_form(form))
    assert parsed["utility"]["power_mid"]["daily"] == "900"
    assert parsed["utility"]["water"]["daily"] == "3"
    assert parsed["elec"]["lab"]["t1"]["tr1"] == "45"
    assert parsed["elec"]["exp"]["t2"]["volt"] == "6.6"
    assert parsed["solar"]["exp_bipv1"]["kw"] == "8.2"
    assert parsed["mech"]["t1"]["comp_supply"] == "7"
    assert parsed["water"]["t2"]["fire_level"] == "90"

    xbytes = export_daily_to_excel(parsed, date(2026, 10, 2))
    assert xbytes[:2] == b"PK"
    print("rist smoke OK")
