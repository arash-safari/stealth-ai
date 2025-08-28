## common/common_tools.py`

import logging
import asyncio
from livekit import api
from livekit.agents import get_job_context
from livekit.api.twirp_client import TwirpError, TwirpErrorCode
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from common.models import scrub_user_data

logger = logging.getLogger("plumber-contact-center")


async def hangup_call() -> str:
    ctx = get_job_context()
    if ctx is None:
        logger.warning("hangup_call(): no JobContext; nothing to do")
        return "no_job_ctx"

    room_name = getattr(ctx.room, "name", None)
    logger.info("hangup_call(): room=%s", room_name)

    try:
        if room_name:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info("hangup_call(): room deleted")
        else:
            logger.info("hangup_call(): no room name; skipping delete_room")
    except TwirpError as e:
        if e.code == TwirpErrorCode.NOT_FOUND or getattr(e, "status", None) == 404:
            logger.info("hangup_call(): room already gone (404) — treating as success")
        else:
            logger.warning("hangup_call(): delete_room failed: %s", e)

    ctx.shutdown(reason="hangup")  # sync
    return "shutdown"


async def scrub_all_histories(context: RunContext) -> None:
    u = context.userdata
    tasks = []
    for agent in u.agents.values():
        ctx = agent.chat_ctx.copy()
        ctx.items = [itm for itm in ctx.items if getattr(itm, "role", "") == "system"]
        tasks.append(agent.update_chat_ctx(ctx))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# Common function_tools used by multiple agents
@function_tool()
async def to_router(context: RunContext):
    curr_agent = context.session.current_agent
    return await curr_agent._transfer_to_agent("router", context)

@function_tool()
async def update_name(context: RunContext, name: str) -> str:
    context.userdata.customer_name = name
    return f"Name updated to: {name}"

@function_tool()
async def update_phone(context: RunContext, phone: str) -> str:
    context.userdata.customer_phone = phone
    return f"Phone updated to: {phone}"

@function_tool()
async def update_email(context: RunContext, email: str) -> str:
    context.userdata.customer_email = email
    return f"Email updated to: {email}"

@function_tool()
async def update_address(
    context: RunContext,
    street: str,
    city: str,
    state: str,
    postal_code: str,
    unit: str | None = None,
) -> str:
    u = context.userdata
    u.street = street
    u.city = city
    u.state = state
    u.postal_code = postal_code
    u.unit = unit
    return f"Address updated to: {u.address_str()}"

@function_tool()
async def update_problem(
    context: RunContext,
    description: str,
    urgency: str | None = "normal",
) -> str:
    u = context.userdata
    u.problem_description = description
    u.urgency = urgency
    return f"Problem updated. Urgency={urgency}. Description={description}"
