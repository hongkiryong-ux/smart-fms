# -*- coding: utf-8 -*-
"""백운생활관 설비 점검일지[냉,난방] — 1일·월보·년보·특이사항."""
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

from models import Building

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "baegun_dorm_schema.json"
_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    return _schema_cache


def is_baegun_dorm_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").strip(),
        *(str(v).strip() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    return "백운생활관" in name or name in names


def _utility_rows(schema: dict | None = None) -> list[dict]:
    schema = schema or load_schema()
    rows: list[dict] = []
    for block in (schema.get("utility") or {}).get("blocks") or []:
        for row in block.get("rows") or []:
            rows.append({
                **row,
                "cat": block.get("cat") or "",
                "building": row.get("building") or block.get("building") or "",
                "multiplier": 1,
            })
    return rows


def _electrical_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    ids = ["time"]
    for group in (schema.get("electrical") or {}).get("groups") or []:
        for field in group.get("fields") or []:
            ids.append(field["id"])
    return ids


def _facility_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["id"] for f in (schema.get("facility") or {}).get("fields") or []]


def _heating_meas_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["id"] for f in (schema.get("heating") or {}).get("fields") or []]


def _cooling_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    ids = ["time"]
    for metric in (schema.get("cooling") or {}).get("metrics") or []:
        ids.append(metric["id"])
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

    elec = schema.get("electrical") or {}
    elec_fields = _electrical_field_ids(schema)
    electrical = {
        t: {fid: "" for fid in elec_fields}
        for t in elec.get("times") or ["t1"]
    }

    fac = schema.get("facility") or {}
    fac_fields = _facility_field_ids(schema)
    facility = {
        b["id"]: {fid: "" for fid in fac_fields}
        for b in fac.get("buildings") or []
    }

    heating = schema.get("heating") or {}
    hm_fields = _heating_meas_field_ids(schema)
    rows_n = int(heating.get("rows_per_building") or 2)
    heating_meas = {
        b["id"]: {
            str(idx): {fid: "" for fid in hm_fields}
            for idx in range(1, rows_n + 1)
        }
        for b in heating.get("buildings") or []
    }
    heating_run = {
        b["id"]: {
            "daily": "",
            "prev_cum": "",
            "monthly_cum": "",
            "prev_manual": False,
        }
        for b in heating.get("buildings") or []
    }
    heating_check = {
        item["id"]: {"day": "", "night": ""}
        for item in heating.get("checklist") or []
    }

    cooling = schema.get("cooling") or {}
    cl_fields = _cooling_field_ids(schema)
    slots = [str(s) for s in (cooling.get("slots") or ["1", "2"])]
    cooling_data = {
        b["id"]: {
            slot: {fid: "" for fid in cl_fields}
            for slot in slots
        }
        for b in cooling.get("buildings") or []
    }
    cooling_check = {
        item["id"]: {"day": "", "night": ""}
        for item in cooling.get("checklist") or []
    }

    return {
        "utility": utility,
        "electrical": electrical,
        "facility": facility,
        "heating_meas": heating_meas,
        "heating_run": heating_run,
        "heating_check": heating_check,
        "cooling": cooling_data,
        "cooling_check": cooling_check,
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


def _recompute_heating_run(out: dict) -> None:
    """monthly_cum = prev_cum + daily (일계 시간)."""
    for _bid, row in (out.get("heating_run") or {}).items():
        if not isinstance(row, dict):
            continue
        daily = _parse_num(row.get("daily"))
        prev_cum = _parse_num(row.get("prev_cum"))
        if prev_cum is not None:
            row["prev_cum"] = _fmt_num(prev_cum)
        if daily is not None:
            row["daily"] = _fmt_num(daily)
            row["monthly_cum"] = _fmt_num((prev_cum or 0) + daily)
        else:
            row["monthly_cum"] = ""


def recompute_daily(
    data: dict, prev_monthly: dict[str, str] | None = None
) -> dict[str, Any]:
    out = deepcopy(empty_daily_payload())
    _deep_merge(out, data or {})
    previous = prev_monthly or {}
    row_defs = {row["id"]: row for row in _utility_rows()}
    for uid in row_defs:
        row = out["utility"].setdefault(uid, {})
        prev = _parse_num(row.get("prev"))
        today = _parse_num(row.get("today"))
        daily = (today - prev) if prev is not None and today is not None else None
        row["daily"] = _fmt_num(daily)
        base = _parse_num(previous.get(uid))
        row["monthly"] = _fmt_num((base or 0) + daily) if daily is not None else ""
    _recompute_heating_run(out)
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
            if t in data["electrical"] and fid in data["electrical"][t]:
                data["electrical"][t][fid] = raw
        elif key.startswith("fac__") and len(parts) == 3:
            bid, fid = parts[1], parts[2]
            if bid in data["facility"] and fid in data["facility"][bid]:
                data["facility"][bid][fid] = raw
        elif key.startswith("hm__") and len(parts) == 4:
            bid, row, fid = parts[1], parts[2], parts[3]
            block = data["heating_meas"].get(bid) or {}
            if row in block and fid in block[row]:
                data["heating_meas"][bid][row][fid] = raw
        elif key.startswith("hr__") and len(parts) == 3:
            bid, field = parts[1], parts[2]
            if bid not in data["heating_run"] or field not in {
                "daily", "prev_cum", "prev_manual"
            }:
                continue
            data["heating_run"][bid][field] = (
                raw == "1" if field == "prev_manual" else raw
            )
        elif key.startswith("hc__") and len(parts) == 3:
            item, field = parts[1], parts[2]
            if item in data["heating_check"] and field in {"day", "night"}:
                data["heating_check"][item][field] = raw
        elif key.startswith("cl__") and len(parts) == 4:
            bid, slot, fid = parts[1], parts[2], parts[3]
            block = data["cooling"].get(bid) or {}
            if slot in block and fid in block[slot]:
                data["cooling"][bid][slot][fid] = raw
        elif key.startswith("cc__") and len(parts) == 3:
            item, field = parts[1], parts[2]
            if item in data["cooling_check"] and field in {"day", "night"}:
                data["cooling_check"][item][field] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
):
    from models import BaegunDormDaily

    return (
        await session.execute(
            select(BaegunDormDaily).where(
                BaegunDormDaily.building_id == building_id,
                BaegunDormDaily.log_date == log_date,
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


def _sync_heating_run_from_previous(out: dict, previous: dict) -> None:
    """prev_manual이 아니면 prev_cum ← 전일 monthly_cum."""
    prev_hr = previous.get("heating_run") or {}
    prev_computed = recompute_daily(previous).get("heating_run") or {}
    for bid, row in (out.get("heating_run") or {}).items():
        if row.get("prev_manual"):
            continue
        src = prev_computed.get(bid) or prev_hr.get(bid) or {}
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
        _sync_heating_run_from_previous(out, previous)
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
    existing_hr = (existing or {}).get("heating_run") or {}
    for bid, values in (posted.get("heating_run") or {}).items():
        if "prev_cum" not in values:
            continue
        old_value = str((existing_hr.get(bid) or {}).get("prev_cum") or "")
        if str(values.get("prev_cum") or "") != old_value:
            values["prev_manual"] = True
    return merge_daily_save(existing, posted, monthly)


async def get_or_create_daily(
    session: AsyncSession, building_id: int, log_date: date
):
    from models import BaegunDormDaily

    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = BaegunDormDaily(building_id=building_id, log_date=log_date, data=data)
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
    src_hr = computed.get("heating_run") or {}
    for bid, row in next_data["heating_run"].items():
        if not row.get("prev_manual"):
            row["prev_cum"] = str((src_hr.get(bid) or {}).get("monthly_cum") or "")
    synced, _ = await sync_prev_values(
        session, building_id, log_date + timedelta(days=1), next_data
    )
    next_row.data = synced
    next_row.updated_at = datetime.utcnow()


def compute_monthly_report(
    year: int,
    month: int,
    daily_rows: list,
    prev_month_last_row=None,
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
    from models import BaegunDormDaily

    rows = (
        await session.execute(
            select(BaegunDormDaily).where(
                BaegunDormDaily.building_id == building_id,
                BaegunDormDaily.log_date >= date(year, 1, 1),
                BaegunDormDaily.log_date <= date(year, 12, 31),
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

    from models import BaegunDormDaily

    rows = (
        await session.execute(
            select(BaegunDormDaily)
            .where(
                BaegunDormDaily.building_id == building_id,
                BaegunDormDaily.log_date >= date(year, month, 1),
                BaegunDormDaily.log_date <= date(
                    year, month, calendar.monthrange(year, month)[1]
                ),
            )
            .order_by(BaegunDormDaily.log_date.asc())
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
    return building if is_baegun_dorm_building(building) else None


def bdorm_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/bdorm/{(building_code or '').strip()}/daily"


# 하위 호환 별칭
baegun_dorm_daily_qr_url = bdorm_daily_qr_url


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
):
    from models import BaegunDormArchive

    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(BaegunDormArchive).where(
                BaegunDormArchive.building_id == building_id,
                BaegunDormArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = json.dumps(row.data or {}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"백운생활관_점검일지_{log_date.isoformat()}.json"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = BaegunDormArchive(
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
                CREATE TABLE IF NOT EXISTS baegun_dorm_daily (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    CONSTRAINT uq_bdorm_daily UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS baegun_dorm_archives (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BYTEA,
                    file_size INTEGER,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    CONSTRAINT uq_bdorm_archive UNIQUE (building_id, log_date)
                )
            """))
        else:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS baegun_dorm_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME,
                    updated_at DATETIME,
                    CONSTRAINT uq_bdorm_daily UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS baegun_dorm_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BLOB,
                    file_size INTEGER,
                    created_at DATETIME,
                    CONSTRAINT uq_bdorm_archive UNIQUE (building_id, log_date)
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
                if not is_baegun_dorm_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[bdorm] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="baegun_dorm_rollover",
        replace_existing=True,
    )


if __name__ == "__main__":
    # 스모크: 스키마·빈 페이로드·재계산·폼 파싱
    schema = load_schema()
    assert schema.get("building_name") == "백운생활관"
    payload = empty_daily_payload()
    assert "multipliers" not in payload
    assert set(payload["utility"]) == {
        "hw2_heat", "hw2_flow", "hw4_heat", "hw4_flow", "hw6_heat", "hw6_flow",
        "water2", "water4", "water6",
    }
    assert set(payload["electrical"]["t1"]) >= {
        "time", "main_a", "main_kw", "lit_a", "lit_temp", "pwr_a", "pwr_temp",
    }
    assert set(payload["facility"]) == {"b2", "b4", "b6"}
    assert set(payload["heating_meas"]["d1"]["1"]) == {
        "time", "sup_temp", "ret_temp", "sup_p", "ret_p",
    }
    assert set(payload["heating_run"]["d2"]) == {
        "daily", "prev_cum", "monthly_cum", "prev_manual",
    }
    assert "pump" in payload["heating_check"]
    assert "oil_p" in payload["cooling"]["b2"]["1"]
    assert "vcb_tr" in payload["cooling_check"]

    sample = {
        "utility": {
            "hw2_heat": {"prev": "100", "today": "110", "prev_manual": False},
            "water2": {"prev": "10", "today": "12"},
        },
        "heating_run": {
            "d1": {"daily": "5", "prev_cum": "20", "prev_manual": False},
        },
    }
    computed = recompute_daily(sample, {"hw2_heat": "50", "water2": "3"})
    assert computed["utility"]["hw2_heat"]["daily"] == "10"
    assert computed["utility"]["hw2_heat"]["monthly"] == "60"
    assert computed["utility"]["water2"]["daily"] == "2"
    assert computed["utility"]["water2"]["monthly"] == "5"
    assert computed["heating_run"]["d1"]["monthly_cum"] == "25"

    form = {
        "u__hw2_heat__prev": "1",
        "u__hw2_heat__today": "4",
        "u__hw2_heat__remark": "ok",
        "el__t1__main_a": "12",
        "fac__b2__hw_temp": "48",
        "hm__d1__1__sup_temp": "55",
        "hr__d1__daily": "3",
        "hr__d1__prev_cum": "10",
        "hc__pump__day": "정상",
        "cl__b2__1__oil_p": "2.0",
        "cc__vcb_tr__night": "양호",
        "notes": "특이없음",
    }
    parsed = parse_daily_form(form)
    assert parsed["utility"]["hw2_heat"]["prev"] == "1"
    assert parsed["utility"]["hw2_heat"]["today"] == "4"
    assert parsed["electrical"]["t1"]["main_a"] == "12"
    assert parsed["facility"]["b2"]["hw_temp"] == "48"
    assert parsed["heating_meas"]["d1"]["1"]["sup_temp"] == "55"
    assert parsed["heating_run"]["d1"]["daily"] == "3"
    assert parsed["heating_check"]["pump"]["day"] == "정상"
    assert parsed["cooling"]["b2"]["1"]["oil_p"] == "2.0"
    assert parsed["cooling_check"]["vcb_tr"]["night"] == "양호"
    assert parsed["notes"] == "특이없음"
    recomputed = recompute_daily(parsed)
    assert recomputed["heating_run"]["d1"]["monthly_cum"] == "13"
    assert recomputed["utility"]["hw2_heat"]["daily"] == "3"

    url = bdorm_daily_qr_url("BAEGUN")
    assert url.endswith("/bdorm/BAEGUN/daily")
    print("[bdorm] smoke ok")
