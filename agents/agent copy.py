# agents/agent.py — LiveKit example–style agent with forced tool call + strict ChatContext parts
from __future__ import annotations

import os
import json
import uuid
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple, cast
from pathlib import Path
from dotenv import load_dotenv

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, RunContext
from livekit.agents.llm import function_tool
from livekit.agents.llm.tool_context import FunctionTool
from livekit.agents.llm.chat_context import ChatContext, ChatMessage

from livekit.plugins import deepgram, cartesia, silero
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins import openai as lk_openai

# --- Logging
from utils.logger import get_logger
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local"); load_dotenv(ROOT / ".env")
log = get_logger("livekit.agents.intent", "intent-agent.log")
logger = logging.getLogger("intent-agent")

# --- Flows (your planners)
from src.intent_agent import classify_intent, print_report_intent_line
from src.book_agent import BOOK_PLANNER
from src.cancel_agent import CANCEL_PLANNER
from src.eta_agent import ETA_PLANNER
from src.reschedule_agent import RESCHEDULE_PLANNER
from src.price_agent import PRICE_PLANNER
from src.status_agent import STATUS_PLANNER

FLOW_PROMPTS: Dict[str, str] = {
    "BOOK": BOOK_PLANNER,
    "CANCEL": CANCEL_PLANNER,
    "ETA": ETA_PLANNER,
    "RESCHEDULE": RESCHEDULE_PLANNER,
    "PRICE": PRICE_PLANNER,
    "STATUS": STATUS_PLANNER,
}

# ---------------------------
# Helpers
# ---------------------------

def _near_dup(a: str, b: str, thresh: float = 0.92) -> bool:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= thresh

def _safe_json_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _safe_json_merge(dst[k], v)
        else:
            dst[k] = v
    return dst

# ---------------------------
# Conversation state
# ---------------------------

DEFAULT_STATE: Dict[str, Any] = {
    "slots": {"address": None, "description": None, "time_window": None, "confirmation": None},
    "meta": {
        "strikes": {"address": 0, "description": 0, "time_window": 0, "confirmation": 0},
        "last_slot_asked": None,
        "last_action": None,
    },
    "tools": {},
}

# ---------------------------
# Tool: the LLM must call exactly once per turn
# ---------------------------

@function_tool
def next_action(name: str, args_json: str):
    """
    Pick exactly one action for the flow.
    args_json: JSON string with keys:
      - utterance: str (what to say)
      - state_updates: object (partial state merge, optional)
    """
    return {"ok": True, "name": name, "args_json": args_json}

# ---------------------------
# Agent
# ---------------------------

