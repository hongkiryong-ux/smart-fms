# ai_analysis.py — Smart FMS 전체 데이터 스냅샷 + 집계/GPT 답변
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import (
    Building,
    CentralControlRoomDaily,
    CcrFacilityDaily,
    Consumable,
    D1Plan,
    Equipment,
    Floor,
    HousingSubstationDaily,
    InspectionLogBuilding,
    InspectionLogBuilding2,
    InspectionLogFile,
    MaintenanceRecord,
    MaterialItem,
    Notice,
    Partner,
    PMInspection,
    PMResult,
    PMSchedule,
    ScheduleEvent,
    Site,
    SteelworksHqDaily,
    User,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)

try:
    from streetlamp.models import Lamp, MaintenanceRequest as LampRequest
except Exception:  # pragma: no cover
    Lamp = None
    LampRequest = None

_CONTEXT_MAX_CHARS = 52000


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
        (
            "inspection_log2",
            ("점검일지2", "주택변전소", "중앙관제실", "제철소본부", "운영일보"),
        ),
        ("materials", ("자재", "재고", "소모품", "material")),
        ("notices", ("공지", "notice")),
        ("schedules", ("일정", "캘린더", "schedule")),
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


def _enum_val(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v or "")


def _clip(text: Any, n: int = 200) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


def _summarize_log2_daily(data: dict | None) -> dict[str, Any]:
    data = data or {}
    out: dict[str, Any] = {}
    fac = data.get("facility") or {}
    util = fac.get("utility") or {}
    if util:
        out["utility"] = {
            uid: {
                k: row.get(k, "")
                for k in ("daily", "monthly", "today", "prev")
                if (row or {}).get(k) not in (None, "")
            }
            for uid, row in util.items()
            if isinstance(row, dict)
        }
    elec = data.get("electrical") or {}
    if elec:
        out["electrical_blocks"] = list(elec.keys())
    notes = fac.get("notes") or data.get("notes")
    if notes:
        out["notes"] = _clip(notes, 300)
    return out


async def _latest_daily_rows(db: AsyncSession, model) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(model, Building.name)
            .join(Building, Building.id == model.building_id)
            .order_by(model.building_id, model.log_date.desc())
        )
    ).all()
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row, bname in rows:
        if row.building_id in seen:
            continue
        seen.add(row.building_id)
        out.append(
            {
                "building_id": row.building_id,
                "building": bname,
                "date": row.log_date.isoformat(),
                "summary": _summarize_log2_daily(row.data),
            }
        )
    return out


def _match_buildings_in_question(question: str, buildings: list[dict]) -> list[dict]:
    q = (question or "").strip()
    if not q:
        return []
    matched: list[dict] = []
    for b in buildings:
        name = b.get("name") or ""
        code = b.get("code") or ""
        if name and name in q:
            matched.append(b)
        elif code and code in q:
            matched.append(b)
    hint = _extract_building_hint(question)
    if hint:
        for b in buildings:
            name = b.get("name") or ""
            if hint in name and b not in matched:
                matched.append(b)
    return matched[:8]


def _extract_year_month(question: str) -> tuple[int | None, int | None]:
    q = question or ""
    year = None
    month = None
    ym = re.search(r"(20\d{2})\s*년", q)
    if ym:
        year = int(ym.group(1))
    mm = re.search(r"(\d{1,2})\s*월", q)
    if mm:
        month = int(mm.group(1))
    return year, month


