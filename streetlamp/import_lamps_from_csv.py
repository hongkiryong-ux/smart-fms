# import_lamps_from_csv.py — data/streetlamp/lamp_codes.csv → DB lamps
from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path

from sqlalchemy import func, select, text

from database import AsyncSessionLocal, Base, ensure_schema_updates, engine
import streetlamp.models  # noqa: F401
from streetlamp.models import Lamp

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"
XLSX_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "가로등 adress.xlsx"


def _is_postgres() -> bool:
    url = (
        os.environ.get("DATABASE_INTERNAL_URL", "")
        or os.environ.get("DATABASE_URL", "")
        or ""
    ).lower()
    return "postgres" in url


async def _sync_lamps_id_sequence(session) -> None:
    if not _is_postgres():
        return
    await session.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('lamps', 'id'), "
            "COALESCE((SELECT MAX(id) FROM lamps), 1)"
            ")"
        )
    )


def load_rows_from_csv() -> list[dict[str, str]]:
    if not CSV_PATH.is_file():
        raise FileNotFoundError(f"{CSV_PATH} 없음")
    rows: list[dict[str, str]] = []
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            prefix = (row.get("group_prefix") or code.rsplit("-", 1)[0]).strip()
            location = (row.get("location") or code).strip() or code
            rows.append({"code": code, "group_prefix": prefix, "location": location})
    return rows


def rebuild_csv_from_xlsx(xlsx_path: Path | None = None) -> int:
    """엑셀 시트 모든 비어 있지 않은 셀 → lamp_codes.csv."""
    from openpyxl import load_workbook

    path = xlsx_path or XLSX_PATH
    if not path.is_file():
        raise FileNotFoundError(f"엑셀 없음: {path}")

    wb = load_workbook(path, data_only=True)
    ws = wb.active
    codes: list[str] = []
    seen: set[str] = set()
    for row in ws.iter_rows(values_only=True):
        for value in row:
            if value is None:
                continue
            code = str(value).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            codes.append(code)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "group_prefix", "location"])
        writer.writeheader()
        for code in codes:
            prefix = code.rsplit("-", 1)[0] if "-" in code else code
            writer.writerow({"code": code, "group_prefix": prefix, "location": code})
    return len(codes)


async def import_lamps(*, replace_all: bool = False) -> int:
    """CSV의 모든 코드를 lamps 에 등록/갱신. 반환: 신규 추가 건수."""
    if not CSV_PATH.is_file() and XLSX_PATH.is_file():
        rebuild_csv_from_xlsx()
    rows = load_rows_from_csv()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_updates()

    added = 0
    updated = 0
    async with AsyncSessionLocal() as session:
        await _sync_lamps_id_sequence(session)

        if replace_all:
            # 의뢰 FK 가 있으면 함께 비움
            from streetlamp.models import MaintenanceRequest

            await session.execute(MaintenanceRequest.__table__.delete())
            await session.execute(Lamp.__table__.delete())
            await session.commit()

        existing_by_code: dict[str, Lamp] = {
            lamp.code: lamp
            for lamp in (
                await session.scalars(select(Lamp).where(Lamp.code.isnot(None)))
            ).all()
            if lamp.code
        }

        for row in rows:
            code = row["code"]
            location = row["location"]
            prefix = row["group_prefix"]
            description = f"구역 {prefix}" if prefix else None
            existing = existing_by_code.get(code)
            if existing:
                changed = False
                if existing.location != location:
                    existing.location = location
                    changed = True
                if (existing.description or "") != (description or ""):
                    existing.description = description
                    changed = True
                if changed:
                    updated += 1
                continue

            lamp = Lamp(code=code, location=location, description=description)
            session.add(lamp)
            existing_by_code[code] = lamp
            added += 1

        await _sync_lamps_id_sequence(session)
        await session.commit()

    print(
        f"[lamp-import] csv={len(rows)} added={added} updated={updated}",
        flush=True,
    )
    return added


async def import_lamps_if_needed() -> int:
    """CSV 대비 누락 코드가 있으면 전체 upsert (기동 시)."""
    if not CSV_PATH.is_file():
        if XLSX_PATH.is_file():
            n = rebuild_csv_from_xlsx()
            print(f"[lamp-import] rebuilt CSV from xlsx: {n}", flush=True)
        else:
            print(f"[lamp-import] skip: no CSV at {CSV_PATH}", flush=True)
            return 0

    rows = load_rows_from_csv()
    if not rows:
        return 0
    expected = {r["code"] for r in rows}

    async with AsyncSessionLocal() as session:
        existing = set(
            await session.scalars(select(Lamp.code).where(Lamp.code.isnot(None)))
        )
        missing = expected - existing
        db_n = await session.scalar(
            select(func.count()).select_from(Lamp).where(Lamp.code.isnot(None))
        )

    force = os.environ.get("STREETLAMP_SYNC_LAMPS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not force and not missing and (db_n or 0) >= len(expected):
        print(
            f"[lamp-import] skip: DB has {db_n} codes, csv={len(expected)}",
            flush=True,
        )
        return 0

    if missing:
        print(f"[lamp-import] missing {len(missing)} codes → sync", flush=True)
    return await import_lamps(replace_all=False)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-xlsx", action="store_true", help="엑셀에서 CSV 재생성")
    parser.add_argument("--replace", action="store_true", help="기존 lamps/의뢰 삭제 후 재등록")
    args = parser.parse_args()
    if args.from_xlsx:
        count = rebuild_csv_from_xlsx()
        print(f"엑셀→CSV {count}건: {CSV_PATH}")
    n = asyncio.run(import_lamps(replace_all=args.replace))
    print(f"완료. 신규 {n}건 (CSV: {CSV_PATH})")
