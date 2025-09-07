# tests/e2e/_booking_llm_harness.py
import json, os, re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple
from datetime import datetime, timedelta, timezone

from openai import OpenAI


@dataclass
class ToolSpec:
    name: str
    schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class CallRecord:
    name: str
    args: Dict[str, Any]
    result: Dict[str, Any]


class ChatHarness:
    """
    Minimal E2E runner for 'system instructions + tools'.
    - Runs a real model (temperature=0) with function-calling
    - Intercepts tool calls and routes them to Python handlers
    - Collects a log of called tools for assertions
    """
    def __init__(self, system_instructions: str, tools: List[ToolSpec], model: str | None = None):
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = OpenAI()
        self.model = model or os.getenv("LLM_TEST_MODEL", "gpt-4o-mini")
        self.messages: List[Dict[str, str]] = [{"role": "system", "content": system_instructions}]
        self.tools = tools
        self.calls: List[CallRecord] = []

    @property
    def api_tools(self) -> List[Dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.schema.get("description", ""), "parameters": t.schema["parameters"]}}
            for t in self.tools
        ]

    def say_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def _find_tool(self, name: str) -> ToolSpec:
        for t in self.tools:
            if t.name == name:
                return t
        raise KeyError(f"Tool {name} not registered")

    def _respond_once(self) -> Tuple[str, List[Dict[str, Any]]]:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            top_p=0,
            messages=self.messages,
            tools=self.api_tools,
            tool_choice="auto",
            max_tokens=256,
        )
        choice = resp.choices[0].message
        tool_calls = choice.tool_calls or []
        assistant_text = choice.content or ""

        # Build the assistant message WITHOUT an empty tool_calls array
        assistant_msg = {"role": "assistant", "content": assistant_text}
        if tool_calls:  # <-- only attach when non-empty
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        self.messages.append(assistant_msg)
        return assistant_text, tool_calls

    def _handle_tool_calls(self, tool_calls: List[Any]) -> None:
        for tc in tool_calls:
            name = tc.function.name
            args_json = tc.function.arguments or "{}"
            try:
                args = json.loads(args_json)
            except Exception:
                args = {}
            spec = self._find_tool(name)
            result = spec.handler(args)  # sync handler returning JSON-serializable dict
            self.calls.append(CallRecord(name=name, args=args, result=result))
            self.messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": json.dumps(result),
            })

    def turn(self) -> str:
        """
        Run a full assistant turn:
        Keep responding to tool calls until the model produces a plain text reply.
        """
        final_text = ""
        # safety to avoid infinite loops if a prompt goes sideways
        for _ in range(8):
            assistant_text, tool_calls = self._respond_once()
            if assistant_text:
                final_text = assistant_text
            if not tool_calls:
                return (final_text or "").strip()
            # satisfy ALL tool calls before letting the model speak again
            self._handle_tool_calls(tool_calls)

        raise RuntimeError("Harness: exceeded tool-call loop limit (possible infinite tool chain).")


# ---------- Booking tool stubs (deterministic) ----------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

def _tomorrow_14_16():
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # force tomorrow 14:00-16:00 UTC
    d = (t0 + timedelta(days=1)).date()
    s = datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc)
    return s, s + timedelta(hours=2)

def booking_tool_specs() -> List[ToolSpec]:
    """
    Tool shapes mirror your Booking instructions:
    - get_available_times(skill, duration_min, priority, date_from, date_to, limit, respect_google_busy)
    - get_nearest_available_time(skill, duration_min, priority, after)
    - get_today()
    - create_appointment(tech_id, start, end, priority, request_text)
    """
    # 1) get_available_times
    def handle_get_available_times(args: Dict[str, Any]) -> Dict[str, Any]:
        # Return three deterministic slots tomorrow
        base, _ = _tomorrow_14_16()
        slots = []
        for i in range(3):
            s = base + timedelta(hours=2*i)
            e = s + timedelta(hours=2)
            slots.append({"tech_id": f"T{i+1}", "start": _iso(s), "end": _iso(e), "source": "stub"})
        return {"slots": slots}

    get_available_times = ToolSpec(
        name="get_available_times",
        schema={
            "description": "Find available booking windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "duration_min": {"type": "integer"},
                    "priority": {"type": "string"},
                    "date_from": {"type": ["string", "null"]},
                    "date_to": {"type": ["string", "null"]},
                    "limit": {"type": "integer"},
                    "respect_google_busy": {"type": "boolean"},
                },
                "required": ["skill", "duration_min", "priority"],
            },
        },
        handler=handle_get_available_times,
    )

    # 2) get_nearest_available_time
    def handle_get_nearest(args: Dict[str, Any]) -> Dict[str, Any]:
        s, e = _tomorrow_14_16()
        return {"nearest_slot": {"tech_id": "T1", "start": _iso(s), "end": _iso(e), "source": "stub"}}

    get_nearest_available_time = ToolSpec(
        name="get_nearest_available_time",
        schema={
            "description": "Return the earliest/nearest window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "duration_min": {"type": "integer"},
                    "priority": {"type": "string"},
                    "after": {"type": ["string", "null"]},
                    "respect_google_busy": {"type": "boolean"},
                },
                "required": ["skill", "duration_min", "priority"],
            },
        },
        handler=handle_get_nearest,
    )

    # 3) get_today
    def handle_today(_args: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {"today": {"date": now.date().isoformat(), "iso": _iso(now), "weekday": now.strftime("%A"), "tz": "UTC"}}

    get_today = ToolSpec(
        name="get_today",
        schema={
            "description": "Provide today's date/time.",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handle_today,
    )

    # 4) create_appointment
    def handle_create(args: Dict[str, Any]) -> Dict[str, Any]:
        # Ignore inputs; return a stable number
        return {
            "message": "Appointment created",
            "appointment": {"appointment_no": "APT-12345", "start": args.get("start"), "end": args.get("end")},
        }

    create_appointment = ToolSpec(
        name="create_appointment",
        schema={
            "description": "Create appointment for a selected slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tech_id": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "priority": {"type": "string"},
                    "request_text": {"type": "string"},
                },
                "required": ["tech_id", "start", "end", "priority"],
            },
        },
        handler=handle_create,
    )

    return [get_available_times, get_nearest_available_time, get_today, create_appointment]


# ---------- tiny text validators ----------

def sentence_count(text: str) -> int:
    # simple but good enough
    text = re.sub(r"\.\.+", ".", text)
    return len(re.findall(r"[.!?](?:\s|$)", text))

def ends_with_single_question(text: str) -> bool:
    return text.strip().endswith("?") and text.count("?") == 1

def has_numbered_list(text: str) -> bool:
    return bool(re.search(r"\b1\)\s.*\d{2}:\d{2}.*", text))

def asks_for(term: str, text: str) -> bool:
    return term.lower() in text.lower()
