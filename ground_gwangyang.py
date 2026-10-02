# -*- coding: utf-8 -*-
"""GROUND광양 운영일보 — 1일·월보·년보·특이사항."""
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

from models import Building, GroundGwangyangArchive, GroundGwangyangDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "ground_gwangyang_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    from ilog2_labels import apply_label_overrides
    return apply_label_overrides("ground_gwangyang", _schema_cache)


def is_ground_gwangyang_building(building: Building | None) -> bool:
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
            "cat": power.get("cat") or power.get("title") or "전력",
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


def _time_ids(schema: dict) -> list[str]:
    return [t["id"] for t in schema.get("times") or []] or ["am", "pm"]


def equip_lines(schema: dict, row: dict) -> list[dict]:
    """설비 행의 시간대별 줄 (줄마다 항목 문구가 다르면 lines 로 정의)."""
    labels = {t["id"]: t.get("label") or t["id"] for t in schema.get("times") or []}
    lines = row.get("lines") or [{"time": t} for t in _time_ids(schema)]
    return [
        {
            **line,
            "time_label": labels.get(line["time"], line["time"]),
            "fields": line.get("fields") or row.get("fields") or [],
        }
        for line in lines
    ]


def _equip_field_ids(schema: dict, row: dict) -> list[str]:
    ids: list[str] = []
    for line in equip_lines(schema, row):
        for field in line["fields"]:
            if field["id"] not in ids:
                ids.append(field["id"])
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
    times = _time_ids(schema)
    elec_fields = [f["id"] for f in (schema.get("elec") or {}).get("fields") or []]
    elec = {t: {"time": "", **{fid: "" for fid in elec_fields}} for t in times}
    equip = {}
    for row in (schema.get("equip") or {}).get("rows") or []:
        fids = _equip_field_ids(schema, row)
        equip[row["id"]] = {
            line["time"]: {"time": "", "result": "", **{fid: "" for fid in fids}}
            for line in equip_lines(schema, row)
        }
    return {
        "utility": utility,
        "elec": elec,
        "equip": equip,
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
        elif key.startswith("el__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["elec"] and fid in data["elec"][t]:
                data["elec"][t][fid] = raw
        elif key.startswith("eq__") and len(parts) == 4:
            rid, t, fid = parts[1], parts[2], parts[3]
            block = data["equip"].get(rid) or {}
            if t in block and fid in block[t]:
                data["equip"][rid][t][fid] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> GroundGwangyangDaily | None:
    return (
        await session.execute(
            select(GroundGwangyangDaily).where(
                GroundGwangyangDaily.building_id == building_id,
                GroundGwangyangDaily.log_date == log_date,
            )
        )
    ).scalar_one_or_none()


async def _monthly_base(session: AsyncSession, building_id: int, log_date: date) -> dict[str, str]:
    """유틸리티 monthly_ids: 이달 1일~전날 일사용량 합계 (달이 바뀌면 0부터 다시 누계)."""
    return await utility_month_base(
        session, GroundGwangyangDaily, building_id, log_date,
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
            select(GroundGwangyangDaily)
            .where(
                GroundGwangyangDaily.building_id == building_id,
                GroundGwangyangDaily.log_date < log_date,
                GroundGwangyangDaily.log_date >= log_date - timedelta(days=PREV_LOOKBACK_DAYS),
            )
            .order_by(GroundGwangyangDaily.log_date.desc())
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
) -> GroundGwangyangDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = GroundGwangyangDaily(building_id=building_id, log_date=log_date, data=data)
    session.add(row)
    await session.flush()
    return row


async def propagate_to_next_day(
    session: AsyncSession, building_id: int, log_date: date, saved_data: dict
) -> None:
    await session.flush()
    next_row = (
        await session.execute(
            select(GroundGwangyangDaily)
            .where(GroundGwangyangDaily.building_id == building_id, GroundGwangyangDaily.log_date > log_date)
            .order_by(GroundGwangyangDaily.log_date)
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
    daily_rows: list[GroundGwangyangDaily],
    prev_month_last_row: GroundGwangyangDaily | None = None,
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
            select(GroundGwangyangDaily).where(
                GroundGwangyangDaily.building_id == building_id,
                GroundGwangyangDaily.log_date >= date(year, 1, 1),
                GroundGwangyangDaily.log_date <= date(year, 12, 31),
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
            select(GroundGwangyangDaily)
            .where(
                GroundGwangyangDaily.building_id == building_id,
                GroundGwangyangDaily.log_date >= date(year, month, 1),
                GroundGwangyangDaily.log_date <= date(year, month, calendar.monthrange(year, month)[1]),
            )
            .order_by(GroundGwangyangDaily.log_date.asc())
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
    return building if is_ground_gwangyang_building(building) else None


def ggy_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/ggy/{(building_code or '').strip()}/daily"


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> GroundGwangyangArchive | None:
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(GroundGwangyangArchive).where(
                GroundGwangyangArchive.building_id == building_id,
                GroundGwangyangArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = export_daily_to_excel(row.data or {}, log_date)
    filename = f"GROUND광양_운영일보_{log_date.isoformat()}.xlsx"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = GroundGwangyangArchive(
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
                CREATE TABLE IF NOT EXISTS ground_gwangyang_daily (
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
                CREATE TABLE IF NOT EXISTS ground_gwangyang_archives (
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
                CREATE TABLE IF NOT EXISTS ground_gwangyang_daily (
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
                CREATE TABLE IF NOT EXISTS ground_gwangyang_archives (
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


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _extra_sheets(schema: dict, data: dict) -> list[tuple[str, list[str], list[list[Any]]]]:
    sheets: list[tuple[str, list[str], list[list[Any]]]] = []
    time_labels = {t["id"]: _label(t) for t in schema.get("times") or []}

    elec = schema.get("elec") or {}
    fields = elec.get("fields") or []
    headers = ["기준", "시간"] + [_label(f) for f in fields]
    rows: list[list[Any]] = [
        [_label(elec.get("std_row"), "title") or "관리기준", ""]
        + [_one_line(f.get("std", "")) for f in fields]
    ]
    for t in _time_ids(schema):
        cell = (data.get("elec") or {}).get(t) or {}
        rows.append([time_labels.get(t, t), cell.get("time", "")] + [cell.get(f["id"], "") for f in fields])
    sheets.append((_label(elec, "title") or "전기", headers, rows))

    equip = schema.get("equip") or {}
    slots = int(equip.get("slots") or 3)
    headers = ["구분", "기준", "시간"] + [f"항목{i + 1}" for i in range(slots)] + ["점검결과", "비고"]
    rows = []
    for row in equip.get("rows") or []:
        block = (data.get("equip") or {}).get(row["id"]) or {}
        for line in equip_lines(schema, row):
            cell = block.get(line["time"]) or {}
            items = []
            for field in line["fields"]:
                value = str(cell.get(field["id"], "") or "")
                unit = _label(field, "unit")
                items.append(" ".join(p for p in (_label(field), value, unit if value else "") if p))
            items += [""] * (slots - len(items))
            note = line.get("note") if "note" in line else row.get("note", "")
            rows.append(
                [_one_line(_label(row, "title")), line["time_label"], cell.get("time", "")]
                + items[:slots]
                + [cell.get("result", ""), _one_line(note or "")]
            )
    sheets.append(("설비", headers, rows))
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
    title = str(schema.get("title") or schema.get("building_name") or "GROUND광양")
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
    title = str(schema.get("title") or schema.get("building_name") or "GROUND광양")
    report = {**monthly_report, "meters": _export_meters(monthly_report.get("meters") or [], True)}
    return export_monthly_utility_workbook(report, title=title)


def export_yearly_to_excel(yearly_report: dict) -> bytes:
    from inspection_log2_export import export_yearly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "GROUND광양")
    report = {**yearly_report, "meters": _export_meters(yearly_report.get("meters") or [], True)}
    return export_yearly_utility_workbook(report, title=title)


async def ensure_registered(session: AsyncSession) -> bool:
    """GROUND광양 건물·점검일지 등록을 보장한다. 변경이 있으면 True."""
    from models import InspectionLogBuilding2, Site

    changed = False
    buildings = (
        await session.execute(select(Building).where(Building.is_active == True))  # noqa: E712
    ).scalars().all()
    building = next((b for b in buildings if is_ground_gwangyang_building(b)), None)
    if not building:
        site = (
            await session.execute(
                select(Site).where(Site.is_active == True).order_by(Site.id.asc())  # noqa: E712
            )
        ).scalars().first()
        if site is None:
            site = (await session.execute(select(Site).order_by(Site.id.asc()))).scalars().first()
        if not site:
            return False
        code = "GROUND-GY"
        exists_code = (
            await session.execute(select(Building).where(Building.code == code))
        ).scalar_one_or_none()
        if exists_code:
            code = f"GROUND-GY-{site.id}"
        building = Building(
            site_id=site.id,
            name="GROUND광양",
            code=code,
            is_active=True,
            description="GROUND광양 운영일보",
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
                print(f"[ggy] ensure_registered: {exc}", flush=True)
            rows = (
                await session.execute(
                    select(InspectionLogBuilding2.building_id, Building).join(
                        Building, Building.id == InspectionLogBuilding2.building_id
                    )
                )
            ).all()
            today = datetime.now(kst).date()
            for building_id, building in rows:
                if not is_ground_gwangyang_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[ggy] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="ggy_rollover",
        replace_existing=True,
    )


if __name__ == "__main__":
    from types import SimpleNamespace

    schema = load_schema()
    assert schema.get("building_name") == "GROUND광양"
    assert is_ground_gwangyang_building(SimpleNamespace(name="GROUND 광양"))
    assert is_ground_gwangyang_building(SimpleNamespace(name="그라운드광양"))
    assert not is_ground_gwangyang_building(SimpleNamespace(name="RIST"))

    payload = empty_daily_payload()
    assert {"power_mid", "power_peak", "power_off", "energy", "water", "gas"} <= set(payload["utility"])
    assert "tr" in payload["elec"]["pm"]
    assert "ref" in payload["equip"]["ghp"]["pm"]
    assert "level" in payload["equip"]["pump"]["am"]
    assert "result" in payload["equip"]["temp"]["am"]

    form = {
        "u__power_mid__prev": "100.5",
        "u__power_mid__today": "101",
        "u__water__prev": "10",
        "u__water__today": "13",
        "el__am__time": "09:00",
        "el__pm__tr": "45",
        "eq__ghp__pm__ref": "101.3",
        "eq__pump__am__level": "Normal",
        "eq__temp__am__result": "양호",
        "notes": "점검완료",
    }
    parsed = recompute_daily(parse_daily_form(form))
    assert parsed["utility"]["power_mid"]["daily"] == "300"
    assert parsed["utility"]["water"]["daily"] == "3"
    assert parsed["elec"]["am"]["time"] == "09:00"
    assert parsed["elec"]["pm"]["tr"] == "45"
    assert parsed["equip"]["ghp"]["pm"]["ref"] == "101.3"
    assert parsed["equip"]["pump"]["am"]["level"] == "Normal"

    xbytes = export_daily_to_excel(parsed, date(2026, 10, 2))
    assert xbytes[:2] == b"PK"
    print("ground_gwangyang smoke OK")
