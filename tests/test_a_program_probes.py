"""A1-A3 had no probes, so a third of the corpus had no equivalence measure.

`oracle_equiv` was hard-coded NA for category A in every arm, leaving
`gen_success` -- compiles plus tests pass -- as the only signal. Unlike C1 and
C2, the blind spot here is not dependency injection: the A tests call the real
functions, which call the real library. What was missing is input COVERAGE --
5, 3 and 4 test cases against three "drop-in" replacements that are not quite
drop-in.

Each test below pairs a CORRECT migration that must pass with a defective one
that must fail. The pairing is the point: an oracle that rejects everything would
satisfy the negative tests alone and measure nothing.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "equivalence"))

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _oracle(name):
    from oracle import ProgramOracle
    return ProgramOracle(os.path.join(CORPUS, name), name)


def _source(program, module):
    return open(os.path.join(CORPUS, program, f"{module}.py"),
                encoding="utf-8").read()


def _swap(text, old, new):
    """`str.replace` that refuses to no-op -- see test_c1_c2's `_break`."""
    assert old in text, f"fixture pattern no longer matches: {old!r}"
    out = text.replace(old, new)
    assert out != text
    return out


def _check(program, module, source):
    return _oracle(program).check_many({module: source})


# ── A1: zlib.crc32 -> fastcrc ────────────────────────────────────────────────
#
# Only `fastcrc.crc32.iso_hdlc` reproduces `zlib.crc32`. `bzip2` agrees on empty
# and all-ones input and diverges everywhere else, which is the kind of backend
# mix-up a one-value test can miss.

def _a1_with(variant):
    src = _source("A1_crc_telemetry", "crc_telemetry")
    src = _swap(src, "import zlib", "from fastcrc import crc32 as _fc")
    return _swap(src, "return zlib.crc32(payload) & 0xFFFFFFFF",
                 f"return _fc.{variant}(payload) & 0xFFFFFFFF")


def test_a1_accepts_the_matching_fastcrc_variant():
    pytest.importorskip("fastcrc")
    r = _check("A1_crc_telemetry", "crc_telemetry", _a1_with("iso_hdlc"))
    assert r.equivalent, r.detail


def test_a1_rejects_a_different_crc_variant():
    pytest.importorskip("fastcrc")
    r = _check("A1_crc_telemetry", "crc_telemetry", _a1_with("bzip2"))
    assert not r.equivalent
    assert "crc32" in r.detail


def test_a1_rejects_a_dropped_unsigned_mask():
    """`struct.pack(">I", ...)` needs an unsigned value."""
    src = _swap(_source("A1_crc_telemetry", "crc_telemetry"),
                "return zlib.crc32(payload) & 0xFFFFFFFF",
                "return zlib.crc32(payload) - 2**32")
    r = _check("A1_crc_telemetry", "crc_telemetry", src)
    assert not r.equivalent


# ── A2: json -> ujson ────────────────────────────────────────────────────────
#
# `ujson` escapes a forward slash as `\\/` by default. The module's docstring
# makes byte-reproducibility a hard requirement, and a SCADA tag like
# `Plant/Unit1/PT101` contains slashes.

def test_a2_rejects_ujson_with_default_slash_escaping():
    pytest.importorskip("ujson")
    src = _swap(_source("A2_sensor_json_logger", "sensor_json_logger"),
                "import json", "import ujson as json")
    r = _check("A2_sensor_json_logger", "sensor_json_logger", src)
    assert not r.equivalent
    assert "tags" in r.detail or "batch" in r.detail


def test_a2_accepts_ujson_with_slash_escaping_disabled():
    pytest.importorskip("ujson")
    src = _swap(_source("A2_sensor_json_logger", "sensor_json_logger"),
                "import json", "import ujson")
    src = _swap(
        src,
        "return json.dumps(record, sort_keys=True, separators=_SEPARATORS)",
        "return ujson.dumps(record, sort_keys=True, separators=_SEPARATORS,\n"
        "                       escape_forward_slashes=False)")
    r = _check("A2_sensor_json_logger", "sensor_json_logger", src)
    assert r.equivalent, r.detail


def test_a2_rejects_unsorted_keys():
    """Sorted keys are what makes the audit log byte-reproducible."""
    src = _swap(_source("A2_sensor_json_logger", "sensor_json_logger"),
                "sort_keys=True", "sort_keys=False")
    r = _check("A2_sensor_json_logger", "sensor_json_logger", src)
    assert not r.equivalent


# ── A3: datetime.fromisoformat -> ciso8601 ───────────────────────────────────

def _a3_ciso():
    src = _swap(_source("A3_iso_ts_parser", "iso_ts_parser"),
                "from datetime import datetime, timezone",
                "import ciso8601\nfrom datetime import datetime, timezone")
    return _swap(src, "dt = datetime.fromisoformat(_normalize(ts))",
                 "dt = ciso8601.parse_datetime(_normalize(ts))")


