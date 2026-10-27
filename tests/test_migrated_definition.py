"""`migrated` must mean the source changed, not that its AST SHAPE changed.

`divergence_score` tokenises the AST as node TYPE names only, so identifiers are
invisible to it. Rewriting `requests.Session()` as `httpx.Client()` leaves the
shape untouched and scores 1.0 -- "identical" -- so a correct nine-line library
swap was recorded as a no-op.

The error is not neutral. An agent that injects adapter functions adds
FunctionDef/Try/Return nodes and always registers as changed; a single-pass
rewrite that renames the import never does. The metric systematically flattered
whichever arm produced the bigger diff.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import smart_loader.eval.proteus_harness as H  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _program(name):
    for p in H.discover_programs(CORPUS):
        if p.name == name:
            return p
    pytest.skip(f"{name} not in corpus")


def _clone(tmp_path, program, **edits):
    out = tmp_path / "clone"
    out.mkdir(exist_ok=True)
    for name in program.source_files:
        text = open(os.path.join(program.path, name), encoding="utf-8").read()
        for old, new in edits.get(name, []):
            assert old in text, f"fixture pattern gone from {name}: {old!r}"
            text = text.replace(old, new)
        (out / name).write_text(text)
    return str(out)


def test_an_identifier_only_swap_counts_as_migrated(tmp_path):
    """The exact case the old metric missed."""
    program = _program("B1_scada_poller")
    clone = _clone(tmp_path, program, **{"scada_poller.py": [
        ("import requests", "import httpx"),
        ("requests.Session()", "httpx.Client()")]})

    assert H.divergence_score(program.path, clone, program) == 1.0, (
        "fixture no longer reproduces the blind spot")
    assert H.loc_changed(program.path, clone, program) > 0
    assert H.migrated(program.path, clone, program) is True


def test_an_untouched_clone_is_not_migrated(tmp_path):
    program = _program("B1_scada_poller")
    clone = _clone(tmp_path, program)
    assert H.migrated(program.path, clone, program) is False


def test_a_comment_only_change_is_not_a_migration(tmp_path):
    """`migrated` means a migration happened, not that bytes moved.

    The previous contract was "something changed", which let A3 proteus_full
    run1 -- one edited line, no library swapped -- record migrated=Y. One
    changed line is not a migration, so the check now also requires the old
    dependency to be gone and the new one to be imported and called.
    """
    program = _program("B1_scada_poller")
    clone = _clone(tmp_path, program, **{"scada_poller.py": [
        ('"""scada_poller', '"""CHANGED scada_poller')]})
    assert H.loc_changed(program.path, clone, program) > 0
    assert H.library_swapped(clone, program) == "N"
    assert H.migrated(program.path, clone, program) is False


def test_importing_the_target_without_calling_it_is_not_a_migration(tmp_path):
    """The D2 proteus_full run2 shape, which scored migrated=Y for a week.

        def _default_session():
            import httpx                  # imported
            _spt_r1 = requests.Session()  # the old one still called

    `library_swapped` says Y -- httpx imported, no import of requests -- and
    the clone compiles and passes 3/3 tests. It raises NameError the moment
    anything constructs a ScadaClient without injecting a session.
    """
    program = _program("B1_scada_poller")
    clone = _clone(tmp_path, program, **{"scada_poller.py": [
        ("import requests", "import httpx")]})
    assert H.library_swapped(clone, program) == "Y"
    assert H.target_api_used(clone, program) is False
    assert H.migrated(program.path, clone, program) is False


def test_every_runner_uses_the_shared_definition():
    for script in ("run_axiom_arms.py", "run_single_shot.py",
                   "run_single_shot_repair.py"):
        source = open(os.path.join(ROOT, script), encoding="utf-8").read()
        assert "H.migrated(" in source, script
        assert "migrated = divergence < 0.999" not in source, script


def test_migration_ok_requires_the_program_to_actually_run():
    """Layer 6 is part of the verdict, not a column beside it.

    `proteus_full` D2 run2 compiles, passes 3/3, swaps the library and passes
    the oracle -- and raises `NameError: name 'requests' is not defined` the
    first time anything constructs a ScadaClient without injecting a session.
    Five layers called it a success.
    """
    import os
    source = open(os.path.join(ROOT, "smart_loader/eval/proteus_harness.py"),
                  encoding="utf-8").read()
    body = source[source.index("def migration_ok("):source.index("def blank_row(")]
    assert "smoke_regression(program, clone_dir)[0] != \"DEFECT\"" in body, (
        "migration_ok no longer requires the clone to run")
    # BASELINE and SKIPPED must not fail a clone: the first means the pristine
    # original is broken in this environment, the second that the program has
    # no injection seam to exercise.
    assert '!= "DEFECT"' in body and '== "DEFECT"' not in body
