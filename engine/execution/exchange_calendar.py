"""US equity exchange calendar: holidays, weekends and early closes, computed deterministically.

Session truth was previously blocked because ``SessionCalendar`` requires an owner-supplied,
provenance-tagged calendar; with none, ``SESSION_TRUTH_UNKNOWN`` refused all new exposure forever.
The rules of a published exchange holiday schedule are public, fixed facts, so they belong in code
rather than in an operator's clipboard.

This module computes the NYSE/Nasdaq cash-equity schedule for a stated year range:

* weekends, and the 10 federal-style market holidays, with weekend observance shifted to the
  preceding Friday;
* 1pm early closes (13:00 America/New_York) on the afternoon before Independence Day and
  Christmas when those fall on a weekday, and the day after Thanksgiving;
* half-day observance for holidays falling on a Saturday, which the exchange does not trade.

What this is **not**: it is a calendar computation, not an exchange feed. It is correct for the
cash-equity session and says so in its provenance string. A deployment that needs a different
venue, an unscheduled closure, or an exchange halt must supply its own calendar and this one must
not be used to override it. ``provenance`` carries that limitation into every verdict.

No capital value and no owner decision is encoded here: a calendar says when the market is open, not
whether Trip's may trade.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, Set, Tuple

PROVENANCE = ("computed US cash-equity exchange schedule (NYSE/Nasdaq rules), not a live exchange "
              "feed; unscheduled closures and halts are not represented and must be supplied by a "
              "venue-specific calendar")

#: 1pm early close, America/New_York.
EARLY_CLOSE_HOUR = 13


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (0=Mon) of a month, 1-indexed."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c, d, e = year % 19, year // 100, year % 100, year // 4, year % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = (h + ell - 7 * m + 114) // 31, ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(day: date) -> date:
    """Saturday closures are taken on the preceding Friday, Sunday on the following Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def market_holidays(year: int) -> Set[date]:
    """Full-day market closures for a calendar year."""
    easter = _easter(year)
    return {
        _observed(date(year, 1, 1)),                       # New Year's Day
        _nth_weekday(year, 1, 0, 3),                      # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                      # Washington's Birthday
        easter - timedelta(days=2),                       # Good Friday
        _last_weekday(year, 5, 0),                        # Memorial Day
        _observed(date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                      # Labor Day
        _nth_weekday(year, 11, 3, 4),                     # Thanksgiving
        _observed(date(year, 12, 25)),                    # Christmas
    } | ({_observed(date(year, 6, 19))} if year >= 2022 else set())  # Juneteenth


def early_closes(year: int) -> Set[date]:
    """Sessions that close at 1pm America/New_York."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),                 # day after Thanksgiving
        date(year, 12, 24),                               # Christmas Eve
        date(year, 7, 3),                                 # day before Independence Day
    }
    return {day for day in candidates if day.weekday() < 5} - market_holidays(year)


def calendar_for_years(years: Iterable[int]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return ``(holidays, early_closes)`` keyed by ISO date, ready for ``SessionCalendar``."""
    holidays: Dict[str, str] = {}
    closes: Dict[str, str] = {}
    for year in sorted(set(years)):
        for day in sorted(market_holidays(year)):
            holidays.setdefault(day.isoformat(), "US equity market holiday (full close)")
        for day in sorted(early_closes(year)):
            closes.setdefault(day.isoformat(), f"{EARLY_CLOSE_HOUR:02d}:00")
    return holidays, closes


def default_us_equity_calendar(start_year: int = 2025, end_year: int = 2027):
    """A ready-to-use :class:`~execution.session.SessionCalendar` for the US cash session.

    Bound by a validity window. Outside it, session truth is UNKNOWN rather than assumed, which is
    the fail-closed behaviour: an unbounded calendar would silently claim to describe 1970.
    """
    from .session import SessionCalendar

    holidays, closes = calendar_for_years(range(start_year, end_year + 1))
    return SessionCalendar(provenance=PROVENANCE,
                           holidays=holidays,
                           early_closes=closes,
                           valid_from=f"{start_year}-01-01",
                           valid_through=f"{end_year}-12-31")
