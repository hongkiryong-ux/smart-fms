# backup_export.py — 시스템관리자 전체 자료 ZIP 백업 (무압축, 스트리밍)
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from datetime import date, datetime
from enum import Enum
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    AppSetting,
    Building,
    BuildingDrawing,
    BuildingStandard,
    D1Plan,
    Equipment,
    EquipmentType,
    Floor,
    InspectionLogBuilding,
    InspectionLogFile,
    MaintenanceRecord,
    MaterialItem,
    MaterialLog,
    Partner,
    PMInspection,
    PMSchedule,
    Site,
    User,
    WorkOrder,
    Zone,
)

try:
    from streetlamp.models import Lamp, MaintenanceRequest
except Exception:  # pragma: no cover
    Lamp = None
    MaintenanceRequest = None


_UNSAFE = re.compile(r"[^\w.\-가-힣]+", re.UNICODE)


def _safe_name(name: str | None, fallback: str = "file") -> str:
    raw = Path(name or "").name.strip() or fallback
    cleaned = _UNSAFE.sub("_", raw).strip("._") or fallback
    return cleaned[:180]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if len(text) > 32000:
        return text[:32000] + "…"
    return text


def _model_keys(model, skip: set[str] | None = None) -> list[str]:
    skip = skip or set()
    keys: list[str] = []
    for col in sa_inspect(model).columns:
        if col.key in skip:
            continue
        if col.type.__class__.__name__ == "LargeBinary":
            continue
        keys.append(col.key)
    return keys


async def _append_sheet(db: AsyncSession, wb: Workbook, title: str, model, skip: set[str] | None = None) -> int:
    keys = _model_keys(model, skip)
    ws = wb.create_sheet(title[:31])
    ws.append(keys)
    count = 0
    result = await db.stream(select(model))
    async for obj in result.scalars():
        ws.append([_cell(getattr(obj, k, None)) for k in keys])
        count += 1
    return count


def _disk_bytes(path: Path) -> bytes | None:
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        return None
    return None


async def _add_blob_files(
    zf: zipfile.ZipFile,
    db: AsyncSession,
    model,
    folder: str,
    *,
    id_attr: str = "id",
    building_attr: str = "building_id",
    disk_builder=None,
) -> int:
    """파일 본문을 한 행씩 읽어 ZIP에 넣는다 (메모리에 전체를 올리지 않음)."""
    cols = [model.id, getattr(model, building_attr), model.original_name, model.stored_name, model.file_data]
    result = await db.stream(select(*cols))
    n = 0
    seen: set[str] = set()
    async for row in result:
        fid = int(row[0])
        bid = int(row[1]) if row[1] is not None else 0
        original = row[2] or row[3] or f"{fid}"
        stored = row[3] or ""
        data = bytes(row[4]) if row[4] else None
        if not data and disk_builder is not None:
            data = _disk_bytes(disk_builder(bid, stored))
        if not data:
            continue
        name = _safe_name(original, f"{fid}")
        zip_path = f"{folder}/{bid}/{fid}_{name}"
        if zip_path in seen:
            zip_path = f"{folder}/{bid}/{fid}_{stored or name}"
        seen.add(zip_path)
        zf.writestr(zip_path, data, compress_type=zipfile.ZIP_STORED)
        n += 1
        del data
    return n


def _add_uploads_dir(zf: zipfile.ZipFile) -> int:
    root = Path("static") / "uploads"
    if not root.is_dir():
        return 0
    n = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            zf.write(path, f"files/disk_uploads/{rel}", compress_type=zipfile.ZIP_STORED)
            n += 1
        except OSError:
            continue
    return n


