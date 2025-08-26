# constants/prompts/intent_prompts.py
from __future__ import annotations
from typing import Optional, List, Dict, Any

__all__ = [
    "build_intent_classifier_prompt",
    "build_router_prompt",
    "plan_ask_intent",
]

# ---------- helpers ----------

def _natural_join(items: List[str]) -> str:
    items = [s for s in (items or []) if s]
    n = len(items)
    if n == 0:
        return ""
    if n == 1:
        return items[0]
    if n == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"

def _sanitize_codes(codes: Optional[List[str]]) -> List[str]:
    if not codes:
        return []
    out, seen = [], set()
    for c in codes:
        up = str(c or "").strip().upper()
        if up and up not in seen:
            seen.add(up)
            out.append(up)
    return out

def _filter_to_known(codes: List[str], flows: Dict[str, Any], *, include_other: bool = False) -> List[str]:
    out = []
    for c in codes:
        if c == "OTHER" and not include_other:
            continue
        if c in flows:
            out.append(c)
    return out

def _labels_for(codes: List[str], labels_map: Dict[str, str]) -> List[str]:
    return [labels_map.get(c, c.lower()) for c in codes]


# ---------- CLASSIFIER PROMPT ----------

def build_intent_classifier_prompt(intents: List[Dict[str, str]], history: str) -> str:
    valid_codes = [it["name"].upper() for it in intents]
    lines = []
    lines.append("You are an intent router for a plumbing company. you should classify user intent in one of valid intent code classes, Pick exactly ONE intent_code that best matches the user's request.")
    lines.append("Use ONLY the codes from the Intents Block below. Never invent new codes.")
    lines.append("")
    # NOTE: rationale is now a SHORT STRING (not JSON)
    lines.append('Call the tool: report_intent(intent_code=<CODE>, confidence=<0..1>, rationale="<short reason>"[, intent_candidates=["<CODE>", ...]]).')
    lines.append("confidence must be a float between 0 and 1 (not a percent).")
    lines.append("")
    lines.append("Rules for rationale and candidates:")
    lines.append('- rationale MUST be a concise string explaining your choice (e.g., "Emergency leak → immediate dispatch").')
    lines.append('- If intent_code is OTHER, you SHOULD include intent_candidates with your best guesses (e.g., ["BOOK"]).')
    lines.append('- Do NOT include any top-level field named "candidates"; always use "intent_candidates".')
    lines.append("- Symptom/emergency language (leak, burst, clog, no hot water, 'emergency') usually implies BOOK is likely — consider BOOK in intent_candidates when appropriate.")
    lines.append("- Do NOT speak normal text; only call the tool.")
    lines.append("you should always have intent_candidates in lists and it should have at least one item, and this list shouldn't contain OTHER")
    lines.append("Intents Block:")
    for it in intents:
        lines.append(f"- {it['name']}: {it.get('description','')}")
    lines.append("")
    lines.append("Recent history:")
    lines.append(history)
    lines.append("")
    lines.append("Now, call report_intent exactly once, with NO extra text.")
    lines.append("")
    lines.append("Examples:")
    lines.append("")
    prompt = "\n".join(lines)
    prompt += """[
        {
                "intent_code": "PRICE",
                "confidence": 0.82,
                "rationale": "User asked for pricing for drain cleaning.",
                "intent_candidates": ["PRICE", "ETA"]
        },
        {
                "intent_code": "BOOK",
                "confidence": 0.93,
                "rationale": "Emergency leak → immediate dispatch",
                "intent_candidates": ["BOOK"]
        },
        {
            "intent_code": "OTHER",
            "confidence": 0.62,
            "rationale": "User reports an emergency leak but didn't explicitly ask to book.",
            "intent_candidates": ["BOOK", "PRICE"]
        }
    ]"""
    return prompt


# ---------- ROUTER PROMPT (narrowed to candidates when present) ----------

def build_router_prompt(
    *,
    candidates: Optional[List[str]],
    labels_map: Dict[str, str],
    explicit_template: Optional[str],
    flows: Dict[str, Any],
) -> str:
    """
    Build the routing question from narrowed candidates. OTHER is never shown.
    """
    cand = _filter_to_known(_sanitize_codes(candidates), flows, include_other=False)
    if cand:
        option_labels = _labels_for(cand, labels_map)
    else:
        # fallback: all known except OTHER
        all_codes = [c for c in labels_map.keys() if c != "OTHER" and c in flows]
        option_labels = _labels_for(all_codes, labels_map)

    options_str = _natural_join(option_labels)

    if isinstance(explicit_template, str) and explicit_template.strip():
        if "{options}" in explicit_template:
            return explicit_template.replace("{options}", options_str).rstrip("?") + "?"
        if not cand:
            return explicit_template.rstrip("?") + "?"

    return f"Just to help me route you: do you want to {options_str}?"


# ---------- ASK-INTENT BUILDER ----------

def plan_ask_intent(
    *,
    text: Optional[str],
    slot: str,
    candidates: Optional[List[str]],
    labels_map: Dict[str, str],
    flows: Dict[str, Any],
    explicit_template: Optional[str],
) -> Dict[str, Any]:
    """
    Produce a question + expected-string for intent capture, narrowed to candidates when provided.
    Returns:
      {
        "text": <question to say>,
        "expected": <pipe list for slot LLM>,
        "codes": <sanitized candidate codes (no OTHER)>
      }
    """
    slot = (slot or "").strip().lower()
    codes = _filter_to_known(_sanitize_codes(candidates), flows, include_other=False)

    # yes/no path
    if slot == "intent_yesno":
        if text and text.strip():
            q = text.strip()
        else:
            if codes:
                lbl = labels_map.get(codes[0], codes[0].lower())
                q = f"It sounds like you want to {lbl}. Should I proceed?"
            else:
                q = "Should I proceed?"
        return {"text": q, "expected": "yes / no", "codes": codes}

    # multi-choice path
    if text and text.strip():
        question = text.strip()
    else:
        question = build_router_prompt(
            candidates=codes or None,
            labels_map=labels_map,
            explicit_template=explicit_template,
            flows=flows,
        )

    if codes:
        labels = _labels_for(codes, labels_map)
    else:
        labels = _labels_for([c for c in labels_map.keys() if c != "OTHER" and c in flows], labels_map)

    expected = " | ".join(labels + ["other"])
    return {"text": question, "expected": expected, "codes": codes}
