import logging
from dataclasses import dataclass, field
from typing import Annotated, Optional

import yaml
from dotenv import load_dotenv
from pydantic import Field
from livekit.api.twirp_client import TwirpError, TwirpErrorCode

from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, AgentSession, RunContext
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.agents import get_job_context
from livekit import api

import uuid
from datetime import datetime, timedelta, time
# --- NEW: service imports + enums
from services import schedule_service as sched
from services import user_service as users
from db.models import AppointmentStatus, RequestPriority

# --- NEW: datetime & enum helpers
from datetime import datetime, timezone
from typing import Optional, Dict
import asyncio
from zoneinfo import ZoneInfo

def _dt_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # ISO 8601 first
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        # a couple of common fallbacks
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except Exception:
                continue
        else:
            raise ValueError(f"Unparseable datetime: {s}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _time_of(daytime: str) -> time:
    # "HH:MM"
    return datetime.strptime(daytime, "%H:%M").time()

_PRIO: Dict[str, RequestPriority] = {
    "P1": RequestPriority.P1,
    "P2": RequestPriority.P2,
    "P3": RequestPriority.P3,
}
_STATUS: Dict[str, AppointmentStatus] = {
    # adjust if your enum names differ
    "scheduled": AppointmentStatus.scheduled,
    "en_route": getattr(AppointmentStatus, "en_route", AppointmentStatus.scheduled),
    "complete": getattr(AppointmentStatus, "complete", AppointmentStatus.scheduled),
    "canceled": AppointmentStatus.canceled,
}

async def hangup_call() -> str:
    ctx = get_job_context()
    if ctx is None:
        logger.warning("hangup_call(): no JobContext; nothing to do")
        return "no_job_ctx"

    room_name = getattr(ctx.room, "name", None)
    logger.info("hangup_call(): room=%s", room_name)

    # Try to end the call for everyone
    try:
        if room_name:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info("hangup_call(): room deleted")
        else:
            logger.info("hangup_call(): no room name; skipping delete_room")
    except TwirpError as e:
        # Treat already-closed room as success
        if e.code == TwirpErrorCode.NOT_FOUND or getattr(e, "status", None) == 404:
            logger.info("hangup_call(): room already gone (404) — treating as success")
        else:
            logger.warning("hangup_call(): delete_room failed: %s", e)

    # IMPORTANT: shutdown is **sync** — do NOT await it
    ctx.shutdown(reason="hangup")
    return "shutdown"

async def scrub_all_histories(context: RunContext) -> None:
    """Keep only system messages in every agent's chat context."""
    u = context.userdata
    tasks = []
    for agent in u.agents.values():
        ctx = agent.chat_ctx.copy()
        # retain only system messages (instructions)
        ctx.items = [itm for itm in ctx.items if getattr(itm, "role", "") == "system"]
        tasks.append(agent.update_chat_ctx(ctx))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

# ------------------------------------------------------------
# Multi‑agent voice system for a Plumbing Company contact center
#
# Fixes for tool schema / Pydantic issues:
#  - All @function_tool methods take `context: RunContext` as the FIRST param,
#    with NO default, and only primitive/JSON‑schema‑friendly user arguments
#    after that. This keeps `context` out of the OpenAI tool schema.
#  - No use of RunContext[...] generics in tool signatures.
#  - No `strict_tool_schema` kwarg (not supported in some plugin versions).
# ------------------------------------------------------------

logger = logging.getLogger("plumber-contact-center")
logger.setLevel(logging.INFO)

load_dotenv()

# Replace with your Cartesia voice IDs if desired
voices = {
    "router": "794f9389-aac1-45b6-b726-9d9369183238",
    "booking": "156fb8d2-335b-4950-9cb3-a2d33befec77",
    "reschedule": "6f84f4b8-58a2-430c-8c79-688dad597532",
    "cancel": "39b376fc-488e-4d0c-8b37-e00b72059fdd",
    "parts": "c5bc2f14-6c18-4e63-9f10-2d8897c5c00c",
    "status": "28b5a8c0-51b2-4a30-a3a5-98a4b2a335c6",
    "pricing": "1e2a3b4c-5d6e-7f80-9a0b-1c2d3e4f5a6b",
    "billing": "f1a2b3c4-d5e6-47f8-9a0b-1c2d3e4f5a6b",
    "operator": "ab12cd34-ef56-7890-ab12-cd34ef567890",
}


# -----------------------------
# Domain models / shared state
# -----------------------------
@dataclass
class UserData:
    # Contact
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None

    # Service location
    street: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None

    # Job details
    problem_description: Optional[str] = None
    urgency: Optional[str] = None  # "normal" | "urgent" | "emergency"

    # Appointment
    appointment_id: Optional[str] = None
    appointment_date: Optional[str] = None          # YYYY-MM-DD
    appointment_window: Optional[str] = None        # HH:MM-HH:MM
    appointment_status: Optional[str] = None        # scheduled | en_route | complete | canceled

    # Parts cart (customer asks us to bring items)
    cart: list[dict] = field(default_factory=list)  # {sku, name, qty, unit_price}
    cart_total: float = 0.0

    # Pricing / estimate
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None

    # Payments (for deposit or parts prepay)
    card_number: Optional[str] = None
    card_expiry: Optional[str] = None
    card_cvv: Optional[str] = None
    amount_authorized: Optional[float] = None

    # Agent shared
    agents: dict[str, "BaseAgent"] = field(default_factory=dict)
    prev_agent: Optional["BaseAgent"] = None

    def address_str(self) -> str:
        parts = [self.street or "", self.unit or "", self.city or "", self.state or "", self.postal_code or ""]
        return ", ".join([p for p in parts if p]) or "unknown"

    def summarize(self) -> str:
        data = {
            "customer": {
                "name": self.customer_name or "unknown",
                "phone": self.customer_phone or "unknown",
                "email": self.customer_email or "unknown",
            },
            "address": {
                "street": self.street or "unknown",
                "unit": self.unit or None,
                "city": self.city or "unknown",
                "state": self.state or "unknown",
                "postal_code": self.postal_code or "unknown",
            },
            "job": {
                "description": self.problem_description or "unknown",
                "urgency": self.urgency or "normal",
            },
            "appointment": {
                "id": self.appointment_id or None,
                "date": self.appointment_date or None,
                "window": self.appointment_window or None,
                "status": self.appointment_status or None,
            },
            "cart": self.cart or [],
            "cart_total": round(self.cart_total, 2),
            "estimate": {
                "low": self.estimate_low,
                "high": self.estimate_high,
            },
            "payment": {
                "card_number": self.card_number or None,
                "expiry": self.card_expiry or None,
                "cvv": self.card_cvv or None,
                "amount_authorized": self.amount_authorized,
            },
        }
        return yaml.dump(data, sort_keys=False)

def scrub_user_data(u: UserData) -> None:
    # Contact
    u.customer_name = None
    u.customer_phone = None
    u.customer_email = None

    # Address
    u.street = u.unit = u.city = u.state = u.postal_code = None

    # Job details
    u.problem_description = None
    u.urgency = None

    # Appointment
    u.appointment_id = None
    u.appointment_date = None
    u.appointment_window = None
    u.appointment_status = None

    # Parts / cart
    u.cart.clear()
    u.cart_total = 0.0

    # Pricing / estimate
    u.estimate_low = None
    u.estimate_high = None

    # Payment (wipe first!)
    u.card_number = None
    u.card_expiry = None
    u.card_cvv = None
    u.amount_authorized = None

    # Agent cross-refs
    u.prev_agent = None


# =========================
# schedule_service TOOLS
# =========================

@function_tool()
async def get_available_times(
    context: RunContext,
    skill: str,
    duration_min: int = 120,
    priority: str = "P3",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 6,
    respect_google_busy: Optional[bool] = True,   # accept None safely
) -> str:
    skill = "drain"
    pr = _PRIO.get(priority.upper(), RequestPriority.P3)

    # sanitize inputs coming from the LLM / JSON
    lim = max(1, int(limit or 6))
    dur = max(1, int(duration_min or 120))
    # if caller sends null, default to True (keep Google free/busy on)
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

    # defensive cap (even if the scheduler misbehaved)
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
    after: Optional[str] = None,              # ISO or "YYYY-MM-DDTHH:MM"; defaults to now UTC
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

    # sanitize inputs
    dur = max(1, int(duration_min or 120))
    respect_busy = True if respect_google_busy is None else bool(respect_google_busy)

    start_from = _dt_utc(after) if after else datetime.now(timezone.utc)
    end_to = start_from + timedelta(days=7)   # <— always consider one week

    # Pull plenty of slots within the 1-week window, then sort locally
    slots = await sched.get_available_times(
        skill=skill,
        duration_min=dur,
        priority=pr,                 # priority still influences internal logic, but date_to caps horizon
        date_from=start_from,
        date_to=end_to,
        limit=200,                   # generous cap; we’ll sort and pick the first
        respect_google_busy=respect_busy,
    )

    if not slots:
        return yaml.dump(
            {
                "nearest_slot": None,
                "message": "No availability found in the next 7 days.",
            },
            sort_keys=False,
        )

    slots.sort(key=lambda s: s["start"])       # ensure earliest first

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


def _parse_window_to_utc(date_str: str, window: str) -> tuple[datetime, datetime]:
    # window="HH:MM-HH:MM"
    try:
        start_s, end_s = window.split("-")
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        s_local = datetime.combine(d, datetime.strptime(start_s, "%H:%M").time())
        e_local = datetime.combine(d, datetime.strptime(end_s, "%H:%M").time())
    except Exception as e:
        raise ValueError(f"Invalid date/window. Expected YYYY-MM-DD and HH:MM-HH:MM. Error: {e}")
    # treat as UTC if no tz info is available in UserData
    s = s_local.replace(tzinfo=timezone.utc)
    e = e_local.replace(tzinfo=timezone.utc)
    return s, e


# =========================
# Book using ONLY UserData
# =========================

@function_tool()
async def get_today(
    context: RunContext,
    tz: Optional[str] = "UTC",
    fmt: Optional[str] = "%Y-%m-%d",
) -> str:
    """
    Return today's date/time in a given IANA timezone.
    - tz: IANA name like 'America/Los_Angeles' (defaults to UTC if invalid/missing)
    - fmt: strftime format for the 'date' field (default %Y-%m-%d)

    YAML response example:
      today:
        date: "2025-08-27"
        iso: "2025-08-27T17:23:45.123456+00:00"
        weekday: "Wednesday"
        tz: "UTC"
        epoch: 1693157025
    """
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
    date_from: Optional[str] = None,     # ISO or "YYYY-MM-DDTHH:MM"
    date_to: Optional[str] = None,       # ISO or "YYYY-MM-DDTHH:MM"
    respect_google_busy: Optional[bool] = True,
) -> str:
    skill = "water heater"
    """
    Create an appointment inside a specified window.
    - If date_from/date_to are provided, they define the search window.
    - Otherwise, falls back to UserData.appointment_date + appointment_window ("HH:MM-HH:MM").
    - If only one bound is provided, the other is inferred from duration_min.
    """
    u: UserData = context.userdata

    # --- required contact fields ---
    missing = []
    if not u.customer_phone: missing.append("customer_phone")
    if not u.customer_name: missing.append("customer_name")
    if missing:
        return f"Missing required user data: {', '.join(missing)}"

    # --- resolve/create user ---
    existing = await users.get_user_by_phone(u.customer_phone)
    user_id = existing["id"] if existing else (await users.create_user(
        full_name=u.customer_name, phone=u.customer_phone, email=u.customer_email
    ))["id"]

    # --- ensure default address if provided ---
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
                pass  # non-fatal

    # --- determine booking window (UTC) ---
    win_start = win_end = None

    # Prefer explicit window if supplied
    if date_from or date_to:
        s = _dt_utc(date_from) if date_from else None
        e = _dt_utc(date_to) if date_to else None

        # Infer the missing bound from duration_min
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
        # Fall back to UserData date + "HH:MM-HH:MM" window
        if not (u.appointment_date and u.appointment_window):
            return "Missing window: provide date_from/date_to or set appointment_date and appointment_window."
        try:
            win_start, win_end = _parse_window_to_utc(u.appointment_date, u.appointment_window)
        except Exception as e:
            return f"Invalid appointment date/window: {e}"

    # --- priority from urgency ---
    urgency = (u.urgency or "normal").lower()
    pr = RequestPriority.P1 if urgency == "emergency" else RequestPriority.P2 if urgency == "urgent" else RequestPriority.P3

    # --- search availability inside the window ---
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

    # --- create the appointment ---
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

    # keep UserData in sync
    u.appointment_id = str(res.get("id") or res.get("appointment_id") or "")
    u.appointment_status = "scheduled"
    try:
        # If UserData window wasn’t set earlier, set it from the chosen slot
        s_iso = res["start"].isoformat() if hasattr(res["start"], "isoformat") else res["start"]
        e_iso = res["end"].isoformat() if hasattr(res["end"], "isoformat") else res["end"]
        s_dt = _dt_utc(s_iso)
        e_dt = _dt_utc(e_iso)
        u.appointment_date = s_dt.date().isoformat()
        u.appointment_window = f"{s_dt.strftime('%H:%M')}-{e_dt.strftime('%H:%M')}"
    except Exception:
        pass

    # stringify datetimes for YAML
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


