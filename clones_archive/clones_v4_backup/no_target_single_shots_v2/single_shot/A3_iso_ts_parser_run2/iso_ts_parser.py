# CHOSEN-TARGET: first-party
"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the

tolerant of the trailing 'Z' zulu suffix).
"""
import re


class timezone:
    """Minimal stand-in for datetime.timezone, storing a UTC offset."""

    __slots__ = ("_offset_seconds",)

    def __init__(self, offset_seconds: int):
        self._offset_seconds = offset_seconds

    @property
    def offset_seconds(self) -> int:
        return self._offset_seconds

    def __eq__(self, other):
        return isinstance(other, timezone) and self._offset_seconds == other._offset_seconds

    def __hash__(self):
        return hash(self._offset_seconds)

    def __repr__(self):
        sign = "+" if self._offset_seconds >= 0 else "-"
        total = abs(self._offset_seconds)
        hh, rem = divmod(total, 3600)
        mm = rem // 60
        return f"timezone({sign}{hh:02d}:{mm:02d})"


timezone.utc = timezone(0)


_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _days_before_year(year: int) -> int:
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _days_before_month(year: int, month: int) -> int:
    days = 0
    for m in range(1, month):
        days += _DAYS_IN_MONTH[m - 1]
        if m == 2 and _is_leap(year):
            days += 1
    return days


def _to_ordinal(year: int, month: int, day: int) -> int:
    return _days_before_year(year) + _days_before_month(year, month) + day


class datetime:
    """Minimal timezone-aware datetime replacement supporting what this module needs."""

    __slots__ = (
        "year", "month", "day", "hour", "minute", "second", "microsecond", "tzinfo",
    )

    def __init__(self, year, month, day, hour=0, minute=0, second=0, microsecond=0, tzinfo=None):
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self.minute = minute
        self.second = second
        self.microsecond = microsecond
        self.tzinfo = tzinfo

    def replace(self, **kwargs):
        fields = {
            "year": self.year,
            "month": self.month,
            "day": self.day,
            "hour": self.hour,
            "minute": self.minute,
            "second": self.second,
            "microsecond": self.microsecond,
            "tzinfo": self.tzinfo,
        }
        fields.update(kwargs)
        return datetime(**fields)

    def timestamp(self) -> float:
        tz = self.tzinfo if self.tzinfo is not None else timezone.utc
        ordinal = _to_ordinal(self.year, self.month, self.day)
        epoch_ordinal = _to_ordinal(1970, 1, 1)
        days = ordinal - epoch_ordinal
        seconds = (
            days * 86400
            + self.hour * 3600
            + self.minute * 60
            + self.second
            - tz.offset_seconds
        )
        return seconds + self.microsecond / 1_000_000

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

    def __repr__(self):
        return (
            f"datetime({self.year}, {self.month}, {self.day}, {self.hour}, "
            f"{self.minute}, {self.second}, {self.microsecond}, {self.tzinfo!r})"
        )


_TS_RE = re.compile(
    r"""^
    (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
    [T ]
    (?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})
    (?:\.(?P<microsecond>\d+))?
    (?P<tz>[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?
    $""",
    re.VERBOSE,
)


def _parse_offset(tz_str: str) -> timezone:
    sign = 1 if tz_str[0] == "+" else -1
    rest = tz_str[1:]
    parts = rest.split(":")
    hh = int(parts[0])
    mm = int(parts[1]) if len(parts) > 1 else 0
    ss = 0
    if len(parts) > 2:
        ss = int(float(parts[2]))
    total_seconds = sign * (hh * 3600 + mm * 60 + ss)
    return timezone(total_seconds)


def _fromisoformat(text: str) -> datetime:
    match = _TS_RE.match(text)
    if not match:
        raise ValueError(f"Invalid isoformat string: {text!r}")

    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))

    micro_str = match.group("microsecond")
    microsecond = 0
    if micro_str:
        micro_str = (micro_str + "000000")[:6]
        microsecond = int(micro_str)

    tzinfo = None
    tz_str = match.group("tz")
    if tz_str:
        tzinfo = _parse_offset(tz_str)

    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo)


def _normalize(text: str) -> str:

    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _fromisoformat(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
