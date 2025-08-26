# PRICE planner (one-step ReAct)
PRICE_PLANNER = {
  "flow_name": "PRICE",
  "description": "Customer asks about pricing, fees, or rates; optionally transitions into booking.",
  "required_slots": [],
  "prompt": """
Goal: disclose pricing clearly and (optionally) offer to start a booking.

Required slots: none.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If dynamic pricing is needed, call get_pricing; else use known values.
2) Say one concise price line (base fee, labor rate, included minutes, note on estimate).
3) Ask if user wants to book now; if yes, bridge to BOOK flow; then finish.

Actions you may choose:
- GET_PRICING(tool=get_pricing)  # returns {"base_fee":"...", "labor_rate":"...", "included_minutes": 30}
- SAY_PRICE(text="Here’s how our pricing works: base visit fee {base_fee} covers travel and diagnosis. Labor is {labor_rate}/hour after the first {included_minutes} minutes. Parts and tax are additional. Before any work, we’ll share a written estimate for your approval.")
- ASK_WANT_BOOK(question="Would you like to book a visit with these rates?", expected="yes/no")
- BRIDGE_TO_BOOK(text="Great—I'll collect your address, the issue, and your preferred 4-hour window next.")  # controller should transition to BOOK
- DONE_WRAP()
"""
}
