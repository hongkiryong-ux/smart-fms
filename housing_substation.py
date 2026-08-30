# -*- coding: utf-8 -*-
"""주택변전소 점검일지2 — 1일·월보 웹 입력·자동 집계·엑셀 아카이브."""
from __future__ import annotations

import io
import json
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Building, HousingSubstationArchive, HousingSubstationDaily

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "resources" / "housing_substation_schema.json"
TEMPLATE_XLSX = ROOT / "resources" / "housing_substation_template.xlsx"

_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema_cache


def is_housing_substation_building(building: Building | None) -> bool:
    if not building:
        return False
    name = (building.name or "").strip()
    target = load_schema().get("building_name", "주택변전소")
    return name == target or target in name


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").upper().replace(".", ""))


def _breaker_match(daily_name: str, monthly_name: str) -> bool:
    a, b = _norm_name(daily_name), _norm_name(monthly_name)
    if a == b:
        return True
    if a in b or b in a:
        return True
    # NO3 INCOMMING vs NO.3 6.6KV INCOMMING
    ma = re.search(r"NO\s*(\d+)", a)
    mb = re.search(r"NO\s*(\d+)", b)
    if ma and mb and ma.group(1) == mb.group(1) and "INCOMMING" in a and "INCOMMING" in b:
        return True
    return False


def empty_daily_payload() -> dict:
    schema = load_schema()
    out: dict[str, Any] = {}
    for block in schema["daily_blocks"]:
        bid = block["id"]
        prev: dict[str, Any] = {}
        for m in block["meters"]:
            prev[m["id"]] = ""
        out[bid] = {"prev": prev, "times": {t: {} for t in block["times"]}}
    return out


def _parse_num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return None


async def get_daily_row(
    session: AsyncSession, building_id: int, log_date: date
) -> HousingSubstationDaily | None:
    return (
        await session.execute(
            select(HousingSubstationDaily).where(
                HousingSubstationDaily.building_id == building_id,
                HousingSubstationDaily.log_date == log_date,
            )
        )
    ).scalar_one_or_none()


async def get_or_create_daily(
    session: AsyncSession, building_id: int, log_date: date
) -> HousingSubstationDaily:
    row = await get_daily_row(session, building_id, log_date)
    if row:
        return row
    prev_vals = await _prev_day_readings(session, building_id, log_date)
    data = empty_daily_payload()
    for bid, meters in prev_vals.items():
        if bid in data:
            for mid, val in meters.items():
                data[bid]["prev"][mid] = val
    row = HousingSubstationDaily(
        building_id=building_id,
        log_date=log_date,
        data=data,
    )
    session.add(row)
    await session.flush()
    return row


async def _prev_day_readings(
    session: AsyncSession, building_id: int, log_date: date
) -> dict[str, dict[str, Any]]:
    """전일 22:00 적산(지침) → 오늘 prev."""
    prev_date = log_date - timedelta(days=1)
    row = await get_daily_row(session, building_id, prev_date)
    if not row or not row.data:
        return {}
    schema = load_schema()
    out: dict[str, dict[str, Any]] = {}
    for block in schema["daily_blocks"]:
        bid = block["id"]
        block_data = (row.data or {}).get(bid) or {}
        times = block_data.get("times") or {}
        t2200 = times.get("22:00") or {}
        prev: dict[str, Any] = {}
        for m in block["meters"]:
            rc = m["reading_col"]
            val = t2200.get(rc, "")
            prev[m["id"]] = val
        out[bid] = prev
    return out


def merge_daily_save(existing: dict, posted: dict) -> dict:
    data = deepcopy(existing) if existing else empty_daily_payload()
    for bid, block_post in (posted or {}).items():
        if bid not in data:
            data[bid] = {"prev": {}, "times": {}}
        if "prev" in block_post:
            data[bid]["prev"].update(block_post.get("prev") or {})
        for t, cells in (block_post.get("times") or {}).items():
            data[bid]["times"].setdefault(t, {})
            data[bid]["times"][t].update(cells or {})
    return data


def compute_monthly_report(
    building_id: int,
    year: int,
    month: int,
    daily_rows: list[HousingSubstationDaily],
) -> dict[str, Any]:
    """월보: 일별 22:00 지침·사용량·최대(A)."""
    del building_id
    schema = load_schema()
    by_date = {r.log_date: r.data or {} for r in daily_rows}
    sections_out = []
    for sec in schema["monthly_sections"]:
        sec_id = sec["id"]
        block = next((b for b in schema["daily_blocks"] if b["id"] == sec_id), None)
        days = []
        for day in range(1, 32):
            try:
                d = date(year, month, day)
            except ValueError:
                break
            row_data = by_date.get(d) or {}
            block_data = row_data.get(sec_id, {}) if block else {}
            times = block_data.get("times") or {}
            prev_map = block_data.get("prev") or {}
            t2200 = times.get("22:00") or {}
            breaker_rows = []
            for br in sec["breakers"]:
                meter = None
                if block:
                    for m in block["meters"]:
                        if _breaker_match(m["name"], br["name"]):
                            meter = m
                            break
                reading = ""
                usage = ""
                max_a = ""
                if meter:
                    rc, cc = meter["reading_col"], meter["current_col"]
                    reading = t2200.get(rc, "")
                    prev_v = _parse_num(prev_map.get(meter["id"]))
                    read_v = _parse_num(reading)
                    mult = meter.get("multiplier") or br.get("multiplier") or 4800
                    if prev_v is not None and read_v is not None:
                        usage = round((read_v - prev_v) * mult, 2)
                    if cc:
                        amps = []
                        for t, cells in times.items():
                            av = _parse_num((cells or {}).get(cc))
                            if av is not None:
                                amps.append(av)
                        if amps:
                            max_a = max(amps)
                breaker_rows.append(
                    {
                        "name": br["name"],
                        "reading": reading,
                        "usage": usage,
                        "max_a": max_a,
                    }
                )
            days.append({"day": day, "date": d.isoformat(), "breakers": breaker_rows})
        sections_out.append({"id": sec_id, "title": sec_id.upper(), "days": days})
    return {"year": year, "month": month, "sections": sections_out}


