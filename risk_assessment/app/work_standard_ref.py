"""작업표준·JSA 참고자료 — AI 위험성평가용 (시설물 유지관리 순서)"""

from __future__ import annotations

import re

from app.field_content import match_jsa_ppt_job
from app.prompts import format_five_m_one_e

PHASE_ORDER = {"작업 전": 0, "작업 중": 1, "작업 후": 2}

MAINTENANCE_WORKFLOW = """
【시설물 전문 유지관리 업체 — 작업표준·JSA 작성 순서 원칙】
1) 사전준비: 작업허가·TBM·보호구·작업구역 통제·도면·SOP 확인
2) 에너지차단: LOTO·전원차단·잔류압/잔류에너지 해제·표지 부착
3) 본작업: 작업표준(SOP) 단계 순서대로 수행 (설비점검·분해·교체·시험)
4) 시운전·기능점검: 복전 전 확인·시운전·측정·누설·진동·온도 확인
5) 마무리: 원상복구·5S·기록·차기점검 사항 이관
6) 비상: 이상 시 작업중지·보고·비상연락망
"""


def _context_text(job: str, five_m: dict[str, str]) -> str:
    return f"{job} {' '.join(v for v in five_m.values() if v)}"


def _format_jsa_sequence(job_key: str, items: list[dict]) -> str:
    """PPT JSA 라이브러리 — 작업 전→중→후, 단위작업 순서 유지."""
    if not items:
        return ""

    sorted_items = sorted(
        enumerate(items),
        key=lambda pair: (
            PHASE_ORDER.get(pair[1].get("phase", "작업 중"), 9),
            pair[0],
        ),
    )

    lines = [
        f"【참고 작업표준·JSA — 『{job_key}』 (사내 기존 양식)】",
        "아래 순서·단위작업·유해요인·대책을 참고해 동일한 작업 흐름으로 평가표를 작성하세요.",
        "",
    ]

    current_phase = ""
    step = 0
    for _, item in sorted_items:
        phase = item.get("phase", "작업 중")
        if phase != current_phase:
            current_phase = phase
            lines.append(f"■ {phase}")
        step += 1
        unit = item.get("unit_task", "본작업")
        hazard = item.get("hazard", "")
        imp = item.get("improvement", "")
        current = item.get("current", "")
        line = f"  {step}. [{unit}]"
        if hazard:
            line += f"\n     - 유해·위험: {hazard[:120]}"
        if current:
            line += f"\n     - 현행조치: {current[:100]}"
        if imp:
            line += f"\n     - 개선대책: {imp[:120]}"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


def _format_preset_reference(preset: dict) -> str:
    name = preset.get("name", "")
    sub = preset.get("sub_category", "")
    five_m = preset.get("five_m_one_e", {})
    lines = [
        f"【참고 소분류·5M1E — 『{name}』】",
        f"공종/섹션: {sub or '-'}",
        format_five_m_one_e(five_m),
        "",
    ]
    return "\n".join(lines)


def build_work_standard_reference(
    job: str,
    five_m: dict[str, str],
    major_name: str = "",
) -> str:
    """
    AI 평가용 참고 블록: 기존 JSA PPT·소분류 preset·유지관리 작업순서.
    """
    job = job.strip()
    if not job:
        return MAINTENANCE_WORKFLOW.strip()

    parts: list[str] = [MAINTENANCE_WORKFLOW.strip(), ""]

    # 1) JSA PPT / Datacenter 라이브러리 (가장 구체적)
    try:
        from app.field_content import _load_jsa_ppt_library

        ctx = _context_text(job, five_m)
        key = match_jsa_ppt_job(job, ctx)
        if key:
            items = _load_jsa_ppt_library().get(key, [])
            block = _format_jsa_sequence(key, items)
            if block:
                parts.append(block)
    except Exception:
        pass

    # 2) 소분류 preset (5M1E·작업 특성)
    try:
        from app.work_type_lookup import WorkTypeLookup

        lookup = WorkTypeLookup()
        preset = lookup.get_by_name(job)
        if not preset and major_name:
            match = lookup.best_match(job, major_name, min_score=40)
            if match:
                preset = match.preset
        if preset:
            parts.append(_format_preset_reference(preset))
    except Exception:
        pass

    # 3) 유사 작업명 검색 (약한 매칭)
    if not any("참고 작업표준" in p for p in parts):
        try:
            from app.field_content import _load_jsa_ppt_library

            library = _load_jsa_ppt_library()
            q_tokens = {t for t in re.findall(r"[\w가-힣]{2,}", job) if len(t) >= 2}
            best_key = None
            best = 0
            for key in library:
                k_tokens = {t for t in re.findall(r"[\w가-힣]{2,}", key) if len(t) >= 2}
                overlap = len(q_tokens & k_tokens)
                if overlap > best:
                    best = overlap
                    best_key = key
            if best_key and best >= 2:
                items = library.get(best_key, [])
                parts.append(
                    f"【유사 참고 JSA — 『{best_key}』 (키워드 유사, 순서 참고)】\n"
                    + _format_jsa_sequence(best_key, items)[:3500]
                )
        except Exception:
            pass

    parts.append(
        "【AI 작성 시 필수】\n"
        "- 위 참고 작업표준·JSA의 **단위작업 순서**를 유지하며 평가표 rows를 배열하세요.\n"
        "- 작업 전→작업 중→작업 후 순으로, 시설물 유지관리 SOP 흐름에 맞게 작성하세요.\n"
        "- 참고 문구를 그대로 복사하지 말고, 현재 작업명·5M1E에 맞게 전문가 수준으로 재작성하세요.\n"
        "- 운전작업(일상점검·운전)·정비작업(분해·교체·시험)·돌발대응(이상·비상)을 구분하세요."
    )

    return "\n".join(parts)
