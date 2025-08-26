# agents/intent_agent.py
import logging
from pathlib import Path
from typing import Any, Dict, Deque, Tuple
from collections import deque
import os

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, AgentSession, RunContext
from livekit.plugins import deepgram, cartesia, silero, noise_cancellation
from livekit.plugins import openai as lk_openai  # safer alias
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from .state import AgentState, SLOT_TO_STATE
from .validators import validate_and_normalize, MID_CONF, HIGH_CONF
from constants.flows import FLOWS as FLOW_SPECS

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env.local")
logger = logging.getLogger("intent-agent")
logger.setLevel(logging.INFO)

# ---------- FlowRunner ----------
class FlowRunner:
    def __init__(self, flow_name: str, spec: Dict[str, Any]):
        self.flow_name = flow_name
        self.spec = spec
        self.current_step = spec.get("entry")
        self.slots: Dict[str, Any] = {}
        self.waiting_for_slot: str | None = None
        self._asked_steps: set[str] = set()

    def resolve_alias(self, key: str) -> str:
        aliases = self.spec.get("aliases", {})
        return aliases.get(key, key)

    def format_text(self, text: str) -> str:
        try:
            return text.format(**self.slots)
        except Exception:
            return text

    def get_step(self) -> Dict[str, Any] | None:
        return self.spec.get("steps", {}).get(self.current_step)

    def advance(self, next_key: str | None) -> None:
        if not next_key:
            self.current_step = None
        else:
            self.current_step = self.resolve_alias(next_key)

    def missing_required_slots(self) -> list[str]:
        req = self.spec.get("required_slots", [])
        return [s for s in req if s not in self.slots or not str(self.slots[s]).strip()]

# ---------- Intent metadata ----------
def _extract_intents_from_flows() -> list[dict]:
    intents = []
    for name, spec in (FLOW_SPECS or {}).items():
        meta = (spec or {}).get("intent") or {}
        intents.append({
            "name": str(name),
            "description": str(meta.get("desc") or meta.get("description") or ""),
            "samples": [str(s) for s in (meta.get("examples") or [])],
        })
    intents.sort(key=lambda x: x["name"])
    return intents

def _build_intent_prompt(intents: list[dict]) -> str:
    lines = [
        "You are a concise, friendly voice assistant.",
        "Task A) Greet first with: 'Hi, how can I help you?'",
        "Task B) Classify the caller's request into exactly ONE of the intents below.",
        "After classification, follow strict flow instructions and call tools as directed.",
        "",
        "Intents:",
    ]
    for it in intents:
        lines.append(f"- name: {it['name']}")
        if it["description"]: lines.append(f"  description: {it['description']}")
        if it["samples"]: lines.append(f"  samples: {'; '.join(it['samples'][:5])}")
    lines += [
        "- name: OTHER",
        "  description: None of the listed intents fits clearly.",
        "",
        "When the assistant asks for a slot, do not speak after the user answers;",
        "CALL the tool 'provide_slot(slot_name, slot_value, confidence)'. If unsure, use slot_value='' and confidence=0.0.",
    ]
    return "\n".join(lines)

# ---------- Slot/state helpers ----------
def _slot_is_filled(state: AgentState, slot_name: str) -> Tuple[bool, float, bool]:
    """Return (filled, confidence, needs_confirmation)."""
    state_key = SLOT_TO_STATE.get(slot_name)
    if not state_key:
        return False, 0.0, False
    entry = state.data.get(state_key)
    if not entry or entry.value is None or str(entry.value).strip() == "":
        return False, 0.0, False
    return True, float(entry.confidence or 0.0), bool(entry.needs_confirmation or False)

def _slots_summary(state: AgentState, flow_req: list[str]) -> str:
    parts = []
    for s in flow_req:
        filled, conf, needs_c = _slot_is_filled(state, s)
        if filled:
            val = state.data[SLOT_TO_STATE[s]].value
            parts.append(f"{s}={val!s} (conf={conf:.2f}{', confirm' if needs_c else ''})")
        else:
            parts.append(f"{s}=<missing>")
    return "; ".join(parts)

