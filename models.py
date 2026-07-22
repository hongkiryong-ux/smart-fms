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
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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


class Floor(Base):
    __tablename__ = "floors"

    id = Column(Integer, primary_key=True)
    building_id = Column(Integer, ForeignKey("buildings.id"), nullable=False)
    name = Column(String(100), nullable=False)
    level = Column(Integer, default=1)
    floor_plan_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)

    building = relationship("Building", back_populates="floors")
    zones = relationship("Zone", back_populates="floor", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("floors.id"), nullable=False)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)

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
    consumables = relationship("Consumable", back_populates="equipment")
    work_orders = relationship("WorkOrder", back_populates="equipment")
    maintenance_records = relationship("MaintenanceRecord", back_populates="equipment")


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
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False)
    title = Column(String(200), nullable=False)
    frequency = Column(Enum(PMFrequency), default=PMFrequency.monthly)
    custom_days = Column(Integer, nullable=True)
    checklist = Column(JSON, default=list)
    assignee_name = Column(String(100), nullable=True)
    next_due = Column(Date, nullable=True)
    last_done = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)

    equipment = relationship("Equipment", back_populates="pm_schedules")


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


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=True)
    qty = Column(Integer, default=0)
    safety_stock = Column(Integer, default=5)
    unit = Column(String(20), default="EA")
    location = Column(String(200), nullable=True)
    unit_price = Column(Float, nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)
