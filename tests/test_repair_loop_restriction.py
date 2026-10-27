"""A check that cannot describe a runtime behaviour must not drive a repair.

This file already carried that rule for two cases: `error_contract` (the
faithful adapter looked like new swallowing) and bandit B113 (its only
available fix IS the divergence). Both were demoted after a blind judge caught
what repairing on them produced.

The repair loop's own record says the rule has to reach further. Of the 20
proteus_full rows with a recorded revert, these drove one:

    A2 r1   Need type annotation for "number_buf"   [var-annotated]
    A2 r3   Argument 5 to "dumps" incompatible      [arg-type]
    C3 r2   Incompatible types in assignment        [assignment]
    C3 r3   Argument "body" to "notify" of "Apprise"[arg-type]
    D1 r1   Name "defines" is not defined           [name-defined]

The last is a real NameError waiting to happen and must keep blocking. The
first cannot change what the program computes -- no value differs because a
list lacked a declared element type -- and it cost A2 seed 1 its entire
migration, ending migration_success=N.

The middle ones are claims about the ADOPTED library's type stubs, which for a
package the code has just adopted are routinely partial. The same question is
already answered by execution: layer 6 calls the program's own API against the
real library and reports the actual exception. A stub disagreeing is weaker
evidence than a call that runs.

What must NOT change: a type error about FIRST-PARTY code still blocks. Only
claims naming the adopted package are demoted.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.verifier_agent import (  # noqa: E402
    MYPY_HYGIENE_CODES, MYPY_STUB_CLAIM_CODES, RECORDED_NOT_REPAIRED_RULES,
    _mypy_code, _new_static_failures)

LIBS = {"smtplib", "apprise", "json", "ujson", "requests", "httpx"}


def _mypy(*messages):
    return {"mypy": {"errors": [f"a.py:1: error: {m}" for m in messages]}}


def _only(patched):
    return _new_static_failures(_mypy(), patched, LIBS)


def test_the_code_is_read_off_the_message():
    assert _mypy_code('Need type annotation for "x"  [var-annotated]') == "var-annotated"
    assert _mypy_code('Name "d" is not defined  [name-defined]') == "name-defined"
    assert _mypy_code("no code here") == ""


# ------------------------------------------------- must NOT drive repair ---

def test_an_annotation_nag_is_recorded_not_repaired():
    """The A2 seed 1 case: it cost a whole migration."""
    found = _only(_mypy('Need type annotation for "number_buf"  [var-annotated]'))
    assert len(found) == 1 and found[0]["recorded_only"] is True


def test_every_hygiene_code_is_recorded():
    for code in MYPY_HYGIENE_CODES:
        found = _only(_mypy(f"something about typing  [{code}]"))
        assert found[0]["recorded_only"] is True, code


def test_a_stub_claim_naming_the_adopted_library_is_recorded():
    """The C3 seed 3 case. Layer 6 tests this by execution instead."""
    found = _only(_mypy(
        'Argument "body" to "notify" of "Apprise" has incompatible type "X"  [arg-type]'))
    assert found[0]["recorded_only"] is True


def test_recorded_findings_are_still_reported():
    found = _only(_mypy('Need type annotation for "x"  [var-annotated]'))
    assert "var-annotated" in found[0]["message"]


# ----------------------------------------------------- must STILL block ---

def test_an_undefined_name_still_blocks():
    """The D1 seed 1 case: a genuine NameError."""
    found = _only(_mypy('Name "defines" is not defined  [name-defined]'))
    assert found[0]["recorded_only"] is False


def test_a_missing_attribute_still_blocks():
    found = _only(_mypy('"Client" has no attribute "__aenter__"  [attr-defined]'))
    assert found[0]["recorded_only"] is False


def test_a_first_party_type_error_still_blocks():
    """Only claims about the ADOPTED package are demoted, not all arg-type."""
    found = _only(_mypy(
        'Argument 1 to "step" has incompatible type "str"; expected "float"  [arg-type]'))
    assert found[0]["recorded_only"] is False


def test_an_unknown_code_still_blocks():
    """Anything not explicitly demoted keeps its power to drive a repair."""
    found = _only(_mypy("something new mypy learned to say  [brand-new-code]"))
    assert found[0]["recorded_only"] is False


def test_pylint_is_untouched():
    base = {"pylint": {"errors": []}}
    patched = {"pylint": {"errors": [
        {"symbol": "undefined-variable", "message": "Undefined variable 'defines'"}]}}
    found = _new_static_failures(base, patched, LIBS)
    assert found[0]["recorded_only"] is False


def test_a_blocking_and_a_recorded_finding_coexist():
    found = _only(_mypy('Need type annotation for "x"  [var-annotated]',
                        'Name "d" is not defined  [name-defined]'))
    assert len(found) == 2
    assert sum(1 for f in found if f["recorded_only"]) == 1
    assert sum(1 for f in found if not f["recorded_only"]) == 1


def test_the_demoted_sets_are_pinned():
    """Nothing joins these quietly."""
    assert RECORDED_NOT_REPAIRED_RULES == {"B113"}
    assert "name-defined" not in MYPY_HYGIENE_CODES | MYPY_STUB_CLAIM_CODES
    assert "attr-defined" not in MYPY_HYGIENE_CODES | MYPY_STUB_CLAIM_CODES
