"""A reverted row must record WHY the Verifier gave up.

Five proteus_full reverts (A2 r1, B1 r1/r3, B3 r1/r3) could not be diagnosed
from the CSV at all. Their ``failure_mode`` read ``source_import_remains``,
which merely describes the restored ORIGINAL file -- of course it still imports
the source library -- and their ``notes`` read "NO-OP: source unchanged". The
Verifier's actual evidence lives in ``_repair_history`` and was discarded, so
the only way to learn why repair failed was to pay for another run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.eval.proteus_harness import COLUMNS  # noqa: E402

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "run_axiom_arms.py")


def test_revert_reason_is_a_column():
    assert "revert_reason" in COLUMNS


def test_runner_populates_it_from_repair_history():
    source = open(RUNNER, encoding="utf-8").read()
    assert '_repair_history' in source
    assert 'row["revert_reason"]' in source


def test_it_reads_the_last_cycle_not_the_first():
    """The cycle that exhausted the budget is the informative one."""
    source = open(RUNNER, encoding="utf-8").read()
    block = source[source.index('history = state.get("_repair_history")'):]
    block = block[:block.index('row["revert_reason"]') + 400]
    assert "history[-1]" in block


def test_it_survives_a_run_with_no_repair_history():
    """A clean run has no history; the column must simply stay empty."""
    source = open(RUNNER, encoding="utf-8").read()
    block = source[source.index('history = state.get("_repair_history")'):]
    assert "or []" in block[:120], "must default to empty rather than raise"
    assert "if history:" in block[:200], "must not index an empty list"


def test_fra_failure_reason_is_recorded_too():
    """C1 r2 came back byte-identical with vca_reentries=0 and no explanation.

    It is a reimplement subject, so it goes through the FRA, not the Connector
    and not the Verifier -- there was no repair history to read. The FRA does
    record why it gave up, in ``fra_report``; that just never reached the CSV.
    """
    source = open(RUNNER, encoding="utf-8").read()
    assert 'state.get("fra_report")' in source
    block = source[source.index('fra = state.get("fra_report")'):]
    block = block[:block.index("history = state.get")]
    assert 'row["revert_reason"]' in block
    assert "or {}" in block, "a run with no FRA must not raise"
    assert 'fra.get("error")' in block, "only record it when there is one"