@function_tool()
async def publish_availability_for_range(
    context: RunContext,
    tech_id: str,
    start_date: str,  # YYYY-MM-DD
    end_date: str,    # YYYY-MM-DD
    start_time: str,  # HH:MM
    end_time: str,    # HH:MM
    weekdays: Optional[list[int]] = None,  # 0=Mon..6=Sun
    clear_overlaps: bool = False,
) -> str:
    res = await sched.publish_availability_for_range(
        tech_id=tech_id,
        start_date=datetime.strptime(start_date, "%Y-%m-%d").date(),
        end_date=datetime.strptime(end_date, "%Y-%m-%d").date(),
        start_time=_time_of(start_time),
        end_time=_time_of(end_time),
        weekdays=weekdays,
        clear_overlaps=clear_overlaps,
    )
    return yaml.dump(res, sort_keys=False)


# =========================
# user_service TOOLS
# =========================

@function_tool()
async def usr_create_user(context: RunContext, full_name: str, phone: str, email: Optional[str] = None) -> str:
    res = await users.create_user(full_name=full_name, phone=phone, email=email)
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_get_user(context: RunContext, user_id: str) -> str:
    res = await users.get_user(user_id)
    return yaml.dump(res or {}, sort_keys=False)

@function_tool()
async def get_user_by_phone(context: RunContext, phone: str) -> str:
    res = await users.get_user_by_phone(phone)
    return yaml.dump(res or {}, sort_keys=False)

