# RESCHEDULE planner (one-step ReAct)
RESCHEDULE_PLANNER = {
  "flow_name": "RESCHEDULE",
  "description": "Customer wants to change the time of an existing appointment.",
  "required_slots": ["appointment_id", "time_window"],
  "prompt": """
Goal: reschedule the existing appointment to a new 4-hour window after a clear confirmation.

Required slots: appointment_id, time_window.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If appointment_id missing → ask for it (optionally lookup details).
2) If time_window missing → get options first (find_available_window), then ask user to pick.
3) Confirm the new time briefly, then call reschedule_appointment. Say completion line and finish.

Actions you may choose:
- ASK_APPOINTMENT_ID(question="What’s the appointment number or name?", expected="ID or name on the booking")
- LOOKUP_APPOINTMENT(tool=lookup_appointment)                     # optional; store as appt
- SUGGEST_WINDOWS(tool=find_available_window)                      # returns {"options":["08:00-12:00","12:00-16:00",...]}
- ASK_NEW_TIME(question="What new time works? {time_1} or {time_2}", expected="4-hour window", question_vars from tools.options)
- CONFIRM_RESCHEDULE(text="Confirm rescheduling appointment '{appointment_id}' to {time_window}?")
- DO_RESCHEDULE(tool=reschedule_appointment)                       # returns {"ok":true}
- SAY_RESCHEDULED(text="All set. Your appointment has been rescheduled.")
- DONE_WRAP()
"""
}
