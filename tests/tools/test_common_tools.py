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

class DummySession:
    def __init__(self):
        # Some code accesses attributes on current_agent (or its .name)
        self.current_agent = types.SimpleNamespace(name="booking")
        self.changed_to = None

    async def change_agent(self, name: str):
        # If your tool calls this, we’re ready.
        self.changed_to = name
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
    # Many of your tools take (context, ...) — provide one by default
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
    Your tool's parameter might be named 'problem', 'description', 'text', or 'issue'.
    Detect the real name and pass it.
    """
    if update_problem is None:
        pytest.skip("update_problem not found")
    target = getattr(update_problem, "__wrapped__", update_problem)
    sig = inspect.signature(target)
    # Find the first parameter after 'context'
    params = [p for p in sig.parameters.values() if p.name != "context"]
    assert params, f"update_problem must accept a problem-like parameter; signature={sig}"
    problem_param = None

    # Prefer common names if present
    for candidate in ("problem", "description", "text", "details", "issue"):
        if candidate in sig.parameters:
            problem_param = candidate
            break
    if not problem_param:
        # Fallback to the first non-context parameter
        problem_param = params[0].name

    ctx = DummyContext()
    kwargs = {problem_param: "Leak under the sink"}
    await _call_tool(update_problem, ctx, **kwargs)

    # Accept either .problem or whatever your tool writes:
    # Try common fields first, then fallback to any str field containing our text.
    if getattr(ctx.userdata, "problem", None):
        assert ctx.userdata.problem == "Leak under the sink"
    else:
        # Probe other known attributes that might be used
        for attr in ("issue", "description", "details", "text"):
            if getattr(ctx.userdata, attr, None):
                assert getattr(ctx.userdata, attr) == "Leak under the sink"
                break
        else:
            pytest.fail("Tool didn't set a recognizable problem field on userdata")

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
    # Should be a nice string (your tool calls u.address_str())
    assert "1 Main St" in u.address_str()
    assert "Austin" in u.address_str()

@pytest.mark.asyncio
async def test_to_router_smoke():
    ctx = DummyContext()
    # We don’t assert exact return shape since implementations vary;
    # just ensure it can be invoked without error given a minimal session stub.
    out = await _call_tool(to_router, ctx)
    assert out is not None