@function_tool()
async def usr_update_user(context: RunContext, user_id: str, full_name: Optional[str] = None,
                          phone: Optional[str] = None, email: Optional[str] = None) -> str:
    data = {}
    if full_name is not None: data["full_name"] = full_name
    if phone is not None: data["phone"] = phone
    if email is not None: data["email"] = email
    res = await users.update_user(user_id, **data)
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_add_address(
    context: RunContext,
    user_id: str,
    line1: str,
    line2: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    label: Optional[str] = None,
    is_default: bool = False,
) -> str:
    res = await users.add_address(
        user_id, line1=line1, line2=line2, city=city, state=state, postal_code=postal_code,
        label=label, is_default=is_default
    )
    return yaml.dump(res, sort_keys=False)

@function_tool()
async def usr_get_default_address(context: RunContext, user_id: str) -> str:
    res = await users.get_default_address(user_id)
    return yaml.dump(res or {}, sort_keys=False)
# -----------------------------------
# Common tool fns (reusable across agents)
# -----------------------------------
@function_tool()
async def update_name(
    context: RunContext,
    name: Annotated[str, Field(description="Customer's full name (confirm spelling before calling)")],
) -> str:
    userdata: UserData = context.userdata
    userdata.customer_name = name
    return f"Name updated to: {name}"


