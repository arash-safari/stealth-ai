import logging
from dataclasses import dataclass, field
from typing import Annotated, Optional
import yaml
from dotenv import load_dotenv
from pydantic import Field
from livekit.api.twirp_client import TwirpError, TwirpErrorCode
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext



from dataclasses import dataclass, field
import yaml
from typing import Optional

@dataclass
class UserData:
    # Contact
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None

    # Service location
    street: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None

    # Job details
    problem_description: Optional[str] = None
    urgency: Optional[str] = None  # "normal" | "urgent" | "emergency"

    # Appointment
    appointment_id: Optional[str] = None
    appointment_date: Optional[str] = None          # YYYY-MM-DD
    appointment_window: Optional[str] = None        # HH:MM-HH:MM
    appointment_status: Optional[str] = None        # scheduled | en_route | complete | canceled

    # Parts cart (customer asks us to bring items)
    cart: list[dict] = field(default_factory=list)  # {sku, name, qty, unit_price}
    cart_total: float = 0.0

    # Pricing / estimate
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None

    # Payments (for deposit or parts prepay)
    card_number: Optional[str] = None
    card_expiry: Optional[str] = None
    card_cvv: Optional[str] = None
    amount_authorized: Optional[float] = None

    # Agent shared
    agents: dict[str, "BaseAgent"] = field(default_factory=dict)
    prev_agent: Optional["BaseAgent"] = None

    def address_str(self) -> str:
        parts = [self.street or "", self.unit or "", self.city or "", self.state or "", self.postal_code or ""]
        return ", ".join([p for p in parts if p]) or "unknown"

    def summarize(self) -> str:
        data = {
            "customer": {
                "name": self.customer_name or "unknown",
                "phone": self.customer_phone or "unknown",
                "email": self.customer_email or "unknown",
            },
            "address": {
                "street": self.street or "unknown",
                "unit": self.unit or None,
                "city": self.city or "unknown",
                "state": self.state or "unknown",
                "postal_code": self.postal_code or "unknown",
            },
            "job": {
                "description": self.problem_description or "unknown",
                "urgency": self.urgency or "normal",
            },
            "appointment": {
                "id": self.appointment_id or None,
                "date": self.appointment_date or None,
                "window": self.appointment_window or None,
                "status": self.appointment_status or None,
            },
            "cart": self.cart or [],
            "cart_total": round(self.cart_total, 2),
            "estimate": {
                "low": self.estimate_low,
                "high": self.estimate_high,
            },
            "payment": {
                "card_number": self.card_number or None,
                "expiry": self.card_expiry or None,
                "cvv": self.card_cvv or None,
                "amount_authorized": self.amount_authorized,
            },
        }
        return yaml.dump(data, sort_keys=False)

logger = logging.getLogger("plumber-contact-center")
logger.setLevel(logging.INFO)

class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering: {agent_name}")

        userdata: UserData = self.session.userdata
        chat_ctx = self.chat_ctx.copy()

        # Carry forward a trimmed recent chat history from previous agent
        if isinstance(userdata.prev_agent, Agent):
            truncated_chat_ctx = userdata.prev_agent.chat_ctx.copy(
                exclude_instructions=True, exclude_function_call=False
            ).truncate(max_items=6)
            existing_ids = {item.id for item in chat_ctx.items}
            items_copy = [item for item in truncated_chat_ctx.items if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)

        # Add current state snapshot for the LLM
        chat_ctx.add_message(
            role="system",
            content=(
                f"You are the {agent_name}.\n"
                f"Current user data (YAML):\n{userdata.summarize()}\n"
            ),
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(tool_choice="none")

    async def _transfer_to_agent(self, name: str, context: RunContext) -> tuple[Agent, str]:
        userdata = context.userdata
        current_agent = context.session.current_agent
        next_agent = userdata.agents[name]
        userdata.prev_agent = current_agent
        return next_agent, f"Transferring to {name}."

