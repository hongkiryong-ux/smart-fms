# -*- coding: utf-8 -*-
"""백운쇼핑센터 운영일보[냉,난방] — 1일·월보·년보·특이사항."""
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

from models import Building, BaegunShoppingArchive, BaegunShoppingDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "baegun_shopping_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    return _schema_cache


def is_baegun_shopping_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    # 백운대 / 백운아트홀 / 백운생활관과 절대 겹치지 않음
    if (
        name.startswith("백운대")
        or "아트홀" in name
        or "생활관" in name
        or name in {"백운대", "백운아트홀", "백운생활관"}
    ):
        return False
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").strip(),
        *(str(v).strip() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    compact = name.replace(" ", "")
    if name in names or compact in {n.replace(" ", "") for n in names}:
        return True
    return "백운쇼핑" in compact


def _utility_rows(schema: dict | None = None) -> list[dict]:
    schema = schema or load_schema()
    rows: list[dict] = []
    for block in (schema.get("utility") or {}).get("blocks") or []:
        for row in block.get("rows") or []:
            rows.append({
                **row,
                "cat": block.get("cat") or "",
                "building": block.get("building") or block.get("stage") or "",
                "item": block.get("item") or "",
                "multiplier_key": block.get("multiplier_key"),
                "multiplier": row.get("multiplier", 1),
            })
    return rows


def _default_multipliers(schema: dict | None = None) -> dict[str, str]:
    schema = schema or load_schema()
    defaults = (schema.get("utility") or {}).get("default_multipliers") or {}
    return {str(k): str(v) for k, v in defaults.items()}


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

    elec: dict[str, dict[str, dict[str, str]]] = {}
    for panel in schema.get("elec_panels") or []:
        panel_id = panel["id"]
        field_ids = ["time"] + [f["id"] for f in panel.get("fields") or []]
        elec[panel_id] = {
            t: {fid: "" for fid in field_ids}
            for t in panel.get("times") or ["t1"]
        }

    heat_pump_sec = schema.get("heat_pump") or {}
    hp_fields = [f["id"] for f in heat_pump_sec.get("fields") or []]
    heat_pump = {
        loc["id"]: {fid: "" for fid in hp_fields}
        for loc in heat_pump_sec.get("locations") or []
    }

    hw_tank_sec = schema.get("hw_tank") or {}
    ht_fields = [f["id"] for f in hw_tank_sec.get("fields") or []]
    if hw_tank_sec.get("shared_time") and "time" not in ht_fields:
        ht_fields = ["time"] + ht_fields
    hw_tank = {
        loc["id"]: {fid: "" for fid in ht_fields}
        for loc in hw_tank_sec.get("locations") or []
    }

    chiller_sec = schema.get("chiller") or {}
    chiller = {f["id"]: "" for f in chiller_sec.get("fields") or []}

    ahu_sec = schema.get("ahu") or {}
    ahu_fields = [f["id"] for f in ahu_sec.get("fields") or []]
    ahu = {
        t: {"time": "", **{fid: "" for fid in ahu_fields}}
        for t in ahu_sec.get("times") or ["t1"]
    }

    fire_sec = schema.get("fire") or {}
    fire = {f["id"]: "" for f in fire_sec.get("fields") or []}

    indoor_sec = schema.get("indoor") or {}
    indoor_fields = [f["id"] for f in indoor_sec.get("fields") or []]
    indoor = {
        t: {"time": "", **{fid: "" for fid in indoor_fields}}
        for t in indoor_sec.get("times") or ["t1"]
    }

    return {
        "utility": utility,
        "multipliers": _default_multipliers(schema),
        "elec": elec,
        "heat_pump": heat_pump,
        "hw_tank": hw_tank,
        "chiller": chiller,
        "ahu": ahu,
        "fire": fire,
        "indoor": indoor,
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
        if parsed is not None:
            return float(parsed)
        defaults = _default_multipliers()
        fallback = _parse_num(defaults.get(key))
        return float(fallback) if fallback is not None else 1.0
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
        elif key.startswith("el__") and len(parts) == 4:
            panel, t, fid = parts[1], parts[2], parts[3]
            block = data["elec"].get(panel) or {}
            if t in block and fid in block[t]:
                data["elec"][panel][t][fid] = raw
        elif key.startswith("hp__") and len(parts) == 3:
            loc, fid = parts[1], parts[2]
            if loc in data["heat_pump"] and fid in data["heat_pump"][loc]:
                data["heat_pump"][loc][fid] = raw
        elif key.startswith("ht__") and len(parts) == 3:
            loc, fid = parts[1], parts[2]
            if loc in data["hw_tank"] and fid in data["hw_tank"][loc]:
                data["hw_tank"][loc][fid] = raw
        elif key.startswith("ch__") and len(parts) == 2:
            fid = parts[1]
            if fid in data["chiller"]:
                data["chiller"][fid] = raw
        elif key.startswith("ahu__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["ahu"] and fid in data["ahu"][t]:
                data["ahu"][t][fid] = raw
        elif key.startswith("fire__") and len(parts) == 2:
            fid = parts[1]
            if fid in data["fire"]:
                data["fire"][fid] = raw
        elif key.startswith("in__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["indoor"] and fid in data["indoor"][t]:
                data["indoor"][t][fid] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> BaegunShoppingDaily | None:
    return (
        await session.execute(
            select(BaegunShoppingDaily).where(
                BaegunShoppingDaily.building_id == building_id,
                BaegunShoppingDaily.log_date == log_date,
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
) -> BaegunShoppingDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = BaegunShoppingDaily(building_id=building_id, log_date=log_date, data=data)
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
    daily_rows: list[BaegunShoppingDaily],
    prev_month_last_row: BaegunShoppingDaily | None = None,
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
            select(BaegunShoppingDaily).where(
                BaegunShoppingDaily.building_id == building_id,
                BaegunShoppingDaily.log_date >= date(year, 1, 1),
                BaegunShoppingDaily.log_date <= date(year, 12, 31),
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
            select(BaegunShoppingDaily)
            .where(
                BaegunShoppingDaily.building_id == building_id,
                BaegunShoppingDaily.log_date >= date(year, month, 1),
                BaegunShoppingDaily.log_date <= date(
                    year, month, calendar.monthrange(year, month)[1]
                ),
            )
            .order_by(BaegunShoppingDaily.log_date.asc())
        )
    ).scalars().all()
    entries = []
    for row in rows:
        text = str((row.data or {}).get("notes") or "").strip()
        if text:
            entries.append({
                "date": row.log_date.isoformat(),
                "day": row.log_date.day,
                "items": [{"time": "", "section": "특기사항", "text": text}],
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
    return building if is_baegun_shopping_building(building) else None


def bshop_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/bshop/{(building_code or '').strip()}/daily"


baegun_shopping_daily_qr_url = bshop_daily_qr_url


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> BaegunShoppingArchive | None:
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(BaegunShoppingArchive).where(
                BaegunShoppingArchive.building_id == building_id,
                BaegunShoppingArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = json.dumps(row.data or {}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"백운쇼핑_운영일보_{log_date.isoformat()}.json"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = BaegunShoppingArchive(
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
                CREATE TABLE IF NOT EXISTS baegun_shopping_daily (
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
                CREATE TABLE IF NOT EXISTS baegun_shopping_archives (
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
                CREATE TABLE IF NOT EXISTS baegun_shopping_daily (
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
                CREATE TABLE IF NOT EXISTS baegun_shopping_archives (
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

    for panel in schema.get("elec_panels") or []:
        fields = panel.get("fields") or []
        headers = ["시간"] + [f.get("label") or f.get("id") for f in fields]
        block = (data.get("elec") or {}).get(panel["id"]) or {}
        rows: list[list[Any]] = []
        for t in panel.get("times") or []:
            cell = block.get(t) or {}
            rows.append([cell.get("time", "")] + [cell.get(f["id"], "") for f in fields])
        sheets.append((str(panel.get("title") or panel["id"])[:31], headers, rows))

    hp = schema.get("heat_pump") or {}
    hp_fields = hp.get("fields") or []
    if hp.get("locations"):
        headers = ["위치"] + [f.get("label") or f.get("id") for f in hp_fields]
        block = data.get("heat_pump") or {}
        rows = []
        for loc in hp.get("locations") or []:
            cell = block.get(loc["id"]) or {}
            rows.append(
                [loc.get("label") or loc["id"]] + [cell.get(f["id"], "") for f in hp_fields]
            )
        sheets.append(("난방순환펌프", headers, rows))

    ht = schema.get("hw_tank") or {}
    ht_fields = [f for f in (ht.get("fields") or [])]
    if ht.get("shared_time"):
        ht_fields = [{"id": "time", "label": "시간"}] + list(ht_fields)
    if ht.get("locations"):
        headers = ["위치"] + [f.get("label") or f.get("id") for f in ht_fields]
        block = data.get("hw_tank") or {}
        rows = []
        for loc in ht.get("locations") or []:
            cell = block.get(loc["id"]) or {}
            rows.append(
                [loc.get("label") or loc["id"]] + [cell.get(f["id"], "") for f in ht_fields]
            )
        sheets.append(("중온수탱크", headers, rows))

    ch = schema.get("chiller") or {}
    ch_fields = ch.get("fields") or []
    if ch_fields:
        headers = [f.get("label") or f.get("id") for f in ch_fields]
        cell = data.get("chiller") or {}
        sheets.append(("흡수식냉온수기", headers, [[cell.get(f["id"], "") for f in ch_fields]]))

    ahu = schema.get("ahu") or {}
    ahu_fields = ahu.get("fields") or []
    if ahu_fields:
        headers = ["시간"] + [f.get("label") or f.get("id") for f in ahu_fields]
        block = data.get("ahu") or {}
        rows = []
        for t in ahu.get("times") or []:
            cell = block.get(t) or {}
            rows.append([cell.get("time", "")] + [cell.get(f["id"], "") for f in ahu_fields])
        sheets.append(("공조온도", headers, rows))

    fire = schema.get("fire") or {}
    fire_fields = fire.get("fields") or []
    if fire_fields:
        headers = [f.get("label") or f.get("id") for f in fire_fields]
        cell = data.get("fire") or {}
        sheets.append(("소방설비", headers, [[cell.get(f["id"], "") for f in fire_fields]]))

    indoor = schema.get("indoor") or {}
    in_fields = indoor.get("fields") or []
    if in_fields:
        headers = ["시간"] + [f.get("label") or f.get("id") for f in in_fields]
        block = data.get("indoor") or {}
        rows = []
        for t in indoor.get("times") or []:
            cell = block.get(t) or {}
            rows.append([cell.get("time", "")] + [cell.get(f["id"], "") for f in in_fields])
        sheets.append(("실내온도", headers, rows))

    return sheets


def export_daily_to_excel(data: dict, log_date: date) -> bytes:
    from inspection_log2_export import (
        build_extra_rows,
        export_daily_utility_workbook,
    )

    schema = load_schema()
    data = recompute_daily(data or {})
    title = str(schema.get("title") or schema.get("building_name") or "백운쇼핑센터")
    return export_daily_utility_workbook(
        title=title,
        log_date=log_date,
        meter_defs=_utility_rows(schema),
        utility=data.get("utility") or {},
        notes=str(data.get("notes") or ""),
        multipliers=data.get("multipliers") if isinstance(data.get("multipliers"), dict) else None,
        extra_sheets=_extra_sheets(schema, data),
        extra_rows=build_extra_rows(data),
    )


def export_monthly_to_excel(monthly_report: dict) -> bytes:
    from inspection_log2_export import export_monthly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "백운쇼핑센터")
    return export_monthly_utility_workbook(monthly_report, title=title)


def export_yearly_to_excel(yearly_report: dict) -> bytes:
    from inspection_log2_export import export_yearly_utility_workbook

    schema = load_schema()
    title = str(schema.get("title") or schema.get("building_name") or "백운쇼핑센터")
    return export_yearly_utility_workbook(yearly_report, title=title)


async def ensure_registered(session: AsyncSession) -> bool:
    """건물·점검일지 등록을 보장한다. 변경이 있으면 True."""
    from models import InspectionLogBuilding2, Site

    changed = False
    buildings = (
        await session.execute(select(Building).where(Building.is_active == True))  # noqa: E712
    ).scalars().all()
    building = next((b for b in buildings if is_baegun_shopping_building(b)), None)
    if not building:
        site = (await session.execute(select(Site).order_by(Site.id.asc()))).scalars().first()
        if not site:
            return False
        code = "BSHOP"
        exists_code = (
            await session.execute(select(Building).where(Building.code == code))
        ).scalar_one_or_none()
        if exists_code:
            code = f"BSHOP-{site.id}"
        building = Building(
            site_id=site.id,
            name="백운쇼핑센터",
            code=code,
            is_active=True,
            description="백운쇼핑 운영일보[냉,난방]",
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
                print(f"[bshop] ensure_registered: {exc}", flush=True)
            rows = (
                await session.execute(
                    select(InspectionLogBuilding2.building_id, Building).join(
                        Building, Building.id == InspectionLogBuilding2.building_id
                    )
                )
            ).all()
            today = datetime.now(kst).date()
            for building_id, building in rows:
                if not is_baegun_shopping_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[bshop] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="baegun_shopping_rollover",
        replace_existing=True,
    )


if __name__ == "__main__":
    from types import SimpleNamespace

    schema = load_schema()
    assert schema.get("building_name") == "백운쇼핑센터"
    assert is_baegun_shopping_building(SimpleNamespace(name="백운쇼핑센터"))
    assert not is_baegun_shopping_building(SimpleNamespace(name="백운대"))
    assert not is_baegun_shopping_building(SimpleNamespace(name="백운아트홀"))
    assert not is_baegun_shopping_building(SimpleNamespace(name="백운생활관"))

    payload = empty_daily_payload()
    assert payload["multipliers"].get("power_s1") == "1"
    assert "p1_mid" in payload["utility"]
    assert "stage1" in payload["elec"]
    assert "mid_kwh" in payload["elec"]["stage1"]["t1"]

    sample = {
        "utility": {"p1_mid": {"prev": "1", "today": "2"}},
        "multipliers": {"power_s1": "100", "power_s23": "1"},
    }
    computed = recompute_daily(sample)
    assert computed["utility"]["p1_mid"]["daily"] == "100"

    form = {
        "mul__power_s1": "100",
        "mul__power_s23": "1",
        "u__p1_mid__prev": "1",
        "u__p1_mid__today": "2",
        "el__stage1__t1__volt": "380",
        "hp__s1__temp_s": "45",
        "ht__s1__temp": "55",
        "ch__hw_supply_temp": "90",
        "ahu__t1__ahu1": "22/24",
        "fire__hydrant_p": "7",
        "in__t1__lobby_s1": "23",
        "notes": "점검완료",
    }
    parsed = parse_daily_form(form)
    assert parsed["multipliers"]["power_s1"] == "100"
    assert parsed["utility"]["p1_mid"]["prev"] == "1"
    assert parsed["elec"]["stage1"]["t1"]["volt"] == "380"
    assert parsed["heat_pump"]["s1"]["temp_s"] == "45"
    assert parsed["notes"] == "점검완료"
    assert recompute_daily(parsed)["utility"]["p1_mid"]["daily"] == "100"

    xbytes = export_daily_to_excel(parsed, date(2026, 9, 7))
    assert xbytes[:2] == b"PK"
    print("baegun_shopping smoke OK")