@function_tool()
async def update_phone(
    context: RunContext,
    phone: Annotated[str, Field(description="Customer's phone number")],
) -> str:
    userdata: UserData = context.userdata
    userdata.customer_phone = phone
    return f"Phone updated to: {phone}"


@function_tool()
async def update_email(
    context: RunContext,
    email: Annotated[str, Field(description="Customer's email address")],
) -> str:
    userdata: UserData = context.userdata
    userdata.customer_email = email
    return f"Email updated to: {email}"


@function_tool()
async def update_address(
    context: RunContext,
    street: Annotated[str, Field(description="Street address (e.g., 123 Main St)")],
    city: Annotated[str, Field(description="City name")],
    state: Annotated[str, Field(description="State/Province/Region")],
    postal_code: Annotated[str, Field(description="ZIP/Postal code")],
    unit: Annotated[Optional[str], Field(description="Apartment/Unit/Suite")] = None,
) -> str:
    userdata: UserData = context.userdata
    userdata.street = street
    userdata.city = city
    userdata.state = state
    userdata.postal_code = postal_code
    userdata.unit = unit
    return f"Address updated to: {userdata.address_str()}"


@function_tool()
async def update_problem(
    context: RunContext,
    description: Annotated[str, Field(description="Customer's plumbing issue in their own words")],
    urgency: Annotated[Optional[str], Field(description='Urgency: "normal", "urgent", or "emergency"')] = "normal",
) -> str:
    userdata: UserData = context.userdata
    userdata.problem_description = description
    userdata.urgency = urgency
    return f"Problem updated. Urgency={urgency}. Description={description}"


@function_tool()
async def to_router(context: RunContext) -> Agent:
    """Transfer to the Router for unrelated questions or switching tasks."""
    curr_agent: BaseAgent = context.session.current_agent
    return await curr_agent._transfer_to_agent("router", context)


