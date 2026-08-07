# streetlamp/models.py — 가로등 QR 정비의뢰 (기존 streetlamp_qr 테이블·ENUM명 유지)
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class RequestStatus(str, enum.Enum):
    received = "received"
    in_progress = "in_progress"
    done = "done"


class RequestType(str, enum.Enum):
    outage = "outage"
    globe_broken = "globe_broken"
    fall_risk = "fall_risk"
    low_brightness = "low_brightness"
    other = "other"


class Lamp(Base):
    __tablename__ = "lamps"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, index=True, nullable=True)
    location = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    requests = relationship("MaintenanceRequest", back_populates="lamp")


class MaintenanceRequest(Base):
    """기존 streetlamp_qr.maintenance_requests 와 동일 스키마·ENUM 타입명."""

    __tablename__ = "maintenance_requests"

    id = Column(Integer, primary_key=True, index=True)
    lamp_id = Column(Integer, ForeignKey("lamps.id"))
    name = Column(String(100), nullable=False)
    phone = Column(String(50), nullable=False)
    request_type = Column(Enum(RequestType, name="requesttype", native_enum=True), nullable=False)
    content = Column(Text, nullable=True)
    status = Column(
        Enum(RequestStatus, name="requeststatus", native_enum=True),
        default=RequestStatus.received,
    )
    work_memo = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    lamp = relationship("Lamp", back_populates="requests")


# 문서/검색용 alias
StreetlampRequest = MaintenanceRequest
StreetlampRequestStatus = RequestStatus
StreetlampRequestType = RequestType
