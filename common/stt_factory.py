# common/stt_factory.py
from typing import Any, Dict, Optional
from livekit.plugins import deepgram as _deepgram
import logging

_log = logging.getLogger("plumber-contact-center")

def build_deepgram_stt(cfg: Dict[str, Any], api_key: Optional[str]) -> Optional[_deepgram.STT]:
    """
    Build Deepgram STT from config. Returns None if api_key is missing.
    Uses stable, voice-optimized defaults; everything is overridable via YAML.
    """
    if not api_key:
        _log.warning("DEEPGRAM_API_KEY missing; STT will be disabled.")
        return None

    dgc = (cfg.get("deepgram") or {})
    stt_cfg = (dgc.get("stt") or {})

    kwargs = {
        "model": dgc.get("model", "nova-2-general"),
        "language": dgc.get("language", "en-US"),
        "sample_rate": int(dgc.get("sample_rate", 16000)),  # stable default vs 48k
        # STT behavior:
        "interim_results": bool(stt_cfg.get("interim_results", True)),
        "no_delay": bool(stt_cfg.get("no_delay", False)),
        "endpointing_ms": int(stt_cfg.get("endpointing_ms", 250)),
        "punctuate": bool(stt_cfg.get("punctuate", True)),
        "smart_format": bool(stt_cfg.get("smart_format", True)),
        "filler_words": bool(stt_cfg.get("filler_words", False)),
        "numerals": bool(stt_cfg.get("numerals", True)),
        "api_key": api_key,
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    _log.info(
        "Deepgram config -> model=%s, sample_rate=%s, language=%s, "
        "interim=%s, no_delay=%s, endpointing_ms=%s, punctuate=%s, smart_format=%s, "
        "filler_words=%s, numerals=%s",
        kwargs.get("model"),
        kwargs.get("sample_rate"),
        kwargs.get("language"),
        kwargs.get("interim_results"),
        kwargs.get("no_delay"),
        kwargs.get("endpointing_ms"),
        kwargs.get("punctuate"),
        kwargs.get("smart_format"),
        kwargs.get("filler_words"),
        kwargs.get("numerals"),
    )
    return _deepgram.STT(**kwargs)
