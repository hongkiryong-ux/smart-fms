"""정비관리 화면의 계정별 표시 범위 (나의 협력사 · 내 건물)."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Building, Equipment, Floor, Partner, Site, User, WorkOrder, Zone


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
