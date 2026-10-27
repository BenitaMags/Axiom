"""Tests for iso_ts_parser (dual-mode)."""
from datetime import timezone

from iso_ts_parser import parse, to_epoch


def test_naive_defaults_to_utc():
    dt = parse("2026-01-01T00:00:00")
    assert dt.tzinfo == timezone.utc
    assert to_epoch("2026-01-01T00:00:00") == 1767225600.0


def test_explicit_offset():
    # 01:00 at +01:00 is the same instant as 00:00 UTC.
    assert to_epoch("2026-01-01T01:00:00+01:00") == 1767225600.0


def test_zulu_suffix():
    assert to_epoch("2026-01-01T00:00:00Z") == 1767225600.0


def test_fractional_seconds():
    dt = parse("2026-01-01T00:00:00.500+00:00")
    assert dt.microsecond == 500000


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
