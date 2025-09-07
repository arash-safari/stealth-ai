## agents/router.py`

import logging
from livekit.plugins import openai
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.base_agent import BaseAgent
from common.common_tools import hangup_call, scrub_all_histories
from common.models import scrub_user_data

logger = logging.getLogger("plumber-contact-center")

class Router(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a friendly plumbing company receptionist.\n"
                "For your very first message, greet the caller and say: "
                "'Welcome to Ali Plumber Company! How can I help you today?'\n"
                "Triage the caller and route them: booking, reschedule, cancel, "
                "status/ETA, pricing/estimate, billing, or operator.\n"
                "Ask minimal questions to decide, then use a tool to transfer."
                "If the caller says they're done (e.g., 'no, that's all', 'thank you, bye'), "
                "say a brief goodbye and CALL the end_call tool to hang up."
            ),
            llm=openai.LLM(parallel_tool_calls=False),
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )

    @function_tool()
    async def to_booking(self, context: RunContext):
        from common.common_tools import to_router as _to_router
        return await context.session.current_agent._transfer_to_agent("booking", context)

    @function_tool()
    async def to_reschedule(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("reschedule", context)

    @function_tool()
    async def to_cancel(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("cancel", context)

    @function_tool()
    async def to_status(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("status", context)

    @function_tool()
    async def to_pricing(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("pricing", context)

    @function_tool()
    async def to_billing(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("billing", context)

    @function_tool()
    async def to_operator(self, context: RunContext):
        return await context.session.current_agent._transfer_to_agent("operator", context)

    @function_tool()
    async def end_call(self, context: RunContext) -> str:
        handle = await context.session.say(
            "Thanks for calling Ali Plumber Company. Goodbye!",
            allow_interruptions=False,
        )
        if handle:
            await handle.wait_for_playout()
        scrub_user_data(context.userdata)
        await scrub_all_histories(context)
        result = await hangup_call()
        logger.info("end_call(): hangup result=%s; userdata & histories scrubbed", result)
        return f"Call ended ({result})."
