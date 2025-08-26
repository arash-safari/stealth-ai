# sim_customer_llm.py
import os, builtins
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Load env & client
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local"); load_dotenv(ROOT / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o-mini"

# External modules (package-relative)
from .intent_tool import classify_intent, print_report_intent_line
from .book_agent import run_book_flow

# Minimal persona
persona = {
    "name": "Leyla Demir",
    "personality": "friendly, concise; prefers clear yes/no answers",
    "address": "İstiklal Cd. 212, Beyoğlu 34430, Istanbul",
    "issue": "I have a steady leak under the kitchen sink; water is pooling in the cabinet.",
    "pref1": "tomorrow 10:00–14:00",
    "pref2": "tomorrow 14:00–18:00",
}

def persona_prompt():
    return (
        f"You are {persona['name']}, a {persona['personality']} homeowner.\n"
        "You are chatting with a plumbing company's booking assistant.\n"
        "- Answer VERY concisely with JUST the answer.\n"
        "- If asked for address: reply street + ZIP exactly.\n"
        "- If offered time windows, pick ONE and repeat it verbatim.\n"
        "- If asked to confirm and details match, reply 'yes'; otherwise 'no' and one short fix.\n"
        f"Your info:\n- Address: {persona['address']}\n"
        f"- Issue: {persona['issue']}\n"
        f"- Preferred windows (in order): {persona['pref1']}, {persona['pref2']}\n"
    )

def persona_answer(question_text: str, transcript_tail: str) -> str:
    msgs = [
        {"role": "system", "content": persona_prompt()},
        {"role": "user", "content":
            "You are mid-conversation.\n"
            f"Recent transcript:\n{transcript_tail}\n\n"
            f"The assistant just asked:\n{question_text}\n\n"
            "Reply with ONLY the answer. If choosing a window, repeat the exact text for that option.\n"
            "If confirming, reply 'yes' if correct; otherwise 'no' and one short fix."
        }
    ]
    r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.2)
    return r.choices[0].message.content.strip()

def run_with_persona(initial_user_text: str):
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
        # Use the last Agent line as the actual question
        q = (last_agent_line or prompt).strip()
        tail = "\n".join(transcript[-8:])
        ans = persona_answer(q, tail)
        real_print(f"User: {ans}")
        transcript.append(f"Agent: {q}")
        transcript.append(f"User: {ans}")
        return ans

    builtins.print = spy_print
    builtins.input = persona_input
    try:
        run_book_flow(initial_user_text)
    finally:
        builtins.input = real_input
        builtins.print = real_print

if __name__ == "__main__":
    first_message = persona["issue"]
    print(f"\n[Persona initial message]\n{first_message}\n")
    intent = classify_intent(first_message)
    print_report_intent_line(intent)
    if intent["intent_code"] == "BOOK":
        run_with_persona(first_message)
    else:
        print("\n(Non-BOOK intent—conversation finished.)")
