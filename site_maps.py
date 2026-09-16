# site_maps.py
"""사업장 지도 — 이미지·핫스팟 로드/저장 (AppSetting)."""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting

GY_OP_SITE_CODES = frozenset({"GY-OP"})
GY_OP_SITE_NAMES = frozenset({"광양운영그룹"})

GY_OP_MAP_IMAGE = "/static/maps/gwangyang_op_group.jpg?v=20260916housing"
GY_OP_MAP_TITLE = "광양운영그룹 안내도"

DEFAULT_GY_OP_HOTSPOTS = [
    {"label": "러닝센타", "names": ["러닝센타", "러닝센터"], "x": 22.0, "y": 78.5, "w": 7.5, "h": 3.2},
    {"label": "기술교육센터", "names": ["기술교육센터"], "x": 27.5, "y": 36.5, "w": 8.5, "h": 3.2},
    {"label": "금당어린이집", "names": ["금당어린이집", "금호어린이집"], "x": 42.5, "y": 33.5, "w": 7.5, "h": 3.0},
    {"label": "어울림체육관", "names": ["어울림체육관"], "x": 40.5, "y": 45.5, "w": 8.0, "h": 3.2},
    {"label": "축구전용구장", "names": ["축구전용구장", "축구장"], "x": 36.0, "y": 52.0, "w": 7.5, "h": 3.0},
    {"label": "백운아트홀", "names": ["백운아트홀"], "x": 51.5, "y": 38.0, "w": 7.0, "h": 3.0},
    {"label": "복지센터", "names": ["복지센터"], "x": 54.0, "y": 48.5, "w": 6.5, "h": 3.0},
    {"label": "휴먼센터", "names": ["휴먼센터"], "x": 57.5, "y": 43.5, "w": 6.5, "h": 3.0},
    {"label": "백운쇼핑센터", "names": ["백운쇼핑센터"], "x": 60.5, "y": 51.0, "w": 8.0, "h": 3.2},
    {"label": "백운플라자", "names": ["백운플라자"], "x": 58.5, "y": 56.5, "w": 7.0, "h": 3.0},
    {"label": "제철회관", "names": ["제철회관"], "x": 55.5, "y": 61.5, "w": 6.5, "h": 3.0},
    {"label": "중앙관제실", "names": ["중앙관제실"], "x": 69.5, "y": 74.0, "w": 7.5, "h": 3.2},
    {"label": "기술연구원", "names": ["기술연구원"], "x": 76.0, "y": 81.0, "w": 7.5, "h": 3.2},
    {"label": "제철소본부", "names": ["제철소본부"], "x": 84.0, "y": 88.0, "w": 8.0, "h": 3.4},
    {"label": "주택변전소", "names": ["주택변전소"], "x": 63.5, "y": 58.0, "w": 7.0, "h": 3.0},
    {"label": "백운생활관1,2동", "names": ["백운생활관1,2동", "백운생활관"], "x": 66.0, "y": 35.5, "w": 9.0, "h": 3.2},
    {"label": "백운생활관3,4동", "names": ["백운생활관3,4동"], "x": 69.5, "y": 39.0, "w": 9.0, "h": 3.0},
    {"label": "백운생활관5,6동", "names": ["백운생활관5,6동"], "x": 73.0, "y": 42.5, "w": 9.0, "h": 3.0},
    {"label": "임원숙소", "names": ["임원숙소", "금호어버이집"], "x": 46.5, "y": 29.5, "w": 8.5, "h": 3.0},
    {"label": "백운대", "names": ["백운대"], "x": 79.0, "y": 16.5, "w": 5.5, "h": 3.0},
    {"label": "2서브", "names": ["2서브"], "x": 73.5, "y": 47.5, "w": 3.2, "h": 3.2},
    {"label": "3서브", "names": ["3서브"], "x": 76.5, "y": 51.0, "w": 3.2, "h": 3.2},
    {"label": "5서브", "names": ["5서브"], "x": 79.5, "y": 45.0, "w": 3.2, "h": 3.2},
    {"label": "6서브", "names": ["6서브"], "x": 82.5, "y": 49.0, "w": 3.2, "h": 3.2},
    {"label": "7서브", "names": ["7서브"], "x": 85.5, "y": 53.0, "w": 3.2, "h": 3.2},
    {"label": "8서브", "names": ["8서브", "백운그린랜드"], "x": 88.5, "y": 42.5, "w": 3.2, "h": 3.2},
    {"label": "12서브", "names": ["12서브"], "x": 78.0, "y": 56.5, "w": 3.2, "h": 3.2},
    {"label": "16서브", "names": ["16서브"], "x": 81.5, "y": 59.5, "w": 3.2, "h": 3.2},
    {"label": "18서브", "names": ["18서브"], "x": 85.0, "y": 62.5, "w": 3.2, "h": 3.2},
    {"label": "51서브", "names": ["51서브"], "x": 30.5, "y": 64.0, "w": 3.2, "h": 3.2},
    {"label": "52서브", "names": ["52서브"], "x": 34.0, "y": 67.5, "w": 3.2, "h": 3.2},
    {"label": "53서브", "names": ["53서브"], "x": 37.5, "y": 71.0, "w": 3.2, "h": 3.2},
    {"label": "54서브", "names": ["54서브"], "x": 28.0, "y": 60.0, "w": 3.2, "h": 3.2},
    {"label": "55서브", "names": ["55서브"], "x": 32.0, "y": 58.0, "w": 3.2, "h": 3.2},
    {"label": "56서브", "names": ["56서브"], "x": 36.0, "y": 61.5, "w": 3.2, "h": 3.2},
    {"label": "57서브", "names": ["57서브"], "x": 26.0, "y": 55.5, "w": 3.2, "h": 3.2},
    {"label": "60서브", "names": ["60서브"], "x": 22.5, "y": 58.5, "w": 3.2, "h": 3.2},
    {"label": "금호빗물펌프장", "names": ["금호빗물펌프장"], "x": 18.5, "y": 72.0, "w": 9.0, "h": 3.2},
]

