# main.py
import os
import hashlib
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, Any
from contextlib import suppress

import yaml
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
# Config loading
# ----------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """
    Load YAML configuration from CONFIG_PATH (env) or ./config.yaml.
    Returns an empty dict if the file doesn't exist or can't be parsed.
    """
    config_path = Path(os.getenv("CONFIG_PATH", "config.yaml"))
    if not config_path.exists():
        logger = logging.getLogger("plumber-contact-center")
        logger.warning("Config file not found at %s. Using built-in defaults.", config_path)
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        logger = logging.getLogger("plumber-contact-center")
        logger.error("Failed to parse %s: %s. Using built-in defaults.", config_path, e)
        return {}


def _cfg_get(d: Dict[str, Any], path: str, default=None):
    """
    Safely fetch a nested key via dotted path, e.g. _cfg_get(cfg, 'openai.llm_model')
    """
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ----------------------------------------------------------------------------
# Bootstrapping
# ----------------------------------------------------------------------------
load_dotenv()
configure_logging()
logger = logging.getLogger("plumber-contact-center")
logger.setLevel(logging.INFO)

CONFIG = _load_config()

# ----------------------------------------------------------------------------
# ENV keys (still from .env)
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# Config (from YAML with sensible defaults)
# ----------------------------------------------------------------------------
# OpenAI models
OPENAI_LLM_MODEL = _cfg_get(CONFIG, "openai.llm_model", "gpt-4o")
OPENAI_TTS_MODEL_FROM_CFG = _cfg_get(CONFIG, "openai.tts_model", "gpt-4o-mini-tts")
OPENAI_DEFAULT_VOICE = _cfg_get(CONFIG, "openai.default_voice", None)  # may be None

# Deepgram audio
DG_SAMPLE_RATE = int(_cfg_get(CONFIG, "deepgram.sample_rate", 48000))
DG_CHANNELS = int(_cfg_get(CONFIG, "deepgram.channels", 1))
DG_ENCODING = _cfg_get(CONFIG, "deepgram.encoding", "linear16")
DG_MODEL = _cfg_get(CONFIG, "deepgram.model", "nova-2-general")
DG_KEEPALIVE = bool(_cfg_get(CONFIG, "deepgram.keepalive", True))

logger.info(
    "Deepgram audio config -> sample_rate=%s, channels=%s, encoding=%s, model=%s, keepalive=%s",
    DG_SAMPLE_RATE,
    DG_CHANNELS,
    DG_ENCODING,
    DG_MODEL,
    DG_KEEPALIVE,
)

# Voices per agent (provider/model/voice) from config, fallback to prior hardcoded map
voices: Dict[str, Dict[str, str]] = _cfg_get(CONFIG, "voices", {}) or {
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

# Choose a default session TTS. Prefer openai.default_voice if provided,
# otherwise use the router agent's voice (common for entry prompts).
_default_provider = "openai"
router_v = voices.get("router", {})
DEFAULT_TTS_PROVIDER = _default_provider if OPENAI_DEFAULT_VOICE else router_v.get("provider", _default_provider)
DEFAULT_TTS_MODEL = (
    OPENAI_TTS_MODEL_FROM_CFG if OPENAI_DEFAULT_VOICE else router_v.get("model", OPENAI_TTS_MODEL_FROM_CFG)
)
DEFAULT_TTS_VOICE = OPENAI_DEFAULT_VOICE or router_v.get("voice", "ash")

logger.info(
    "Default session TTS -> provider=%s, model=%s, voice=%s",
    DEFAULT_TTS_PROVIDER,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
)

# ----------------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------------

def build_tts(provider: str, model: str, voice: str):
    if provider == "openai":
        # When using OpenAI TTS, both model and voice are required.
        return openai.TTS(model=model, voice=voice)
    elif provider == "cartesia":
        # Cartesia uses voice_id; 'model' is unused here but kept for a uniform signature
        return cartesia.TTS(voice_id=voice)
    else:
        raise ValueError(f"Unsupported TTS provider: {provider}")


def build_deepgram_stt() -> deepgram.STT:
    # Pull a language from YAML if you have one; default to en-US.
    dg_language = _cfg_get(CONFIG, "deepgram.language", "en-US")

    # NOTE: Python plugin takes options directly on the constructor.
    # It also runs its own KeepAlive task under the hood.
    stt = deepgram.STT(
        model=DG_MODEL,                 # e.g., "nova-2-general"
        language=dg_language,           # e.g., "en-US"
        interim_results=True,
        punctuate=True,
        smart_format=True,
        sample_rate=DG_SAMPLE_RATE,     # e.g., 48000
        no_delay=True,
        endpointing_ms=25,
        filler_words=True,
        numerals=False,
        api_key=DEEPGRAM_API_KEY,
        # energy_filter=True,  # optional: helps with silence handling
    )
    return stt

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

    stt = build_deepgram_stt()

    # LLM (OpenAI)
    llm = openai.LLM(model=OPENAI_LLM_MODEL)

    # Default session TTS
    session_tts = build_tts(
        provider=DEFAULT_TTS_PROVIDER,
        model=DEFAULT_TTS_MODEL,
        voice=DEFAULT_TTS_VOICE,
    )

    session = AgentSession[UserData](
        userdata=userdata,
        stt=stt,
        llm=llm,
        tts=session_tts,
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
