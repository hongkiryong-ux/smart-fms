"""정비관리 메뉴 신규 건수 배지 (사용자별 확인 시각 저장)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting, User, WorkOrder, WorkOrderStatus

KST = ZoneInfo("Asia/Seoul")

_WO_RECEIVED = (WorkOrderStatus.received,)
_WO_OPEN = (
    WorkOrderStatus.received,
    WorkOrderStatus.assigned,
    WorkOrderStatus.in_progress,
)


def _seen_key(user_id: int) -> str:
    return f"maint.nav_seen.{user_id}"


def _default_seen() -> dict:
    return {"work_orders": None, "facility": None, "d1_partners": {}}


def _parse_seen(raw: str | None) -> dict:
    base = _default_seen()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return base
    if not isinstance(data, dict):
        return base
    partners_raw = data.get("d1_partners") or {}
    partners: dict[str, str | None] = {}
    if isinstance(partners_raw, dict):
        for k, v in partners_raw.items():
            partners[str(k)] = v if v in (None, "") else str(v)
    return {
        "work_orders": data.get("work_orders") or None,
        "facility": data.get("facility") or None,
        "d1_partners": partners,
    }


def _serialize_seen(state: dict) -> str:
    return json.dumps(
        {
            "work_orders": state.get("work_orders"),
            "facility": state.get("facility"),
            "d1_partners": state.get("d1_partners") or {},
        },
        ensure_ascii=False,
    )


async def get_maint_seen(db: AsyncSession, user_id: int) -> dict:
    row = await db.get(AppSetting, _seen_key(user_id))
    if row and row.value:
        return _parse_seen(row.value)
    return _default_seen()


async def save_maint_seen(db: AsyncSession, user_id: int, state: dict) -> None:
    payload = _serialize_seen(state)
    key = _seen_key(user_id)
    row = await db.get(AppSetting, key)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=key, value=payload))


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_unseen(event_at: datetime | None, last_seen_iso: str | None) -> bool:
    if event_at is None:
        return False
    last_seen = _parse_iso(last_seen_iso)
    if last_seen is None:
        return True
    ev = event_at
    if ev.tzinfo is not None:
        ev = ev.astimezone(timezone.utc).replace(tzinfo=None)
    return ev > last_seen


def _today_kst() -> date:
    return datetime.now(KST).date()


def _wo_created_date_kst(wo: WorkOrder) -> date | None:
    dt = getattr(wo, "created_at", None)
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def _wo_approved_date_kst(wo: WorkOrder) -> date | None:
    dt = getattr(wo, "approved_at", None)
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def _wo_is_today_partner_receipt(wo: WorkOrder, today: date | None = None) -> bool:
    if not getattr(wo, "partner_id", None):
        return False
    if not getattr(wo, "d1_approved", False):
        return False
    key = wo.status.value if isinstance(wo.status, WorkOrderStatus) else str(wo.status)
    if key in ("completed", "verified", "closed"):
        return False
    day = today or _today_kst()
    appr_day = _wo_approved_date_kst(wo)
    if appr_day is not None:
        return appr_day == day
    return _wo_created_date_kst(wo) == day


def _receipt_event_at(wo: WorkOrder) -> datetime | None:
    dt = getattr(wo, "approved_at", None) or getattr(wo, "created_at", None)
    return dt if isinstance(dt, datetime) else None


async def mark_maint_seen(
    db: AsyncSession,
    user_id: int,
    section: str,
    *,
    partner_id: int | None = None,
) -> None:
    state = await get_maint_seen(db, user_id)
    now = _now_iso()
    if section == "work_orders":
        state["work_orders"] = now
    elif section == "facility":
        state["facility"] = now
    elif section == "d1_partner" and partner_id:
        partners = dict(state.get("d1_partners") or {})
        partners[str(partner_id)] = now
        state["d1_partners"] = partners
    await save_maint_seen(db, user_id, state)


async def compute_maint_badges(db: AsyncSession, user: User | None) -> dict:
    from auth import can_access_menu

    empty = {
        "total": 0,
        "work_orders": 0,
        "facility": 0,
        "d1_by_partner": {},
    }
    if user is None:
        return empty

    seen = await get_maint_seen(db, user.id)
    today = _today_kst()

    work_orders_count = 0
    if can_access_menu(user, "work_orders"):
        wo_rows = (
            await db.execute(
                select(WorkOrder).where(
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(_WO_RECEIVED),
                )
            )
        ).scalars().all()
        for wo in wo_rows:
            if _is_unseen(getattr(wo, "created_at", None), seen.get("work_orders")):
                work_orders_count += 1

    facility_count = 0
    if can_access_menu(user, "facility_section"):
        fac_rows = (
            await db.execute(
                select(WorkOrder).where(
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(_WO_OPEN),
                    WorkOrder.partner_id.is_not(None),
                    WorkOrder.approval_requested.is_(True),
                    WorkOrder.work_permitted.is_(False),
                )
            )
        ).scalars().all()
        for wo in fac_rows:
            event_at = getattr(wo, "approval_requested_at", None) or getattr(
                wo, "created_at", None
            )
            if _is_unseen(event_at, seen.get("facility")):
                facility_count += 1

    d1_by_partner: dict[int, int] = {}
    d1_total = 0
    if can_access_menu(user, "d1"):
        open_rows = (
            await db.execute(
                select(WorkOrder).where(
                    WorkOrder.is_active == True,  # noqa: E712
                    WorkOrder.status.in_(_WO_OPEN),
                    WorkOrder.partner_id.is_not(None),
                    WorkOrder.d1_approved.is_(True),
                )
            )
        ).scalars().all()
        partner_seen = seen.get("d1_partners") or {}
        for wo in open_rows:
            if not _wo_is_today_partner_receipt(wo, today):
                continue
            pid = int(wo.partner_id or 0)
            if pid <= 0:
                continue
            last = partner_seen.get(str(pid))
            if _is_unseen(_receipt_event_at(wo), last):
                d1_by_partner[pid] = d1_by_partner.get(pid, 0) + 1
                d1_total += 1

    total = work_orders_count + facility_count + d1_total
    return {
        "total": total,
        "work_orders": work_orders_count,
        "facility": facility_count,
        "d1_by_partner": d1_by_partner,
    }