# -------------------
# Core base agent
# -------------------
class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering: {agent_name}")

        userdata: UserData = self.session.userdata
        chat_ctx = self.chat_ctx.copy()

        # Carry forward a trimmed recent chat history from previous agent
        if isinstance(userdata.prev_agent, Agent):
            truncated_chat_ctx = userdata.prev_agent.chat_ctx.copy(
                exclude_instructions=True, exclude_function_call=False
            ).truncate(max_items=6)
            existing_ids = {item.id for item in chat_ctx.items}
            items_copy = [item for item in truncated_chat_ctx.items if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)

        # Add current state snapshot for the LLM
        chat_ctx.add_message(
            role="system",
            content=(
                f"You are the {agent_name}.\n"
                f"Current user data (YAML):\n{userdata.summarize()}\n"
            ),
        )
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(tool_choice="none")

    async def _transfer_to_agent(self, name: str, context: RunContext) -> tuple[Agent, str]:
        userdata = context.userdata
        current_agent = context.session.current_agent
        next_agent = userdata.agents[name]
        userdata.prev_agent = current_agent
        return next_agent, f"Transferring to {name}."


# -------------------
# Router / Triage agent
# -------------------
class Router(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly plumbing company receptionist.\n"
                "For your very first message, greet the caller and say: "
                "'Welcome to Ali Plumber Company! How can I help you today?'\n"
                "Triage the caller and route them: booking, reschedule, cancel, "
                "parts/product requests, status/ETA, pricing/estimate, billing, or operator.\n"
                "Ask minimal questions to decide, then use a tool to transfer."
                "If the caller says they're done (e.g., 'no, that's all', 'thank you, bye'), "
                "say a brief goodbye and CALL the end_call tool to hang up."

            ),
            llm=openai.LLM(parallel_tool_calls=False),
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )

    @function_tool()
    async def to_booking(self, context: RunContext):
        """User wants to book a new appointment."""
        return await self._transfer_to_agent("booking", context)

    @function_tool()
    async def to_reschedule(self, context: RunContext):
        """User wants to reschedule an existing appointment."""
        return await self._transfer_to_agent("reschedule", context)

    @function_tool()
    async def to_cancel(self, context: RunContext):
        """User wants to cancel an appointment."""
        return await self._transfer_to_agent("cancel", context)

    @function_tool()
    async def to_parts(self, context: RunContext):
        """User wants to add parts/products for the plumber to bring."""
        return await self._transfer_to_agent("parts", context)

    @function_tool()
    async def to_status(self, context: RunContext):
        """User wants status or ETA update."""
        return await self._transfer_to_agent("status", context)

    @function_tool()
    async def to_pricing(self, context: RunContext):
        """User is asking about prices or a rough quote."""
        return await self._transfer_to_agent("pricing", context)

    @function_tool()
    async def to_billing(self, context: RunContext):
        """User needs to prepay a deposit, pay for parts, or settle an invoice."""
        return await self._transfer_to_agent("billing", context)

    @function_tool()
    async def to_operator(self, context: RunContext):
        """User has another request (not covered)."""
        return await self._transfer_to_agent("operator", context)

    @function_tool()
    async def end_call(self, context: RunContext) -> str:
        handle = await context.session.say(
            "Thanks for calling Ali Plumber Company. Goodbye!",
            allow_interruptions=False,
        )
        if handle:
            await handle.wait_for_playout()

        # scrub PII + per-agent histories
        scrub_user_data(context.userdata)          # you added this earlier
        await scrub_all_histories(context)         # <- clears message history

        # hang up & shut down (404-safe)
        result = await hangup_call()
        logger.info("end_call(): hangup result=%s; userdata & histories scrubbed", result)
        return f"Call ended ({result})."    
