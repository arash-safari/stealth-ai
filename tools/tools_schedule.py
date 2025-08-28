## tools/tools_schedule.py`

import yaml
from datetime import datetime, timedelta, timezone
from typing import Optional
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext
from services import schedule_service as sched
from services import user_service as users

from common.utils import _dt_utc, _time_of, _parse_window_to_utc, _PRIO, _STATUS, ZoneInfo
from common.models import UserData
from db.models import RequestPriority

@function_tool()
async def get_available_times(
    context: RunContext,
    skill: str,
    duration_min: int = 120,
    priority: str = "P3",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 6,
    respect_google_busy: Optional[bool] = True,
) -> str:
    skill = "drain"  # preserve original override
    pr = _PRIO.get(priority.upper(), RequestPriority.P3)
    lim = max(1, int(limit or 6))
    dur = max(1, int(duration_min or 120))
    respect_busy = True if respect_google_busy is None else bool(respect_google_busy)

    slots = await sched.get_available_times(
        skill=skill,
        duration_min=dur,
        priority=pr,
        date_from=_dt_utc(date_from),
        date_to=_dt_utc(date_to),
        limit=lim,
        respect_google_busy=respect_busy,
    )
    slots = slots[:lim]
    out = [
        {
            "tech_id": s["tech_id"],
            "start": s["start"].isoformat(),
            "end": s["end"].isoformat(),
            "source": s["source"],
        }
        for s in slots
    ]
    return yaml.dump({"slots": out}, sort_keys=False)


@function_tool()
async def get_nearest_available_time(
    context: RunContext,
    skill: str,
    duration_min: int = 120,
    priority: str = "P3",
    after: Optional[str] = None,
    respect_google_busy: Optional[bool] = True,
) -> str:
    """
    Return the earliest/nearest available slot within the next 7 days.
    YAML response:
      nearest_slot:
        tech_id: "<uuid>"
        start: "2025-08-27T17:00:00+00:00"
        end:   "2025-08-27T19:00:00+00:00"
        source: "db" | "db+google"
    """
    skill = "drain"
    pr = _PRIO.get(priority.upper(), RequestPriority.P3)

    dur = max(1, int(duration_min or 120))
    respect_busy = True if respect_google_busy is None else bool(respect_google_busy)

    start_from = _dt_utc(after) if after else datetime.now(timezone.utc)
    end_to = start_from + timedelta(days=7)

    slots = await sched.get_available_times(
        skill=skill,
        duration_min=dur,
        priority=pr,
        date_from=start_from,
        date_to=end_to,
        limit=200,
        respect_google_busy=respect_busy,
    )

    if not slots:
        return yaml.dump(
            {"nearest_slot": None, "message": "No availability found in the next 7 days."},
            sort_keys=False,
        )

    slots.sort(key=lambda s: s["start"])  # earliest first

    s = slots[0]
    out = {
        "nearest_slot": {
            "tech_id": s["tech_id"],
            "start": s["start"].isoformat(),
            "end": s["end"].isoformat(),
            "source": s["source"],
        }
    }
    return yaml.dump(out, sort_keys=False)


@function_tool()
async def svc_hold_slot(
    context: RunContext,
    tech_id: str,
    user_id: Optional[str],
    start: str,
    end: str,
    ttl_seconds: int = 180,
    request_text: Optional[str] = None,
    show_tentative_on_google: bool = False,
) -> str:
    res = await sched.hold_slot(
        tech_id=tech_id,
        user_id=user_id,
        start=_dt_utc(start),
        end=_dt_utc(end),
        ttl_seconds=ttl_seconds,
        request_text=request_text,
        show_tentative_on_google=show_tentative_on_google,
    )
    res["start"] = res["start"].isoformat()
    res["end"] = res["end"].isoformat()
    res["expires_at"] = res["expires_at"].isoformat()
    return yaml.dump(res, sort_keys=False)


@function_tool()
async def get_today(
    context: RunContext,
    tz: Optional[str] = "UTC",
    fmt: Optional[str] = "%Y-%m-%d",
) -> str:
    try:
        zone = ZoneInfo(tz) if tz else timezone.utc
    except Exception:
        zone = timezone.utc
        tz = "UTC"

    now = datetime.now(zone)
    out = {
        "today": {
            "date": now.strftime(fmt or "%Y-%m-%d"),
            "iso": now.isoformat(),
            "weekday": now.strftime("%A"),
            "tz": tz or "UTC",
            "epoch": int(now.timestamp()),
        }
    }
    return yaml.dump(out, sort_keys=False)


