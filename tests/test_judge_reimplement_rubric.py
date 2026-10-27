"""The judge's rubric must be satisfiable for reimplement subjects.

The general prompt lists "an exception type, message, or class that callers can
observe changing" as a reportable defect. That is right when one library replaces
another and impossible to avoid when the library is removed outright: first-party
code cannot raise `transitions.core.MachineError` without importing the package
it was told to delete.

Measured, not assumed. A hand-rolled FSM verified three ways -- passes the
corrected oracle, handles both halves of the refusal behaviour, matches the real
library across 300 differential fuzz scripts -- was judged MAJOR on exactly one
finding: RuntimeError instead of MachineError, with a summary reading "behaviour
is otherwise faithful". Three clones that VENDORED the library verbatim scored
CLEAN, because copied code raises the copied exception.

So the rubric ranked the correct answer below the one the provenance guard exists
to forbid.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _programs():
    import smart_loader.eval.proteus_harness as H
    return {p.name: p for p in H.discover_programs(CORPUS)}


def test_the_note_is_added_for_reimplement_subjects():
    import judge_clones as J
    program = _programs()["C2_tank_level_fsm"]
    assert program.reimplement
    prompt, leaks = J.build_prompt(program, program.path)
    assert "this rewrite REMOVES" in prompt
    assert "CANNOT raise" in prompt
    assert leaks == [], "the note must not leak arm identity"


def test_the_note_is_absent_for_substitution_subjects():
    """Where one library replaces another, a changed exception type IS a defect.

    The note must not silently weaken the rubric for the nine substitution
    subjects, which is most of the corpus.
    """
    import judge_clones as J
    program = _programs()["D3_telemetry_gateway"]
    assert not program.reimplement
    prompt, _ = J.build_prompt(program, program.path)
    assert "this rewrite REMOVES" not in prompt
    assert "exception type, message, or class that callers can observe" in prompt


def test_the_note_still_treats_swallowed_errors_as_defects():
    """Relaxing the TYPE must not relax whether the error is raised at all.

    C2 v1's real defect was swallowing invalid-trigger errors entirely, and C2
    v2's was raising where the library returns False. Both must stay reportable.
    """
    import judge_clones as J
    prompt, _ = J.build_prompt(_programs()["C2_tank_level_fsm"],
                               _programs()["C2_tank_level_fsm"].path)
    assert "WHETHER and WHEN they are raised" in prompt
    assert "swallows IS a defect" in prompt
    assert "raises where the original returned a value" in prompt


def test_the_note_asks_the_judge_to_flag_vendoring():
    """The mechanical guard catches copying; the judge should too, as a second
    line of defence rather than the only one."""
    import judge_clones as J
    prompt, _ = J.build_prompt(_programs()["C2_tank_level_fsm"],
                               _programs()["C2_tank_level_fsm"].path)
    assert "verbatim is NOT a way to be faithful" in prompt


def test_every_reimplement_subject_gets_the_note():
    import judge_clones as J
    reimplement = [p for p in _programs().values() if p.reimplement]
    assert len(reimplement) >= 2, "expected C1 and C2 at least"
    for program in reimplement:
        prompt, leaks = J.build_prompt(program, program.path)
        assert "this rewrite REMOVES" in prompt, program.name
        assert leaks == [], program.name


def test_the_prompt_forbids_reasoning_in_the_problem_field():
    """Several findings this session withdrew their own claim mid-sentence.

    Two of four placebo findings read as the model thinking aloud and concluding
    "so no divergence" / "so this is not the issue", while still being counted as
    findings and therefore setting the verdict.
    """
    import judge_clones as J
    program = _programs()["C2_tank_level_fsm"]
    prompt, _ = J.build_prompt(program, program.path)
    assert "State each finding DIRECTLY" in prompt
    assert "that is not a finding" in prompt


def test_the_placebo_lesson_is_recorded_in_the_module():
    """A control must be verified on the axis the judge actually reviews.

    The hand-written placebo passed the oracle, both refusal halves and 300 fuzz
    scripts, and still lacked `.machine` -- which none of those three checks
    inspect, and which the judge correctly flagged. Checking a control only on
    the axes you already thought of is how an unfaithful control gets trusted.
    """
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    assert "REIMPLEMENT_NOTE" in src
