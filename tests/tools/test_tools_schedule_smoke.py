# tests/tools/test_tools_schedule_smoke.py
import asyncio
import types
from datetime import datetime, timezone, timedelta
import inspect
import yaml
import pytest

tools = pytest.importorskip("tools.tools_schedule")

# ---------- light stubs ----------

class DummyUser:
    def __init__(self):
        # required by create_appointment
        self.customer_name = "Alex Client"
        self.customer_phone = "+15551234567"
        self.customer_email = "alex@example.com"
        self.street = "1 Main St"
        self.unit = "Apt 2"
        self.city = "Austin"
        self.state = "TX"
        self.postal_code = "78701"
        self.urgency = "urgent"
        self.problem_description = "Leak under the sink"
        self.appointment_date = "2025-09-10"
        self.appointment_window = "14:00-16:00"
        self.appointment_id = None
        self.appointment_status = None

    def address_str(self):
        return f"{self.street}, {self.unit}, {self.city} {self.state} {self.postal_code}"

class DummyContext:
    def __init__(self):
        self.userdata = DummyUser()

def _unwrap(fn):
    return getattr(fn, "__wrapped__", fn)

async def _call(fn, *args, **kwargs):
    target = _unwrap(fn)
    if inspect.iscoroutinefunction(target):
        return await target(*args, **kwargs)
    return target(*args, **kwargs)

# ---------- monkeypatch helpers ----------

@pytest.fixture
def patch_services(monkeypatch):
    """
    Patches tools.users and tools.sched with deterministic fakes.
    """
    # users service fakes
    async def fake_get_user_by_phone(phone):
        return None
    async def fake_create_user(full_name, phone, email=None):
        return {"id": "user-1", "full_name": full_name, "phone": phone, "email": email}
    async def fake_get_default_address(user_id):
        return None
    async def fake_add_address(**kwargs):
        return {"ok": True, **kwargs}

    monkeypatch.setattr(tools, "users", types.SimpleNamespace(
        get_user_by_phone=fake_get_user_by_phone,
        create_user=fake_create_user,
        get_default_address=fake_get_default_address,
        add_address=fake_add_address,
    ), raising=True)

    # schedule service fakes (filled per-test if needed)
    ns = types.SimpleNamespace()
    monkeypatch.setattr(tools, "sched", ns, raising=True)
    return ns

# ---------- tests ----------

@pytest.mark.asyncio
async def test_get_today_yaml():
    ctx = DummyContext()
    out = await _call(tools.get_today, ctx, tz="UTC", fmt="%Y-%m-%d")
    data = yaml.safe_load(out)
    assert "today" in data and "date" in data["today"] and "iso" in data["today"]

@pytest.mark.asyncio
async def test_get_available_times_yaml(patch_services):
    # Provide 3 deterministic slots from sched.get_available_times
    now = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    slots = [
        {"tech_id": "t-1", "start": now, "end": now + timedelta(hours=2), "source": "db"},
        {"tech_id": "t-2", "start": now + timedelta(hours=3), "end": now + timedelta(hours=5), "source": "db"},
        {"tech_id": "t-3", "start": now + timedelta(days=1), "end": now + timedelta(days=1, hours=2), "source": "google"},
    ]
    async def fake_get_available_times(**kwargs):
        return slots
    patch_services.get_available_times = fake_get_available_times

    ctx = DummyContext()
    out = await _call(
        tools.get_available_times,
        ctx,
        skill="plumbing",
        duration_min=120,
        priority="P2",
        date_from=None,
        date_to=None,
        limit=6,
        respect_google_busy=True,
    )
    data = yaml.safe_load(out)
    assert "slots" in data and len(data["slots"]) == 3
    assert data["slots"][0]["tech_id"] == "t-1"
    assert data["slots"][0]["start"].endswith("+00:00")

@pytest.mark.asyncio
async def test_get_nearest_available_time_none(patch_services):
    async def fake_get_available_times(**kwargs):
        return []
    patch_services.get_available_times = fake_get_available_times

    ctx = DummyContext()
    out = await _call(
        tools.get_nearest_available_time,
        ctx,
        skill="plumbing",
        duration_min=120,
        priority="P3",
        after=None,
        respect_google_busy=True,
    )
    data = yaml.safe_load(out)
    assert data["nearest_slot"] is None
    assert "message" in data

