from __future__ import annotations
from datetime import date, time, datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, EmailStr, field_validator
# --- Project imports ---
from db.models import (
    AppointmentStatus,
    RequestPriority,
    
)

class TechCreate(BaseModel):
    full_name: str
    code: Optional[str] = None
    timezone: str = Field(default="America/Los_Angeles")
    google_calendar_id: Optional[str] = None
    skills: List[str] = Field(default_factory=list)


class TechOut(BaseModel):
    id: str
    full_name: str
    code: Optional[str] = None
    timezone: str
    google_calendar_id: Optional[str] = None
    skills: List[str] = Field(default_factory=list)


class TechAvailabilityCreate(BaseModel):
    start_date: date
    end_date: date
    start_time: time
    end_time: time
    weekdays: Optional[List[int]] = Field(
        default=None, description="0=Mon ... 6=Sun. Omit for every day."
    )
    clear_overlaps: bool = Field(
        default=False,
        description="If true, delete overlapping shifts before inserting.",
    )

    @field_validator("weekdays")
    @classmethod
    def _validate_weekdays(cls, v: Optional[List[int]]):
        if v is None:
            return v
        bad = [x for x in v if not isinstance(x, int) or x < 0 or x > 6]
        if bad:
            raise ValueError("weekdays values must be integers in 0..6")
        # de-dup to avoid inserting same day twice
        return sorted(set(v))


class SlotOut(BaseModel):
    tech_id: str
    start: datetime
    end: datetime
    source: Literal["db", "db+google"]


class AppointmentCreate(BaseModel):
    user_id: str
    tech_id: Optional[str] = None
    skill: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    duration_min: int = 120  # used when booking earliest by skill
    priority: RequestPriority = RequestPriority.P3
    request_text: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def _tz_required(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime fields must include a timezone offset")
        return v


class AppointmentPatch(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    status: Optional[AppointmentStatus] = None
    request_text: Optional[str] = None

    @field_validator("start", "end")
    @classmethod
    def _tz_required(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime fields must include a timezone offset")
        return v


class AppointmentOut(BaseModel):
    id: str
    appointment_no: Optional[int] = None  # may be null for legacy rows
    user_id: str
    tech_id: str
    start: datetime
    end: datetime
    priority: RequestPriority
    status: AppointmentStatus
    google_event_id: Optional[str] = None
    hangout_link: Optional[str] = None


class HoldCreate(BaseModel):
    tech_id: str
    user_id: Optional[str] = None
    start: datetime
    end: datetime
    ttl_seconds: int = 180
    request_text: Optional[str] = None
    show_tentative_on_google: bool = False

    @field_validator("start", "end")
    @classmethod
    def _tz_required(cls, v: Optional[datetime]):
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime fields must include a timezone offset")
        return v


class HoldOut(BaseModel):
    id: str
    tech_id: str
    start: datetime
    end: datetime
    expires_at: datetime


class UserCreate(BaseModel):
    full_name: str
    phone: str
    email: Optional[EmailStr] = None


class AddressOut(BaseModel):
    id: str
    label: Optional[str] = None
    line1: str
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class UserOut(BaseModel):
    id: str
    full_name: str
    phone: str
    email: Optional[str] = None
    addresses: List[AddressOut] = Field(default_factory=list)