# -------------------
# Booking agent
# -------------------
class Booking(BaseAgent):
    CATALOGUE = {
        # Optional: used when customers add parts during booking
        "FCT-001": {"name": "Kitchen faucet", "price": 120.0},
        "FLT-002": {"name": "Toilet fill valve", "price": 35.0},
        "WH-40": {"name": "40-gal water heater", "price": 950.0},
        "P-PTFE": {"name": "PTFE tape", "price": 4.0},
    }

    def __init__(self) -> None:
        super().__init__(
            instructions=(
    "Booking agent. Be concise and ask EXACTLY one question per turn.\n"
    "Start with a brief hello, then ask for the next missing field only. After each answer, ask the next.\n"
    "Collect (in order): name, phone (email optional), full service address (street, unit?, city, state, postal), "
    "problem description, urgency (normal/urgent/emergency), preferred date.\n"
    "When address+problem+urgency+preferred date are known, call get_available_times "
    "(skill='plumbing', duration_min=120, priority: emergency→P1, urgent→P2, else P3) and show the 2–4 earliest windows "
    "as a numbered list like: '1) Tue 14:00–16:00'. Ask: 'Which number works?'\n"
    "If the user asks for the earliest time or gives no preferred date, you may call get_nearest_available_time to find the soonest window.\n"
    "Use get_today to obtain today's date/time when interpreting phrases like 'today' or 'tomorrow'.\n"
    "When calling get_available_times, if date_from or date_to are not provided by the user, pass them as None.\n"
    "After selection, confirm in ≤2 short sentences (date, window, address). If customer says yes, call create_appointment "
    "with the chosen tech_id/start/end, priority, and a brief request_text (problem + address). Then read back the appointment id and window.\n"
    "If no slots are available, ask a single follow-up: expand date range or try another day (yes/no). If user revises info, update and continue.\n"
    "No small talk. Keep each message ≤2 sentences. Never ask multi-part questions."
            ),
            tools=[update_name, update_phone, update_email,
                    update_address, update_problem, to_router,
                    # new
                    create_appointment,
                    get_today,
                    get_nearest_available_time,
                    get_available_times],
            # tts=cartesia.TTS(voice=voices["booking"]),
            tts = openai.TTS(model="gpt-4o-mini-tts", voice="ash")  
        )

    def _generate_windows(self, preferred_date: Optional[str]) -> list[dict]:
        # Simple local scheduler: M-F 9:00–17:00, 2-hour windows, next ~30 working days
        start_date = datetime.utcnow().date()
        if preferred_date:
            try:
                start_date = datetime.strptime(preferred_date, "%Y-%m-%d").date()
            except Exception:
                pass
        windows = []
        day_cursor = start_date
        # collect about 30 weekdays worth of windows
        collected_days = 0
        while collected_days < 30:
            if day_cursor.weekday() < 5:  # Mon-Fri
                for h in [9, 11, 13, 15]:  # 2h blocks starting at 9,11,13,15
                    start_dt = datetime.combine(day_cursor, time(hour=h))
                    end_dt = start_dt + timedelta(hours=2)
                    if start_dt >= datetime.utcnow():
                        windows.append({
                            "date": day_cursor.isoformat(),
                            "window": f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}",
                        })
                collected_days += 1
            day_cursor += timedelta(days=1)
        return windows

    # @function_tool()
    # async def find_available_windows(
    #     self,
    #     context: RunContext,
    #     preferred_date: Annotated[Optional[str], Field(description="Preferred YYYY-MM-DD")] = None,
    # ) -> str:
    #     """Return earliest available 2-hour windows starting from preferred_date (if provided)."""
    #     wins = self._generate_windows(preferred_date)
    #     top = wins[:6]
    #     self._last_suggested = top  # type: ignore[attr-defined]
    #     return yaml.dump({"suggested_windows": top}, sort_keys=False)

    @function_tool()
    async def choose_window(
        self,
        context: RunContext,
        date: Annotated[str, Field(description="Chosen date YYYY-MM-DD from suggestions or custom")],
        window: Annotated[str, Field(description='Time window in HH:MM-HH:MM (e.g., "11:00-13:00")')],
    ) -> str:
        userdata: UserData = context.userdata
        userdata.appointment_date = date
        userdata.appointment_window = window
        return f"Selected window: {date} {window}"

    @function_tool()
    async def confirm_appointment(self, context: RunContext) -> str | tuple[Agent, str]:
        """Confirm the appointment after address and window are set."""
        u: UserData = context.userdata
        missing = []
        if not u.street or not u.city or not u.state or not u.postal_code:
            missing.append("address")
        if not u.appointment_date or not u.appointment_window:
            missing.append("date/window")
        if not u.customer_name:
            missing.append("name")
        if not u.customer_phone:
            missing.append("phone")
        if missing:
            return f"Missing required info: {', '.join(missing)}. Please provide these first."

        u.appointment_id = str(uuid.uuid4())[:8]
        u.appointment_status = "scheduled"
        return await self._transfer_to_agent("router", context)


