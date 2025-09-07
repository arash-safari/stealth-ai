from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum as PyEnum
import ssl
from sqlalchemy.pool import NullPool

from sqlalchemy import (
    Text,
    Enum as SAEnum,
    ForeignKey,
    UniqueConstraint,
    Boolean,
    Index,
    BigInteger,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.schema import Identity
from sqlalchemy.engine.url import make_url, URL
from db.session import engine

class Base(DeclarativeBase):
    pass

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ---------- Enums ----------
class AppointmentStatus(str, PyEnum):
    scheduled = "scheduled"
    completed = "completed"
    canceled = "canceled"

class RequestPriority(str, PyEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

class CallSender(str, PyEnum):
    user = "user"
    agent = "agent"
    system = "system"

# ---------- Models ----------
class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("phone", name="uq_users_phone"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)

class Address(Base):
    __tablename__ = "addresses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[Optional[str]] = mapped_column(Text)
    line1: Mapped[str] = mapped_column(Text)
    line2: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(Text)
    postal_code: Mapped[Optional[str]] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(SADateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(SADateTime(timezone=True), default=utcnow, onupdate=utcnow)

Index(
    "uq_default_address_per_user",
    Address.user_id,
    unique=True,
    postgresql_where=(Address.is_default == True),  # type: ignore
)

class Tech(Base):
    __tablename__ = "techs"
    __table_args__ = (UniqueConstraint("code", name="uq_techs_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[Optional[str]] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="America/Los_Angeles")
    active: Mapped[bool] = mapped_column(default=True)
    google_calendar_id: Mapped[Optional[str]] = mapped_column(Text)

class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("name", name="uq_skills_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)

class TechSkill(Base):
    __tablename__ = "tech_skills"
    tech_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("techs.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)

class TechShift(Base):
    __tablename__ = "tech_shifts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tech_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("techs.id", ondelete="CASCADE"))
    start_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))

class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("appointment_no", name="uq_appointments_appointment_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Public, user-facing number (0,1,2,...)
    appointment_no: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=0, minvalue=0),
        nullable=False,
        index=True,
        unique=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    tech_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("techs.id", ondelete="CASCADE"))
    start_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    priority: Mapped[RequestPriority] = mapped_column(SAEnum(RequestPriority, name="request_priority", create_constraint=False), default=RequestPriority.P3)
    status: Mapped[AppointmentStatus] = mapped_column(SAEnum(AppointmentStatus, name="appointment_status", create_constraint=False), default=AppointmentStatus.scheduled)
    request_text: Mapped[Optional[str]] = mapped_column(Text)
    phone_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    address_line1_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    address_line2_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    city_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    state_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    postal_code_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    google_event_id: Mapped[Optional[str]] = mapped_column(Text)
    hangout_link: Mapped[Optional[str]] = mapped_column(Text)

class Hold(Base):
    __tablename__ = "holds"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tech_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("techs.id", ondelete="CASCADE"))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    start_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(SADateTime(timezone=True))
    request_text: Mapped[Optional[str]] = mapped_column(Text)

class Call(Base):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    phone: Mapped[Optional[str]] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text, default="phone")
    issue_category: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(SADateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(SADateTime(timezone=True))

class CallMessage(Base):
    __tablename__ = "call_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"))
    sender: Mapped[CallSender] = mapped_column(SAEnum(CallSender, name="call_sender", create_constraint=False))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(SADateTime(timezone=True), default=utcnow)

Index("ix_tech_shifts_tech_range", TechShift.tech_id, TechShift.start_ts, TechShift.end_ts)
Index("ix_appointments_tech_range", Appointment.tech_id, Appointment.start_ts, Appointment.end_ts)
Index("ix_holds_tech_range", Hold.tech_id, Hold.start_ts, Hold.end_ts)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

__all__ = [
    "engine",
    "Session",
    "Base",
    "User",
    "Address",
    "Tech",
    "Skill",
    "TechSkill",
    "TechShift",
    "Appointment",
    "Hold",
    "Call",
    "CallMessage",
    "AppointmentStatus",
    "RequestPriority",
    "CallSender",
    "init_db",
]
