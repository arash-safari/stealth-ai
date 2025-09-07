# tests/tools/test_tools_schedule_contracts.py
import inspect
import pytest

tools = pytest.importorskip("tools.tools_schedule")

REQ = {
    "get_available_times": [
        "context", "skill", "duration_min", "priority",
        "date_from", "date_to", "limit", "respect_google_busy",
    ],
    "get_nearest_available_time": [
        "context", "skill", "duration_min", "priority",
        "after", "respect_google_busy",
    ],
    "svc_hold_slot": [
        "context", "tech_id", "user_id", "start", "end",
        "ttl_seconds", "request_text", "show_tentative_on_google",
    ],
    "get_today": ["context", "tz", "fmt"],
    "create_appointment": [
        "context", "skill", "duration_min", "date_from", "date_to", "respect_google_busy",
    ],
    "read_meeting": ["context", "appointment_no"],
    "update_meeting": ["context", "appointment_no", "start", "end", "status", "request_text"],
    "cancel_meeting": ["context", "appointment_no"],
    "create_earliest_meeting": ["context", "user_id", "skill", "duration_min", "priority", "request_text"],
}

def _params(fn):
    sig = inspect.signature(fn)
    return [p.name for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)]

def test_tools_exist_and_callable():
    missing = [n for n in REQ if not hasattr(tools, n)]
    assert not missing, f"Missing tools: {missing}"
    for n in REQ:
        assert callable(getattr(tools, n)), f"{n} not callable"

@pytest.mark.parametrize("name,expected", REQ.items())
def test_signatures_match(name, expected):
    fn = getattr(tools, name)
    target = getattr(fn, "__wrapped__", fn)  # unwrap @function_tool
    params = _params(target)
    for req in expected:
        assert req in params, f"{name} must accept '{req}', got {params}"
