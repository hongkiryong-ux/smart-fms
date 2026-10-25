"""정비관리 화면의 계정별 표시 범위 (나의 협력사 · 내 건물)."""
from __future__ import annotations

import json

from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from models import AppSetting, Building, Equipment, Floor, Partner, Site, User, WorkOrder, Zone

DEFAULT_ALL_BUILDINGS_KEY = "my_buildings.default_all_v1"


def parse_id_set(raw) -> set[int]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out: set[int] = set()
    for v in raw or []:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


async def scoped_partner_ids(db: AsyncSession, user: User | None) -> set[int]:
    """정비접수 '나의 협력사' 중 사용 중인 협력사 id (비어 있으면 전체)."""
    ids = parse_id_set(getattr(user, "wo_partner_ids", None))
    if not ids:
        return set()
    rows = await db.execute(
        select(Partner.id).where(Partner.id.in_(ids), Partner.is_active == True)  # noqa: E712
    )
    return set(rows.scalars().all())


async def scoped_building_ids(db: AsyncSession, user: User | None) -> set[int]:
    """'내 건물'(점검·설비와 공유) 중 사용 중인 건물 id (비어 있으면 전체)."""
    ids = parse_id_set(getattr(user, "pm_building_ids", None))
    if not ids:
        return set()
    rows = await db.execute(
        select(Building.id)
        .join(Site, Building.site_id == Site.id)
        .where(
            Building.id.in_(ids),
            Building.is_active == True,  # noqa: E712
            Site.is_active == True,  # noqa: E712
        )
    )
    return set(rows.scalars().all())


def _active_building_ids_stmt():
    return (
        select(Building.id)
        .join(Site, Building.site_id == Site.id)
        .where(Building.is_active == True, Site.is_active == True)  # noqa: E712
    )


async def ensure_default_all_buildings(session: AsyncSession) -> None:
    """최초 1회: '내 건물'이 비어 있는 기존 계정은 사용 중인 건물 전체를 선택 상태로 둔다."""
    if await session.get(AppSetting, DEFAULT_ALL_BUILDINGS_KEY) is not None:
        return
    active = set((await session.execute(_active_building_ids_stmt())).scalars().all())
    if not active:
        return
    users = (await session.execute(select(User))).scalars().all()
    filled = 0
    for u in users:
        if parse_id_set(u.pm_building_ids) & active:
            continue
        u.pm_building_ids = sorted(active)
        filled += 1
    session.add(AppSetting(key=DEFAULT_ALL_BUILDINGS_KEY, value="1"))
    await session.commit()
    print(f"[startup] my buildings default-all applied to {filled} users", flush=True)


@event.listens_for(Session, "before_flush")
def _new_user_default_buildings(session, flush_context, instances):
    """새 계정은 사용 중인 건물 전체를 '내 건물'로 시작."""
    new_users = [
        o for o in session.new if isinstance(o, User) and not parse_id_set(o.pm_building_ids)
    ]
    if not new_users:
        return
    active = sorted(session.connection().execute(_active_building_ids_stmt()).scalars().all())
    if not active:
        return
    for u in new_users:
        u.pm_building_ids = list(active)


@event.listens_for(Session, "after_flush")
def _new_building_into_scopes(session, flush_context):
    """새 건물은 '내 건물'을 지정한 모든 계정에 기본 포함 (이후 해제는 계정 설정대로 유지)."""
    new_ids = {o.id for o in session.new if isinstance(o, Building) and o.id is not None}
    if not new_ids:
        return
    users_t = User.__table__
    conn = session.connection()
    rows = conn.execute(
        select(users_t.c.id, users_t.c.pm_building_ids).where(
            users_t.c.pm_building_ids.isnot(None)
        )
    ).all()
    changed: dict[int, list[int]] = session.info.setdefault("_scope_user_ids", {})
    for uid, raw in rows:
        ids = parse_id_set(raw)
        if not ids or new_ids <= ids:
            continue
        merged = sorted(ids | new_ids)
        conn.execute(
            update(users_t).where(users_t.c.id == uid).values(pm_building_ids=merged)
        )
        changed[uid] = merged


@event.listens_for(Session, "after_flush_postexec")
def _sync_scope_users_in_session(session, flush_context):
    changed = session.info.pop("_scope_user_ids", None)
    if not changed:
        return
    for key, obj in list(session.identity_map.items()):
        if not isinstance(obj, User):
            continue
        uid = key[1][0] if key[1] else None
        if uid in changed:
            set_committed_value(obj, "pm_building_ids", list(changed[uid]))


def wo_in_buildings(building_ids: set[int]):
    """정비 건의 설비가 지정 건물에 있는지 (설비 없는 건은 제외)."""
    zone_ids = (
        select(Zone.id)
        .join(Floor, Zone.floor_id == Floor.id)
        .where(Floor.building_id.in_(building_ids))
    )
    return WorkOrder.equipment_id.in_(
        select(Equipment.id).where(Equipment.zone_id.in_(zone_ids))
    )
