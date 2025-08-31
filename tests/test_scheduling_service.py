# tests/test_scheduling_service.py
from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, time, date, timezone
from typing import Dict, List, Tuple

import pytest
from sqlalchemy import delete, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

# Module under test
_svc = importlib.import_module("services.schedule_service")

# Models / enums only
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

pytestmark = pytest.mark.asyncio


# ----------------------------- Autouse patches -----------------------------
@pytest.fixture(autouse=True)
async def bind_service_to_test_session(monkeypatch, db_session: AsyncSession):
    """
    Make the service use the SAME AsyncSession (and event loop) as the test.
    """
    class _Ctx:
        async def __aenter__(self):
            return db_session
        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(_svc, "Session", lambda: _Ctx(), raising=True)
    yield


@pytest.fixture(autouse=True)
async def fresh_connections_and_clear_type_cache(db_session: AsyncSession):
    """
    Robust fix for asyncpg 'cache lookup failed for type ####':
      1) Dispose the engine pool before each test so no connection with stale
         enum OIDs is reused.
      2) On the first fresh connection, run DISCARD ALL and reload_schema_state()
         to clear prepared statements and client-side type caches.
    """
    # End any stray transaction
    await db_session.commit()

    # 1) Drop all pooled connections (ensures brand-new conns for the test)
    try:
        await db_session.bind.dispose()  # AsyncEngine.dispose()
    except Exception:
        pass

    # 2) Touch a fresh connection and scrub caches explicitly
    try:
        async with db_session.bind.connect() as conn:
            try:
                await conn.exec_driver_sql("DISCARD ALL")
            except Exception:
                # Not supported by some backends (e.g., SQLite)
                pass
            try:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection  # asyncpg.connection.Connection
                await driver.reload_schema_state()
            except Exception:
                pass
    except Exception:
        pass

    yield


# ----------------------------- Small helpers -----------------------------
def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _overlaps(a: Tuple[datetime, datetime], b: Tuple[datetime, datetime]) -> bool:
    return _utc(a[0]) < _utc(b[1]) and _utc(b[0]) < _utc(a[1])


@dataclass
class Seed:
    skill: Skill
    tech: Tech
    user: User


