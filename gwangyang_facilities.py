"""광양 주택지역 공공시설물 현황 — 엑셀 기준 UTF-8 데이터 로더."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "resources" / "gwangyang_public_facilities.json"

GWANGYANG_FACILITIES_PATH = "/admin/dashboard/gwangyang-facilities"


def is_gwangyang_ops_site(name: str | None) -> bool:
    n = (name or "").strip()
    return "광양운영" in n


@lru_cache(maxsize=1)
def load_gwangyang_facilities() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {
            "title": "광양 주택지역 공공시설물 현황",
            "as_of": "",
            "source": "",
            "headers": ["구분", "건물명", "준공일자", "취득액(천원)", "연면적(㎡)", "坪", "비고"],
            "left": [],
            "right": [],
        }
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def paired_rows(data: dict[str, Any]) -> list[tuple[dict | None, dict | None]]:
    left = data.get("left") or []
    right = data.get("right") or []
    n = max(len(left), len(right))
    out: list[tuple[dict | None, dict | None]] = []
    for i in range(n):
        L = left[i] if i < len(left) else None
        R = right[i] if i < len(right) else None
        out.append((L, R))
    return out
