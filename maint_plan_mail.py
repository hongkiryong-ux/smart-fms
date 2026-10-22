# maint_plan_mail.py — 정비관리 설정: 다음날 협력사 작업 예정 항목 일일 메일 (하루 전 발송)
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maint_mail import (
    KST,
    PRIORITY_LABELS,
    STATUS_LABELS,
    _clamp_int,
    _deliver,
    _truthy,
    build_xlsx,
    fmt_kst,
    load_sender,
    parse_emails,
    render_table_mail,
)
from models import (
    AppSetting,
    Building,
    Equipment,
    Floor,
    Partner,
    Site,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)

JOB_ID = "maint_partner_plan_mail"
PREFIX = "maint_plan_mail."

# 포함 기준: 예정일 지정(협력사 작업 확정) / 작업허가 승인요청 이상 / 작업허가 승인완료만
SCOPES = (
    ("scheduled", "작업 예정일이 내일로 지정된 건 전체"),
    ("requested", "작업허가 승인요청한 건만 (승인완료 포함)"),
    ("permitted", "작업허가 승인완료된 건만"),
)
SCOPE_KEYS = tuple(k for k, _ in SCOPES)

DEFAULTS: dict[str, str] = {
    "enabled": "0",
    "hour": "16",
    "minute": "0",
    "scope": "scheduled",
    "to": "",
    "per_partner": "1",
    "partner_to": "{}",
    "send_empty": "0",
    "intro": (
        "안녕하십니까, Digital FMS 정비관리입니다.\n"
        "{작업일} 작업 예정인 {협력사} 정비 {건수}건을 아래와 같이 안내드립니다.\n"
        "작업 준비와 안전조치를 확인하시고 진행 부탁드립니다. 감사합니다."
    ),
    "last_run_date": "",
    "last_result": "",
    "last_result_at": "",
}

OPEN_STATUSES = (WorkOrderStatus.received, WorkOrderStatus.assigned, WorkOrderStatus.in_progress)
WEEKDAYS = "월화수목금토일"
FOOTER = "※ Digital FMS 정비관리에서 자동 발송된 메일입니다. 잠재위험·안전대책 등 상세 내역은 첨부 엑셀 파일에 있습니다."

BODY_COLUMNS = (
    ("No", "id", 30),
    ("우선순위", "priority", 44),
    ("위치", "location", 90),
    ("설비", "_eq", 100),
    ("정비제목", "title", 120),
    ("정비내용", "description", 180),
    ("작업유형", "work_type", 48),
    ("담당자", "assignee", 48),
    ("작업허가", "permit", 56),
    ("진행상태", "status", 56),
)
XLSX_COLUMNS = (
    ("No", "id", 8),
    ("협력사", "partner_name", 16),
    ("작업예정일", "scheduled_date", 12),
    ("우선순위", "priority", 9),
    ("위치", "location", 22),
    ("설비코드", "eq_code", 16),
    ("설비명", "eq_name", 22),
    ("정비제목", "title", 30),
    ("정비내용", "description", 40),
    ("작업유형", "work_type", 12),
    ("담당자", "assignee", 12),
    ("의뢰자", "requester", 12),
    ("작업허가", "permit", 12),
    ("진행상태", "status", 12),
    ("위험등급", "risk_grade", 10),
    ("잠재위험", "hazard", 36),
    ("안전대책", "safety", 36),
)


async def _get(session: AsyncSession, key: str) -> str:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == PREFIX + key))
    ).scalar_one_or_none()
    if row and row.value is not None:
        return row.value
    return DEFAULTS.get(key, "")


async def _set(session: AsyncSession, key: str, value: str) -> None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == PREFIX + key))
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=PREFIX + key, value=value))


async def load_settings(session: AsyncSession) -> dict:
    raw = {k: await _get(session, k) for k in DEFAULTS}
    try:
        partner_to = json.loads(raw["partner_to"] or "{}")
        if not isinstance(partner_to, dict):
            partner_to = {}
    except ValueError:
        partner_to = {}
    return {
        "enabled": _truthy(raw["enabled"]),
        "hour": _clamp_int(raw["hour"], 0, 23, 16),
        "minute": _clamp_int(raw["minute"], 0, 59, 0),
        "scope": raw["scope"] if raw["scope"] in SCOPE_KEYS else "scheduled",
        "to": raw["to"],
        "per_partner": _truthy(raw["per_partner"]),
        "partner_to": {str(k): str(v) for k, v in partner_to.items()},
        "send_empty": _truthy(raw["send_empty"]),
        "intro": raw["intro"],
        "last_run_date": raw["last_run_date"],
        "last_result": raw["last_result"],
        "last_result_at": raw["last_result_at"],
    }


