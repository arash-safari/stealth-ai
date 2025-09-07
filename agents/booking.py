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
    # CATALOGUE = {
    #     "FCT-001": {"name": "Kitchen faucet", "price": 120.0},
    #     "FLT-002": {"name": "Toilet fill valve", "price": 35.0},
    #     "WH-40": {"name": "40-gal water heater", "price": 950.0},
    #     "P-PTFE": {"name": "PTFE tape", "price": 4.0},
    # }

    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "Booking agent. Be concise and ask EXACTLY one question per turn.\n"
                "Start with a brief hello, then ask for the next missing field only. After each answer, ask the next.\n"
                "Collect (in order): name, phone (email optional), full service address (street, unit?, city, state, postal), "
                "problem description, urgency (normal/urgent/emergency), preferred date.\n"
                "When address+problem+urgency+preferred date are known, call get_available_times "
                "(skill='plumbing', duration_min=120, priority: emergency→P1, urgent→P2, else P3) and show the 2–4 earliest windows "
                "as a numbered list like: '1) Tue 14:00–16:00'. Ask: 'Which number works?'\n"
                "If the user asks for the earliest time or gives no preferred date, you may call get_nearest_available_time to find the soonest window.\n"
                "Use get_today to obtain today's date/time when interpreting phrases like 'today' or 'tomorrow'.\n"
                "When calling get_available_times, if date_from or date_to are not provided by the user, pass them as None.\n"
                "After selection, confirm in ≤2 short sentences (date, window, address). If customer says yes, call create_appointment "
                "with the chosen tech_id/start/end, priority, and a brief request_text (problem + address). Then read back the appointment number and window.\n"
                "If no slots are available, ask a single follow-up: expand date range or try another day (yes/no). If user revises info, update and continue.\n"
                "No small talk. Keep each message ≤2 sentences. Never ask multi-part questions."
            ),
            tools=[
                update_name, update_phone, update_email, update_address, update_problem, to_router,
                create_appointment, get_today, get_nearest_available_time, get_available_times
            ],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="ash"),
        )


    @function_tool()
    async def confirm_appointment(self, context: RunContext):
        u = context.userdata
        missing = []
        if not u.street or not u.city or not u.state or not u.postal_code:
            missing.append("address")
        if not u.appointment_date or not u.appointment_window:
            missing.append("date/window")
        if not u.customer_name:
            missing.append("name")
        if not u.customer_phone:
            missing.append("phone")
        if missing:
            return f"Missing required info: {', '.join(missing)}. Please provide these first."
        u.appointment_id = str(uuid.uuid4())[:8]
        u.appointment_status = "scheduled"
        return await self._transfer_to_agent("router", context)
