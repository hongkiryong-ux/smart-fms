# -*- coding: utf-8 -*-
"""백운대 점검일지 — 1일·월보·년보·특이사항."""
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

from models import Building, BaegundaeArchive, BaegundaeDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "baegundae_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    return _schema_cache


def is_baegundae_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").strip(),
        *(str(v).strip() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    return name in names or name.startswith("백운대")


def _utility_rows(schema: dict | None = None) -> list[dict]:
    schema = schema or load_schema()
    utility = schema.get("utility") or {}
    rows: list[dict] = []
    meters = utility.get("meters") or []
    if meters:
        for meter in meters:
            rows.append({
                **meter,
                "cat": meter.get("cat") or "",
                "building": meter.get("building") or "",
                "item": meter.get("item") or meter.get("label") or "",
                "multiplier_key": meter.get("multiplier_key"),
                "multiplier": meter.get("multiplier", 1),
            })
        return rows
    for block in utility.get("blocks") or []:
        for row in block.get("rows") or []:
            rows.append({
                **row,
                "cat": block.get("cat") or "",
                "building": block.get("building") or "",
                "item": block.get("item") or "",
                "multiplier_key": block.get("multiplier_key"),
                "multiplier": row.get("multiplier", 1),
            })
    return rows


def _default_multipliers(schema: dict | None = None) -> dict[str, str]:
    schema = schema or load_schema()
    defaults = (schema.get("utility") or {}).get("default_multipliers") or {}
    return {str(k): str(v) for k, v in defaults.items()}


def _inspect_field_ids(section: dict) -> list[str]:
    ids: list[str] = []
    for group in section.get("groups") or []:
        for field in group.get("fields") or []:
            ids.append(field["id"])
    return ids


def _inspect_block(section: dict) -> dict[str, dict[str, str]]:
    field_ids = _inspect_field_ids(section)
    out: dict[str, dict[str, str]] = {}
    for slot in section.get("times") or []:
        tid = slot["id"] if isinstance(slot, dict) else str(slot)
        default = str(slot.get("default") or "") if isinstance(slot, dict) else ""
        out[tid] = {
            **{fid: "" for fid in field_ids},
            "time": default,
        }
    return out


def _indoor_block(section: dict) -> dict[str, dict[str, str]]:
    field_ids = [f["id"] for f in section.get("fields") or []]
    out: dict[str, dict[str, str]] = {}
    for slot in section.get("times") or []:
        tid = slot["id"] if isinstance(slot, dict) else str(slot)
        default = str(slot.get("default") or "") if isinstance(slot, dict) else ""
        out[tid] = {
            **{fid: "" for fid in field_ids},
            "time": default,
        }
    return out


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
    executive = schema.get("executive") or {}
    exec_fields = [f["id"] for f in executive.get("fields") or []]
    return {
        "utility": utility,
        "multipliers": _default_multipliers(schema),
        "electrical": _inspect_block(schema.get("electrical") or {}),
        "executive": {
            unit["id"]: {fid: "" for fid in exec_fields}
            for unit in executive.get("units") or []
        },
        "indoor": _indoor_block(schema.get("indoor") or {}),
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


def _row_multiplier(row_def: dict, multipliers: dict[str, Any]) -> float:
    key = row_def.get("multiplier_key")
    if key:
        parsed = _parse_num(multipliers.get(key))
        return float(parsed) if parsed is not None else 1.0
    return float(row_def.get("multiplier") or 1)


def recompute_daily(
    data: dict, prev_monthly: dict[str, str] | None = None
) -> dict[str, Any]:
    out = deepcopy(empty_daily_payload())
    _deep_merge(out, data or {})
    previous = prev_monthly or {}
    multipliers = out.get("multipliers") or {}
    row_defs = {row["id"]: row for row in _utility_rows()}
    for uid, definition in row_defs.items():
        row = out["utility"].setdefault(uid, {})
        prev = _parse_num(row.get("prev"))
        today = _parse_num(row.get("today"))
        multiplier = _row_multiplier(definition, multipliers)
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
        if key.startswith("mul__") and len(parts) == 2:
            if parts[1] in data["multipliers"]:
                data["multipliers"][parts[1]] = raw
        elif key.startswith("u__") and len(parts) == 3:
            uid, field = parts[1], parts[2]
            if uid not in data["utility"] or field not in {
                "prev", "today", "remark", "prev_manual"
            }:
                continue
            data["utility"][uid][field] = raw == "1" if field == "prev_manual" else raw
        elif key.startswith("el__") and len(parts) == 3:
            tid, fid = parts[1], parts[2]
            if tid in data["electrical"] and fid in data["electrical"][tid]:
                data["electrical"][tid][fid] = raw
        elif key.startswith("ex__") and len(parts) == 3:
            unit, fid = parts[1], parts[2]
            if unit in data["executive"] and fid in data["executive"][unit]:
                data["executive"][unit][fid] = raw
        elif key.startswith("in__") and len(parts) == 3:
            tid, fid = parts[1], parts[2]
            if tid in data["indoor"] and fid in data["indoor"][tid]:
                data["indoor"][tid][fid] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> BaegundaeDaily | None:
    return (
        await session.execute(
            select(BaegundaeDaily).where(
                BaegundaeDaily.building_id == building_id,
                BaegundaeDaily.log_date == log_date,
            )
        )
    ).scalar_one_or_none()


def _monthly_from_previous(previous: dict | None, log_date: date) -> dict[str, str]:
    if not previous or (log_date - timedelta(days=1)).month != log_date.month:
        return {}
    utility = previous.get("utility") or {}
    return {
        uid: str((utility.get(uid) or {}).get("monthly") or "")
        for uid in (load_schema().get("utility") or {}).get("monthly_ids") or []
    }


async def sync_prev_values(
    session: AsyncSession, building_id: int, log_date: date, data: dict
) -> tuple[dict, bool]:
    previous_row = await get_daily_row(session, building_id, log_date - timedelta(days=1))
    previous = previous_row.data if previous_row else {}
    monthly = _monthly_from_previous(previous, log_date)
    out = merge_daily_save(empty_daily_payload(), data, monthly)
    before = deepcopy(data or {})
    if previous:
        previous_utility = recompute_daily(previous).get("utility") or {}
        for uid, row in out["utility"].items():
            if not row.get("prev_manual"):
                row["prev"] = (previous_utility.get(uid) or {}).get("today", "")
        prev_mul = previous.get("multipliers") or {}
        data_mul = (data or {}).get("multipliers") or {}
        for key, value in prev_mul.items():
            if key in out["multipliers"] and not data_mul.get(key) and value not in (None, ""):
                out["multipliers"][key] = value
    out = recompute_daily(out, monthly)
    return out, out != before


async def finalize_daily_save(
    session: AsyncSession,
    building_id: int,
    log_date: date,
    existing: dict,
    posted: dict,
) -> dict:
    previous_row = await get_daily_row(session, building_id, log_date - timedelta(days=1))
    monthly = _monthly_from_previous(previous_row.data if previous_row else None, log_date)
    posted = deepcopy(posted or {})
    existing_utility = (existing or {}).get("utility") or {}
    for uid, values in (posted.get("utility") or {}).items():
        if "prev" not in values:
            continue
        old_value = str((existing_utility.get(uid) or {}).get("prev") or "")
        if str(values.get("prev") or "") != old_value:
            values["prev_manual"] = True
    return merge_daily_save(existing, posted, monthly)


async def get_or_create_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> BaegundaeDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = BaegundaeDaily(building_id=building_id, log_date=log_date, data=data)
    session.add(row)
    await session.flush()
    return row


async def propagate_to_next_day(
    session: AsyncSession, building_id: int, log_date: date, saved_data: dict
) -> None:
    next_row = await get_daily_row(session, building_id, log_date + timedelta(days=1))
    if not next_row:
        return
    next_data = merge_daily_save(empty_daily_payload(), next_row.data or {})
    source = recompute_daily(saved_data).get("utility") or {}
    for uid, row in next_data["utility"].items():
        if not row.get("prev_manual"):
            row["prev"] = (source.get(uid) or {}).get("today", "")
    src_mul = (saved_data or {}).get("multipliers") or {}
    for key, value in src_mul.items():
        if key in next_data["multipliers"] and not (next_row.data or {}).get("multipliers", {}).get(key):
            next_data["multipliers"][key] = value
    synced, _ = await sync_prev_values(
        session, building_id, log_date + timedelta(days=1), next_data
    )
    next_row.data = synced
    next_row.updated_at = datetime.utcnow()


def compute_monthly_report(
    year: int,
    month: int,
    daily_rows: list[BaegundaeDaily],
    prev_month_last_row: BaegundaeDaily | None = None,
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
        days.append({
            "day": day,
            "date": log_date.isoformat(),
            "utility": values,
        })
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
            select(BaegundaeDaily).where(
                BaegundaeDaily.building_id == building_id,
                BaegundaeDaily.log_date >= date(year, 1, 1),
                BaegundaeDaily.log_date <= date(year, 12, 31),
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
            select(BaegundaeDaily)
            .where(
                BaegundaeDaily.building_id == building_id,
                BaegundaeDaily.log_date >= date(year, month, 1),
                BaegundaeDaily.log_date <= date(
                    year, month, calendar.monthrange(year, month)[1]
                ),
            )
            .order_by(BaegundaeDaily.log_date.asc())
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


async def get_building_for_qr(
    session: AsyncSession, code: str
) -> Building | None:
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
    return building if is_baegundae_building(building) else None


def baegundae_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/bdae/{(building_code or '').strip()}/daily"


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> BaegundaeArchive | None:
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(BaegundaeArchive).where(
                BaegundaeArchive.building_id == building_id,
                BaegundaeArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = json.dumps(row.data or {}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"백운대_점검일지_{log_date.isoformat()}.json"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = BaegundaeArchive(
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
                CREATE TABLE IF NOT EXISTS baegundae_daily (
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
                CREATE TABLE IF NOT EXISTS baegundae_archives (
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
                CREATE TABLE IF NOT EXISTS baegundae_daily (
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
                CREATE TABLE IF NOT EXISTS baegundae_archives (
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


def _extra_sheets(schema: dict, data: dict) -> list[tuple[str, list[str], list[list[Any]]]]:
    sheets: list[tuple[str, list[str], list[list[Any]]]] = []

    electrical = schema.get("electrical") or {}
    el_fields: list[dict] = []
    for group in electrical.get("groups") or []:
        for field in group.get("fields") or []:
            el_fields.append({**field, "cat": group.get("cat") or ""})
    if el_fields or (electrical.get("times") or []):
        headers = ["기록시간"] + [
            f"{f.get('cat', '')} {f.get('label', '')}".strip() for f in el_fields
        ]
        block = data.get("electrical") or {}
        rows: list[list[Any]] = []
        for slot in electrical.get("times") or []:
            tid = slot["id"] if isinstance(slot, dict) else str(slot)
            default = str(slot.get("default") or "") if isinstance(slot, dict) else ""
            cell = block.get(tid) or {}
            rows.append(
                [cell.get("time", default)] + [cell.get(f["id"], "") for f in el_fields]
            )
        sheets.append(("전기설비", headers, rows))

    executive = schema.get("executive") or {}
    units = executive.get("units") or []
    fields = executive.get("fields") or []
    if units and fields:
        headers = ["항목"] + [u.get("label") or u.get("id") for u in units]
        block = data.get("executive") or {}
        rows = []
        for field in fields:
            row = [field.get("label") or field.get("id")]
            for unit in units:
                cell = block.get(unit["id"]) or {}
                row.append(cell.get(field["id"], ""))
            rows.append(row)
        sheets.append(("임원숙소설비", headers, rows))

    indoor = schema.get("indoor") or {}
    in_fields = indoor.get("fields") or []
    if in_fields or (indoor.get("times") or []):
        headers = ["기록시간"] + [f.get("label") or f.get("id") for f in in_fields]
        block = data.get("indoor") or {}
        rows = []
        for slot in indoor.get("times") or []:
            tid = slot["id"] if isinstance(slot, dict) else str(slot)
            default = str(slot.get("default") or "") if isinstance(slot, dict) else ""
            cell = block.get(tid) or {}
            rows.append(
                [cell.get("time", default)] + [cell.get(f["id"], "") for f in in_fields]
            )
        sheets.append(("실내온도", headers, rows))

    return sheets


def export_daily_to_excel(data: dict, log_date: date) -> bytes:
    from inspection_log2_export import export_daily_utility_workbook

    schema = load_schema()
    data = recompute_daily(data or {})
    title = str(schema.get("title") or schema.get("building_name") or "백운대")
    return export_daily_utility_workbook(
        title=title,
        log_date=log_date,
        meter_defs=_utility_rows(schema),
        utility=data.get("utility") or {},
        notes=str(data.get("notes") or ""),
        multipliers=data.get("multipliers") if isinstance(data.get("multipliers"), dict) else None,
        extra_sheets=_extra_sheets(schema, data),
    )


def export_monthly_to_excel(monthly_report: dict) -> bytes:
    from inspection_log2_export import export_monthly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "백운대")
    return export_monthly_utility_workbook(monthly_report, title=title)


def export_yearly_to_excel(yearly_report: dict) -> bytes:
    from inspection_log2_export import export_yearly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "백운대")
    return export_yearly_utility_workbook(yearly_report, title=title)


async def ensure_registered(session: AsyncSession) -> bool:
    """건물·점검일지 등록을 보장한다. 변경이 있으면 True."""
    from models import InspectionLogBuilding2, Site

    changed = False
    buildings = (
        await session.execute(select(Building).where(Building.is_active == True))  # noqa: E712
    ).scalars().all()
    building = next((b for b in buildings if is_baegundae_building(b)), None)
    if not building:
        site = (await session.execute(select(Site).order_by(Site.id.asc()))).scalars().first()
        if not site:
            return False
        code = "BDAE"
        exists_code = (
            await session.execute(select(Building).where(Building.code == code))
        ).scalar_one_or_none()
        if exists_code:
            code = f"BDAE-{site.id}"
        building = Building(
            site_id=site.id,
            name="백운대",
            code=code,
            is_active=True,
            description="백운대 점검일지",
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
                print(f"[baegundae] ensure_registered: {exc}", flush=True)
            rows = (
                await session.execute(
                    select(InspectionLogBuilding2.building_id, Building).join(
                        Building, Building.id == InspectionLogBuilding2.building_id
                    )
                )
            ).all()
            today = datetime.now(kst).date()
            for building_id, building in rows:
                if not is_baegundae_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[baegundae] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="baegundae_rollover",
        replace_existing=True,
    )