async def save_settings(
    session: AsyncSession,
    *,
    enabled: bool,
    hour: int,
    minute: int,
    scope: str,
    to: str,
    per_partner: bool,
    partner_to: dict[str, str],
    send_empty: bool,
    intro: str | None = None,
) -> None:
    if intro is not None:
        await _set(session, "intro", intro.strip()[:2000])
    await _set(session, "enabled", "1" if enabled else "0")
    await _set(session, "hour", str(max(0, min(23, hour))))
    await _set(session, "minute", str(max(0, min(59, minute))))
    await _set(session, "scope", scope if scope in SCOPE_KEYS else "scheduled")
    await _set(session, "to", to)
    await _set(session, "per_partner", "1" if per_partner else "0")
    await _set(
        session,
        "partner_to",
        json.dumps({k: v for k, v in partner_to.items() if v.strip()}, ensure_ascii=False),
    )
    await _set(session, "send_empty", "1" if send_empty else "0")


def target_day(now_utc: datetime | None = None) -> date:
    """발송일(KST) 다음날 = 메일에 담을 작업 예정일."""
    now = (now_utc or datetime.utcnow()).replace(tzinfo=None)
    return now.replace(tzinfo=timezone.utc).astimezone(KST).date() + timedelta(days=1)


def day_label(d: date) -> str:
    return f"{d.isoformat()}({WEEKDAYS[d.weekday()]})"


def _permit_label(wo: WorkOrder) -> str:
    if getattr(wo, "work_permitted", False):
        return "승인완료"
    if getattr(wo, "approval_requested", False):
        return "승인요청"
    return "미요청"


async def fetch_plans(session: AsyncSession, day: date, scope: str = "scheduled") -> list[dict]:
    """D-1(협력사 지정·승인) 미완료 정비 중 작업 예정일이 day 인 건."""
    conds = [
        WorkOrder.is_active == True,  # noqa: E712
        WorkOrder.d1_approved == True,  # noqa: E712
        WorkOrder.completion_approval_pending.is_not(True),
        WorkOrder.status.in_(OPEN_STATUSES),
        WorkOrder.scheduled_date == day,
    ]
    if scope == "permitted":
        conds.append(WorkOrder.work_permitted == True)  # noqa: E712
    elif scope == "requested":
        conds.append(
            (WorkOrder.approval_requested == True) | (WorkOrder.work_permitted == True)  # noqa: E712
        )
    rows = (
        await session.execute(
            select(WorkOrder, Partner, Equipment, Building.name, Site.name)
            .join(Partner, WorkOrder.partner_id == Partner.id)
            .outerjoin(Equipment, WorkOrder.equipment_id == Equipment.id)
            .outerjoin(Zone, Equipment.zone_id == Zone.id)
            .outerjoin(Floor, Zone.floor_id == Floor.id)
            .outerjoin(Building, Floor.building_id == Building.id)
            .outerjoin(Site, Site.id == WorkOrder.site_id)
            .where(*conds)
            .order_by(Partner.name, WorkOrder.priority.desc(), WorkOrder.id)
        )
    ).all()
    out: list[dict] = []
    for wo, partner, eq, bname, sname in rows:
        status = wo.status.value if isinstance(wo.status, WorkOrderStatus) else str(wo.status or "")
        out.append(
            {
                "id": wo.id,
                "partner_id": partner.id,
                "partner_name": partner.name,
                "partner_email": (partner.email or "").strip(),
                "scheduled_date": wo.scheduled_date.isoformat() if wo.scheduled_date else "",
                "priority": PRIORITY_LABELS.get(wo.priority or "", wo.priority or ""),
                "location": " / ".join(x for x in (sname, bname) if x),
                "eq_code": eq.code if eq else "",
                "eq_name": eq.name if eq else "",
                "title": (wo.title or "").strip(),
                "description": (wo.description or "").strip(),
                "work_type": wo.work_type or "",
                "assignee": wo.assignee_name or "",
                "requester": wo.requester_name or "",
                "permit": _permit_label(wo),
                "status": STATUS_LABELS.get(status, status),
                "risk_grade": wo.risk_grade or "",
                "hazard": (wo.hazard_content or "").strip(),
                "safety": (wo.safety_measures or "").strip(),
            }
        )
    return out


def render_intro(template: str, *, partner_name: str, count: int, work_day: date, send_day: str) -> str:
    out = (template or "").strip()
    for key, val in (
        ("{협력사}", partner_name or "협력사"),
        ("{건수}", str(count)),
        ("{작업일}", day_label(work_day)),
        ("{날짜}", send_day),
    ):
        out = out.replace(key, val)
    return out


def build_mail(
    rows: list[dict], work_day: date, *, partner_name: str = "", intro: str = "", send_day: str = ""
) -> tuple[str, str]:
    intro_txt = render_intro(
        intro,
        partner_name=partner_name or "협력사",
        count=len(rows),
        work_day=work_day,
        send_day=send_day or fmt_kst(datetime.utcnow(), "%Y-%m-%d"),
    )
    return render_table_mail(
        rows,
        partner_name=partner_name,
        intro_txt=intro_txt,
        title=f"{'[' + partner_name + '] ' if partner_name else ''}{day_label(work_day)} 작업 예정 항목",
        meta=[("작업 예정일", day_label(work_day)), ("작업 건수", f"{len(rows)}건")],
        empty_msg="해당 날짜에 작업 예정인 정비가 없습니다.",
        columns=BODY_COLUMNS,
        footer=FOOTER,
    )