# -------------------
# Reschedule agent
# -------------------
class Reschedule(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a rescheduling agent. Verify appointment exists, offer new windows (reuse booking windows), then update and confirm."
            ),
            tools=[to_router,
                   read_meeting, get_available_times, update_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova"),
        )
        self._booking_helper = Booking()

    @function_tool()
    async def offer_new_windows(
        self,
        context: RunContext,
        preferred_date: Annotated[Optional[str], Field(description="Preferred YYYY-MM-DD")] = None,
    ) -> str:
        u: UserData = context.userdata
        if not u.appointment_id or u.appointment_status in {"canceled", None}:
            return "No active appointment found to reschedule."
        wins = self._booking_helper._generate_windows(preferred_date)
        return yaml.dump({"new_windows": wins[:6]}, sort_keys=False)

    @function_tool()
    async def apply_new_window(
        self,
        context: RunContext,
        date: Annotated[str, Field(description="New date YYYY-MM-DD")],
        window: Annotated[str, Field(description="New time window HH:MM-HH:MM")],
    ) -> str:
        u: UserData = context.userdata
        if not u.appointment_id:
            return "No appointment found."
        u.appointment_date = date
        u.appointment_window = window
        u.appointment_status = "scheduled"
        return f"Rescheduled to {date} {window}"

    @function_tool()
    async def confirm_reschedule(self, context: RunContext) -> str | tuple[Agent, str]:
        u: UserData = context.userdata
        if not u.appointment_id or not u.appointment_date or not u.appointment_window:
            return "Missing appointment details."
        return await self._transfer_to_agent("router", context)


# -------------------
# Cancel agent
# -------------------
class Cancel(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a cancellation agent. Confirm identity and appointment, then cancel upon user confirmation."
            ),
            tools=[to_router,
                #    new
                   read_meeting, cancel_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova")  ,
        )

    @function_tool()
    async def cancel_appointment(self, context: RunContext) -> str | tuple[Agent, str]:
        u: UserData = context.userdata
        if not u.appointment_id or u.appointment_status in {None, "canceled"}:
            return "No active appointment to cancel."
        u.appointment_status = "canceled"
        return await self._transfer_to_agent("router", context)


# -------------------
# Parts & Products agent
# -------------------
class Parts(BaseAgent):
    CATALOGUE = {
        "FCT-001": {"name": "Kitchen faucet", "price": 120.0},
        "FLT-002": {"name": "Toilet fill valve", "price": 35.0},
        "TRP-001": {"name": "P-trap (1-1/2\" ABS)", "price": 14.0},
        "WH-40": {"name": "40-gal water heater", "price": 950.0},
        "SPLY-PEX": {"name": "PEX supply line", "price": 9.0},
        "P-PTFE": {"name": "PTFE tape", "price": 4.0},
    }

    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a parts agent. Help customers add/remove parts the plumber should bring. Keep a running total. Encourage checkout if prepayment is required."
            ),
            tools=[to_router],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="alloy")  ,
        )

    def _recalc_total(self, u: UserData) -> None:
        u.cart_total = round(sum(item["qty"] * item["unit_price"] for item in u.cart), 2)

    @function_tool()
    async def list_catalogue(self, context: RunContext) -> str:
        cats = [{"sku": sku, **info} for sku, info in self.CATALOGUE.items()]
        return yaml.dump({"catalogue": cats}, sort_keys=False)

    @function_tool()
    async def add_part(
        self,
        context: RunContext,
        sku: Annotated[str, Field(description="SKU from catalogue (e.g., FCT-001)")],
        qty: Annotated[int, Field(description="Quantity", ge=1)] = 1,
    ) -> str:
        u: UserData = context.userdata
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
    async def remove_part(
        self,
        context: RunContext,
        sku: Annotated[str, Field(description="SKU to remove")],
    ) -> str:
        u: UserData = context.userdata
        before = len(u.cart)
        u.cart = [i for i in u.cart if i["sku"] != sku]
        if len(u.cart) == before:
            return f"SKU {sku} not in cart."
        self._recalc_total(u)
        return f"Removed {sku}. Cart total=${u.cart_total:.2f}"

    @function_tool()
    async def view_cart(self, context: RunContext) -> str:
        u: UserData = context.userdata
        return yaml.dump({"cart": u.cart, "total": u.cart_total}, sort_keys=False)


# -------------------
# Status / ETA agent
# -------------------
class Status(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a status agent. If the appointment is today, give an ETA within the chosen window. Otherwise, remind the scheduled date/window."
            ),
            tools=[to_router,
                   read_meeting],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="verse")  ,
        )

    @function_tool()
    async def check_status(self, context: RunContext) -> str:
        u: UserData = context.userdata
        if not u.appointment_id or not u.appointment_date or not u.appointment_window:
            return "No appointment found."
        today = datetime.utcnow().date().isoformat()
        if u.appointment_status == "canceled":
            return f"Appointment {u.appointment_id} is canceled."
        if u.appointment_date == today:
            # Simulated live ETA
            return (
                f"Technician scheduled today between {u.appointment_window}. "
                f"Current status: en route window. ETA within 30–90 minutes depending on traffic."
            )
        return f"Appointment {u.appointment_id} is scheduled on {u.appointment_date} at {u.appointment_window}."