MAP_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAP_UPLOAD_EXTS = MAP_IMAGE_EXTS | {".pdf"}


def site_has_map(site: Any) -> bool:
    """모든 활성 사업장은 분할 지도 패널 지원."""
    return site is not None and getattr(site, "is_active", True)


def site_map_setting_key(site_id: int) -> str:
    return f"site_map.hotspots.{int(site_id)}"


def site_map_image_setting_key(site_id: int) -> str:
    return f"site_map.image.{int(site_id)}"


def site_map_upload_dir(site_id: int) -> Path:
    return Path("static") / "uploads" / "sites" / str(int(site_id))


def grid_columns(site_count: int) -> int:
    """사업장 수에 따른 분할 열 수 (2×2 기본, 증가 시 확장)."""
    n = max(0, int(site_count))
    if n <= 1:
        return 1
    if n <= 4:
        return 2
    return max(2, min(4, math.ceil(math.sqrt(n))))


def _is_gy_op(site: Any) -> bool:
    code = (getattr(site, "code", None) or "").strip()
    name = (getattr(site, "name", None) or "").strip()
    return code in GY_OP_SITE_CODES or name in GY_OP_SITE_NAMES


def _norm(s: str) -> str:
    return "".join((s or "").split()).casefold()


def _match_building(buildings: list, names: list[str]):
    norms = [_norm(n) for n in names if n]
    if not norms:
        return None
    for b in buildings:
        bn = _norm(getattr(b, "name", "") or "")
        if not bn:
            continue
        for n in norms:
            if bn == n or n in bn or bn in n:
                return b
    return None


def _clamp_pct(v: Any, default: float = 50.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, round(n, 2)))


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _active_buildings(site: Any, buildings: list | None = None) -> list:
    src = buildings if buildings is not None else getattr(site, "buildings", None) or []
    return [b for b in src if getattr(b, "is_active", True)]


def default_hotspots_for_buildings(site: Any, buildings: list) -> list[dict]:
    if _is_gy_op(site):
        templates = DEFAULT_GY_OP_HOTSPOTS
    else:
        templates = []
    out: list[dict] = []
    for h in templates:
        b = _match_building(buildings, list(h.get("names") or []) + [h.get("label") or ""])
        out.append(
            {
                "id": _new_id(),
                "label": (getattr(b, "name", None) if b else None) or h["label"],
                "building_id": getattr(b, "id", None) if b else None,
                "x": float(h["x"]),
                "y": float(h["y"]),
                "w": float(h.get("w", 7.0)),
                "h": float(h.get("h", 3.0)),
            }
        )
    return out


def normalize_hotspots(raw: list | None, buildings: list) -> list[dict]:
    by_id = {int(b.id): b for b in buildings if getattr(b, "id", None) is not None}
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        bid = item.get("building_id")
        try:
            bid_i = int(bid) if bid is not None and bid != "" else None
        except (TypeError, ValueError):
            bid_i = None
        b = by_id.get(bid_i) if bid_i is not None else None
        label = (item.get("label") or "").strip()
        if b and not label:
            label = b.name or ""
        if not label and not b:
            continue
        out.append(
            {
                "id": str(item.get("id") or _new_id()),
                "label": label or (b.name if b else "바로가기"),
                "building_id": bid_i,
                "building_name": b.name if b else None,
                "matched": b is not None,
                "x": _clamp_pct(item.get("x"), 50.0),
                "y": _clamp_pct(item.get("y"), 50.0),
                "w": _clamp_pct(item.get("w"), 7.0) or 7.0,
                "h": _clamp_pct(item.get("h"), 3.0) or 3.0,
            }
        )
    return out


async def get_site_map_image_url(db: AsyncSession, site: Any) -> str | None:
    if site is None or getattr(site, "id", None) is None:
        return None
    row = await db.get(AppSetting, site_map_image_setting_key(site.id))
    if row and (row.value or "").strip():
        return row.value.strip()
    if _is_gy_op(site):
        return GY_OP_MAP_IMAGE
    return None


