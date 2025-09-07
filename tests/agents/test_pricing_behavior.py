import inspect
import pytest
from agents.pricing import Pricing

# ---------- small helpers ----------

class DummyUser:
    def __init__(self, desc=""):
        self.problem_description = desc
        self.estimate_low = None
        self.estimate_high = None

class DummyCtx:
    def __init__(self, desc=""):
        self.userdata = DummyUser(desc)

async def _call_estimate(agent: Pricing, ctx: DummyCtx):
    """Call get_estimate whether or not it's wrapped by @function_tool."""
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

# ---------- behavioral tests ----------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "desc,expected_low,expected_high",
    [
        ("clogged toilet not draining", 120, 250),   # first row keywords
        ("pipe LEAK under slab", 180, 450),          # case-insensitive
        ("no hot water heater issue", 250, 1200),    # water heater bucket
        ("install new faucet in kitchen", 90, 220),  # faucet install
        ("garbage disposal jammed / humming", 150, 350),
        ("totally unknown thing", 110, 400),         # default fallback
    ],
)
async def test_message_contains_range_and_userdata_updates(desc, expected_low, expected_high):
    ag = Pricing()
    ctx = DummyCtx(desc)
    msg = await _call_estimate(ag, ctx)

    # Message content
    assert msg.startswith("Estimated range:")
    # allow either hyphen or en dash in output
    dash_ok = "–" if "–" in msg else "-"
    assert f"${expected_low:.0f}{dash_ok}${expected_high:.0f}" in msg or \
           (f"${expected_low:.0f}–${expected_high:.0f}" in msg) or \
           (f"${expected_low:.0f}-${expected_high:.0f}" in msg)

    # Side-effects on userdata
    assert ctx.userdata.estimate_low == float(expected_low)
    assert ctx.userdata.estimate_high == float(expected_high)

@pytest.mark.asyncio
async def test_keyword_precedence_first_match_wins():
    """
    If multiple buckets match, behavior should pick the FIRST bucket in ISSUE_TABLE.
    e.g., description includes both 'toilet' (clog bucket) and 'leak' (leak bucket) ->
    expect the 'clog/drain/toilet' range (120–250).
    """
    ag = Pricing()
    ctx = DummyCtx("toilet leak with slow drain")
    msg = await _call_estimate(ag, ctx)

    assert "$120" in msg and ("$250" in msg or "250" in msg)
    assert ctx.userdata.estimate_low == 120.0
    assert ctx.userdata.estimate_high == 250.0

@pytest.mark.asyncio
async def test_repeated_calls_overwrite_userdata():
    ag = Pricing()

    # 1st call -> faucet
    ctx = DummyCtx("new faucet install")
    msg1 = await _call_estimate(ag, ctx)
    assert "$90" in msg1 and "$220" in msg1
    assert (ctx.userdata.estimate_low, ctx.userdata.estimate_high) == (90.0, 220.0)

    # 2nd call with different text -> leak bucket overwrites values
    ctx.userdata.problem_description = "pipe leak under sink"
    msg2 = await _call_estimate(ag, ctx)
    assert "$180" in msg2 and "$450" in msg2
    assert (ctx.userdata.estimate_low, ctx.userdata.estimate_high) == (180.0, 450.0)

@pytest.mark.asyncio
async def test_message_formatting_mentions_cost_context():
    ag = Pricing()
    ctx = DummyCtx("clogged drain")
    msg = await _call_estimate(ag, ctx)
    # Your message includes this explanatory tail today; keep it stable.
    assert "labor + standard parts" in msg
    assert "taxes extra" in msg

# ---------- optional stretch test (xfail) ----------

@pytest.mark.asyncio
@pytest.mark.xfail(reason="Enable once get_estimate adds explicit on-site diagnosis recommendation.")
async def test_recommends_on_site_diagnosis_in_message():
    ag = Pricing()
    ctx = DummyCtx("unknown description")
    msg = await _call_estimate(ag, ctx)
    assert "on-site" in msg.lower() and "diagnosis" in msg.lower()
