"""Tests for sensor_json_logger (dual-mode)."""
from sensor_json_logger import format_batch, format_reading


def test_canonical_record():
    out = format_reading("PT101", 42.5, "2026-01-01T00:00:00+00:00")
    assert out == '{"quality":"GOOD","tag":"PT101","ts":"2026-01-01T00:00:00+00:00","value":42.5}'


def test_keys_are_sorted_and_compact():
    out = format_reading("A", 1, "t", quality="BAD")
    assert out == '{"quality":"BAD","tag":"A","ts":"t","value":1}'
    assert " " not in out  # compact separators


def test_batch_is_newline_delimited():
    readings = [
        {"tag": "T1", "value": 1, "ts": "t1"},
        {"tag": "T2", "value": 2, "ts": "t2", "quality": "UNCERTAIN"},
    ]
    lines = format_batch(readings).split("\n")
    assert len(lines) == 2
    assert lines[0] == '{"quality":"GOOD","tag":"T1","ts":"t1","value":1}'
    assert '"quality":"UNCERTAIN"' in lines[1]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