def _compact_housing_monthly_power(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sec in report.get("sections") or []:
        breaker_map = {b["meter_id"]: b for b in sec.get("breakers") or []}
        sec_data: dict[str, Any] = {
            "section": sec.get("title", ""),
            "monthly_totals_kwh": [],
            "daily_usage_kwh": [],
        }
        for tot in sec.get("totals") or []:
            mid = tot.get("meter_id")
            br = breaker_map.get(mid) or {}
            usage = tot.get("usage_sum")
            if usage not in (None, ""):
                sec_data["monthly_totals_kwh"].append(
                    {
                        "name": br.get("name", mid),
                        "total": usage,
                        "max_a": tot.get("max_a_max", ""),
                    }
                )
        for day in sec.get("days") or []:
            usages: dict[str, Any] = {}
            for br in day.get("breakers") or []:
                if br.get("usage") not in (None, ""):
                    usages[br.get("name", "")] = br["usage"]
            if usages:
                sec_data["daily_usage_kwh"].append(
                    {"day": day.get("day"), "date": day.get("date"), "usage": usages}
                )
        if sec_data["monthly_totals_kwh"] or sec_data["daily_usage_kwh"]:
            out.append(sec_data)
    return out


async def _gather_housing_monthly_reports(
    db: AsyncSession,
    question: str,
    building_rows: list[Building],
) -> list[dict[str, Any]]:
    """주택변전소 월보(일별·월합계 전력사용량) — 질문 연·월 기준."""
    import calendar

    from housing_substation import fetch_monthly_report_data, is_housing_substation_building

    q = question or ""
    year, month = _extract_year_month(q)
    housing_buildings = [b for b in building_rows if is_housing_substation_building(b)]
    if not housing_buildings:
        return []

    power_kw = any(k in q for k in ("전력", "사용량", "kwh", "kw", "월보", "전기", "전류"))
    housing_kw = any(k in q for k in ("주택변전소", "주택"))
    if not (housing_kw or power_kw or year is not None or month is not None):
        return []

    target_year = year or _today().year
    target_month = month or _today().month
    last_day = calendar.monthrange(target_year, target_month)[1]
    d_from = date(target_year, target_month, 1)
    d_to = date(target_year, target_month, last_day)

    reports_out: list[dict[str, Any]] = []
    for b in housing_buildings:
        row_count = await _count(
            db,
            select(func.count(HousingSubstationDaily.id)).where(
                HousingSubstationDaily.building_id == b.id,
                HousingSubstationDaily.log_date >= d_from,
                HousingSubstationDaily.log_date <= d_to,
            ),
        )
        monthly = await fetch_monthly_report_data(db, b.id, target_year, target_month)
        reports_out.append(
            {
                "building": b.name,
                "building_id": b.id,
                "year": target_year,
                "month": target_month,
                "daily_rows_in_month": row_count,
                "power_usage": _compact_housing_monthly_power(monthly),
            }
        )
    return reports_out


async def gather_context(db: AsyncSession, intent: str, question: str) -> dict[str, Any]:
    """Smart FMS 전체 운영 데이터 스냅샷 (일반질문·GPT 공통)."""
    ctx: dict[str, Any] = {
        "intent": intent,
        "question": (question or "").strip(),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_scope": "full_fms_snapshot",
        "sections": {},
    }
    sec = ctx["sections"]

    site_rows = (
        await db.execute(select(Site).where(Site.is_active == True).order_by(Site.name))  # noqa: E712
    ).scalars().all()
    building_rows = (
        await db.execute(
            select(Building)
            .where(Building.is_active == True)  # noqa: E712
            .options(selectinload(Building.site))
            .order_by(Building.name)
        )
    ).scalars().all()
    building_map = {b.id: b for b in building_rows}

    sec["sites"] = [
        {
            "id": s.id,
            "name": s.name,
            "code": s.code,
            "address": _clip(s.address, 120),
            "manager": s.manager_name or "",
        }
        for s in site_rows
    ]
    sec["buildings"] = [
        {
            "id": b.id,
            "name": b.name,
            "code": b.code,
            "site": b.site.name if b.site else "",
            "manager": b.manager_name or "",
        }
        for b in building_rows
    ]
    sec["question_buildings"] = _match_buildings_in_question(question, sec["buildings"])

    overview = {
        "sites": len(sec["sites"]),
        "buildings": len(sec["buildings"]),
        "equipment_active": await _count(
            db, select(func.count(Equipment.id)).where(Equipment.is_active == True)  # noqa: E712
        ),
        "work_orders_active": await _count(
            db, select(func.count(WorkOrder.id)).where(WorkOrder.is_active == True)  # noqa: E712
        ),
        "pm_schedules": await _count(db, select(func.count(PMSchedule.id))),
        "partners": await _count(db, select(func.count(Partner.id))),
        "inspection_log_files": await _count(db, select(func.count(InspectionLogFile.id))),
        "users_active": await _count(
            db,
            select(func.count(User.id)).where(
                User.is_active == True, User.is_approved == True  # noqa: E712
            ),
        ),
        "material_items": await _count(db, select(func.count(MaterialItem.id))),
        "d1_plans": await _count(db, select(func.count(D1Plan.id))),
        "maintenance_records": await _count(db, select(func.count(MaintenanceRecord.id))),
        "consumables": await _count(db, select(func.count(Consumable.id))),
        "notices_active": await _count(
            db, select(func.count(Notice.id)).where(Notice.is_active == True)  # noqa: E712
        ),
        "schedule_events": await _count(
            db,
            select(func.count(ScheduleEvent.id)).where(ScheduleEvent.is_active == True),  # noqa: E712
        ),
    }
    if Lamp is not None:
        overview["lamps"] = await _count(db, select(func.count(Lamp.id)))
    if LampRequest is not None:
        overview["lamp_requests"] = await _count(db, select(func.count(LampRequest.id)))
    sec["overview"] = overview

    eq_rows = (
        await db.execute(
            select(Building.name, func.count(Equipment.id))
            .select_from(Equipment)
            .join(Zone, Equipment.zone_id == Zone.id)
            .join(Floor, Zone.floor_id == Floor.id)
            .join(Building, Floor.building_id == Building.id)
            .where(Equipment.is_active == True)  # noqa: E712
            .group_by(Building.id, Building.name)
            .order_by(func.count(Equipment.id).desc())
            .limit(30)
        )
    ).all()
    cat_rows = (
        await db.execute(
            select(Equipment.category, func.count(Equipment.id))
            .where(Equipment.is_active == True)  # noqa: E712
            .group_by(Equipment.category)
            .order_by(func.count(Equipment.id).desc())
            .limit(20)
        )
    ).all()
    recent_eq = (
        await db.execute(
            select(Equipment)
            .where(Equipment.is_active == True)  # noqa: E712
            .order_by(Equipment.id.desc())
            .limit(20)
        )
    ).scalars().all()
    sec["equipment"] = {
        "by_building": [{"building": r[0], "count": int(r[1])} for r in eq_rows],
        "by_category": [{"category": r[0] or "기타", "count": int(r[1])} for r in cat_rows],
        "recent": [
            {
                "id": e.id,
                "name": _clip(e.name, 80),
                "code": e.code or "",
                "category": _enum_val(e.category),
                "status": e.status or "",
            }
            for e in recent_eq
        ],
    }

    status_rows = (
        await db.execute(
            select(WorkOrder.status, func.count(WorkOrder.id))
            .where(WorkOrder.is_active == True)  # noqa: E712
            .group_by(WorkOrder.status)
        )
    ).all()
    status_map = {_enum_val(st): int(n) for st, n in status_rows}
    open_n = sum(status_map.get(k, 0) for k in ("received", "assigned", "in_progress"))
    recent_wo = (
        await db.execute(
            select(WorkOrder)
            .where(WorkOrder.is_active == True)  # noqa: E712
            .order_by(WorkOrder.id.desc())
            .limit(25)
        )
    ).scalars().all()
    sec["work_orders"] = {
        "by_status": status_map,
        "open_count": open_n,
        "completed": status_map.get("completed", 0)
        + status_map.get("verified", 0)
        + status_map.get("closed", 0),
        "approval_pending": await _count(
            db,
            select(func.count(WorkOrder.id)).where(
                WorkOrder.is_active == True,  # noqa: E712
                WorkOrder.approval_requested == True,  # noqa: E712
                WorkOrder.work_permitted == False,  # noqa: E712
            ),
        ),
        "recent": [
            {
                "id": wo.id,
                "title": _clip(wo.title, 80),
                "status": _enum_val(wo.status),
                "priority": wo.priority or "",
                "d1_approved": bool(wo.d1_approved),
                "work_permitted": bool(wo.work_permitted),
                "partner_id": wo.partner_id,
                "assignee": wo.assignee_name or "",
                "scheduled_date": str(wo.scheduled_date or ""),
            }
            for wo in recent_wo
        ],
    }

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
            select(PMInspection.result, func.count(PMInspection.id)).group_by(PMInspection.result)
        )
    ).all()
    results = {_enum_val(r): int(n) for r, n in result_rows}
    recent_pm = (
        await db.execute(select(PMInspection).order_by(PMInspection.id.desc()).limit(15))
    ).scalars().all()
    sec["pm"] = {
        "schedules": overview["pm_schedules"],
        "due_within_7_days": due_soon,
        "overdue": overdue,
        "inspection_results": results,
        "fault": results.get(PMResult.fault.value, results.get("fault", 0)),
        "caution": results.get(PMResult.caution.value, results.get("caution", 0)),
        "recent_inspections": [
            {
                "id": p.id,
                "result": _enum_val(p.result),
                "inspected_at": str(p.inspected_at or ""),
                "notes": _clip(p.note, 100),
            }
            for p in recent_pm
        ],
    }

    d1_status_rows = (
        await db.execute(select(D1Plan.status, func.count(D1Plan.id)).group_by(D1Plan.status))
    ).all()
    recent_d1 = (
        await db.execute(select(D1Plan).order_by(D1Plan.id.desc()).limit(20))
    ).scalars().all()
    sec["d1_plans"] = {
        "by_status": {_enum_val(st): int(n) for st, n in d1_status_rows},
        "recent": [
            {
                "id": p.id,
                "title": _clip(p.title, 80),
                "work_date": str(p.work_date or ""),
                "status": _enum_val(p.status),
                "partner_id": p.partner_id,
                "building_id": p.building_id,
                "is_urgent": bool(p.is_urgent),
            }
            for p in recent_d1
        ],
    }

    partners = (await db.execute(select(Partner).order_by(Partner.name).limit(50))).scalars().all()
    sec["partners"] = [
        {
            "id": p.id,
            "name": p.name,
            "code": getattr(p, "code", "") or "",
            "contact": _clip(getattr(p, "contact_name", "") or "", 40),
            "risk_grade": getattr(p, "risk_grade", "") or "",
        }
        for p in partners
    ]

    ilog_rows = (
        await db.execute(
            select(InspectionLogBuilding, Building)
            .join(Building, Building.id == InspectionLogBuilding.building_id)
            .order_by(Building.name)
        )
    ).all()
    ilog2_rows = (
        await db.execute(
            select(InspectionLogBuilding2, Building)
            .join(Building, Building.id == InspectionLogBuilding2.building_id)
            .order_by(Building.name)
        )
    ).all()
    ilog_files = (
        await db.execute(
            select(InspectionLogFile.building_id, func.count(InspectionLogFile.id))
            .group_by(InspectionLogFile.building_id)
        )
    ).all()
    file_map = {int(bid): int(n) for bid, n in ilog_files}
    sec["inspection_logs"] = {
        "registered_buildings": [
            {"id": b.id, "name": b.name, "code": b.code, "files": file_map.get(b.id, 0)}
            for _, b in ilog_rows
        ],
        "files_total": overview["inspection_log_files"],
    }
    sec["inspection_logs2"] = {
        "registered_buildings": [
            {"id": b.id, "name": b.name, "code": b.code} for _, b in ilog2_rows
        ],
        "housing_substation": {
            "daily_records": await _count(db, select(func.count(HousingSubstationDaily.id))),
            "latest": await _latest_daily_rows(db, HousingSubstationDaily),
        },
        "central_control_room": {
            "daily_records": await _count(db, select(func.count(CentralControlRoomDaily.id))),
            "latest": await _latest_daily_rows(db, CentralControlRoomDaily),
        },
        "ccr_facility": {
            "daily_records": await _count(db, select(func.count(CcrFacilityDaily.id))),
            "latest": await _latest_daily_rows(db, CcrFacilityDaily),
        },
        "steelworks_hq": {
            "daily_records": await _count(db, select(func.count(SteelworksHqDaily.id))),
            "latest": await _latest_daily_rows(db, SteelworksHqDaily),
        },
    }

    materials = (
        await db.execute(select(MaterialItem).order_by(MaterialItem.name).limit(80))
    ).scalars().all()
    sec["materials"] = {
        "items": [
            {
                "name": m.name,
                "quantity": int(m.quantity or 0),
                "group": m.group_name or "",
                "location": m.location or "",
                "spec": _clip(m.spec, 60),
            }
            for m in materials
        ],
        "low_stock": [
            {"name": m.name, "quantity": int(m.quantity or 0)}
            for m in materials
            if int(m.quantity or 0) <= 5
        ][:20],
    }

    notices = (
        await db.execute(
            select(Notice)
            .where(Notice.is_active == True)  # noqa: E712
            .order_by(Notice.is_pinned.desc(), Notice.published_at.desc())
            .limit(15)
        )
    ).scalars().all()
    sec["notices"] = [
        {
            "id": n.id,
            "title": _clip(n.title, 100),
            "category": n.category or "",
            "pinned": bool(n.is_pinned),
            "published_at": str(n.published_at or ""),
            "body": _clip(n.body, 200),
        }
        for n in notices
    ]

    upcoming = (
        await db.execute(
            select(ScheduleEvent)
            .where(
                ScheduleEvent.is_active == True,  # noqa: E712
                ScheduleEvent.event_date >= today,
                ScheduleEvent.event_date <= today + timedelta(days=60),
            )
            .order_by(ScheduleEvent.event_date.asc())
            .limit(30)
        )
    ).scalars().all()
    sec["schedules"] = [
        {
            "id": e.id,
            "title": _clip(e.title, 80),
            "category": e.category or "",
            "date": str(e.event_date or ""),
            "time": e.event_time or "",
            "location": e.location or "",
        }
        for e in upcoming
    ]

    role_rows = (
        await db.execute(
            select(User.role, func.count(User.id)).where(
                User.is_active == True, User.is_approved == True  # noqa: E712
            ).group_by(User.role)
        )
    ).all()
    sec["users"] = {
        "active_approved": overview["users_active"],
        "by_role": {_enum_val(r): int(n) for r, n in role_rows},
    }

    recent_maint = (
        await db.execute(select(MaintenanceRecord).order_by(MaintenanceRecord.id.desc()).limit(15))
    ).scalars().all()
    sec["maintenance_records"] = {
        "total": overview["maintenance_records"],
        "recent": [
            {
                "id": m.id,
                "equipment_id": m.equipment_id,
                "title": _clip(m.title, 80),
                "work_date": str(m.work_date or ""),
                "action": _clip(m.action, 100),
                "cost": m.cost,
            }
            for m in recent_maint
        ],
    }

    if Lamp is not None:
        lamp_sec: dict[str, Any] = {"lamps": overview.get("lamps", 0)}
        if LampRequest is not None:
            lamp_sec["requests_total"] = overview.get("lamp_requests", 0)
            try:
                req_rows = (
                    await db.execute(
                        select(LampRequest.status, func.count(LampRequest.id)).group_by(
                            LampRequest.status
                        )
                    )
                ).all()
                lamp_sec["requests_by_status"] = {
                    _enum_val(s): int(n) for s, n in req_rows
                }
            except Exception:
                pass
            recent_req = (
                await db.execute(select(LampRequest).order_by(LampRequest.id.desc()).limit(12))
            ).scalars().all()
            lamp_sec["recent_requests"] = [
                {
                    "id": r.id,
                    "status": _enum_val(getattr(r, "status", "")),
                    "note": _clip(
                        (
                            _enum_val(getattr(r, "request_type", ""))
                            + " "
                            + str(getattr(r, "content", "") or "")
                        ),
                        80,
                    ),
                }
                for r in recent_req
            ]
        sec["streetlamp"] = lamp_sec

    housing_monthly = await _gather_housing_monthly_reports(db, question, building_rows)
    if housing_monthly:
        sec["housing_monthly_reports"] = housing_monthly

    return ctx


