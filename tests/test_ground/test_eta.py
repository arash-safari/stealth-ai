# sim_eta_llm.py
# Simple simulator for the ETA flow.
# Run: python3 -m src.sim_eta_llm
import os, builtins
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Env / client
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local"); load_dotenv(ROOT / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o-mini"

# External modules
from .intent_tool import classify_intent, print_report_intent_line
from .eta_agent import run_eta_flow

# Persona
persona = {
    "name": "Leyla Demir",
    "personality": "friendly, concise; prefers clear yes/no answers",
    "appointment_id": "BK-123456",
    "starter": "Hi, what’s the ETA for my appointment?",
}

def persona_system_prompt() -> str:
    return (
        f"You are {persona['name']}, a {persona['personality']} homeowner.\n"
        "You are chatting with a plumbing company's assistant about ETA.\n"
        "- Answer VERY concisely with JUST the answer.\n"
        "- If asked for appointment number/ID: reply with it exactly.\n"
        "- If the assistant tells you your arrival window & ETA, you don't need to say anything more.\n"
        f"Your info:\n- Appointment ID: {persona['appointment_id']}\n"
    )

def persona_answer_for(question_text: str, transcript_tail: str) -> str:
    msgs = [
        {"role": "system", "content": persona_system_prompt()},
        {"role": "user", "content":
            "You are mid-conversation.\n"
            f"Recent transcript:\n{transcript_tail}\n\n"
            f"The assistant just asked:\n{question_text}\n\n"
            "Reply with ONLY the answer. If asked for your appointment ID, reply with it exactly."
        }
    ]
    r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.2)
    return r.choices[0].message.content.strip()

def run_with_persona(initial_user_text: str):
    transcript = [f"User: {initial_user_text}"]
    real_input, real_print = builtins.input, builtins.print
    last_agent_question = ""

    def is_questionish(agent_text: str) -> bool:
        t = agent_text.lower().strip()
        if t.endswith("?"):
            return True
        if "appointment number" in t or "appointment id" in t or "appointment-id" in t or "id" in t:
            return True
        return False

    def spy_print(*args, **kwargs):
        nonlocal last_agent_question
        text = " ".join(str(a) for a in args).strip()
        if text.startswith("Agent:"):
            content = text[len("Agent:"):].strip()
            # Only store lines that look like a QUESTION/PROMPT
            if is_questionish(content):
                last_agent_question = content
        return real_print(*args, **kwargs)

    def persona_input(prompt: str = "") -> str:
        # Use the last Agent question if we have it; otherwise fallback to prompt
        q = (last_agent_question or prompt).strip()
        tail = "\n".join(transcript[-8:])
        ans = persona_answer_for(q, tail)
        real_print(f"User: {ans}")
        transcript.append(f"Agent: {q}")
        transcript.append(f"User: {ans}")
        return ans

    # Patch I/O
    builtins.print = spy_print
    builtins.input = persona_input
    try:
        # Echo initial user line so it appears in console
        print(f"User: {initial_user_text}")
        run_eta_flow(initial_user_text)
    finally:
        builtins.input = real_input
        builtins.print = real_print

if __name__ == "__main__":
    first_message = persona["starter"]
    print(f"\n[Persona initial message]\n{first_message}\n")

    # Classify and print the exact one-liner
    intent = classify_intent(first_message)
    print_report_intent_line(intent)

    # Be forgiving in tests: run ETA flow even if classifier misfires
    if intent.get("intent_code") != "ETA":
        print("Classifier did not return ETA; running ETA flow anyway for test.\n")
    run_with_persona(first_message)
