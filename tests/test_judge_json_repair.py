"""A dropped brace must not cost a verdict -- and must not invent one.

A D1 review came back well formed except for its braces: the model wrote
``summary`` as a field INSIDE the finding object and closed a single ``}``,
leaving the finding, the findings array and the outer object all open. The
reply was recorded UNPARSEABLE and the call was paid for twice.

The repair is structural only. These tests pin the property that makes it safe
to run at all: it can RECOVER a finding but can never produce the empty
findings list that reads as CLEAN. `parse_verdict` exists to keep "the judge
said nothing was wrong" and "we could not read the judge's answer" apart, and a
repair that could bridge them would defeat it.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from judge_clones import (  # noqa: E402
    _repair_json_shape, all_findings_withdrawn, parse_verdict,
    summary_affirms_clean)

# The reply verbatim, minus the finding's prose.
D1_REPLY = (
    '{"findings": [{"file": "control.py", "line": "return clamp(output, 0.0, '
    '100.0)", "severity": "MINOR", "problem": "both orderings are identical '
    'here so behaviour matches for all inputs including NaN.", "summary": "No '
    'behavioural divergence found; the control logic and pipeline ordering '
    'are preserved."}'
)


def test_the_d1_reply_that_was_recorded_unparseable_now_reads():
    parsed = parse_verdict(D1_REPLY)
    assert parsed is not None
    assert len(parsed["findings"]) == 1
    assert parsed["findings"][0]["file"] == "control.py"
    # summary was lifted out of the finding to where the schema puts it
    assert parsed["summary"].startswith("No behavioural divergence found")
    assert "summary" not in parsed["findings"][0]


def test_well_formed_replies_are_untouched():
    good = json.dumps({"findings": [{"file": "a.py", "problem": "x"}],
                       "summary": "one defect"})
    parsed = parse_verdict(good)
    assert len(parsed["findings"]) == 1
    assert parsed["summary"] == "one defect"
    assert _repair_json_shape(good) is None      # nothing was open


def test_a_bare_findings_array_still_parses():
    parsed = parse_verdict('[{"file": "a.py", "problem": "x"}]')
    assert len(parsed["findings"]) == 1


# ---------------------------------------------------------------- safety ---

def test_truncation_before_any_finding_is_not_recovered():
    """The dangerous case: an empty findings list IS a CLEAN verdict.

    The distinction is who emitted the emptiness. A reply cut off at ``[``
    never said how many findings there were, so it stays unreadable. A reply
    that actually wrote ``[]`` said "none", and reading that as CLEAN is
    correct even if the rest was truncated -- so those two are asserted apart
    rather than lumped together.
    """
    assert _repair_json_shape('{"findings": [') is None
    assert parse_verdict('{"findings": [') is None

    said_none = parse_verdict('{"findings": [], "summary": "all good"')
    assert said_none is not None and said_none["findings"] == []


def test_prose_and_garbage_are_still_unreadable():
    for junk in ("I reviewed the code and it looks fine to me.",
                 "", "{", "}", "{{{", '{"summary": "clean"',
                 '{"findings": "not a list"'):
        assert parse_verdict(junk) is None, junk


def test_an_unterminated_string_is_not_guessed_at():
    assert _repair_json_shape('{"findings": [{"problem": "it bre') is None


def test_lifting_a_summary_cannot_by_itself_create_a_pass():
    """Verdict-neutrality: the lifted text still has to stand on its own."""
    broken = (
        '{"findings": [{"file": "a.py", "severity": "MAJOR", "problem": '
        '"the timeout is dropped", "summary": "the rewrite drops the '
        'connect timeout and will hang"}'
    )
    parsed = parse_verdict(broken)
    assert parsed is not None
    assert parsed["summary"].startswith("the rewrite drops")
    assert not summary_affirms_clean(parsed["summary"])
    assert not all_findings_withdrawn(parsed["findings"])
    assert parsed["verdict"] == "MAJOR"


def test_an_existing_top_level_summary_is_never_overwritten():
    broken = (
        '{"findings": [{"file": "a.py", "problem": "x", "summary": "inner"}], '
        '"summary": "outer"'
    )
    parsed = parse_verdict(broken)
    assert parsed["summary"] == "outer"
