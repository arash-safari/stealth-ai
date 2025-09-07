import pytest
from agents.booking import Booking

class DummyUser:
    def __init__(self):
        # address
        self.street = None; self.city = None; self.state = None; self.postal_code = None
        # selections
        self.appointment_date = None; self.appointment_window = None
        # contact
        self.customer_name = None; self.customer_phone = None
        # outputs
        self.appointment_id = None; self.appointment_status = None

class DummyCtx:
    def __init__(self): self.userdata = DummyUser()

# unwrap helper in case of @function_tool
async def _call_confirm(agent: Booking, ctx: DummyCtx):
    method = getattr(agent.confirm_appointment, "__wrapped__", None)
    return await (method(agent, ctx) if method else agent.confirm_appointment(ctx))

@pytest.mark.asyncio
async def test_confirm_appointment_reports_missing_in_order():
    ag = Booking(); ctx = DummyCtx()
    msg = await _call_confirm(ag, ctx)
    assert msg == "Missing required info: address, date/window, name, phone. Please provide these first."

@pytest.mark.asyncio
async def test_confirm_appointment_success_sets_status_and_transfers(monkeypatch):
    ag = Booking(); ctx = DummyCtx()
    u = ctx.userdata
    u.street, u.city, u.state, u.postal_code = "1 Main", "Austin", "TX", "78701"
    u.customer_name, u.customer_phone = "Sam", "+15551234567"
    u.appointment_date, u.appointment_window = "2025-09-10", "14:00–16:00"

    called = {}
    async def fake_transfer(name, context):
        called["name"] = name; called["ctx"] = id(context); return "OK"
    monkeypatch.setattr(ag, "_transfer_to_agent", fake_transfer)

    out = await _call_confirm(ag, ctx)
    assert out == "OK"
    assert u.appointment_status == "scheduled"
    assert isinstance(u.appointment_id, str) and len(u.appointment_id) == 8
    assert called["name"] == "router" and called["ctx"] == id(ctx)
