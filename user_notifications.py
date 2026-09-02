# user_notifications.py — 사용자 개인 알림
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, UserNotification, WorkOrder


def _person_label(user: User) -> str:
    name = (getattr(user, "name", None) or "").strip()
    uname = (getattr(user, "username", None) or "").strip()
    if name and uname and name != uname:
        return f"{name} ({uname})"
    return name or uname or f"user-{user.id}"


async def resolve_requester_user_id(db: AsyncSession, wo: WorkOrder) -> int | None:
    """정비의뢰 접수자 user_id (없으면 이름으로 매칭)."""
    rid = getattr(wo, "requester_user_id", None)
    if rid:
        return int(rid)
    label = (wo.requester_name or wo.assignee_name or "").strip()
    if not label:
        return None
    rows = (
        await db.execute(
            select(User).where(
                User.is_active == True,  # noqa: E712
                User.is_approved == True,  # noqa: E712
            )
        )
    ).scalars().all()
    folded = label.casefold()
    for u in rows:
        if _person_label(u).casefold() == folded:
            return int(u.id)
        if (u.name or "").strip().casefold() == folded:
            return int(u.id)
        if (u.username or "").strip().casefold() == folded:
            return int(u.id)
    return None


async def notify_work_order_rejected(
    db: AsyncSession,
    wo: WorkOrder,
    *,
    reason: str,
    rejected_by: str,
) -> UserNotification | None:
    uid = await resolve_requester_user_id(db, wo)
    if not uid:
        return None
    desc = (wo.description or wo.title or "").strip()
    if len(desc) > 120:
        desc = desc[:117] + "..."
    body = f"정비의뢰 #{wo.id}이(가) 반려되었습니다.\n\n반려 사유:\n{reason.strip()}"
    if desc:
        body = f"정비의뢰 #{wo.id} — {desc}\n\n반려 사유:\n{reason.strip()}"
    note = UserNotification(
        user_id=uid,
        work_order_id=wo.id,
        kind="wo_rejected",
        title=f"정비의뢰 반려 ({rejected_by})",
        body=body,
        is_read=False,
    )
    db.add(note)
    return note


async def fetch_unread_notifications(
    db: AsyncSession, user_id: int, *, limit: int = 10
) -> list[UserNotification]:
    return list(
        (
            await db.execute(
                select(UserNotification)
                .where(
                    UserNotification.user_id == user_id,
                    UserNotification.is_read == False,  # noqa: E712
                )
                .order_by(UserNotification.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    )


async def mark_notification_read(
    db: AsyncSession, notification_id: int, user_id: int
) -> bool:
    note = await db.get(UserNotification, notification_id)
    if not note or note.user_id != user_id:
        return False
    note.is_read = True
    return True
