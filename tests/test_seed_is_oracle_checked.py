"""A search that applies no transformation must still check its seed.

`LengthTargetedSearch.run` used to hardcode `oracle_passed=True` whenever no
candidate survived, reasoning that unchanged code is trivially equivalent. That
holds against the *seed*, but the oracle compares against the pristine
*original* -- and in reimplement mode the seed is the FRA's rewrite. A C1 run
shipped a PID whose constructor raised `AttributeError` on every call, reported
as "Equivalence oracle PASS", because of this.
"""
from smart_loader.isomorph.search import LengthTargetedSearch


class _Verdict:
    def __init__(self, equivalent, detail):
        self.equivalent, self.detail = equivalent, detail


class _Oracle:
    """Rejects everything -- seed included."""
    def __init__(self, equivalent):
        self._equivalent = equivalent
        self.calls = []

    def check(self, module_name, source):
        self.calls.append(source)
        return _Verdict(self._equivalent,
                        "all four layers passed" if self._equivalent
                        else "probes failed: AttributeError")

    def check_many(self, sources):
        return self.check("", "")


# A module nothing in the registry can transform, so the search applies
# nothing and returns the seed.
INERT = "X = 1\n"


def _run(equivalent):
    oracle = _Oracle(equivalent)
    search = LengthTargetedSearch(oracle, beam_size=2, max_iterations=2)
    return oracle, search.run("controller", None, seed_source=INERT)


def test_a_failing_seed_is_not_reported_as_a_pass():
    oracle, result = _run(equivalent=False)
    assert result.applied == [], "expected no transformation on an inert module"
    assert result.oracle_passed is False, (
        "the seed failed the oracle but the search reported a pass"
    )
    assert oracle.calls, "the seed was never checked against the oracle"


def test_a_failing_seed_says_so_in_the_detail():
    _, result = _run(equivalent=False)
    assert "FAILS" in result.oracle_detail
    assert "no transformation applied" in result.oracle_detail


def test_a_passing_seed_is_still_a_pass():
    _, result = _run(equivalent=True)
    assert result.applied == []
    assert result.oracle_passed is True
    assert "passes" in result.oracle_detail


def test_the_unchanged_variant_is_returned_verbatim():
    """Whatever the verdict, an unchanged result must not mutate the source."""
    for equivalent in (True, False):
        _, result = _run(equivalent)
        assert result.variant_source == INERT


def test_the_judge_progress_line_reports_the_reclassified_verdict():
    """The live log must not print CLEAN for a clone that migrated nothing.

    The CSV and the summary table both applied `is_noop()`; the progress line
    did not, so a NO-OP row scrolled past as "CLEAN  0 finding(s)".
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "judge_clones.py"), encoding="utf-8").read()
    # Anchor on the verdict line specifically -- an earlier `{label}  ERROR`
    # print would otherwise match first.
    head = src.index("{len(parsed['findings'])} finding(s)")
    region = src[max(0, head - 400):head + 80]
    assert "shown_verdict" in region
    assert "parsed['verdict']:<7}" not in region, (
        "the progress line still prints the judge's raw verdict"
    )