async def _ensure_skill(db: AsyncSession, name: str) -> Skill:
    existing = (await db.execute(select(Skill).where(Skill.name.ilike(name)))).scalar_one_or_none()
    if existing:
        return existing
    s = Skill(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _create_user(db: AsyncSession) -> User:
    u = User(
        full_name=f"Test User {uuid.uuid4().hex[:6]}",
        email=f"test+{uuid.uuid4().hex[:6]}@example.com",
        phone="+15555550123",
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _create_tech(db: AsyncSession, tz: str = "America/Los_Angeles", with_calendar: bool = True) -> Tech:
    t = Tech(
        full_name=f"Tech {uuid.uuid4().hex[:6]}",
        timezone=tz,
        google_calendar_id=(f"tech-{uuid.uuid4().hex[:6]}@calendar.test" if with_calendar else None),
        active=True,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _link_tech_skill(db: AsyncSession, tech_id, skill_id):
    await db.execute(
        sql_text("INSERT INTO tech_skills (tech_id, skill_id) VALUES (:tid, :sid)"),
        {"tid": str(tech_id), "sid": str(skill_id)},
    )
    await db.commit()


async def _publish_shifts(db: AsyncSession, tech: Tech, day: date, start=time(9, 0), end=time(17, 0)):
    out = await _svc.publish_availability_for_range(
        tech_id=str(tech.id),
        start_date=day,
        end_date=day,
        start_time=start,
        end_time=end,
        weekdays=None,
        clear_overlaps=False,
    )
    assert out["shifts_created"] == 1


# ----------------------------- Fixtures -----------------------------
@pytest.fixture
async def seeded_env(db_session: AsyncSession):
    """
    Creates skill, tech, user, links skill to tech, and publishes one workday shift tomorrow.
    Uses the isolated `db_session` from conftest.py (and the autouse patch so the service shares it).
    """
    db = db_session

    # Seed base entities
    skill_name = f"plumbing-{uuid.uuid4().hex[:4]}"
    skill = await _ensure_skill(db, skill_name)
    tech = await _create_tech(db)
    user = await _create_user(db)

    # Link skill<->tech and publish shift tomorrow
    await _link_tech_skill(db, tech.id, skill.id)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    await _publish_shifts(db, tech, tomorrow)

    seed = Seed(skill=skill, tech=tech, user=user)
    yield seed

    # Teardown rows we created (tables dropped elsewhere if your conftest does it)
    try:
        await db.execute(delete(TechShift).where(TechShift.tech_id == tech.id))
        await db.execute(delete(Appointment).where(Appointment.tech_id == tech.id))
        await db.execute(delete(Hold).where(Hold.tech_id == tech.id))
        await db.execute(sql_text("DELETE FROM tech_skills WHERE tech_id=:tid"), {"tid": str(tech.id)})
        await db.execute(delete(Tech).where(Tech.id == tech.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()
    except Exception:
        await db.rollback()


@pytest.fixture
def fake_gcal(monkeypatch):
    """
    Simple fake Google Calendar for tests that want it.
    """
    state = {"busy": {}, "events": {}, "deleted": [], "last_upsert": None}

    def freebusy_batch(cal_ids: List[str], start: datetime, end: datetime) -> Dict[str, List[Tuple[datetime, datetime]]]:
        out = {}
        for cid in cal_ids:
            out[cid] = state["busy"].get(cid, [])
        return out

    def upsert_event(**kwargs):
        ev_id = kwargs.get("event_id") or f"ev_{uuid.uuid4().hex[:8]}"
        ev = dict(kwargs)
        ev["id"] = ev_id
        state["events"][ev_id] = ev
        state["last_upsert"] = ev
        ev["hangoutLink"] = ev.get("hangoutLink") or f"https://meet.example/{ev_id}"
        return ev

    def delete_event(calendar_id, event_id):
        state["deleted"].append((calendar_id, event_id))
        state["events"].pop(event_id, None)
        return None

    def extract_meet_link(ev):
        return ev.get("hangoutLink")

    monkeypatch.setattr(_svc, "freebusy_batch", freebusy_batch, raising=True)
    monkeypatch.setattr(_svc, "upsert_event", upsert_event, raising=True)
    monkeypatch.setattr(_svc, "delete_event", delete_event, raising=True)
    monkeypatch.setattr(_svc, "extract_meet_link", extract_meet_link, raising=True)
    return state


# ----------------------------- Tests -----------------------------
async def test_publish_availability_for_range_creates_shifts(seeded_env):
    d0 = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    d1 = d0 + timedelta(days=6)
    out = await _svc.publish_availability_for_range(
        tech_id=str(seeded_env.tech.id),
        start_date=d0,
        end_date=d1,
        start_time=time(8, 0),
        end_time=time(12, 0),
        weekdays=[0, 2, 4],
        clear_overlaps=True,
    )
    assert out["shifts_created"] >= 1


async def test_get_available_times_db_only(seeded_env, db_session: AsyncSession):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    start_block = datetime.combine(tomorrow, time(11, 0), tzinfo=timezone.utc)
    end_block = start_block + timedelta(hours=2)

    # Create a blocking appointment using the provided session
    appt = Appointment(
        user_id=seeded_env.user.id,
        tech_id=seeded_env.tech.id,
        start_ts=start_block,
        end_ts=end_block,
        status=AppointmentStatus.scheduled,
    )
    db_session.add(appt)
    await db_session.commit()

    slots = await _svc.get_available_times(
        skill=seeded_env.skill.name,
        duration_min=120,
        respect_google_busy=False,
        limit=20,
    )
    assert len(slots) > 0
    for s in slots:
        assert s["source"] == "db"
        assert not _overlaps((s["start"], s["end"]), (start_block, end_block))


async def test_hold_slot_and_persistence(seeded_env, db_session: AsyncSession):
    # Find a free 2h slot
    slots = await _svc.get_available_times(
        skill=seeded_env.skill.name, duration_min=120, respect_google_busy=False, limit=5
    )
    assert slots, "Expected at least one slot"
    first = slots[0]

    # Place a hold
    hold = await _svc.hold_slot(
        tech_id=first["tech_id"],
        user_id=str(seeded_env.user.id),
        start=first["start"],
        end=first["end"],
        ttl_seconds=180,
        request_text="Test hold",
        show_tentative_on_google=False,
    )
    assert hold["id"]

    # Verify it exists and blocks future availability
    h = await db_session.get(Hold, uuid.UUID(hold["id"]))
    assert h is not None
    assert h.start_ts == first["start"] and h.end_ts == first["end"]

    # Now the same window should not be available anymore
    slots_after = await _svc.get_available_times(
        skill=seeded_env.skill.name, duration_min=120, respect_google_busy=False, limit=20
    )
    for s in slots_after:
        assert not _overlaps((s["start"], s["end"]), (first["start"], first["end"]))


async def test_create_earliest_meeting_matches_first_slot(seeded_env):
    # compute the earliest slot
    expected = await _svc.get_available_times(
        skill=seeded_env.skill.name, duration_min=120, respect_google_busy=False, limit=1
    )
    assert expected, "No slots found to test"
    first = expected[0]

    # create_earliest_meeting should pick the same slot
    appt = await _svc.create_earliest_meeting(
        user_id=str(seeded_env.user.id),
        skill=seeded_env.skill.name,
        duration_min=120,
        priority=RequestPriority.P3,
        request_text="Earliest please",
    )

    assert appt["tech_id"] == first["tech_id"]
    assert appt["start"] == first["start"]
    assert appt["end"] == first["end"]



# async def test_get_available_times_respects_google_busy(seeded_env, fake_gcal):
#     cal_id = seeded_env.tech.google_calendar_id
#     assert cal_id, "Tech should have a calendar id for this test"

#     tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
#     busy_s = datetime.combine(tomorrow, time(9, 0), tzinfo=timezone.utc)
#     busy_e = busy_s + timedelta(hours=2)
#     fake_gcal["busy"][cal_id] = [(busy_s, busy_e)]

#     slots = await _svc.get_available_times(
#         skill=seeded_env.skill.name,
#         duration_min=60,
#         respect_google_busy=True,
#         limit=40,
#     )
#     assert len(slots) > 0
#     for s in slots:
#         assert s["source"] == "db+google"
#         assert not _overlaps((s["start"], s["end"]), (busy_s, busy_e))


# async def test_hold_slot_and_persistence(seeded_env, db_session: AsyncSession): # <-- IMPORTANT: Request fixture
#     slots = await _svc.get_available_times(
#         skill=seeded_env.skill.name, duration_min=60, limit=10
#     )
#     assert slots, "Expected at least one available slot"
#     target = slots[0]
#     ttl = 90

#     res = await _svc.hold_slot(
#         tech_id=target["tech_id"],
#         user_id=str(seeded_env.user.id),
#         start=target["start"],
#         end=target["end"],
#         ttl_seconds=ttl,
#     )
#     hold_id = uuid.UUID(res["id"])

#     # Verify hold exists in DB using the provided db_session
#     h = await db_session.get(Hold, hold_id)
#     assert h is not None
#     assert h.start_ts == target["start"]


# async def test_create_read_update_cancel_meeting(seeded_env, fake_gcal):
#     slots = await _svc.get_available_times(
#         skill=seeded_env.skill.name, duration_min=120, limit=5
#     )
#     assert slots
#     s = slots[0]

#     created = await _svc.create_meeting(
#         user_id=str(seeded_env.user.id), tech_id=s["tech_id"], start=s["start"], end=s["end"],
#     )
#     appt_id = created["id"]
#     appt_no = created["appointment_no"]

#     read_by_no = await _svc.read_meeting(f"#{appt_no}")
#     assert read_by_no["id"] == appt_id

#     new_start = s["start"] + timedelta(minutes=30)
#     new_end = s["end"] + timedelta(minutes=30)
#     await _svc.update_meeting(
#         appointment_no=str(appt_no), start=new_start, end=new_end,
#     )

#     canceled = await _svc.cancel_meeting(str(appt_id))
#     assert canceled["ok"] is True
#     assert any(ev_id == created["google_event_id"] for _, ev_id in fake_gcal["deleted"])


# async def test_create_earliest_meeting_matches_first_slot(seeded_env):
#     first_slots = await _svc.get_available_times(
#         skill=seeded_env.skill.name, duration_min=120, limit=1
#     )
#     assert first_slots, "No availability to test"
#     s = first_slots[0]

#     created = await _svc.create_earliest_meeting(
#         user_id=str(seeded_env.user.id),
#         skill=seeded_env.skill.name,
#         duration_min=120,
#     )
#     assert _utc(created["start"]) == _utc(s["start"])
#     assert _utc(created["end"]) == _utc(s["end"])