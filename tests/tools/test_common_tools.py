# tests/tools/test_common_tools.py
import inspect
import types
import pytest

common_tools = pytest.importorskip("common.common_tools")
update_name = getattr(common_tools, "update_name", None)
update_phone = getattr(common_tools, "update_phone", None)
update_email = getattr(common_tools, "update_email", None)
update_address = getattr(common_tools, "update_address", None)
update_problem = getattr(common_tools, "update_problem", None)
to_router = getattr(common_tools, "to_router", None)

# --- helpers / stubs ---

class DummyUser:
    def __init__(self):
        self.customer_name = None
        self.customer_phone = None
        self.customer_email = None
        self.problem = None
        self.street = None
        self.unit = None
        self.city = None
        self.state = None
        self.postal_code = None

    def address_str(self) -> str:
        parts = []
        if self.street:
            s = self.street
            if self.unit:
                s = f"{s}, {self.unit}"
            parts.append(s)
        cityline = " ".join([p for p in [self.city, self.state, self.postal_code] if p])
        if cityline:
            parts.append(cityline)
        return ", ".join(parts) if parts else "(no address)"

class DummyAgent:
    def __init__(self):
        self.transfers = []

    async def _transfer_to_agent(self, name: str, context):
        self.transfers.append((name, id(context)))
        return {"changed_to": name, "context_id": id(context)}

class DummySession:
    def __init__(self):
        self.current_agent = DummyAgent()

    async def change_agent(self, name: str):
        # not used by your current tool, but available if needed
        return {"changed_to": name}

class DummyContext:
    def __init__(self):
        self.userdata = DummyUser()
        self.session = DummySession()

async def _call_tool(fn, ctx=None, **kwargs):
    """
    Tools can be decorated with @function_tool; call __wrapped__ if present.
    Handles both sync/async implementations.
    """
    if fn is None:
        pytest.skip("Tool not found")
    target = getattr(fn, "__wrapped__", fn)
    if ctx is None:
        ctx = DummyContext()
    if inspect.iscoroutinefunction(target):
        return await target(ctx, **kwargs)
    return target(ctx, **kwargs)

# --- tests ---

@pytest.mark.asyncio
async def test_update_name_sets_userdata():
    ctx = DummyContext()
    await _call_tool(update_name, ctx, name="Alex")
    assert ctx.userdata.customer_name == "Alex"

@pytest.mark.asyncio
async def test_update_phone_sets_userdata():
    ctx = DummyContext()
    await _call_tool(update_phone, ctx, phone="+1 (555) 123-4567")
    assert ctx.userdata.customer_phone  # truthy; exact format depends on your tool

@pytest.mark.asyncio
async def test_update_email_sets_userdata():
    ctx = DummyContext()
    await _call_tool(update_email, ctx, email="alex@example.com")
    assert ctx.userdata.customer_email == "alex@example.com"

@pytest.mark.asyncio
async def test_update_problem_sets_userdata():
    """
    Be resilient to parameter naming and target attribute differences.
    Pass the correct kwarg by inspecting the signature, and then accept
    any userdata attribute that was set to our problem text.
    """
    if update_problem is None:
        pytest.skip("update_problem not found")
    target = getattr(update_problem, "__wrapped__", update_problem)
    sig = inspect.signature(target)
    # choose a sensible parameter name
    for candidate in ("problem", "description", "text", "details", "issue"):
        if candidate in sig.parameters:
            param = candidate
            break
    else:
        # fallback: first non-context param
        params = [p for p in sig.parameters.values() if p.name != "context"]
        assert params, f"update_problem must accept a problem-like parameter; signature={sig}"
        param = params[0].name

    ctx = DummyContext()
    before = dict(ctx.userdata.__dict__)  # snapshot
    PROBLEM = "Leak under the sink"
    await _call_tool(update_problem, ctx, **{param: PROBLEM})
    after = dict(ctx.userdata.__dict__)

    # accept any attribute (existing or newly added) set to our problem text
    changed = {k: v for k, v in after.items() if before.get(k) != v}
    hit = [k for k, v in changed.items() if isinstance(v, str) and v == PROBLEM]
    if not hit:
        # also scan all attrs (some tools might set an attribute that wasn't in __dict__ before)
        hit = [k for k, v in after.items() if isinstance(v, str) and v == PROBLEM]

    assert hit, "Tool didn't set a recognizable problem-like field on userdata"

@pytest.mark.asyncio
async def test_update_address_sets_all_fields():
    ctx = DummyContext()
    await _call_tool(
        update_address, ctx,
        street="1 Main St",
        unit="Apt 2",
        city="Austin",
        state="TX",
        postal_code="78701",
    )
    u = ctx.userdata
    assert (u.street, u.unit, u.city, u.state, u.postal_code) == (
        "1 Main St", "Apt 2", "Austin", "TX", "78701"
    )
    assert "1 Main St" in u.address_str()
    assert "Austin" in u.address_str()

@pytest.mark.asyncio
async def test_to_router_smoke():
    ctx = DummyContext()
    out = await _call_tool(to_router, ctx)
    assert out is not None
    # and our stubbed agent recorded the transfer
    assert ctx.session.current_agent.transfers
    name, ctx_id = ctx.session.current_agent.transfers[-1]
    assert name == "router"
    assert ctx_id == id(ctx)
