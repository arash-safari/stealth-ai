import os
import sys
import ssl
import hmac
import hashlib
import asyncio
import logging
from typing import Optional, Callable, Awaitable
from contextlib import suppress

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import AgentSession
from livekit.plugins import deepgram, openai, cartesia, silero

from common.models import UserData
from agents.router import Router
from agents.booking import Booking
from agents.reschedule import Reschedule
from agents.cancel import Cancel
from agents.parts import Parts
from agents.status import Status
from agents.pricing import Pricing
from agents.billing import Billing
from agents.operator import Operator

# Optional central logging
from common.logging_config import configure_logging

# ----------------------------------------------------------------------------
# Bootstrapping
# ----------------------------------------------------------------------------
load_dotenv()
configure_logging()
logger = logging.getLogger("plumber-contact-center")
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------------
# Config & Validation
# ----------------------------------------------------------------------------
DG_SAMPLE_RATE = int(os.getenv("DG_SAMPLE_RATE", "48000"))  # 16000 or 48000
DG_CHANNELS = int(os.getenv("DG_CHANNELS", "1"))
DG_ENCODING = os.getenv("DG_ENCODING", "linear16")
DG_MODEL = os.getenv("DG_MODEL", "nova-2-general")  # adjust to your plan
DG_KEEPALIVE = os.getenv("DG_KEEPALIVE", "true").lower() == "true"

OPENAI_MODEL = os.getenv("OPENAI_TURNS_MODEL", "gpt-4o")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "ash")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return "<none>"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"sha256:{digest[:8]}"


if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY is missing. OpenAI calls will fail.")
else:
    logger.info("OpenAI key: %s", _mask_key(OPENAI_API_KEY))

if not DEEPGRAM_API_KEY:
    logger.warning("DEEPGRAM_API_KEY is missing. Deepgram realtime will fail.")
else:
    logger.info("Deepgram key: %s", _mask_key(DEEPGRAM_API_KEY))

logger.info(
    "Deepgram audio config -> sample_rate=%s, channels=%s, encoding=%s, model=%s, keepalive=%s",
    DG_SAMPLE_RATE,
    DG_CHANNELS,
    DG_ENCODING,
    DG_MODEL,
    DG_KEEPALIVE,
)

# ----------------------------------------------------------------------------
# Voice IDs (generalized: can be Cartesia or OpenAI)
# ----------------------------------------------------------------------------
# voices dict now supports multiple providers per agent.
# Example: voices["booking"] = {"provider": "openai", "voice": "alloy"}
# or voices["booking"] = {"provider": "cartesia", "voice": "uuid-here"}
voices = {
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


def build_tts(provider: str, voice: str):
    if provider == "openai":
        return openai.TTS(model=OPENAI_TTS_MODEL, voice=voice)
    elif provider == "cartesia":
        return cartesia.TTS(voice_id=voice)
    else:
        raise ValueError(f"Unsupported TTS provider: {provider}")

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


# ----------------------------------------------------------------------------
# Deepgram STT factory with explicit options
# ----------------------------------------------------------------------------

def build_deepgram_stt() -> deepgram.STT:
    try:
        stt_opts = deepgram.STTOptions(
            sample_rate=DG_SAMPLE_RATE,
            encoding=DG_ENCODING,
            channels=DG_CHANNELS,
            model=DG_MODEL,
            interim_results=True,
            smart_format=True,
            punctuate=True,
            enable_keepalive=DG_KEEPALIVE,
        )
        stt = deepgram.STT(options=stt_opts, api_key=DEEPGRAM_API_KEY)
    except AttributeError:
        logger.warning("deepgram.STTOptions not found; falling back to default constructor.")
        stt = deepgram.STT()
    return stt


# ----------------------------------------------------------------------------
# Entrypoint (robust)
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

    stt = build_deepgram_stt()

    session = AgentSession[UserData](
        userdata=userdata,
        stt=stt,
        llm=openai.LLM(model=OPENAI_MODEL),
        tts=build_tts("openai", OPENAI_TTS_VOICE),
        vad=silero.VAD.load(),
        max_tool_steps=6,
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
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
