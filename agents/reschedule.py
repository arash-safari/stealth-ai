# agents/reschedule.py

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from tools.tools_schedule import (
    read_meeting,
    get_available_times,
    update_meeting,
    confirm_reschedule as tool_confirm_reschedule,
)
from common.utils import _dt_utc
import yaml


class Reschedule(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a rescheduling agent. Verify appointment exists, offer new windows (reuse booking windows), "
                "then update and confirm."
            ),
            tools=[read_meeting, get_available_times, update_meeting, tool_confirm_reschedule],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )
        from agents.booking import Booking
        self._booking_helper = Booking()

    @function_tool()
    async def confirm_reschedule(
        self,
        context: RunContext,
        start: str,
        end: str,
        appointment_no: str | None = None,
        request_text: str | None = None,
    ) -> str:
        """
        Finalize the reschedule and TELL the user the new appointment number and window.
        Updates userdata so downstream agents are in sync.
        """
        u = context.userdata
        appt = appointment_no or getattr(u, "appointment_id", None)
        if not appt:
            return "No appointment number on file. Please provide your appointment number."

        # Call tool and parse YAML (tool may return str YAML or dict already)
        tool = getattr(tool_confirm_reschedule, "__wrapped__", tool_confirm_reschedule)
        res = await tool(context, appointment_no=str(appt), start=start, end=end, request_text=request_text)
        payload = yaml.safe_load(res) if isinstance(res, str) else (res or {})

        # Pull new number and window; fall back to args if tool omitted times
        new_no = payload.get("appointment_no") or str(appt)
        appt_obj = payload.get("appointment", {}) or {}
        s_iso = appt_obj.get("start") or start
        e_iso = appt_obj.get("end") or end

        # Update userdata (robust to parse errors)
        try:
            s_dt = _dt_utc(s_iso)
            e_dt = _dt_utc(e_iso)
            u.appointment_date = s_dt.date().isoformat()
            u.appointment_window = f"{s_dt.strftime('%H:%M')}-{e_dt.strftime('%H:%M')}"
        except Exception:
            # If conversion fails, use raw inputs to at least fill something sensible
            u.appointment_date = (s_iso or "")[:10] or getattr(u, "appointment_date", None)
            try:
                # try to carve out HH:MM from raw ISO strings
                s_hm = s_iso[11:16]
                e_hm = e_iso[11:16]
                u.appointment_window = f"{s_hm}-{e_hm}"
            except Exception:
                pass

        u.appointment_status = "rescheduled"
        u.appointment_id = new_no

        # Say the number + window (≤2 sentences)
        date_part = u.appointment_date or "(date pending)"
        window_part = u.appointment_window or "(window pending)"
        return f"Done. Your new appointment number is {new_no}. Window: {date_part} {window_part}."