def _xlsx(rows: list[dict]) -> bytes:
    return build_xlsx(rows, XLSX_COLUMNS, sheet="작업 예정")


async def run_send(
    session: AsyncSession, *, test: bool = False, now_utc: datetime | None = None
) -> tuple[bool, str]:
    """설정대로 발송 → (실패 없음 여부, 안내 문구). test=True 이면 발송기록(중복 방지일)을 바꾸지 않음."""
    settings = await load_settings(session)
    sender = await load_sender(session)
    now = now_utc or datetime.utcnow()
    day = target_day(now)
    send_day = fmt_kst(now, "%Y-%m-%d")
    rows = await fetch_plans(session, day, settings["scope"])
    stamp = day.strftime("%Y%m%d")
    tag = "[테스트] " if test else ""
    label = day_label(day)

    sent: list[str] = []
    errors: list[str] = []
    skipped: list[str] = []

    combined_to, _ = parse_emails(settings["to"])
    if combined_to:
        if rows or settings["send_empty"]:
            data = _xlsx(rows)
            subject = f"{tag}[정비관리] {label} 작업 예정 항목 {len(rows)}건 (통합)"
            body, html = build_mail(rows, day, intro=settings["intro"], send_day=send_day)
            for addr in combined_to:
                err = await _deliver(sender, addr, subject, body, data, f"작업예정_{stamp}_통합.xlsx", html)
                if err:
                    errors.append(err)
                else:
                    sent.append(addr)
        else:
            skipped.append("통합: 작업 0건")

    if settings["per_partner"]:
        groups: dict[int, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["partner_id"], []).append(r)
        for pid, items in groups.items():
            name = items[0]["partner_name"]
            addrs, _ = parse_emails(settings["partner_to"].get(str(pid)) or items[0]["partner_email"])
            if not addrs:
                skipped.append(f"{name}: 메일 주소 없음")
                continue
            data = _xlsx(items)
            subject = f"{tag}[정비관리] {name} {label} 작업 예정 항목 {len(items)}건"
            body, html = build_mail(items, day, partner_name=name, intro=settings["intro"], send_day=send_day)
            safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name)
            for addr in addrs:
                err = await _deliver(sender, addr, subject, body, data, f"{safe}_작업예정_{stamp}.xlsx", html)
                if err:
                    errors.append(err)
                else:
                    sent.append(addr)

    no_target = not combined_to and not settings["per_partner"]
    if no_target:
        msg = "받는 메일 주소가 없습니다. 통합 수신 주소를 입력하거나 협력사별 발송을 켜세요."
    else:
        parts = [f"{tag}작업 예정일 {label} · {len(rows)}건"]
        if sent:
            parts.append(f"발송 {len(sent)}통 ({', '.join(sent[:10])}{' 외' if len(sent) > 10 else ''})")
        if skipped:
            parts.append("미발송: " + ", ".join(skipped))
        if errors:
            parts.append("실패: " + " / ".join(errors[:5]))
        if not sent and not errors and not skipped:
            parts.append("발송 대상 없음")
        msg = " · ".join(parts)

    if not test:
        await _set(session, "last_run_date", send_day)
    await _set(session, "last_result", msg[:1000])
    await _set(session, "last_result_at", fmt_kst(datetime.utcnow(), "%Y-%m-%d %H:%M:%S"))
    await session.commit()
    print(f"[maint-plan-mail] {msg}", flush=True)
    return (not errors and not no_target), msg


def _seconds_to_next_run(settings: dict, now_utc: datetime) -> float:
    now_k = now_utc.replace(tzinfo=timezone.utc).astimezone(KST)
    run = now_k.replace(hour=settings["hour"], minute=settings["minute"], second=0, microsecond=0)
    if run <= now_k:
        run += timedelta(days=1)
    return (run - now_k).total_seconds()


async def job_send(session_factory) -> None:
    async with session_factory() as session:
        settings = await load_settings(session)
        if not settings["enabled"]:
            return
        # 스케줄러가 몇 초 일찍 깨면 '내일' 날짜가 하루 어긋나므로 정각까지 대기
        early = _seconds_to_next_run(settings, datetime.utcnow())
        if 0 < early < 120:
            await asyncio.sleep(early + 1)
        today = datetime.now(KST).strftime("%Y-%m-%d")
        if settings["last_run_date"] == today:
            return
        await run_send(session)


async def reschedule(scheduler, session_factory) -> None:
    """설정을 읽어 매일 발송 작업을 등록/해제."""
    from apscheduler.triggers.cron import CronTrigger

    async with session_factory() as session:
        settings = await load_settings(session)
    try:
        scheduler.remove_job(JOB_ID)
    except Exception:
        pass
    if not settings["enabled"]:
        return
    scheduler.add_job(
        job_send,
        CronTrigger(hour=settings["hour"], minute=settings["minute"], timezone=KST),
        args=[session_factory],
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )


def next_run_label(scheduler) -> str:
    if scheduler is None:
        return ""
    try:
        job = scheduler.get_job(JOB_ID)
    except Exception:
        return ""
    if not job or not job.next_run_time:
        return ""
    return job.next_run_time.astimezone(KST).strftime("%Y-%m-%d %H:%M")
