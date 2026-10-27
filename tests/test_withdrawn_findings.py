"""A finding retracted by its own text must not set the verdict.

The judge uses the `problem` field as a scratchpad: it works through a
possibility, concludes the behaviour matches after all, and still returns the
entry with a severity attached -- which then sets the row's verdict. Measured
across the 20 verdicts collected in this session, 3 had EVERY finding retracted
this way, including one on a clone independently confirmed faithful by the
oracle, a 300-script differential fuzz and a direct surface comparison. Its
summary read "no concrete divergence was found"; its verdict was MAJOR.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Verbatim from real replies this session.
REAL_WITHDRAWN = (
    "In the original, when the interlock fails and the state is already FAULT, "
    "the function fell through to the checks; in the rewrite the same "
    "fall-through occurs, so this matches -- however the original's `return` "
    "only executed inside the combined condition, which is the same here, so "
    "behaviour is equivalent."
)
REAL_SUBSTANTIVE = (
    "simple-pid short-circuits and returns the previous output unchanged when "
    "`dt < sample_time`; this re-implementation always recomputes, so repeated "
    "rapid calls produce new outputs where the original returned the cached "
    "`_last_output`."
)


def test_a_retracted_finding_is_detected():
    from judge_clones import finding_withdrawn
    assert finding_withdrawn(REAL_WITHDRAWN)


def test_a_substantive_finding_is_not_detected():
    from judge_clones import finding_withdrawn
    assert not finding_withdrawn(REAL_SUBSTANTIVE)


def test_a_mid_sentence_match_is_not_a_retraction():
    """Only the tail counts -- a withdrawal is a conclusion.

    The same words early in a sentence usually introduce a real defect.
    """
    from judge_clones import finding_withdrawn
    text = ("This matches the original on the first call, but every later call "
            "divides by a dt of zero and raises ZeroDivisionError, so the "
            "controller crashes on a coarse clock.")
    assert not finding_withdrawn(text)


def test_all_withdrawn_requires_every_finding():
    """One real finding among retracted ones must still score."""
    from judge_clones import all_findings_withdrawn
    assert all_findings_withdrawn([{"problem": REAL_WITHDRAWN}])
    assert not all_findings_withdrawn([
        {"problem": REAL_WITHDRAWN}, {"problem": REAL_SUBSTANTIVE}])
    assert not all_findings_withdrawn([])          # CLEAN, not NO-VERDICT


def test_no_verdict_is_excluded_from_the_clean_rate():
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    assert 'if v not in ("NO-OP", "VENDORED", "NO-VERDICT")' in src


def test_a_single_heuristic_cannot_manufacture_a_pass():
    """CLEAN needs TWO agreeing signals; NO-VERDICT is the fallback.

    The retraction detector is a keyword heuristic over model prose. On its own
    it must never be able to turn a verdict into a pass -- a misfire has to cost
    a finding, not invent one. So the CLEAN branch additionally requires the
    judge's own summary to state that it found nothing, and anything short of
    that lands on NO-VERDICT, which is excluded from the rate rather than
    counted as success.
    """
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    block = src[src.index("if all_findings_withdrawn("):]
    block = block[:block.index("vendored, overlap")]
    assert "summary_affirms_clean" in block, (
        "CLEAN is reachable from the retraction heuristic alone"
    )
    assert 'verdict = "NO-VERDICT"' in block, "no fallback when signals disagree"
    # The clean branch must be the guarded one, the no-verdict branch the else.
    assert block.index('verdict = "CLEAN"') < block.index('verdict = "NO-VERDICT"')



# ── retracted findings + a clean summary means CLEAN ──────────────────────────
#
# When every finding retracts itself AND the summary says the reviewer found
# nothing, two independent signals agree and the honest record is CLEAN. Scoring
# it NO-VERDICT discards a real clean verdict; scoring it MAJOR asserts a defect
# the judge already took back. Observed on all three C2 v3 clones, each
# independently confirmed by a 300-script differential fuzz.

REAL_CLEAN_SUMMARY = (
    "The rewrite reimplements the FSM faithfully (guard failure returns False, "
    "unmatched source raises an error, state/valve bookkeeping and step logic "
    "match); no concrete behavioural divergence found."
)
REAL_DEFECT_SUMMARY = (
    "The in-house PID's dt/first-call and repeat-call semantics diverge from "
    "simple_pid.PID, changing the controller's numeric output on the first "
    "update and on rapid successive updates."
)


def test_a_clean_summary_is_recognised():
    from judge_clones import summary_affirms_clean
    assert summary_affirms_clean(REAL_CLEAN_SUMMARY)


def test_a_defect_summary_is_not_recognised_as_clean():
    from judge_clones import summary_affirms_clean
    assert not summary_affirms_clean(REAL_DEFECT_SUMMARY)
    assert not summary_affirms_clean("")


def test_an_explicit_withdrawn_marker_is_detected():
    """A finding ending "the real divergence is none... (withdrawn)" set a MAJOR
    verdict because the marker was missing from the phrase list."""
    from judge_clones import finding_withdrawn
    assert finding_withdrawn(
        "In the original the interlock branch returns immediately only when "
        "tripping; since FAULT matches neither branch the behaviour is "
        "identical, whereas the original short-circuited -- the real divergence "
        "is none... (withdrawn)")


def test_clean_requires_both_signals():
    """A single heuristic over prose must never be able to manufacture a pass.

    A real finding plus a clean-sounding summary must stay a defect -- observed
    on C1 v4 run2, whose summary read well while its zero-dt finding was real.
    """
    from judge_clones import all_findings_withdrawn, summary_affirms_clean
    findings = [{"problem": REAL_SUBSTANTIVE}]
    assert not all_findings_withdrawn(findings)
    assert summary_affirms_clean("I found no concrete divergence")  # signal 2 only
    # The scoring branch requires all_findings_withdrawn FIRST, so this pair
    # cannot reach the CLEAN branch.
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    block = src[src.index("if all_findings_withdrawn("):]
    block = block[:block.index("vendored, overlap")]
    assert "summary_affirms_clean" in block
    assert block.index("if all_findings_withdrawn(") < block.index("summary_affirms_clean")


def test_an_ambiguous_summary_still_yields_no_verdict():
    from judge_clones import summary_affirms_clean
    assert not summary_affirms_clean(
        "The rewrite is largely faithful; the main observable difference is that "
        "invalid triggers now silently return False instead of raising.")
