## agents/pricing.py

from livekit.plugins import cartesia
from common.base_agent import BaseAgent
from livekit.agents.llm import function_tool

class Pricing(BaseAgent):
    ISSUE_TABLE = [
        {"keywords": ["clog", "drain", "toilet"], "low": 120, "high": 250},
        {"keywords": ["leak", "pipe", "burst"], "low": 180, "high": 450},
        {"keywords": ["water heater", "no hot", "heater"], "low": 250, "high": 1200},
        {"keywords": ["faucet", "tap", "install"], "low": 90, "high": 220},
        {"keywords": ["garbage disposal", "disposal"], "low": 150, "high": 350},
    ]

    def __init__(self, voices: dict | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a pricing agent. Provide rough ranges only (not binding). "
                "If problem description exists, infer the best range; otherwise ask a couple of clarifying questions. "
                "Always recommend an on-site diagnosis for a firm quote."
            ),
            tools=[],
            tts=cartesia.TTS(voice=(voices or {}).get("pricing", "")),
        )

    @function_tool()
    async def get_estimate(self, context) -> str:
        u = context.userdata
        desc = (u.problem_description or "").lower()
        low, high = 110, 400
        for row in self.ISSUE_TABLE:
            if any(k in desc for k in row["keywords"]):
                low, high = row["low"], row["high"]
                break
        u.estimate_low, u.estimate_high = float(low), float(high)
        return f"Estimated range: ${low:.0f}–${high:.0f} (labor + standard parts; taxes extra)."
