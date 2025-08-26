# sim_cancel_llm.py
# Orchestrates a "customer LLM" talking to:
#   1) the separate intent classifier (intent_tool.py)
#   2) your cancel_agent.py communicator
#
# Run (from project root):  python3 -m src.sim_cancel_llm
import os
import builtins
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Project root = one level up from /src
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

# OpenAI client
MODEL = "gpt-4o-mini"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# External modules
from .intent_tool import classify_intent, print_report_intent_line
from .cancel_agent import run_cancel_flow

# ---- Persona profile ----
persona = {
    "name": "Leyla Demir",
    "personality": "friendly, concise; prefers clear yes/no answers",
    "appointment_id": "BK-123456",
    "reason": "My plans changed.",
}

def persona_system_prompt() -> str:
    return (
        f"You are {persona['name']}, a {persona['personality']} homeowner.\n"
        "You are chatting with a plumbing company's assistant to CANCEL an appointment.\n"
        "- Answer VERY concisely with JUST the answer.\n"
        "- If asked for appointment number/ID: reply with it exactly.\n"
        "- If asked to confirm cancellation and the details match, reply \"yes\"; otherwise \"no\" and one short fix.\n"
        "- If asked for a reason, reply briefly.\n"
        f"Your info:\n"
        f"- Appointment ID: {persona['appointment_id']}\n"
        f"- Reason: {persona['reason']}\n"
    )

def persona_initial_message(client: OpenAI) -> str:
    """Have the persona open the chat with a brief cancel intent."""
    msgs = [
        {"role": "system", "content": persona_system_prompt()},
        {"role": "user", "content": "Open the conversation with one short sentence asking to cancel your appointment."}
    ]
    r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.2)
    return r.choices[0].message.content.strip()

def persona_answer_for(question_text: str, transcript_tail: str) -> str:
    """Answer JUST the last Agent question, using the persona's fixed info."""
    msgs = [
        {"role": "system", "content": persona_system_prompt()},
        {"role": "user", "content":
            "You are mid-conversation.\n"
            f"Recent transcript:\n{transcript_tail}\n\n"
            f"The assistant just asked:\n{question_text}\n\n"
            "Reply with ONLY the answer. If asked for your appointment ID, reply with it exactly.\n"
            "If confirming cancellation, reply 'yes' if correct; otherwise 'no' and one short fix."
        }
    ]
    r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.2)
    return r.choices[0].message.content.strip()

def run_with_persona(initial_user_text: str):
    """Monkey-patch input/print so the persona answers the latest Agent line."""
    transcript = [f"User: {initial_user_text}"]
    real_input, real_print = builtins.input, builtins.print
    last_agent_line = ""

    def spy_print(*args, **kwargs):
        nonlocal last_agent_line
        text = " ".join(str(a) for a in args).strip()
        if text.startswith("Agent:"):
            last_agent_line = text[len("Agent:"):].strip()
        return real_print(*args, **kwargs)

    def persona_input(prompt: str = "") -> str:
        q = (last_agent_line or prompt).strip()
        tail = "\n".join(transcript[-8:])
        ans = persona_answer_for(q, tail)
        real_print(f"User: {ans}")
        transcript.append(f"Agent: {q}")
        transcript.append(f"User: {ans}")
        return ans

    builtins.print = spy_print
    builtins.input = persona_input
    try:
        run_cancel_flow(initial_user_text)
    finally:
        builtins.input = real_input
        builtins.print = real_print

if __name__ == "__main__":
    # Persona starts the conversation
    first_message = persona_initial_message(client)
    print(f"\n[Persona initial message]\n{first_message}\n")

    # Classify via the separate intent tool (and print the exact one-liner)
    intent = classify_intent(first_message)
    print_report_intent_line(intent)

    # If CANCEL, run the cancel chat with persona auto-replies
    if intent["intent_code"] == "CANCEL":
        run_with_persona(first_message)
    else:
        print("\n(Non-CANCEL intent—conversation finished.)")
