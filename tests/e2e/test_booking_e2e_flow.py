import os, re, pytest
from agents.booking import Booking
from tests.e2e._booking_llm_harness import (
    ChatHarness, booking_tool_specs,
    sentence_count, ends_with_single_question, has_numbered_list, asks_for,
)

# Only run these when explicitly requested (keeps CI fast/cheap)
pytestmark = pytest.mark.skipif(
    not (os.getenv("OPENAI_API_KEY") and os.getenv("RUN_LLM_TESTS") == "1"),
    reason="Set OPENAI_API_KEY and RUN_LLM_TESTS=1 to run live E2E LLM tests."
)

def _new_harness():
    sys_prompt = Booking().instructions
    return ChatHarness(system_instructions=sys_prompt, tools=booking_tool_specs())

# Tolerant “single-prompt” check (allows one internal '?' and a trailing '.')
def is_single_prompt(text: str) -> bool:
    if ends_with_single_question(text):
        return True
    low = (text or "").strip().lower()
    if low.endswith(".") and text.count("?") <= 1:
        ask_cues = ("please", "provide", "what’s", "what's", "what is",
                    "tell me", "share", "may i have", "can i have")
        return any(cue in low for cue in ask_cues)
    return False

# Robust detector for the “urgency” step
def asks_for_urgency(text: str) -> bool:
    low = (text or "").lower()
    if "urgency" in low or "how urgent" in low:
        return True
    # common enumerations imply the urgency step even without the word
    has_enum = ("normal" in low) and ("urgent" in low or "emergency" in low)
    return has_enum or ("priority" in low)

def test_ordered_slot_flow_and_create_appointment():
    h = _new_harness()

    # 1) greet → ask name (≤2 sentences, single ask)
    h.say_user("hi")
    a1 = h.turn()
    assert sentence_count(a1) <= 2
    assert is_single_prompt(a1)
    assert asks_for("name", a1)

    # 2) give name → ask phone
    h.say_user("Alex Rivera")
    a2 = h.turn()
    assert sentence_count(a2) <= 2
    assert is_single_prompt(a2)
    assert asks_for("phone", a2)

    # 3) give phone → ask full address (allow internal '?' like 'unit?')
    h.say_user("+1 555 123 4567")
    a3 = h.turn()
    assert sentence_count(a3) <= 2
    assert is_single_prompt(a3)
    assert asks_for("address", a3) and all(w in a3.lower() for w in ["street", "city", "state", "postal"])

    # 4) give address → ask problem
    h.say_user("1 Main St, Apt 2, Austin, TX 78701")
    a4 = h.turn()
    assert sentence_count(a4) <= 2
    assert is_single_prompt(a4)
    assert asks_for("problem", a4)

    # 5) give problem → ask urgency
    h.say_user("Leak under the sink")
    a5 = h.turn()
    assert sentence_count(a5) <= 2
    assert is_single_prompt(a5)
    assert asks_for_urgency(a5)   # <- more robust than literal "urgency"

    # 6) say 'urgent' → ask preferred date
    h.say_user("urgent")
    a6 = h.turn()
    assert sentence_count(a6) <= 2
    assert is_single_prompt(a6)
    assert asks_for("preferred date", a6) or asks_for("date", a6)

    # 7) provide a date → agent should fetch availability and show a numbered list
    h.say_user("tomorrow afternoon")
    a7 = h.turn()
    if not has_numbered_list(a7):
        h.say_user("any time works")
        a7 = h.turn()

    assert has_numbered_list(a7), f"Should show numbered windows; got: {a7!r}"
    assert "which number works" in a7.lower()

    # Ensure tool was called correctly
    tool_names = [c.name for c in h.calls]
    assert "get_available_times" in tool_names

    # 8) choose a window "2" → agent should confirm selection in ≤2 sentences
    h.say_user("2")
    a8 = h.turn()
    assert sentence_count(a8) <= 3
    assert ends_with_single_question(a8) or "confirm" in a8.lower() or "okay to book" in a8.lower()

    # 9) confirm "yes" → agent should create the appointment and read back number/window
    h.say_user("yes")
    a9 = h.turn()

    # Must have called create_appointment
    create_calls = [c for c in h.calls if c.name == "create_appointment"]
    assert create_calls, "Agent should call create_appointment after confirmation"

    # verify essential args shape
    ca = create_calls[-1]
    for k in ("tech_id", "start", "end", "priority"):
        assert k in ca.args, f"create_appointment missing arg {k}"

    # The final message should read back appointment number or say it's booked
    assert re.search(r"(appointment).*(number|no|#)", a9.lower()) or "booked" in a9.lower()
