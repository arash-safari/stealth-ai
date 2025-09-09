# common/base_agent.py
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, List, Literal, Optional

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext

logger = logging.getLogger("plumber-contact-center")


def _resolved_tool_name(fn: Any) -> str:
    """Get the public name for a tool function (unwrap if decorated)."""
    target = getattr(fn, "__wrapped__", fn)
    return getattr(target, "__name__", str(target))

class BaseAgent(Agent):
    """
    Base class for all agents.
    - Adds universal tools:
        * route(target=...)  -> transfer to router/booking/reschedule/...
        * end_call(message?) -> speak goodbye, stop recorder, scrub, hangup
    - Avoids duplicate tool names across agents
    - On enter: pulls a small rolling window of prior agent chat for continuity
    """

    def __init__(self, *args, tools: List[Callable[..., Any]] | None = None, **kwargs) -> None:
        # Start from agent-provided tools (if any)
        tool_list: List[Callable[..., Any]] = list(tools or [])

        # Remove any names that we are going to provide universally to avoid conflicts
        builtin_names = {"route", "end_call"}
        tool_list = [t for t in tool_list if _resolved_tool_name(t) not in builtin_names]

        # -----------------------------
        # ROUTE (universal)
        # -----------------------------
        async def _route_impl(
            context: RunContext,
            target: Literal["router", "booking", "cancel", "reschedule", "status", "pricing", "billing", "operator"] = "router",
            reason: Optional[str] = None,
        ):
            normalized = (target or "router").strip().lower()
            alias = {
                "book": "booking",
                "resched": "reschedule",
                "price": "pricing",
                "status_check": "status",
                "ops": "operator",
            }
            name = alias.get(normalized, normalized)

            next_agent, msg = await self._transfer_to_agent(name, context)
            if next_agent is None:
                # Nothing to hand off to; let the LLM speak the explanation
                return msg

            # Minimal tracking for audit/analytics
            try:
                context.userdata.last_handoff = {
                    "from": self.__class__.__name__,
                    "to": name,
                    "reason": reason,
                }
            except Exception:
                pass

            # IMPORTANT: return the tuple so the framework actually swaps agents
            spoken = f"{msg} Reason: {reason}" if reason else msg
            return next_agent, spoken

        @function_tool(name="route")
        async def route_tool(
            context: RunContext,
            target: Literal["router", "booking", "cancel", "reschedule", "status", "pricing", "billing", "operator"] = "router",
            reason: Optional[str] = None,
        ):
            return await _route_impl(context, target=target, reason=reason)

        tool_list.append(route_tool)

        # -----------------------------
        # END CALL (universal)
        # -----------------------------
        async def _end_call_impl(
            context: RunContext,
            message: Optional[str] = None,
            scrub: bool = True,
        ) -> str:
            """
            Speak a goodbye (uninterruptible), stop recorder (uploads transcript JSONL, stops egress),
            scrub context, then hang up the call.
            """
            # Local imports to avoid circular deps
            from common.common_tools import hangup_call, scrub_all_histories
            from common.models import scrub_user_data
            from db.session import Session
            from db.models import Call, utcnow

            # 1) Say goodbye
            text = message or "Thanks for calling Ali Plumber Company. Goodbye!"
            try:
                handle = await context.session.say(text, allow_interruptions=False)
                if handle:
                    await handle.wait_for_playout()
            except Exception:
                # Non-fatal: continue teardown
                pass

            # 2) Stop recorder first so audio includes the goodbye
            rec = getattr(context.session, "_call_recorder", None)
            if rec:
                try:
                    await rec.shutdown()  # idempotent
                except Exception as e:
                    logger.warning("CallRecorder shutdown failed: %s", e)

            # 3) Fallback: ensure Call.ended_at is set if no recorder
            try:
                call_id = getattr(context.userdata, "call_id", None)
                if call_id and not rec:
                    try:
                        async with Session() as db:
                            c = await db.get(Call, uuid.UUID(call_id))
                            if c and not c.ended_at:
                                c.ended_at = utcnow()
                                await db.commit()
                    except Exception:
                        logger.debug("Could not set ended_at fallback", exc_info=True)
            except Exception:
                pass

            # 4) Scrub runtime memory/history if requested
            if scrub:
                try:
                    scrub_user_data(context.userdata)
                except Exception:
                    pass
                try:
                    await scrub_all_histories(context)
                except Exception:
                    pass

            # 5) Hang up
            result = await hangup_call()
            logger.info("end_call(): hangup=%s, scrubbed=%s", result, scrub)
            return f"Call ended ({result})."

        @function_tool(name="end_call")
        async def end_call_tool(
            context: RunContext,
            message: Optional[str] = None,
            scrub: bool = True,
        ) -> str:
            return await _end_call_impl(context, message=message, scrub=scrub)

        tool_list.append(end_call_tool)

        # -----------------------------
        # Deduplicate any remaining tool names (belt & suspenders)
        # -----------------------------
        deduped: List[Callable[..., Any]] = []
        seen: set[str] = set()
        for t in tool_list:
            nm = _resolved_tool_name(t)
            if nm in seen:
                continue
            seen.add(nm)
            deduped.append(t)

        try:
            logger.info(
                "Tools for %s: %s",
                self.__class__.__name__,
                [_resolved_tool_name(t) for t in deduped],
            )
        except Exception:
            pass

        super().__init__(*args, tools=deduped, **kwargs)

    # -----------------------------
    # Agent lifecycle hook
    # -----------------------------
    async def on_enter(self) -> None:
        """
        When an agent becomes active:
        - Bring a tiny window of prior agent chat (no duplicate items) for continuity
        - Add a system message with current userdata summary (YAML)
        - Kick off a reply with tool_choice="none" to let the LLM speak immediately
        """
        agent_name = self.__class__.__name__
        logger.info("Entering: %s", agent_name)

        userdata = self.session.userdata
        chat_ctx = self.chat_ctx.copy()

        # Merge a few prior items from the previous agent (if any)
        if hasattr(userdata, "prev_agent") and isinstance(userdata.prev_agent, Agent):
            prev_ctx = userdata.prev_agent.chat_ctx.copy(
                exclude_instructions=True,
                exclude_function_call=False,
            ).truncate(max_items=6)
            existing_ids = {item.id for item in chat_ctx.items}
            for it in prev_ctx.items:
                if it.id not in existing_ids:
                    chat_ctx.items.append(it)

        # Add a compact system message with current user data
        try:
            summary = userdata.summarize()
        except Exception:
            summary = "(unavailable)"
        chat_ctx.add_message(
            role="system",
            content=f"You are the {agent_name}.\nCurrent user data (YAML):\n{summary}\n",
        )

        await self.update_chat_ctx(chat_ctx)

        # Let the agent speak on entry without forcing a tool call
        self.session.generate_reply(tool_choice="none")

    # -----------------------------
    # Agent handoff helper
    # -----------------------------
    async def _transfer_to_agent(self, name: str, context: RunContext):
        """
        Resolve the target agent from userdata.agents and hand the session over.
        Return (next_agent, message) – the framework handles the actual swap.
        """
        userdata = context.userdata
        current_agent = context.session.current_agent

        # Resolve
        next_agent = userdata.agents.get(name)
        if not next_agent:
            # Best effort fall back to router if available
            if "router" in userdata.agents:
                name = "router"
                next_agent = userdata.agents["router"]
            else:
                return None, f"Cannot transfer: target '{name}' not found."

        # Record the previous agent for history merge
        userdata.prev_agent = current_agent
        return next_agent, f"Transferring to {name}."
