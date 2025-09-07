## agents/cancel.py`

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from tools.tools_schedule import read_meeting, cancel_meeting

class Cancel(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a cancellation agent. Confirm identity and appointment, then cancel upon user confirmation."
            ),
            tools=[read_meeting, cancel_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )
