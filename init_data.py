# init_data.py
"""초기 데모 데이터 생성."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import hash_password
from models import (
    Building,
    Consumable,
    D1Plan,
    D1Status,
    Equipment,
    EquipmentTemplate,
    EquipmentType,
    Floor,
    InventoryItem,
    Partner,
    PMFrequency,
    PMSchedule,
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderStatus,
    Zone,
)


async def seed_if_empty(session: AsyncSession) -> None:
    existing = await session.execute(select(Site).limit(1))
    if existing.scalar_one_or_none():
        await ensure_category_demo(session)
        return

    admin = User(
        username="admin",
        password_hash=hash_password("password123"),
        name="시스템관리자",
        role=UserRole.system_admin,
        email="admin@poscowide.com",
    )
    facility = User(
        username="facility",
        password_hash=hash_password("password123"),
        name="김시설",
        role=UserRole.facility_manager,
        phone="010-1234-5678",
    )
    session.add_all([admin, facility])

    partner = Partner(
        name="(주)광양설비",
        code="GY-001",
        contact_name="이협력",
        phone="010-9876-5432",
        email="partner@example.com",
        contract_end=date.today() + timedelta(days=365),
    )
    session.add(partner)
    await session.flush()

    site = Site(
        name="광양제철소",
        code="GY",
        address="전라남도 광양시",
        latitude=34.9408,
        longitude=127.6956,
        manager_name="박운영",
    )
    session.add(site)
    await session.flush()

    building = Building(
        site_id=site.id,
        name="동력발전동",
        code="BLD-A",
        manager_name="최동력",
    )
    session.add(building)
    await session.flush()

    floor = Floor(building_id=building.id, name="1층", level=1)
    session.add(floor)
    await session.flush()

    zone = Zone(floor_id=floor.id, name="기계실", code="Z-MR")
    session.add(zone)
    await session.flush()

    eq_type_ahu = EquipmentType(name="공조기(AHU)", category="설비", icon="hvac")
    eq_type_pump = EquipmentType(name="펌프", category="설비", icon="pump")
    eq_type_panel = EquipmentType(name="배전반", category="전기", icon="elec")
    eq_type_light = EquipmentType(name="조명", category="전기", icon="light")
    eq_type_door = EquipmentType(name="출입문", category="토건", icon="door")
    eq_type_floor = EquipmentType(name="바닥/도장", category="토건", icon="civil")
    session.add_all(
        [eq_type_ahu, eq_type_pump, eq_type_panel, eq_type_light, eq_type_door, eq_type_floor]
    )
    await session.flush()

    ahu_template = EquipmentTemplate(
        equipment_type_id=eq_type_ahu.id,
        name="표준 AHU 템플릿",
        manufacturer="삼성",
        model="AHU-5000",
        pm_items=[
            "필터 상태 점검",
            "벨트 장력 확인",
            "팬 모터 소음 확인",
            "냉매 온도 측정",
        ],
        consumables=[
            {"name": "필터", "interval_days": 90},
            {"name": "V벨트", "interval_days": 180},
            {"name": "베어링", "interval_hours": 8000},
        ],
        plc_tags=["AHU.RUN", "AHU.ALARM", "AHU.TEMP"],
        pm_cycle_days=30,
    )
    session.add(ahu_template)
    await session.flush()

    equipment_list = [
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_ahu.id,
            template_id=ahu_template.id,
            code="AHU-001",
            name="1호 공조기",
            category="설비",
            manufacturer="삼성",
            model="AHU-5000",
            serial_no="SN-2024-001",
            installed_at=date(2022, 3, 15),
            manager_name="김시설",
            plc_tag="AHU001.RUN",
            running_hours=12500,
            status="normal",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_ahu.id,
            template_id=ahu_template.id,
            code="AHU-002",
            name="2호 공조기",
            category="설비",
            manufacturer="삼성",
            model="AHU-5000",
            serial_no="SN-2024-002",
            installed_at=date(2022, 3, 15),
            manager_name="김시설",
            plc_tag="AHU002.RUN",
            running_hours=11800,
            status="warning",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_pump.id,
            code="PUMP-001",
            name="냉각수 펌프",
            category="설비",
            manufacturer="그랜포스",
            model="CR-32",
            manager_name="김시설",
            status="normal",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_panel.id,
            code="EL-PANEL-01",
            name="1층 주배전반",
            category="전기",
            manufacturer="LS산전",
            model="GIPAM",
            manager_name="이전기",
            status="normal",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_light.id,
            code="EL-LED-01",
            name="기계실 LED 조명",
            category="전기",
            manufacturer="삼성",
            model="LED-50W",
            manager_name="이전기",
            status="normal",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_door.id,
            code="CV-DOOR-01",
            name="기계실 방화문",
            category="토건",
            manufacturer="현대도어",
            model="FD-120",
            manager_name="박토건",
            status="normal",
        ),
        Equipment(
            zone_id=zone.id,
            equipment_type_id=eq_type_floor.id,
            code="CV-FLOOR-01",
            name="기계실 에폭시 바닥",
            category="토건",
            manager_name="박토건",
            status="normal",
        ),
    ]
    session.add_all(equipment_list)
    await session.flush()

    for eq in equipment_list[:2]:
        session.add(
            PMSchedule(
                equipment_id=eq.id,
                title=f"{eq.name} 정기점검",
                frequency=PMFrequency.monthly,
                checklist=ahu_template.pm_items,
                assignee_name="김시설",
                next_due=date.today() + timedelta(days=7),
            )
        )
        session.add(
            Consumable(
                equipment_id=eq.id,
                name="필터",
                replace_criteria="date",
                replace_interval_days=90,
                last_replaced=date.today() - timedelta(days=75),
                next_replace=date.today() + timedelta(days=15),
                stock_qty=3,
                safety_stock=2,
            )
        )

    session.add_all(
        [
            WorkOrder(
                equipment_id=equipment_list[1].id,
                site_id=site.id,
                title="2호 공조기 소음 점검",
                description="팬 모터 이상 소음 발생",
                status=WorkOrderStatus.in_progress,
                priority="high",
                assignee_name="이협력",
                partner_id=partner.id,
            ),
            WorkOrder(
                equipment_id=equipment_list[0].id,
                site_id=site.id,
                title="1호 공조기 필터 교체",
                status=WorkOrderStatus.received,
                priority="normal",
                assignee_name="김시설",
            ),
        ]
    )

    session.add(
        D1Plan(
            work_date=date.today() + timedelta(days=1),
            site_id=site.id,
            building_id=building.id,
            equipment_id=equipment_list[1].id,
            title="2호 공조기 벨트 교체",
            work_content="V벨트 교체 및 장력 조정",
            work_time="09:00-12:00",
            partner_id=partner.id,
            worker_count=2,
            status=D1Status.approved,
            created_by="김시설",
            jsa_data={
                "hazards": ["감전", "협착", "고소작업"],
                "controls": ["LOTO 적용", "안전대 착용", "2인 1조"],
            },
            tbm_data={
                "ppe": ["안전모", "안전화", "장갑"],
                "tools_checked": True,
                "briefing_done": False,
            },
            permit_data={"type": "일반작업", "approved": False},
        )
    )

    session.add_all(
        [
            InventoryItem(code="FLT-AHU", name="AHU 필터", category="소모품", qty=10, safety_stock=5),
            InventoryItem(code="BELT-V", name="V벨트 B형", category="소모품", qty=4, safety_stock=2),
            InventoryItem(code="BRG-6205", name="베어링 6205", category="부품", qty=8, safety_stock=3),
        ]
    )

    await session.commit()
    print("[seed] demo data created", flush=True)


async def ensure_category_demo(session: AsyncSession) -> None:
    """기존 DB에 설비/전기/토건 구분 및 샘플 보강."""
    from sqlalchemy import func, update

    # 기존 설비에 category 기본값
    await session.execute(
        update(Equipment).where(Equipment.category.is_(None)).values(category="설비")
    )
    await session.execute(
        update(EquipmentType).where(EquipmentType.category.is_(None)).values(category="설비")
    )

    # 전기/토건 샘플이 없으면 추가
    elec_count = (
        await session.execute(
            select(func.count(Equipment.id)).where(Equipment.category == "전기")
        )
    ).scalar() or 0
    if elec_count > 0:
        await session.commit()
        return

    zone = (await session.execute(select(Zone).limit(1))).scalar_one_or_none()
    if not zone:
        await session.commit()
        return

    async def _get_or_create_type(name: str, category: str) -> EquipmentType:
        row = (
            await session.execute(select(EquipmentType).where(EquipmentType.name == name))
        ).scalar_one_or_none()
        if row:
            row.category = category
            return row
        t = EquipmentType(name=name, category=category)
        session.add(t)
        await session.flush()
        return t

    t_panel = await _get_or_create_type("배전반", "전기")
    t_light = await _get_or_create_type("조명", "전기")
    t_door = await _get_or_create_type("출입문", "토건")
    t_floor = await _get_or_create_type("바닥/도장", "토건")

    # AHU/펌프 타입도 설비로 표시
    for name in ("공조기(AHU)", "펌프"):
        row = (
            await session.execute(select(EquipmentType).where(EquipmentType.name == name))
        ).scalar_one_or_none()
        if row:
            row.category = "설비"

    await session.execute(
        update(Equipment)
        .where(Equipment.category == "설비")
        .values(category="설비")
    )

    samples = [
        ("EL-PANEL-01", "1층 주배전반", "전기", t_panel.id, "LS산전", "GIPAM"),
        ("EL-LED-01", "기계실 LED 조명", "전기", t_light.id, "삼성", "LED-50W"),
        ("CV-DOOR-01", "기계실 방화문", "토건", t_door.id, "현대도어", "FD-120"),
        ("CV-FLOOR-01", "기계실 에폭시 바닥", "토건", t_floor.id, None, None),
    ]
    for code, name, cat, type_id, mfr, model in samples:
        exists = (
            await session.execute(select(Equipment).where(Equipment.code == code))
        ).scalar_one_or_none()
        if exists:
            exists.category = cat
            continue
        session.add(
            Equipment(
                zone_id=zone.id,
                equipment_type_id=type_id,
                code=code,
                name=name,
                category=cat,
                manufacturer=mfr,
                model=model,
                status="normal",
            )
        )

    await session.commit()
    print("[seed] category demo upgraded", flush=True)
