# -*- coding: utf-8 -*-
"""Park1538 운영일보 — 1일·월보·년보·특이사항."""
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
SCHEMA_PATH = ROOT / "resources" / "park1538_schema.json"
_schema_cache: dict | None = None

_NAME_MARKERS = ("PARK1538", "Park1538", "park1538", "파크1538")


def load_schema() -> dict:
    global _schema_cache
    mtime = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0
    if _schema_cache is None or _schema_cache.get("_mtime") != mtime:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["_mtime"] = mtime
        _schema_cache = data
    return _schema_cache


def is_park1538_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    if any(marker in name for marker in _NAME_MARKERS):
        return True
    schema = load_schema()
    names = {
        str(schema.get("building_name") or "").strip(),
        *(str(v).strip() for v in schema.get("building_aliases") or []),
    }
    names.discard("")
    return name in names


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
    elec = schema.get("electrical") or {}
    ids = ["time"]
    for field in elec.get("tr_fields") or []:
        ids.append(field["id"])
    for field in elec.get("acb_fields") or []:
        ids.append(field["id"])
    return ids


def _ahu_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["id"] for f in (schema.get("ahu") or {}).get("fields") or []]


def _hotwater_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [
        f["id"]
        for f in (schema.get("hotwater_water") or {}).get("hotwater_fields") or []
    ]


def _water_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [
        f["id"]
        for f in (schema.get("hotwater_water") or {}).get("water_fields") or []
    ]


def _tank_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    ids = ["time"]
    for field in (schema.get("hotwater_water") or {}).get("tank_fields") or []:
        fid = field["id"]
        if fid not in ids:
            ids.append(fid)
    return ids


def _floor_hvac_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["id"] for f in (schema.get("floor_hvac") or {}).get("fields") or []]


def _fire_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["id"] for f in (schema.get("fire") or {}).get("fields") or []]