async def save_site_map_image_url(db: AsyncSession, site_id: int, url: str) -> str:
    key = site_map_image_setting_key(site_id)
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=url))
    else:
        row.value = url
    await db.commit()
    return url


def _pdf_first_page_to_jpg(pdf_path: Path, jpg_path: Path, scale: float = 2.0) -> None:
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        if doc.page_count < 1:
            raise ValueError("PDF에 페이지가 없습니다.")
        page = doc[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        pix.save(str(jpg_path))
    finally:
        doc.close()


def store_site_map_upload(site_id: int, content: bytes, suffix: str) -> tuple[str, str]:
    """업로드 저장. PDF는 1페이지를 JPG로 변환해 반환 URL 생성."""
    upload_dir = site_map_upload_dir(site_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = suffix.lower()
    version = str(int(time.time()))

    if suffix == ".pdf":
        pdf_path = upload_dir / "map.pdf"
        jpg_path = upload_dir / "map.jpg"
        pdf_path.write_bytes(content)
        _pdf_first_page_to_jpg(pdf_path, jpg_path)
        return f"/static/uploads/sites/{site_id}/map.jpg?v={version}", "map.jpg"

    dest_name = f"map{suffix}"
    (upload_dir / dest_name).write_bytes(content)
    return f"/static/uploads/sites/{site_id}/{dest_name}?v={version}", dest_name


async def save_site_map_image_upload(
    db: AsyncSession, site_id: int, content: bytes, suffix: str
) -> str:
    url, _ = store_site_map_upload(site_id, content, suffix)
    return await save_site_map_image_url(db, site_id, url)


async def load_site_map_hotspots(db: AsyncSession, site: Any) -> list[dict]:
    buildings = _active_buildings(site)
    if site is None or getattr(site, "id", None) is None:
        return normalize_hotspots(default_hotspots_for_buildings(site, buildings), buildings)

    row = await db.get(AppSetting, site_map_setting_key(site.id))
    if row and (row.value or "").strip():
        try:
            data = json.loads(row.value)
            raw = data.get("hotspots") if isinstance(data, dict) else data
            if isinstance(raw, list):
                return normalize_hotspots(raw, buildings)
        except Exception:
            pass
    return normalize_hotspots(default_hotspots_for_buildings(site, buildings), buildings)


async def save_site_map_hotspots(
    db: AsyncSession, site_id: int, hotspots: list[dict]
) -> list[dict]:
    key = site_map_setting_key(site_id)
    payload = {
        "hotspots": [
            {
                "id": h.get("id") or _new_id(),
                "label": h.get("label") or "",
                "building_id": h.get("building_id"),
                "x": _clamp_pct(h.get("x"), 50.0),
                "y": _clamp_pct(h.get("y"), 50.0),
                "w": max(2.0, _clamp_pct(h.get("w"), 7.0)),
                "h": max(2.0, _clamp_pct(h.get("h"), 3.0)),
            }
            for h in hotspots
            if isinstance(h, dict)
        ]
    }
    raw = json.dumps(payload, ensure_ascii=False)
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=raw))
    else:
        row.value = raw
    await db.commit()
    return payload["hotspots"]


async def build_site_map_payload(db: AsyncSession, site: Any) -> dict | None:
    if not site_has_map(site):
        return None
    buildings = _active_buildings(site)
    hotspots = await load_site_map_hotspots(db, site)
    image = await get_site_map_image_url(db, site)
    building_options = [
        {"id": b.id, "name": b.name or f"건물#{b.id}"}
        for b in sorted(buildings, key=lambda x: (x.name or "").casefold())
    ]
    return {
        "image": image,
        "title": f"{getattr(site, 'name', '') or '사업장'} 안내도",
        "site_id": site.id,
        "site_name": getattr(site, "name", "") or "",
        "site_code": getattr(site, "code", "") or "",
        "hotspots": hotspots,
        "matched_count": sum(1 for h in hotspots if h.get("matched")),
        "buildings": building_options,
        "has_image": bool(image),
    }


async def build_site_grid_panel(db: AsyncSession, site: Any) -> dict | None:
    """분할 화면용 — 사진·사업장 정보만 (건물 핫스팟 제외)."""
    if not site_has_map(site):
        return None
    image = await get_site_map_image_url(db, site)
    sid = int(site.id)
    name = getattr(site, "name", "") or "사업장"
    return {
        "image": image,
        "title": name,
        "site_id": sid,
        "site_name": name,
        "site_code": getattr(site, "code", "") or "",
        "has_image": bool(image),
        "site_url": f"/admin/sites?site_id={sid}&view=map",
    }


async def build_sites_grid_payload(db: AsyncSession, sites: list) -> dict:
    panels = []
    for site in sites:
        panel = await build_site_grid_panel(db, site)
        if panel:
            panels.append(panel)
    cols = grid_columns(len(panels))
    return {"columns": cols, "panels": panels, "site_count": len(panels)}