# ---------- IntentAgent ----------
class IntentAgent(Agent):
    def __init__(self) -> None:
        self._intents = _extract_intents_from_flows()
        self._flow: FlowRunner | None = None
        self._state = AgentState()

        # Keep last 20 messages (agent + user combined)
        self._history: Deque[Tuple[str, str]] = deque(maxlen=20)
        self._last_speaker: str = "AGENT"  # we greet first

        # gate: ignore premature tool calls until a new user turn arrives
        self._waiting_for_new_user_turn: bool = False

        audio_on = os.getenv("INTENT_AUDIO", "0") == "1"

        turn_detector = None
        if audio_on:
            try:
                turn_detector = MultilingualModel()
            except RuntimeError:
                turn_detector = None

        super().__init__(
            stt=deepgram.STT(model="nova-3", language="multi") if audio_on else None,
            llm=lk_openai.LLM(model=os.getenv("AGENT_MODEL", "gpt-4o-mini")),
            tts=cartesia.TTS(model="sonic-2") if audio_on else None,
            vad=silero.VAD.load() if audio_on else None,
            turn_detection=turn_detector,
            instructions=_build_intent_prompt(self._intents),
        )

    # ----- history helpers -----
    def note_agent(self, text: str) -> None:
        if text:
            self._history.append(("AGENT", text))
            self._last_speaker = "AGENT"

    def note_user(self, text: str) -> None:
        if text:
            self._history.append(("USER", text))
            self._last_speaker = "USER"
            # a real user turn unblocks slot acceptance
            self._waiting_for_new_user_turn = False

    def history_snapshot(self, k: int = 20) -> str:
        rows = list(self._history)[-k:]
        return " | ".join(f"{w}: {m}" for w, m in rows)

    # ----- greet -----
    async def on_enter(self):
        await self.session.say("Hi, how can I help you?")
        self.note_agent("Hi, how can I help you?")

    # ----- (optional) classification using history -----
    async def classify_from_history(self, user_utterance: str):
        self.note_user(user_utterance)
        hist = self.history_snapshot()
        intents_lines = "\n".join(
            [f"- {it['name']}: {it.get('description','')}" for it in self._intents] + ["- OTHER: fallback"]
        )
        instr = (
            "Classify the user's request into exactly ONE intent from this list:\n"
            f"{intents_lines}\n\n"
            f"Recent history: {hist}\n"
            "Immediately CALL report_intent(intent_name, confidence, rationale). "
            "Do not speak; only call the tool."
        )
        await self.session.generate_reply(user_input=user_utterance, instructions=instr)

    # ----- tools -----
    @function_tool
    async def report_intent(self, context: RunContext, intent_name: str, confidence: float = 0.0, rationale: str = ""):
        name = (intent_name or "").strip().upper()
        if name not in FLOW_SPECS:
            name = "OTHER"
        print(f"[INTENT] name={name} conf={confidence:.2f} why={rationale}")
        await self.start_flow(name)

    @function_tool
    async def provide_slot(self, context: RunContext, slot_name: str, slot_value: str, confidence: float = 0.0):
        # server-side guard: only accept after a *new* user turn
        if self._waiting_for_new_user_turn or self._last_speaker != "USER":
            print("[SLOT] premature tool call ignored (waiting for user)")
            return None, "Waiting for user"

        if not self._flow or not self._flow.waiting_for_slot:
            return None, "No slot expected right now."

        expected = self._flow.waiting_for_slot
        if slot_name != expected:
            slot_name = expected

        # record short user-derived info into history (kept compact)
        self.note_user(f"{slot_name}={slot_value}")

        # validate + decide
        is_valid, norm, reason = validate_and_normalize(slot_name, slot_value)
        action = "advance"
        needs_confirmation = False

        if not is_valid:
            action = "reask"
        elif confidence < MID_CONF:
            action = "clarify"
        elif MID_CONF <= confidence < HIGH_CONF:
            needs_confirmation = True
            action = "confirm"

        # store
        state_key = SLOT_TO_STATE.get(slot_name)
        if state_key:
            self._state.set(state_key, norm if is_valid else slot_value, max(confidence, 0.0), needs_confirmation)

        print(f"[SLOT] {slot_name} = {norm if is_valid else slot_value} (valid={is_valid}, conf={confidence:.2f}, action={action})")

        # clear wait-for-slot
        self._flow.waiting_for_slot = None

        # branches
        if action == "reask":
            msg = "Sorry, I didn’t catch that. " + (self._flow.get_step().get("question", "Could you repeat?") if self._flow.get_step() else "")
            await self.session.say(msg)
            self.note_agent(msg)
            await self._arm_slot_capture(slot_name, self._flow.get_step() or {})
            return None, "Reasked."

        if action in ("clarify", "confirm"):
            disp = norm if is_valid else slot_value
            q = f"Just to confirm, did you say: {disp}?"
            await self.session.say(q)
            self.note_agent(q)
            await self._arm_slot_capture(slot_name, self._flow.get_step() or {}, clarification=True)
            return None, "Clarifying."

        # accepted → continue
        await self.continue_flow(context)
        return None, "Advanced."

    @function_tool
    async def continue_flow(self, context: RunContext):
        if not self._flow:
            return None, "No active flow."
        step = self._flow.get_step()
        self._flow.advance(step.get("next") if step else None)
        await self._step_loop()
        return None, "Continued."

    async def start_flow(self, flow_name: str):
        spec = FLOW_SPECS.get(flow_name, FLOW_SPECS.get("OTHER"))
        self._flow = FlowRunner(flow_name, spec)
        await self._step_loop()

    # ----- arming & asking -----
    async def _arm_slot_capture(self, slot: str, step: dict, clarification: bool = False):
        """Arm capture for exactly one slot. STRICT WAIT MODE: do nothing until a NEW user message arrives."""
        self._flow.waiting_for_slot = slot
        self._waiting_for_new_user_turn = True  # block premature tool calls
        expected = (step or {}).get("expected", "")

        hist = self.history_snapshot()
        req = self._flow.spec.get("required_slots", []) if self._flow else []
        known = _slots_summary(self._state, req)

        extra = "This is a confirmation.\n" if clarification else ""
        instr = (
            f"{extra}"
            "CONTROLLER (STRICT WAIT MODE):\n"
            f"- LAST_SPEAKER={self._last_speaker}.\n"
            "- Do NOT produce assistant text now. Do NOT call any tool now. "
            "Do NOTHING until a NEW user message arrives.\n\n"
            "When (and only when) a NEW user message arrives:\n"
            f"  1) Using History+Known below, extract a concise value for slot '{slot}' "
            f"(expected: {expected if expected else 'free text'}). Prefer the NEW message; fallback to history if unambiguous.\n"
            "  2) Compute confidence 0.0–1.0 by exactness/format.\n"
            f"  3) CALL provide_slot(slot_name='{slot}', slot_value=<value>, confidence=<score>).\n"
            "  4) Do not output assistant text here.\n\n"
            f"History: {hist}\n"
            f"Known slots: {known}\n"
        )
        await self.session.generate_reply(instructions=instr)

    async def _ask_slot(self, step: dict):
        """Ask exactly this step’s question unless already filled; then WAIT."""
        slot = step.get("slot")
        if slot:
            filled, conf, needs_c = _slot_is_filled(self._state, slot)
            if filled and not needs_c and conf >= HIGH_CONF:
                self._flow.advance(step.get("next"))
                await self._step_loop()
                return
            if filled and needs_c:
                val = self._state.data[SLOT_TO_STATE[slot]].value
                q = f"Just to confirm, did you say: {val}?"
                await self.session.say(q); self.note_agent(q)
                await self._arm_slot_capture(slot, step, clarification=True)
                return

        q = step.get("question", "Could you tell me more?")
        await self.session.say(q); self.note_agent(q)
        await self._arm_slot_capture(slot or "", step)
        return  # always wait

    async def _do_system(self, step: dict):
        text = step.get("question", "")
        filled = text
        for k, entry in self._state.data.items():
            filled = filled.replace("{"+k.value+"}", str(entry.value))
        await self.session.say(filled); self.note_agent(filled)

    # ----- main loop (no back-to-back messages) -----
    async def _step_loop(self):
        if not self._flow:
            return

        while self._flow and self._flow.current_step:
            step = self._flow.get_step()
            if not step:
                break

            stype = step.get("type")
            nxt = step.get("next")

            if stype == "slot":
                await self._ask_slot(step)
                return  # wait for provide_slot()

            elif stype == "system":
                await self._do_system(step)

                # If this system line wants a reply and next is a slot, arm and WAIT
                sys_q = (step.get("question") or "").strip()
                hold_for_reply = bool(step.get("hold_for_reply")) or sys_q.endswith("?")
                next_step = (self._flow.spec.get("steps", {}) or {}).get(nxt)

                if hold_for_reply and next_step and next_step.get("type") == "slot":
                    await self._arm_slot_capture(next_step.get("slot"), next_step)
                    self._flow.advance(nxt)
                    return  # wait

                # otherwise advance but DON'T speak more in this tick
                self._flow.advance(nxt)
                return

            else:
                # unknown -> skip
                self._flow.advance(nxt)
                continue

        # If we finished and we're not waiting on a slot, wrap up
        if self._flow and not self._flow.waiting_for_slot:
            await self.session.say("Anything else I can help you with?")
            self.note_agent("Anything else I can help you with?")

# ----- worker entrypoint -----
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        preemptive_generation=True,  # keep snappy on telephony; tests can disable if desired
    )
    agent = IntentAgent()

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=dict(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="intent-agent"))
