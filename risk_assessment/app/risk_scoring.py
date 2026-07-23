"""빈도·강도·위험도 산정 규칙

사망 가능 재해(추락·감전·밀폐 등): 강도 4 고정, 개선후에도 강도 유지·빈도만 하향.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.local_engine import RiskRow

# 사망·중대재해 가능 → 강도(4) 고정 대상
FATAL_RISK_KEYWORDS = (
    "추락", "떨어짐", "낙하", "실족", "고소", "비계", "사다리", "개구부",
    "감전", "전기", "활선", "누전", "충전", "분전", "판넬",
    "밀폐", "질식", "산소결핍", "산소부족", "맨홀", "탱크",
    "깔림", "협착", "끼임",
    "폭발", "파열",
    "화재", "연소", "인화",
    "중대재해", "사망",
)

STRONG_CONTROL_KEYWORDS = (
    "loto", "잠금", "표지", "안전대", "이중고리", "방호", "인터록", "난간",
    "발판", "낙하방지", "밀폐공간", "산소", "가스측정", "환기", "접지",
    "절연", "내전압", "추락방지", "개구부", "출입통제", "에너지차단",
)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def is_fatal_risk(hazard: str, injury: str = "", unit_task: str = "") -> bool:
    blob = f"{hazard} {injury} {unit_task}".lower()
    return any(k in blob for k in FATAL_RISK_KEYWORDS)


def _has_strong_controls(improvements: str) -> bool:
    imp = improvements.lower()
    return any(k in imp for k in STRONG_CONTROL_KEYWORDS)


def suggest_freq_after(freq_before: int, improvements: str) -> int:
    """개선후 빈도 — 강도는 유지하고 빈도만 낮춤."""
    fb = _clamp(freq_before, 1, 5)
    if not improvements.strip():
        return max(1, fb - 1)
    if _has_strong_controls(improvements):
        return max(1, fb - 2)
    return max(1, fb - 1)


def normalize_scores(
    freq_before: int,
    sev_before: int,
    freq_after: int,
    sev_after: int,
    hazard: str,
    injury: str = "",
    unit_task: str = "",
    improvements: str = "",
) -> tuple[int, int, int, int]:
    fb = _clamp(freq_before, 1, 5)
    sb = _clamp(sev_before, 1, 4)
    fa = _clamp(freq_after, 1, 5)
    sa = _clamp(sev_after, 1, 4)

    if is_fatal_risk(hazard, injury, unit_task):
        sb = 4
        sa = 4
        fa = suggest_freq_after(fb, improvements)
        if fa >= fb:
            fa = max(1, fb - 1)
        return fb, sb, fa, sa

    if fa * sa > fb * sb:
        fa = max(1, fb - 1)
        sa = sb
    elif sa < sb:
        fa = suggest_freq_after(fb, improvements) if improvements.strip() else fa
        sa = sb

    return fb, sb, fa, sa


def normalize_risk_row(row: RiskRow) -> RiskRow:
    fb, sb, fa, sa = normalize_scores(
        row.freq_before,
        row.sev_before,
        row.freq_after,
        row.sev_after,
        row.hazard,
        row.injury,
        row.unit_task,
        row.improvements,
    )
    return replace(row, freq_before=fb, sev_before=sb, freq_after=fa, sev_after=sa)


def normalize_risk_rows(rows: list[RiskRow]) -> list[RiskRow]:
    return [normalize_risk_row(r) for r in rows]
