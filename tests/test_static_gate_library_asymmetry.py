"""The differential static gate must not report the substitution itself.

Two defects, both measured on D2, both in `_new_static_failures`.

1. THE FINGERPRINT CARRIED THE LIBRARY NAME. Bandit writes the library into
   its message -- "Call to requests without timeout" vs "Call to httpx without
   timeout" -- so the same rule about the same construct produced two
   different keys. Even where both sides are flagged identically, the patched
   one reads as introduced by the migration.

2. B113 DROVE THE REPAIR LOOP. `requests.Session()` is not covered by
   `request_without_timeout`; `httpx.Client()` is. So the baseline is clean,
   the patched side is not, and the Connector is told to fix it. The only
   available fix is a client-level timeout -- which the original never had,
   so the program now aborts after 10s where it used to block indefinitely.
   A blind judge flagged exactly that on both D2 runs; on the second it was
   the ONLY finding, a defect manufactured entirely by this gate.

   The file already states the rule for this case, at `error_contract`:
   "a check that cannot tell a defect from its own fix must not be allowed to
   steer a repair." B113's fix IS the defect, so it gets the same treatment --
   recorded, never repaired.

What must NOT change: a genuinely new finding still blocks. These tests pin
both directions.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.agents.verifier_agent import (  # noqa: E402
    RECORDED_NOT_REPAIRED_RULES, _mask_libraries, _new_static_failures)

LIBS = {"requests", "httpx"}


def _bandit(*issues):
    return {"bandit": {"issues": [
        {"severity": "MEDIUM", "test_id": tid, "text": text}
        for tid, text in issues]}}


# ------------------------------------------------- 1. the D2 case exactly ---

def test_b113_is_recorded_and_never_blocks():
    """Baseline clean, patched flagged -- the real D2 shape."""
    found = _new_static_failures(
        _bandit(), _bandit(("B113", "Call to httpx without timeout")), LIBS)
    assert len(found) == 1
    assert found[0]["rule"] == "B113"
    assert found[0]["recorded_only"] is True
    assert [f for f in found if not f["recorded_only"]] == []


def test_b113_is_still_reported_not_silently_dropped():
    found = _new_static_failures(
        _bandit(), _bandit(("B113", "Call to httpx without timeout")), LIBS)
    assert "without timeout" in found[0]["message"]


# ------------------------------------------------ 2. library-name masking ---

def test_the_same_rule_on_both_sides_is_not_new():
    """Only the library name differs, so nothing was introduced."""
    assert _new_static_failures(
        _bandit(("B310", "Audit url open for permitted schemes: requests")),
        _bandit(("B310", "Audit url open for permitted schemes: httpx")),
        LIBS) == []


def test_masking_is_applied_to_pylint_and_mypy_too():
    base = {"pylint": {"errors": [
        {"symbol": "no-member", "message": "Module 'requests' has no 'foo'"}]}}
    patched = {"pylint": {"errors": [
        {"symbol": "no-member", "message": "Module 'httpx' has no 'foo'"}]}}
    assert _new_static_failures(base, patched, LIBS) == []

    base = {"mypy": {"errors": ["a.py:1: error: Module 'requests' is untyped"]}}
    patched = {"mypy": {"errors": ["a.py:9: error: Module 'httpx' is untyped"]}}
    assert _new_static_failures(base, patched, LIBS) == []


def test_distribution_and_module_spellings_both_mask():
    assert _mask_libraries("modbus_tk raised", {"modbus-tk"}) == "<lib> raised"
    assert _mask_libraries("modbus-tk raised", {"modbus-tk"}) == "<lib> raised"


def test_masking_does_not_hide_an_unrelated_word():
    assert _mask_libraries("requested a timeout", LIBS) == "requested a timeout"


# --------------------------------------------- 3. real findings still block ---

def test_a_genuinely_new_finding_still_blocks():
    found = _new_static_failures(
        _bandit(), _bandit(("B602", "subprocess call with shell=True")), LIBS)
    assert len(found) == 1
    assert found[0]["recorded_only"] is False


def test_a_new_finding_alongside_b113_still_blocks():
    found = _new_static_failures(
        _bandit(),
        _bandit(("B113", "Call to httpx without timeout"),
                ("B602", "subprocess call with shell=True")),
        LIBS)
    actionable = [f for f in found if not f["recorded_only"]]
    assert len(found) == 2 and len(actionable) == 1
    assert actionable[0]["rule"] == "B602"


def test_low_severity_bandit_findings_are_still_ignored():
    low = {"bandit": {"issues": [
        {"severity": "LOW", "test_id": "B311", "text": "random"}]}}
    assert _new_static_failures(_bandit(), low, LIBS) == []


def test_no_libraries_given_still_diffs_normally():
    """Masking is additive; with no libraries the old behaviour stands."""
    found = _new_static_failures(
        _bandit(("B310", "scheme: requests")),
        _bandit(("B310", "scheme: httpx")), ())
    assert len(found) == 1


def test_only_the_documented_rule_is_exempt():
    assert RECORDED_NOT_REPAIRED_RULES == {"B113"}