def test_a3_accepts_ciso8601():
    """The tolerance difference must NOT be treated as a defect.

    `ciso8601` accepts a lowercase `z` and maps `T24:00:00` to the following
    midnight, where `fromisoformat` raises. ISO 8601 permits both forms, and the
    corpus names Z-tolerance as the motivation for the swap -- so gating on them
    would make A3 unmigratable for something that is not a defect. All fifteen
    timestamps the original accepts parse identically.
    """
    pytest.importorskip("ciso8601")
    r = _check("A3_iso_ts_parser", "iso_ts_parser", _a3_ciso())
    assert r.equivalent, r.detail


def test_a3_rejects_a_parser_that_accepts_malformed_input():
    """Tolerating ISO-legal forms is fine; accepting nonsense is not."""
    src = _swap(_source("A3_iso_ts_parser", "iso_ts_parser"),
                "    dt = datetime.fromisoformat(_normalize(ts))",
                "    try:\n"
                "        dt = datetime.fromisoformat(_normalize(ts))\n"
                "    except ValueError:\n"
                "        dt = datetime(1970, 1, 1)")
    r = _check("A3_iso_ts_parser", "iso_ts_parser", src)
    assert not r.equivalent
    assert "rejected" in r.detail


def test_a3_rejects_dropping_the_utc_assumption():
    """Naive input is documented as UTC; a naive result breaks every epoch."""
    src = _swap(_a3_ciso(),
                "    if dt.tzinfo is None:\n        dt = dt.replace(tzinfo=timezone.utc)\n",
                "")
    r = _check("A3_iso_ts_parser", "iso_ts_parser", src)
    assert not r.equivalent


# ── the pristine originals, and the wiring ───────────────────────────────────

@pytest.mark.parametrize("program,module", [
    ("A1_crc_telemetry", "crc_telemetry"),
    ("A2_sensor_json_logger", "sensor_json_logger"),
    ("A3_iso_ts_parser", "iso_ts_parser"),
])
def test_the_pristine_original_passes_every_layer(program, module):
    r = _check(program, module, _source(program, module))
    assert r.equivalent, r.detail


def test_the_harness_reports_a_verdict_for_a(tmp_path):
    """`oracle_equiv` must stop being NA for category A."""
    import smart_loader.eval.proteus_harness as H
    programs = {p.name: p for p in H.discover_programs(CORPUS)}
    for name in ("A1_crc_telemetry", "A2_sensor_json_logger", "A3_iso_ts_parser"):
        program = programs[name]
        verdict, _ = H.oracle_verdict(program, program.path)
        assert verdict == "Y", f"{name}: pristine original scored {verdict}"


def test_the_harness_still_abstains_where_there_are_no_probes():
    """No probes must mean NA, never an invented verdict.

    Every program in the corpus now has probes -- A here, B in Track 3b -- so the
    abstention path needs a synthetic name to exercise at all. It still matters:
    a future program added to the corpus before its probes are written must
    record NA rather than silently pass.
    """
    import dataclasses

    import smart_loader.eval.proteus_harness as H
    programs = {p.name: p for p in H.discover_programs(CORPUS)}
    unprobed = dataclasses.replace(programs["A1_crc_telemetry"],
                                   name="Z9_not_in_the_probe_registry")
    verdict, detail = H.oracle_verdict(unprobed, unprobed.path)
    assert verdict == "NA", detail


def test_both_single_shot_and_proteus_measure_a():
    """The verdict must be recorded identically in every arm, or A is not
    comparable across them."""
    for script in ("run_single_shot.py", "run_axiom_arms.py"):
        src = open(os.path.join(ROOT, script), encoding="utf-8").read()
        assert "oracle_verdict" in src, script


def test_a2_rejects_a_backend_that_turns_nan_into_null():
    """The sensor case. `orjson` writes NaN as `null`.

    That collapses "the transmitter reported not-a-number" into "there was no
    reading" -- different facts in an audit log, acted on differently downstream,
    and a failed transmitter reports NaN routinely.
    """
    pytest.importorskip("orjson")
    src = _swap(_source("A2_sensor_json_logger", "sensor_json_logger"),
                "import json", "import orjson")
    src = _swap(
        src,
        "return json.dumps(record, sort_keys=True, separators=_SEPARATORS)",
        "return orjson.dumps(record, option=orjson.OPT_SORT_KEYS).decode()")
    r = _check("A2_sensor_json_logger", "sensor_json_logger", src)
    assert not r.equivalent


def test_a2_golden_records_the_nan_and_infinity_cases():
    """Pin the coverage itself, so a future probe edit cannot quietly drop it."""
    import json as _json
    golden = _json.load(open(os.path.join(ROOT, "equivalence", "golden.json"),
                             encoding="utf-8"))
    values = golden["A2_sensor_json_logger"]["values"]
    assert any("NaN" in v for v in values)
    assert any('"value":Infinity' in v for v in values)
    assert any('"value":-Infinity' in v for v in values)
