# site_maps.py
"""사업장 지도 뷰 — 건물 핫스팟(클릭 → 건물 현황)."""
from __future__ import annotations

from typing import Any

# 광양운영그룹 안내도 (static/maps/gwangyang_op_group.jpg, 1024×893)
# x/y 는 이미지 기준 % (좌상단 원점). 기존 지도 라벨 위에 투명 클릭 영역을 둔다.
GY_OP_SITE_CODES = frozenset({"GY-OP"})
GY_OP_SITE_NAMES = frozenset({"광양운영그룹"})

GY_OP_MAP = {
    "image": "/static/maps/gwangyang_op_group.jpg",
    "title": "광양운영그룹 안내도",
    # w/h: 클릭 영역 크기(%)
    "hotspots": [
        # 연구·교육 (좌·중앙 상단)
        {"label": "러닝센타", "names": ["러닝센타", "러닝센터"], "x": 22.0, "y": 78.5, "w": 7.5, "h": 3.2},
        {"label": "기술교육센터", "names": ["기술교육센터"], "x": 27.5, "y": 36.5, "w": 8.5, "h": 3.2},
        {"label": "금당어린이집", "names": ["금당어린이집", "금호어린이집"], "x": 42.5, "y": 33.5, "w": 7.5, "h": 3.0},
        # 문화·체육·생활 (중앙)
        {"label": "어울림체육관", "names": ["어울림체육관"], "x": 40.5, "y": 45.5, "w": 8.0, "h": 3.2},
        {"label": "축구전용구장", "names": ["축구전용구장", "축구장"], "x": 36.0, "y": 52.0, "w": 7.5, "h": 3.0},
        {"label": "백운아트홀", "names": ["백운아트홀"], "x": 51.5, "y": 38.0, "w": 7.0, "h": 3.0},
        {"label": "복지센터", "names": ["복지센터"], "x": 54.0, "y": 48.5, "w": 6.5, "h": 3.0},
        {"label": "휴먼센터", "names": ["휴먼센터"], "x": 57.5, "y": 43.5, "w": 6.5, "h": 3.0},
        {"label": "백운쇼핑센터", "names": ["백운쇼핑센터"], "x": 60.5, "y": 51.0, "w": 8.0, "h": 3.2},
        {"label": "백운플라자", "names": ["백운플라자"], "x": 58.5, "y": 56.5, "w": 7.0, "h": 3.0},
        {"label": "제철회관", "names": ["제철회관"], "x": 55.5, "y": 61.5, "w": 6.5, "h": 3.0},
        # 행정·연구 (우하단)
        {"label": "중앙관제실", "names": ["중앙관제실"], "x": 69.5, "y": 74.0, "w": 7.5, "h": 3.2},
        {"label": "기술연구원", "names": ["기술연구원"], "x": 76.0, "y": 81.0, "w": 7.5, "h": 3.2},
        {"label": "제철소본부", "names": ["제철소본부"], "x": 84.0, "y": 88.0, "w": 8.0, "h": 3.4},
        {"label": "주택변전소", "names": ["주택변전소"], "x": 63.5, "y": 58.0, "w": 7.0, "h": 3.0},
        # 생활관·숙소·산
        {"label": "백운생활관1,2동", "names": ["백운생활관1,2동", "백운생활관"], "x": 66.0, "y": 35.5, "w": 9.0, "h": 3.2},
        {"label": "백운생활관3,4동", "names": ["백운생활관3,4동"], "x": 69.5, "y": 39.0, "w": 9.0, "h": 3.0},
        {"label": "백운생활관5,6동", "names": ["백운생활관5,6동"], "x": 73.0, "y": 42.5, "w": 9.0, "h": 3.0},
        {"label": "임원숙소", "names": ["임원숙소", "금호어버이집"], "x": 46.5, "y": 29.5, "w": 8.5, "h": 3.0},
        {"label": "백운대", "names": ["백운대"], "x": 79.0, "y": 16.5, "w": 5.5, "h": 3.0},
        # 서브(빨간 번호 마커) — 동쪽 1~18
        {"label": "2서브", "names": ["2서브"], "x": 73.5, "y": 47.5, "w": 3.2, "h": 3.2},
        {"label": "3서브", "names": ["3서브"], "x": 76.5, "y": 51.0, "w": 3.2, "h": 3.2},
        {"label": "5서브", "names": ["5서브"], "x": 79.5, "y": 45.0, "w": 3.2, "h": 3.2},
        {"label": "6서브", "names": ["6서브"], "x": 82.5, "y": 49.0, "w": 3.2, "h": 3.2},
        {"label": "7서브", "names": ["7서브"], "x": 85.5, "y": 53.0, "w": 3.2, "h": 3.2},
        {"label": "8서브", "names": ["8서브", "백운그린랜드"], "x": 88.5, "y": 42.5, "w": 3.2, "h": 3.2},
        {"label": "12서브", "names": ["12서브"], "x": 78.0, "y": 56.5, "w": 3.2, "h": 3.2},
        {"label": "16서브", "names": ["16서브"], "x": 81.5, "y": 59.5, "w": 3.2, "h": 3.2},
        {"label": "18서브", "names": ["18서브"], "x": 85.0, "y": 62.5, "w": 3.2, "h": 3.2},
        # 서브 — 서쪽 51~60
        {"label": "51서브", "names": ["51서브"], "x": 30.5, "y": 64.0, "w": 3.2, "h": 3.2},
        {"label": "52서브", "names": ["52서브"], "x": 34.0, "y": 67.5, "w": 3.2, "h": 3.2},
        {"label": "53서브", "names": ["53서브"], "x": 37.5, "y": 71.0, "w": 3.2, "h": 3.2},
        {"label": "54서브", "names": ["54서브"], "x": 28.0, "y": 60.0, "w": 3.2, "h": 3.2},
        {"label": "55서브", "names": ["55서브"], "x": 32.0, "y": 58.0, "w": 3.2, "h": 3.2},
        {"label": "56서브", "names": ["56서브"], "x": 36.0, "y": 61.5, "w": 3.2, "h": 3.2},
        {"label": "57서브", "names": ["57서브"], "x": 26.0, "y": 55.5, "w": 3.2, "h": 3.2},
        {"label": "60서브", "names": ["60서브"], "x": 22.5, "y": 58.5, "w": 3.2, "h": 3.2},
        {"label": "금호빗물펌프장", "names": ["금호빗물펌프장"], "x": 18.5, "y": 72.0, "w": 9.0, "h": 3.2},
    ],
}


def site_has_map(site: Any) -> bool:
    if site is None:
        return False
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


def build_site_map_payload(site: Any, buildings: list | None = None) -> dict | None:
    """선택 사업장용 지도 페이로드. 매칭된 핫스팟만 building_id 포함."""
    if not site_has_map(site):
        return None
    active = [
        b
        for b in (buildings if buildings is not None else getattr(site, "buildings", None) or [])
        if getattr(b, "is_active", True)
    ]
    hotspots = []
    for h in GY_OP_MAP["hotspots"]:
        b = _match_building(active, list(h.get("names") or []) + [h.get("label") or ""])
        item = {
            "label": h["label"],
            "x": h["x"],
            "y": h["y"],
            "w": h.get("w", 7.0),
            "h": h.get("h", 3.0),
            "building_id": getattr(b, "id", None) if b else None,
            "building_name": getattr(b, "name", None) if b else None,
            "matched": b is not None,
        }
        hotspots.append(item)
    return {
        "image": GY_OP_MAP["image"],
        "title": GY_OP_MAP["title"],
        "hotspots": hotspots,
        "matched_count": sum(1 for h in hotspots if h["matched"]),
    }
