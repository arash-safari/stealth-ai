# common/config_loader.py
import os
import yaml
import logging
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("plumber-contact-center")

def load_config() -> Dict[str, Any]:
    """Load YAML from CONFIG_PATH or ./config.yaml, with safe defaults."""
    config_path = Path(os.getenv("CONFIG_PATH", "config.yaml"))
    if not config_path.exists():
        _log.warning("Config file not found at %s. Using built-in defaults.", config_path)
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        _log.error("Failed to parse %s: %s. Using built-in defaults.", config_path, e)
        return {}

def cfg_get(d: Dict[str, Any], path: str, default=None):
    """Safely fetch a nested key via dotted path, e.g. cfg_get(cfg, 'openai.llm_model')."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

def mask_key(key: Optional[str]) -> str:
    if not key:
        return "<none>"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"sha256:{digest[:8]}"
