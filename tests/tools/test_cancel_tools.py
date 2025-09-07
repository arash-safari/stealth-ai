import types
from datetime import datetime, timezone, timedelta
import inspect
import yaml
import pytest

# Tools under test
tools = pytest.importorskip("tools.tools_schedule")
read_meeting = getattr(tools, "read_meeting")
cancel_meeting = getattr(tools, "cancel_meeting")

def _unwrap(fn):
    return getattr(fn, "__wrapped__", fn)

async def _call(fn, *args, **kwargs):
    target = _unwrap(fn)
    if inspect.iscoroutinefunction(target):
        return await target(*args, **kwargs)
    return target(*args, **kwargs)

class DummyContext:
    pass

@pytest.mark.asyncio
async def test_read_meeting_formats_full_record(monkeypatch):
    # Arrange: fake service returns datetimes (should be isoformated by tool)
    s = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    e = s + timedelta(hours=2)

    calls = {}
    async def fake_read_meeting_by_appointment_number(appointment_no):
        calls["appointment_no_type"] = type(appointment_no).__name__
        return {
            "appointment_no": str(appointment_no),
            "customer": "Alex Client",
            "start": s,
            "end": e,
            "status": "scheduled",
        }

    # Patch only this function on the sched namespace used by the tools module
    monkeypatch.setattr(tools, "sched", types.SimpleNamespace(
        read_meeting_by_appointment_number=fake_read_meeting_by_appointment_number
    ), raising=True)

    # Act
    y = await _call(read_meeting, DummyContext(), appointment_no="A123")
    data = yaml.safe_load(y)

    # Assert
    assert data["appointment_no"] == "A123"
    assert data["start"].endswith("+00:00")
    assert data["end"].endswith("+00:00")
    assert calls["appointment_no_type"] == "str"  # tool should pass a string

@pytest.mark.asyncio
async def test_cancel_meeting_passthrough_yaml(monkeypatch):
    async def fake_cancel_meeting(appointment_no):
        return {"appointment_no": str(appointment_no), "cancelled": True}

    monkeypatch.setattr(tools, "sched", types.SimpleNamespace(
        cancel_meeting=fake_cancel_meeting
    ), raising=True)

    y = await _call(cancel_meeting, DummyContext(), appointment_no="A123")
    data = yaml.safe_load(y)
    assert data["appointment_no"] == "A123"
    assert data["cancelled"] is True
