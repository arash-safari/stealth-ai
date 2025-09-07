import pytest

def _tool_names(agent):
    return {getattr(t, "__name__", getattr(getattr(t, "__wrapped__", None), "__name__", str(t))) for t in agent.tools}

@pytest.mark.asyncio
async def test_booking_agent_wiring():
    from agents.booking import Booking
    ag = Booking()
    names = _tool_names(ag)
    # core tools that the Booking agent depends on
    assert {"get_available_times", "get_nearest_available_time", "get_today", "create_appointment"} <= names
    assert ag.tts is not None
    assert "Booking agent" in ag.instructions

@pytest.mark.asyncio
async def test_cancel_agent_wiring():
    from agents.cancel import Cancel
    ag = Cancel()
    names = _tool_names(ag)
    assert {"read_meeting", "cancel_meeting"} <= names
    assert ag.tts is not None
    assert "cancellation agent" in ag.instructions.lower()
