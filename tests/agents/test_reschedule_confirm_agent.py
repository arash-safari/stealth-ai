# tests/agents/test_reschedule_confirm_agent.py
import inspect
import types
import pytest
from agents.reschedule import Reschedule

class DummyUser:
    def __init__(self):
        self.appointment_id = "A123"
        self.appointment_status = "scheduled"
        self.appointment_date = None
        self.appointment_window = None

class DummyCtx:
    def __init__(self):
        self.userdata = DummyUser()

async def _call_confirm_reschedule(agent: Reschedule, ctx: DummyCtx, **kwargs):
    """
    Call agent.confirm_reschedule regardless of decorator/binding:
    - If @function_tool added __wrapped__, bind it to the instance first
      so we don't pass self twice.
    - Otherwise call the bound method directly.
    """
    meth = agent.confirm_reschedule
    wrapped = getattr(meth, "__wrapped__", None)
    if wrapped is not None:
        # Bind the original function to the instance
        bound = wrapped.__get__(agent, type(agent))
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx, **kwargs)
        return bound(ctx, **kwargs)
    # Already a bound method
    if inspect.iscoroutinefunction(meth):
        return await meth(ctx, **kwargs)
    return meth(ctx, **kwargs)

@pytest.mark.asyncio
async def test_confirm_reschedule_speaks_number_and_updates_userdata(monkeypatch):
    ag = Reschedule()
    ctx = DummyCtx()

    # Patch the tool reference the agent imported as tool_confirm_reschedule
    import agents.reschedule as resmod

    async def fake_tool(context, appointment_no, start, end, request_text=None):
        # Return YAML the agent will parse; include a *new* number
        return (
            "message: Rescheduled\n"
            "appointment_no: A456\n"
            "appointment:\n"
            f"  start: {start}\n"
            f"  end: {end}\n"
            "  status: scheduled\n"
        )

    # Provide an object with __wrapped__ so agent's unwrapping path works
    monkeypatch.setattr(
        resmod,
        "tool_confirm_reschedule",
        types.SimpleNamespace(__wrapped__=fake_tool),
        raising=True,
    )

    msg = await _call_confirm_reschedule(
        ag,
        ctx,
        start="2025-09-11T16:00:00Z",
        end="2025-09-11T18:00:00Z",
    )

    # The agent should SAY the new number and window
    assert "A456" in msg
    assert "2025-09-11 16:00-18:00" in msg

    # And userdata should reflect the change
    assert ctx.userdata.appointment_id == "A456"
    assert ctx.userdata.appointment_status == "rescheduled"
    assert ctx.userdata.appointment_date == "2025-09-11"
    assert ctx.userdata.appointment_window == "16:00-18:00"

@pytest.mark.asyncio
async def test_confirm_reschedule_requires_appointment_id():
    ag = Reschedule()
    ctx = DummyCtx()
    ctx.userdata.appointment_id = None  # no number on file

    out = await _call_confirm_reschedule(
        ag,
        ctx,
        start="2025-09-11T16:00:00Z",
        end="2025-09-11T18:00:00Z",
    )
    assert "No appointment number" in out
