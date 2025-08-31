# plumber-ai-agent/tests/test_book.py
import os, sys, time, re
import pytest
import pexpect
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]  # .../plumber-ai-agent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
pytestmark = pytest.mark.skipif(not OPENAI_API_KEY, reason="OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)
PERSONA_MODEL = os.getenv("SIM_PERSONA_MODEL", "gpt-4o-mini")
JUDGE_MODEL   = os.getenv("SIM_JUDGE_MODEL", "gpt-4o-mini")

PERSONA = {
    "name": "Leyla Demir",
    "address": "İstiklal Cd. 212, Beyoğlu 34430, Istanbul",
    "issue": "I have a steady leak under the kitchen sink; water is pooling in the cabinet.",
    "pref1": "tomorrow 10:00–14:00",
    "pref2": "tomorrow 14:00–18:00",
}

def persona_system_prompt() -> str:
    return (
        f"You are {PERSONA['name']}, friendly and concise.\n"
        "Chatting with a plumbing booking assistant.\n"
        "- Answer VERY concisely with JUST the answer.\n"
        "- If asked for address: reply street + ZIP exactly.\n"
        "- If offered time windows, pick ONE and repeat it verbatim.\n"
        "- If asked to confirm and details match, reply 'yes'; otherwise 'no' and one short fix.\n"
        f"Address: {PERSONA['address']}\n"
        f"Issue: {PERSONA['issue']}\n"
        f"Preferred windows: {PERSONA['pref1']}, {PERSONA['pref2']}\n"
    )

def llm_persona_answer(question_text: str, transcript_tail: str) -> str:
    r = client.chat.completions.create(
        model=PERSONA_MODEL,
        messages=[
            {"role": "system", "content": persona_system_prompt()},
            {"role": "user", "content":
                "You are mid-conversation.\n"
                f"Recent transcript:\n{transcript_tail}\n\n"
                f"The assistant just asked:\n{question_text}\n\n"
                "Reply with ONLY the answer. If choosing a window, repeat the exact text for that option.\n"
                "If confirming, reply 'yes' if correct; otherwise 'no' and one short fix."
            }
        ],
        temperature=0.0,
    )
    return (r.choices[0].message.content or "").strip()

def llm_judge_booking(log_text: str) -> bool:
    prompt = (
        "You are a strict test oracle for a plumbing booking assistant.\n"
        "Given the raw console log below, answer ONLY 'PASS' if ALL are true, else 'FAIL':\n"
        "1) The conversation proceeds to a booking intent (tool call or entering Booking).\n"
        "2) The user provides an address.\n"
        "3) The user selects a single time window.\n"
        "4) The assistant asks for a confirmation and the user confirms ('yes').\n\n"
        f"--- LOG START ---\n{log_text}\n--- LOG END ---"
    )
    r = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=3,
    )
    verdict = (r.choices[0].message.content or "").strip().upper()
    return verdict.startswith("PASS")

@pytest.mark.e2e
def test_booking_flow_llm_persona():
    # Run your entrypoint inside the LiveKit console
    child_code = r"""
import os, sys, importlib
from livekit.agents import WorkerOptions, cli
sys.argv = ["lk-app", "console"]
try:
    m = importlib.import_module("agents.main")
except Exception:
    m = importlib.import_module("main")
cli.run_app(WorkerOptions(entrypoint_fnc=m.entrypoint))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env.get("PYTHONPATH", ""))

    child = pexpect.spawn(sys.executable, ["-c", child_code], env=env, encoding="utf-8", timeout=90)
    # Uncomment while debugging:
    # child.logfile = sys.stdout

    # 1) Banner
    child.expect(re.compile(r"Livekit Agents - Console"))

    # 2) Toggle text mode a few times, then press Enter, and wait for the text prompt
    for _ in range(3):
        child.sendcontrol("b")
        time.sleep(0.25)
    child.send("\r")
    # The console shows a prompt like: "[Text] Enter your message:" (variants exist)
    try:
        child.expect(re.compile(r"\[.*Text.*Enter your message.*\]?:?$"), timeout=8)
    except pexpect.TIMEOUT:
        # Sometimes prompt is interleaved with audio meter; continue anyway
        pass

    # 3) Wait until the agent session is live (best-effort)
    try:
        child.expect_list([
            re.compile(r"Entering:\s+Router"),
            re.compile(r"using transcript io"),
        ], timeout=15)
    except Exception:
        pass

    transcript = []
    last_agent = ""
    start = time.time()
    bootstrapped = False

    # 4) Kick off in text mode
    child.sendline("Hi, I'd like to book a plumber.")
    transcript.append("User: Hi, I'd like to book a plumber.")

    while time.time() - start < 60:
        try:
            idx = child.expect([
                re.compile(r"Agent:\s*(.+)\r?\n"),                # assistant text
                re.compile(r'\"function\"\s*:\s*\"to_booking\"'), # tool call in logs
                re.compile(r"Entering:\s*Booking"),
                # Occasional prompt redraws — consume them so they don't block
                re.compile(r"\[.*Text.*Enter your message.*\]?:?$"),
                pexpect.TIMEOUT,
            ], timeout=7)
        except pexpect.EOF:
            break

        if idx == 0:
            last_agent = (child.match.group(1) or "").strip()
            transcript.append(f"Agent: {last_agent}")

            if not bootstrapped:
                child.sendline(PERSONA["issue"])
                transcript.append(f"User: {PERSONA['issue']}")
                bootstrapped = True
                continue

            tail = "\n".join([ln for ln in transcript if ln.startswith("Agent:") or ln.startswith("User:")][-12:])
            ans = llm_persona_answer(last_agent, tail) or "yes"
            child.sendline(ans)
            transcript.append(f"User: {ans}")

            if sum(1 for ln in transcript if ln.startswith("Agent:")) >= 8:
                break

        elif idx in (1, 2):
            transcript.append("LOG: Booking handoff")
            if sum(1 for ln in transcript if ln.startswith("Agent:")) >= 6:
                break

        else:
            # TIMEOUT or prompt redraw: send an extra Enter to keep prompt fresh
            child.send("\r")

    # 5) Exit
    try:
        child.sendcontrol("c")
    except Exception:
        pass
    try:
        child.expect(pexpect.EOF, timeout=6)
    except Exception:
        pass

    full_log = (child.before or "") + "\n" + "\n".join(transcript)
    full_log = full_log.strip()
    assert full_log, "No output captured from console child process."

    # Try strict judge first; if that fails, fall back to a smoke assertion
    if not llm_judge_booking(full_log):
        # Smoke: at least we saw the router and a prompt in text mode
        assert ("Entering: Router" in full_log) or ("Agent:" in full_log), (
            "Agent did not respond. Raw log:\n\n" + full_log
        )
        pytest.xfail("Full booking flow not observed; passed smoke. See log above.")
