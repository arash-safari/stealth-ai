# main.py
import os
import asyncio
import logging
from contextlib import suppress
from typing import Optional, Callable, Awaitable, Dict, Any

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import AgentSession
from livekit.plugins import openai, silero

from common.models import UserData
from common.voice_factory import build_tts_for
from common.config_loader import load_config, cfg_get, mask_key
from common.stt_factory import build_deepgram_stt

for name in ("cloud.secrets.env", ".env.local", "env.local", ".env"):
    if os.path.exists(name):
        load_dotenv(name, override=False)

from agents.router import Router
from agents.booking import Booking
from agents.reschedule import Reschedule
from agents.cancel import Cancel
from agents.status import Status
from agents.pricing import Pricing
from agents.billing import Billing
from agents.operator import Operator
from db.models import init_db
from db.session import ping as db_ping
# Optional central logging
from common.logging_config import configure_logging

for name in ("cloud.secrets.env", ".env.local", "env.local", ".env"):
    if os.path.exists(name):
        load_dotenv(name, override=False)

# ----------------------------------------------------------------------------
# Bootstrapping
# ----------------------------------------------------------------------------
load_dotenv()
configure_logging()

logger = logging.getLogger("plumber-contact-center")
logger.setLevel(logging.INFO)

CONFIG: Dict[str, Any] = load_config()

# ENV keys
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY is missing. OpenAI calls will fail.")
else:
    logger.info("OpenAI key: %s", mask_key(OPENAI_API_KEY))

if not DEEPGRAM_API_KEY:
    logger.warning("DEEPGRAM_API_KEY is missing. Deepgram realtime will fail.")
else:
    logger.info("Deepgram key: %s", mask_key(DEEPGRAM_API_KEY))

for name in ["livekit.agents", "livekit.plugins.deepgram", "aiohttp.client", "aiohttp.client_ws"]:
    logging.getLogger(name).setLevel(logging.DEBUG)

# ----------------------------------------------------------------------------
# Core models & voices
# ----------------------------------------------------------------------------
OPENAI_LLM_MODEL = cfg_get(CONFIG, "openai.llm_model", "gpt-4o")
OPENAI_TTS_MODEL = cfg_get(CONFIG, "openai.tts_model", "gpt-4o-mini-tts")
OPENAI_DEFAULT_VOICE = cfg_get(CONFIG, "openai.default_voice", None)

# Voices per agent (configurable). You can override in config.yaml under `voices:`
voices: Dict[str, Dict[str, str]] = cfg_get(CONFIG, "voices", {}) or {
    "router": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "alloy"},
    "booking": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "verse"},
    "reschedule": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "sage"},
    "cancel": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "breeze"},
    "status": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "pearl"},
    "pricing": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "aria"},
    "billing": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "lyra"},
    "operator": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "nala"},
}
# If a global OpenAI default voice is set, seed a voices["default"] for the factory fallback.
if OPENAI_DEFAULT_VOICE and "default" not in voices:
    voices["default"] = {"provider": "openai", "model": OPENAI_TTS_MODEL, "voice": OPENAI_DEFAULT_VOICE}

# Which agent voice powers the session TTS (defaults to router)
SESSION_VOICE_AGENT = cfg_get(CONFIG, "session.voice_agent", "router")
VAD_ENABLED = bool(cfg_get(CONFIG, "session.vad", True))
MAX_TOOL_STEPS = int(cfg_get(CONFIG, "session.max_tool_steps", 6))

# ----------------------------------------------------------------------------
# Helpers: robust retry with cancel, jitter, and backoff
# ----------------------------------------------------------------------------
async def run_with_retries(
    func: Callable[[], Awaitable[None]],
    *,
    max_tries: int = 6,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    on_error: Optional[Callable[[int, BaseException], Awaitable[None]]] = None,
) -> None:
    delay = base_delay
    for attempt in range(max_tries):
        try:
            await func()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if on_error:
                with suppress(Exception):
                    await on_error(attempt, e)
            if attempt == max_tries - 1:
                raise
            jitter = (asyncio.get_running_loop().time() % 1.0) * (delay * 0.5)
            sleep_for = min(delay + jitter, max_delay)
            logger.warning(
                "Retrying after error (attempt %s/%s) in %.2fs: %r",
                attempt + 1,
                max_tries,
                sleep_for,
                e,
            )
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2.0, max_delay)

async def _bootstrap():
    await init_db()
    with suppress(Exception):
        await db_ping()

# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------
async def entrypoint(ctx: JobContext):
    userdata = UserData()
    userdata.agents.update(
        {
            "router": Router(voices=voices),
            "booking": Booking(voices=voices),
            "reschedule": Reschedule(voices=voices),
            "cancel": Cancel(voices=voices),
            "status": Status(voices=voices),
            "pricing": Pricing(voices=voices),
            "billing": Billing(voices=voices),
            "operator": Operator(voices=voices),
        }
    )

    # STT & LLM
    # stt = build_deepgram_stt(CONFIG, DEEPGRAM_API_KEY)
    stt = openai.STT(model="gpt-4o-transcribe", language="en")

    llm = openai.LLM(model=OPENAI_LLM_MODEL)

    # TTS via voice factory (provider-agnostic)
    session_tts = build_tts_for(SESSION_VOICE_AGENT, voices)
    logger.info("Session TTS -> agent=%s", SESSION_VOICE_AGENT)

    # Optional VAD (a bit more permissive so quiet mics still trigger)
    if VAD_ENABLED:
        try:
            vad = silero.VAD.load(activation_threshold=0.35, min_silence_duration=0.25)
        except Exception as e:
            logger.warning("VAD load failed, continuing without VAD: %s", e)
            vad = None
    else:
        vad = None
    logger.info("VAD enabled: %s", bool(vad))

    # --- Keep user audio during agent turns; make interruptions responsive ---
    session = AgentSession[UserData](
        userdata=userdata,
        stt=stt,
        llm=llm,
        tts=session_tts,
        vad=vad,
        max_tool_steps=MAX_TOOL_STEPS,
        allow_interruptions=True,                 # let the user barge in
        discard_audio_if_uninterruptible=False,   # buffer mic audio instead of dropping it
        min_interruption_duration=0.2,            # responsive interruptions
        min_interruption_words=0,
        min_endpointing_delay=0.2,                # end user turn a bit quicker
        max_endpointing_delay=6.0,                # cap very long rambles
    )

    # ---- Diagnostics: log agent state & user transcripts; silence watchdog ----
    last_heard_ts: float = 0.0
    try:
        @session.on("agent_state_changed")
        def _on_state(evt):
            logger.debug("agent_state=%s", getattr(evt, "state", evt))

        @session.on("user_input_transcribed")
        def _on_user(evt):
            nonlocal last_heard_ts
            last_heard_ts = asyncio.get_running_loop().time()
            logger.debug(
                "USER[%s]: %s",
                "final" if getattr(evt, "is_final", False) else "partial",
                getattr(evt, "transcript", ""),
            )
    except Exception:
        # Older SDKs may not expose these events; ignore if unavailable.
        pass

    async def _silence_watchdog(timeout_s: float = 12.0, interval: float = 1.0):
        """Warn once if no transcripts arrive for a while (helps spot gating/device issues)."""
        start = asyncio.get_running_loop().time()
        warned = False
        while not warned:
            await asyncio.sleep(interval)
            now = asyncio.get_running_loop().time()
            if last_heard_ts == 0.0 and (now - start) > timeout_s:
                logger.warning(
                    "No user transcripts for %.0fs. If the console meter moves but nothing transcribes, "
                    "audio is likely gated by turn-taking; if the meter is flat (~-70 dBFS), select the correct "
                    "mic in the 'Recording' tab of pavucontrol.",
                    timeout_s,
                )
                warned = True

    async def _on_error(attempt: int, err: BaseException) -> None:
        msg = str(err)
        if "Connection reset by peer" in msg or "1011" in msg:
            logger.error("Deepgram WS dropped (attempt %s): %s", attempt + 1, msg)
        elif "401" in msg or "403" in msg:
            logger.error("Deepgram auth/permission error: %s", msg)
        else:
            logger.error("Session start failed: %s", msg)

    async def _start_session() -> None:
        await session.start(agent=userdata.agents["router"], room=ctx.room)
        # belt & suspenders: make sure mic is accepting audio
        try:
            session.input.set_audio_enabled(True)
            logger.debug("mic explicitly enabled after session.start()")
        except Exception:
            pass
        # arm the silence watchdog (non-fatal; just logs once if nothing is heard)
        asyncio.create_task(_silence_watchdog())

    await run_with_retries(_start_session, on_error=_on_error)

if __name__ == "__main__":
    asyncio.run(_bootstrap())
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