def _extract_building_hint(question: str) -> str | None:
    q = (question or "").strip()
    m = re.search(r"([가-힣A-Za-z0-9\-]+)\s*(건물|동|센터|관)", q)
    if m:
        return m.group(1)
    return None


def _format_section_lines(title: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    return ["", f"■ {title}"] + lines


def _answer_for_buildings(sec: dict, matched: list[dict]) -> list[str]:
    lines: list[str] = []
    eq_by_name = {r["building"]: r["count"] for r in sec.get("equipment", {}).get("by_building", [])}
    ilog = {b["name"]: b for b in sec.get("inspection_logs", {}).get("registered_buildings", [])}
    ilog2 = {b["name"]: b for b in sec.get("inspection_logs2", {}).get("registered_buildings", [])}
    for b in matched:
        name = b.get("name", "")
        parts = [f"{name} (코드 {b.get('code', '')}, 사업장 {b.get('site', '')})"]
        if name in eq_by_name:
            parts.append(f"활성 설비 {eq_by_name[name]:,}건")
        if name in ilog:
            parts.append(f"점검일지 등록·파일 {ilog[name].get('files', 0)}건")
        if name in ilog2:
            parts.append("점검일지2 등록됨")
        lines.append("  · " + " · ".join(parts))
    return lines


def build_aggregate_answer(ctx: dict[str, Any], question: str) -> str:
    """전체 FMS 데이터 기반 일반질문 답변."""
    sec = ctx.get("sections", {})
    ov = sec.get("overview", {})
    lines: list[str] = []
    matched = sec.get("question_buildings") or []
    if matched:
        lines.extend(_format_section_lines("질문 관련 건물", _answer_for_buildings(sec, matched)))

    for rep in sec.get("housing_monthly_reports") or []:
        hm_lines = [
            f"  {rep['building']} {rep['year']}년 {rep['month']}월 "
            f"(입력 일지 {rep['daily_rows_in_month']}일)"
        ]
        for sec_pwr in rep.get("power_usage") or []:
            hm_lines.append(f"  [{sec_pwr.get('section', '')}]")
            for t in sec_pwr.get("monthly_totals_kwh") or []:
                hm_lines.append(f"    · {t['name']}: 월합계 {t['total']} kWh")
            for d in sec_pwr.get("daily_usage_kwh") or []:
                parts = ", ".join(f"{k}={v}kWh" for k, v in (d.get("usage") or {}).items())
                hm_lines.append(f"    · {d.get('day')}일: {parts}")
        lines.extend(_format_section_lines("주택변전소 전력 사용량", hm_lines))

    lines.append(f"[Smart FMS 전체 데이터] 기준 시각: {ctx.get('as_of', '')}")
    lines.append(
        f"사업장 {ov.get('sites', 0)} · 건물 {ov.get('buildings', 0)} · "
        f"활성 설비 {ov.get('equipment_active', 0):,} · 정비의뢰 {ov.get('work_orders_active', 0)} · "
        f"PM {ov.get('pm_schedules', 0)} · D-1 {ov.get('d1_plans', 0)} · "
        f"자재 {ov.get('material_items', 0)} · 점검일지 파일 {ov.get('inspection_log_files', 0)}"
        + (f" · 가로등 {ov.get('lamps', 0):,}" if "lamps" in ov else "")
    )

    eq = sec.get("equipment", {})
    eq_lines = [f"  · {r['building']}: {r['count']:,}건" for r in eq.get("by_building", [])[:10]]
    lines.extend(_format_section_lines("설비 (건물별)", eq_lines))

    wo = sec.get("work_orders", {})
    wo_lines = [
        f"  진행 중 {wo.get('open_count', 0)}건 · 완료계열 {wo.get('completed', 0)}건 · "
        f"작업허가 대기 {wo.get('approval_pending', 0)}건"
    ]
    for r in wo.get("recent", [])[:6]:
        wo_lines.append(
            f"  · #{r['id']} [{r['status']}] {r['title']}"
            + (" (D-1승인)" if r.get("d1_approved") else "")
        )
    lines.extend(_format_section_lines("정비의뢰", wo_lines))

    pm = sec.get("pm", {})
    lines.extend(
        _format_section_lines(
            "예방점검(PM)",
            [
                f"  일정 {pm.get('schedules', 0)} · 7일 이내 {pm.get('due_within_7_days', 0)} · "
                f"지연 {pm.get('overdue', 0)} · 고장 {pm.get('fault', 0)} · 주의 {pm.get('caution', 0)}"
            ],
        )
    )

    d1 = sec.get("d1_plans", {})
    if d1:
        st = d1.get("by_status") or {}
        d1_lines = [f"  상태: {', '.join(f'{k}={v}' for k, v in st.items())}" if st else ""]
        for r in d1.get("recent", [])[:5]:
            d1_lines.append(f"  · {r.get('work_date')} [{r.get('status')}] {r.get('title')}")
        lines.extend(_format_section_lines("D-1 작업계획", [x for x in d1_lines if x]))

    ilog2 = sec.get("inspection_logs2", {})
    if ilog2:
        reg = ", ".join(b["name"] for b in ilog2.get("registered_buildings", [])) or "없음"
        il2_lines = [f"  등록 건물: {reg}"]
        for key, label in (
            ("housing_substation", "주택변전소"),
            ("central_control_room", "중앙관제실(전기)"),
            ("ccr_facility", "중앙관제실(설비)"),
            ("steelworks_hq", "제철소본부"),
        ):
            mod = ilog2.get(key) or {}
            latest = mod.get("latest") or []
            il2_lines.append(f"  · {label}: 일지 {mod.get('daily_records', 0)}건")
            for row in latest[:2]:
                il2_lines.append(f"    - {row.get('building')} 최근 {row.get('date')}")
        lines.extend(_format_section_lines("점검일지2", il2_lines))

    mats = sec.get("materials", {})
    if mats.get("items"):
        mat_lines = [
            f"  · {m['name']}: {m['quantity']}{' (' + m['group'] + ')' if m.get('group') else ''}"
            for m in mats["items"][:12]
        ]
        if mats.get("low_stock"):
            mat_lines.append(
                "  재고 부족(≤5): "
                + ", ".join(f"{m['name']}({m['quantity']})" for m in mats["low_stock"][:8])
            )
        lines.extend(_format_section_lines("자재", mat_lines))

    if sec.get("notices"):
        n_lines = [
            f"  · [{n.get('category')}] {n.get('title')}" for n in sec["notices"][:5]
        ]
        lines.extend(_format_section_lines("공지", n_lines))

    if sec.get("schedules"):
        s_lines = [
            f"  · {e.get('date')} {e.get('title')} ({e.get('category')})"
            for e in sec["schedules"][:8]
        ]
        lines.extend(_format_section_lines("주요설비 일정(60일)", s_lines))

    if sec.get("partners"):
        names = [p["name"] for p in sec["partners"][:15]]
        lines.extend(_format_section_lines("협력사", ["  " + ", ".join(names)]))

    if sec.get("streetlamp"):
        sl = sec["streetlamp"]
        lines.extend(
            _format_section_lines(
                "가로등",
                [f"  등록 {sl.get('lamps', 0):,} · 의뢰 {sl.get('requests_total', 0)}"],
            )
        )

    lines.append("")
    lines.append(
        "※ Smart FMS 전체 DB 스냅샷 기반 답변입니다. 해석·제안이 필요하면 「AI 질문(GPT)」을 사용하세요."
    )
    return "\n".join(lines)


def format_aggregate_answer(ctx: dict[str, Any], *, include_footer: bool = True) -> str:
    """집계 근거 텍스트 (GPT evidence용)."""
    return build_aggregate_answer(ctx, ctx.get("question", "")).replace(
        "\n※ Smart FMS 전체 DB 스냅샷 기반 답변입니다. 해석·제안이 필요하면 「AI 질문(GPT)」을 사용하세요.",
        "\n※ GPT 분석에 사용된 FMS 전체 데이터 요약입니다." if not include_footer else "",
    )


def _sanitize_openai_api_key(api_key: str) -> str:
    key = (api_key or "").strip().replace("\ufeff", "")
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
    return key


def call_openai_detail(
    *,
    api_key: str,
    model: str,
    question: str,
    context: dict[str, Any],
) -> str:
    import urllib.error
    import urllib.request

    key = _sanitize_openai_api_key(api_key)
    system = (
        "당신은 POSCO WIDE Smart FMS 시설관리 분석 도우미입니다. "
        "제공된 JSON은 Smart FMS에 등록된 전체 운영 데이터의 최신 스냅샷입니다 "
        "(사업장·건물·설비·정비의뢰·PM·D-1·협력사·점검일지·점검일지2·자재·공지·일정·가로등 등). "
        "housing_monthly_reports에는 주택변전소 월보 전력사용량(일별·월합계 kWh)이 포함됩니다. "
        "JSON에 있는 수치·목록만 근거로 질문에 한국어로 답하세요. "
        "없는 정보는 추측하지 말고 '데이터에 없음'이라고 하세요. "
        "비밀번호·API키·개인 연락처는 언급하지 마세요. "
        "답변 서두에 'GPT 분석'이라고 쓰지 말고 바로 본론부터 작성하세요."
    )
    payload_ctx = json.dumps(context, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(payload_ctx) > _CONTEXT_MAX_CHARS:
        payload_ctx = payload_ctx[:_CONTEXT_MAX_CHARS] + "..."
    user_msg = (
        f"질문:\n{question}\n\n"
        f"Smart FMS 전체 데이터(JSON):\n{payload_ctx}\n\n"
        "위 데이터만 근거로 질문에 답하고, 필요하면 표나 목록으로 정리하세요."
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
    aggregate_text = build_aggregate_answer(context, q)

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
