from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime, timezone

class Base(DeclarativeBase):
    pass
def utcnow() -> datetime:
    return datetime.now(timezone.utc)
