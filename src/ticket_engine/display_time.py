"""Display time: how the box shows a moment to the developer.

WHY THIS EXISTS
---------------
Ticket 77 (ADR 0007 rule 4, ADR 0010 rule 5). The developer reads the box status
issue in Central time, and a UTC `2026-10-07T20:11Z` made them do the sum every
time. `format_display` writes `YYYY-MM-DD h:mm AM|PM CDT|CST`; `parse_display`
reads it back to a UTC instant, so the box-silent check, the dispatcher and the
morning report keep working on the same issue body.

Two constraints shaped the code:

- The `ZoneInfo` is built inside `format_display`, never at import. Windows has no
  system time-zone database, so `pyproject.toml` depends on `tzdata` there; a
  missing database must fail the call that needs it, not every import of the engine.
- `parse_display` maps the `CDT`/`CST` suffix to a fixed offset instead of asking
  the zone database. The suffix is the only thing that tells the two 1:30 AMs of
  the autumn switch apart, and the offsets are what the suffix means.
"""
from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

__all__ = ["DISPLAY_TIMEZONE", "format_display", "parse_display"]

DISPLAY_TIMEZONE = "America/Chicago"

_DISPLAY_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) (\d{1,2}):(\d{2}) (AM|PM) (CDT|CST)$"
)
_OFFSET_HOURS = {"CDT": -5, "CST": -6}


def format_display(dt: datetime.datetime) -> str:
    """Format a timezone-aware datetime as `2026-10-07 3:11 PM CDT`."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    local = dt.astimezone(ZoneInfo(DISPLAY_TIMEZONE))
    hour12 = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return (
        f"{local:%Y-%m-%d} {hour12}:{local.minute:02d} {meridiem} {local.tzname()}"
    )


def parse_display(text: str) -> datetime.datetime | None:
    """Parse `format_display` text into a UTC-aware datetime, or None if it is not that."""
    if not isinstance(text, str):
        return None
    match = _DISPLAY_RE.match(text)
    if not match:
        return None
    year, month, day, hour12, minute, meridiem, zone = match.groups()
    hour = int(hour12)
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if meridiem == "PM" else 0)
    offset = datetime.timezone(datetime.timedelta(hours=_OFFSET_HOURS[zone]))
    try:
        local = datetime.datetime(
            int(year), int(month), int(day), hour, int(minute), tzinfo=offset
        )
    except ValueError:
        return None
    return local.astimezone(datetime.UTC)
