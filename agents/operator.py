## agents/operator.py

from livekit.plugins import openai
from common.base_agent import BaseAgent

class Operator(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are an operator for miscellaneous requests: warranty, invoices, landlord approvals, etc. "
                "Gather details and route back to Router if caller changes tasks."
            ),
            tools=[],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )
