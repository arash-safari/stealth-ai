# common/base_agent.py
import logging
from typing import Literal, Optional, List, Callable, Any
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext

logger = logging.getLogger("plumber-contact-center")

def _resolved_tool_name(fn) -> str:
    target = getattr(fn, "__wrapped__", fn)
    return getattr(target, "__name__", str(target))

class BaseAgent(Agent):
    def __init__(self, *args, tools=None, **kwargs) -> None:
        tool_list: List[Callable[..., Any]] = list(tools or [])

        # Remove any preexisting names that we provide universally
        tool_list = [t for t in tool_list if _resolved_tool_name(t) not in {"handoff", "route", "end_call"}]

        # -------- ROUTE (universal) --------
        async def _route_impl(context: RunContext,
                              target: Literal["router","booking","cancel","reschedule","status","pricing"]="router",
                              reason: Optional[str] = None) -> str:
            normalized = (target or "router").strip().lower()
            alias = {"book":"booking","resched":"reschedule","price":"pricing","status_check":"status"}
            name = alias.get(normalized, normalized)

            next_agent, msg = await self._transfer_to_agent(name, context)
            if next_agent is None:
                return msg
            try:
                context.userdata.last_handoff = {"from": self.__class__.__name__, "to": name, "reason": reason}
            except Exception:
                pass
            return f"{msg} Reason noted: {reason}" if reason else msg

        @function_tool(name="route")
        async def route_tool(context: RunContext,
                             target: Literal["router","booking","cancel","reschedule","status","pricing"]="router",
                             reason: Optional[str] = None) -> str:
            return await _route_impl(context, target=target, reason=reason)

        tool_list.append(route_tool)

        # -------- END CALL (universal) --------
        async def _end_call_impl(context: RunContext,
                                 message: Optional[str] = None,
                                 scrub: bool = True) -> str:
            # Local imports to avoid cross-import headaches
            from common.common_tools import hangup_call, scrub_all_histories
            from common.models import scrub_user_data

            msg = message or "Thanks for calling Ali Plumber Company. Goodbye!"
            try:
                handle = await context.session.say(msg, allow_interruptions=False)
                if handle:
                    await handle.wait_for_playout()
            except Exception:
                # Non-fatal; continue cleanup & hangup
                pass

            if scrub:
                try:
                    scrub_user_data(context.userdata)
                except Exception:
                    pass
                try:
                    await scrub_all_histories(context)
                except Exception:
                    pass

            result = await hangup_call()
            logger.info("end_call(): hangup result=%s; userdata & histories scrubbed=%s", result, scrub)
            return f"Call ended ({result})."

        @function_tool(name="end_call")
        async def end_call_tool(context: RunContext,
                                message: Optional[str] = None,
                                scrub: bool = True) -> str:
            return await _end_call_impl(context, message=message, scrub=scrub)

        tool_list.append(end_call_tool)

        # Final de-dup (belt & suspenders)
        deduped, seen = [], set()
        for t in tool_list:
            name = _resolved_tool_name(t)
            if name in seen:
                continue
            seen.add(name)
            deduped.append(t)

        try:
            logger.info("Tools for %s: %s", self.__class__.__name__,
                        [_resolved_tool_name(t) for t in deduped])
        except Exception:
            pass

        super().__init__(*args, tools=deduped, **kwargs)

    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering: {agent_name}")

        userdata = self.session.userdata
        chat_ctx = self.chat_ctx.copy()

        if isinstance(userdata.prev_agent, Agent):
            truncated = userdata.prev_agent.chat_ctx.copy(
                exclude_instructions=True, exclude_function_call=False
            ).truncate(max_items=6)
            existing_ids = {item.id for item in chat_ctx.items}
            chat_ctx.items.extend([it for it in truncated.items if it.id not in existing_ids])

        chat_ctx.add_message(
            role="system",
            content=f"You are the {agent_name}.\nCurrent user data (YAML):\n{userdata.summarize()}\n",
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(tool_choice="none")

    async def _transfer_to_agent(self, name: str, context: RunContext):
        userdata = context.userdata
        current_agent = context.session.current_agent
        next_agent = userdata.agents.get(name)
        if not next_agent:
            if "router" in userdata.agents:
                next_agent = userdata.agents["router"]
                name = "router"
            else:
                return None, f"Cannot transfer: target '{name}' not found."
        userdata.prev_agent = current_agent
        return next_agent, f"Transferring to {name}."
