# equipment_schema.py
"""엑셀 시트별 설비 양식(컬럼) 정의 및 폼/표시 헬퍼."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models import Equipment

NAME_KEYS = (
    "구분",
    "구 분",
    "구  분",
    "구    분",
    "명칭",
    "PUMP",
    "FAN",
    "Pump",
    "FAN MOTOR",
    "MOTOR",
    "Motor",
)
MANUFACTURER_KEYS = ("제조사", "제조사/년", "제조회사", "MAKER", "제조년월")
MODEL_KEYS = ("TYPE", "Type or Model명", "Type & Model", "MODEL NO.", "형식", "형 식")
SERIAL_KEYS = ("Serial No", "Serial No.", "SER.NO", "제품번호", "제조번호")

_SCHEMA_PATH = Path(__file__).with_name("equipment_schemas.json")
_DEFAULT_SCHEMAS: dict[str, list[str]] = {}


def _load_schemas() -> dict[str, list[str]]:
    global _DEFAULT_SCHEMAS
    if _DEFAULT_SCHEMAS:
        return _DEFAULT_SCHEMAS
    if _SCHEMA_PATH.exists():
        _DEFAULT_SCHEMAS = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _DEFAULT_SCHEMAS


def is_noise_key(key: str) -> bool:
    return key.startswith("_") or bool(re.match(r"^col\d+$", key))


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def get_category_fields(category: str, equipment: list[Equipment] | None = None) -> list[str]:
    """시트(카테고리)별 엑셀 컬럼 목록 — 등록/수정 폼·목록에 사용."""
    schemas = _load_schemas()
    fields: list[str] = list(schemas.get(category, []))
    seen = set(fields)

    if equipment:
        from collections import Counter

        freq: Counter[str] = Counter()
        for eq in equipment:
            for k in (eq.extra_data or {}):
                if not is_noise_key(k):
                    freq[k] += 1
        for k, _ in freq.most_common():
            if k not in seen:
                fields.append(k)
                seen.add(k)

    if not fields:
        fields = ["구분", "제조사", "TYPE", "Serial No"]
    return fields


def list_display_fields(category: str, equipment: list[Equipment] | None = None, limit: int = 6) -> list[str]:
    """목록 테이블에 표시할 컬럼 (코드·관리 제외)."""
    fields = get_category_fields(category, equipment)
    # 코드/명칭 중복 최소화 — 구분류 우선
    preferred = []
    for k in fields:
        if k in ("구분", "구 분", "명칭", "TYPE", "제조사", "Serial No"):
            preferred.append(k)
    for k in fields:
        if k not in preferred:
            preferred.append(k)
    return preferred[:limit]


def field_value(eq: Equipment, field: str) -> str:
    """설비의 시트 컬럼 값."""
    extra = eq.extra_data or {}
    if field in extra and str(extra[field]).strip():
        return str(extra[field]).strip()
    mapping = {
        "구분": eq.name,
        "구 분": eq.name,
        "구  분": eq.name,
        "명칭": eq.name,
        "제조사": eq.manufacturer,
        "제조사/년": eq.manufacturer,
        "MAKER": eq.manufacturer,
        "TYPE": eq.model,
        "형식": eq.model,
        "Serial No": eq.serial_no,
        "Serial No.": eq.serial_no,
    }
    val = mapping.get(field)
    return str(val).strip() if val else ""


def parse_extra_form(form: Any) -> dict[str, str]:
    """폼에서 extra__ 접두사 필드 추출."""
    extra: dict[str, str] = {}
    for key, val in form.items():
        if not str(key).startswith("extra__"):
            continue
        field = str(key)[7:]
        text = str(val).strip() if val is not None else ""
        if text:
            extra[field] = text
    return extra


def resolve_core_fields(extra: dict[str, str], fallback_name: str = "") -> tuple[str, str, str, str]:
    """extra_data에서 명칭·제조사·모델·Serial 추출."""
    name = _pick(extra, NAME_KEYS) or fallback_name.strip()
    manufacturer = _pick(extra, MANUFACTURER_KEYS)
    model = _pick(extra, MODEL_KEYS)
    serial_no = _pick(extra, SERIAL_KEYS)
    return name, manufacturer, model, serial_no


def merge_extra_for_save(
    extra: dict[str, str],
    name: str,
    manufacturer: str | None,
    model: str | None,
    serial_no: str | None,
) -> dict[str, str]:
    """핵심 필드를 extra_data에도 반영."""
    merged = dict(extra)
    for k in NAME_KEYS:
        if k in merged or not name:
            continue
        if k in _load_schemas().get("", []) or k in ("구분", "명칭"):
            merged.setdefault(k, name)
    if manufacturer:
        merged.setdefault("제조사", manufacturer)
    if model:
        merged.setdefault("TYPE", model)
    if serial_no:
        merged.setdefault("Serial No", serial_no)
    return merged
