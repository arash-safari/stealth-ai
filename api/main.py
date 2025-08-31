from __future__ import annotations

import uuid
import os
from contextlib import asynccontextmanager
from datetime import date, time, datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict

from fastapi import FastAPI, HTTPException, Query, Body, Response
from pydantic import BaseModel, Field, EmailStr, field_validator
from zoneinfo import ZoneInfo
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from dotenv import load_dotenv

# --- Load env before importing models/engine ---
for name in (".env.local", "env.local", ".env"):
    if os.path.exists(name):
        load_dotenv(name, override=False)

# --- Project imports ---
from db.session import Session  # async session factory
from db.models import (
    Tech,
    Skill,
    TechSkill,
    TechShift,
    Appointment,
    User,
    Address,
    AppointmentStatus,
    RequestPriority,
    init_db,
    engine,
)
from services.schedule_service import (
    get_available_times,
    hold_slot,
    create_meeting,
    read_meeting,
    update_meeting,
    cancel_meeting,
    create_earliest_meeting,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB once at startup
    await init_db()
    yield
    # Optional: close the engine cleanly on shutdown
    await engine.dispose()


app = FastAPI(
    lifespan=lifespan,
    title="Plumber Contact Center API",
    version="0.2.0",
)


# ---------------------------
# Pydantic Schemas (v2)
# ---------------------------
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


# ---------------------------
# Helpers
# ---------------------------
async def _ensure_skill_ids(db: Session, names: List[str]) -> List[uuid.UUID]:
    """Return skill ids for given names (create rows as needed). Case-insensitive match."""
    out: List[uuid.UUID] = []
    for n in names:
        n_norm = n.strip()
        if not n_norm:
            continue
        row = (
            await db.execute(select(Skill).where(Skill.name.ilike(n_norm)))
        ).scalar_one_or_none()
        if row:
            out.append(row.id)
        else:
            s = Skill(name=n_norm)
            db.add(s)
            await db.flush()
            await db.refresh(s)
            out.append(s.id)
    return out


async def _tech_to_out(db: Session, t: Tech) -> TechOut:
    sk = (
        await db.execute(
            select(Skill.name)
            .join(TechSkill, TechSkill.skill_id == Skill.id)
            .where(TechSkill.tech_id == t.id)
        )
    ).scalars().all()
    return TechOut(
        id=str(t.id),
        full_name=t.full_name,
        code=t.code,
        timezone=t.timezone,
        google_calendar_id=t.google_calendar_id,
        skills=sk,
    )


def _local_range_to_utc(d: date, start_t: time, end_t: time, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start_local = datetime.combine(d, start_t, tzinfo=z)
    end_local = datetime.combine(d, end_t, tzinfo=z)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _require_tz(dt: Optional[datetime], name: str):
    if dt is not None and dt.tzinfo is None:
        raise HTTPException(400, f"{name} must include a timezone offset")


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except Exception:
        raise HTTPException(400, f"{field} must be a UUID")


# ---------------------------
# Techs
# ---------------------------
@app.post(
    "/techs",
    response_model=TechOut,
    status_code=201,
    response_model_exclude_none=True,
)
async def create_tech(payload: TechCreate):
    async with Session() as db:
        t = Tech(
            full_name=payload.full_name.strip(),
            code=(payload.code or None),
            timezone=payload.timezone,
            google_calendar_id=payload.google_calendar_id,
            active=True,
        )
        db.add(t)
        await db.flush()
        # skills
        ids = await _ensure_skill_ids(db, payload.skills)
        if ids:
            stmt = pg_insert(TechSkill).values(
                [{"tech_id": t.id, "skill_id": sid} for sid in ids]
            ).on_conflict_do_nothing(index_elements=["tech_id", "skill_id"])
            await db.execute(stmt)
        await db.commit()
        await db.refresh(t)
        return await _tech_to_out(db, t)


@app.get(
    "/techs/{tech_id}",
    response_model=TechOut,
    response_model_exclude_none=True,
)
async def get_tech(tech_id: str):
    async with Session() as db:
        t = await db.get(Tech, _parse_uuid(tech_id, "tech_id"))
        if not t:
            raise HTTPException(404, "Tech not found")
        return await _tech_to_out(db, t)


@app.post(
    "/techs/{tech_id}/skills",
    response_model=TechOut,
    response_model_exclude_none=True,
)
async def add_skills(tech_id: str, skills: List[str] = Body(...)):
    async with Session() as db:
        t = await db.get(Tech, _parse_uuid(tech_id, "tech_id"))
        if not t:
            raise HTTPException(404, "Tech not found")
        ids = await _ensure_skill_ids(db, skills)
        if ids:
            stmt = pg_insert(TechSkill).values(
                [{"tech_id": t.id, "skill_id": sid} for sid in ids]
            ).on_conflict_do_nothing(index_elements=["tech_id", "skill_id"])
            await db.execute(stmt)
        await db.commit()
        return await _tech_to_out(db, t)


# ---------------------------
# Availability (TechShift)
# ---------------------------
@app.post(
    "/techs/{tech_id}/availability",
    status_code=201,
    response_model_exclude_none=True,
)
async def publish_availability(tech_id: str, body: TechAvailabilityCreate):
    """
    Creates TechShift rows for each matching day in the range.
    Times are interpreted in the tech's timezone, stored UTC.
    """
    async with Session() as db:
        t = await db.get(Tech, _parse_uuid(tech_id, "tech_id"))
        if not t:
            raise HTTPException(404, "Tech not found")

        if body.end_date < body.start_date:
            raise HTTPException(400, "end_date must be >= start_date")
        if body.end_time <= body.start_time:
            raise HTTPException(400, "end_time must be after start_time")

        # iterate days
        cur = body.start_date
        created = 0
        while cur <= body.end_date:
            if body.weekdays is None or cur.weekday() in body.weekdays:
                start_utc, end_utc = _local_range_to_utc(
                    cur, body.start_time, body.end_time, t.timezone
                )

                if body.clear_overlaps:
                    await db.execute(
                        delete(TechShift).where(
                            TechShift.tech_id == t.id,
                            TechShift.end_ts > start_utc,
                            TechShift.start_ts < end_utc,
                        )
                    )

                db.add(TechShift(tech_id=t.id, start_ts=start_utc, end_ts=end_utc))
                created += 1
            cur = cur + timedelta(days=1)

        await db.commit()
        return {"ok": True, "shifts_created": created}


# ---------------------------
# Availability query (scheduler)
# ---------------------------
@app.get(
    "/availability",
    response_model=List[SlotOut],
    response_model_exclude_none=True,
)
async def availability(
    skill: str = Query(..., description="Skill name"),
    duration_min: int = 120,
    priority: RequestPriority = RequestPriority.P3,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 20,
    postal_code: Optional[str] = None,
    respect_google_busy: bool = True,
):
    _require_tz(date_from, "date_from")
    _require_tz(date_to, "date_to")

    slots = await get_available_times(
        skill=skill,
        duration_min=duration_min,
        priority=priority,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        address_postal_code=postal_code,
        respect_google_busy=respect_google_busy,
    )
    return [SlotOut(**s) for s in slots]


# ---------------------------
# Appointments
# ---------------------------
@app.post(
    "/appointments",
    response_model=AppointmentOut,
    status_code=201,
    response_model_exclude_none=True,
)
async def create_appt(body: AppointmentCreate):
    """
    Create an appointment with explicit tech+time or by skill (earliest).
    Always returns appointment_no.
    """
    if body.tech_id and body.start and body.end:
        created = await create_meeting(
            user_id=body.user_id,
            tech_id=body.tech_id,
            start=body.start,
            end=body.end,
            priority=body.priority,
            request_text=body.request_text,
        )
        full = await read_meeting(created["id"])  # ensure appointment_no is included
        return AppointmentOut(**full)

    if body.skill and not (body.start or body.end or body.tech_id):
        created = await create_earliest_meeting(
            user_id=body.user_id,
            skill=body.skill,
            duration_min=body.duration_min,
            priority=body.priority,
            request_text=body.request_text,
        )
        full = await read_meeting(created["id"])  # ensure appointment_no is included
        return AppointmentOut(**full)

    raise HTTPException(
        400,
        "Provide either (tech_id, start, end) OR (skill) for earliest slot booking.",
    )


@app.get(
    "/appointments/{appointment_id}",
    response_model=AppointmentOut,
    response_model_exclude_none=True,
)
async def get_appt(appointment_id: str):
    appt = await read_meeting(appointment_id)  # already returns appointment_no
    return AppointmentOut(**appt)


@app.patch(
    "/appointments/{appointment_id}",
    response_model=AppointmentOut,
    response_model_exclude_none=True,
)
async def patch_appt(appointment_id: str, body: AppointmentPatch):
    await update_meeting(
        appointment_no=appointment_id,
        start=body.start,
        end=body.end,
        status=body.status,
        request_text=body.request_text,
    )
    full = await read_meeting(appointment_id)  # return full row (with appointment_no)
    return AppointmentOut(**full)


@app.delete("/appointments/{appointment_id}", status_code=204)
async def delete_appt(appointment_id: str):
    await cancel_meeting(appointment_id)
    return Response(status_code=204)


@app.get(
    "/users/{user_id}/appointments",
    response_model=List[AppointmentOut],
    response_model_exclude_none=True,
)
async def user_appts(user_id: str):
    async with Session() as db:
        rows = (
            await db.execute(
                select(Appointment)
                .where(Appointment.user_id == _parse_uuid(user_id, "user_id"))
                .order_by(Appointment.start_ts.desc())
            )
        ).scalars().all()
        return [
            AppointmentOut(
                id=str(a.id),
                appointment_no=a.appointment_no,
                user_id=str(a.user_id),
                tech_id=str(a.tech_id),
                start=a.start_ts,
                end=a.end_ts,
                priority=a.priority,
                status=a.status,
                google_event_id=a.google_event_id,
                hangout_link=a.hangout_link,
            )
            for a in rows
        ]


# ---------------------------
# Holds (temporary reservations)
# ---------------------------
@app.post(
    "/holds",
    response_model=HoldOut,
    response_model_exclude_none=True,
)
async def create_hold(body: HoldCreate):
    try:
        h = await hold_slot(
            tech_id=body.tech_id,
            user_id=body.user_id,
            start=body.start,
            end=body.end,
            ttl_seconds=body.ttl_seconds,
            request_text=body.request_text,
            show_tentative_on_google=body.show_tentative_on_google,
        )
        return HoldOut(**h)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


# ---------------------------
# Users
# ---------------------------
@app.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    response_model_exclude_none=True,
)
async def create_user(payload: UserCreate = Body(...)):
    async with Session() as db:
        u = User(
            full_name=payload.full_name.strip(),
            phone=payload.phone.strip(),
            email=(payload.email.lower() if payload.email else None),
        )
        db.add(u)
        try:
            await db.commit()
            await db.refresh(u)
        except IntegrityError as e:
            await db.rollback()
            raise HTTPException(409, "Phone already exists") from e
        return UserOut(id=str(u.id), full_name=u.full_name, phone=u.phone, email=u.email)


@app.get(
    "/users/{user_id}",
    response_model=UserOut,
    response_model_exclude_none=True,
)
async def get_user(user_id: str):
    async with Session() as db:
        u = await db.get(User, _parse_uuid(user_id, "user_id"))
        if not u:
            raise HTTPException(404, "User not found")

        addr_rows = (
            await db.execute(
                select(Address)
                .where(Address.user_id == u.id)
                .order_by(Address.is_default.desc(), Address.created_at.desc())
            )
        ).scalars().all()

        addresses = [
            AddressOut(
                id=str(a.id),
                label=a.label,
                line1=a.line1,
                line2=a.line2,
                city=a.city,
                state=a.state,
                postal_code=a.postal_code,
                is_default=a.is_default,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a in addr_rows
        ]

        return UserOut(
            id=str(u.id),
            full_name=u.full_name,
            phone=u.phone,
            email=u.email,
            addresses=addresses,
        )


@app.get(
    "/appointments",
    response_model=List[AppointmentOut],
    response_model_exclude_none=True,
)
async def list_appointments(
    user_id: Optional[str] = None,
    tech_id: Optional[str] = None,
    status: Optional[AppointmentStatus] = None,  # e.g. scheduled|en_route|complete|canceled
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    include_canceled: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order: Literal["asc", "desc"] = "asc",
):
    """
    List appointments with simple filters and pagination.

    Overlap logic for ranges:
      - if date_from is set: end_ts > date_from
      - if date_to   is set: start_ts < date_to
    """
    _require_tz(date_from, "date_from")
    _require_tz(date_to, "date_to")

    async with Session() as db:
        stmt = select(Appointment)

        # Optional filters
        if user_id:
            stmt = stmt.where(Appointment.user_id == _parse_uuid(user_id, "user_id"))
        if tech_id:
            stmt = stmt.where(Appointment.tech_id == _parse_uuid(tech_id, "tech_id"))

        if status is not None:
            stmt = stmt.where(Appointment.status == status)
        elif not include_canceled:
            stmt = stmt.where(Appointment.status != AppointmentStatus.canceled)

        if date_from is not None:
            stmt = stmt.where(Appointment.end_ts > date_from)
        if date_to is not None:
            stmt = stmt.where(Appointment.start_ts < date_to)

        # Sort + paginate
        stmt = stmt.order_by(
            Appointment.start_ts.desc() if order == "desc" else Appointment.start_ts.asc()
        )
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        rows = (await db.execute(stmt)).scalars().all()

        return [
            AppointmentOut(
                id=str(a.id),
                appointment_no=a.appointment_no,
                user_id=str(a.user_id),
                tech_id=str(a.tech_id),
                start=a.start_ts,
                end=a.end_ts,
                priority=a.priority,
                status=a.status,
                google_event_id=a.google_event_id,
                hangout_link=a.hangout_link,
            )
            for a in rows
        ]


@app.get(
    "/users",
    response_model=List[UserOut],
    response_model_exclude_none=True,
)
async def list_users(
    q: Optional[str] = Query(None, description="Case-insensitive search in name/phone/email"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order: Literal["asc", "desc"] = "asc",
):
    async with Session() as db:
        stmt = select(User)

        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                (User.full_name.ilike(like))
                | (User.phone.ilike(like))
                | (User.email.ilike(like))
            )

        stmt = stmt.order_by(User.full_name.asc() if order == "asc" else User.full_name.desc())

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        users = (await db.execute(stmt)).scalars().all()
        if not users:
            return []

        user_ids = [u.id for u in users]

        addr_rows = (
            await db.execute(
                select(Address)
                .where(Address.user_id.in_(user_ids))
                .order_by(Address.is_default.desc(), Address.created_at.desc())
            )
        ).scalars().all()

        # group addresses by user_id
        addr_map: Dict[uuid.UUID, List[AddressOut]] = {}
        for a in addr_rows:
            addr_map.setdefault(a.user_id, []).append(
                AddressOut(
                    id=str(a.id),
                    label=a.label,
                    line1=a.line1,
                    line2=a.line2,
                    city=a.city,
                    state=a.state,
                    postal_code=a.postal_code,
                    is_default=a.is_default,
                    created_at=a.created_at,
                    updated_at=a.updated_at,
                )
            )

        return [
            UserOut(
                id=str(u.id),
                full_name=u.full_name,
                phone=u.phone,
                email=u.email,
                addresses=addr_map.get(u.id, []),
            )
            for u in users
        ]
