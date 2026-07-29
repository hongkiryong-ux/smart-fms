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


def dicts_to_risk_rows(row_dicts: list[dict] | None):
    from app.local_engine import RiskRow

    rows = []
    for d in row_dicts or []:
        rows.append(
            RiskRow(
                work_class=str(d.get("work_class") or ""),
                phase=str(d.get("phase") or ""),
                unit_task=str(d.get("unit_task") or ""),
                hazard=str(d.get("hazard") or ""),
                injury=str(d.get("injury") or ""),
                current=str(d.get("current") or ""),
                freq_before=int(d.get("freq_before") or 1),
                sev_before=int(d.get("sev_before") or 1),
                improvements=str(d.get("improvements") or ""),
                law=str(d.get("law") or ""),
                law_url=str(d.get("law_url") or ""),
                freq_after=int(d.get("freq_after") or 1),
                sev_after=int(d.get("sev_after") or 1),
                source=str(d.get("source") or ""),
            )
        )
    return rows


def _make_bundle(job_name: str, rows, meta: dict | None = None):
    from app.report_exporter import AssessmentBundle, ReportMeta

    m = meta or {}
    return AssessmentBundle(
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


def build_report_text(job_name: str, rows, meta: dict | None = None) -> str:
    from app.report_exporter import format_report_text

    return format_report_text(_make_bundle(job_name, rows, meta))


def export_excel_bytes(
    job_name: str,
    row_dicts: list[dict] | None,
    meta: dict | None = None,
) -> bytes:
    """원본 export_to_excel로 위험성평가서 .xlsx 바이트 생성."""
    from tempfile import TemporaryDirectory

    from app.report_exporter import export_to_excel

    rows = dicts_to_risk_rows(row_dicts)
    if not rows:
        raise ValueError("내보낼 평가 결과가 없습니다.")
    bundle = _make_bundle(job_name, rows, meta)
    with TemporaryDirectory() as td:
        path = Path(td) / "risk_assessment.xlsx"
        export_to_excel(bundle, path)
        return path.read_bytes()


def resolve_openai_credentials() -> tuple[str, str]:
    """OpenAI API 키·모델 (환경변수 → ai_settings.json)."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or "").strip()
    try:
        from app.ai_settings import AISettings
        from app.runtime_paths import ensure_runtime_dirs

        ensure_runtime_dirs()
        s = AISettings.load()
        if not key:
            key = (s.openai_api_key or "").strip()
        if not model:
            model = (s.openai_model or "").strip()
    except Exception:
        pass
    return key, (model or "gpt-4o-mini")


def ai_ready() -> bool:
    return bool(resolve_openai_credentials()[0])


def save_openai_settings(api_key: str, model: str = "") -> dict[str, Any]:
    """웹에서 입력한 OpenAI 키를 로컬 ai_settings.json(+프로세스 env)에 저장."""
    from app.ai_settings import AISettings
    from app.runtime_paths import ensure_runtime_dirs

    ensure_runtime_dirs()
    s = AISettings.load()
    key = (api_key or "").strip()
    if key:
        # 마스킹된 값이 다시 저장되지 않게
        if set(key) <= {"•", "*"} or key.endswith("…"):
            key = s.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        else:
            s.openai_api_key = key
            os.environ["OPENAI_API_KEY"] = key
    mdl = (model or "").strip()
    if mdl:
        s.openai_model = mdl
        os.environ["OPENAI_MODEL"] = mdl
    s.provider = "chatgpt_api"
    s.save()
    masked = ""
    k = (s.openai_api_key or "").strip()
    if k:
        masked = (k[:4] + "…" + k[-4:]) if len(k) > 8 else "****"
    return {"ok": bool(k), "masked_key": masked, "model": s.openai_model}


def ai_key_masked() -> str:
    key, _ = resolve_openai_credentials()
    if not key:
        return ""
    return (key[:4] + "…" + key[-4:]) if len(key) > 8 else "****"


def learn_documents(
    file_paths: list[str | Path],
    major_name: str,
    *,
    allow_update: bool = False,
) -> dict[str, Any]:
    """업로드 문서를 파싱해 소분류(user_presets)에 학습 등록."""
    from app.document_learner import parse_documents, registration_name_for_result

    if not major_name.strip():
        raise ValueError("대분류를 선택하세요.")
    paths = [Path(p) for p in file_paths if p]
    if not paths:
        raise ValueError("학습할 파일을 선택하세요.")

    results = parse_documents(paths, major_name.strip())
    lu = _lookup()
    registered: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in results:
        name = registration_name_for_result(item)
        try:
            lu.import_document_learn(
                name,
                item.five_m_one_e or {},
                major_name.strip(),
                rows=item.rows or [],
                source_file=item.source_path or "",
                sheet_title=item.sheet_title or "",
                allow_update=allow_update,
            )
            registered.append(
                {
                    "name": name,
                    "rows": item.row_count,
                    "warnings": list(item.warnings or []),
                }
            )
        except Exception as e:
            errors.append(f"{name}: {e}")

    _lookup.cache_clear()
    _engine.cache_clear()
    return {
        "parsed": len(results),
        "registered": registered,
        "errors": errors,
    }


def register_assessment_locally(
    work_name: str,
    five_m: dict[str, str],
    major_name: str,
    rows,
) -> str:
    """평가 결과를 로컬 소분류(user_presets)에 자동 등록. 메시지 반환."""
    try:
        lu = _lookup()
        preset = lu.learn_from_assessment(
            work_name,
            five_m or {},
            (major_name or "").strip(),
            rows=rows,
        )
        _lookup.cache_clear()
        _engine.cache_clear()
        if preset:
            return f"로컬 등록 완료: 『{preset.get('name') or work_name}』"
        return ""
    except Exception as e:
        return f"로컬 등록 실패: {e}"


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
    register_msg = ""

    api_key, _model = resolve_openai_credentials()
    if use_ai:
        if not api_key:
            error = "AI 키가 없습니다. 아래 ‘AI 키 설정’에 OpenAI API 키를 저장하세요."
        else:
            # 프로세스에 반영 (_assess_ai가 env를 읽음)
            os.environ["OPENAI_API_KEY"] = api_key
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

    # AI 작성 성공 시 로컬 소분류에 자동 등록 (다음 로컬 평가에 재사용)
    if mode == "ai" and rows:
        register_msg = register_assessment_locally(job, five_m or {}, major_name, rows)

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
        "register_msg": register_msg,
    }


def _assess_ai(job: str, five_m: dict[str, str], major_name: str):
    """OpenAI API 키만 사용하는 경량 AI 경로 (웹 로그인/Playwright 제외)."""
    import json
    import urllib.request

    from app.ai_assessment import finalize_ai_rows, load_ai_structured_prompt, parse_ai_rows
    from app.prompts import build_assessment_user_message

    api_key, model = resolve_openai_credentials()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")
    system = load_ai_structured_prompt()
    try:
        user_msg = build_assessment_user_message(job, five_m, compact=False)
    except Exception:
        user_msg = f"작업명: {job}\n5M1E: {five_m}"

    body = json.dumps(
        {
            "model": model or "gpt-4o-mini",
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
    with urllib.request.urlopen(req, timeout=120) as resp:
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
