# ai_analysis.py — FMS 자료 집계 답변 + (선택) OpenAI 세부 분석
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Building,
    Equipment,
    Floor,
    InspectionLogFile,
    MaintenanceRecord,
    Partner,
    PMInspection,
    PMResult,
    PMSchedule,
    Site,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)

try:
    from streetlamp.models import Lamp, MaintenanceRequest as LampRequest
except Exception:  # pragma: no cover
    Lamp = None
    LampRequest = None


def classify_intent(question: str) -> str:
    q = (question or "").strip().lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("equipment", ("설비", "장비", "자산", "equipment", "건물별 설비")),
        ("work_order", ("정비의뢰", "정비접수", "워크오더", "work order", "cmms", "고장수리")),
        ("pm", ("예방점검", "pm", "점검주기", "점검결과", "지연점검")),
        ("streetlamp", ("가로등", "lamp", "불점등", "시민")),
        ("d1", ("d-1", "d1", "작업허가", "협력사 작업", "시설섹션")),
        ("partner", ("협력사", "업체", "partner")),
        ("inspection_log", ("점검일지", "일지", "엑셀일지")),
        ("overview", ("전체", "현황", "요약", "대시보드", "몇", "얼마", "통계", "총")),
    ]
    scores: dict[str, int] = {}
    for intent, kws in rules:
        score = sum(1 for kw in kws if kw in q)
        if score:
            scores[intent] = score
    if not scores:
        return "overview"
    return max(scores, key=scores.get)


def _today() -> date:
    return date.today()


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


