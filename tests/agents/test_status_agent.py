import types
from datetime import datetime, timedelta, timezone
import inspect
import pytest

from agents.status import Status
import agents.status as status_mod  # so we can monkeypatch read_meeting


class DummyCtx:
    pass


async def _call_check(agent: Status, ctx: DummyCtx, **kwargs):
    """
    Call agent.check_status regardless of @function_tool decoration.
    Ensures we don't pass `self` twice.
    """
    meth = agent.check_status
    wrapped = getattr(meth, "__wrapped__", None)
    if wrapped is not None:
        bound = wrapped.__get__(agent, type(agent))
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx, **kwargs)
        return bound(ctx, **kwargs)
    if inspect.iscoroutinefunction(meth):
        return await meth(ctx, **kwargs)
    return meth(ctx, **kwargs)


def _iso_at(date_dt: datetime, hour: int, minute: int = 0):
    dt = date_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat()


def test_status_agent_wiring():
    ag = Status()
    assert "status agent" in ag.instructions.lower()
    assert "appointment" in ag.instructions.lower()
    assert ag.tts is not None
    # Tools contain read_meeting (imported symbol name)
    tool_names = {
        getattr(t, "__name__", getattr(getattr(t, "__wrapped__", None), "__name__", str(t)))
        for t in ag.tools
    }
    assert "read_meeting" in tool_names


@pytest.mark.asyncio
async def test_check_status_today_happy_path(monkeypatch):
    ag = Status()
    ctx = DummyCtx()

    # Make a window that is *today* in UTC
    now_utc = datetime.now(timezone.utc)
    start_iso = _iso_at(now_utc, 14)
    end_iso = _iso_at(now_utc, 16)

    async def fake_read_meeting(context, appointment_no: str):
        assert appointment_no == "A123"
        # Minimal fields used by the agent
        return (
            "appointment_no: A123\n"
            "status: scheduled\n"
            f"start: {start_iso}\n"
            f"end: {end_iso}\n"
        )

    monkeypatch.setattr(status_mod, "read_meeting", fake_read_meeting, raising=True)

    msg = await _call_check(ag, ctx, ref="A123")
    # Should mention "today" and the window
    assert "today" in msg.lower()
    assert "14:00-16:00" in msg or "14:00–16:00" in msg  # en dash tolerant
    assert "eta" in msg.lower()


@pytest.mark.asyncio
async def test_check_status_future_date(monkeypatch):
    ag = Status()
    ctx = DummyCtx()

    # Tomorrow in UTC
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    s = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 10, 0, tzinfo=timezone.utc)
    e = s + timedelta(hours=2)

    async def fake_read_meeting(context, appointment_no: str):
        return (
            "appointment_no: A777\n"
            "status: scheduled\n"
            f"start: {s.isoformat()}\n"
            f"end: {e.isoformat()}\n"
        )

    monkeypatch.setattr(status_mod, "read_meeting", fake_read_meeting, raising=True)

    msg = await _call_check(ag, ctx, ref="A777")
    assert "Appointment #A777 is scheduled on" in msg
    assert "10:00-12:00" in msg


@pytest.mark.asyncio
async def test_check_status_canceled(monkeypatch):
    ag = Status()
    ctx = DummyCtx()

    now_utc = datetime.now(timezone.utc)
    start_iso = _iso_at(now_utc, 9)
    end_iso = _iso_at(now_utc, 11)

    async def fake_read_meeting(context, appointment_no: str):
        return (
            "appointment_no: A999\n"
            "status: canceled\n"
            f"start: {start_iso}\n"
            f"end: {end_iso}\n"
        )

    monkeypatch.setattr(status_mod, "read_meeting", fake_read_meeting, raising=True)

    msg = await _call_check(ag, ctx, ref="A999")
    assert msg == "Appointment #A999 is canceled."


@pytest.mark.asyncio
async def test_check_status_not_found(monkeypatch):
    ag = Status()
    ctx = DummyCtx()

    async def fake_read_meeting(context, appointment_no: str):
        raise RuntimeError("not found")

    monkeypatch.setattr(status_mod, "read_meeting", fake_read_meeting, raising=True)

    msg = await _call_check(ag, ctx, ref="DOESNT-EXIST")
    assert msg == "Appointment not found."


@pytest.mark.asyncio
async def test_check_status_time_unavailable(monkeypatch):
    ag = Status()
    ctx = DummyCtx()

    # Missing/invalid start/end should trigger the "time info unavailable" branch
    async def fake_read_meeting(context, appointment_no: str):
        return (
            "appointment_no: A555\n"
            "status: scheduled\n"
            # start/end omitted on purpose
        )

    monkeypatch.setattr(status_mod, "read_meeting", fake_read_meeting, raising=True)

    msg = await _call_check(ag, ctx, ref="A555")
    assert "Appointment #A555: time info unavailable." == msg