@function_tool()
async def create_appointment(
    context: RunContext,
    skill: Optional[str] = "plumbing",
    duration_min: int = 120,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    respect_google_busy: Optional[bool] = True,
) -> str:
    skill = "drain"  # preserve original override
    u: UserData = context.userdata

    missing = []
    if not u.customer_phone: missing.append("customer_phone")
    if not u.customer_name: missing.append("customer_name")
    if missing:
        return f"Missing required user data: {', '.join(missing)}"

    existing = await users.get_user_by_phone(u.customer_phone)
    user_id = existing["id"] if existing else (await users.create_user(
        full_name=u.customer_name, phone=u.customer_phone, email=u.customer_email
    ))["id"]

    has_any_address = any([u.street, u.city, u.state, u.postal_code, u.unit])
    if has_any_address:
        default_addr = await users.get_default_address(user_id)
        if not default_addr:
            try:
                await users.add_address(
                    user_id=user_id,
                    line1=(u.street or "Address line 1"),
                    line2=u.unit,
                    city=u.city,
                    state=u.state,
                    postal_code=u.postal_code,
                    label="Service",
                    is_default=True,
                )
            except Exception:
                pass

    win_start = win_end = None
    if date_from or date_to:
        s = _dt_utc(date_from) if date_from else None
        e = _dt_utc(date_to) if date_to else None
        if s and not e:
            e = s + timedelta(minutes=max(1, int(duration_min or 120)))
        elif e and not s:
            s = e - timedelta(minutes=max(1, int(duration_min or 120)))
        if not (s and e):
            return "Invalid window: need date_from or date_to (or both)."
        if e <= s:
            return "Invalid window: date_to must be later than date_from."
        win_start, win_end = s, e
    else:
        if not (u.appointment_date and u.appointment_window):
            return "Missing window: provide date_from/date_to or set appointment_date and appointment_window."
        try:
            win_start, win_end = _parse_window_to_utc(u.appointment_date, u.appointment_window)
        except Exception as e:
            return f"Invalid appointment date/window: {e}"

    urgency = (u.urgency or "normal").lower()
    pr = RequestPriority.P1 if urgency == "emergency" else RequestPriority.P2 if urgency == "urgent" else RequestPriority.P3

    dur = max(1, int(duration_min or 120))
    respect_busy = True if respect_google_busy is None else bool(respect_google_busy)

    slots = await sched.get_available_times(
        skill=skill or "plumbing",
        duration_min=dur,
        priority=pr,
        date_from=win_start,
        date_to=win_end,
        limit=50,
        respect_google_busy=respect_busy,
    )

    chosen = next((s for s in slots if s["start"] >= win_start and s["end"] <= win_end), None)
    if not chosen:
        return "No availability in the selected window. Please choose another window."

    req_text = (u.problem_description or "Plumbing service").strip()
    if has_any_address:
        req_text = f"{req_text} — Address: {u.address_str()}"

    res = await sched.create_meeting(
        user_id=user_id,
        tech_id=chosen["tech_id"],
        start=chosen["start"],
        end=chosen["end"],
        priority=pr,
        request_text=req_text,
    )

    u.appointment_id = str(res.get("id") or res.get("appointment_id") or "")
    u.appointment_status = "scheduled"
    try:
        s_iso = res["start"].isoformat() if hasattr(res["start"], "isoformat") else res["start"]
        e_iso = res["end"].isoformat() if hasattr(res["end"], "isoformat") else res["end"]
        s_dt = _dt_utc(s_iso)
        e_dt = _dt_utc(e_iso)
        u.appointment_date = s_dt.date().isoformat()
        u.appointment_window = f"{s_dt.strftime('%H:%M')}-{e_dt.strftime('%H:%M')}"
    except Exception:
        pass

    res["start"] = res["start"].isoformat()
    res["end"] = res["end"].isoformat()
    return yaml.dump(
        {"message": "Appointment created from UserData", "user_id": user_id, "appointment": res},
        sort_keys=False,
    )


@function_tool()
async def read_meeting(context: RunContext, appointment_id: str) -> str:
    res = await sched.read_meeting(appointment_id)
    res["start"] = res["start"].isoformat()
    res["end"] = res["end"].isoformat()
    return yaml.dump(res, sort_keys=False)


@function_tool()
async def update_meeting(
    context: RunContext,
    appointment_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    status: Optional[str] = None,
    request_text: Optional[str] = None,
) -> str:
    res = await sched.update_meeting(
        appointment_id=appointment_id,
        start=_dt_utc(start) if start else None,
        end=_dt_utc(end) if end else None,
        status=_STATUS[status] if status else None,
        request_text=request_text,
    )
    res["start"] = res["start"].isoformat()
    res["end"] = res["end"].isoformat()
    return yaml.dump(res, sort_keys=False)


@function_tool()
async def cancel_meeting(context: RunContext, appointment_id: str) -> str:
    res = await sched.cancel_meeting(appointment_id)
    return yaml.dump(res, sort_keys=False)


@function_tool()
async def create_earliest_meeting(
    context: RunContext,
    user_id: str,
    skill: str,
    duration_min: int = 120,
    priority: str = "P3",
    request_text: Optional[str] = None,
) -> str:
    from db.models import RequestPriority
    pr = _PRIO.get(priority.upper(), RequestPriority.P3)
    res = await sched.create_earliest_meeting(
        user_id=user_id,
        skill=skill,
        duration_min=duration_min,
        priority=pr,
        request_text=request_text,
    )
    res["start"] = res["start"].isoformat()
    res["end"] = res["end"].isoformat()
    return yaml.dump(res, sort_keys=False)
