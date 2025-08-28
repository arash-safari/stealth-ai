## agents/reschedule.py`

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from tools.tools_schedule import read_meeting, get_available_times, update_meeting

class Reschedule(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a rescheduling agent. Verify appointment exists, offer new windows (reuse booking windows), then update and confirm."
            ),
            tools=[read_meeting, get_available_times, update_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )
        from agents.booking import Booking
        self._booking_helper = Booking()

    @function_tool()
    async def offer_new_windows(self, context: RunContext, preferred_date: str | None = None) -> str:
        u = context.userdata
        if not u.appointment_id or u.appointment_status in {"canceled", None}:
            return "No active appointment found to reschedule."
        wins = self._booking_helper._generate_windows(preferred_date)
        import yaml
        return yaml.dump({"new_windows": wins[:6]}, sort_keys=False)

    @function_tool()
    async def apply_new_window(self, context: RunContext, date: str, window: str) -> str:
        u = context.userdata
        if not u.appointment_id:
            return "No appointment found."
        u.appointment_date = date
        u.appointment_window = window
        u.appointment_status = "scheduled"
        return f"Rescheduled to {date} {window}"

    @function_tool()
    async def confirm_reschedule(self, context: RunContext):
        u = context.userdata
        if not u.appointment_id or not u.appointment_date or not u.appointment_window:
            return "Missing appointment details."
        return await self._transfer_to_agent("router", context)
