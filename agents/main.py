import logging
from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import AgentSession
from livekit.plugins import deepgram, openai, cartesia, silero

from common.models import UserData
from agents.router import Router
from agents.booking import Booking
from agents.reschedule import Reschedule
from agents.cancel import Cancel
from agents.parts import Parts
from agents.status import Status
from agents.pricing import Pricing
from agents.billing import Billing
from agents.operator import Operator

# Optional central logging
from common.logging_config import configure_logging

load_dotenv()
configure_logging()
logger = logging.getLogger("plumber-contact-center")

# Replace with your Cartesia/OpenAI voice IDs if desired
voices = {
    "router": "794f9389-aac1-45b6-b726-9d9369183238",
    "booking": "156fb8d2-335b-4950-9cb3-a2d33befec77",
    "reschedule": "6f84f4b8-58a2-430c-8c79-688dad597532",
    "cancel": "39b376fc-488e-4d0c-8b37-e00b72059fdd",
    "parts": "c5bc2f14-6c18-4e63-9f10-2d8897c5c00c",
    "status": "28b5a8c0-51b2-4a30-a3a5-98a4b2a335c6",
    "pricing": "1e2a3b4c-5d6e-7f80-9a0b-1c2d3e4f5a6b",
    "billing": "f1a2b3c4-d5e6-47f8-9a0b-1c2d3e4f5a6b",
    "operator": "ab12cd34-ef56-7890-ab12-cd34ef567890",
}

async def entrypoint(ctx: JobContext):
    userdata = UserData()

    userdata.agents.update({
        "router": Router(voices=voices),
        "booking": Booking(voices=voices),
        "reschedule": Reschedule(voices=voices),
        "cancel": Cancel(voices=voices),
        "parts": Parts(voices=voices),
        "status": Status(voices=voices),
        "pricing": Pricing(voices=voices),
        "billing": Billing(voices=voices),
        "operator": Operator(voices=voices),
    })

    session = AgentSession[UserData](
        userdata=userdata,
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(model="gpt-4o-mini-tts", voice="ash"),
        vad=silero.VAD.load(),
        max_tool_steps=6,
    )

    await session.start(
        agent=userdata.agents["router"],
        room=ctx.room,
    )

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

