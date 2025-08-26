# src/price_agent.py
# Minimal, single-LLM-per-turn PRICE flow (LLM handles logic + “3-strikes”; code is thin)
# pip install --upgrade openai python-dotenv
import os, json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# --- env / client ---
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local"); load_dotenv(ROOT / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o-mini"

# --- tiny domain stub ---
def get_pricing(_state):
    """
    Return demo pricing. Replace with your live pricing logic if needed.
    """
    return {"base_fee": "₺750", "labor_rate": "₺600", "included_minutes": 30}

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
          "GET_PRICING","SAY_PRICE","ASK_WANT_BOOK","BRIDGE_TO_BOOK","DONE_WRAP"
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
PRICE_PLANNER = """
You are the PRICE flow brain for a plumbing company. One turn at a time, do BOTH:
(1) Analyze the conversation (history + latest user message) and update price state in args_json.state_updates.
(2) Pick exactly ONE action and write a single Agent line in args_json.utterance.

Goal: disclose pricing clearly and (optionally) offer to start a booking.

Actions the host code runs:
- GET_PRICING → host fills state.tools.pricing with {base_fee, labor_rate, included_minutes}.
- BRIDGE_TO_BOOK → host will print your bridge line and end (controller may hand off to BOOK externally).

Conversation rules (you enforce; host has NO logic):
- Be natural, friendly, brief. Never ask two questions in one turn.
- If dynamic pricing is needed, call GET_PRICING; else you may use known values from state.tools.pricing.
- Say ONE concise price line: base visit fee, labor rate, included minutes, “parts & tax extra”, and
  “written estimate before work”.
- Then ASK_WANT_BOOK once (“Would you like to book a visit with these rates?”).
- If user says yes → BRIDGE_TO_BOOK next turn. If no → DONE_WRAP.
- Do NOT repeat the same question verbatim. If the user didn’t answer, rephrase once.
- “3 strikes” for booking question: after 3 consecutive non-answers to ASK_WANT_BOOK,
  say exactly: "It seems you are not answering my question. If you do not answer, I should end the call."
  Then ask ONE last explicit yes/no. If still unclear next turn → DONE_WRAP.
- Maintain and update these keys (you own them):
  state.slots = { want_book? }
  state.meta  = { strikes: {want_book}, last_action }
  state.tools = { pricing? }

State (JSON) you can read/write:
{dynamic_context}

Output a single next_action(...) tool call with:
- name: chosen action
- args_json.utterance: one concise Agent message
- args_json.state_updates: any updates to state.slots / state.meta / state.tools (only what changed)
"""

def run_price_flow(initial_user_text: str):
    # conversation state fully owned by the LLM (we just merge what it returns)
    state = {
      "slots": {"want_book": None},
      "meta":  {"strikes": {"want_book":0}, "last_action": None},
      "tools": {}
    }
    history = [{"role":"user","text": initial_user_text}]
    print(f"User: {initial_user_text}")

    while True:
        ctx = json.dumps({"history":history, "state":state}, ensure_ascii=False, indent=2)
        prompt = PRICE_PLANNER.replace("{dynamic_context}", ctx)

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
        if name == "GET_PRICING":
            state["tools"]["pricing"] = get_pricing(state)
            print("Agent: " + (utter or "Let me check our current rates…"))
            history.append({"role":"assistant","text": utter or "Checked pricing."})
            state["meta"]["last_action"] = "GET_PRICING"
            continue

        if name == "BRIDGE_TO_BOOK":
            # Downstream controller can call run_book_flow after this line if desired.
            print("Agent: " + (utter or "Great—I'll collect your address, the issue, and a preferred 4-hour window next."))
            break

        if name == "DONE_WRAP":
            print("Agent: " + (utter or "All set. If you have more questions, I’m here to help!"))
            break

        # SAY_PRICE or ASK_WANT_BOOK → print and (if question) wait for user
        print("Agent: " + (utter or "—"))
        if name == "ASK_WANT_BOOK":
            user = input("User: ").strip()
            history += [{"role":"assistant","text": utter},{"role":"user","text": user}]
        else:
            # Non-question line (SAY_PRICE) — keep the turn in history and let LLM drive next step
            history += [{"role":"assistant","text": utter}]
        state["meta"]["last_action"] = name
