import inspect
import pytest
from agents.pricing import Pricing

class DummyUser:
    def __init__(self, desc=""):
        self.problem_description = desc
        self.estimate_low = None
        self.estimate_high = None

class DummyCtx:
    def __init__(self, desc=""):
        self.userdata = DummyUser(desc)

async def _call_estimate(agent: Pricing, ctx: DummyCtx):
    """Call get_estimate safely whether @function_tool wrapped or not."""
    meth = agent.get_estimate
    wrapped = getattr(meth, "__wrapped__", None)
    if wrapped is not None:
        bound = wrapped.__get__(agent, type(agent))
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx)
        return bound(ctx)
    if inspect.iscoroutinefunction(meth):
        return await meth(ctx)
    return meth(ctx)

def _tool_names(agent):
    names = set()
    for t in agent.tools:
        f = getattr(t, "__wrapped__", None) or t
        names.add(getattr(f, "__name__", str(f)))
    return names

def test_pricing_agent_wiring():
    ag = Pricing()
    instr = ag.instructions.lower()
    assert "pricing agent" in instr
    assert "range" in instr
    assert "diagnosis" in instr
    assert ag.tts is not None  # TTS configured (cartesia)

    # Depending on BaseAgent, tools may auto-include @function_tool methods.
    # Accept either no tools or a single 'get_estimate' tool.
    tool_names = _tool_names(ag)
    if ag.tools:
        assert "get_estimate" in tool_names
    else:
        assert tool_names == set()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "desc,expected_low,expected_high",
    [
        ("clogged toilet not draining", 120, 250),          # matches ["clog","drain","toilet"]
        ("pipe leak under sink", 180, 450),                 # matches ["leak","pipe","burst"]
        ("WATER HEATER making noise", 250, 1200),           # case-insensitive match
        ("install new faucet", 90, 220),                    # matches ["faucet","tap","install"]
        ("garbage disposal jammed", 150, 350),              # matches disposal row
        ("unknown issue text", 110, 400),                   # fallback default
    ],
)
async def test_get_estimate_ranges(desc, expected_low, expected_high):
    ag = Pricing()
    ctx = DummyCtx(desc)
    msg = await _call_estimate(ag, ctx)

    # Message should mention the numbers
    assert "Estimated range:" in msg
    assert f"${expected_low:.0f}" in msg
    assert f"${expected_high:.0f}" in msg

    # Userdata should be updated with floats
    assert isinstance(ctx.userdata.estimate_low, float)
    assert isinstance(ctx.userdata.estimate_high, float)
    assert ctx.userdata.estimate_low == float(expected_low)
    assert ctx.userdata.estimate_high == float(expected_high)

@pytest.mark.asyncio
async def test_get_estimate_empty_description_uses_default():
    ag = Pricing()
    ctx = DummyCtx(desc="")
    msg = await _call_estimate(ag, ctx)
    assert "$110" in msg and "$400" in msg
    assert ctx.userdata.estimate_low == 110.0
    assert ctx.userdata.estimate_high == 400.0
