# import_lamps_from_csv.py — data/streetlamp/lamp_codes.csv → DB lamps
from __future__ import annotations

import asyncio
import csv
import os
import tempfile
from pathlib import Path

from sqlalchemy import func, select, text

from database import AsyncSessionLocal, engine
import streetlamp.models  # noqa: F401
from streetlamp.models import Lamp

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "lamp_codes.csv"
XLSX_PATH = Path(__file__).resolve().parent.parent / "data" / "streetlamp" / "가로등 adress.xlsx"

# 백그라운드 작업 상태 (프로세스 단위)
_import_lock = asyncio.Lock()
_import_status: dict[str, object] = {
    "running": False,
    "message": "",
    "added": 0,
    "updated": 0,
    "total": 0,
    "error": "",
}


def get_import_status() -> dict[str, object]:
    return dict(_import_status)


def _is_postgres() -> bool:
    url = (
        os.environ.get("DATABASE_INTERNAL_URL", "")
        or os.environ.get("DATABASE_URL", "")
        or ""
    ).lower()
    return "postgres" in url


async def _ensure_lamps_table() -> None:
    """가로등 테이블만 빠르게 보장 (전체 스키마 마이그레이션은 하지 않음)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS lamps (
      id SERIAL PRIMARY KEY,
      code VARCHAR(64) UNIQUE,
      location VARCHAR(255) NOT NULL,
      description TEXT
    )
    """
    if not _is_postgres():
        ddl = """
        CREATE TABLE IF NOT EXISTS lamps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code VARCHAR(64) UNIQUE,
          location VARCHAR(255) NOT NULL,
          description TEXT
        )
        """
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
        if _is_postgres():
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lamps_code ON lamps (code)"))


async def _sync_lamps_id_sequence(session) -> None:
    if not _is_postgres():
        return
    await session.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('lamps', 'id'), "
            "COALESCE((SELECT MAX(id) FROM lamps), 1))"
        )
    )


def load_rows_from_csv(path: Path | None = None) -> list[dict[str, str]]:
    csv_path = path or CSV_PATH
    if not csv_path.is_file():
        raise FileNotFoundError(f"{csv_path} 없음")
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            prefix = (row.get("group_prefix") or code.rsplit("-", 1)[0]).strip()
            location = (row.get("location") or code).strip() or code
            rows.append({"code": code, "group_prefix": prefix, "location": location})
    return rows


def rebuild_csv_from_xlsx(xlsx_path: Path | None = None) -> tuple[int, Path]:
    """엑셀 → CSV. Render 등 읽기전용 배포본은 /tmp 에 기록.

    반환: (건수, 사용한 CSV 경로)
    """
    from openpyxl import load_workbook

    path = xlsx_path or XLSX_PATH
    if not path.is_file():
        raise FileNotFoundError(f"엑셀 없음: {path}")

    # 배포본 CSV가 이미 있으면 재생성 생략 (버튼 응답 지연·디스크 쓰기 방지)
    if CSV_PATH.is_file() and os.environ.get("STREETLAMP_FORCE_XLSX_REBUILD", "").strip() not in (
        "1",
        "true",
        "yes",
    ):
        rows = load_rows_from_csv(CSV_PATH)
        return len(rows), CSV_PATH

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

    out = CSV_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(out, codes)
    except OSError:
        out = Path(tempfile.gettempdir()) / "smart_fms_lamp_codes.csv"
        _write_csv(out, codes)
    return len(codes), out


def _write_csv(path: Path, codes: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "group_prefix", "location"])
        writer.writeheader()
        for code in codes:
            prefix = code.rsplit("-", 1)[0] if "-" in code else code
            writer.writerow({"code": code, "group_prefix": prefix, "location": code})


async def import_lamps(*, replace_all: bool = False, csv_path: Path | None = None) -> int:
    """CSV의 모든 코드를 lamps 에 등록/갱신. 반환: 신규 추가 건수."""
    path = csv_path or CSV_PATH
    if not path.is_file() and XLSX_PATH.is_file():
        _, path = rebuild_csv_from_xlsx()
    rows = load_rows_from_csv(path)

    await _ensure_lamps_table()

    added = 0
    updated = 0
    async with AsyncSessionLocal() as session:
        await _sync_lamps_id_sequence(session)

        if replace_all:
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

        batch_new: list[Lamp] = []
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
            batch_new.append(lamp)
            existing_by_code[code] = lamp
            added += 1
            # 배치 커밋으로 락·메모리 완화
            if len(batch_new) >= 200:
                session.add_all(batch_new)
                await session.commit()
                batch_new.clear()

        if batch_new:
            session.add_all(batch_new)
        await _sync_lamps_id_sequence(session)
        await session.commit()

    _import_status.update(
        {
            "added": added,
            "updated": updated,
            "total": len(rows),
            "message": f"완료: 신규 {added} · 갱신 {updated} · 전체 {len(rows)}",
        }
    )
    print(
        f"[lamp-import] csv={len(rows)} added={added} updated={updated}",
        flush=True,
    )
    return added


async def run_import_job(*, replace_all: bool = False) -> None:
    """웹 버튼용 백그라운드 임포트."""
    if _import_lock.locked():
        return
    async with _import_lock:
        _import_status.update(
            {
                "running": True,
                "message": "가로등 어드레스 등록 중…",
                "added": 0,
                "updated": 0,
                "total": 0,
                "error": "",
            }
        )
        try:
            csv_path = CSV_PATH
            rebuilt = 0
            try:
                rebuilt, csv_path = rebuild_csv_from_xlsx()
                _import_status["message"] = f"CSV 준비 {rebuilt}건 → DB 등록 중…"
            except FileNotFoundError:
                if not CSV_PATH.is_file():
                    raise
            await import_lamps(replace_all=replace_all, csv_path=csv_path)
        except Exception as exc:
            _import_status.update(
                {
                    "running": False,
                    "error": str(exc)[:300],
                    "message": f"실패: {exc}",
                }
            )
            print(f"[lamp-import] failed: {exc}", flush=True)
            return
        _import_status["running"] = False


async def import_lamps_if_needed() -> int:
    """CSV 대비 누락 코드가 있으면 전체 upsert (기동 시)."""
    if not CSV_PATH.is_file():
        if XLSX_PATH.is_file():
            n, _ = rebuild_csv_from_xlsx()
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
        count, path = rebuild_csv_from_xlsx()
        print(f"엑셀→CSV {count}건: {path}")
    n = asyncio.run(import_lamps(replace_all=args.replace))
    print(f"완료. 신규 {n}건 (CSV: {CSV_PATH})")
