# cancel_agent.py
# Minimal, single-LLM-per-turn CANCEL flow (LLM handles checks, anti-repeat, and 3-strikes)
# pip install --upgrade openai python-dotenv
import os, json
from datetime import datetime
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
    # toy example — in real life you'd query your DB
    return {
        "appointment_id": appointment_id,
        "title": "Leak repair",
        "address": "İstiklal Cd. 212, Beyoğlu 34430, Istanbul",
        "window": "2025-08-26 10:00–14:00",
        "technician": "Mehmet K.",
        "status": "scheduled",
    }

def cancel_appointment(appointment_id: str | None):
    """Pretend to cancel; return ok."""
    return {"ok": bool(appointment_id), "canceled_at": datetime.now().isoformat(timespec="seconds")}

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
          "ASK_APPOINTMENT_ID","LOOKUP_APPOINTMENT","CONFIRM_CANCEL",
          "DO_CANCEL","SAY_CANCELED","DONE_WRAP"
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
CANCEL_PLANNER = """
You are the CANCEL flow brain for a plumbing company. One turn at a time, do BOTH:
(1) Analyze the entire conversation (history + latest user message) and update cancellation state in args_json.state_updates.
(2) Pick exactly ONE action that moves us forward and write a single Agent message in args_json.utterance.

Goal: cancel the user’s existing appointment after a clear confirmation.

Required slot:
- appointment_id (the booking number or a clear identifier visible to the customer)

Available domain actions the host code will run when you choose them:
- LOOKUP_APPOINTMENT → host fills state.tools.appt with details (if any) and prints them.
- DO_CANCEL → host cancels the appointment using state.slots.appointment_id and prints a short success line.

CRITICAL conversation rules (you enforce these; host has NO logic):
- Be natural, friendly, brief. Never ask two questions in one turn.
- Ask for the appointment ID as it appears on their confirmation (e.g., BK-123456). If they gave a name/phone instead, rephrase ONCE to ask for the actual ID.
- Do NOT repeat the same question verbatim. If the user didn’t answer, REPHRASE or narrow (e.g., “It usually starts with BK-…”).
- “3 strikes” per slot: after failing to get a direct answer to the SAME slot 3 times (consecutive),
  say exactly: "It seems you are not answering my question. If you do not answer, I should end the call."
  Then ask ONE last very explicit version. If that still fails on the next turn, choose DONE_WRAP.
- Treat “yes / ok / sure / thanks …” as NON-answers unless we are explicitly confirming cancellation.
- After you have a plausible ID, you may LOOKUP_APPOINTMENT once (optional) to restate details.
- Then ask CONFIRM_CANCEL once, short and clear. If user says yes → DO_CANCEL next turn. If no → DONE_WRAP.
- Maintain and update these keys (you fully own them):
  state.slots = { appointment_id, confirmation }
  state.meta  = { strikes: {appointment_id, confirmation}, last_action }
  state.tools = { appt? }

State (JSON) you can read/write:
{dynamic_context}

Your output MUST be a single next_action(...) tool call with:
- name: the chosen action
- args_json.utterance: one concise Agent message to print now
- args_json.state_updates: any updates to state.slots / state.meta / state.tools (only what changed)
"""

def run_cancel_flow(initial_user_text: str):
    # conversation state fully owned by the LLM (we just merge what it returns)
    state = {
      "slots": {"appointment_id": None, "confirmation": None},
      "meta":  {"strikes": {"appointment_id":0,"confirmation":0}, "last_action": None},
      "tools": {}
    }
    history = [{"role":"user","text": initial_user_text}]
    print(f"User: {initial_user_text}")

    while True:
        ctx = json.dumps({"history":history, "state":state}, ensure_ascii=False, indent=2)
        prompt = CANCEL_PLANNER.replace("{dynamic_context}", ctx)

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
            state["tools"]["appt"] = lookup_appointment(state["slots"]["appointment_id"])
            print("Agent: " + (utter or "Let me pull up your appointment details…"))
            appt = state["tools"].get("appt") or {}
            if appt:
                print(f"Agent: Found {appt.get('appointment_id')} — {appt.get('title')} at {appt.get('address')} within {appt.get('window')}.")
            else:
                print("Agent: I couldn’t find an appointment with that ID.")
            history.append({"role":"assistant","text": utter or "Lookup run."})
            state["meta"]["last_action"] = "LOOKUP_APPOINTMENT"
            continue

        if name == "DO_CANCEL":
            res = cancel_appointment(state["slots"]["appointment_id"])
            print("Agent: " + (utter or "Canceling your appointment…"))
            if res.get("ok"):
                print("Agent: ✅ Done. Your appointment has been canceled.")
            else:
                print("Agent: I couldn’t cancel that appointment. Let me connect you to a human.")
            history.append({"role":"assistant","text": utter or "Canceled."})
            break

        if name == "SAY_CANCELED":
            print("Agent: " + (utter or "Done. Your appointment has been canceled."))
            history.append({"role":"assistant","text": utter or "Said canceled."})
            break

        if name == "DONE_WRAP":
            print("Agent: " + (utter or "All set. Have a good day!"))
            break

        # ASK_APPOINTMENT_ID or CONFIRM_CANCEL → print and wait for user
        print("Agent: " + (utter or "—"))
        user = input("User: ").strip()
        history += [{"role":"assistant","text": utter},{"role":"user","text": user}]
        state["meta"]["last_action"] = name
