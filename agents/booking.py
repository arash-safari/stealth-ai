## agents/booking.py`

import uuid
from datetime import datetime, time, timedelta
from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from common.common_tools import update_name, update_phone, update_email, update_address, update_problem, to_router
from tools.tools_schedule import (
    create_appointment,
    get_today,
    get_nearest_available_time,
    get_available_times,
)

class Booking(BaseAgent):

    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "Booking agent. Be concise and ask EXACTLY one question per turn.\n"
                "Start with a brief hello, then ask for the next missing field only. After each answer, ask the next.\n"
                "Collect (in order): name, phone (email optional), full service address "
                "(street, unit (optional), city, state, postal), problem description, "
                "urgency (normal/urgent/emergency), preferred date.\n"
                "When address+problem+urgency+preferred date are known, call get_available_times "
                "(skill='plumbing', duration_min=120, priority: emergency→P1, urgent→P2, else P3) and show the 2–4 earliest windows "
                "as a numbered list like: '1) Tue 14:00–16:00'. Ask: 'Which number works?'\n"
                "If the user asks for the earliest time or gives no preferred date, you may call get_nearest_available_time.\n"
                "Use get_today to interpret 'today' or 'tomorrow'. If date_from/date_to are unknown, pass None.\n"
                "After the customer picks a window, confirm briefly (date, window, address).\n"
                "WHEN THE CUSTOMER SAYS 'YES': First reply with one short sentence to the user like "
                "'Got it — confirming your appointment now. Please wait a moment.' and then, in the same turn, "
                "call create_appointment exactly once. ⬅️\n"
                "After create_appointment returns, read back the appointment number and window.\n"
                "If no slots are available, ask a single follow-up: expand the date range or try another day (yes/no). "
                "If the user revises info, update and continue.\n"
                "If the user asks to cancel, reschedule, check status, or get pricing, hand off to the appropriate agent.\n"
                "HARD RULES: Keep each message ≤2 sentences (confirmation may use up to 3), never ask multi-part questions, "
                "do not re-run create_appointment if an appointment is already scheduled."
            ),
            tools=[
                update_name, update_phone, update_email, update_address, update_problem, to_router,
                create_appointment, get_today, get_nearest_available_time, get_available_times
            ],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="ash"),
        )