async def gather_context(db: AsyncSession, intent: str, question: str) -> dict[str, Any]:
    """질문 의도에 맞는 FMS 집계 컨텍스트 (전체 raw dump 금지)."""
    ctx: dict[str, Any] = {
        "intent": intent,
        "question": (question or "").strip(),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sections": {},
    }

    # 공통 개요 (항상 소량 포함)
    overview = {
        "sites": await _count(db, select(func.count(Site.id))),
        "buildings": await _count(db, select(func.count(Building.id))),
        "equipment_active": await _count(
            db, select(func.count(Equipment.id)).where(Equipment.is_active == True)  # noqa: E712
        ),
        "work_orders_active": await _count(
            db, select(func.count(WorkOrder.id)).where(WorkOrder.is_active == True)  # noqa: E712
        ),
        "pm_schedules": await _count(db, select(func.count(PMSchedule.id))),
        "partners": await _count(db, select(func.count(Partner.id))),
        "inspection_log_files": await _count(db, select(func.count(InspectionLogFile.id))),
    }
    if Lamp is not None:
        overview["lamps"] = await _count(db, select(func.count(Lamp.id)))
    if LampRequest is not None:
        overview["lamp_requests"] = await _count(db, select(func.count(LampRequest.id)))
    ctx["sections"]["overview"] = overview

    if intent in ("overview", "equipment"):
        # 건물별 설비 상위
        rows = (
            await db.execute(
                select(Building.name, func.count(Equipment.id))
                .select_from(Equipment)
                .join(Zone, Equipment.zone_id == Zone.id)
                .join(Floor, Zone.floor_id == Floor.id)
                .join(Building, Floor.building_id == Building.id)
                .where(Equipment.is_active == True)  # noqa: E712
                .group_by(Building.id, Building.name)
                .order_by(func.count(Equipment.id).desc())
                .limit(15)
            )
        ).all()
        cat_rows = (
            await db.execute(
                select(Equipment.category, func.count(Equipment.id))
                .where(Equipment.is_active == True)  # noqa: E712
                .group_by(Equipment.category)
                .order_by(func.count(Equipment.id).desc())
                .limit(12)
            )
        ).all()
        ctx["sections"]["equipment"] = {
            "by_building_top": [{"building": r[0], "count": int(r[1])} for r in rows],
            "by_category_top": [{"category": r[0] or "기타", "count": int(r[1])} for r in cat_rows],
        }

        # 질문 속 건물명 힌트
        bname = _extract_building_hint(question)
        if bname:
            matched = (
                await db.execute(
                    select(Building).where(Building.name.contains(bname)).limit(5)
                )
            ).scalars().all()
            detail = []
            for b in matched:
                n = await _count(
                    db,
                    select(func.count(Equipment.id))
                    .select_from(Equipment)
                    .join(Zone)
                    .join(Floor)
                    .where(Floor.building_id == b.id, Equipment.is_active == True),  # noqa: E712
                )
                detail.append({"id": b.id, "name": b.name, "equipment": n})
            ctx["sections"]["equipment"]["building_match"] = detail

    if intent in ("overview", "work_order", "d1"):
        status_rows = (
            await db.execute(
                select(WorkOrder.status, func.count(WorkOrder.id))
                .where(WorkOrder.is_active == True)  # noqa: E712
                .group_by(WorkOrder.status)
            )
        ).all()
        status_map = {}
        for st, n in status_rows:
            key = st.value if hasattr(st, "value") else str(st or "")
            status_map[key] = int(n)
        open_n = sum(
            status_map.get(k, 0)
            for k in ("received", "assigned", "in_progress")
        )
        recent = (
            await db.execute(
                select(WorkOrder)
                .where(WorkOrder.is_active == True)  # noqa: E712
                .order_by(WorkOrder.id.desc())
                .limit(8)
            )
        ).scalars().all()
        ctx["sections"]["work_orders"] = {
            "by_status": status_map,
            "open_count": open_n,
            "completed": status_map.get("completed", 0)
            + status_map.get("verified", 0)
            + status_map.get("closed", 0),
            "recent": [
                {
                    "id": wo.id,
                    "title": (wo.title or "")[:80],
                    "status": wo.status.value if hasattr(wo.status, "value") else str(wo.status),
                    "d1_approved": bool(getattr(wo, "d1_approved", False)),
                    "partner_id": wo.partner_id,
                }
                for wo in recent
            ],
        }

    if intent in ("overview", "pm"):
        today = _today()
        due_soon = await _count(
            db,
            select(func.count(PMSchedule.id)).where(
                PMSchedule.is_active == True,  # noqa: E712
                PMSchedule.next_due != None,  # noqa: E711
                PMSchedule.next_due <= today + timedelta(days=7),
            ),
        )
        overdue = await _count(
            db,
            select(func.count(PMSchedule.id)).where(
                PMSchedule.is_active == True,  # noqa: E712
                PMSchedule.next_due != None,  # noqa: E711
                PMSchedule.next_due < today,
            ),
        )
        result_rows = (
            await db.execute(
                select(PMInspection.result, func.count(PMInspection.id)).group_by(
                    PMInspection.result
                )
            )
        ).all()
        results = {}
        for r, n in result_rows:
            key = r.value if hasattr(r, "value") else str(r or "")
            results[key] = int(n)
        ctx["sections"]["pm"] = {
            "schedules": overview["pm_schedules"],
            "due_within_7_days": due_soon,
            "overdue": overdue,
            "inspection_results": results,
            "fault": results.get(PMResult.fault.value, results.get("fault", 0)),
            "caution": results.get(PMResult.caution.value, results.get("caution", 0)),
        }

    if intent in ("overview", "streetlamp") and Lamp is not None:
        lamp_sec: dict[str, Any] = {"lamps": overview.get("lamps", 0)}
        if LampRequest is not None:
            lamp_sec["requests_total"] = overview.get("lamp_requests", 0)
            # status 컬럼이 있으면 집계
            try:
                req_rows = (
                    await db.execute(
                        select(LampRequest.status, func.count(LampRequest.id)).group_by(
                            LampRequest.status
                        )
                    )
                ).all()
                lamp_sec["requests_by_status"] = {
                    (s.value if hasattr(s, "value") else str(s or "")): int(n)
                    for s, n in req_rows
                }
            except Exception:
                pass
            recent_req = (
                await db.execute(
                    select(LampRequest).order_by(LampRequest.id.desc()).limit(8)
                )
            ).scalars().all()
            lamp_sec["recent_requests"] = [
                {
                    "id": r.id,
                    "status": getattr(r, "status", None).value
                    if hasattr(getattr(r, "status", None), "value")
                    else str(getattr(r, "status", "") or ""),
                    "note": (
                        (getattr(r, "request_type", None).value
                         if hasattr(getattr(r, "request_type", None), "value")
                         else str(getattr(r, "request_type", "") or ""))
                        + " "
                        + (getattr(r, "content", None) or "")
                    )[:60],
                }
                for r in recent_req
            ]
        ctx["sections"]["streetlamp"] = lamp_sec

    if intent in ("overview", "partner", "d1"):
        partners = (
            await db.execute(select(Partner).order_by(Partner.name).limit(30))
        ).scalars().all()
        ctx["sections"]["partners"] = [
            {"id": p.id, "name": p.name, "code": getattr(p, "code", "") or ""}
            for p in partners
        ]

    if intent in ("overview", "inspection_log"):
        ctx["sections"]["inspection_logs"] = {
            "files": overview["inspection_log_files"],
        }

    return ctx


