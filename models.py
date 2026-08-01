# models.py
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import relationship

from database import Base


class UserRole(str, enum.Enum):
    system_admin = "system_admin"
    site_admin = "site_admin"
    group_leader = "group_leader"
    part_leader = "part_leader"
    facility_manager = "facility_manager"
    partner = "partner"
    external = "external"
    viewer = "viewer"


class WorkOrderStatus(str, enum.Enum):
    received = "received"
    assigned = "assigned"
    in_progress = "in_progress"
    completed = "completed"
    verified = "verified"
    closed = "closed"


class PMFrequency(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"
    semi_annual = "semi_annual"
    annual = "annual"
    custom = "custom"


class PMResult(str, enum.Enum):
    normal = "normal"  # 정상
    caution = "caution"  # 주의
    fault = "fault"  # 고장


class D1Status(str, enum.Enum):
    draft = "draft"
    review = "review"
    approved = "approved"
    jsa_pending = "jsa_pending"
    tbm_pending = "tbm_pending"
    permit_pending = "permit_pending"
    in_progress = "in_progress"
    completed = "completed"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.viewer)
    phone = Column(String(50), nullable=True)
    email = Column(String(120), nullable=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=True)
    # 협력사 목록에 없을 때 직접 입력한 회사명 (목록 선택 시에도 표시용으로 동기화)
    company_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    # 직원 가입 후 관리자 승인 전 False
    is_approved = Column(Boolean, default=True)
    # 계정별 권한 (시스템관리자는 검사 시 항상 허용)
    can_create = Column(Boolean, default=True)
    can_edit = Column(Boolean, default=True)
    can_delete = Column(Boolean, default=True)
    # 메인 메뉴 접근 키 목록 (null이면 역할 기본값 사용)
    menu_access = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # 위험성평가 AI — 계정별 개인 키 (다른 사용자와 공유하지 않음)
    openai_api_key = Column(String(200), nullable=True)
    openai_model = Column(String(80), nullable=True)

    partner = relationship("Partner")

    @property
    def company_display(self) -> str:
        if self.partner is not None and getattr(self.partner, "name", None):
            return self.partner.name
        return (self.company_name or "").strip()


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    address = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    manager_name = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    buildings = relationship("Building", back_populates="site", cascade="all, delete-orphan")


class Building(Base):
    __tablename__ = "buildings"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    name = Column(String(200), nullable=False)
    code = Column(String(50), nullable=False)
    gps_lat = Column(Float, nullable=True)
    gps_lng = Column(Float, nullable=True)
    manager_name = Column(String(100), nullable=True)
    floor_plan_url = Column(String(500), nullable=True)
    photo_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)

    site = relationship("Site", back_populates="buildings")
    floors = relationship("Floor", back_populates="building", cascade="all, delete-orphan")
    drawings = relationship(
        "BuildingDrawing", back_populates="building", cascade="all, delete-orphan"
    )
    standards = relationship(
        "BuildingStandard", back_populates="building", cascade="all, delete-orphan"
    )
    inspection_log_files = relationship(
        "InspectionLogFile", back_populates="building", cascade="all, delete-orphan"
    )


class BuildingDrawing(Base):
    """건물 도면 첨부 파일."""

    __tablename__ = "building_drawings"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    original_name = Column(String(300), nullable=True)
    stored_name = Column(String(300), nullable=False)
    content_type = Column(String(100), nullable=True)
    # Render 등에서 디스크가 휘발성이라 DB에도 본문 보관
    file_data = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    building = relationship("Building", back_populates="drawings")
    floor = relationship("Floor")

    @property
    def url(self) -> str:
        return f"/admin/buildings/{self.building_id}/drawings/{self.id}/file"

    @property
    def is_image(self) -> bool:
        ct = (self.content_type or "").lower()
        name = (self.original_name or self.stored_name or "").lower()
        if ct.startswith("image/"):
            return True
        return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))

    @property
    def is_pdf(self) -> bool:
        ct = (self.content_type or "").lower()
        name = (self.original_name or self.stored_name or "").lower()
        return ct == "application/pdf" or name.endswith(".pdf")


class BuildingStandard(Base):
    """건물 표준서 첨부 파일."""

    __tablename__ = "building_standards"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    original_name = Column(String(300), nullable=True)
    stored_name = Column(String(300), nullable=False)
    content_type = Column(String(100), nullable=True)
    file_data = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    building = relationship("Building", back_populates="standards")

    @property
    def url(self) -> str:
        return f"/admin/buildings/{self.building_id}/standards/{self.id}/file"

    @property
    def is_pdf(self) -> bool:
        ct = (self.content_type or "").lower()
        name = (self.original_name or self.stored_name or "").lower()
        return ct == "application/pdf" or name.endswith(".pdf")