@pytest.mark.asyncio
async def test_svc_hold_slot_formats_yaml(patch_services):
    now = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    async def fake_hold_slot(**kwargs):
        return {
            "tech_id": kwargs["tech_id"],
            "user_id": kwargs["user_id"],
            "start": now,
            "end": now + timedelta(hours=2),
            "expires_at": now + timedelta(minutes=3),
        }
    patch_services.hold_slot = fake_hold_slot

    ctx = DummyContext()
    out = await _call(
        tools.svc_hold_slot,
        ctx,
        tech_id="t-9",
        user_id="user-1",
        start="2025-09-10T14:00:00Z",
        end="2025-09-10T16:00:00Z",
        ttl_seconds=180,
        request_text="hold this please",
        show_tentative_on_google=False,
    )
    data = yaml.safe_load(out)
    assert data["tech_id"] == "t-9"
    assert data["start"].endswith("+00:00")
    assert data["expires_at"].endswith("+00:00")

@pytest.mark.asyncio
async def test_create_appointment_happy_path(patch_services):
    """
    No explicit date_from/date_to -> uses userdata.appointment_date/window, searches for slots,
    picks one inside the window, creates meeting, updates userdata, returns YAML.
    """
    # availability within 14:00-16:00 window
    s = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    e = datetime(2025, 9, 10, 16, 0, tzinfo=timezone.utc)
    async def fake_get_available_times(**kwargs):
        return [
            {"tech_id": "t-1", "start": s, "end": e, "source": "db"},
            {"tech_id": "t-2", "start": s + timedelta(hours=3), "end": e + timedelta(hours=3), "source": "db"},
        ]
    async def fake_create_meeting(**kwargs):
        return {"id": "A123", "start": s, "end": e, "tech_id": kwargs["tech_id"]}

    patch_services.get_available_times = fake_get_available_times
    patch_services.create_meeting = fake_create_meeting

    # users service already patched in fixture; ensure its funcs exist
    # (done in patch_services)

    ctx = DummyContext()
    out = await _call(
        tools.create_appointment,
        ctx,
        skill="plumbing",
        duration_min=120,
        date_from=None,
        date_to=None,
        respect_google_busy=True,
    )
    data = yaml.safe_load(out)
    assert data["message"].startswith("Appointment created")
    assert data["appointment"]["start"].endswith("+00:00")
    assert ctx.userdata.appointment_status == "scheduled"
    assert ctx.userdata.appointment_id == "A123" or ctx.userdata.appointment_id  # accepts either id/no.

@pytest.mark.asyncio
async def test_create_appointment_invalid_window(patch_services):
    # Not called: we just verify validation branch before scheduler
    ctx = DummyContext()
    out = await _call(
        tools.create_appointment,
        ctx,
        date_from="2025-09-10T16:00:00Z",
        date_to="2025-09-10T15:00:00Z",
    )
    assert "Invalid window" in out

@pytest.mark.asyncio
async def test_read_update_cancel_meeting(patch_services):
    s = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    e = s + timedelta(hours=2)

    async def fake_read_meeting_by_appointment_number(appointment_no):
        return {"appointment_no": appointment_no, "start": s, "end": e, "status": "scheduled"}

    async def fake_update_meeting(**kwargs):
        return {"appointment_no": kwargs["appointment_no"], "start": s + timedelta(hours=1), "end": e + timedelta(hours=1)}

    async def fake_cancel_meeting(appointment_no):
        return {"appointment_no": appointment_no, "cancelled": True}

    patch_services.read_meeting_by_appointment_number = fake_read_meeting_by_appointment_number
    patch_services.update_meeting = fake_update_meeting
    patch_services.cancel_meeting = fake_cancel_meeting

    ctx = DummyContext()

    # read
    y1 = yaml.safe_load(await _call(tools.read_meeting, ctx, appointment_no="A123"))
    assert y1["appointment_no"] == "A123"
    assert y1["start"].endswith("+00:00")

    # update
    y2 = yaml.safe_load(await _call(tools.update_meeting, ctx, appointment_no="A123", start="2025-09-10T15:00:00Z"))
    assert y2["start"].endswith("+00:00")

    # cancel
    y3 = yaml.safe_load(await _call(tools.cancel_meeting, ctx, appointment_no="A123"))
    assert y3["cancelled"] is True

@pytest.mark.asyncio
async def test_create_earliest_meeting(patch_services):
    s = datetime(2025, 9, 10, 14, 0, tzinfo=timezone.utc)
    async def fake_create_earliest_meeting(**kwargs):
        return {"id": "AM1", "start": s, "end": s + timedelta(hours=2)}
    patch_services.create_earliest_meeting = fake_create_earliest_meeting

    ctx = DummyContext()
    out = await _call(
        tools.create_earliest_meeting,
        ctx,
        user_id="user-1",
        skill="plumbing",
        duration_min=120,
        priority="P2",
        request_text="please asap",
    )
    data = yaml.safe_load(out)
    assert data["start"].endswith("+00:00")
    assert data["end"].endswith("+00:00")
