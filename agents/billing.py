## agents/billing.py

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from common.base_agent import BaseAgent
from common.common_tools import update_name, update_phone, update_email

class Billing(BaseAgent):
    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a billing agent. Confirm amount (deposit or cart total), then collect card number, expiry, and CVV step by step. "
                "Acknowledge last four only when repeating numbers back."
            ),
            tools=[update_name, update_phone, update_email],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="verse"),
        )

    @function_tool()
    async def confirm_amount(self, context, amount: float) -> str:
        u = context.userdata
        u.amount_authorized = float(max(0.0, amount))
        return f"Amount set to ${u.amount_authorized:.2f}"

    @function_tool()
    async def update_card(self, context, number: str, expiry: str, cvv: str) -> str:
        u = context.userdata
        u.card_number = number
        u.card_expiry = expiry
        u.card_cvv = cvv
        tail = number[-4:] if number else "****"
        return f"Card ending {tail} stored for authorization."

    @function_tool()
    async def authorize_payment(self, context):
        u = context.userdata
        if not (u.amount_authorized and u.card_number and u.card_expiry and u.card_cvv):
            return "Missing amount or card details."
        u.amount_authorized = 0.0
        return await self._transfer_to_agent("router", context)
