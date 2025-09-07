import asyncio
import types
import uuid
import pytest

# Adjust this import to your project structure
from agents.booking import Booking


# ---------- minimal stubs ----------

class DummyUser:
    def __init__(self):
        # address
        self.street = None
        self.city = None
        self.state = None
        self.postal_code = None
        # booking selections
        self.appointment_date = None
        self.appointment_window = None
        # contact
        self.customer_name = None
        self.customer_phone = None
        # outputs
        self.appointment_id = None
        self.appointment_status = None


class DummyContext:
    def __init__(self):
        self.userdata = DummyUser()


# Some decorators wrap the function; make a helper that calls the underlying
# function if it's been wrapped (i.e., has __wrapped__).
async def call_confirm(booking: Booking, ctx: DummyContext):
    method = getattr(booking.confirm_appointment, "__wrapped__", None)
    if method is not None:
        # __wrapped__ is an unbound function; pass self explicitly
        return await method(booking, ctx)
    return await booking.confirm_appointment(ctx)


# ---------- fixtures ----------

@pytest.fixture
def ctx():
    return DummyContext()

@pytest.fixture
def booking():
    return Booking()

@pytest.fixture
def filled_user(ctx):
    u = ctx.userdata
    u.street, u.city, u.state, u.postal_code = "1 Main St", "Austin", "TX", "78701"
    u.customer_name, u.customer_phone = "Alex", "+15551234567"
    u.appointment_date, u.appointment_window = "2025-09-10", "14:00–16:00"
    return u


# ---------- tests: missing fields ----------

@pytest.mark.asyncio
async def test_confirm_appointment_all_missing(booking, ctx):
    msg = await call_confirm(booking, ctx)
    # Exact string is deterministic given the method body
    assert msg == (
        "Missing required info: address, date/window, name, phone. Please provide these first."
    )

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator, expected",
    [
        # Missing address only
        (
            lambda u: [
                setattr(u, "street", None),
                setattr(u, "city", None),
                setattr(u, "state", None),
                setattr(u, "postal_code", None),
                setattr(u, "customer_name", "Alex"),
                setattr(u, "customer_phone", "+1"),
                setattr(u, "appointment_date", "2025-09-10"),
                setattr(u, "appointment_window", "10:00–12:00"),
            ],
            "Missing required info: address. Please provide these first.",
        ),
        # Missing date/window only
        (
            lambda u: [
                setattr(u, "street", "1 Main"),
                setattr(u, "city", "Austin"),
                setattr(u, "state", "TX"),
                setattr(u, "postal_code", "78701"),
                setattr(u, "customer_name", "Alex"),
                setattr(u, "customer_phone", "+1"),
                setattr(u, "appointment_date", None),
                setattr(u, "appointment_window", None),
            ],
            "Missing required info: date/window. Please provide these first.",
        ),
        # Missing name only
        (
            lambda u: [
                setattr(u, "street", "1 Main"),
                setattr(u, "city", "Austin"),
                setattr(u, "state", "TX"),
                setattr(u, "postal_code", "78701"),
                setattr(u, "customer_name", None),
                setattr(u, "customer_phone", "+1"),
                setattr(u, "appointment_date", "2025-09-10"),
                setattr(u, "appointment_window", "10:00–12:00"),
            ],
            "Missing required info: name. Please provide these first.",
        ),
        # Missing phone only
        (
            lambda u: [
                setattr(u, "street", "1 Main"),
                setattr(u, "city", "Austin"),
                setattr(u, "state", "TX"),
                setattr(u, "postal_code", "78701"),
                setattr(u, "customer_name", "Alex"),
                setattr(u, "customer_phone", None),
                setattr(u, "appointment_date", "2025-09-10"),
                setattr(u, "appointment_window", "10:00–12:00"),
            ],
            "Missing required info: phone. Please provide these first.",
        ),
        # Missing multiple: address + name (checks order)
        (
            lambda u: [
                setattr(u, "street", None),
                setattr(u, "city", None),
                setattr(u, "state", None),
                setattr(u, "postal_code", None),
                setattr(u, "customer_name", None),
                setattr(u, "customer_phone", "+1"),
                setattr(u, "appointment_date", "2025-09-10"),
                setattr(u, "appointment_window", "10:00–12:00"),
            ],
            "Missing required info: address, name. Please provide these first.",
        ),
    ],
)
async def test_confirm_appointment_partial_missing(booking, ctx, mutator, expected):
    mutator(ctx.userdata)
    msg = await call_confirm(booking, ctx)
    assert msg == expected


# ---------- tests: success path ----------

@pytest.mark.asyncio
async def test_confirm_appointment_success_sets_fields_and_transfers(booking, ctx, filled_user, monkeypatch):
    # Make UUID deterministic
    class FakeUUID:
        def __str__(self):
            return "cafebabe-dead-beef-feed-faceb00c0001"
    monkeypatch.setattr(uuid, "uuid4", lambda: FakeUUID())

    # Capture transfer call and return a known value
    called = {}
    async def fake_transfer(agent_name, context):
        called["agent_name"] = agent_name
        called["context_id"] = id(context)
        return "ROUTED_OK"
    monkeypatch.setattr(booking, "_transfer_to_agent", fake_transfer)

    result = await call_confirm(booking, ctx)

    # Returned value should come from the transfer
    assert result == "ROUTED_OK"

    # Appointment fields set
    u = ctx.userdata
    assert u.appointment_status == "scheduled"
    assert u.appointment_id == "cafebabe"  # first 8 chars of the fake uuid string

    # Transfer was invoked to the router with the same ctx
    assert called["agent_name"] == "router"
    assert called["context_id"] == id(ctx)


@pytest.mark.asyncio
async def test_confirm_appointment_transfer_failure_bubbles_up(booking, ctx, filled_user, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("router unavailable")
    monkeypatch.setattr(booking, "_transfer_to_agent", boom)

    with pytest.raises(RuntimeError) as ei:
        await call_confirm(booking, ctx)
    assert "router unavailable" in str(ei.value)
