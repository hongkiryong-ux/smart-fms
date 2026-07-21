# excel_import.py
"""설비현황 엑셀 파싱 · 등록 · 출력."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import xlrd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Building, Equipment, Floor, Site, Zone

SKIP_SHEETS = {"총괄", "총괄표", "Sheet1", "TOTAL", "개요", "표지"}
SITE_NAME = "광양운영그룹"
SITE_CODE = "GY-OP"

# 사용자 제공 건물 목록 (파일명 기준)
BUILDING_NAMES = [
    "러닝센타",
    "기술연구원",
    "기술교육센터",
    "금호빗물펌프장",
    "금당어린이집",
    "60서브",
    "57서브",
    "56서브",
    "55서브",
    "54서브",
    "53서브",
    "52서브",
    "51서브",
    "18서브",
    "16서브",
    "12서브",
    "8서브,백운그린랜드",
    "7서브",
    "6서브",
    "5서브",
    "3서브",
    "2서브",
    "휴먼센터",
    "축구전용구장",
    "중앙관제실",
    "주택변전소",
    "제철회관",
    "제철소본부",
    "임원숙소 1,2,3,5,금호어버이집",
    "어울림체육관",
    "복지센터",
    "백운플라자",
    "백운아트홀",
    "백운쇼핑센터",
    "백운생활관5,6동",
    "백운생활관3,4동",
    "백운생활관1,2동",
    "백운대",
]


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val).strip()


def _building_code(name: str) -> str:
    code = re.sub(r"[^\w가-힣]", "", name)[:20]
    return code or "BLD"


def _open_workbook(path: str | Path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl

        return ("openpyxl", openpyxl.load_workbook(path, read_only=True, data_only=True))
    return ("xlrd", xlrd.open_workbook(str(path)))


def _sheet_names(wb_kind: str, wb) -> list[str]:
    if wb_kind == "xlrd":
        return wb.sheet_names()
    return wb.sheetnames


def _get_sheet(wb_kind: str, wb, name: str):
    if wb_kind == "xlrd":
        return wb.sheet_by_name(name)
    return wb[name]


def _cell(sheet, wb_kind: str, r: int, c: int) -> str:
    if wb_kind == "xlrd":
        return _safe_str(sheet.cell_value(r, c))
    row = list(sheet.iter_rows(min_row=r + 1, max_row=r + 1, values_only=True))
    if not row:
        return ""
    vals = row[0]
    return _safe_str(vals[c] if c < len(vals) else "")


def _row_values(sheet, wb_kind: str, r: int, max_col: int) -> list[str]:
    return [_cell(sheet, wb_kind, r, c) for c in range(max_col)]


def _find_header_row(sheet, wb_kind: str, nrows: int, ncols: int) -> int | None:
    keywords = ("구분", "구 분", "명칭", "TYPE", "형식", "PUMP", "FAN")
    for r in range(min(10, nrows)):
        row = _row_values(sheet, wb_kind, r, ncols)
        joined = " ".join(row)
        if any(k in joined for k in keywords):
            return r
    return None


def _is_data_row(cells: list[str]) -> bool:
    text = "".join(cells).strip()
    if not text:
        return False
    if cells[0] in ("계", "합계", "소계"):
        return False
    if "합계" in text or "소계" in text:
        return False
    return True


def parse_excel_file(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """엑셀 파일 → {시트명: [행 dict]} (총괄 제외)."""
    wb_kind, wb = _open_workbook(path)
    result: dict[str, list[dict]] = {}

    try:
        for sheet_name in _sheet_names(wb_kind, wb):
            if sheet_name in SKIP_SHEETS:
                continue
            sheet = _get_sheet(wb_kind, wb, sheet_name)
            if wb_kind == "xlrd":
                nrows, ncols = sheet.nrows, sheet.ncols
            else:
                nrows = sheet.max_row or 0
                ncols = sheet.max_column or 0

            if nrows < 2:
                continue

            header_row = _find_header_row(sheet, wb_kind, nrows, ncols)
            if header_row is None:
                continue

            headers = [_safe_str(h) or f"col{i}" for i, h in enumerate(_row_values(sheet, wb_kind, header_row, ncols))]
            rows: list[dict] = []

            for r in range(header_row + 1, nrows):
                cells = _row_values(sheet, wb_kind, r, ncols)
                if not _is_data_row(cells):
                    continue
                row_dict = {headers[i]: cells[i] for i in range(len(headers)) if headers[i] and cells[i]}
                if not row_dict:
                    continue
                # 설비명 추출
                name = (
                    row_dict.get("구분")
                    or row_dict.get("구 분")
                    or row_dict.get("명칭")
                    or cells[0]
                    or cells[2] if len(cells) > 2 else ""
                )
                if not name or name in ("PUMP", "FAN", "MOTOR"):
                    continue
                row_dict["_name"] = name
                rows.append(row_dict)

            if rows:
                result[sheet_name] = rows
    finally:
        if wb_kind == "openpyxl":
            wb.close()

    return result


def _equipment_code(building_code: str, sheet: str, idx: int, name: str) -> str:
    base = re.sub(r"[^\w]", "", sheet)[:6].upper()
    nm = re.sub(r"[^\w가-힣]", "", name)[:10]
    return f"{building_code}-{base}-{idx:03d}"[:64]


async def ensure_site_and_building(
    session: AsyncSession, building_name: str
) -> tuple[Site, Building, Zone]:
    site = (
        await session.execute(select(Site).where(Site.code == SITE_CODE))
    ).scalar_one_or_none()
    if not site:
        site = Site(name=SITE_NAME, code=SITE_CODE, address="전라남도 광양시")
        session.add(site)
        await session.flush()

    bcode = _building_code(building_name)
    building = (
        await session.execute(
            select(Building).where(Building.site_id == site.id, Building.code == bcode)
        )
    ).scalar_one_or_none()
    if not building:
        building = Building(site_id=site.id, name=building_name, code=bcode)
        session.add(building)
        await session.flush()

    floor = (
        await session.execute(
            select(Floor).where(Floor.building_id == building.id, Floor.name == "1층")
        )
    ).scalar_one_or_none()
    if not floor:
        floor = Floor(building_id=building.id, name="1층", level=1)
        session.add(floor)
        await session.flush()

    zone = (
        await session.execute(
            select(Zone).where(Zone.floor_id == floor.id, Zone.name == "전체")
        )
    ).scalar_one_or_none()
    if not zone:
        zone = Zone(floor_id=floor.id, name="전체", code="ALL")
        session.add(zone)
        await session.flush()

    return site, building, zone


async def import_excel_to_building(
    session: AsyncSession,
    building_name: str,
    file_path: str | Path,
    replace: bool = False,
) -> dict[str, int]:
    """엑셀 파일을 건물에 import. replace=True면 기존 설비 비활성화 후 재등록."""
    _, building, zone = await ensure_site_and_building(session, building_name)
    parsed = parse_excel_file(file_path)

    if replace:
        existing = (
            await session.execute(
                select(Equipment)
                .join(Zone)
                .join(Floor)
                .where(Floor.building_id == building.id, Equipment.is_active == True)
            )
        ).scalars().all()
        for eq in existing:
            eq.is_active = False

    stats = {"sheets": 0, "created": 0, "updated": 0}
    bcode = building.code

    for sheet_name, rows in parsed.items():
        stats["sheets"] += 1
        for idx, row in enumerate(rows, start=1):
            name = row.pop("_name", f"항목{idx}")
            code = _equipment_code(bcode, sheet_name, idx, name)

            existing = (
                await session.execute(
                    select(Equipment).where(Equipment.code == code)
                )
            ).scalar_one_or_none()

            manufacturer = row.get("제조사") or row.get("제조사/년") or ""
            model = row.get("TYPE") or row.get("Type or Model명") or row.get("MODEL NO.") or row.get("형식") or ""
            serial_no = row.get("Serial No") or row.get("Serial No.") or ""

            if existing:
                existing.is_active = True
                existing.name = name
                existing.category = sheet_name
                existing.zone_id = zone.id
                existing.manufacturer = manufacturer or existing.manufacturer
                existing.model = model or existing.model
                existing.serial_no = serial_no or existing.serial_no
                existing.extra_data = row
                stats["updated"] += 1
            else:
                session.add(
                    Equipment(
                        zone_id=zone.id,
                        code=code,
                        name=name,
                        category=sheet_name,
                        manufacturer=manufacturer,
                        model=model,
                        serial_no=serial_no or None,
                        extra_data=row,
                        status="normal",
                    )
                )
                stats["created"] += 1

    await session.commit()
    return stats


async def ensure_all_buildings(session: AsyncSession) -> int:
    """건물 목록만 등록 (엑셀 없이)."""
    count = 0
    for name in BUILDING_NAMES:
        await ensure_site_and_building(session, name)
        count += 1
    await session.commit()
    return count


async def import_from_directory(
    session: AsyncSession,
    directory: str | Path,
    replace: bool = True,
) -> dict[str, Any]:
    """디렉터리 내 xls/xlsx 파일 일괄 import."""
    directory = Path(directory)
    results: dict[str, Any] = {"buildings": 0, "total_created": 0, "total_updated": 0, "errors": []}

    # 먼저 모든 건물 등록
    results["buildings"] = await ensure_all_buildings(session)

    for name in BUILDING_NAMES:
        matched = None
        for ext in (".xls", ".xlsx", ".XLS", ".XLSX"):
            p = directory / f"{name}{ext}"
            if p.exists():
                matched = p
                break
        if not matched:
            # fuzzy: 파일명에 건물명 포함
            for f in directory.iterdir():
                if f.suffix.lower() in (".xls", ".xlsx") and name in f.stem:
                    matched = f
                    break
        if not matched:
            results["errors"].append(f"파일 없음: {name}")
            continue
        try:
            stats = await import_excel_to_building(session, name, matched, replace=replace)
            results["total_created"] += stats["created"]
            results["total_updated"] += stats["updated"]
        except Exception as e:
            results["errors"].append(f"{name}: {e}")

    return results


def export_building_excel(
    building_name: str,
    equipment_by_sheet: dict[str, list[Equipment]],
) -> bytes:
    """건물 설비를 엑셀 파일(bytes)로 출력."""
    wb = Workbook()
    wb.remove(wb.active)

    header_fill = PatternFill(start_color="003876", end_color="003876", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for sheet_name, items in equipment_by_sheet.items():
        safe_name = sheet_name[:31]
        ws = wb.create_sheet(title=safe_name)

        if not items:
            ws.append(["데이터 없음"])
            continue

        # extra_data 키 수집
        all_keys: list[str] = []
        for eq in items:
            for k in (eq.extra_data or {}):
                if k not in all_keys and not k.startswith("_"):
                    all_keys.append(k)

        headers = ["코드", "명칭", "제조사", "모델", "Serial"] + all_keys
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font

        for eq in items:
            extra = eq.extra_data or {}
            row = [
                eq.code,
                eq.name,
                eq.manufacturer or "",
                eq.model or "",
                eq.serial_no or "",
            ] + [extra.get(k, "") for k in all_keys]
            ws.append(row)

    # 총괄 시트
    summary = wb.create_sheet(title="총괄", index=0)
    summary.append([f"{building_name} 설비현황"])
    summary.append(["시트", "건수"])
    for sheet_name, items in equipment_by_sheet.items():
        summary.append([sheet_name, len(items)])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def match_building_filename(stem: str) -> str | None:
    """파일명(stem)에서 건물명 매칭."""
    stem = stem.strip()
    if stem in BUILDING_NAMES:
        return stem
    for name in BUILDING_NAMES:
        if name in stem or stem in name:
            return name
    return stem
