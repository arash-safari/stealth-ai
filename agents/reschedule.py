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