def _extract_building_hint(question: str) -> str | None:
    q = (question or "").strip()
    m = re.search(r"([가-힣A-Za-z0-9\-]+)\s*(건물|동|센터|관)", q)
    if m:
        return m.group(1)
    return None


def format_aggregate_answer(ctx: dict[str, Any], *, include_footer: bool = True) -> str:
    """API 키 없이 집계만으로 한국어 답변 생성."""
    lines: list[str] = []
    ov = ctx.get("sections", {}).get("overview", {})
    lines.append(f"[집계] 기준 시각: {ctx.get('as_of', '')}")
    lines.append(
        f"전체 현황 - 사업장 {ov.get('sites', 0)} · 건물 {ov.get('buildings', 0)} · "
        f"활성 설비 {ov.get('equipment_active', 0):,} · 정비의뢰 {ov.get('work_orders_active', 0)} · "
        f"PM일정 {ov.get('pm_schedules', 0)} · 점검일지 파일 {ov.get('inspection_log_files', 0)}"
        + (f" · 가로등 {ov.get('lamps', 0):,}" if "lamps" in ov else "")
        + (f" · 가로등 의뢰 {ov.get('lamp_requests', 0)}" if "lamp_requests" in ov else "")
    )

    sec = ctx.get("sections", {})
    if "equipment" in sec:
        eq = sec["equipment"]
        lines.append("")
        lines.append("■ 설비")
        if eq.get("building_match"):
            for b in eq["building_match"]:
                lines.append(f"  · {b['name']}: 활성 설비 {b['equipment']:,}건")
        lines.append("  (건물별 상위)")
        for row in eq.get("by_building_top", [])[:8]:
            lines.append(f"  · {row['building']}: {row['count']:,}건")
        if eq.get("by_category_top"):
            lines.append("  (분류별 상위)")
            for row in eq["by_category_top"][:8]:
                lines.append(f"  · {row['category']}: {row['count']:,}건")

    if "work_orders" in sec:
        wo = sec["work_orders"]
        lines.append("")
        lines.append("■ 정비의뢰")
        lines.append(
            f"  진행 중(접수·배정·진행) {wo.get('open_count', 0)}건 · "
            f"완료계열 {wo.get('completed', 0)}건"
        )
        st = wo.get("by_status") or {}
        if st:
            pretty = ", ".join(f"{k}={v}" for k, v in st.items())
            lines.append(f"  상태별: {pretty}")
        for r in wo.get("recent", [])[:5]:
            d1 = "D-1승인" if r.get("d1_approved") else "D-1미승인"
            lines.append(f"  · #{r['id']} [{r['status']}/{d1}] {r['title']}")

    if "pm" in sec:
        pm = sec["pm"]
        lines.append("")
        lines.append("■ 예방점검(PM)")
        lines.append(
            f"  일정 {pm.get('schedules', 0)} · 7일 이내 도래 {pm.get('due_within_7_days', 0)} · "
            f"지연 {pm.get('overdue', 0)} · 고장결과 {pm.get('fault', 0)} · 주의 {pm.get('caution', 0)}"
        )

    if "streetlamp" in sec:
        sl = sec["streetlamp"]
        lines.append("")
        lines.append("■ 가로등")
        lines.append(f"  등록 {sl.get('lamps', 0):,} · 의뢰 누적 {sl.get('requests_total', 0)}")
        if sl.get("requests_by_status"):
            pretty = ", ".join(f"{k}={v}" for k, v in sl["requests_by_status"].items())
            lines.append(f"  의뢰 상태: {pretty}")

    if "partners" in sec and ctx.get("intent") in ("partner", "d1", "overview"):
        names = [p["name"] for p in sec["partners"][:12]]
        if names:
            lines.append("")
            lines.append("■ 협력사: " + ", ".join(names))

    if "inspection_logs" in sec:
        lines.append("")
        lines.append(f"■ 점검일지 파일 {sec['inspection_logs'].get('files', 0)}건")

    if include_footer:
        lines.append("")
        lines.append(
            "※ 이 답변은 DB 집계 결과입니다. GPT 해석이 필요하면 우측 「AI 질문」을 사용하세요."
        )
    return "\n".join(lines)


