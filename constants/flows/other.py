# OTHER planner (one-step ReAct)
OTHER_PLANNER = {
  "flow_name": "OTHER",
  "description": "Catch-all flow when the request doesn’t match predefined paths; routes to a human.",
  "required_slots": ["description"],
  "prompt": """
Goal: capture a brief description and hand the conversation off to a human.

Required slots: description.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If description missing → ask for a short description.
2) After description present → say one routing line and finish (or call a handoff tool if available).

Actions you may choose:
- ASK_ISSUE_GENERIC(question="How can I help today?", expected="Short description")
- SAY_HANDOFF(text="Thanks for the details. I’ll route you to a human specialist now.")
- (optional) DO_HANDOFF(tool=handoff_to_human)
- DONE_WRAP()
"""
}
