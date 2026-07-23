"""AI 구조화 위험성평가 — 전문가 수준 내용·GPT 법적근거 매칭"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from app.field_content import (
    clean_improvement_text,
    ensure_work_phases,
    polish_hazard,
    polish_improvement,
)
from app.law_common import ARTICLE_RE, confidence, extract_keywords
from app.law_index import find_law_candidates
from app.law_lookup import infer_law
from app.law_lookup import LAW_DEFAULT
from app.local_engine import RiskRow, _sort_rows
from app.risk_scoring import normalize_risk_row, normalize_risk_rows, normalize_scores
from app.prompts import HAZARD_CHECKLIST, build_assessment_user_message
from app.runtime_paths import prompt_path
from app.work_standard_ref import build_work_standard_reference

STRUCTURED_PROMPT_PATH = prompt_path("condition1_ai_structured.txt")
LAW_MATCH_PROMPT_PATH = prompt_path("condition1_ai_law_match.txt")

LAW_RELEVANCE_THRESHOLD = 80
LAW_BATCH_SIZE = 6


def load_ai_structured_prompt() -> str:
    if STRUCTURED_PROMPT_PATH.exists():
        return STRUCTURED_PROMPT_PATH.read_text(encoding="utf-8")
    return "JSA 전문가. JSON rows만 출력."


def load_ai_law_match_prompt() -> str:
    if LAW_MATCH_PROMPT_PATH.exists():
        return LAW_MATCH_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "법적근거 매칭. relevance 80 미만이면 law 공란. "
        'JSON: {"matches":[{"index":0,"law":"","law_url":"","relevance":0}]}'
    )


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("AI 응답이 비어 있습니다.")

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI JSON 파싱 실패: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("AI 응답 형식이 올바르지 않습니다.")
    return data


def _row_from_dict(item: dict) -> RiskRow:
    if not isinstance(item, dict):
        raise ValueError("rows 항목 형식 오류")
    fb = _clamp_int(item.get("freq_before"), 1, 5, 3)
    sb = _clamp_int(item.get("sev_before"), 1, 4, 3)
    fa = _clamp_int(item.get("freq_after"), 1, 5, fb)
    sa = _clamp_int(item.get("sev_after"), 1, 4, sb)

    hazard = polish_hazard(str(item.get("hazard") or "").strip() or "유해·위험요인 미기재")
    injury = str(item.get("injury") or "").strip() or "기타"
    unit_task = str(item.get("unit_task") or "본작업").strip() or "본작업"
    current = str(item.get("current") or "").strip() or "현행 안전조치 확인 필요"
    improvements = polish_improvement(
        clean_improvement_text(str(item.get("improvements") or "").strip() or "추가 안전조치 필요")
    )

    fb, sb, fa, sa = normalize_scores(fb, sb, fa, sa, hazard, injury, unit_task, improvements)

    return RiskRow(
        work_class=str(item.get("work_class") or "운전작업").strip() or "운전작업",
        phase=str(item.get("phase") or "작업 중").strip() or "작업 중",
        unit_task=unit_task,
        hazard=hazard,
        injury=injury,
        current=current,
        freq_before=fb,
        sev_before=sb,
        improvements=improvements,
        law="",
        law_url="",
        freq_after=fa,
        sev_after=sa,
        source="AI",
    )


def parse_ai_rows(raw: str) -> list[RiskRow]:
    data = _extract_json(raw)
    items = data.get("rows")
    if not isinstance(items, list) or not items:
        raise ValueError("AI 응답에 rows 배열이 없습니다.")
    rows = [_row_from_dict(item) for item in items]
    if len(rows) < 5:
        raise ValueError(f"평가 항목이 너무 적습니다 ({len(rows)}건).")
    return rows


def _law_url_valid(url: str) -> bool:
    u = url.strip().lower()
    return u.startswith("https://www.law.go.kr/")


def _local_relevance(law: str, hazard: str, injury: str, improvement: str, unit_task: str) -> int:
    if not law.strip():
        return 0
    keywords = extract_keywords(hazard, injury, unit_task, improvement)
    score = confidence(law, keywords, hazard)
    return min(100, int(score * 2.5))


def _content_overlap(law: str, hazard: str, current: str, improvements: str) -> int:
    """법 조항명과 행 내용 키워드 겹침률 (0~100)."""
    m = ARTICLE_RE.search(law)
    if not m:
        return 0
    title = m.group(2)
    title_parts = re.findall(r"[\w가-힣]{2,}", title)
    if not title_parts:
        return 0
    blob = f"{hazard} {current} {improvements}"
    hits = sum(1 for p in title_parts if p in blob)
    return int(hits / len(title_parts) * 100)


def _collect_candidates(row: RiskRow) -> list[dict]:
    candidates = find_law_candidates(
        hazard=row.hazard,
        injury=row.injury,
        improvement=row.improvements,
        current=row.current,
        unit_task=row.unit_task,
        limit=8,
    )
    local_law, local_url = infer_law(
        row.hazard, row.injury, row.improvements, row.current, row.unit_task
    )
    if local_law:
        known = {c["law"] for c in candidates}
        if local_law not in known:
            candidates.insert(0, {"law": local_law, "url": local_url, "title": local_law, "score": 0})
    return candidates


def _build_law_match_message(batch: list[tuple[int, RiskRow]]) -> str:
    parts = ["아래 위험성평가 항목마다 법적근거를 매칭하세요.", ""]
    for idx, row in batch:
        parts.append(f"=== index {idx} ===")
        parts.append(f"단위작업: {row.unit_task}")
        parts.append(f"유해·위험요인: {row.hazard}")
        parts.append(f"재해형태: {row.injury}")
        parts.append(f"개선전(현재안전조치): {row.current}")
        parts.append(f"개선후(안전보건 대책): {row.improvements}")
        cands = _collect_candidates(row)
        if cands:
            parts.append("후보 법령:")
            for i, c in enumerate(cands[:8], 1):
                parts.append(f"  {i}. {c.get('law', '')} | {c.get('url', '')}")
        else:
            parts.append("후보 법령: (없음 — 내용과 80% 이상 일치하는 조항만 직접 제시, 없으면 공란)")
        parts.append("")
    parts.append(
        f"각 index에 대해 relevance가 {LAW_RELEVANCE_THRESHOLD} 미만이면 law·law_url을 공란으로 반환하세요."
    )
    return "\n".join(parts)


def _apply_law_match(row: RiskRow, law: str, url: str, relevance: int) -> RiskRow:
    law = (law or "").strip()
    url = (url or "").strip()
    if relevance < LAW_RELEVANCE_THRESHOLD or not law:
        return replace(row, law="", law_url="")

    if not _law_url_valid(url):
        overlap = _content_overlap(law, row.hazard, row.current, row.improvements)
        local_rel = _local_relevance(law, row.hazard, row.injury, row.improvements, row.unit_task)
        if max(overlap, local_rel) < LAW_RELEVANCE_THRESHOLD:
            return replace(row, law="", law_url="")
        cands = _collect_candidates(row)
        for c in cands:
            if c.get("law") == law or law in c.get("law", ""):
                url = c.get("url", url)
                break
        if not _law_url_valid(url):
            return replace(row, law="", law_url="")

    overlap = _content_overlap(law, row.hazard, row.current, row.improvements)
    local_rel = _local_relevance(law, row.hazard, row.injury, row.improvements, row.unit_task)
    if max(relevance, overlap, local_rel) < LAW_RELEVANCE_THRESHOLD:
        return replace(row, law="", law_url="")

    return replace(row, law=law, law_url=url)


def _parse_law_matches(raw: str, expected_indices: list[int]) -> dict[int, dict]:
    data = _extract_json(raw)
    matches = data.get("matches", [])
    result: dict[int, dict] = {}
    if not isinstance(matches, list):
        return result
    for item in matches:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx is None:
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        rel = item.get("relevance", 0)
        try:
            rel = int(rel)
        except (TypeError, ValueError):
            rel = 0
        result[idx] = {
            "law": str(item.get("law") or "").strip(),
            "law_url": str(item.get("law_url") or "").strip(),
            "relevance": rel,
        }
    for idx in expected_indices:
        result.setdefault(idx, {"law": "", "law_url": "", "relevance": 0})
    return result


def _fallback_law_match(row: RiskRow) -> RiskRow:
    """GPT 실패 시 로컬 후보 — 80% 이상일 때만."""
    cands = _collect_candidates(row)
    best_rel = 0
    best_law, best_url = "", ""
    for c in cands:
        law = c.get("law", "")
        url = c.get("url", "")
        rel = max(
            _local_relevance(law, row.hazard, row.injury, row.improvements, row.unit_task),
            _content_overlap(law, row.hazard, row.current, row.improvements),
        )
        if rel > best_rel:
            best_rel, best_law, best_url = rel, law, url
    return _apply_law_match(row, best_law, best_url, best_rel)


def match_laws_with_gpt(
    ai,
    rows: list[RiskRow],
    on_status: Optional[Callable[[str], None]] = None,
) -> list[RiskRow]:
    """GPT로 행별 법적근거 매칭 — 유사도 80% 미만은 공란."""
    out = list(rows)
    indexed = list(enumerate(rows))
    total_batches = (len(indexed) + LAW_BATCH_SIZE - 1) // LAW_BATCH_SIZE

    for batch_no, start in enumerate(range(0, len(indexed), LAW_BATCH_SIZE), 1):
        batch = indexed[start : start + LAW_BATCH_SIZE]
        if on_status:
            on_status(f"GPT 법적근거 검색·매칭 중… ({batch_no}/{total_batches})")

        msg = _build_law_match_message(batch)
        try:
            raw = ai.generate_json(msg, system_prompt=load_ai_law_match_prompt())
            matches = _parse_law_matches(raw, [idx for idx, _ in batch])
        except Exception:
            for idx, row in batch:
                out[idx] = _fallback_law_match(row)
            continue

        for idx, row in batch:
            m = matches.get(idx, {})
            out[idx] = _apply_law_match(
                row,
                m.get("law", ""),
                m.get("law_url", ""),
                m.get("relevance", 0),
            )

    return out


def finalize_ai_rows(
    rows: list[RiskRow],
    job: str,
    ai=None,
    on_status: Optional[Callable[[str], None]] = None,
) -> list[RiskRow]:
    # 요구사항: 모든 작업에 "안전관리용 CCTV설치" 관련 평가 행이 반드시 포함되도록 보강
    def _has_cctv(r: RiskRow) -> bool:
        blob = " ".join([r.unit_task or "", r.hazard or "", r.current or "", r.improvements or ""])
        b = blob.replace(" ", "").lower()
        return ("cctv" in b) or ("안전관리용cctv설치" in b)

    if rows and not any(_has_cctv(r) for r in rows):
        rows = list(rows) + [
            RiskRow(
                work_class="운전작업",
                phase="작업 중",
                unit_task="작업구역 안전관리·CCTV 모니터링",
                hazard="작업구역 사각지대 발생으로 위험행동·접근을 조기에 인지하지 못해 사고로 이어질 우려",
                injury="부딪힘, 끼임, 추락",
                current="현장 순찰 위주로 사각지대 통제·감시가 불충분함",
                freq_before=3,
                sev_before=3,
                improvements="안전관리용 CCTV설치 및 모니터링 체계 구축, 사각지대 표지·출입통제, 이상행동 즉시 경보·작업중지 절차 운영",
                law=LAW_DEFAULT[0],
                law_url=LAW_DEFAULT[1],
                freq_after=2,
                sev_after=2,
                source="CCTV-ALWAYS",
            )
        ]
    rows = ensure_work_phases(rows, job)
    rows = _sort_rows(rows)
    rows = normalize_risk_rows(rows)
    if ai is not None:
        rows = match_laws_with_gpt(ai, rows, on_status=on_status)
    return rows


def _build_rich_user_message(
    job: str,
    five_m: dict[str, str],
    safety_context: str,
    major_name: str = "",
) -> str:
    ref = build_work_standard_reference(job, five_m, major_name)
    msg = build_assessment_user_message(
        job,
        five_m,
        safety_context=safety_context,
        compact=False,
    )
    if ref:
        msg += f"\n\n{ref}"
    msg += (
        "\n\n【AI 작성 지침 — 전문가 수준】\n"
        "1. 시설물 유지관리 업체 작업표준(SOP) 순서에 맞게 unit_task·평가 행을 배열.\n"
        "2. 【참고 작업표준·JSA】가 있으면 그 순서·단계를 우선 따르고, 내용은 현장에 맞게 재작성.\n"
        "3. 유해·위험요인·개선전·개선후를 구체적으로 작성.\n"
        "4. 운전/정비/돌발 × 작업 전·중·후를 빠짐없이 포함.\n"
        "5. 아래 위험점을 각각 검토해 관련 항목에 반영:\n"
        f"   {', '.join(HAZARD_CHECKLIST)}\n"
        "6. JSON rows만 출력 (법적근거는 이 단계에서 작성하지 않음)."
    )
    return msg


def run_ai_assessment(
    ai,
    job: str,
    five_m: dict[str, str],
    safety_context: str = "",
    major_name: str = "",
    on_status: Optional[Callable[[str], None]] = None,
) -> list[RiskRow]:
    if on_status:
        on_status("작업표준·JSA 참고자료 반영 중…")

    user_msg = _build_rich_user_message(job, five_m, safety_context, major_name)

    if on_status:
        on_status("GPT 전문가 수준 위험성평가 작성 중…")
    raw = ai.generate_json(user_msg, system_prompt=load_ai_structured_prompt())

    if on_status:
        on_status("평가 내용 검증·양식 변환 중…")

    rows = parse_ai_rows(raw)
    return finalize_ai_rows(rows, job or "일반 작업", ai=ai, on_status=on_status)
