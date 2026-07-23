"""유해위험요인 파악표 기반 법적근거 시나리오 매칭 (law.go.kr 검증 조문)

hazard / injury / unit_task 텍스트를 시나리오 패턴과 대조하여
가장 적합한 산업안전보건기준에 관한 규칙 조항을 반환합니다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.law_lookup import LAWS, LAW_DEFAULT
from app.law_catalog import normalize_law
from app.runtime_paths import DATA_DIR

SCENARIOS_PATH = DATA_DIR / "hazard_law_scenarios.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _match_patterns(text: str, patterns: list[str]) -> bool:
    if not text or not patterns:
        return False
    return any(re.search(p, text, re.I) for p in patterns)


@lru_cache(maxsize=1)
def _load_scenarios() -> list[dict[str, Any]]:
    if not SCENARIOS_PATH.exists():
        return []
    try:
        data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("scenarios", [])


def match_scenario_law(
    hazard: str = "",
    injury: str = "",
    unit_task: str = "",
    *,
    min_priority: int = 78,
) -> tuple[str, str, str] | None:
    """시나리오 매칭 — (law, url, scenario_id) 또는 None"""
    haz = _norm(hazard)
    inj = _norm(injury)
    unit = _norm(unit_task)
    combined = f"{haz} {inj} {unit}".strip()
    if not combined:
        return None

    best_score = 0
    best_key = ""
    best_id = ""

    for sc in _load_scenarios():
        priority = int(sc.get("priority", 70))
        if priority < min_priority:
            continue

        score = 0
        patterns = sc.get("patterns") or []
        injury_patterns = sc.get("injury_patterns") or []
        unit_patterns = sc.get("unit_patterns") or []

        if patterns and _match_patterns(combined, patterns):
            score += priority
            if _match_patterns(haz, patterns):
                score += 8
        if injury_patterns and inj and _match_patterns(inj, injury_patterns):
            score += priority // 2 + 6
        if unit_patterns and unit and _match_patterns(unit, unit_patterns):
            score += 12

        if score > best_score:
            law_key = sc.get("law_key", "")
            if law_key in LAWS:
                best_score = score
                best_key = law_key
                best_id = sc.get("id", "")

    if best_score < min_priority or not best_key:
        return None

    law, url = LAWS[best_key]
    law, url = normalize_law(law, url)
    return law, url, best_id


def scenario_cache_entries() -> dict[str, dict]:
    """build_law_index — 시나리오 situation을 query_cache 시드로 변환"""
    out: dict[str, dict] = {}
    for sc in _load_scenarios():
        situation = (sc.get("situation") or "").strip()
        law_key = sc.get("law_key", "")
        if not situation or law_key not in LAWS:
            continue
        law, url = LAWS[law_key]
        key = f"{situation[:80]}||"
        out[key] = {"law": law, "url": url, "score": sc.get("priority", 80), "source": sc.get("id", "")}
    return out
