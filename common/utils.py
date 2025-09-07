## common/utils.py`

import logging
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Union

from db.models import AppointmentStatus, RequestPriority

logger = logging.getLogger("plumber-contact-center")


def _dt_utc(s: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """
    Parse s into a timezone-aware UTC datetime.
    Accepts:
      - datetime (naive or tz-aware)
      - ISO strings (with or without 'Z')
      - 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD HH:MM:SS'
    """
    if s is None or s == "":
        return None

    # If already a datetime, normalize to UTC
    if isinstance(s, datetime):
        dt = s
    else:
        s2 = str(s).strip()
        # Normalize trailing 'Z' to +00:00 for fromisoformat
        if s2.endswith("Z"):
            s2 = s2[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s2)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s2, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"Unparseable datetime: {s!r}")

    # If naive, assume UTC; otherwise convert to UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# def _dt_utc(s: Optional[Union[str, datetime]]) -> Optional[datetime]:
#     """
#     Parse s into a timezone-aware UTC datetime.
#     Accepts:
#       - datetime (naive or tz-aware)
#       - ISO strings (with or without 'Z')
#       - 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD HH:MM:SS'
#     """
#     if s is None or s == "":
#         return None

#     # If already a datetime, normalize to UTC
#     if isinstance(s, datetime):
#         dt = s
#     else:
#         s2 = str(s).strip()
#         # Normalize trailing 'Z' to +00:00 for fromisoformat
#         if s2.endswith("Z"):
#             s2 = s2[:-1] + "+00:00"
#         try:
#             dt = datetime.fromisoformat(s2)
#         except ValueError:
#             for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
#                 try:
#                     dt = datetime.strptime(s2, fmt)
#                     break
#                 except ValueError:
#                     continue
#             else:
#                 raise ValueError(f"Unparseable datetime: {s!r}")

#     # If naive, assume UTC; otherwise convert to UTC
#     if dt.tzinfo is None:
#         dt = dt.replace(tzinfo=timezone.utc)
#     return dt.astimezone(timezone.utc)


def _time_of(daytime: str) -> time:
    return datetime.strptime(daytime, "%H:%M").time()


_PRIO: Dict[str, RequestPriority] = {
    "P1": RequestPriority.P1,
    "P2": RequestPriority.P2,
    "P3": RequestPriority.P3,
}

_STATUS: Dict[str, AppointmentStatus] = {
    "scheduled": AppointmentStatus.scheduled,
    "en_route": getattr(AppointmentStatus, "en_route", AppointmentStatus.scheduled),
    "complete": getattr(AppointmentStatus, "complete", AppointmentStatus.scheduled),
    "canceled": AppointmentStatus.canceled,
}


def _parse_window_to_utc(date_str: str, window: str) -> tuple[datetime, datetime]:
    # window="HH:MM-HH:MM"
    try:
        start_s, end_s = window.split("-")
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        s_local = datetime.combine(d, datetime.strptime(start_s, "%H:%M").time())
        e_local = datetime.combine(d, datetime.strptime(end_s, "%H:%M").time())
    except Exception as e:
        raise ValueError(f"Invalid date/window. Expected YYYY-MM-DD and HH:MM-HH:MM. Error: {e}")
    s = s_local.replace(tzinfo=timezone.utc)
    e = e_local.replace(tzinfo=timezone.utc)
    return s, e


__all__ = [
    "_dt_utc",
    "_time_of",
    "_parse_window_to_utc",
    "_PRIO",
    "_STATUS",
    "ZoneInfo",
]