def _sanitize_openai_api_key(api_key: str) -> str:
    """HTTP Authorization 헤더는 latin-1만 허용 → 키는 ASCII만 사용."""
    key = (api_key or "").strip().replace("\ufeff", "")
    # 마스킹 값이 잘못 저장된 경우
    if not key or "…" in key or set(key) <= {"•", "*"}:
        raise RuntimeError(
            "OpenAI API 키가 올바르지 않습니다. "
            "마스킹된 값이 아니라 원본 키(sk-...)를 다시 저장해 주세요."
        )
    try:
        key.encode("ascii")
    except UnicodeEncodeError as e:
        raise RuntimeError(
            "API 키에 한글/유니코드 문자가 포함되어 있습니다. "
            "OpenAI 키(영문·숫자·기호만)를 다시 등록해 주세요."
        ) from e
    if not (key.startswith("sk-") or key.startswith("sk-proj-")):
        # 형식만 경고성 — 일부 키는 다를 수 있어 차단하지는 않음
        pass
    return key


def call_openai_detail(
    *,
    api_key: str,
    model: str,
    question: str,
    context: dict[str, Any],
) -> str:
    """집계 컨텍스트를 바탕으로 GPT 세부 분석 문장 생성."""
    import urllib.error
    import urllib.request

    key = _sanitize_openai_api_key(api_key)
    system = (
        "당신은 POSCO WIDE Smart FMS 시설관리 분석 도우미입니다. "
        "제공된 JSON 집계만 근거로 한국어로 답하세요. "
        "없는 수치는 추측하지 말고, 개선 제안은 근거와 함께 짧게 제시하세요. "
        "비밀번호·API키·개인 연락처는 언급하지 마세요. "
        "답변 서두에 'GPT 분석'이라고 쓰지 말고, 바로 본론부터 작성하세요."
    )
    payload_ctx = json.dumps(context, ensure_ascii=False, default=str)
    if len(payload_ctx) > 28000:
        payload_ctx = payload_ctx[:28000] + "..."
    user_msg = (
        f"질문:\n{question}\n\n"
        f"FMS 집계 데이터(JSON):\n{payload_ctx}\n\n"
        "위 데이터만으로 세부 분석 답변을 작성하세요."
    )
    model_name = (model or "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        model_name.encode("ascii")
    except UnicodeEncodeError:
        model_name = "gpt-4o-mini"

    body_obj = {
        "model": model_name,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }

    def _post(use_model: str) -> dict:
        payload = dict(body_obj)
        payload["model"] = use_model
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "SmartFMS/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:400]
            if e.code == 404 and use_model != "gpt-4o-mini":
                return _post("gpt-4o-mini")
            raise RuntimeError(f"OpenAI 오류 ({e.code}): {err_body}") from e

    data = _post(model_name)
    text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not text:
        raise RuntimeError("OpenAI 응답이 비어 있습니다.")
    return text


async def run_analysis(
    db: AsyncSession,
    question: str,
    *,
    mode: str,
    api_key: str = "",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """
    mode:
      - aggregate: 집계만
      - detail: API 키로 GPT 분석 (없으면 needs_api_key)
    반환: answer(화면 본문), evidence(집계 근거, detail일 때), mode, ...
    """
    import asyncio

    q = (question or "").strip()
    if not q:
        return {
            "ok": False,
            "mode": mode,
            "needs_api_key": False,
            "answer": "질문을 입력해 주세요.",
            "evidence": "",
            "intent": "overview",
            "context": {},
            "error": "질문을 입력해 주세요.",
        }

    intent = classify_intent(q)
    context = await gather_context(db, intent, q)
    evidence = format_aggregate_answer(context, include_footer=False)
    aggregate_text = format_aggregate_answer(context, include_footer=True)

    if mode != "detail":
        return {
            "ok": True,
            "mode": "aggregate",
            "needs_api_key": False,
            "answer": aggregate_text,
            "evidence": "",
            "intent": intent,
            "context": context,
            "error": "",
        }

    key = (api_key or "").strip()
    if not key:
        return {
            "ok": True,
            "mode": "needs_key",
            "needs_api_key": True,
            "answer": (
                "OpenAI API 키가 없어 GPT 분석을 실행하지 못했습니다.\n"
                "아래 「API 키 등록」에서 키를 저장한 뒤 AI 질문을 다시 눌러 주세요."
            ),
            "evidence": evidence,
            "intent": intent,
            "context": context,
            "error": "",
        }

    try:
        detail = await asyncio.to_thread(
            call_openai_detail,
            api_key=key,
            model=model or "gpt-4o-mini",
            question=q,
            context=context,
        )
        return {
            "ok": True,
            "mode": "detail",
            "needs_api_key": False,
            "answer": detail,
            "evidence": evidence,
            "intent": intent,
            "context": context,
            "error": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "mode": "detail_error",
            "needs_api_key": False,
            "answer": f"GPT 호출에 실패했습니다.\n{e}",
            "evidence": evidence,
            "intent": intent,
            "context": context,
            "error": str(e),
        }