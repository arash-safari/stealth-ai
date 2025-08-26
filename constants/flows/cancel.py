# CANCEL planner (one-step ReAct)
CANCEL_PLANNER = {
  "flow_name": "CANCEL",
  "description": "Customer wants to cancel an existing appointment.",
  "required_slots": ["appointment_id"],
  "prompt": """
Goal: cancel the user’s existing appointment after a clear confirmation.

Required slots: appointment_id.

You see: {history}, {state.slots+confidence}, {tools}

Output EXACTLY ONE tool call:
  next_action(name, args_json, rationale)

Policy:
1) If appointment_id missing → ask for it.
2) After appointment_id present → (optional) lookup details, then confirm cancellation in one short line.
3) If user consents, call cancel_appointment. Then say a short completion line and finish.

Actions you may choose:
- ASK_APPOINTMENT_ID(question="What’s the appointment number or name?", expected="ID or name on the booking")
- LOOKUP_APPOINTMENT(tool=lookup_appointment)               # store e.g. as appt
- CONFIRM_CANCEL(text="Confirm you want to cancel appointment '{appointment_id}'?")
- DO_CANCEL(tool=cancel_appointment)                         # returns e.g. {"ok": true}
- SAY_CANCELED(text="Done. Your appointment has been canceled.")
- DONE_WRAP()
"""
}
