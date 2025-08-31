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
from agents.parts import Parts
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
    "parts": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "opal"},
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
            "parts": Parts(voices=voices),
            "status": Status(voices=voices),
            "pricing": Pricing(voices=voices),
            "billing": Billing(voices=voices),
            "operator": Operator(voices=voices),
        }
    )

    # STT & LLM
    stt = build_deepgram_stt(CONFIG, DEEPGRAM_API_KEY)
    llm = openai.LLM(model=OPENAI_LLM_MODEL)

    # TTS via voice factory (provider-agnostic)
    session_tts = build_tts_for(SESSION_VOICE_AGENT, voices)
    logger.info("Session TTS -> agent=%s", SESSION_VOICE_AGENT)

    # Optional VAD
    vad = silero.VAD.load() if VAD_ENABLED else None
    logger.info("VAD enabled: %s", VAD_ENABLED)

    session = AgentSession[UserData](
        userdata=userdata,
        stt=stt,
        llm=llm,
        tts=session_tts,
        vad=vad,
        max_tool_steps=MAX_TOOL_STEPS,
    )

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

    await run_with_retries(_start_session, on_error=_on_error)


if __name__ == "__main__":
    asyncio.run(_bootstrap())
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
