# # tests/test_runbook_text_user.py
# # Pytest: LLM-driven, text-only conversation test for IntentAgent (PLUMBER persona)

# import asyncio
# import os
# import re
# import sys
# import pytest
# from typing import List, Tuple

# sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# from dotenv import load_dotenv
# from livekit.agents.voice import AgentSession
# from livekit.plugins import openai as lk_openai
# from agents.intent_agent import IntentAgent
# from openai import OpenAI

# # Load keys & knobs from .env.local
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env.local"))

# TEST_MODEL = os.getenv("TEST_USER_MODEL", "gpt-4o-mini")
# MAX_TURNS  = int(os.getenv("TEST_MAX_TURNS", "16"))
# _oai = OpenAI()

# def _test_user_system_prompt(
#     caller_name=os.getenv("TEST_CALLER_NAME", "Nia"),
#     personality=os.getenv("TEST_USER_PERSONALITY", "calm, cooperative"),
#     issue=os.getenv("TEST_ISSUE", "there's a steady leak under the kitchen sink"),
#     address=os.getenv("TEST_ADDRESS", "123 Market St, Apt 5B"),
#     time_window=os.getenv("TEST_TIME_WINDOW", "today between 2–4 pm"),
#     phone=os.getenv("TEST_PHONE", "+1 415 555 0134"),
#     email=os.getenv("TEST_EMAIL", "nia@example.com"),
#     urgency=os.getenv("TEST_URGENCY", "emergency"),
# ) -> str:
#     return f"""
# You are role-playing a real caller to a plumbing assistant. Stay strictly in character.

# Persona:
# - Name: {caller_name}; Personality: {personality}.
# - You need a plumber: {issue}.
# - Address: {address}. Preferred time: {time_window}.
# - Phone: {phone}; Email: {email}. Urgency: {urgency}.

# Ground rules:
# 1) Answer ONLY to the assistant's latest question. Keep replies short (one sentence).
# 2) If asked for a specific info (slot), provide JUST that value:
#    - issue/problem: "{issue}"
#    - address/location: "{address}"
#    - time/availability: "{time_window}"
#    - name: "{caller_name}"
#    - phone: "{phone}"
#    - email: "{email}"
# 3) If the assistant asks to confirm, reply "yes" or "no" (optionally + a short phrase).
# 4) Don’t ask questions unless you must clarify.
# 5) Opening line: state the plumbing problem and urgency briefly.

# Output: plain text only (no role labels, no markdown).
# """.strip()

# async def llm_user_next_utterance(system_prompt: str, transcript: List[Tuple[str, str]]) -> str:
#     messages = [{"role": "system", "content": system_prompt}]
#     for who, msg in transcript:
#         messages.append({"role": "assistant" if who == "AGENT" else "user", "content": msg})

#     if not any(who == "TEST_USER" for who, _ in transcript):
#         messages.append({"role": "assistant", "content": "The assistant just greeted you; give your opening line now."})

#     resp = _oai.chat.completions.create(
#         model=TEST_MODEL,
#         temperature=0.3,
#         max_tokens=120,
#         messages=messages,
#         **({"seed": 42} if "seed" in _oai.chat.completions.create.__code__.co_varnames else {})
#     )
#     text = resp.choices[0].message.content.strip()
#     return re.sub(r"\s+", " ", text)

# def _needs_user_reply(text: str) -> bool:
#     """Heuristic: reply when it's a question, a confirmation, or a clear slot ask."""
#     t = text.lower().strip()
#     if t.endswith("?"):
#         return True
#     slot_cues = ("confirm", "what is", "what's", "please provide", "provide", "address", "phone", "email",
#                  "availability", "time", "name", "issue", "describe the issue", "problem")
#     return any(k in t for k in slot_cues)

# def _is_wrap_up(text: str) -> bool:
#     return bool(re.search(r"anything else i can help you with\??", text, re.I)) or \
#            bool(re.search(r"(appointment|booking).*(confirmed|scheduled)", text, re.I))

