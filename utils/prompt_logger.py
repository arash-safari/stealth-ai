# utils/prompt_logger.py
from __future__ import annotations
import sqlite3, json, datetime, os, threading
from typing import Optional, Any, Dict

ISO = "%Y-%m-%dT%H:%M:%S.%fZ"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  ended_at   TEXT,
  session_id TEXT,
  turn       INTEGER,
  tag        TEXT,
  system_prompt       TEXT,
  instructions_prompt TEXT,
  user_input          TEXT,
  response_type       TEXT,   -- "tool_call" | "assistant_text" | "other"
  response_text       TEXT,
  response_json       TEXT
);
"""

class PromptLogger:
  """
  Simple prompt/response tracer:
    - begin_trace(...) before session.generate_reply(...)
    - end_trace(...)   inside your function_tool handlers (report_intent/next_action/provide_slot)
  """
  def __init__(self, db_path: str = "traces.sqlite3", echo: bool = True):
    self._db_path = db_path
    self._echo = echo
    self._lock = threading.Lock()
    self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
    self._conn.execute(_SCHEMA)
    self._conn.commit()

  def _now(self) -> str:
    return datetime.datetime.utcnow().strftime(ISO)

  def begin_trace(
      self,
      *,
      session_id: str,
      turn: int,
      tag: str,
      system_prompt: str,
      instructions: str | None,
      user_input: str | None,
  ) -> int:
    created = self._now()
    with self._lock:
      cur = self._conn.cursor()
      cur.execute(
        "INSERT INTO llm_traces (created_at, session_id, turn, tag, system_prompt, instructions_prompt, user_input) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (created, session_id, turn, tag, system_prompt or "", instructions or "", user_input or ""),
      )
      trace_id = cur.lastrowid
      self._conn.commit()

    if self._echo:
      sep = "=" * 60
      print(f"\n{sep}\n[LLM PROMPT #{turn} | {tag}] (trace_id={trace_id})")
      if system_prompt:
        print("\n--- SYSTEM PROMPT ---")
        print(system_prompt)
      if instructions:
        print("\n--- INSTRUCTIONS / TASK PROMPT ---")
        print(instructions)
      if user_input:
        print("\n--- USER INPUT ---")
        print(user_input)
      print(sep)
    return trace_id

  def end_trace(
      self,
      trace_id: int | None,
      *,
      response_type: str,
      response_text: Optional[str] = None,
      response_json: Optional[Dict[str, Any]] = None,
  ) -> None:
    if not trace_id:
      return
    ended = self._now()
    rj = json.dumps(response_json, ensure_ascii=False) if response_json else None
    with self._lock:
      self._conn.execute(
        "UPDATE llm_traces SET ended_at=?, response_type=?, response_text=?, response_json=? WHERE id=?",
        (ended, response_type, response_text or "", rj, trace_id),
      )
      self._conn.commit()

    if self._echo:
      print("\n--- LLM RESPONSE ---")
      if response_type == "tool_call":
        print(f"[tool_call] {response_json}")
      elif response_type == "assistant_text":
        print(response_text or "")
      else:
        print(response_text or response_json or "")
      print("=" * 60)