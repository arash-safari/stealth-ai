from __future__ import annotations
import logging
import os
import json
from typing import Any

__all__ = ["get_logger", "LLMLogger", "truncate"]


def truncate(s: Any, limit: int = 4000) -> str:
    """Safely truncate long values for logs (keeps unicode; appends ellipsis)."""
    if not isinstance(s, str):
        try:
            s = json.dumps(s, ensure_ascii=False)
        except Exception:
            s = str(s)
    return s if len(s) <= limit else (s[:limit] + " …[truncated]")


def get_logger(name: str = "livekit.agents.intent", logfile: str = "intent-agent.log") -> logging.Logger:
    """Create or fetch a configured logger with console + file handlers.
    Respects LOGLEVEL env. Idempotent (won't duplicate handlers).
    """
    level = getattr(logging, os.getenv("LOGLEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if called multiple times
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(ch)

    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(fh)

    return logger


class LLMLogger:
    """High-signal logging for planner/router interactions."""

    def __init__(self, logger: logging.Logger, session_id: str):
        self._log = logger
        self._sid = session_id

    def plan_start(self, planner: str, instructions: str, user_msg: str = "Output EXACTLY ONE next_action tool call."):
        self._log.info("PLANNER START | session=%s planner=%s", self._sid, planner)
        self._log.debug(
            "PLANNER INPUT | session=%s planner=%s\n--- INSTRUCTIONS ---\n%s\n--- USER MSG ---\n%s",
            self._sid,
            planner,
            truncate(instructions),
            truncate(user_msg),
        )

    def plan_response_tool(self, planner: str, tool_name: str, payload: dict):
        self._log.info("PLANNER RESP  | session=%s planner=%s tool=%s", self._sid, planner, tool_name)
        self._log.debug("PLANNER RESP BODY | session=%s\n%s", self._sid, truncate(json.dumps(payload, ensure_ascii=False, indent=2)))

    def router_result(self, user_text: str, result: dict):
        conf = float(result.get("confidence", 0.0) or 0.0)
        self._log.info("ROUTER RESULT | session=%s intent=%s conf=%.2f candidates=%s",
                       self._sid, result.get("intent_code"), conf, result.get("intent_candidates"))
        self._log.debug("ROUTER INPUT  | session=%s user=%s", self._sid, truncate(user_text))
        try:
            raw = json.dumps(result, ensure_ascii=False)
        except Exception:
            raw = str(result)
        self._log.debug("ROUTER RAW    | session=%s %s", self._sid, truncate(raw))