# @pytest.mark.asyncio
# async def test_text_user_conversation_prints_and_completes():
#     # Roomless TEXT-ONLY session
#     session = AgentSession(
#         preemptive_generation=True,
#         llm=lk_openai.LLM(model=os.getenv("AGENT_MODEL", "gpt-4o-mini")),
#     )
#     agent = IntentAgent()
#     await session.start(agent=agent)

#     # Capture agent's spoken text
#     transcript: List[Tuple[str, str]] = []
#     async def say_and_log(text: str, *args, **kwargs):
#         msg = str(text)
#         print(f"AGENT:     {msg}")
#         transcript.append(("AGENT", msg))
#         return None
#     agent.session.say = say_and_log  # type: ignore

#     # Agent greeting
#     await agent.on_enter()

#     # Build plumber persona prompt
#     system_prompt = _test_user_system_prompt()

#     # First user message
#     user_line = await llm_user_next_utterance(system_prompt, transcript)
#     print(f"TEST_USER: {user_line}")
#     transcript.append(("TEST_USER", user_line))
#     agent.note_user(user_line)  # <= ADD THIS
#     await agent.session.generate_reply(user_input=user_line)

#     # Turn loop: DRAIN all new agent lines each tick and reply to those that need a user response
#     next_agent_idx = len([1 for w, _ in transcript if w == "AGENT"])  # start after greeting
#     wrapped_up = False

#     # for _ in range(MAX_TURNS):
#     #     await asyncio.sleep(0.25)
#     #     agent_lines = [msg for who, msg in transcript if who == "AGENT"]

#     #     # Drain new agent lines one-by-one (handles bursts)
#     #     while next_agent_idx < len(agent_lines):
#     #         last = agent_lines[next_agent_idx]
#     #         next_agent_idx += 1

#     #         if _is_wrap_up(last):
#     #             wrapped_up = True
#     #             closing = "no, thanks."
#     #             print(f"TEST_USER: {closing}")
#     #             transcript.append(("TEST_USER", closing))
#     #             break

#     #         if _needs_user_reply(last):
#     #             user_line = await llm_user_next_utterance(system_prompt, transcript)
#     #             print(f"TEST_USER: {user_line}")
#     #             transcript.append(("TEST_USER", user_line))
#     #             agent.note_user(user_line)

#     #             # IMPORTANT: don't override instructions; the agent arms slot-capture itself
#     #             await agent.session.generate_reply(user_input=user_line)
#     for _ in range(MAX_TURNS):
#         await asyncio.sleep(0.25)
#         if transcript and transcript[-1][0] == "AGENT":
#             last = transcript[-1][1]

#             if _is_wrap_up(last):
#                 closing = "no, thanks."
#                 print(f"TEST_USER: {closing}")
#                 transcript.append(("TEST_USER", closing))
#                 agent.note_user(closing)
#                 await agent.session.generate_reply(user_input=user_line)

#                 break

#             user_line = await llm_user_next_utterance(system_prompt, transcript)
#             print(f"TEST_USER: {user_line}")
#             transcript.append(("TEST_USER", user_line))
#             agent.note_user(user_line)
#             await agent.session.generate_reply(user_input=user_line)
#         if wrapped_up:
#             break

#     # Print transcript always
#     print("\n" + "-" * 60)
#     print("CALL CAPTURED (TRANSCRIPT)")
#     print("-" * 60)
#     for who, msg in transcript:
#         print(f"{who:>9}: {msg}")

#     # Assertions
#     total_agent_utts = sum(1 for w, _ in transcript if w == "AGENT")
#     total_user_utts  = sum(1 for w, _ in transcript if w == "TEST_USER")
#     assert total_agent_utts >= 2, "Agent did not produce enough turns"
#     assert total_user_utts  >= 2, "LLM test user did not produce enough turns"
#     # Either wrapped up or had a healthy exchange
#     assert wrapped_up or (total_agent_utts + total_user_utts) >= 6

#     await session.aclose()
