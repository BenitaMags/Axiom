# CHOSEN-TARGET: first-party
"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is
first-party and tolerant of the trailing 'Z' zulu suffix.
"""
import re

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

_TS_RE = re.compile(
    r"""^
    (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
    (?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<frac>\d+))?)?)?
    (?P<tz>Z|[+-]\d{2}:?\d{2}(?::?\d{2}(?:\.\d+)?)?)?
    $""",
    re.VERBOSE,
)


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month - 1]


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since epoch (1970-01-01), using the well-known civil_from_days algorithm."""
    a = year - (1 if month <= 2 else 0)
    era = (a if a >= 0 else a - 399) // 400
    yoe = a - era * 400
    mp = (month + 9) % 12
    doy = (153 * mp + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


class timezone:
    """Minimal fixed-offset timezone, API-compatible subset of datetime.timezone."""

    def __init__(self, offset_seconds: int, name: str = None):
        self._offset_seconds = offset_seconds
        self._name = name

    def utcoffset(self):
        return self._offset_seconds

    def __eq__(self, other):
        return isinstance(other, timezone) and self._offset_seconds == other._offset_seconds

    def __repr__(self):
        return f"timezone(offset_seconds={self._offset_seconds})"


timezone.utc = timezone(0, "UTC")


class datetime:
    """Minimal timezone-aware datetime replacement supporting what this module needs."""

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
            self.hour, self.minute, self.second,
            self.microsecond, tzinfo,
        )

    def timestamp(self) -> float:
        days = _days_from_civil(self.year, self.month, self.day)
        seconds = days * 86400 + self.hour * 3600 + self.minute * 60 + self.second
        offset = 0
        if self.tzinfo is not None:
            offset = self.tzinfo.utcoffset()
        total = seconds - offset + self.microsecond / 1_000_000.0
        return float(total)

    def __eq__(self, other):
        if not isinstance(other, datetime):
            return NotImplemented
        return (
            self.year, self.month, self.day, self.hour, self.minute,
            self.second, self.microsecond, self.tzinfo
        ) == (
            other.year, other.month, other.day, other.hour, other.minute,
            other.second, other.microsecond, other.tzinfo
        )

    def __repr__(self):
        return (
            f"datetime({self.year}, {self.month}, {self.day}, "
            f"{self.hour}, {self.minute}, {self.second}, "
            f"{self.microsecond}, tzinfo={self.tzinfo!r})"
        )

    @classmethod
    def fromisoformat(cls, text: str) -> "datetime":
        m = _TS_RE.match(text)
        if not m:
            raise ValueError(f"Invalid isoformat string: {text!r}")

        year = int(m.group("year"))
        month = int(m.group("month"))
        day = int(m.group("day"))

        if not (1 <= month <= 12):
            raise ValueError(f"month must be in 1..12: {text!r}")
        if not (1 <= day <= _days_in_month(year, month)):
            raise ValueError(f"day is out of range for month: {text!r}")

        hour = int(m.group("hour")) if m.group("hour") else 0
        minute = int(m.group("minute")) if m.group("minute") else 0
        second = int(m.group("second")) if m.group("second") else 0

        frac = m.group("frac")
        microsecond = 0
        if frac:
            frac = (frac + "000000")[:6]
            microsecond = int(frac)

        if not (0 <= hour <= 23):
            raise ValueError(f"hour must be in 0..23: {text!r}")
        if not (0 <= minute <= 59):
            raise ValueError(f"minute must be in 0..59: {text!r}")
        if not (0 <= second <= 59):
            raise ValueError(f"second must be in 0..59: {text!r}")

        tz_str = m.group("tz")
        tzinfo = None
        if tz_str:
            if tz_str == "Z":
                tzinfo = timezone.utc
            else:
                sign = 1 if tz_str[0] == "+" else -1
                rest = tz_str[1:]
                rest = rest.replace(":", "")
                tz_hour = int(rest[0:2])
                tz_minute = int(rest[2:4]) if len(rest) >= 4 else 0
                tz_second = int(rest[4:6]) if len(rest) >= 6 else 0
                offset_seconds = sign * (tz_hour * 3600 + tz_minute * 60 + tz_second)
                tzinfo = timezone(offset_seconds)

        return cls(year, month, day, hour, minute, second, microsecond, tzinfo)


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
