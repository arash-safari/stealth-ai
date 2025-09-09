# db/models_artifacts.py
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Text, BigInteger, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

# Reuse the same Base/utcnow used everywhere else
from db.models import Base, utcnow  # ensure this actually exports Base & utcnow

class ArtifactType(str, PyEnum):
    audio_recording = "audio_recording"
    transcript_json = "transcript_json"
    transcript_text = "transcript_text"

class StorageProvider(str, PyEnum):
    s3 = "s3"

class CallArtifact(Base):
    __tablename__ = "call_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )

    # what is it?
    type: Mapped[ArtifactType] = mapped_column(
        SAEnum(ArtifactType, name="artifact_type", create_constraint=False)
    )
    provider: Mapped[StorageProvider] = mapped_column(
        SAEnum(StorageProvider, name="storage_provider", create_constraint=False)
    )

    # where is it?
    bucket: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    version_id: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)

    # metadata
    content_type: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    kms_key_id: Mapped[str | None] = mapped_column(Text)
    egress_id: Mapped[str | None] = mapped_column(Text)

    # ✅ tz-aware timestamp in DB
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=utcnow
    )
