# STATUS planner (one-step ReAct)
STATUS_PLANNER = {
  "flow_name": "STATUS",
  "description": "Customer asks for the current status or state of an existing appointment.",
  "required_slots": ["appointment_id"],
  "prompt": """
Goal: share the current status and scheduled window for the user’s appointment.

Required slots: appointment_id.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If appointment_id missing → ask for it.
2) After appointment_id present → call get_status (or lookup_appointment + get_status).
3) Say a concise status line with status and arrival_window, then finish.

Actions you may choose:
- ASK_APPOINTMENT_ID(question="What’s your appointment number?", expected="ID or name on the booking")
- LOOKUP_APPOINTMENT(tool=lookup_appointment)                # optional; store as appt
- GET_STATUS(tool=get_status)                                # returns {"status":"...", "arrival_window":"..."}
- SAY_STATUS(text="Thanks! I’ve found appointment {appointment_id}. Current status: {status}. Scheduled window: {arrival_window}. I’ll notify you if anything changes.")
- DONE_WRAP()
"""
}