class InspectionLogBuilding(Base):
    """점검일지에 등록된 건물."""

    __tablename__ = "inspection_log_buildings"

    id = Column(Integer, primary_key=True)
    building_id = Column(
        Integer, ForeignKey("buildings.id"), nullable=False, unique=True, index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)

    building = relationship("Building")


class InspectionLogFile(Base):
    """점검일지 엑셀 파일."""

    __tablename__ = "inspection_log_files"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    original_name = Column(String(300), nullable=True)
    stored_name = Column(String(300), nullable=False)
    content_type = Column(String(100), nullable=True)
    file_data = Column(LargeBinary, nullable=True)
    uploaded_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # 마지막 편집 위치 {sheet, sheetIndex, cell, x, y}
    last_edit_pos = Column(JSON, nullable=True)

    building = relationship("Building", back_populates="inspection_log_files")

    @property
    def url(self) -> str:
        return f"/admin/inspection-logs/{self.building_id}/files/{self.id}/file"


class Floor(Base):
    __tablename__ = "floors"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)
    name = Column(String(100), nullable=False)
    level = Column(Integer, default=1)
    floor_plan_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)

    building = relationship("Building", back_populates="floors")
    zones = relationship("Zone", back_populates="floor", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=False)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)

    floor = relationship("Floor", back_populates="zones")
    equipment = relationship("Equipment", back_populates="zone", cascade="all, delete-orphan")


class EquipmentCategory(str, enum.Enum):
    """건물 내 설비 대분류."""
    facility = "설비"
    electrical = "전기"
    civil = "토건"


class EquipmentType(Base):
    __tablename__ = "equipment_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    category = Column(String(20), default="설비", nullable=False)  # 설비/전기/토건
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=True)

    templates = relationship("EquipmentTemplate", back_populates="equipment_type")


class EquipmentTemplate(Base):
    __tablename__ = "equipment_templates"

    id = Column(Integer, primary_key=True)
    equipment_type_id = Column(Integer, ForeignKey("equipment_types.id"), nullable=False)
    name = Column(String(200), nullable=False)
    manufacturer = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    pm_items = Column(JSON, default=list)
    consumables = Column(JSON, default=list)
    plc_tags = Column(JSON, default=list)
    pm_cycle_days = Column(Integer, default=30)
    manual_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)

    equipment_type = relationship("EquipmentType", back_populates="templates")


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True)
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=False)
    equipment_type_id = Column(Integer, ForeignKey("equipment_types.id"), nullable=True)
    template_id = Column(Integer, ForeignKey("equipment_templates.id"), nullable=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(50), default="기타", nullable=False, index=True)  # 엑셀 시트명
    manufacturer = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    serial_no = Column(String(100), nullable=True)
    installed_at = Column(Date, nullable=True)
    manager_name = Column(String(100), nullable=True)
    plc_tag = Column(String(200), nullable=True)
    running_hours = Column(Float, default=0)
    nfc_tag = Column(String(100), nullable=True)
    status = Column(String(50), default="normal")
    manual_url = Column(String(500), nullable=True)
    photo_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    extra_data = Column(JSON, default=dict)  # 시트별 추가 컬럼

    zone = relationship("Zone", back_populates="equipment")
    equipment_type = relationship("EquipmentType")
    template = relationship("EquipmentTemplate")
    pm_schedules = relationship("PMSchedule", back_populates="equipment")
    pm_inspections = relationship("PMInspection", back_populates="equipment")
    consumables = relationship("Consumable", back_populates="equipment")
    work_orders = relationship("WorkOrder", back_populates="equipment")
    maintenance_records = relationship("MaintenanceRecord", back_populates="equipment")
    change_logs = relationship(
        "EquipmentChangeLog",
        back_populates="equipment",
        order_by="EquipmentChangeLog.changed_at.desc()",
    )


class EquipmentChangeLog(Base):
    """설비 사양(엑셀양식) 수정 로그."""

    __tablename__ = "equipment_change_logs"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False, index=True)
    changed_by = Column(String(100), nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow, index=True)
    summary = Column(String(300), nullable=False, default="")
    changes = Column(JSON, default=list)  # [{field, old, new}, ...]

    equipment = relationship("Equipment", back_populates="change_logs")


