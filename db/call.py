# db/models.py
from __future__ import annotations
import os
import uuid
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, String, Text, DateTime, Enum as SAEnum, ForeignKey, JSON, Index
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --- Choose DB via env; default to sqlite+aiosqlite to avoid extra deps in tests ---
DB_URL = os.getenv("DB_URL", "sqlite+aiosqlite:///./plumber_calls.db")

# --- Base / Engine / Session ---
class Base(AsyncAttrs, DeclarativeBase):
    pass

engine = create_async_engine(DB_URL, future=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

# --- Enums ---
class CallSender(str, Enum):
    user = "user"
    agent = "agent"
    system = "system"

# --- Models ---
def _uuid_pk() -> uuid.UUID:
    return uuid.uuid4()

class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True) if DB_URL.startswith("postgresql") else String(36),
        primary_key=True,
        default=_uuid_pk,
    )
    # core identifiers
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="phone")

    # categorization / notes
    issue_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # artifacts
    audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # single agent wav
    bundle_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # call.json path

    # snapshots
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    instructions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["CallMessage"]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CallMessage.created_at.asc()",
    )

    __table_args__ = (
        Index("ix_calls_issue_category", "issue_category"),
        Index("ix_calls_started_at", "started_at"),
    )

class CallMessage(Base):
    __tablename__ = "call_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True) if DB_URL.startswith("postgresql") else String(36),
        primary_key=True,
        default=_uuid_pk,
    )
    call_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=True) if DB_URL.startswith("postgresql") else String(36),
        ForeignKey("calls.id", ondelete="CASCADE"),
    )
    sender: Mapped[CallSender] = mapped_column(SAEnum(CallSender), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    call: Mapped["Call"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_call_messages_call_id_created_at", "call_id", "created_at"),
        Index("ix_call_messages_content_gist", "content"),  # harmless in SQLite, useful in PG if you switch to FTS
    )

# --- helper: init DB (create tables) ---
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
