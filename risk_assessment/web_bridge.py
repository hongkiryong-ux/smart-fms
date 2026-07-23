# risk_assessment/web_bridge.py
"""원본 P-WIDE app 엔진을 FastAPI 웹에서 쓰기 위한 브리지."""
from __future__ import annotations

import os
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

# Render/서버에서는 법령 웹검색 비활성 (로컬 인덱스·규칙만)
os.environ.setdefault("LAW_WEB_SEARCH", "0")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@lru_cache(maxsize=1)
def _lookup():
    from app.work_type_lookup import WorkTypeLookup

    return WorkTypeLookup()


@lru_cache(maxsize=1)
def _engine():
    from app.local_engine import LocalAssessmentEngine

    return LocalAssessmentEngine()


def list_majors() -> list[dict[str, str]]:
    lu = _lookup()
    return [{"id": m.get("id", ""), "name": m.get("name", "")} for m in lu.major_categories]


def list_presets(major_name: str = "") -> list[dict[str, Any]]:
    lu = _lookup()
    items = lu.list_presets(major_name or None)
    out = []
    for p in items:
        out.append(
            {
                "id": p.get("id") or p.get("name"),
                "name": p.get("name") or "",
                "major_category": p.get("major_category") or "",
                "sub_category": p.get("sub_category") or "",
                "description": p.get("description") or "",
                "source": p.get("source") or "",
                "five_m_one_e": p.get("five_m_one_e") or {},
            }
        )
    return out


def get_preset(name: str = "", preset_id: str = "") -> dict | None:
    lu = _lookup()
    if name:
        p = lu.get_by_name(name.strip())
        if p:
            return p
    if preset_id:
        for p in lu.presets:
            if str(p.get("id")) == str(preset_id) or p.get("name") == preset_id:
                return p
    return None


def rows_to_dict(rows) -> list[dict]:
    from app.local_engine import risk_grade

    out = []
    for r in rows:
        gb, lb, _ = risk_grade(r.score_before)
        ga, la, _ = risk_grade(r.score_after)
        out.append(
            {
                "work_class": r.work_class,
                "phase": r.phase,
                "unit_task": r.unit_task,
                "hazard": r.hazard,
                "injury": r.injury,
                "current": r.current,
                "freq_before": r.freq_before,
                "sev_before": r.sev_before,
                "score_before": r.score_before,
                "grade_before": gb,
                "grade_before_label": lb,
                "improvements": r.improvements,
                "law": r.law,
                "law_url": r.law_url,
                "freq_after": r.freq_after,
                "sev_after": r.sev_after,
                "score_after": r.score_after,
                "grade_after": ga,
                "grade_after_label": la,
                "source": r.source or "",
            }
        )
    return out


def form_rows_to_dict(form_rows) -> list[dict]:
    out = []
    prev = ""
    for r in form_rows:
        seq = r.work_sequence if r.work_sequence != prev else "〃"
        prev = r.work_sequence
        out.append(
            {
                "work_sequence": seq,
                "work_sequence_raw": r.work_sequence,
                "work_process": r.work_process,
                "disaster_type": r.disaster_type,
                "hazard_factor": r.hazard_factor,
                "f_before": r.f_before,
                "s_before": r.s_before,
                "r_before": r.r_before,
                "measures_before": r.measures_before,
                "result_before": r.result_before(),
                "f_after": r.f_after,
                "s_after": r.s_after,
                "r_after": r.r_after,
                "measures_after": r.measures_after,
                "result_after": r.result_after(),
                "law": r.law,
                "law_url": r.law_url,
            }
        )
    return out


def build_report_text(job_name: str, rows, meta: dict | None = None) -> str:
    from app.report_exporter import AssessmentBundle, ReportMeta, format_report_text

    m = meta or {}
    bundle = AssessmentBundle(
        meta=ReportMeta(
            job_name=job_name,
            department=m.get("department") or "",
            section=m.get("section") or "",
            evaluator=m.get("evaluator") or "",
            assessment_no=m.get("assessment_no") or "",
            apply_type=m.get("apply_type") or "정기평가",
            ai_name=m.get("ai_name") or "P-WIDE V3 Web Local",
        ),
        rows=rows,
        mode=m.get("mode") or "local",
    )
    return format_report_text(bundle)


def assess(
    work_name: str,
    five_m: dict[str, str],
    *,
    use_ai: bool = False,
    major_name: str = "",
    meta: dict | None = None,
) -> dict[str, Any]:
    """원본 LocalAssessmentEngine(+선택 AI)으로 평가 후 웹용 dict 반환."""
    from app.risk_form import convert_to_form_rows

    job = (work_name or "").strip() or "일반 작업"
    mode = "local"
    rows = None
    error = ""

    if use_ai and os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            rows = _assess_ai(job, five_m, major_name)
            if rows:
                mode = "ai"
        except Exception as e:
            error = f"AI 평가 실패 → 로컬 모드로 전환: {e}"
            rows = None

    if not rows:
        rows = _engine().build_rows(job, five_m or {})
        mode = "local" if mode != "ai" else mode

    form = convert_to_form_rows(rows, job)
    report = build_report_text(job, rows, {**(meta or {}), "mode": mode, "ai_name": (
        "P-WIDE V3 Web AI" if mode == "ai" else "P-WIDE V3 Web Local"
    )})

    return {
        "work_name": job,
        "mode": mode,
        "mode_label": "AI 작성" if mode == "ai" else "로컬 전용",
        "rows": rows_to_dict(rows),
        "form_rows": form_rows_to_dict(form),
        "report_text": report,
        "error": error,
        "row_count": len(rows),
    }


def _assess_ai(job: str, five_m: dict[str, str], major_name: str):
    """OpenAI API 키만 사용하는 경량 AI 경로 (웹 로그인/Playwright 제외)."""
    import json
    import urllib.request

    from app.ai_assessment import finalize_ai_rows, load_ai_structured_prompt, parse_ai_rows
    from app.prompts import build_assessment_user_message

    api_key = os.environ["OPENAI_API_KEY"].strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    system = load_ai_structured_prompt()
    try:
        user_msg = build_assessment_user_message(job, five_m, compact=False)
    except Exception:
        user_msg = f"작업명: {job}\n5M1E: {five_m}"

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = data["choices"][0]["message"]["content"]
    rows = parse_ai_rows(raw)
    return finalize_ai_rows(rows, job, ai=None, on_status=None)


def run_additional(command_num: int, job: str, five_m: dict, report_text: str = "", major_name: str = "", user_question: str = "") -> str:
    """추가 명령 1~7 (네트워크 검색 포함 — 시간 소요 가능)."""
    from app.additional_commands import run_additional_command

    return run_additional_command(
        command_num,
        job,
        None,
        five_m or {},
        report_text or "",
        major_name=major_name or "",
        user_question=user_question or "",
    )
