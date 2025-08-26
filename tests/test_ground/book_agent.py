# book_agent.py
# Minimal, single-LLM-per-turn BOOK flow (LLM handles ALL checks, re-asks, and 3-strikes)
# pip install --upgrade openai python-dotenv
import os, json
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# --- env / client ---
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local"); load_dotenv(ROOT / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o-mini"

# --- tiny domain stubs ---
def find_available_window(_state):
    d = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return {"suggested_windows": [f"{d} 10:00–14:00", f"{d} 14:00–18:00"]}

def check_if_support_address(address):
    # keep it dumb/minimal; LLM decides when to call
    ok = bool(address) and ("istanbul" in address.lower() or "344" in address)  # toy check
    return {"supported": ok}

def create_booking(address, description, time_window):
    return {"booking_id": f"BK-{int(datetime.now().timestamp())}",
            "address": address, "description": description, "time_window": time_window, "status": "scheduled"}

# --- single tool the model must call each turn ---
TOOLS = [{
  "type":"function",
  "function":{
    "name":"next_action",
    "description":"Choose exactly one next step and (optionally) update state.",
    "parameters":{
      "type":"object",
      "properties":{
        "name":{"type":"string","enum":[
          "ASK_ADDRESS","ASK_ISSUE","SUGGEST_WINDOWS","ASK_TIME_WINDOW",
          "VALIDATE_ADDRESS_ZONE","CONFIRM_BOOKING","CREATE_BOOKING","DONE_WRAP"
        ]},
        "args_json":{
          "type":"object",
          "properties":{
            "utterance":{"type":"string","description":"What the Agent should say next (one message)."},
            "state_updates":{"type":"object","description":"Any keys in state to overwrite/merge this turn."}
          },
          "additionalProperties": True
        },
        "rationale":{"type":"string"}
      },
      "required":["name","args_json","rationale"],
      "additionalProperties": False
    }
  }
}]

# --- the whole brain lives in this prompt ---
BOOK_PLANNER = """
You are the BOOK flow brain for a plumbing company. One turn at a time, do BOTH:
(1) Analyze the entire conversation (history + latest user message) and update booking state in args_json.state_updates.
(2) Pick exactly ONE action that moves us forward and write a single Agent message in args_json.utterance.

Booking slots:
- address (street + ZIP code REQUIRED), description (short issue), time_window (must match suggested windows if present),
  confirmation (boolean the user said yes to the final details).

Domain tools the host code will actually run when you choose them:
- SUGGEST_WINDOWS → host fills state.tools.suggested_windows with two options and shows them to user.
- VALIDATE_ADDRESS_ZONE → host will check state.address and may end if unsupported.
- CREATE_BOOKING → host will create a booking using state values and end.

CRITICAL conversation rules (you enforce these; host has NO logic):
- Be natural, friendly, brief. Never ask two questions in one turn.
- When asking for the address, ALWAYS say “including ZIP code”. If the user gave an address without ZIP, ask ONLY for the ZIP next (e.g., “What’s the ZIP there? e.g., 34430”).
- Do NOT repeat the same question verbatim. If the user didn’t answer, REPHRASE or narrow. Provide an example if helpful:
  “Please type it like: ‘İstiklal Cd. 212, Beyoğlu 34430, Istanbul’.”
- “3 strikes” per slot: after failing to get a direct answer to the SAME slot 3 times (consecutive),
  say exactly: "It seems you are not answering my question. If you do not answer, I should end the call."
  Then ask ONE last very explicit version. If that still fails on the next turn, choose DONE_WRAP.
- Treat “yes / ok / sure / thanks …” as NON-answers unless we are explicitly confirming the final booking.
- After SUGGEST_WINDOWS, do NOT call SUGGEST_WINDOWS again next turn. Ask for a choice (ASK_TIME_WINDOW) or move on.
- Time choice may be “1/2/first/second” or an exact window; normalize it yourself into state.time_window.
- Only call VALIDATE_ADDRESS_ZONE once AFTER you have a plausible address (street + ZIP).
- When address, description, and time_window are known with decent confidence, ask CONFIRM_BOOKING once.
  If user answers yes, then choose CREATE_BOOKING next turn; if no, ask one short fix and re-confirm once.

Maintain and update these keys (you fully own them):
  state.slots = { address, description, time_window, confirmation }
  state.meta  = { strikes: {address, description, time_window, confirmation}, last_slot_asked, last_action }
  state.tools = { suggested_windows? }

State to read/write (JSON):
{dynamic_context}

Your output MUST be a single next_action(...) tool call with:
- name: the chosen action
- args_json.utterance: one concise Agent message to print now
- args_json.state_updates: any updates to state.slots / state.meta / state.tools (only what changed)
"""

