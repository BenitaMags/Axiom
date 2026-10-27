"""A vendored dependency must not be scored as a clean migration.

The judge asks one question -- "does this behave differently from the original?"
-- and a verbatim copy of the dependency behaves IDENTICALLY. So CLEAN is the
correct answer to the question asked and a completely wrong verdict. A C1 round
returned 3/3 CLEAN on three byte-identical clones carrying 73% of
`simple_pid.PID`'s code lines in runs of four or more.

`is_vendored` is the counterpart of `is_noop`: both catch a clone that earns a
good verdict without doing the work, and both are measured from files rather than
asked of a model.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _c1_program():
    import smart_loader.eval.proteus_harness as H
    for p in H.discover_programs(CORPUS):
        if p.name == "C1_pid_pump_controller":
            return p
    pytest.skip("C1 not in corpus")


def test_a_vendored_clone_is_flagged(tmp_path):
    import inspect
    try:
        import simple_pid
    except ImportError:
        pytest.skip("simple-pid not installed")
    from judge_clones import is_vendored

    program = _c1_program()
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in program.source_files:
        src = open(os.path.join(program.path, name), encoding="utf-8").read()
        if name == "controller.py":
            # Exactly the failure mode observed: the library's class pasted in
            # and the import removed.
            src = (src.replace("    from simple_pid import PID"
                               "  # lazy import for testability\n", "")
                   + "\n\n" + inspect.getsource(simple_pid.PID))
        (clone / name).write_text(src)

    vendored, overlap = is_vendored(program, str(clone))
    assert vendored, f"a pasted copy of PID scored only {overlap:.0%}"
    assert overlap > 0.6


def test_the_pristine_original_is_not_flagged(tmp_path):
    """The original imports the library; it does not contain it."""
    from judge_clones import is_vendored
    program = _c1_program()
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in program.source_files:
        (clone / name).write_text(
            open(os.path.join(program.path, name), encoding="utf-8").read())
    vendored, overlap = is_vendored(program, str(clone))
    assert not vendored and overlap < 0.2


def test_library_substitution_subjects_are_never_flagged(tmp_path):
    """Only reimplement subjects are asked to write the behaviour themselves.

    On a substitution subject, resembling the OUTGOING package is neither
    expected nor meaningful, so the check must abstain rather than guess.
    """
    from judge_clones import is_vendored
    program = _c1_program()
    # `reimplement` is derived from to_lib ("in-house controller" etc.), so a
    # substitution subject is made by naming a real package as the target.
    import dataclasses
    substitution = dataclasses.replace(program, to_lib="some-other-package")
    assert substitution.reimplement is False
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in program.source_files:
        (clone / name).write_text(
            open(os.path.join(program.path, name), encoding="utf-8").read())
    vendored, overlap = is_vendored(substitution, str(clone))
    assert vendored is False and overlap == 0.0


def test_vendored_is_excluded_from_the_clean_rate():
    """Like NO-OP, it must not count towards `attempted`."""
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    assert '"NO-OP", "VENDORED"' in src


def test_vendored_is_checked_before_noop():
    """A vendored clone is not unchanged -- it is changed into someone else's
    code. Conflating the two would hide the more serious outcome."""
    src = open(os.path.join(ROOT, "judge_clones.py"), encoding="utf-8").read()
    assert src.index("if vendored:") < src.index("elif is_noop(row):")
