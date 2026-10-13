"""Natural date-range parsing for time-based memory recall. Stdlib only.

Understands the way people ask about past work:

    split_query("what did we complete yesterday")
    -> ("complete", (1692057600.0, 1692144000.0))

Ranges are UTC [start, end) epoch seconds, consistent with
store.iso_date (which formats in UTC). Unrecognized text yields None,
leaving plain lexical recall unchanged.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone

from .store import iso_date

DAY = 86400

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
}

ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_WD = "|".join(WEEKDAYS)
_MO = "|".join(sorted(MONTHS, key=len, reverse=True))

# Leading question scaffolding stripped so the lexical remainder can match
# ("what did we complete yesterday" -> "complete").
_SCAFFOLD_RES = [
    re.compile(r"^(what\s+(did|have)\s+(we|i|you)\s+)", re.I),
    re.compile(r"^(show\s+me|tell\s+me|remind\s+me(\s+of)?|give\s+me|list)\s+", re.I),
]


def _at(ts: float | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ts, timezone.utc)


def _day_range(day: datetime) -> tuple[float, float]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _month_range(year: int, month: int) -> tuple[float, float]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month == 12), month % 12 + 1, 1,
                   tzinfo=timezone.utc)
    return start.timestamp(), end.timestamp()


def _nth_weekday_of_month(year: int, month: int, weekday: int,
                          n: int) -> datetime:
    """n=1..4 for first..fourth, n=-1 for the last such weekday."""
    if n == -1:
        day = datetime(year + (month == 12), month % 12 + 1, 1,
                       tzinfo=timezone.utc) - timedelta(days=1)
        while day.weekday() != weekday:
            day -= timedelta(days=1)
        return day
    day = datetime(year, month, 1, tzinfo=timezone.utc)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day + timedelta(weeks=n - 1)


def _default_year(start_ts: float, now_ts: float) -> float:
    """Roll a year-less date back one year when it would land in the future."""
    if start_ts > now_ts:
        day = datetime.fromtimestamp(start_ts, timezone.utc)
        try:
            return day.replace(year=day.year - 1).timestamp()
        except ValueError:  # Feb 29 on a non-leap year
            return (day - timedelta(days=1)).replace(year=day.year - 1).timestamp()
    return start_ts


def match_range(text: str, now: float | None = None,
                ) -> tuple[tuple[float, float], tuple[int, int]] | None:
    """Return ((start, end), (match_start, match_end)) for the first date
    phrase in text, or None when there is no recognizable date.

    Relative weekdays resolve to the most recent past ("this Friday" on a
    Sunday is the Friday just gone); recall only looks backwards."""
    ref = _at(now)
    now_ts = ref.timestamp()
    today = ref.replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.search(r"\bday before yesterday\b", text, re.I)
    if m:
        return _day_range(today - timedelta(days=2)), m.span()

    m = re.search(r"\byesterday\b", text, re.I)
    if m:
        return _day_range(today - timedelta(days=1)), m.span()

    m = re.search(r"\btoday\b", text, re.I)
    if m:
        return _day_range(today), m.span()

    m = re.search(rf"\b(first|second|third|fourth|last)\s+({_WD})\s+of\s+({_MO})\b(?:\s+(\d{{4}}))?",
                  text, re.I)
    if m:
        ordinal, weekday, month = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
        year = int(m.group(4)) if m.group(4) else ref.year
        n = -1 if ordinal == "last" else ORDINAL_WORDS[ordinal]
        day = _nth_weekday_of_month(year, MONTHS[month], WEEKDAYS[weekday], n)
        start, end = _day_range(day)
        if m.group(4) is None:
            start = _default_year(start, now_ts)
            end = start + DAY
        return (start, end), m.span()

    m = re.search(rf"\b(last|this)\s+({_WD})\b", text, re.I)
    if m:
        qualifier, weekday = m.group(1).lower(), WEEKDAYS[m.group(2).lower()]
        back = (today.weekday() - weekday) % 7
        if qualifier == "last" and back == 0:
            back = 7
        return _day_range(today - timedelta(days=back)), m.span()

    m = re.search(r"\b(last|this)\s+(week|month|year)\b", text, re.I)
    if m:
        qualifier, period = m.group(1).lower(), m.group(2).lower()
        if period == "week":
            monday = today - timedelta(days=today.weekday())
            if qualifier == "last":
                monday -= timedelta(weeks=1)
            start = monday.timestamp()
            return (start, start + 7 * DAY), m.span()
        if period == "month":
            year, month = ref.year, ref.month
            if qualifier == "last":
                month -= 1
                if month == 0:
                    month, year = 12, year - 1
            return _month_range(year, month), m.span()
        year = ref.year - (qualifier == "last")
        start = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        return (start, datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()), m.span()

    m = re.search(rf"\b({_MO})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,?\s*(\d{{4}}))?",
                  text, re.I)
    if m:
        month, day = MONTHS[m.group(1).lower()], int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref.year
        try:
            start, _ = _day_range(datetime(year, month, day,
                                           tzinfo=timezone.utc))
        except ValueError:
            pass  # impossible day (Feb 30): try the remaining patterns
        else:
            if m.group(3) is None:
                start = _default_year(start, now_ts)
            return (start, start + DAY), m.span()

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            start, _ = _day_range(datetime(int(m.group(1)), int(m.group(2)),
                                           int(m.group(3)),
                                           tzinfo=timezone.utc))
        except ValueError:
            pass  # impossible date: try the remaining patterns
        else:
            return (start, start + DAY), m.span()

    m = re.search(rf"\b({_MO})\b(?:\s*,?\s*(\d{{4}}))?", text, re.I)
    # Bare "may"/"march" are usually verbs ("how may we deploy"), so only
    # a year makes them an unambiguous month reference.
    if m and not (m.group(1).lower() in ("may", "march")
                  and m.group(2) is None):
        month = MONTHS[m.group(1).lower()]
        year = int(m.group(2)) if m.group(2) else ref.year
        start, end = _month_range(year, month)
        if m.group(2) is None:
            start = _default_year(start, now_ts)
            end = _month_range(datetime.fromtimestamp(
                start, timezone.utc).year, month)[1]
        return (start, end), m.span()

    m = re.search(r"\b(\d{4})-(\d{2})\b", text)
    if m:
        try:
            return _month_range(int(m.group(1)), int(m.group(2))), m.span()
        except ValueError:
            return None

    m = re.search(r"\blast\s+(\d{1,3})\s+(days?|weeks?|months?)\b", text, re.I)
    if m:
        count, unit = int(m.group(1)), m.group(2).lower()
        if unit.startswith("month"):
            year, month = ref.year, ref.month - count
            while month <= 0:
                month += 12
                year -= 1
            last_day = calendar.monthrange(year, month)[1]
            start = datetime(year, month, min(ref.day, last_day),
                             tzinfo=timezone.utc).timestamp()
            return (start, now_ts), m.span()
        span = count * (7 * DAY if unit.startswith("week") else DAY)
        return (now_ts - span, now_ts), m.span()

    return None


def parse_range(text: str, now: float | None = None,
                ) -> tuple[float, float] | None:
    """Return the (start, end) epoch range for the first date phrase in text."""
    hit = match_range(text, now)
    return hit[0] if hit else None


def split_query(query: str, now: float | None = None,
                ) -> tuple[str, tuple[float, float] | None]:
    """Split a recall query into (lexical remainder, date range|None).

    The date phrase is removed so it cannot pollute lexical scoring, and
    leading question scaffolding ("what did we", "show me") is stripped.
    """
    hit = match_range(query, now)
    cleaned = query
    rng = None
    if hit:
        (start, end), (s, e) = hit
        rng = (start, end)
        cleaned = (query[:s] + " " + query[e:]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?")
    prev = None
    while prev != cleaned:
        prev = cleaned
        for pattern in _SCAFFOLD_RES:
            cleaned = pattern.sub("", cleaned).strip()
    return cleaned, rng


def parse_day(text: str) -> tuple[float, float] | None:
    """Parse an explicit YYYY-MM-DD bound (CLI --since/--until, MCP params)."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", text)
    if not m:
        return None
    try:
        start, _ = _day_range(datetime(int(m.group(1)), int(m.group(2)),
                                       int(m.group(3)), tzinfo=timezone.utc))
    except ValueError:
        return None
    return start, start + DAY


def label(since: float | None, until: float | None) -> str:
    """Short human label for a range, for recall headers and answers."""
    if since is None and until is None:
        return ""
    if since is not None and until is not None and until - since <= DAY + 1:
        return iso_date(since)
    parts = [iso_date(since) if since else "…"]
    if until:
        parts.append(iso_date(until - 1))
    return "..".join(parts)
