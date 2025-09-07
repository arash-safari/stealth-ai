# agents/status.py
import yaml
from datetime import datetime, timezone
from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from tools.tools_schedule import read_meeting
from common.utils import _dt_utc  # <-- add this import

class Status(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a status agent. Always look up the appointment via the scheduler service using the caller's appointment number. "
                "If the appointment is today, give an ETA within the chosen window. Otherwise, remind the scheduled date/window."
            ),
            tools=[read_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="verse"),
        )

    @function_tool()
    async def check_status(self, context: RunContext, ref: str) -> str:
        """Check status by public appointment number (preferred) or UUID string."""
        # Fetch fresh data from the schedule service (no reliance on context.userdata)
        try:
            yaml_str = await read_meeting(context, appointment_no=ref)
        except Exception:
            return "Appointment not found."

        try:
            data = yaml.safe_load(yaml_str) or {}
        except Exception:
            return "Could not parse appointment details."

        appt_id = data.get("id")
        appt_no = data.get("appointment_no")
        status = str(data.get("status") or "").lower()
        start_raw = data.get("start")
        end_raw = data.get("end")
        label = f"#{appt_no}" if appt_no is not None else (appt_id or "(unknown)")

        # Parse window robustly (strings with 'Z', or datetime objects)
        try:
            sdt_utc = _dt_utc(start_raw)
            edt_utc = _dt_utc(end_raw)
            if not sdt_utc or not edt_utc:
                raise ValueError("missing time")
        except Exception:
            return f"Appointment {label}: time info unavailable."

        appt_date = sdt_utc.date().isoformat()
        window = f"{sdt_utc.strftime('%H:%M')}-{edt_utc.strftime('%H:%M')}"
        today = datetime.now(timezone.utc).date().isoformat()

        if "canceled" in status:
            return f"Appointment {label} is canceled."
        if appt_date == today:
            return (
                f"Technician scheduled today between {window}. "
                f"Current status: en route window. ETA within 30–90 minutes depending on traffic."
            )
        return f"Appointment {label} is scheduled on {appt_date} at {window}."
