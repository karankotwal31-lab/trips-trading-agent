"""Market session truth.

Trip's may only create new exposure inside a proven market session, and the exchange calendar
is an external dependency that does not exist in this repository yet. Inventing holiday dates
would be fabricating market facts, so this module REQUIRES an owner-supplied, provenance-tagged
calendar and returns ``UNKNOWN`` — which blocks new exposure — for anything it cannot prove.

Timezone conversion is delegated to ``zoneinfo`` so DST transitions and US/Eastern offsets are
handled by the tz database rather than by arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

DEFAULT_SESSION_TZ = "America/New_York"


class SessionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SessionVerdict:
    status: SessionStatus
    code: str
    reasons: Tuple[str, ...]
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def permits_new_exposure(self) -> bool:
        return self.status is SessionStatus.OPEN

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "code": self.code, "reasons": list(self.reasons),
                "permits_new_exposure": self.permits_new_exposure, "detail": dict(self.detail)}


class SessionCalendarError(RuntimeError):
    """The calendar definition itself is unusable."""


class SessionCalendar:
    """Deterministic US equity session evaluation.

    ``holidays`` maps ISO dates to a reason; ``early_closes`` maps ISO dates to a local
    ``HH:MM`` close time. ``provenance`` must describe where the calendar came from.
    ``valid_from`` / ``valid_through`` bound the calendar's authority; outside that window the
    verdict is UNKNOWN rather than a guess.
    """

    def __init__(self, *, provenance: str = "", timezone_name: str = DEFAULT_SESSION_TZ,
                 open_time: str = "09:30", close_time: str = "16:00",
                 holidays: Optional[Mapping[str, str]] = None,
                 early_closes: Optional[Mapping[str, str]] = None,
                 valid_from: Optional[str] = None, valid_through: Optional[str] = None) -> None:
        self.provenance = (provenance or "").strip()
        self.timezone_name = timezone_name
        self.open_time = _parse_hhmm(open_time, "open_time")
        self.close_time = _parse_hhmm(close_time, "close_time")
        self.holidays = dict(holidays or {})
        self.early_closes = dict(early_closes or {})
        self.valid_from = _parse_date(valid_from, "valid_from")
        self.valid_through = _parse_date(valid_through, "valid_through")
        try:
            self._tz = ZoneInfo(timezone_name)
        except Exception as exc:
            raise SessionCalendarError(f"unrecognized session timezone {timezone_name!r}") from exc

    def describe(self) -> Dict[str, Any]:
        return {
            "provenance": self.provenance,
            "timezone": self.timezone_name,
            "open": self.open_time.strftime("%H:%M"),
            "close": self.close_time.strftime("%H:%M"),
            "holiday_count": len(self.holidays),
            "early_close_count": len(self.early_closes),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_through": self.valid_through.isoformat() if self.valid_through else None,
            "calendar_supplied": bool(self.holidays) and bool(self.provenance),
        }

    def _unknown(self, reason: str) -> SessionVerdict:
        return SessionVerdict(SessionStatus.UNKNOWN, "SESSION_TRUTH_UNKNOWN", (reason,),
                              {"calendar": self.describe()})

    def evaluate(self, at: datetime) -> SessionVerdict:
        if at.tzinfo is None:
            return self._unknown("evaluation time must be timezone-aware")
        if not self.provenance:
            return self._unknown("no provenance-tagged exchange calendar is configured")
        if not self.holidays:
            return self._unknown("exchange calendar has no supplied holiday set")
        if self.valid_from is None or self.valid_through is None:
            return self._unknown("exchange calendar has no declared validity window")

        local = at.astimezone(self._tz)
        today = local.date()
        if today < self.valid_from or today > self.valid_through:
            return self._unknown(
                f"date {today.isoformat()} is outside the calendar validity window "
                f"{self.valid_from.isoformat()}..{self.valid_through.isoformat()}")

        detail = {"local_time": local.isoformat(), "session_date": today.isoformat(),
                  "timezone": self.timezone_name}

        if today.weekday() >= 5:
            return SessionVerdict(SessionStatus.CLOSED, "SESSION_WEEKEND",
                                  (f"{today.isoformat()} is a weekend",), detail)
        if today.isoformat() in self.holidays:
            return SessionVerdict(SessionStatus.CLOSED, "SESSION_HOLIDAY",
                                  (f"{today.isoformat()} is a market holiday: "
                                   f"{self.holidays[today.isoformat()]}",), detail)

        close = self.close_time
        if today.isoformat() in self.early_closes:
            close = _parse_hhmm(self.early_closes[today.isoformat()], "early close")
            detail["early_close"] = close.strftime("%H:%M")

        opened = datetime.combine(today, self.open_time, tzinfo=self._tz)
        closed = datetime.combine(today, close, tzinfo=self._tz)
        detail["session_open_local"] = opened.isoformat()
        detail["session_close_local"] = closed.isoformat()
        detail["session_open_utc"] = opened.astimezone(timezone.utc).isoformat()
        detail["session_close_utc"] = closed.astimezone(timezone.utc).isoformat()

        if local < opened:
            return SessionVerdict(SessionStatus.CLOSED, "SESSION_NOT_YET_OPEN",
                                  ("pre-open",), detail)
        if local >= closed:
            return SessionVerdict(SessionStatus.CLOSED, "SESSION_AFTER_CLOSE",
                                  ("after close",), detail)
        return SessionVerdict(SessionStatus.OPEN, "SESSION_OPEN", (), detail)


def _parse_hhmm(value: str, name: str) -> time:
    text = str(value).strip()
    if len(text) != 5 or text[2] != ":" or not (text[:2].isdigit() and text[3:].isdigit()):
        raise SessionCalendarError(f"{name} must be HH:MM")
    hour, minute = int(text[:2]), int(text[3:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SessionCalendarError(f"{name} is not a valid time")
    return time(hour=hour, minute=minute)


def _parse_date(value: Optional[str], name: str) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except Exception as exc:
        raise SessionCalendarError(f"{name} must be an ISO date") from exc
