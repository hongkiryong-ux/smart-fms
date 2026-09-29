"""점검일지 양식 고정 문구(스키마 라벨) 사용자 수정.

각 일지 모듈의 load_schema()가 apply_label_overrides()를 거쳐 스키마를 돌려주므로,
화면·엑셀 출력 등 스키마를 쓰는 모든 곳에 수정 문구가 반영된다.
id 등 데이터 키는 건드리지 않아 저장된 입력값은 그대로 유지된다.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent
SETTING_PREFIX = "ilog2.labels."

# module key → (스키마 파일, 일지 화면 경로 조각)
MODULES: dict[str, tuple[str, str]] = {
    "housing_substation": ("housing_substation_schema.json", "housing"),
    "central_control_room": ("central_control_room_schema.json", "central-control-room"),
    "ccr_facility": ("ccr_facility_schema.json", "ccr-facility"),
    "steelworks_hq": ("steelworks_hq_schema.json", "steelworks-hq"),
    "steelworks_hall": ("steelworks_hall_schema.json", "steelworks-hall"),
    "human_center": ("human_center_schema.json", "human-center"),
    "eoulrim_gym": ("eoulrim_gym_schema.json", "eoulrim-gym"),
    "baegun_art_hall": ("baegun_art_hall_schema.json", "baegun-art-hall"),
    "sub53": ("sub53_schema.json", "53-sub"),
    "baegun_shopping": ("baegun_shopping_schema.json", "baegun-shopping"),
    "baegundae": ("baegundae_schema.json", "baegundae"),
    "baegun_dorm": ("baegun_dorm_schema.json", "baegun-dorm"),
    "giga_town": ("giga_town_schema.json", "giga-town"),
    "park1538": ("park1538_schema.json", "park1538"),
}

# 행마다 개별 수정하는 표시 문구
TEXT_KEYS: tuple[str, ...] = (
    "title", "sheet_title", "subtitle", "label", "sub_label", "form_label",
    "sum_label", "item", "unit", "daily_unit", "std", "range", "note",
)
# 같은 목록에서 값이 같은 행끼리 셀 병합(rowspan)하므로 묶어서 한 번에 수정
GROUP_KEYS: tuple[str, ...] = ("group", "cat")

KEY_LABELS = {
    "title": "제목", "sheet_title": "시트 제목", "subtitle": "부제목", "label": "항목명",
    "sub_label": "하위 항목", "form_label": "양식 항목", "sum_label": "합계 항목",
    "item": "항목", "unit": "단위", "daily_unit": "단위", "std": "기준", "range": "범위",
    "note": "비고", "group": "구분(묶음)", "cat": "구분(묶음)",
}

_overrides: dict[str, dict[str, str]] = {}
_version = 0
_applied: dict[str, tuple[int, int, dict]] = {}


def _item_id(kind: str, path: list, key: str, original: str) -> str:
    return json.dumps([kind, path, key, original], ensure_ascii=False, separators=(",", ":"))


def _resolve(root: Any, path: list) -> Any:
    node = root
    for part in path:
        if isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            node = node[part]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _apply_one(schema: dict, item_id: str, value: str) -> None:
    try:
        kind, path, key, original = json.loads(item_id)
    except (ValueError, TypeError):
        return
    target = _resolve(schema, path)
    if kind == "p":
        if isinstance(target, dict) and target.get(key) == original:
            target[key] = value
    elif kind == "g" and isinstance(target, list):
        for row in target:
            if isinstance(row, dict) and row.get(key) == original:
                row[key] = value


def apply_label_overrides(module_key: str, schema: dict) -> dict:
    ov = _overrides.get(module_key)
    if not ov or not isinstance(schema, dict):
        return schema
    cached = _applied.get(module_key)
    if cached and cached[0] == id(schema) and cached[1] == _version:
        return cached[2]
    out = deepcopy(schema)
    for item_id, value in ov.items():
        _apply_one(out, item_id, value)
    _applied[module_key] = (id(schema), _version, out)
    return out


def load_base_schema(module_key: str) -> dict:
    fname = MODULES[module_key][0]
    return json.loads((ROOT / "resources" / fname).read_text(encoding="utf-8"))


def _context_name(node: dict) -> str:
    for k in ("title", "label", "cat", "group", "name", "item"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def collect_items(module_key: str) -> list[dict]:
    """수정 가능한 문구 목록 (기본 스키마 기준, 현재 수정값 포함)."""
    base = load_base_schema(module_key)
    ov = _overrides.get(module_key) or {}
    items: list[dict] = []
    seen_groups: set[str] = set()

    def section_of(path: list) -> str:
        if not path:
            return "기본 정보"
        top = base.get(path[0]) if isinstance(base, dict) else None
        if isinstance(top, dict):
            return _context_name(top) or str(path[0])
        return str(path[0])

    def context_of(path: list, trail: list[str]) -> str:
        section = section_of(path)
        return " › ".join(t for t in trail if t and t != section)

    def walk(node: Any, path: list, trail: list[str]) -> None:
        if isinstance(node, dict):
            here = _context_name(node) if path else ""
            for k, v in node.items():
                if isinstance(v, str) and k in TEXT_KEYS and v.strip():
                    iid = _item_id("p", path, k, v)
                    ctx_trail = list(trail)
                    if k not in ("title", "label", "item") and here:
                        ctx_trail.append(here)
                    items.append({
                        "id": iid, "section": section_of(path), "context": context_of(path, ctx_trail),
                        "key": k, "key_label": KEY_LABELS.get(k, k),
                        "original": v, "current": ov.get(iid, v), "changed": iid in ov,
                    })
            next_trail = trail + ([here] if here else [])
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, path + [k], next_trail)
        elif isinstance(node, list):
            for gk in GROUP_KEYS:
                for row in node:
                    if not isinstance(row, dict):
                        continue
                    gv = row.get(gk)
                    if not isinstance(gv, str) or not gv.strip():
                        continue
                    iid = _item_id("g", path, gk, gv)
                    if iid in seen_groups:
                        continue
                    seen_groups.add(iid)
                    items.append({
                        "id": iid, "section": section_of(path),
                        "context": context_of(path, trail),
                        "key": gk, "key_label": KEY_LABELS.get(gk, gk),
                        "original": gv, "current": ov.get(iid, gv), "changed": iid in ov,
                    })
            for idx, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    walk(v, path + [idx], trail)

    walk(base, [], [])
    return items


def current_overrides(module_key: str) -> dict[str, str]:
    return dict(_overrides.get(module_key) or {})


def _set_memory(module_key: str, data: dict[str, str]) -> None:
    global _version
    if data:
        _overrides[module_key] = dict(data)
    else:
        _overrides.pop(module_key, None)
    _applied.pop(module_key, None)
    _version += 1


async def load_all(db: AsyncSession) -> None:
    from models import AppSetting

    rows = (
        await db.execute(select(AppSetting).where(AppSetting.key.like(f"{SETTING_PREFIX}%")))
    ).scalars().all()
    for row in rows:
        mk = row.key[len(SETTING_PREFIX):]
        if mk not in MODULES:
            continue
        try:
            data = json.loads(row.value or "{}")
        except ValueError:
            continue
        if isinstance(data, dict):
            _set_memory(mk, {str(k): str(v) for k, v in data.items()})


async def save_overrides(db: AsyncSession, module_key: str, data: dict[str, str]) -> None:
    from models import AppSetting

    key = f"{SETTING_PREFIX}{module_key}"
    row = await db.get(AppSetting, key)
    if data:
        payload = json.dumps(data, ensure_ascii=False)
        if row is None:
            db.add(AppSetting(key=key, value=payload))
        else:
            row.value = payload
    elif row is not None:
        await db.delete(row)
    await db.commit()
    _set_memory(module_key, data)
