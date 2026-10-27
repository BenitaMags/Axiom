# CHOSEN-TARGET: first-party
"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the

tolerant of the trailing 'Z' zulu suffix).
"""
import re

_TZ_RE = re.compile(r"([+-])(\d{2}):(\d{2})(?::(\d{2}))?$")


class timezone:
    """Minimal stand-in for datetime.timezone."""

    def __init__(self, offset_seconds: int, name: str = None):
        self._offset_seconds = offset_seconds
        self._name = name

    @property
    def offset_seconds(self) -> int:
        return self._offset_seconds

    def __eq__(self, other):
        return isinstance(other, timezone) and self._offset_seconds == other._offset_seconds

    def __repr__(self):
        return f"timezone(offset_seconds={self._offset_seconds})"


timezone.utc = timezone(0, "UTC")


class datetime:
    """Minimal stand-in for datetime.datetime supporting what this module needs."""

    def __init__(self, year, month, day, hour=0, minute=0, second=0,
                 microsecond=0, tzinfo=None):
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self.minute = minute
        self.second = second
        self.microsecond = microsecond
        self.tzinfo = tzinfo

    def replace(self, tzinfo=None):
        return datetime(
            self.year, self.month, self.day,
            self.hour, self.minute, self.second, self.microsecond,
            tzinfo=tzinfo if tzinfo is not None else self.tzinfo,
        )

    def _is_leap(self, year):
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

    def _days_before_year(self, year):
        y = year - 1
        return y * 365 + y // 4 - y // 100 + y // 400

    _DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    def _days_before_month(self, year, month):
        days = 0
        for m in range(1, month):
            if m == 2 and self._is_leap(year):
                days += 29
            else:
                days += self._DAYS_IN_MONTH[m]
        return days

    def _to_ordinal(self):
        return (
            self._days_before_year(self.year)
            + self._days_before_month(self.year, self.month)
            + self.day
        )

    def timestamp(self) -> float:
        # Days since epoch (1970-01-01)
        epoch_ordinal = self._days_before_year(1970) + self._days_before_month(1970, 1) + 1
        days = self._to_ordinal() - epoch_ordinal
        seconds = days * 86400 + self.hour * 3600 + self.minute * 60 + self.second
        total = seconds + self.microsecond / 1_000_000

        if self.tzinfo is not None:
            total -= self.tzinfo.offset_seconds
        return float(total)

    def __repr__(self):
        return (
            f"datetime({self.year}, {self.month}, {self.day}, "
            f"{self.hour}, {self.minute}, {self.second}, {self.microsecond}, "
            f"tzinfo={self.tzinfo!r})"
        )

    def __eq__(self, other):
        if not isinstance(other, datetime):
            return NotImplemented
        return (
            self.year, self.month, self.day, self.hour, self.minute,
            self.second, self.microsecond, self.tzinfo,
        ) == (
            other.year, other.month, other.day, other.hour, other.minute,
            other.second, other.microsecond, other.tzinfo,
        )

    @classmethod
    def fromisoformat(cls, text: str) -> "datetime":
        tzinfo = None
        s = text

        m = _TZ_RE.search(s)
        if m:
            sign, hh, mm, ss = m.groups()
            offset = int(hh) * 3600 + int(mm) * 60 + (int(ss) if ss else 0)
            if sign == "-":
                offset = -offset
            tzinfo = timezone(offset)
            s = s[: m.start()]

        if "T" in s:
            date_part, time_part = s.split("T", 1)
        elif " " in s:
            date_part, time_part = s.split(" ", 1)
        else:
            date_part, time_part = s, "00:00:00"

        year, month, day = (int(p) for p in date_part.split("-"))

        hour = minute = second = 0
        microsecond = 0
        if time_part:
            time_main = time_part
            if "." in time_main:
                time_main, frac = time_main.split(".", 1)
                frac = (frac + "000000")[:6]
                microsecond = int(frac)

            parts = time_main.split(":")
            if len(parts) >= 1 and parts[0] != "":
                hour = int(parts[0])
            if len(parts) >= 2:
                minute = int(parts[1])
            if len(parts) >= 3:
                second = int(parts[2])

        return cls(year, month, day, hour, minute, second, microsecond, tzinfo=tzinfo)


def _normalize(text: str) -> str:

    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = datetime.fromisoformat(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