class IntentAgent(Agent):
    """
    LiveKit example–style agent:
    - Uses a turn detector (no preemptive gen)
    - Overrides llm_node to force a tool call
    - Rewrites ChatContext with proper OpenAI content parts
    """

    def __init__(self) -> None:
        self.audio_on = os.getenv("INTENT_AUDIO", "0") == "1"
        if self.audio_on and not os.getenv("CARTESIA_API_KEY"):
            log.warning("INTENT_AUDIO=1 but CARTESIA_API_KEY is not set; TTS will fail.")

        # Guards
        self._planner_lock = asyncio.Lock()
        self._intent_started = False
        self.sticky_flow = os.getenv("STICKY_FLOW", "1") == "1"
        self.drop_double_question = os.getenv("DROP_DOUBLE_QUESTION", "1") == "1"
        self._awaiting_answer = False
        self._turn_id = 0
        self._last_question_turn_id: Optional[int] = None

        # Data
        self.flow_code: Optional[str] = None
        self.flow_prompt: Optional[str] = None
        self.state: Dict[str, Any] = json.loads(json.dumps(DEFAULT_STATE))
        self.history: List[Dict[str, str]] = []  # role: "user" | "assistant"

        super().__init__(
            # Keep this minimal; we inject real system prompt in llm_node
            instructions="You are a planner that must call the provided function tool.",
            stt=deepgram.STT(model="nova-3", language="multi"),
            llm=lk_openai.LLM(model=os.getenv("AGENT_MODEL", "gpt-4o"), temperature=0),
            tts=cartesia.TTS(model="sonic-2") if self.audio_on else None,
            vad=silero.VAD.load(),
        )

    # -----------------------
    # Lifecycle
    # -----------------------

    async def on_enter(self) -> None:
        """Send a fixed greeting immediately when the agent joins the session."""
        self._reset()
        greet = os.getenv("INTENT_GREETING", "hello, how can I help you?")
        try:
            await self.session.say(greet, add_to_chat_ctx=False, allow_interruptions=False)
        except Exception as e:
            log.exception("Greeting TTS failed: %s", e)

    async def on_user_message(self, session: AgentSession, text: str, ctx: Optional[RunContext] = None):
        """Keep our own minimal history and set the current flow."""
        if not text:
            return
        self._turn_id += 1
        self.history.append({"role": "user", "content": text})
        if (not self._intent_started) or (not self.sticky_flow and self._should_reclassify(text)):
            self._intent_started = True
            args = classify_intent(text)
            print_report_intent_line(args)
            self.flow_code = args.get("intent_code")
            self.flow_prompt = FLOW_PROMPTS.get(
                self.flow_code,
                "You are a helpful, concise assistant.\n{dynamic_context}",
            )
            log.info(f"Flow selected: {self.flow_code}")

    # -----------------------
    # ChatContext construction (strict OpenAI content part shape)
    # -----------------------

    @staticmethod
    def _text_parts(text: str) -> list[dict]:
        # OpenAI-style content part
        return [{"type": "text", "text": text}]

    def _mk_msg(self, role: str, text: str) -> ChatMessage:
        # Always construct with parts; avoid plain string to pass validation.
        return ChatMessage(role=role, content=self._text_parts(text))

    def _rewrite_ctx_inplace(self, ctx: ChatContext):
        """Replace pipeline ctx with our system + minimal history using valid content parts."""
        dynamic_context = json.dumps(self.state, ensure_ascii=False)
        system_prompt = (self.flow_prompt or "You are a helpful, concise assistant.\n{dynamic_context}").format(
            dynamic_context=dynamic_context
        )
        # Reset messages list safely
        try:
            ctx.messages.clear()
        except Exception:
            setattr(ctx, "messages", [])

        ctx.messages.append(self._mk_msg("system", system_prompt))
        # Minimal rolling history (can expand if you want more)
        for m in self.history[-6:]:
            ctx.messages.append(self._mk_msg(m["role"], m["content"]))

        # Safety: ensure at least one user message to avoid empty-array 400s
        if not any(msg.role == "user" for msg in ctx.messages):
            ctx.messages.append(self._mk_msg("user", "."))

        # Optional debug
        try:
            logger.info(f"ctx.messages prepared: {len(ctx.messages)} items; roles={[m.role for m in ctx.messages]}")
        except Exception:
            pass

    # -----------------------
    # LLM node (forced single tool call; we speak ourselves)
    # -----------------------

    async def llm_node(
        self,
        chat_ctx: ChatContext,                  # provided by pipeline — we overwrite it in-place
        tools: list[FunctionTool],
        model_settings,
    ):
        async with self._planner_lock:
            if not self._intent_started:
                last_user = next((m["content"] for m in reversed(self.history) if m["role"] == "user"), "")
                if last_user:
                    self._intent_started = True
                    args = classify_intent(last_user)
                    print_report_intent_line(args)
                    self.flow_code = args.get("intent_code")
                    self.flow_prompt = FLOW_PROMPTS.get(
                        self.flow_code,
                        "You are a helpful, concise assistant.\n{dynamic_context}",
                    )

            # Build valid context (no default leakage)
            self._rewrite_ctx_inplace(chat_ctx)
            tool_choice = {"type": "function", "function": {"name": "next_action"}}

            # Robust response extraction across plugin versions
            message = None
            try:
                # Try non-streaming first
                resp = await self.llm.chat(chat_ctx=chat_ctx, tools=[next_action], tool_choice=tool_choice)
                message = getattr(resp, "message", None)
                if message is None and getattr(resp, "choices", None):
                    message = getattr(resp.choices[0], "message", None)
                if message is None:
                    raise AttributeError("no message on non-streaming response")
            except Exception:
                # Streaming fallback
                last_event_msg = None
                async with self.llm.chat(chat_ctx=chat_ctx, tools=[next_action], tool_choice=tool_choice) as stream:
                    async for ev in stream:
                        if hasattr(ev, "message") and ev.message:
                            last_event_msg = ev.message
                if hasattr(stream, "message") and getattr(stream, "message"):
                    message = stream.message
                elif hasattr(stream, "final_message") and getattr(stream, "final_message"):
                    message = stream.final_message
                elif hasattr(stream, "get_final_message"):
                    try:
                        message = await stream.get_final_message()
                    except Exception:
                        message = None
                if message is None:
                    message = last_event_msg

            if not message:
                await self.session.say("Sorry—I'm having trouble right now. Could you repeat that?", add_to_chat_ctx=False)
                return

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                await self.session.say("Got it. What would you like to do next?", add_to_chat_ctx=False)
                return

            # Parse next_action payload (args_json is a JSON string)
            tc = tool_calls[0]
            raw_args = (tc.get("function") or {}).get("arguments") if isinstance(tc, dict) \
                       else getattr(getattr(tc, "function", None), "arguments", None)

            try:
                parsed = json.loads(raw_args or "{}")
            except Exception:
                parsed = {}

            name = (parsed.get("name") or "").strip()
            payload_raw = parsed.get("args_json")
            if isinstance(payload_raw, str):
                try:
                    payload = json.loads(payload_raw)
                except Exception:
                    payload = {}
            elif isinstance(payload_raw, dict):
                payload = payload_raw
            else:
                payload = {}

            utterance = (payload.get("utterance") or "").strip()
            state_updates = payload.get("state_updates") or {}

            if state_updates:
                _safe_json_merge(self.state, state_updates)

            await self._apply_domain_action(name)

            # guards
            last_assist = next((m for m in reversed(self.history) if m["role"] == "assistant"), None)
            if last_assist and _near_dup(utterance, last_assist.get("content", "")):
                utterance = "Just to clarify—" + utterance

            if self.drop_double_question and "?" in utterance:
                if self._awaiting_answer and self._last_question_turn_id == self._turn_id:
                    utterance = utterance.replace("?", ".")
                else:
                    self._awaiting_answer = True
                    self._last_question_turn_id = self._turn_id

            if utterance:
                try:
                    await self.session.say(utterance, add_to_chat_ctx=True)
                except Exception as e:
                    log.exception("TTS say failed: %s", e)
                self.history.append({"role": "assistant", "content": utterance})

            self.state.setdefault("meta", {}).setdefault("last_action", None)
            self.state["meta"]["last_action"] = name
            return

    # -----------------------
    # Domain side-effects
    # -----------------------

    async def _apply_domain_action(self, action_name: str):
        action_name = (action_name or "").strip().upper()
        if not action_name:
            return

        if action_name == "SUGGEST_WINDOWS":
            if not self.state.get("tools", {}).get("suggested_windows"):
                self.state.setdefault("tools", {})["suggested_windows"] = self._generate_two_windows()
                log.info("Suggested windows set: %s", self.state["tools"]["suggested_windows"])

        elif action_name == "VALIDATE_ADDRESS_ZONE":
            addr = (self.state.get("slots", {}) or {}).get("address") or ""
            ok, reason = self._validate_address(addr)
            self.state.setdefault("tools", {})["zone_valid"] = ok
            if not ok:
                self.state["tools"]["zone_invalid_reason"] = reason
            log.info("Address validation: ok=%s reason=%r", ok, reason)

        elif action_name == "CREATE_BOOKING":
            booking_id = str(uuid.uuid4())[:8]
            self.state.setdefault("tools", {})["booking_id"] = booking_id
            self.state.setdefault("tools", {})["booking_status"] = "CREATED"
            log.info("Booking created: %s", booking_id)

        elif action_name in {"DONE_WRAP", "END", "HANGUP"}:
            log.info("Planner requested end: %s", action_name)

    # -----------------------
    # Utilities
    # -----------------------

    def _reset(self):
        self._planner_lock = asyncio.Lock()
        self._intent_started = False
        self._awaiting_answer = False
        self._turn_id = 0
        self._last_question_turn_id = None
        self.flow_code = None
        self.flow_prompt = None
        self.state = json.loads(json.dumps(DEFAULT_STATE))
        self.history = []

    def _should_reclassify(self, text: str) -> bool:
        if not self.flow_code:
            return True
        t = text.lower()
        hints = {
            "CANCEL": ["cancel", "call off"],
            "RESCHEDULE": ["reschedule", "change time", "move appointment"],
            "PRICE": ["price", "cost", "how much", "fee", "quote"],
            "STATUS": ["status", "is it confirmed", "appointment state"],
            "ETA": ["eta", "when arrive", "how long", "arrival"],
            "BOOK": ["book", "schedule", "appointment", "come fix", "leak", "burst", "clog"],
        }
        for code, keys in hints.items():
            if any(k in t for k in keys) and code != self.flow_code:
                return True
        return False

    def _generate_two_windows(self) -> List[str]:
        return ["Tomorrow 09:00–12:00", "Tomorrow 13:00–16:00"]

    def _validate_address(self, address: str) -> Tuple[bool, Optional[str]]:
        address = (address or "").strip()
        if not address:
            return False, "No address provided"
        has_zip = any(ch.isdigit() for ch in address)
        if not has_zip:
            return False, "Missing ZIP code"
        digits = ''.join(ch for ch in address if ch.isdigit())
        if digits and not digits.startswith("34"):
            return False, "Out of service area"
        return True, None

# ---------------------------
# Worker entrypoint
# ---------------------------

async def entrypoint(ctx: JobContext) -> None:
    """
    Start an AgentSession using an English turn detector (matches the example you provided).
    llm_node controls the system prompt and tool usage.
    """
    session = AgentSession(
        vad=silero.VAD.load(),
        turn_detection=EnglishModel(),
    )
    await session.start(agent=IntentAgent(), room=ctx.room)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
