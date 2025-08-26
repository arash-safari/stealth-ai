# src/sim_price_llm.py
# Simple simulator for the PRICE flow.
# Run: python3 -m src.sim_price_llm
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
from .price_agent import run_price_flow

# Persona
persona = {
    "name": "Leyla Demir",
    "personality": "friendly, concise; prefers clear yes/no answers",
    "starter": "Hi, what do you charge to fix a leak?",
    "will_book": True  # flip to False if you want the persona to decline booking
}

def persona_system_prompt() -> str:
    decision = "yes" if persona["will_book"] else "no"
    return (
        f"You are {persona['name']}, a {persona['personality']} homeowner.\n"
        "You are chatting with a plumbing company's assistant about PRICING.\n"
        "- Answer VERY concisely with JUST the answer.\n"
        "- If asked whether you want to book now, reply exactly \"" + decision + "\".\n"
    )

def persona_answer_for(question_text: str, transcript_tail: str) -> str:
    msgs = [
        {"role": "system", "content": persona_system_prompt()},
        {"role": "user", "content":
            "You are mid-conversation.\n"
            f"Recent transcript:\n{transcript_tail}\n\n"
            f"The assistant just asked:\n{question_text}\n\n"
            "Reply with ONLY the answer. If asked whether to book, reply exactly yes/no per your preference."
        }
    ]
    r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.2)
    return r.choices[0].message.content.strip()

def run_with_persona(initial_user_text: str):
    transcript = [f"User: {initial_user_text}"]
    real_input, real_print = builtins.input, builtins.print
    last_agent_question = ""

    def is_questionish(agent_text: str) -> bool:
        t = agent_text.strip()
        return t.endswith("?")

    def spy_print(*args, **kwargs):
        nonlocal last_agent_question
        text = " ".join(str(a) for a in args).strip()
        if text.startswith("Agent:"):
            content = text[len("Agent:"):].strip()
            if is_questionish(content):
                last_agent_question = content
        return real_print(*args, **kwargs)

    def persona_input(prompt: str = "") -> str:
        q = (last_agent_question or prompt).strip()
        tail = "\n".join(transcript[-8:])
        ans = persona_answer_for(q, tail)
        real_print(f"User: {ans}")
        transcript.append(f"Agent: {q}")
        transcript.append(f"User: {ans}")
        return ans

    builtins.print = spy_print
    builtins.input = persona_input
    try:
        print(f"User: {initial_user_text}")
        run_price_flow(initial_user_text)
    finally:
        builtins.input = real_input
        builtins.print = real_print

if __name__ == "__main__":
    first_message = persona["starter"]
    print(f"\n[Persona initial message]\n{first_message}\n")

    # Classify via the separate intent tool (and print the exact one-liner)
    intent = classify_intent(first_message)
    print_report_intent_line(intent)

    # Be forgiving in tests: run PRICE flow even if classifier misfires
    if intent.get("intent_code") != "PRICE":
        print("Classifier did not return PRICE; running PRICE flow anyway for test.\n")
    run_with_persona(first_message)
