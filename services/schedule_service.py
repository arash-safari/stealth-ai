"""
Scheduling service — Python + SQLAlchemy (Postgres) + optional Google Calendar
--------------------------------------------------------------------

Exports:
  - get_available_times(...)
  - hold_slot(...)
  - create_meeting(...)
  - read_meeting(...)                         # accepts UUID or number
  - read_meeting_by_appointment_number(...)
  - update_meeting(... by appointment_no)     # accepts number or UUID
  - cancel_meeting(... by appointment_ref)    # accepts number or UUID
  - create_earliest_meeting(...)
  - publish_availability_for_range(...)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, TypedDict
import re

from zoneinfo import ZoneInfo
from sqlalchemy import select, text as sql_text, delete, update, cast, String
from sqlalchemy.ext.asyncio import AsyncSession  # <-- added
from db.session import Session
from db.models import (
    Skill,
    TechShift,
    Appointment,
    Hold,
    Tech,
    User,
    AppointmentStatus,
    RequestPriority,
)

# ---- Optional Google Calendar integration (safe stubs if not present) ----
try:
    from gcal import freebusy_batch, upsert_event, delete_event, extract_meet_link  # type: ignore
except Exception:
    try:
        from services.gcal import freebusy_batch, upsert_event, delete_event, extract_meet_link  # type: ignore
    except Exception:
        def freebusy_batch(cal_ids, start, end):
            return {}
        def upsert_event(**kwargs):
            return None
        def delete_event(calendar_id, event_id):
            return None
        def extract_meet_link(ev):
            return None


# --------------- Interval utils ---------------
@dataclass
class Interval:
    start: datetime
    end: datetime


def _overlaps(a: Interval, b: Interval) -> bool:
    return a.start < b.end and b.start < a.end


def _subtract(base: Interval, blocks: List[Interval]) -> List[Interval]:
    free = [base]
    for b in sorted(blocks, key=lambda x: x.start):
        next_free: List[Interval] = []
        for f in free:
            if not _overlaps(f, b):
                next_free.append(f)
            else:
                if f.start < b.start:
                    next_free.append(Interval(f.start, b.start))
                if b.end < f.end:
                    next_free.append(Interval(b.end, f.end))
        free = next_free
    return [i for i in free if i.end > i.start]


def _split_into_slots(intervals: List[Interval], duration: timedelta, limit: int) -> List[Interval]:
    out: List[Interval] = []
    for iv in intervals:
        cur = iv.start
        while cur + duration <= iv.end:
            out.append(Interval(cur, cur + duration))
            if len(out) >= limit:
                return out
            cur = cur + duration
    return out


# --------------- Public API ---------------
class SlotSuggestion(TypedDict):
    tech_id: str
    start: datetime
    end: datetime
    source: str  # "db" | "db+google"


async def get_available_times(
    *,
    skill: str,
    duration_min: int = 120,
    priority: RequestPriority = RequestPriority.P3,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 20,
    address_postal_code: Optional[str] = None,  # reserved for service-area routing
    respect_google_busy: bool = True,
) -> List[SlotSuggestion]:
    """Compute availability by skill + duration, subtracting DB holds/appointments and (optionally) Google FreeBusy."""
    from sqlalchemy import cast, String  # local import to avoid forgetting it at module top

    if duration_min <= 0 or limit <= 0:
        return []

    now = datetime.now(timezone.utc)
    start_h = date_from or now

    # Horizon by priority (faster for P1, wider for P3)
    horizon_days = 1 if priority == RequestPriority.P1 else 3 if priority == RequestPriority.P2 else 7
    end_h = date_to or (start_h + timedelta(days=horizon_days))

    async with Session() as db:
        # Resolve skill row
        skill_row = (await db.execute(select(Skill).where(Skill.name.ilike(skill)))).scalar_one_or_none()
        if not skill_row:
            return []

        # Techs that have the skill & are active
        tech_rows = (
            await db.execute(
                sql_text(
                    """
                    SELECT t.id, t.timezone, t.google_calendar_id
                    FROM techs t
                    JOIN tech_skills ts ON ts.tech_id = t.id
                    WHERE ts.skill_id = :sid AND t.active = TRUE
                    """
                ),
                {"sid": str(skill_row.id)},
            )
        ).all()
        if not tech_rows:
            return []

        tech_ids = [r[0] for r in tech_rows]
        cal_by_tech = {r[0]: r[2] for r in tech_rows}

        # Shifts that intersect our horizon
        shifts: List[TechShift] = (
            await db.execute(
                select(TechShift).where(
                    TechShift.tech_id.in_(tech_ids),
                    TechShift.end_ts > start_h,
                    TechShift.start_ts < end_h,
                ).order_by(TechShift.start_ts)
            )
        ).scalars().all()
        if not shifts:
            return []

        # Busy blocks from DB: appointments (not canceled) + holds (not expired)
        # IMPORTANT: cast enum to text to avoid asyncpg enum OID cache issues in prepared statements.
        appts = (
            await db.execute(
                select(Appointment.start_ts, Appointment.end_ts, Appointment.tech_id).where(
                    Appointment.tech_id.in_(tech_ids),
                    cast(Appointment.status, String) != "canceled",
                    Appointment.end_ts > start_h,
                    Appointment.start_ts < end_h,
                )
            )
        ).all()

        holds = (
            await db.execute(
                select(Hold.start_ts, Hold.end_ts, Hold.tech_id).where(
                    Hold.tech_id.in_(tech_ids),
                    Hold.expires_at > now,
                    Hold.end_ts > start_h,
                    Hold.start_ts < end_h,
                )
            )
        ).all()

        busy_by_tech: Dict[uuid.UUID, List[Interval]] = {tid: [] for tid in tech_ids}
        for s, e, tid in appts:
            busy_by_tech[tid].append(Interval(s, e))
        for s, e, tid in holds:
            busy_by_tech[tid].append(Interval(s, e))

        # Google FreeBusy (optional)
        source = "db+google" if respect_google_busy else "db"
        if respect_google_busy:
            cal_ids = [cal_by_tech[tid] for tid in tech_ids if cal_by_tech[tid]]
            busy_by_cal = await _freebusy(cal_ids, start_h, end_h)
            for tid in tech_ids:
                cid = cal_by_tech.get(tid)
                if cid and cid in busy_by_cal:
                    for s, e in busy_by_cal[cid]:
                        busy_by_tech[tid].append(Interval(s, e))

        duration = timedelta(minutes=duration_min)
        results: List[SlotSuggestion] = []

        # Free slots per tech
        for tid in tech_ids:
            t_shifts = [sh for sh in shifts if sh.tech_id == tid]
            if not t_shifts:
                continue

            base_intervals = [Interval(max(sh.start_ts, start_h), min(sh.end_ts, end_h)) for sh in t_shifts]
            blocks = busy_by_tech.get(tid, [])
            free: List[Interval] = []
            for base in base_intervals:
                free.extend(_subtract(base, blocks))

            # Generate slots
            slots = _split_into_slots(sorted(free, key=lambda x: x.start), duration, max(0, limit - len(results)))
            for s in slots:
                results.append(
                    {
                        "tech_id": str(tid),
                        "start": s.start,
                        "end": s.end,
                        "source": source,
                    }
                )
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        results.sort(key=lambda r: r["start"])  # type: ignore[arg-type]
        return results

async def _freebusy(cal_ids: List[str], start_h: datetime, end_h: datetime):
    # Wrapper to ease testing/mocking
    return freebusy_batch(cal_ids, start_h, end_h) if cal_ids else {}


async def hold_slot(
    *,
    tech_id: str,
    user_id: Optional[str],
    start: datetime,
    end: datetime,
    ttl_seconds: int = 180,
    request_text: Optional[str] = None,
    show_tentative_on_google: bool = False,
):
    """Create a temporary reservation (hold). Postgres constraints should prevent overlaps (add EXCLUDE in migrations)."""
    now = datetime.now(timezone.utc)
    expires_at = min(end, now + timedelta(seconds=max(1, ttl_seconds)))

    async with Session() as db:
        # Create DB hold
        try:
            h = Hold(
                tech_id=uuid.UUID(tech_id),
                user_id=(uuid.UUID(user_id) if user_id else None),
                start_ts=start,
                end_ts=end,
                expires_at=expires_at,
                request_text=request_text,
            )
            db.add(h)
            await db.commit()
            await db.refresh(h)
        except Exception as e:  # overlap/constraint
            await db.rollback()
            raise RuntimeError(f"Hold conflicts with existing booking/hold: {e}")

        # (Optional) Tentative GCal event
        if show_tentative_on_google:
            t = await db.get(Tech, uuid.UUID(tech_id))
            if t and t.google_calendar_id:
                upsert_event(
                    calendar_id=t.google_calendar_id,
                    event_id=None,
                    summary="(Hold) Service window",
                    description=request_text,
                    start=start,
                    end=end,
                    time_zone=t.timezone,
                    attendees=[],
                    appointment_id=f"HOLD:{h.id}",
                    tentative=True,
                )
        return {"id": str(h.id), "tech_id": tech_id, "start": start, "end": end, "expires_at": expires_at}


# ---------- Helper: resolve by UUID or numeric appointment_no ----------
async def _resolve_appointment(db: AsyncSession, ref: str) -> Optional[Appointment]:
    """
    Prefer public numeric appointment_no, then fall back to internal UUID.
    Accepts strings like "12345" or "#12345"; ignores whitespace.
    """
    if ref is None:
        return None
    s = str(ref).strip()

    # 1) Prefer public number
    #    Allow a leading '#' or whitespace; reject if not purely digits after stripping.
    m = re.fullmatch(r"#?(\d+)", s)
    if m:
        try:
            number = int(m.group(1))
            appt = await db.scalar(select(Appointment).where(Appointment.appointment_no == number))
            if appt:
                return appt
        except Exception:
            # fall through to UUID attempt
            pass

    # 2) Fallback to UUID (accepts 32-hex or hyphenated)
    try:
        u = uuid.UUID(s)
        appt = await db.get(Appointment, u)
        if appt:
            return appt
    except (ValueError, TypeError, AttributeError):
        pass

    return None


async def create_meeting(
    *,
    user_id: str,
    tech_id: str,
    start: datetime,
    end: datetime,
    priority: RequestPriority = RequestPriority.P3,
    request_text: Optional[str] = None,
):
    async with Session() as db:
        u = await db.get(User, uuid.UUID(user_id))
        if not u:
            raise RuntimeError("User not found")
        t = await db.get(Tech, uuid.UUID(tech_id))
        if not t:
            raise RuntimeError("Tech not found")

        # Create appointment in DB
        try:
            a = Appointment(
                user_id=u.id,
                tech_id=t.id,
                start_ts=start,
                end_ts=end,
                priority=priority,
                status=AppointmentStatus.scheduled,
                request_text=request_text,
                phone_snapshot=u.phone,
                address_line1_snapshot=None,  # fill from Address default in future
                address_line2_snapshot=None,
                city_snapshot=None,
                state_snapshot=None,
                postal_code_snapshot=None,
            )
            db.add(a)
            await db.commit()
            await db.refresh(a)
        except Exception as e:
            await db.rollback()
            raise RuntimeError(f"Appointment conflict: {e}")

        # Google event + Meet link (optional)
        if t.google_calendar_id:
            ev = upsert_event(
                calendar_id=t.google_calendar_id,
                event_id=None,
                summary=f"Service: {request_text or 'Plumbing appointment'}",
                description=f"Priority {priority} — {u.full_name} ({u.phone})",
                start=start,
                end=end,
                time_zone=t.timezone,
                attendees=([{ "email": u.email, "displayName": u.full_name }] if u.email else []),
                appointment_id=str(a.id),
            )
            if ev:
                a.google_event_id = ev.get("id")
                a.hangout_link = extract_meet_link(ev)
                await db.commit()

        return {
            "id": str(a.id),
            "appointment_no": getattr(a, "appointment_no", None),  # <-- added
            "user_id": str(a.user_id),
            "tech_id": str(a.tech_id),
            "start": a.start_ts,
            "end": a.end_ts,
            "priority": a.priority,
            "status": a.status,
            "google_event_id": a.google_event_id,
            "hangout_link": a.hangout_link,
        }


async def read_meeting(appointment_no: str):
    """
    Fetch an appointment by either:
      - UUID primary key (internal), or
      - numeric public Appointment ID (appointment_no)
    """
    async with Session() as db:
        appt = await _resolve_appointment(db, appointment_no)
        if appt is None:
            raise RuntimeError("Appointment not found")

        return {
            "id": str(appt.id),
            "appointment_no": getattr(appt, "appointment_no", None),
            "user_id": str(appt.user_id),
            "tech_id": str(appt.tech_id),
            "start": appt.start_ts,
            "end": appt.end_ts,
            "priority": appt.priority,
            "status": appt.status,
            "request_text": appt.request_text,
            "google_event_id": appt.google_event_id,
            "hangout_link": appt.hangout_link,
        }


async def update_meeting(
    *,
    appointment_no: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    status: Optional[AppointmentStatus] = None,
    request_text: Optional[str] = None,
):
    async with Session() as db:
        a = await _resolve_appointment(db, appointment_no)  # <-- accepts either form
        if not a:
            raise RuntimeError("Appointment not found")
        t = await db.get(Tech, a.tech_id)
        u = await db.get(User, a.user_id)

        # Update DB first
        if start is not None:
            a.start_ts = start
        if end is not None:
            a.end_ts = end
        if status is not None:
            a.status = status
        if request_text is not None:
            a.request_text = request_text
        try:
            await db.commit()
            await db.refresh(a)
        except Exception as e:
            await db.rollback()
            raise RuntimeError(f"Update conflicts with existing booking: {e}")

        # Sync Google event
        if a.google_event_id and t and t.google_calendar_id:
            ev = upsert_event(
                calendar_id=t.google_calendar_id,
                event_id=a.google_event_id,
                summary=f"Service: {a.request_text or 'Plumbing appointment'}",
                description=f"Priority {a.priority} — {u.full_name if u else ''} ({u.phone if u else ''})",
                start=a.start_ts,
                end=a.end_ts,
                time_zone=t.timezone,
                attendees=([{"email": u.email, "displayName": u.full_name}] if (u and u.email) else []),
                appointment_id=str(a.id),
            )
            if ev:
                a.hangout_link = extract_meet_link(ev)
                await db.commit()
        return {
            "id": str(a.id),
            "appointment_no": getattr(a, "appointment_no", None),  # <-- added for convenience
            "start": a.start_ts,
            "end": a.end_ts,
            "status": a.status,
            "hangout_link": a.hangout_link,
        }


async def cancel_meeting(appointment_ref: str):
    """
    Cancel an appointment by either:
      - public numeric appointment number, or
      - internal UUID
    Also removes the Google Calendar event if present.
    """
    async with Session() as db:
        a = await _resolve_appointment(db, appointment_ref)
        if not a:
            raise RuntimeError("Appointment not found")

        t = await db.get(Tech, a.tech_id)

        a.status = AppointmentStatus.canceled
        await db.commit()

        # Best-effort Google cleanup (don't fail cancellation if GCal fails)
        if a.google_event_id and t and t.google_calendar_id:
            try:
                delete_event(t.google_calendar_id, a.google_event_id)
            except Exception:
                # swallow GCal errors; DB already shows canceled
                pass

        return {
            "ok": True,
            "appointment_no": getattr(a, "appointment_no", None),
            "id": str(a.id),
            "status": a.status,
        }


async def create_earliest_meeting(
    *, user_id: str, skill: str, duration_min: int = 120, priority: RequestPriority = RequestPriority.P3, request_text: Optional[str] = None
):
    slots = await get_available_times(skill=skill, duration_min=duration_min, priority=priority, limit=1)
    if not slots:
        raise RuntimeError("No availability found")
    s = slots[0]
    return await create_meeting(
        user_id=user_id,
        tech_id=s["tech_id"],
        start=s["start"],
        end=s["end"],
        priority=priority,
        request_text=request_text,
    )


async def get_appointment_by_no(session: AsyncSession, number: int) -> Optional[Appointment]:
    stmt = select(Appointment).where(Appointment.appointment_no == number)
    return await session.scalar(stmt)


# --------------- Bulk availability publish (for a tech) ---------------
def _local_range_to_utc(d: date, start_t: time, end_t: time, tz: str) -> Tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start_local = datetime.combine(d, start_t, tzinfo=z)
    end_local = datetime.combine(d, end_t, tzinfo=z)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def publish_availability_for_range(
    *,
    tech_id: str,
    start_date: date,
    end_date: date,
    start_time: time,
    end_time: time,
    weekdays: Optional[List[int]] = None,  # 0=Mon..6=Sun. None=every day
    clear_overlaps: bool = False,
) -> Dict[str, int]:
    """
    Create TechShift rows for each matching day in [start_date, end_date], interpreting times in the tech timezone.
    """
    if end_date < start_date:
        raise RuntimeError("end_date must be >= start_date")
    if end_time <= start_time:
        raise RuntimeError("end_time must be after start_time")

    async with Session() as db:
        t = await db.get(Tech, uuid.UUID(tech_id))
        if not t:
            raise RuntimeError("Tech not found")

        cur = start_date
        created = 0
        while cur <= end_date:
            if weekdays is None or cur.weekday() in weekdays:
                s_utc, e_utc = _local_range_to_utc(cur, start_time, end_time, t.timezone)

                if clear_overlaps:
                    await db.execute(
                        delete(TechShift).where(
                            TechShift.tech_id == t.id,
                            TechShift.end_ts > s_utc,
                            TechShift.start_ts < e_utc,
                        )
                    )

                db.add(TechShift(tech_id=t.id, start_ts=s_utc, end_ts=e_utc))
                created += 1
            cur = cur + timedelta(days=1)

        await db.commit()
        return {"shifts_created": created}

async def read_meeting_by_appointment_number(appointment_no: int):
    """
    Fetch an appointment by its public numeric Appointment ID (appointment_no).
    Returns the same shape as read_meeting(...).
    """
    async with Session() as db:
        appt = await get_appointment_by_no(db, int(appointment_no))
        if appt is None:
            raise RuntimeError("Appointment not found")

        return {
            "id": str(appt.id),
            "appointment_no": getattr(appt, "appointment_no", None),
            "user_id": str(appt.user_id),
            "tech_id": str(appt.tech_id),
            "start": appt.start_ts,
            "end": appt.end_ts,
            "priority": appt.priority,
            "status": appt.status,
            "request_text": appt.request_text,
            "google_event_id": appt.google_event_id,
            "hangout_link": appt.hangout_link,
        }