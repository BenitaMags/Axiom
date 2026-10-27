"""A CSV row and the clone it describes must be provably the same artefact.

The runners used to create the clone directory with `exist_ok=True` and copy
files in one at a time. A later run into the same `--clone-dir` therefore
overwrote matching names, left non-matching ones behind, and produced a directory
that was a MIXTURE of two runs. Seven of 144 rows in
`results_FOUR_ARM_FINAL.csv` ended up describing clones that no longer matched
what was on disk, and nothing surfaced it until the clones were re-scored weeks
later -- by which point two findings had been briefly reported off stale
directories.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import smart_loader.eval.proteus_harness as H  # noqa: E402


class _Program:
    def __init__(self, name):
        self.name = name


def _work(tmp_path, **files):
    """A scratch directory holding the given modules. Keys are module names, so
    the `.py` suffix is added here -- `save_clone` copies only `.py` files, and a
    helper that forgot the suffix would silently produce empty clones."""
    directory = tmp_path / "work"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / f"{name}.py").write_text(text)
    return str(directory)


def test_a_resave_never_writes_into_an_existing_clone(tmp_path):
    """The exact bug: the second save must not leave the first run's files."""
    root = str(tmp_path / "clones")
    first = _work(tmp_path, a="x = 1\n", only_in_first="y = 1\n")
    H.save_clone(first, root, "arm", _Program("P"), 1)

    second = tmp_path / "work2"
    second.mkdir()
    (second / "a.py").write_text("x = 2\n")
    H.save_clone(str(second), root, "arm", _Program("P"), 1)

    saved = os.path.join(root, "arm", "P_run1")
    assert sorted(os.listdir(saved)) == ["a.py"], (
        "a file from the previous run survived into the new clone")
    assert open(os.path.join(saved, "a.py")).read() == "x = 2\n"


def test_the_superseded_clone_is_kept_not_deleted(tmp_path):
    """It is the only remaining evidence for whatever CSV row described it."""
    root = str(tmp_path / "clones")
    H.save_clone(_work(tmp_path, a="x = 1\n"), root, "arm", _Program("P"), 1)
    second = tmp_path / "w2"
    second.mkdir()
    (second / "a.py").write_text("x = 2\n")
    H.save_clone(str(second), root, "arm", _Program("P"), 1)

    kept = [d for d in os.listdir(os.path.join(root, "arm"))
            if "superseded" in d]
    assert len(kept) == 1
    assert open(os.path.join(root, "arm", kept[0], "a.py")).read() == "x = 1\n"


def test_the_fingerprint_changes_with_content_and_with_filenames(tmp_path):
    root = str(tmp_path / "clones")
    one = H.save_clone(_work(tmp_path, a="x = 1\n"), root, "arm",
                       _Program("A"), 1)
    two = H.save_clone(_work(tmp_path / "b", a="x = 2\n"), root, "arm",
                       _Program("B"), 1)
    three = H.save_clone(_work(tmp_path / "c", b="x = 1\n"), root, "arm",
                         _Program("C"), 1)
    assert one != two, "content change did not change the fingerprint"
    assert one != three, "a renamed file did not change the fingerprint"


def test_the_fingerprint_is_stable_for_identical_content(tmp_path):
    root = str(tmp_path / "clones")
    one = H.save_clone(_work(tmp_path / "x", a="x = 1\n"), root, "arm",
                       _Program("A"), 1)
    two = H.save_clone(_work(tmp_path / "y", a="x = 1\n"), root, "arm",
                       _Program("B"), 1)
    assert one == two


def test_clone_sha_is_a_csv_column():
    assert "clone_sha" in H.COLUMNS


def test_every_runner_uses_the_shared_save(tmp_path):
    """Three copies of this logic is how they drift apart."""
    for script in ("run_axiom_arms.py", "run_single_shot.py",
                   "run_single_shot_repair.py"):
        source = open(os.path.join(ROOT, script), encoding="utf-8").read()
        assert "H.save_clone(" in source, script
        assert "os.makedirs(dest, exist_ok=True)" not in source, (
            f"{script} still creates the clone directory itself")


def test_the_repair_arm_keeps_both_clones_distinct(tmp_path):
    """attempt 1 and the final attempt must not collide."""
    root = str(tmp_path / "clones")
    a = H.save_clone(_work(tmp_path / "p", a="x = 1\n"), root, "arm",
                     dest_name="P_run1_attempt1")
    b = H.save_clone(_work(tmp_path / "q", a="x = 2\n"), root, "arm",
                     dest_name="P_run1")
    assert a != b
    assert sorted(os.listdir(os.path.join(root, "arm"))) == ["P_run1",
                                                             "P_run1_attempt1"]


def test_the_verifier_detects_a_tampered_clone(tmp_path):
    """End to end: a changed clone must make verify_clones.py exit non-zero."""
    corpus = os.path.expanduser("~/Downloads/evaluation")
    if not os.path.isdir(corpus):
        import pytest
        pytest.skip("evaluation corpus not present")

    programs = {p.name: p for p in H.discover_programs(corpus)}
    program = programs["A1_crc_telemetry"]
    root = str(tmp_path / "clones")
    work = tmp_path / "w"
    work.mkdir()
    for name in os.listdir(program.path):
        if name.endswith(".py"):
            (work / name).write_text(
                open(os.path.join(program.path, name), encoding="utf-8").read())
    sha = H.save_clone(str(work), root, "single_shot", program, 1)

    csv_path = tmp_path / "rows.csv"
    passed, total, _, _ = H.run_tests(os.path.join(root, "single_shot",
                                                   "A1_crc_telemetry_run1"),
                                      program)
    csv_path.write_text(
        "program,config,run_id,tests_passed,tests_total,clone_sha\n"
        f"A1_crc_telemetry,single_shot,1,{passed},{total},{sha}\n")

    def run():
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "verify_clones.py"),
             "--csv", str(csv_path), "--clones", root, "--corpus", corpus],
            capture_output=True, text=True)

    clean = run()
    assert clean.returncode == 0, clean.stdout + clean.stderr

    saved = os.path.join(root, "single_shot", "A1_crc_telemetry_run1",
                         "crc_telemetry.py")
    with open(saved, "a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    tampered = run()
    assert tampered.returncode == 1
    assert "DISAGREES" in tampered.stdout


def test_an_empty_destination_is_reused_not_a_crash(tmp_path):
    """An interrupted earlier run leaves an empty clone directory.

    Raising there would abort a run after the API spend, for a directory that
    holds nothing worth preserving.
    """
    root = str(tmp_path / "clones")
    os.makedirs(os.path.join(root, "arm", "P_run1"))
    sha = H.save_clone(_work(tmp_path, a="x = 1\n"), root, "arm",
                       _Program("P"), 1)
    assert sha
    assert sorted(os.listdir(os.path.join(root, "arm", "P_run1"))) == ["a.py"]
    assert not [d for d in os.listdir(os.path.join(root, "arm"))
                if "superseded" in d], "an empty directory must not be kept"


def test_every_runner_records_the_fingerprint_on_the_row():
    """Returning the hash is not enough -- it has to reach the CSV.

    `run_single_shot_repair` called `_save_clone` and discarded its return value,
    so all 36 rows of a finished run shipped with an empty `clone_sha` and could
    only be verified by the weaker test-count fallback.
    """
    for script in ("run_axiom_arms.py", "run_single_shot.py",
                   "run_single_shot_repair.py"):
        source = open(os.path.join(ROOT, script), encoding="utf-8").read()
        assert 'row["clone_sha"]' in source, (
            f"{script} saves the clone but never records its hash on the row")
