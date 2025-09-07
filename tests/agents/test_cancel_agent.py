# tests/agents/test_cancel_agent.py
import pytest
from agents.cancel import Cancel

def _tool_name(t):
    # unwrap @function_tool if present
    f = getattr(t, "__wrapped__", None) or t
    return getattr(f, "__name__", str(f))

def test_cancel_agent_wiring():
    ag = Cancel()

    # 1) Instructions are present and meaningful
    istr = ag.instructions.lower()
    assert "cancel" in istr
    assert "confirm identity" in istr
    assert ag.tts is not None  # TTS configured

    # 2) Exactly the two tools are wired
    tool_names = {_tool_name(t) for t in ag.tools}
    assert tool_names == {"read_meeting", "cancel_meeting"}

def test_cancel_agent_has_no_local_cancel_method():
    ag = Cancel()
    # By design, Cancel agent defers to tools; no custom cancel_* method is expected
    assert not hasattr(ag, "cancel_appointment")
    # And we don't accidentally have a similarly named method either
    forbidden = [n for n in dir(ag) if n.startswith("cancel_") and callable(getattr(ag, n))]
    # Allow internal BaseAgent/private attrs; only fail if there are explicit cancel_* methods
    assert forbidden == [], f"Unexpected cancel_* methods found: {forbidden}"
