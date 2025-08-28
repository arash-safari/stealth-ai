## `agents/status.py`

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from common.base_agent import BaseAgent

class Status(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a status agent. If the appointment is today, give an ETA within the chosen window. Otherwise, remind the scheduled date/window."
            ),
            tools=[],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="verse"),
        )

    @function_tool()
    async def check_status(self, context) -> str:
        u = context.userdata
        if not u.appointment_id or not u.appointment_date or not u.appointment_window:
            return "No appointment found."
        today = __import__("datetime").datetime.utcnow().date().isoformat()
        if u.appointment_status == "canceled":
            return f"Appointment {u.appointment_id} is canceled."
        if u.appointment_date == today:
            return (
                f"Technician scheduled today between {u.appointment_window}. "
                f"Current status: en route window. ETA within 30–90 minutes depending on traffic."
            )
        return f"Appointment {u.appointment_id} is scheduled on {u.appointment_date} at {u.appointment_window}."
