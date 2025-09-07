from datetime import datetime, timezone, timedelta
import types
import yaml
import inspect
import pytest

tools = pytest.importorskip("tools.tools_schedule")

def _unwrap(f): return getattr(f, "__wrapped__", f)
async def _call(f, *a, **k):
    f = _unwrap(f)
    if inspect.iscoroutinefunction(f): return await f(*a, **k)
    return f(*a, **k)

class DummyCtx: pass

@pytest.mark.asyncio
async def test_confirm_reschedule_returns_number_and_iso(monkeypatch):
    s = datetime(2025, 9, 11, 16, 0, tzinfo=timezone.utc)
    e = s + timedelta(hours=2)

    async def fake_update_meeting(**kwargs):
        assert kwargs["appointment_no"] == "A123"
        assert kwargs["start"] == s and kwargs["end"] == e
        return {"appointment_no": "A123", "start": s, "end": e, "status": "scheduled"}

    monkeypatch.setattr(tools, "sched", types.SimpleNamespace(update_meeting=fake_update_meeting), raising=True)

    y = await _call(
        tools.confirm_reschedule,
        DummyCtx(),
        appointment_no="A123",
        start="2025-09-11T16:00:00Z",
        end="2025-09-11T18:00:00Z",
        request_text="move please",
    )
    data = yaml.safe_load(y)
    assert data["message"] == "Rescheduled"
    assert data["appointment_no"] == "A123"
    assert data["appointment"]["start"].endswith("+00:00")
    assert data["appointment"]["end"].endswith("+00:00")
