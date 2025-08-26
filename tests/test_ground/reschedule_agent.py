# src/reschedule_agent.py
# Minimal, single-LLM-per-turn RESCHEDULE flow (LLM handles checks, anti-repeat, and 3-strikes)
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
def lookup_appointment(appointment_id: str | None):
    """Return a stub appointment if the id looks plausible; else empty."""
    if not appointment_id:
        return {}
    return {
        "appointment_id": appointment_id,
        "title": "Leak repair",
        "address": "İstiklal Cd. 212, Beyoğlu 34430, Istanbul",
        "window": "2025-08-26 10:00–14:00",
        "technician": "Mehmet K.",
        "status": "scheduled",
    }

def find_available_window(_state):
    """Return two next-day 4-hour windows as options plus time_1/time_2 for phrasing."""
    d = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    options = [f"{d} 10:00–14:00", f"{d} 14:00–18:00"]
    return {"options": options, "time_1": options[0], "time_2": options[1]}

def reschedule_appointment(appointment_id: str | None, time_window: str | None):
    """Pretend to reschedule; return ok + the new window."""
    return {"ok": bool(appointment_id and time_window), "new_time_window": time_window}

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
          "ASK_APPOINTMENT_ID","LOOKUP_APPOINTMENT","SUGGEST_WINDOWS","ASK_NEW_TIME",
          "CONFIRM_RESCHEDULE","DO_RESCHEDULE","SAY_RESCHEDULED","DONE_WRAP"
        ]},
        "args_json":{
          "type":"object",
          "properties":{
            "utterance":{"type":"string","description":"What the Agent should say next (one message)."},
            "state_updates":{"type":"object","description":"Any keys in state to merge this turn (slots/meta/tools)."}
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
RESCHEDULE_PLANNER = """
You are the RESCHEDULE flow brain for a plumbing company. One turn at a time, do BOTH:
(1) Analyze the entire conversation (history + latest user message) and update reschedule state in args_json.state_updates.
(2) Pick exactly ONE action that moves us forward and write a single Agent message in args_json.utterance.

Goal: reschedule the existing appointment to a new 4-hour window after a clear confirmation.

Required slots:
- appointment_id (the booking number as on their confirmation, e.g., BK-123456)
- time_window (one of the offered options)

Available domain actions the host code will run when you choose them:
- LOOKUP_APPOINTMENT → host fills state.tools.appt with details (if any) and prints them.
- SUGGEST_WINDOWS → host fills state.tools.options with two options and shows them.
- DO_RESCHEDULE → host reschedules using state.slots.{appointment_id,time_window} and finishes.

CRITICAL conversation rules (you enforce these; host has NO logic):
- Be natural, friendly, brief. Never ask two questions in one turn.
- If appointment_id is missing or unclear → ask for it (“What’s your appointment number? It usually looks like BK-123456.”).
- Do NOT repeat the same question verbatim. If the user didn’t answer, REPHRASE or narrow.
- “3 strikes” per slot: after failing to get a direct answer to the SAME slot 3 times (consecutive),
  say exactly: "It seems you are not answering my question. If you do not answer, I should end the call."
  Then ask ONE last explicit version. If that still fails on the next turn, choose DONE_WRAP.
- Treat “yes / ok / sure …” as NON-answers unless explicitly confirming the new time.
- After you have a plausible ID, you may LOOKUP_APPOINTMENT once (optional) to restate details.
- If time_window missing → SUGGEST_WINDOWS once, then ASK_NEW_TIME (“What new time works? {time_1} or {time_2}?”).
- The user may answer “1/2/first/second” or repeat an exact option; normalize it yourself into state.slots.time_window.
- Confirm once with CONFIRM_RESCHEDULE (short). If they agree → DO_RESCHEDULE next turn. If no → ask for a different window once.
- Maintain and update these keys (you fully own them):
  state.slots = { appointment_id, time_window, confirmation? }
  state.meta  = { strikes: {appointment_id, time_window, confirmation}, last_action }
  state.tools = { appt?, options?, time_1?, time_2? }

State (JSON) you can read/write:
{dynamic_context}

Your output MUST be a single next_action(...) tool call with:
- name: the chosen action
- args_json.utterance: one concise Agent message to print now
- args_json.state_updates: any updates to state.slots / state.meta / state.tools (only what changed)
"""

def run_reschedule_flow(initial_user_text: str):
    # conversation state fully owned by the LLM (we just merge what it returns)
    state = {
      "slots": {"appointment_id": None, "time_window": None, "confirmation": None},
      "meta":  {"strikes": {"appointment_id":0,"time_window":0,"confirmation":0}, "last_action": None},
      "tools": {}
    }
    history = [{"role":"user","text": initial_user_text}]
    print(f"User: {initial_user_text}")

    while True:
        ctx = json.dumps({"history":history, "state":state}, ensure_ascii=False, indent=2)
        prompt = RESCHEDULE_PLANNER.replace("{dynamic_context}", ctx)

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

        # merge LLM-provided updates (LLM owns the state)
        for k in ("slots","meta","tools"):
            if k in updates and isinstance(updates[k], dict):
                state[k].update(updates[k])

        # run the chosen action (domain side-effects)
        if name == "LOOKUP_APPOINTMENT":
            state["tools"]["appt"] = lookup_appointment(state["slots"].get("appointment_id"))
            print("Agent: " + (utter or "Let me pull up your appointment details…"))
            appt = state["tools"].get("appt") or {}
            if appt:
                print(f"Agent: Found {appt.get('appointment_id')} — {appt.get('title')} at {appt.get('address')} within {appt.get('window')}.")
            else:
                print("Agent: I couldn’t find an appointment with that ID.")
            history.append({"role":"assistant","text": utter or "Lookup run."})
            state["meta"]["last_action"] = "LOOKUP_APPOINTMENT"
            continue

        if name == "SUGGEST_WINDOWS":
            state["tools"].update(find_available_window(state))
            print("Agent: " + (utter or "Here are the next available windows:"))
            for i,w in enumerate(state["tools"]["options"], 1):
                print(f"Agent: {i}. {w}")
            history.append({"role":"assistant","text": utter or "Shown windows."})
            state["meta"]["last_action"] = "SUGGEST_WINDOWS"
            continue

        if name == "DO_RESCHEDULE":
            res = reschedule_appointment(state["slots"].get("appointment_id"), state["slots"].get("time_window"))
            print("Agent: " + (utter or "Rescheduling your appointment…"))
            if res.get("ok"):
                print(f"Agent: ✅ All set. Your appointment has been rescheduled to {res.get('new_time_window')}.")
            else:
                print("Agent: I couldn’t reschedule that appointment. Let me connect you to a human.")
            history.append({"role":"assistant","text": utter or "Rescheduled."})
            break

        if name == "SAY_RESCHEDULED":
            print("Agent: " + (utter or "All set. Your appointment has been rescheduled."))
            history.append({"role":"assistant","text": utter or "Said rescheduled."})
            break

        if name == "DONE_WRAP":
            print("Agent: " + (utter or "All set. Have a good day!"))
            break

        # ASK_APPOINTMENT_ID / ASK_NEW_TIME / CONFIRM_RESCHEDULE → print and wait for user
        print("Agent: " + (utter or "—"))
        user = input("User: ").strip()
        history += [{"role":"assistant","text": utter},{"role":"user","text": user}]
        state["meta"]["last_action"] = name