async def list_archives(
    session: AsyncSession, building_id: int, limit: int = 60
) -> list[HousingSubstationArchive]:
    rows = (
        await session.execute(
            select(HousingSubstationArchive)
            .where(HousingSubstationArchive.building_id == building_id)
            .order_by(HousingSubstationArchive.log_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def archive_daily_excel(
    session: AsyncSession,
    building_id: int,
    log_date: date,
) -> HousingSubstationArchive | None:
    """해당 일자 1일 시트를 엑셀으로 저장."""
    row = await get_daily_row(session, building_id, log_date)
    if not row:
        return None
    existing = (
        await session.execute(
            select(HousingSubstationArchive).where(
                HousingSubstationArchive.building_id == building_id,
                HousingSubstationArchive.log_date == log_date,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    xbytes = export_daily_to_excel(row.data or {}, log_date)
    fname = f"주택변전소_1일_{log_date.isoformat()}.xlsx"
    arch = HousingSubstationArchive(
        building_id=building_id,
        log_date=log_date,
        original_name=fname,
        file_data=xbytes,
        file_size=len(xbytes),
    )
    session.add(arch)
    await session.flush()
    return arch


def export_daily_to_excel(data: dict, log_date: date) -> bytes:
    """템플릿 xlsx에 당일 입력값을 채워 반환."""
    if not TEMPLATE_XLSX.is_file():
        wb = load_workbook()
        ws = wb.active
        ws.title = "1일"
        ws["A1"] = log_date.isoformat()
    else:
        wb = load_workbook(TEMPLATE_XLSX)
        ws = wb["1일"] if "1일" in wb.sheetnames else wb.active
        ws["A1"] = log_date.day

    schema = load_schema()
    block_starts = {"no3": 7, "no2": 20, "no1": 32}
    time_index = {"06:00": 0, "10:00": 1, "14:00": 2, "17:00": 3, "20:00": 4, "22:00": 5}

    for block in schema["daily_blocks"]:
        bid = block["id"]
        start = block_starts.get(bid, 7)
        block_data = data.get(bid) or {}
        times = block_data.get("times") or {}
        col_map = {c["col"]: c for c in block.get("input_columns") or []}
        all_cols = {c["col"] for c in block.get("input_columns") or []}
        for m in block.get("meters") or []:
            all_cols.add(m["reading_col"])
        for t, idx in time_index.items():
            row = start + idx
            cells = times.get(t) or {}
            for col_letter in all_cols:
                val = cells.get(col_letter)
                if val not in (None, ""):
                    ws[f"{col_letter}{row}"] = val

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def rollover_at_midnight(session: AsyncSession, building_id: int, closing_date: date) -> None:
    """당일 23:59 마감: 해당 일자 엑셀 아카이브 후 다음 날 일지 생성."""
    await archive_daily_excel(session, building_id, closing_date)
    await get_or_create_daily(session, building_id, closing_date + timedelta(days=1))
    await session.commit()


async def ensure_tables(engine) -> None:
    from sqlalchemy import text

    url = str(engine.url).lower()
    is_pg = "postgresql" in url or "postgres" in url
    async with engine.begin() as conn:
        if is_pg:
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS housing_substation_daily (
                        id SERIAL PRIMARY KEY,
                        building_id INTEGER NOT NULL REFERENCES buildings(id),
                        log_date DATE NOT NULL,
                        data JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        UNIQUE (building_id, log_date)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS housing_substation_archives (
                        id SERIAL PRIMARY KEY,
                        building_id INTEGER NOT NULL REFERENCES buildings(id),
                        log_date DATE NOT NULL,
                        original_name VARCHAR(300),
                        file_data BYTEA,
                        file_size INTEGER,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        UNIQUE (building_id, log_date)
                    )
                    """
                )
            )
        else:
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS housing_substation_daily (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        building_id INTEGER NOT NULL,
                        log_date DATE NOT NULL,
                        data TEXT NOT NULL DEFAULT '{}',
                        created_at DATETIME,
                        updated_at DATETIME,
                        UNIQUE (building_id, log_date)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS housing_substation_archives (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        building_id INTEGER NOT NULL,
                        log_date DATE NOT NULL,
                        original_name VARCHAR(300),
                        file_data BLOB,
                        file_size INTEGER,
                        created_at DATETIME,
                        UNIQUE (building_id, log_date)
                    )
                    """
                )
            )


def register_scheduler(scheduler, session_factory, kst) -> None:
    """매일 23:59 KST — 주택변전소 일지 마감."""

    async def _job():
        from sqlalchemy import select

        async with session_factory() as session:
            try:
                buildings = (
                    await session.execute(select(Building).where(Building.is_active == True))  # noqa: E712
                ).scalars().all()
                target = load_schema().get("building_name", "주택변전소")
                today = datetime.now(kst).date()
                for b in buildings:
                    if not b.name or target not in b.name:
                        continue
                    try:
                        await rollover_at_midnight(session, b.id, today)
                    except Exception as e:
                        print(f"[housing_sub] rollover building={b.id}: {e}", flush=True)
            except Exception as e:
                print(f"[housing_sub] scheduler job: {e}", flush=True)

    scheduler.add_job(
        _job,
        trigger="cron",
        hour=23,
        minute=59,
        id="housing_substation_rollover",
        replace_existing=True,
    )
