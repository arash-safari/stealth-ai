import re
from typing import Any, Optional, Tuple
from .state import SLOT_TO_STATE,StateKey

HIGH_CONF = 0.80
MID_CONF = 0.55

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s]{7,}\d)")
TIME_RANGE_RE = re.compile(r"(?P<start>\d{1,2}:\d{2})\s*[-–]\s*(?P<end>\d{1,2}:\d{2})")

def _normalize_phone(s: str) -> Optional[str]:
    m = PHONE_RE.search(s or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    if len(digits) < 10:
        return None
    return "+" + digits if not s.strip().startswith("+") else s.strip()

def _parse_time_window(s: str) -> Optional[Tuple[str, str]]:
    if not s:
        return None
    s = s.lower().strip()
    if "morning" in s:
        return ("08:00", "12:00")
    if "afternoon" in s:
        return ("12:00", "16:00")
    if "evening" in s:
        return ("16:00", "20:00")
    m = TIME_RANGE_RE.search(s)
    if not m:
        return None
    return (m.group("start"), m.group("end"))

def validate_and_normalize(slot: str, raw_value: str) -> Tuple[bool, Any, str]:
    """
    Returns: (is_valid, normalized_value, reason_if_invalid)
    Keep it simple & deterministic; the LLM gives us a confidence too.
    """
    v = (raw_value or "").strip()
    if not v:
        return (False, None, "empty")

    key = SLOT_TO_STATE.get(slot)

    if key == StateKey.user_phone_number:
        p = _normalize_phone(v)
        return (p is not None, p, "bad_phone" if p is None else "")

    if key == StateKey.user_window_slot_meeting_time:
        tw = _parse_time_window(v)
        return (tw is not None, tw, "bad_time_window" if tw is None else "")

    if key == StateKey.user_address:
        # trivial check: at least street + zip-like digits
        has_digits = bool(re.search(r"\d{4,}", v))
        has_words = len(re.findall(r"[A-Za-z]+", v)) >= 2
        ok = has_digits and has_words
        return (ok, v, "bad_address" if not ok else "")

    # default: accept non-empty
    return (True, v, "")
