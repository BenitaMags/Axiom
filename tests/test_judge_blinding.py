"""Arm-identifying words must be MASKED, not merely detected.

The guard aborts a batch when a leak survives, because a judge that can tell
which arm wrote a clone invalidates every verdict in the run. That part is
correct and stays.

What was wrong: only ``axiom`` was ever rewritten. The rest of the vocabulary
was checked and never masked, so a clone containing one aborted instead of
being blinded. Measured on a D1 variant whose docstring read "Breaks the
single-shot supervisory cycle into discrete stages" -- ordinary English for one
pass of a control loop, describing the PROGRAM and revealing nothing about its
origin. The batch stopped after one paid call.

The fix masks; it does not relax. `leaks()` is unchanged and still aborts.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import judge_clones as J  # noqa: E402


def test_innocent_prose_is_masked_not_flagged():
    text = "Breaks the single-shot supervisory cycle into discrete stages."
    out = J.blind(text)
    assert J.leaks(out) == []
    assert "supervisory cycle" in out, "meaning must survive the mask"


def test_every_arm_word_is_masked():
    for raw in ("single_shot", "single-shot", "SINGLE_SHOT",
                "proteus", "Proteus", "AXIOM", "axiom",
                "smart_loader", "logic_substitution"):
        assert J.leaks(J.blind(f"written by {raw} here")) == [], raw


def test_the_guard_itself_is_unchanged():
    """An unblinded leak must still be caught -- masking is not a relaxation."""
    assert J.leaks("this came from the proteus_full arm") != []
    assert J.leaks("single_shot produced this") != []


def test_identifiers_are_renamed_rather_than_deleted():
    """Deleting a token would hand the judge a NameError to report."""
    out = J.blind("x = _axiom_wrapper()\ndef _axiom_wrapper(): pass\n")
    assert "wrapper" in out
    assert out.count("_compat_wrapper") == 2, out
