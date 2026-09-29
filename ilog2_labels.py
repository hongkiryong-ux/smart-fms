"""점검일지 양식 고정 문구(스키마 라벨) 사용자 수정.

각 일지 모듈의 load_schema()가 apply_label_overrides()를 거쳐 스키마를 돌려주므로,
화면·엑셀 출력 등 스키마를 쓰는 모든 곳에 수정 문구가 반영된다.
id 등 데이터 키는 건드리지 않아 저장된 입력값은 그대로 유지된다.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
from contextvars import ContextVar
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

# QR 1일 입력 화면 경로 첫 조각 → module key
QR_PREFIXES: dict[str, str] = {
    "hs": "housing_substation", "ccr": "central_control_room", "ccrf": "ccr_facility",
    "swhq": "steelworks_hq", "swhall": "steelworks_hall", "hcenter": "human_center",
    "egym": "eoulrim_gym", "bahall": "baegun_art_hall", "s53": "sub53",
    "bshop": "baegun_shopping", "bdae": "baegundae", "bdorm": "baegun_dorm",
    "gtown": "giga_town", "p1538": "park1538",
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
_items_cache: dict[str, tuple[int, list[dict]]] = {}
_marked_cache: dict[str, tuple[int, int, dict]] = {}

# 화면 수정 모드: 문구를 사용자 영역 문자로 감싸 화면 JS가 어느 문구인지 알 수 있게 한다.
MARK_START, MARK_MID, MARK_END = "\ue000", "\ue001", "\ue002"
_mark_module: ContextVar[str | None] = ContextVar("ilog2_mark_module", default=None)


def module_for_segment(segment: str) -> str | None:
    for key, (_fname, seg) in MODULES.items():
        if seg == segment:
            return key
    return None


def module_for_qr_prefix(prefix: str) -> str | None:
    return QR_PREFIXES.get(prefix)


def set_mark_module(module_key: str | None):
    return _mark_module.set(module_key)


def reset_mark_module(token) -> None:
    _mark_module.reset(token)


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


def _with_overrides(module_key: str, schema: dict) -> dict:
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


def _with_marks(module_key: str, schema: dict) -> dict:
    cached = _marked_cache.get(module_key)
    if cached and cached[0] == id(schema) and cached[1] == _version:
        return cached[2]
    out = deepcopy(schema)
    for idx, it in enumerate(collect_items(module_key)):
        try:
            kind, path, key, _orig = json.loads(it["id"])
        except (ValueError, TypeError):
            continue
        cur = it["current"]
        wrapped = f"{MARK_START}{idx}{MARK_MID}{cur}{MARK_END}"
        target = _resolve(out, path)
        if kind == "p" and isinstance(target, dict) and target.get(key) == cur:
            target[key] = wrapped
        elif kind == "g" and isinstance(target, list):
            for row in target:
                if isinstance(row, dict) and row.get(key) == cur:
                    row[key] = wrapped
    _marked_cache[module_key] = (id(schema), _version, out)
    return out


def apply_label_overrides(module_key: str, schema: dict) -> dict:
    if not isinstance(schema, dict):
        return schema
    applied = _with_overrides(module_key, schema)
    if _mark_module.get() == module_key:
        return _with_marks(module_key, applied)
    return applied


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
    cached = _items_cache.get(module_key)
    if cached and cached[0] == _version:
        return [dict(it) for it in cached[1]]
    items = _collect_items(module_key)
    _items_cache[module_key] = (_version, items)
    return [dict(it) for it in items]


def _collect_items(module_key: str) -> list[dict]:
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
    _marked_cache.pop(module_key, None)
    _items_cache.pop(module_key, None)
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

    rows = (
        await db.execute(select(AppSetting).where(AppSetting.key.like(f"{TPL_SETTING_PREFIX}%")))
    ).scalars().all()
    for row in rows:
        mk = row.key[len(TPL_SETTING_PREFIX):]
        if mk not in MODULES:
            continue
        try:
            data = json.loads(row.value or "{}")
        except ValueError:
            continue
        if isinstance(data, dict):
            _tpl_set_memory(mk, _clean_tpl_data(data))


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


# ── 화면 고정 문구 (템플릿에 직접 쓰인 글씨) ──────────────────────────────
# 일지 화면 HTML을 내보낼 때 content 영역의 글씨 조각마다
# "문구 해시 + 같은 문구 중 몇 번째"로 키를 매겨 수정값을 바꿔 끼운다.
# 스키마 문구는 화면에서 항상 표식으로 감싸 두어 이 키 계산에서 빠지게 한다.
TPL_SETTING_PREFIX = "ilog2.tpl."
TPL_START = "<!--ilbl-tpl-->"
TPL_END = "<!--/ilbl-tpl-->"
TPL_MAX_LEN = 300

_tpl_overrides: dict[str, dict[str, list[str]]] = {}

_TOKEN_RE = re.compile(
    r"<!--.*?-->|<(script|style|textarea|title|button|select)\b[^>]*>.*?</\1\s*>|<[^>]*>|[^<]+",
    re.S | re.I,
)
_SCHEMA_OPEN_RE = re.compile("\ue000[^\ue001\ue002]*\ue001")
_HAS_WORD_RE = re.compile(r"[A-Za-z\u3131-\u318e\uac00-\ud7a3]")
_TPL_KEY_RE = re.compile(r"t[0-9a-f]{10}_\d{1,4}")


def _norm_text(raw_html_text: str) -> str:
    return " ".join(_html.unescape(raw_html_text).split())


def tpl_key(text: str, occurrence: int) -> str:
    return f"t{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}_{occurrence}"


def is_tpl_key(key: str) -> bool:
    return bool(_TPL_KEY_RE.fullmatch(key or ""))


def tpl_key_matches(key: str, original: str) -> bool:
    if not is_tpl_key(key):
        return False
    occ = int(key.rsplit("_", 1)[1])
    return tpl_key(_norm_text(original), occ) == key


def strip_marks(html_text: str) -> str:
    return _SCHEMA_OPEN_RE.sub("", html_text).replace(MARK_END, "")


def _tpl_value_html(value: str) -> str:
    return _html.escape(value, quote=False).replace("\r\n", "\n").replace("\n", "<br />")


def _process_region(region: str, module_key: str, edit: bool) -> str:
    ov = _tpl_overrides.get(module_key) or {}
    counts: dict[str, int] = {}
    originals: dict[str, str] = {}
    out: list[str] = []
    in_schema = False

    def plain(raw: str) -> str:
        core = raw.strip()
        if not core or not _HAS_WORD_RE.search(core) or core.startswith(("http://", "https://")):
            return raw
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        text = _norm_text(core)
        occ = counts.get(text, 0)
        counts[text] = occ + 1
        key = tpl_key(text, occ)
        saved = ov.get(key)
        shown = _tpl_value_html(saved[1]) if saved and saved[0] == text else core
        if edit:
            originals[key] = text
            shown = f"{MARK_START}{key}{MARK_MID}{shown}{MARK_END}"
        return lead + shown + trail

    for m in _TOKEN_RE.finditer(region):
        tok = m.group(0)
        if tok.startswith("<"):
            out.append(tok)
            continue
        pos = 0
        while pos < len(tok):
            if in_schema:
                j = tok.find(MARK_END, pos)
                if j < 0:
                    out.append(tok[pos:])
                    break
                out.append(tok[pos:j + 1])
                pos = j + 1
                in_schema = False
                continue
            mo = _SCHEMA_OPEN_RE.search(tok, pos)
            end = mo.start() if mo else len(tok)
            if end > pos:
                out.append(plain(tok[pos:end]))
            if not mo:
                break
            out.append(mo.group(0))
            pos = mo.end()
            in_schema = True

    body = "".join(out)
    if edit and originals:
        payload = json.dumps(originals).replace("</", "<\\/")
        body = f'<script type="application/json" id="ilbl-tpl-orig">{payload}</script>' + body
    return body


def render_page(html_text: str, module_key: str, edit: bool) -> str:
    """일지 화면 HTML에 화면 고정 문구 수정값을 반영하고, 수정 모드가 아니면 표식을 지운다."""
    start = html_text.find(TPL_START)
    if start >= 0:
        end = html_text.find(TPL_END, start)
        if end < 0:
            end = len(html_text)
        region = html_text[start + len(TPL_START):end]
        html_text = html_text[:start] + _process_region(region, module_key, edit) + html_text[end:]
    if not edit:
        html_text = strip_marks(html_text)
    return html_text


def _clean_tpl_data(data: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if is_tpl_key(str(k)) and isinstance(v, (list, tuple)) and len(v) == 2:
            out[str(k)] = [str(v[0]), str(v[1])]
    return out


def _tpl_set_memory(module_key: str, data: dict[str, list[str]]) -> None:
    if data:
        _tpl_overrides[module_key] = dict(data)
    else:
        _tpl_overrides.pop(module_key, None)


def current_tpl_overrides(module_key: str) -> dict[str, list[str]]:
    return {k: list(v) for k, v in (_tpl_overrides.get(module_key) or {}).items()}


async def save_tpl_overrides(db: AsyncSession, module_key: str, data: dict[str, list[str]]) -> None:
    from models import AppSetting

    key = f"{TPL_SETTING_PREFIX}{module_key}"
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
    _tpl_set_memory(module_key, data)
