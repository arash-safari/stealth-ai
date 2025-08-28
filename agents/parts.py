## agents/parts.py`

from livekit.plugins import openai
from livekit.agents.llm import function_tool
from common.base_agent import BaseAgent

class Parts(BaseAgent):
    CATALOGUE = {
        "FCT-001": {"name": "Kitchen faucet", "price": 120.0},
        "FLT-002": {"name": "Toilet fill valve", "price": 35.0},
        "TRP-001": {"name": "P-trap (1-1/2\" ABS)", "price": 14.0},
        "WH-40": {"name": "40-gal water heater", "price": 950.0},
        "SPLY-PEX": {"name": "PEX supply line", "price": 9.0},
        "P-PTFE": {"name": "PTFE tape", "price": 4.0},
    }

    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a parts agent. Help customers add/remove parts the plumber should bring. Keep a running total. Encourage checkout if prepayment is required."
            ),
            tools=[],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="alloy"),
        )

    def _recalc_total(self, u) -> None:
        u.cart_total = round(sum(item["qty"] * item["unit_price"] for item in u.cart), 2)

    @function_tool()
    async def list_catalogue(self, context) -> str:
        import yaml
        cats = [{"sku": sku, **info} for sku, info in self.CATALOGUE.items()]
        return yaml.dump({"catalogue": cats}, sort_keys=False)

    @function_tool()
    async def add_part(self, context, sku: str, qty: int = 1) -> str:
        u = context.userdata
        if sku not in self.CATALOGUE:
            return f"Unknown SKU: {sku}"
        info = self.CATALOGUE[sku]
        for item in u.cart:
            if item["sku"] == sku:
                item["qty"] += qty
                self._recalc_total(u)
                return f"Updated {sku} qty to {item['qty']}. Cart total=${u.cart_total:.2f}"
        u.cart.append({"sku": sku, "name": info["name"], "qty": qty, "unit_price": info["price"]})
        self._recalc_total(u)
        return f"Added {qty} × {info['name']} (SKU {sku}). Cart total=${u.cart_total:.2f}"

    @function_tool()
    async def remove_part(self, context, sku: str) -> str:
        u = context.userdata
        before = len(u.cart)
        u.cart = [i for i in u.cart if i["sku"] != sku]
        if len(u.cart) == before:
            return f"SKU {sku} not in cart."
        self._recalc_total(u)
        return f"Removed {sku}. Cart total=${u.cart_total:.2f}"

    @function_tool()
    async def view_cart(self, context) -> str:
        import yaml
        u = context.userdata
        return yaml.dump({"cart": u.cart, "total": u.cart_total}, sort_keys=False)
