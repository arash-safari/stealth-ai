## common/base_agent.py`
import logging
from livekit.agents.voice import Agent

logger = logging.getLogger("plumber-contact-center")

class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering: {agent_name}")

        userdata = self.session.userdata
        chat_ctx = self.chat_ctx.copy()

        if isinstance(userdata.prev_agent, Agent):
            truncated_chat_ctx = userdata.prev_agent.chat_ctx.copy(
                exclude_instructions=True, exclude_function_call=False
            ).truncate(max_items=6)
            existing_ids = {item.id for item in chat_ctx.items}
            items_copy = [item for item in truncated_chat_ctx.items if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)

        chat_ctx.add_message(
            role="system",
            content=(
                f"You are the {agent_name}.\n"
                f"Current user data (YAML):\n{userdata.summarize()}\n"
            ),
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(tool_choice="none")

    async def _transfer_to_agent(self, name: str, context):
        userdata = context.userdata
        current_agent = context.session.current_agent
        next_agent = userdata.agents[name]
        userdata.prev_agent = current_agent
        return next_agent, f"Transferring to {name}."