# -------------------
# Pricing / Estimate agent
# -------------------
class Pricing(BaseAgent):
    ISSUE_TABLE = [
        {"keywords": ["clog", "drain", "toilet"], "low": 120, "high": 250},
        {"keywords": ["leak", "pipe", "burst"], "low": 180, "high": 450},
        {"keywords": ["water heater", "no hot", "heater"], "low": 250, "high": 1200},
        {"keywords": ["faucet", "tap", "install"], "low": 90, "high": 220},
        {"keywords": ["garbage disposal", "disposal"], "low": 150, "high": 350},
    ]

    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a pricing agent. Provide rough ranges only (not binding). "
                "If problem description exists, infer the best range; otherwise ask a couple of clarifying questions. "
                "Always recommend an on-site diagnosis for a firm quote."
            ),
            tools=[to_router],
            tts=cartesia.TTS(voice=voices["pricing"]),
        )

    @function_tool()
    async def get_estimate(self, context: RunContext) -> str:
        u: UserData = context.userdata
        desc = (u.problem_description or "").lower()
        low, high = 110, 400  # fallback generic
        for row in self.ISSUE_TABLE:
            if any(k in desc for k in row["keywords"]):
                low, high = row["low"], row["high"]
                break
        u.estimate_low, u.estimate_high = float(low), float(high)
        return f"Estimated range: ${low:.0f}–${high:.0f} (labor + standard parts; taxes extra)."


# -------------------
# Billing / Checkout agent
# -------------------
class Billing(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a billing agent. Confirm amount (deposit or cart total), then collect card number, expiry, and CVV step by step. "
                "Acknowledge last four only when repeating numbers back."
            ),
            tools=[update_name, update_phone, update_email, to_router],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="verse")  ,
        )

    @function_tool()
    async def confirm_amount(
        self,
        context: RunContext,
        amount: Annotated[float, Field(description="Amount to authorize (e.g., deposit or parts total)")],
    ) -> str:
        u: UserData = context.userdata
        u.amount_authorized = float(max(0.0, amount))
        return f"Amount set to ${u.amount_authorized:.2f}"

    @function_tool()
    async def update_card(
        self,
        context: RunContext,
        number: Annotated[str, Field(description="Credit card number (confirm carefully; mask in confirmations)")],
        expiry: Annotated[str, Field(description="Expiry MM/YY or MM/YYYY")],
        cvv: Annotated[str, Field(description="CVV")],
    ) -> str:
        u: UserData = context.userdata
        u.card_number = number
        u.card_expiry = expiry
        u.card_cvv = cvv
        tail = number[-4:] if number else "****"
        return f"Card ending {tail} stored for authorization."

    @function_tool()
    async def authorize_payment(self, context: RunContext) -> str | tuple[Agent, str]:
        u: UserData = context.userdata
        if not (u.amount_authorized and u.card_number and u.card_expiry and u.card_cvv):
            return "Missing amount or card details."
        # Simulate success; reset amount
        u.amount_authorized = 0.0
        return await self._transfer_to_agent("router", context)


# -------------------
# Operator (catch‑all) agent
# -------------------
class Operator(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are an operator for miscellaneous requests: warranty, invoices, landlord approvals, etc. "
                "Gather details and route back to Router if caller changes tasks."
            ),
            tools=[to_router],
            tts=openai.TTS(model="gpt-4o-mini-tts", voice="nova")  ,
        )


# -------------------
# Entrypoint wiring
# -------------------
async def entrypoint(ctx: JobContext):
    userdata = UserData()

    userdata.agents.update({
        "router": Router(),
        "booking": Booking(),
        "reschedule": Reschedule(),
        "cancel": Cancel(),
        "parts": Parts(),
        "status": Status(),
        "pricing": Pricing(),
        "billing": Billing(),
        "operator": Operator(),
    })

    session = AgentSession[UserData](
        userdata=userdata,
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        # tts=cartesia.TTS(),
        tts=openai.TTS(model="gpt-4o-mini-tts", voice="ash"),
        vad=silero.VAD.load(),
        max_tool_steps=6,
        # To use OpenAI Realtime, replace the models accordingly:
        # llm=openai.realtime.RealtimeModel(voice="alloy"),
    )

    await session.start(
        agent=userdata.agents["router"],
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Example: enable noise cancellation if available
            # noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Optionally greet:
    # await session.current_agent.say("Thanks for calling ClearFlow Plumbing. How can we help today?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