class MaintenanceRecord(Base):
    """설비 정비이력 (정비완료 자동등록 + 수동등록)."""

    __tablename__ = "maintenance_records"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False, index=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=True)
    title = Column(String(300), nullable=False)
    work_date = Column(Date, nullable=False)
    worker_name = Column(String(100), nullable=True)
    cause = Column(Text, nullable=True)
    action = Column(Text, nullable=True)
    parts_used = Column(Text, nullable=True)
    work_hours = Column(Float, nullable=True)
    cost = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    is_manual = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    equipment = relationship("Equipment", back_populates="maintenance_records")
    work_order = relationship("WorkOrder")


class PMSchedule(Base):
    __tablename__ = "pm_schedules"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    frequency = Column(Enum(PMFrequency), default=PMFrequency.monthly)
    custom_days = Column(Integer, nullable=True)
    checklist = Column(JSON, default=list)
    assignee_name = Column(String(100), nullable=True)
    next_due = Column(Date, nullable=True)
    last_done = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)

    equipment = relationship("Equipment", back_populates="pm_schedules")
    inspections = relationship("PMInspection", back_populates="schedule")


class PMInspection(Base):
    """예방점검 결과 기록."""

    __tablename__ = "pm_inspections"

    id = Column(Integer, primary_key=True)
    schedule_id = Column(Integer, ForeignKey("pm_schedules.id"), nullable=False, index=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False, index=True)
    result = Column(Enum(PMResult), nullable=False, default=PMResult.normal)
    note = Column(Text, nullable=True)
    inspector_name = Column(String(100), nullable=True)
    inspected_at = Column(DateTime, default=datetime.utcnow)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=True)

    schedule = relationship("PMSchedule", back_populates="inspections")
    equipment = relationship("Equipment", back_populates="pm_inspections")
    work_order = relationship("WorkOrder")


class Consumable(Base):
    __tablename__ = "consumables"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False)
    name = Column(String(200), nullable=False)
    replace_criteria = Column(String(50), default="date")
    replace_interval_days = Column(Integer, nullable=True)
    replace_interval_hours = Column(Float, nullable=True)
    last_replaced = Column(Date, nullable=True)
    next_replace = Column(Date, nullable=True)
    stock_qty = Column(Integer, default=0)
    safety_stock = Column(Integer, default=1)

    equipment = relationship("Equipment", back_populates="consumables")


class Partner(Base):
    __tablename__ = "partners"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    contact_name = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(120), nullable=True)
    contract_end = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(WorkOrderStatus), default=WorkOrderStatus.received)
    priority = Column(String(20), default="normal")
    assignee_name = Column(String(100), nullable=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=True)
    work_type = Column(String(100), nullable=True)
    cause = Column(Text, nullable=True)
    action = Column(Text, nullable=True)
    parts_used = Column(Text, nullable=True)
    cost = Column(Float, nullable=True)
    work_hours = Column(Float, nullable=True)
    scheduled_date = Column(Date, nullable=True)  # 정비 예정일
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    equipment = relationship("Equipment", back_populates="work_orders")
    partner = relationship("Partner")


class D1Plan(Base):
    __tablename__ = "d1_plans"

    id = Column(Integer, primary_key=True)
    work_date = Column(Date, nullable=False)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    title = Column(String(300), nullable=False)
    work_content = Column(Text, nullable=True)
    work_time = Column(String(100), nullable=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=True)
    worker_count = Column(Integer, default=1)
    is_urgent = Column(Boolean, default=False)
    status = Column(Enum(D1Status), default=D1Status.draft)
    jsa_data = Column(JSON, default=dict)
    tbm_data = Column(JSON, default=dict)
    permit_data = Column(JSON, default=dict)
    permit_no = Column(String(50), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    site = relationship("Site")
    building = relationship("Building")
    equipment = relationship("Equipment")
    partner = relationship("Partner")


class MaterialItem(Base):
    """자재관리(원본 inventory 앱) — 품명 단위 재고."""

    __tablename__ = "material_items"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), unique=True, nullable=False)
    quantity = Column(Integer, default=0)
    spec = Column(String(300), nullable=True)
    remarks = Column(Text, nullable=True)
    group_name = Column(String(100), nullable=False, default="소모품")
    location = Column(String(200), nullable=True)  # 저장 위치
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MaterialGroup(Base):
    """자재 그룹 (동적 추가/삭제/이름변경)."""

    __tablename__ = "material_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)


class MaterialLog(Base):
    """자재 입고/출고/삭제 로그."""

    __tablename__ = "material_logs"

    id = Column(Integer, primary_key=True)
    action = Column(String(50), nullable=False)  # 입고, 출고, 삭제, 등록, 초기화
    name = Column(String(200), nullable=False)
    quantity = Column(Integer, default=0)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)