def run_book_flow(initial_user_text: str):
    # conversation state fully owned by the LLM (we just merge what it returns)
    state = {
      "slots": {"address": None, "description": None, "time_window": None, "confirmation": None},
      "meta":  {"strikes": {"address":0,"description":0,"time_window":0,"confirmation":0},
                "last_slot_asked": None, "last_action": None},
      "tools": {}
    }
    history = [{"role":"user","text": initial_user_text}]
    print(f"User: {initial_user_text}")

    while True:
        ctx = json.dumps({"history":history, "state":state}, ensure_ascii=False, indent=2)
        prompt = BOOK_PLANNER.replace("{dynamic_context}", ctx)

        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role":"system","content":"You are a helpful, concise assistant."},
                {"role":"system","content": prompt},
                {"role":"user","content":"Output EXACTLY ONE next_action tool call."}
            ],
            tools=TOOLS,
            tool_choice={"type":"function","function":{"name":"next_action"}},
            temperature=0,
        )
        call = resp.choices[0].message.tool_calls[0]
        args = json.loads(call.function.arguments)
        name = args["name"]
        j = args.get("args_json", {}) or {}
        utter = j.get("utterance","").strip()
        updates = j.get("state_updates", {}) or {}

        # merge LLM-provided updates blindly (LLM owns the state)
        # shallow merge for slots/meta/tools
        for k in ("slots","meta","tools"):
            if k in updates and isinstance(updates[k], dict):
                state[k].update(updates[k])

        # run the chosen action (domain side-effects only)
        if name == "SUGGEST_WINDOWS":
            state["tools"].update(find_available_window(state))
            print("Agent: " + (utter or "Here are the next available windows:"))
            for i,w in enumerate(state["tools"]["suggested_windows"],1):
                print(f"Agent: {i}. {w}")
            state["meta"]["last_action"] = "SUGGEST_WINDOWS"
            history.append({"role":"assistant","text": utter or "Shown windows."})
            # no user input this turn; LLM must ask next turn
            continue

        if name == "VALIDATE_ADDRESS_ZONE":
            res = check_if_support_address(state["slots"]["address"] or "")
            state["tools"]["address_validation"] = res
            print("Agent: " + (utter or "Let me quickly check your service area…"))
            history.append({"role":"assistant","text": utter or "Checking address zone."})
            if not res.get("supported"):
                print("Agent: Sorry — that area isn’t supported yet. I’ll end the call for now.")
                break
            state["meta"]["last_action"] = "VALIDATE_ADDRESS_ZONE"
            continue

        if name == "CONFIRM_BOOKING":
            print("Agent: " + (utter or "Please confirm the appointment details."))
            user = input("User: ").strip()
            history += [{"role":"assistant","text": utter},{"role":"user","text": user}]
            state["meta"]["last_action"] = "CONFIRM_BOOKING"
            continue

        if name == "CREATE_BOOKING":
            bk = create_booking(state["slots"]["address"], state["slots"]["description"], state["slots"]["time_window"])
            state["tools"]["booking"] = bk
            print(f"Agent: ✅ Booking created: {bk['booking_id']}")
            print(f"Agent: Address: {bk['address']}")
            print(f"Agent: Issue: {bk['description']}")
            print(f"Agent: Window: {bk['time_window']}")
            print("Agent: You’ll get a confirmation message shortly.")
            break

        if name == "DONE_WRAP":
            print("Agent: " + (utter or "All set. Have a great day!"))
            break

        # ASK_* or ASK_TIME_WINDOW → we print and wait for the user
        print("Agent: " + (utter or "—"))
        user = input("User: ").strip()
        history += [{"role":"assistant","text": utter},{"role":"user","text": user}]
        state["meta"]["last_action"] = name
