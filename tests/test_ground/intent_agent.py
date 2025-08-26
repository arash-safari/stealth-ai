# intent_tool.py
# pip install --upgrade openai
import os
import json
from typing import Dict, List
from openai import OpenAI

MODEL = "gpt-4o-mini"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -- Tool schema the model must call --
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report_intent",
            "description": "Return the user's intent classification for the plumbing workflow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_code": {
                        "type": "string",
                        "enum": ["BOOK", "CANCEL", "ETA", "OTHER", "PRICE", "RESCHEDULE", "STATUS"]
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "intent_candidates": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            # IMPORTANT: exclude OTHER from candidates
                            "enum": ["BOOK", "CANCEL", "ETA", "PRICE", "RESCHEDULE", "STATUS"]
                        },
                        "minItems": 1
                    }
                },
                "required": ["intent_code", "confidence", "rationale", "intent_candidates"],
                "additionalProperties": False
            },
        },
    }
]

# -- Instruction block (verbatim to your spec) --
INSTRUCTION = """You are an intent router for a plumbing company. you should classify user intent in one of valid intent code classes, Pick exactly ONE intent_code that best matches the user's request.
Use ONLY the codes from the Intents Block below. Never invent new codes.

Call the tool: report_intent(intent_code=<CODE>, confidence=<0..1>, rationale="<short reason>"[, intent_candidates=["<CODE>", ...]]).
confidence must be a float between 0 and 1 (not a percent).

Rules for rationale and candidates:
- rationale MUST be a concise string explaining your choice (e.g., "Emergency leak → immediate dispatch").
- If intent_code is OTHER, you SHOULD include intent_candidates with your best guesses (e.g., ["BOOK"]).
- Do NOT include any top-level field named "candidates"; always use "intent_candidates".
- Symptom/emergency language (leak, burst, clog, no hot water, 'emergency') usually implies BOOK is likely — consider BOOK in intent_candidates when appropriate.
- Do NOT speak normal text; only call the tool.
you should always have intent_candidates in lists and it should have at least one item, and this list shouldn't contain OTHER

Intents Block:
- BOOK: Customer wants to book a visit to repair a service.
- CANCEL: Customer wants to cancel an existing appointment.
- ETA: Customer asks for the technician’s ETA or arrival window for an existing appointment.
- OTHER: Catch-all flow when the request doesn’t match predefined paths; routes to a human.
- PRICE: Customer asks about pricing, fees, or rates; optionally transitions into booking.
- RESCHEDULE: Customer wants to change the time of an existing appointment.
- STATUS: Customer asks for the current status or state of an existing appointment.

Now, call report_intent exactly once, with NO extra text.
"""

def classify_intent(user_text: str) -> Dict:
    """Return {'intent_code','confidence','rationale','intent_candidates'} by forcing the tool call."""
    messages = [
        {"role": "system", "content": "You are a helpful, concise assistant."},
        {"role": "system", "content": INSTRUCTION},
        {"role": "user", "content": user_text},
    ]
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "report_intent"}},
        temperature=0,
    )
    tool_call = resp.choices[0].message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    # Clamp confidence and return
    args["confidence"] = max(0.0, min(1.0, float(args["confidence"])))
    return args

def print_report_intent_line(args: Dict) -> None:
    """Print the exact one-liner format required by your router."""
    rationale = str(args["rationale"]).replace('"', '\\"')
    print(
        f'report_intent('
        f'intent_code="{args["intent_code"]}", '
        f'confidence={args["confidence"]:.2f}, '
        f'rationale="{rationale}", '
        f'intent_candidates={json.dumps(args["intent_candidates"])}'
        f')'
    )

# Optional: small CLI so this file can be run directly
if __name__ == "__main__":
    try:
        user_text = input("Enter user message: ").strip()
    except EOFError:
        user_text = ""
    res = classify_intent(user_text or "There's a steady leak under the kitchen sink, and it's an emergency.")
    print_report_intent_line(res)
