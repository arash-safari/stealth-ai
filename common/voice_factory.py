# -----------------------------------------------------------------------------
# common/voice_factory.py — provider-agnostic TTS factory
# -----------------------------------------------------------------------------
# Usage:
#   from common.voice_factory import build_tts_for
#   tts = build_tts_for("booking", voices)
#   # voices can include per-agent or a "default" entry.
#
# Schema examples:
# voices = {
    # "router": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "alloy"},
    # "booking": {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "verse"},
# },
#   "booking": {"provider": "cartesia", "voice_id": "156fb8d2-...", "speed": 1.0},
#   "router":  {"provider": "openai", "model": "gpt-4o-realtime-preview", "voice": "ash"},
# }

from typing import Any, Dict, Optional
from livekit.plugins import openai as _openai, cartesia as _cartesia
import logging

_vlog = logging.getLogger("plumber-contact-center")


def _pick(mapping: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return {k: mapping[k] for k in keys if k in mapping and mapping[k] is not None}


def build_tts_for(agent_name: str, voices: Optional[Dict[str, Dict[str, Any]]]):
    """Return a TTS instance for the given agent using a provider-agnostic voices dict.

    Supported providers: "openai", "cartesia".
    Fallback order: voices[agent_name] -> voices["default"] -> OpenAI(gpt-4o-mini-tts, voice="ash").
    Recognized keys:
      - openai: {provider, model, voice}
      - cartesia: {provider, voice_id, speed?, style?}
    Extra keys are ignored.
    """
    spec: Dict[str, Any] = {}
    if isinstance(voices, dict):
        spec = voices.get(agent_name) or voices.get("default") or {}

    provider = (spec.get("provider") or "openai").lower()

    if provider == "cartesia":
        # Cartesia TTS usually needs a voice ID; optional speed/style
        kwargs = _pick(spec, "voice_id", "speed", "style")
        if "voice_id" not in kwargs:
            _vlog.warning(
                "voices[%s]: cartesia provider without voice_id; falling back to OpenAI",
                agent_name,
            )
        else:
            _vlog.info("TTS[%s]: cartesia %s", agent_name, kwargs.get("voice_id"))
            return _cartesia.TTS(**kwargs)
        # fall through to openai fallback

    # OpenAI default path
    model = spec.get("model", "gpt-4o-mini-tts")
    voice = spec.get("voice", "ash")
    _vlog.info("TTS[%s]: openai model=%s voice=%s", agent_name, model, voice)
    return _openai.TTS(model=model, voice=voice)


