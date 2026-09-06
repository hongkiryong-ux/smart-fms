# -*- coding: utf-8 -*-
"""어울림체육관 운영일보[냉,난방] — 1일·월보·년보·특이사항."""
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

from models import Building, EoulrimGymArchive, EoulrimGymDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "eoulrim_gym_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    return _schema_cache


def is_eoulrim_gym_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").strip(),
        *(str(v).strip() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    return "어울림체육관" in name or name in names


def _utility_rows(schema: dict | None = None) -> list[dict]:
    schema = schema or load_schema()
    rows: list[dict] = []
    for block in (schema.get("utility") or {}).get("blocks") or []:
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


def _electrical_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    elec = schema.get("electrical") or {}
    ids = ["time"]
    for field in elec.get("main_fields") or []:
        ids.append(field["id"])
    for group in elec.get("vcb_groups") or []:
        for field in group.get("fields") or []:
            ids.append(field["id"])
    return ids


def _unit_row_block(
    units: list[dict], fields: list[dict]
) -> dict[str, dict[str, dict[str, str]]]:
    """단위설비별 행 인덱스("1","2",…) 필드 블록."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    field_ids = [f["id"] for f in fields]
    for unit in units:
        uid = unit["id"]
        rows = int(unit.get("rows") or 1)
        out[uid] = {
            str(idx): {fid: "" for fid in field_ids}
            for idx in range(1, rows + 1)
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
    elec = schema.get("electrical") or {}
    cooling = schema.get("cooling") or {}
    heating = schema.get("heating") or {}
    ahu = schema.get("ahu") or {}
    hw_fire = schema.get("hotwater_fire") or {}
    indoor = schema.get("indoor_temp") or {}
    eq_run = schema.get("equipment_run") or {}
    events = schema.get("events") or {}

    elec_fields = _electrical_field_ids(schema)
    electrical = {
        t: {fid: "" for fid in elec_fields}
        for t in elec.get("times") or ["t1"]
    }

    indoor_locs = [loc["id"] for loc in indoor.get("locations") or []]
    indoor_temp = {
        t: {
            **{lid: "" for lid in indoor_locs},
            "time": "",
            "remark": "",
        }
        for t in indoor.get("times") or ["t1"]
    }

    equipment_run = {
        row["id"]: {
            "run1": "",
            "run2": "",
            "run3": "",
            "today_hrs": "",
            "prev_cum": "",
            "monthly_cum": "",
            "remark": "",
            "prev_manual": False,
        }
        for row in eq_run.get("rows") or []
    }

    event_fields = [f["id"] for f in events.get("fields") or []]
    events_data = {
        row["id"]: {fid: "" for fid in event_fields}
        for row in events.get("rows") or []
    }

    return {
        "utility": utility,
        "multipliers": _default_multipliers(schema),
        "electrical": electrical,
        "cooling": _unit_row_block(
            cooling.get("units") or [], cooling.get("fields") or []
        ),
        "heating": _unit_row_block(
            heating.get("units") or [], heating.get("fields") or []
        ),
        "ahu": _unit_row_block(ahu.get("units") or [], ahu.get("fields") or []),
        "hotwater": {f["id"]: "" for f in hw_fire.get("hotwater") or []},
        "fire": {f["id"]: "" for f in hw_fire.get("fire") or []},
        "indoor_temp": indoor_temp,
        "equipment_run": equipment_run,
        "events": events_data,
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


def _recompute_equipment_run(out: dict) -> None:
    """today_hrs는 수기 입력(run1/2/3 시간 텍스트와 무관). monthly_cum = prev_cum + today_hrs."""
    for _eid, row in (out.get("equipment_run") or {}).items():
        if not isinstance(row, dict):
            continue
        today_hrs = _parse_num(row.get("today_hrs"))
        prev_cum = _parse_num(row.get("prev_cum"))
        if prev_cum is not None:
            row["prev_cum"] = _fmt_num(prev_cum)
        if today_hrs is not None:
            row["today_hrs"] = _fmt_num(today_hrs)
            row["monthly_cum"] = _fmt_num((prev_cum or 0) + today_hrs)
        else:
            row["monthly_cum"] = ""


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
    _recompute_equipment_run(out)
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
            t, fid = parts[1], parts[2]
            if t in data["electrical"] and fid in data["electrical"][t]:
                data["electrical"][t][fid] = raw
        elif key.startswith("cl__") and len(parts) == 4:
            unit, row, fid = parts[1], parts[2], parts[3]
            block = data["cooling"].get(unit) or {}
            if row in block and fid in block[row]:
                data["cooling"][unit][row][fid] = raw
        elif key.startswith("ht__") and len(parts) == 4:
            unit, row, fid = parts[1], parts[2], parts[3]
            block = data["heating"].get(unit) or {}
            if row in block and fid in block[row]:
                data["heating"][unit][row][fid] = raw
        elif key.startswith("ahu__") and len(parts) == 4:
            unit, row, fid = parts[1], parts[2], parts[3]
            block = data["ahu"].get(unit) or {}
            if row in block and fid in block[row]:
                data["ahu"][unit][row][fid] = raw
        elif key.startswith("hw__") and len(parts) == 2:
            if parts[1] in data["hotwater"]:
                data["hotwater"][parts[1]] = raw
        elif key.startswith("fire__") and len(parts) == 2:
            if parts[1] in data["fire"]:
                data["fire"][parts[1]] = raw
        elif key.startswith("it__") and len(parts) == 3:
            t, fid = parts[1], parts[2]
            if t in data["indoor_temp"] and fid in data["indoor_temp"][t]:
                data["indoor_temp"][t][fid] = raw
        elif key.startswith("eq__") and len(parts) == 3:
            eid, field = parts[1], parts[2]
            if eid not in data["equipment_run"] or field not in {
                "run1", "run2", "run3", "today_hrs", "prev_cum", "remark", "prev_manual"
            }:
                continue
            data["equipment_run"][eid][field] = (
                raw == "1" if field == "prev_manual" else raw
            )
        elif key.startswith("ev__") and len(parts) == 3:
            eid, field = parts[1], parts[2]
            if eid in data["events"] and field in data["events"][eid]:
                data["events"][eid][field] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> EoulrimGymDaily | None:
    return (
        await session.execute(
            select(EoulrimGymDaily).where(
                EoulrimGymDaily.building_id == building_id,
                EoulrimGymDaily.log_date == log_date,
            )
        )
    ).scalar_one_or_none()


def _monthly_from_previous(previous: dict | None, log_date: date) -> dict[str, str]:
    """유틸리티 monthly_ids만 전일 월누계 이월(월 경계에서 초기화)."""
    if not previous or (log_date - timedelta(days=1)).month != log_date.month:
        return {}
    utility = previous.get("utility") or {}
    return {
        uid: str((utility.get(uid) or {}).get("monthly") or "")
        for uid in (load_schema().get("utility") or {}).get("monthly_ids") or []
    }


def _sync_equipment_from_previous(out: dict, previous: dict) -> None:
    """prev_manual이 아니면 prev_cum ← 전일 monthly_cum."""
    prev_eq = previous.get("equipment_run") or {}
    # 전일 데이터도 재계산해 monthly_cum을 확정
    prev_computed = recompute_daily(previous).get("equipment_run") or {}
    for eid, row in (out.get("equipment_run") or {}).items():
        if row.get("prev_manual"):
            continue
        src = prev_computed.get(eid) or prev_eq.get(eid) or {}
        row["prev_cum"] = str(src.get("monthly_cum") or "")


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
        # 당일 배율이 비어 있으면 전일 배율 사용
        prev_mul = previous.get("multipliers") or {}
        data_mul = (data or {}).get("multipliers") or {}
        for key, value in prev_mul.items():
            if key in out["multipliers"] and not data_mul.get(key) and value not in (None, ""):
                out["multipliers"][key] = value
        _sync_equipment_from_previous(out, previous)
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
    existing_eq = (existing or {}).get("equipment_run") or {}
    for eid, values in (posted.get("equipment_run") or {}).items():
        if "prev_cum" not in values:
            continue
        old_value = str((existing_eq.get(eid) or {}).get("prev_cum") or "")
        if str(values.get("prev_cum") or "") != old_value:
            values["prev_manual"] = True
    return merge_daily_save(existing, posted, monthly)


async def get_or_create_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> EoulrimGymDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = EoulrimGymDaily(building_id=building_id, log_date=log_date, data=data)
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
    computed = recompute_daily(saved_data)
    source = computed.get("utility") or {}
    for uid, row in next_data["utility"].items():
        if not row.get("prev_manual"):
            row["prev"] = (source.get(uid) or {}).get("today", "")
    # 다음날 배율이 비어 있으면 당일 배율 전파
    src_mul = (saved_data or {}).get("multipliers") or {}
    for key, value in src_mul.items():
        if key in next_data["multipliers"] and not (next_row.data or {}).get("multipliers", {}).get(key):
            next_data["multipliers"][key] = value
    # 설비 가동: prev_manual이 아니면 prev_cum ← 당일 monthly_cum
    src_eq = computed.get("equipment_run") or {}
    for eid, row in next_data["equipment_run"].items():
        if not row.get("prev_manual"):
            row["prev_cum"] = str((src_eq.get(eid) or {}).get("monthly_cum") or "")
    synced, _ = await sync_prev_values(
        session, building_id, log_date + timedelta(days=1), next_data
    )
    next_row.data = synced
    next_row.updated_at = datetime.utcnow()


def compute_monthly_report(
    year: int,
    month: int,
    daily_rows: list[EoulrimGymDaily],
    prev_month_last_row: EoulrimGymDaily | None = None,
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
            select(EoulrimGymDaily).where(
                EoulrimGymDaily.building_id == building_id,
                EoulrimGymDaily.log_date >= date(year, 1, 1),
                EoulrimGymDaily.log_date <= date(year, 12, 31),
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
            select(EoulrimGymDaily)
            .where(
                EoulrimGymDaily.building_id == building_id,
                EoulrimGymDaily.log_date >= date(year, month, 1),
                EoulrimGymDaily.log_date <= date(
                    year, month, calendar.monthrange(year, month)[1]
                ),
            )
            .order_by(EoulrimGymDaily.log_date.asc())
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
    return building if is_eoulrim_gym_building(building) else None


def egym_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/egym/{(building_code or '').strip()}/daily"


# 하위 호환 별칭
eoulrim_gym_daily_qr_url = egym_daily_qr_url


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> EoulrimGymArchive | None:
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(EoulrimGymArchive).where(
                EoulrimGymArchive.building_id == building_id,
                EoulrimGymArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = json.dumps(row.data or {}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"어울림체육관_운영일보_{log_date.isoformat()}.json"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = EoulrimGymArchive(
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
                CREATE TABLE IF NOT EXISTS eoulrim_gym_daily (
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
                CREATE TABLE IF NOT EXISTS eoulrim_gym_archives (
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
                CREATE TABLE IF NOT EXISTS eoulrim_gym_daily (
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
                CREATE TABLE IF NOT EXISTS eoulrim_gym_archives (
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


def register_scheduler(scheduler, session_factory, kst) -> None:
    async def _daily_job() -> None:
        from models import InspectionLogBuilding2

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(InspectionLogBuilding2.building_id, Building).join(
                        Building, Building.id == InspectionLogBuilding2.building_id
                    )
                )
            ).all()
            today = datetime.now(kst).date()
            for building_id, building in rows:
                if not is_eoulrim_gym_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[egym] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="eoulrim_gym_rollover",
        replace_existing=True,
    )
