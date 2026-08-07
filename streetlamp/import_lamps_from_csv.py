# import_lamps_from_csv.py — data/lamp_codes.csv → DB lamps 테이블
import asyncio
import csv
import os
from pathlib import Path

from sqlalchemy import func, select, text

from database import AsyncSessionLocal, Base, ensure_schema_updates, engine
import streetlamp.models  # noqa: F401 - Base metadata에 가로등 모델 등록
from streetlamp.models import Lamp

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"


def _is_postgres() -> bool:
    url = (
        os.environ.get("DATABASE_INTERNAL_URL", "")
        or os.environ.get("DATABASE_URL", "")
        or ""
    ).lower()
    return "postgres" in url


async def _sync_lamps_id_sequence(session) -> None:
    """init_lamps 등이 id를 직접 넣은 뒤 시퀀스가 뒤처질 때 충돌 방지."""
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


async def import_lamps() -> int:
    if not CSV_PATH.is_file():
        raise FileNotFoundError(
            f"{CSV_PATH} 없음. 먼저 scripts/build_lamp_codes_csv.py 를 실행하세요."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_updates()

    rows: list[dict[str, str]] = []
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if code:
                rows.append(row)

    added = 0
    async with AsyncSessionLocal() as session:
        await _sync_lamps_id_sequence(session)

        existing_codes: set[str] = set(
            await session.scalars(select(Lamp.code).where(Lamp.code.isnot(None)))
        )

        for row in rows:
            code = row["code"].strip()
            prefix = (row.get("group_prefix") or code.rsplit("-", 1)[0]).strip()
            location = f"가로등 {code}"

            if code in existing_codes:
                result = await session.execute(select(Lamp).where(Lamp.code == code))
                existing = result.scalar_one_or_none()
                if existing and existing.location != location:
                    existing.location = location
                continue

            session.add(
                Lamp(
                    code=code,
                    location=location,
                    description=f"구역 {prefix}" if prefix else None,
                )
            )
            existing_codes.add(code)
            added += 1

        # 기존 숫자 id 1~100 → code 문자열 백필 (하위 호환)
        for num in range(1, 101):
            code_s = str(num)
            result = await session.execute(select(Lamp).where(Lamp.id == num))
            lamp = result.scalar_one_or_none()
            if lamp and not lamp.code:
                lamp.code = code_s
                existing_codes.add(code_s)

        await _sync_lamps_id_sequence(session)
        await session.commit()

    return added


async def import_lamps_if_needed() -> int:
    """CSV에 있는 코드가 DB에 없을 때만 import (기동마다 전체 스캔 방지)."""
    if not CSV_PATH.is_file():
        print(f"[lamp-import] skip: no CSV at {CSV_PATH}", flush=True)
        return 0

    expected = 0
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("code") or "").strip():
                expected += 1
    if expected <= 0:
        return 0

    async with AsyncSessionLocal() as session:
        gl1 = await session.scalar(select(Lamp).where(Lamp.code == "GL-1"))
        if gl1 is not None:
            print("[lamp-import] skip: GL-1 already in DB", flush=True)
            return 0

        n = await session.scalar(
            select(func.count()).select_from(Lamp).where(Lamp.code.isnot(None))
        )
        if (n or 0) >= expected:
            print(
                f"[lamp-import] skip: DB has {n} coded lamps (>={expected})",
                flush=True,
            )
            return 0

    added = await import_lamps()
    print(f"[lamp-import] done: {added} new lamps (expected {expected})", flush=True)
    return added


if __name__ == "__main__":
    n = asyncio.run(import_lamps())
    print(f"완료. 새로 등록된 가로등: {n}개 (CSV: {CSV_PATH})")