async def build_backup_zip(db: AsyncSession, dest: Path) -> dict:
    """업로드 파일 + 업무 엑셀을 무압축 ZIP으로 dest에 기록."""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(
        dest,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as zf:
        counts["inspection_logs"] = await _add_blob_files(
            zf,
            db,
            InspectionLogFile,
            "files/inspection_logs",
            disk_builder=lambda bid, stored: (
                Path("static") / "uploads" / "buildings" / str(bid) / "inspection_logs" / stored
            ),
        )
        counts["drawings"] = await _add_blob_files(
            zf,
            db,
            BuildingDrawing,
            "files/drawings",
            disk_builder=lambda bid, stored: (
                Path("static") / "uploads" / "buildings" / str(bid) / stored
            ),
        )
        counts["standards"] = await _add_blob_files(
            zf,
            db,
            BuildingStandard,
            "files/standards",
            disk_builder=lambda bid, stored: (
                Path("static") / "uploads" / "buildings" / str(bid) / "standards" / stored
            ),
        )
        counts["disk_uploads"] = _add_uploads_dir(zf)

        wb = Workbook(write_only=True)
        counts["excel_sites"] = await _append_sheet(db, wb, "사업장", Site)
        counts["excel_buildings"] = await _append_sheet(db, wb, "건물", Building)
        counts["excel_floors"] = await _append_sheet(db, wb, "층", Floor)
        counts["excel_zones"] = await _append_sheet(db, wb, "구역", Zone)
        counts["excel_equipment"] = await _append_sheet(db, wb, "설비", Equipment)
        counts["excel_eq_types"] = await _append_sheet(db, wb, "설비종류", EquipmentType)
        counts["excel_pm"] = await _append_sheet(db, wb, "점검일정", PMSchedule)
        counts["excel_pm_insp"] = await _append_sheet(db, wb, "점검결과", PMInspection)
        counts["excel_work_orders"] = await _append_sheet(db, wb, "정비의뢰", WorkOrder)
        counts["excel_maint"] = await _append_sheet(db, wb, "정비이력", MaintenanceRecord)
        counts["excel_d1"] = await _append_sheet(db, wb, "D1계획", D1Plan)
        counts["excel_partners"] = await _append_sheet(db, wb, "협력사", Partner)
        counts["excel_users"] = await _append_sheet(
            db,
            wb,
            "사용자",
            User,
            skip={"password_hash", "openai_api_key"},
        )
        counts["excel_materials"] = await _append_sheet(db, wb, "자재", MaterialItem)
        counts["excel_material_logs"] = await _append_sheet(db, wb, "자재로그", MaterialLog)
        counts["excel_ilog_buildings"] = await _append_sheet(
            db, wb, "점검일지건물", InspectionLogBuilding
        )
        counts["excel_ilog_files"] = await _append_sheet(db, wb, "점검일지파일목록", InspectionLogFile)
        counts["excel_drawings"] = await _append_sheet(db, wb, "도면목록", BuildingDrawing)
        counts["excel_standards"] = await _append_sheet(db, wb, "표준서목록", BuildingStandard)
        counts["excel_settings"] = await _append_sheet(db, wb, "앱설정", AppSetting)
        if Lamp is not None:
            counts["excel_lamps"] = await _append_sheet(db, wb, "가로등", Lamp)
        if MaintenanceRequest is not None:
            counts["excel_lamp_req"] = await _append_sheet(
                db, wb, "가로등정비의뢰", MaintenanceRequest
            )

        xlsx_buf = BytesIO()
        wb.save(xlsx_buf)
        zf.writestr(
            "excel/업무데이터.xlsx",
            xlsx_buf.getvalue(),
            compress_type=zipfile.ZIP_STORED,
        )
        del xlsx_buf

        readme = (
            "Smart FMS 전체 백업\n"
            f"- 생성시각: {datetime.now().isoformat(sep=' ', timespec='seconds')}\n"
            "- files/ : 사이트에 올린 원본 파일 (점검일지 엑셀, 도면, 표준서)\n"
            "- excel/업무데이터.xlsx : 사업장·설비·정비·점검 등 업무 자료\n"
            "- 이미 압축된 파일은 다시 압축하지 않았습니다 (빠른 다운로드).\n"
            f"- 건수: {json.dumps(counts, ensure_ascii=False)}\n"
        )
        zf.writestr("README.txt", readme.encode("utf-8"), compress_type=zipfile.ZIP_STORED)

    return counts
