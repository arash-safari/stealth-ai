import inspect
import pytest
from agents.booking import Booking

# ---------- tiny stubs ----------

class DummyUser:
    def __init__(self):
        # address fields
        self.street = None
        self.unit = None
        self.city = None
        self.state = None
        self.postal_code = None

        # contact
        self.customer_name = None
        self.customer_phone = None
        self.customer_email = None

        # appointment prefs
        self.appointment_date = None
        self.appointment_window = None
        self.urgency = None
        self.problem_description = None

        # outputs
        self.appointment_id = None
        self.appointment_status = None

class DummyCtx:
    def __init__(self):
        self.userdata = DummyUser()

# unwrap-safe invoker for @function_tool methods on the agent
async def _call_confirm(agent: Booking, ctx: DummyCtx):
    meth = agent.confirm_appointment
    wrapped = getattr(meth, "__wrapped__", None)
    if wrapped is not None:
        bound = wrapped.__get__(agent, type(agent))
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx)
        return bound(ctx)
    if inspect.iscoroutinefunction(meth):
        return await meth(ctx)
    return meth(ctx)

# ---------- behavior tests ----------

def test_booking_agent_wiring():
    ag = Booking()
    s = ag.instructions.lower()
    # high-level behavior guarantees (what the user experiences)
    assert "one question per turn" in s or "exactly one question" in s
    assert "collect (in order): name, phone" in s
    # tools presence (not strict equality; impl may add more)
    tool_names = {getattr(t, "__name__", getattr(getattr(t, "__wrapped__", None), "__name__", str(t)))
                  for t in ag.tools}
    expected = {"get_available_times", "get_nearest_available_time", "get_today", "create_appointment"}
    assert expected.issubset(tool_names)
    assert ag.tts is not None

@pytest.mark.asyncio
async def test_confirm_reports_missing_in_correct_order():
    """
    With nothing provided, confirm_appointment should list *address, date/window, name, phone*
    in that exact order, per spec.
    """
    ag = Booking()
    ctx = DummyCtx()

    msg = await _call_confirm(ag, ctx)
    assert msg == "Missing required info: address, date/window, name, phone. Please provide these first."

@pytest.mark.asyncio
async def test_confirm_after_address_still_asks_for_window_then_contact():
    """
    Once a full address is present, the next missing should be date/window, then name, then phone.
    """
    ag = Booking(); ctx = DummyCtx()
    u = ctx.userdata
    u.street, u.city, u.state, u.postal_code = "1 Main", "Austin", "TX", "78701"

    msg = await _call_confirm(ag, ctx)
    assert msg == "Missing required info: date/window, name, phone. Please provide these first."

@pytest.mark.asyncio
async def test_confirm_after_window_and_name_needs_phone_only():
    ag = Booking(); ctx = DummyCtx()
    u = ctx.userdata
    # full address
    u.street, u.city, u.state, u.postal_code = "1 Main", "Austin", "TX", "78701"
    # date/window
    u.appointment_date, u.appointment_window = "2025-09-10", "14:00–16:00"
    # name only
    u.customer_name = "Alex Client"

    msg = await _call_confirm(ag, ctx)
    assert msg == "Missing required info: phone. Please provide these first."

@pytest.mark.asyncio
async def test_confirm_success_sets_status_id_and_transfers(monkeypatch):
    """
    When all required info is present, the method should:
      - set appointment_status = 'scheduled'
      - generate an 8-char appointment_id
      - transfer to 'router' agent
    """
    ag = Booking(); ctx = DummyCtx()
    u = ctx.userdata
    u.street, u.city, u.state, u.postal_code = "1 Main", "Austin", "TX", "78701"
    u.appointment_date, u.appointment_window = "2025-09-10", "14:00–16:00"
    u.customer_name, u.customer_phone = "Sam Customer", "+15551234567"

    called = {}
    async def fake_transfer(name, context):
        called["name"] = name
        called["ctx_id"] = id(context)
        return "ROUTED_OK"
    monkeypatch.setattr(ag, "_transfer_to_agent", fake_transfer)

    out = await _call_confirm(ag, ctx)

    assert out == "ROUTED_OK"
    assert u.appointment_status == "scheduled"
    assert isinstance(u.appointment_id, str) and len(u.appointment_id) == 8
    assert called["name"] == "router" and called["ctx_id"] == id(ctx)

@pytest.mark.asyncio
async def test_confirm_requires_full_address_not_partial():
    """
    Partial address should still request 'address' (street+city+state+postal are required).
    """
    ag = Booking(); ctx = DummyCtx()
    u = ctx.userdata
    u.street = "123 Road"  # missing city/state/postal

    msg = await _call_confirm(ag, ctx)
    # still demands full address first
    assert msg.startswith("Missing required info: address")