def _ups_field_ids(schema: dict | None = None) -> list[str]:
    schema = schema or load_schema()
    ups = schema.get("ups") or {}
    packs = int(ups.get("packs") or 0)
    ids = [f"pack_{i}" for i in range(1, packs + 1)]
    for field in ups.get("fields") or []:
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

    elec = schema.get("electrical") or {}
    elec_fields = _electrical_field_ids(schema)
    times = [str(t) for t in elec.get("times") or ["t1", "t2"]]
    electrical = {
        panel["id"]: {
            t: {fid: "" for fid in elec_fields}
            for t in times
        }
        for panel in elec.get("panels") or []
    }

    ahu_schema = schema.get("ahu") or {}
    ahu_fields = _ahu_field_ids(schema)
    ahu_slots = [str(s) for s in ahu_schema.get("slots") or ["1", "2"]]
    ahu = {
        unit["id"]: {
            slot: {fid: "" for fid in ahu_fields}
            for slot in ahu_slots
        }
        for unit in ahu_schema.get("units") or []
    }

    hw_schema = schema.get("hotwater_water") or {}
    hw_slots = [str(s) for s in hw_schema.get("slots") or ["1", "2"]]
    hw_fields = _hotwater_field_ids(schema)
    hw = {
        slot: {
            b["id"]: {fid: "" for fid in hw_fields}
            for b in hw_schema.get("hotwater") or []
        }
        for slot in hw_slots
    }
    water_fields = _water_field_ids(schema)
    water = {
        slot: {
            b["id"]: {fid: "" for fid in water_fields}
            for b in hw_schema.get("water") or []
        }
        for slot in hw_slots
    }
    tank_fields = _tank_field_ids(schema)
    tank = {
        slot: {fid: "" for fid in tank_fields}
        for slot in hw_slots
    }

    floor_hvac = {fid: "" for fid in _floor_hvac_field_ids(schema)}
    fire = {fid: "" for fid in _fire_field_ids(schema)}
    ups = {fid: "" for fid in _ups_field_ids(schema)}

    return {
        "utility": utility,
        "electrical": electrical,
        "ahu": ahu,
        "hw": hw,
        "water": water,
        "tank": tank,
        "floor_hvac": floor_hvac,
        "fire": fire,
        "ups": ups,
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
    """유틸리티만 재계산(배수 1)."""
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
            panel, t, fid = parts[1], parts[2], parts[3]
            block = data["electrical"].get(panel) or {}
            if t in block and fid in block[t]:
                data["electrical"][panel][t][fid] = raw
        elif key.startswith("ahu__") and len(parts) == 4:
            unit, slot, fid = parts[1], parts[2], parts[3]
            block = data["ahu"].get(unit) or {}
            if slot in block and fid in block[slot]:
                data["ahu"][unit][slot][fid] = raw
        elif key.startswith("hw__") and len(parts) == 4:
            slot, bid, fid = parts[1], parts[2], parts[3]
            block = data["hw"].get(slot) or {}
            if bid in block and fid in block[bid]:
                data["hw"][slot][bid][fid] = raw
        elif key.startswith("ws__") and len(parts) == 4:
            slot, bid, fid = parts[1], parts[2], parts[3]
            block = data["water"].get(slot) or {}
            if bid in block and fid in block[bid]:
                data["water"][slot][bid][fid] = raw
        elif key.startswith("tank__") and len(parts) == 3:
            slot, fid = parts[1], parts[2]
            if slot in data["tank"] and fid in data["tank"][slot]:
                data["tank"][slot][fid] = raw
        elif key.startswith("fh__") and len(parts) == 2:
            fid = parts[1]
            if fid in data["floor_hvac"]:
                data["floor_hvac"][fid] = raw
        elif key.startswith("fire__") and len(parts) == 2:
            fid = parts[1]
            if fid in data["fire"]:
                data["fire"][fid] = raw
        elif key.startswith("ups__") and len(parts) == 2:
            fid = parts[1]
            if fid in data["ups"]:
                data["ups"][fid] = raw
        elif key == "notes":
            data["notes"] = raw
    return data


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
):
    from models import Park1538Daily

    return (
        await session.execute(
            select(Park1538Daily).where(
                Park1538Daily.building_id == building_id,
                Park1538Daily.log_date == log_date,
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
):
    from models import Park1538Daily

    row = await get_daily_row(session, building_id, log_date)
    if row:
        synced, changed = await sync_prev_values(session, building_id, log_date, row.data or {})
        if changed:
            row.data = synced
            await session.flush()
        return row
    data, _ = await sync_prev_values(session, building_id, log_date, empty_daily_payload())
    row = Park1538Daily(building_id=building_id, log_date=log_date, data=data)
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
    from models import Park1538Daily

    rows = (
        await session.execute(
            select(Park1538Daily).where(
                Park1538Daily.building_id == building_id,
                Park1538Daily.log_date >= date(year, 1, 1),
                Park1538Daily.log_date <= date(year, 12, 31),
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

    from models import Park1538Daily

    rows = (
        await session.execute(
            select(Park1538Daily)
            .where(
                Park1538Daily.building_id == building_id,
                Park1538Daily.log_date >= date(year, month, 1),
                Park1538Daily.log_date <= date(
                    year, month, calendar.monthrange(year, month)[1]
                ),
            )
            .order_by(Park1538Daily.log_date.asc())
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
    return building if is_park1538_building(building) else None


def p1538_daily_qr_url(building_code: str, request: Any | None = None) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base or 'http://127.0.0.1:8000'}/p1538/{(building_code or '').strip()}/daily"


# 하위 호환 별칭
park1538_daily_qr_url = p1538_daily_qr_url


def qr_png_bytes(url: str) -> bytes:
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _archive_daily(
    session: AsyncSession, building_id: int, log_date: date
):
    from models import Park1538Archive

    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    archive = (
        await session.execute(
            select(Park1538Archive).where(
                Park1538Archive.building_id == building_id,
                Park1538Archive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    payload = json.dumps(row.data or {}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"Park1538_운영일보_{log_date.isoformat()}.json"
    if archive:
        archive.original_name = filename
        archive.file_data = payload
        archive.file_size = len(payload)
    else:
        archive = Park1538Archive(
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
                CREATE TABLE IF NOT EXISTS park1538_daily (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    CONSTRAINT uq_p1538_daily UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS park1538_archives (
                    id SERIAL PRIMARY KEY,
                    building_id INTEGER NOT NULL REFERENCES buildings(id),
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BYTEA,
                    file_size INTEGER,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    CONSTRAINT uq_p1538_archive UNIQUE (building_id, log_date)
                )
            """))
        else:
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS park1538_daily (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME,
                    updated_at DATETIME,
                    CONSTRAINT uq_p1538_daily UNIQUE (building_id, log_date)
                )
            """))
            await connection.execute(text("""
                CREATE TABLE IF NOT EXISTS park1538_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    original_name VARCHAR(300),
                    file_data BLOB,
                    file_size INTEGER,
                    created_at DATETIME,
                    CONSTRAINT uq_p1538_archive UNIQUE (building_id, log_date)
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
                if not is_park1538_building(building):
                    continue
                try:
                    await rollover_at_midnight(session, building_id, today)
                except Exception as exc:
                    await session.rollback()
                    print(f"[p1538] rollover building={building_id}: {exc}", flush=True)

    scheduler.add_job(
        _daily_job,
        "cron",
        hour=23,
        minute=59,
        timezone=kst,
        id="park1538_rollover",
        replace_existing=True,
    )


if __name__ == "__main__":
    # 스모크: 스키마·빈 페이로드·재계산·폼 파싱
    schema = load_schema()
    assert schema.get("building_name") == "PARK1538"
    payload = empty_daily_payload()
    assert "multipliers" not in payload
    assert set(payload["utility"]) == {
        "promo_water", "promo_hw", "edu_water", "edu_hw", "gas", "power",
    }
    assert set(payload["electrical"]) == {"vcb1", "vcb2", "vcb3", "vcb4", "vcb5"}
    assert set(payload["electrical"]["vcb1"]["t1"]) >= {
        "time", "tr_v", "tr_a", "tr_kw", "tr_kvar", "tr_pf", "tr_temp",
        "acb_v", "acb_a", "acb_pf", "acb_kwh",
    }
    assert "ahu101" in payload["ahu"]
    assert set(payload["ahu"]["ahu101"]["1"]) == {
        "time", "ra_temp", "ra_hum", "sa_temp", "sa_hum", "dp",
    }
    assert set(payload["hw"]["1"]) == {"edu", "promo"}
    assert set(payload["hw"]["1"]["edu"]) == {
        "sup_temp", "sup_p", "ret_temp", "ret_p",
    }
    assert set(payload["water"]["2"]) == {"promo", "edu"}
    assert set(payload["water"]["2"]["promo"]) == {"set_p", "cur_p"}
    assert set(payload["tank"]["1"]) == {"time", "height", "level"}
    assert set(payload["floor_hvac"]) == {
        "time", "sup_temp", "sup_p", "ret_temp", "ret_p",
    }
    assert set(payload["fire"]) == {"hydrant", "sprinkler"}
    assert set(payload["ups"]) == {
        "pack_1", "pack_2", "pack_3", "pack_4",
        "pack_5", "pack_6", "pack_7", "pack_8",
        "total_v", "out_v", "soc", "cell_temp",
    }

    sample = {
        "utility": {
            "promo_water": {"prev": "100", "today": "110", "prev_manual": False},
            "gas": {"prev": "10", "today": "12"},
        },
    }
    computed = recompute_daily(sample, {"promo_water": "50", "gas": "3"})
    assert computed["utility"]["promo_water"]["daily"] == "10"
    assert computed["utility"]["promo_water"]["monthly"] == "60"
    assert computed["utility"]["gas"]["daily"] == "2"
    assert computed["utility"]["gas"]["monthly"] == "5"

    form = {
        "u__promo_water__prev": "1",
        "u__promo_water__today": "4",
        "u__promo_water__remark": "ok",
        "el__vcb1__t1__tr_a": "12",
        "el__vcb1__t1__acb_v": "380",
        "ahu__ahu101__1__ra_temp": "24",
        "hw__1__edu__sup_temp": "55",
        "ws__2__promo__set_p": "3.0",
        "tank__1__height": "2.5",
        "tank__1__time": "09:00",
        "fh__sup_temp": "22",
        "fire__hydrant": "정상",
        "ups__pack_1": "12.6",
        "ups__soc": "98",
        "notes": "특이없음",
    }
    parsed = parse_daily_form(form)
    assert parsed["utility"]["promo_water"]["prev"] == "1"
    assert parsed["utility"]["promo_water"]["today"] == "4"
    assert parsed["electrical"]["vcb1"]["t1"]["tr_a"] == "12"
    assert parsed["electrical"]["vcb1"]["t1"]["acb_v"] == "380"
    assert parsed["ahu"]["ahu101"]["1"]["ra_temp"] == "24"
    assert parsed["hw"]["1"]["edu"]["sup_temp"] == "55"
    assert parsed["water"]["2"]["promo"]["set_p"] == "3.0"
    assert parsed["tank"]["1"]["height"] == "2.5"
    assert parsed["tank"]["1"]["time"] == "09:00"
    assert parsed["floor_hvac"]["sup_temp"] == "22"
    assert parsed["fire"]["hydrant"] == "정상"
    assert parsed["ups"]["pack_1"] == "12.6"
    assert parsed["ups"]["soc"] == "98"
    assert parsed["notes"] == "특이없음"
    recomputed = recompute_daily(parsed)
    assert recomputed["utility"]["promo_water"]["daily"] == "3"

    url = p1538_daily_qr_url("PARK1538")
    assert url.endswith("/p1538/PARK1538/daily")
    print("[p1538] smoke ok")
