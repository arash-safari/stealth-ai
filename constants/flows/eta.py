# ETA planner (one-step ReAct)
ETA_PLANNER = {
  "flow_name": "ETA",
  "description": "Customer asks for the technician’s ETA or arrival window for an existing appointment.",
  "required_slots": ["appointment_id"],
  "prompt": """
Goal: provide the current arrival window and ETA for the user’s appointment.

Required slots: appointment_id.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If appointment_id missing → ask for it.
2) After appointment_id present → call get_eta (or lookup_appointment + get_eta).
3) Say a short line with arrival_window and eta, then finish.

Actions you may choose:
- ASK_APPOINTMENT_ID(question="Can you share your appointment number?", expected="ID or name on the booking")
- LOOKUP_APPOINTMENT(tool=lookup_appointment)            # optional; store as appt
- GET_ETA(tool=get_eta)                                  # returns {"arrival_window":"...","eta":"..."}
- SAY_ETA(text="Thanks! I’ve found appointment {appointment_id}. Your current arrival window is {arrival_window}. Estimated arrival: {eta}.")
- DONE_WRAP()
"""
}
