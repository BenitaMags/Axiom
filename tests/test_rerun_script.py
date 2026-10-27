"""The batch re-runner must never repeat a paid run, nor keep a fake one.

Two properties matter for a 72-unit unattended batch:

RESUMABILITY. An interrupted batch resumes where it stopped. A unit is
(config, program, run_id), and a finished one is skipped.

VOID RUNS DO NOT COUNT AS FINISHED. Measured in this project: two runs exited
0, printed a summary and appended a row having made no API calls at all,
because the account was out of credits. The rows read
``migration_success=N, failure_mode=nonconvergence`` -- indistinguishable from
a real pipeline failure, and a resume would have skipped them as done. So a row
with zero LLM calls is pruned (after a backup) and its unit re-run, and two
void runs in a row stop the batch instead of manufacturing 70 more fictions.
"""
import csv
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "rerun_proteus_arms", os.path.join(ROOT, "rerun_proteus_arms.py"))
rr = importlib.util.module_from_spec(_spec)
sys.modules["rerun_proteus_arms"] = rr
_spec.loader.exec_module(rr)

COLS = ["config", "program", "run_id", "migration_success", "llm_calls", "cost_usd"]


def _csv(tmp_path, *rows):
    path = str(tmp_path / "r.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def _row(config, program, run, calls="8", cost="0.30"):
    return {"config": config, "program": program, "run_id": str(run),
            "migration_success": "Y", "llm_calls": calls, "cost_usd": cost}


# ------------------------------------------------------------ void rows ---

def test_a_row_with_no_llm_calls_is_void():
    assert rr._is_void({"llm_calls": "0"})
    assert rr._is_void({"llm_calls": ""})
    assert rr._is_void({})
    assert not rr._is_void({"llm_calls": "1"})


def test_void_rows_are_pruned_and_backed_up(tmp_path):
    path = _csv(tmp_path,
                _row("proteus_full", "A1_crc_telemetry", 1),
                _row("proteus_full", "A2_sensor_json_logger", 1, calls="0", cost="0"))
    assert rr._prune_void(path) == 1
    assert os.path.isfile(path + ".with-void-rows")
    kept = rr._rows(path)
    assert len(kept) == 1 and kept[0]["program"] == "A1_crc_telemetry"


def test_pruning_a_clean_csv_changes_nothing(tmp_path):
    path = _csv(tmp_path, _row("proteus_full", "A1_crc_telemetry", 1))
    assert rr._prune_void(path) == 0
    assert not os.path.isfile(path + ".with-void-rows")


def test_a_void_row_is_replanned_not_skipped(tmp_path):
    path = _csv(tmp_path,
                _row("proteus_full", "A1_crc_telemetry", 1),
                _row("proteus_full", "A2_sensor_json_logger", 1, calls="0"))
    plan = rr._plan(path, rr.ARMS, rr.PROGRAMS, range(1, 4))
    assert ("proteus_full", "A1_crc_telemetry", 1) not in plan
    assert ("proteus_full", "A2_sensor_json_logger", 1) in plan


def test_a_void_row_contributes_nothing_to_spend(tmp_path):
    path = _csv(tmp_path,
                _row("proteus_full", "A1_crc_telemetry", 1, cost="0.30"),
                _row("proteus_full", "A2_sensor_json_logger", 1, calls="0", cost="0"))
    assert rr._spent(path) == pytest.approx(0.30)


# --------------------------------------------------------- the schedule ---

def test_a_fresh_run_plans_every_unit(tmp_path):
    plan = rr._plan(str(tmp_path / "missing.csv"), rr.ARMS, rr.PROGRAMS, range(1, 4))
    assert len(plan) == 2 * 12 * 3 == 72
    assert len(set(plan)) == 72


def test_finished_units_are_skipped(tmp_path):
    done = [_row(a, p, 1) for a in rr.ARMS for p in rr.PROGRAMS]
    path = _csv(tmp_path, *done)
    plan = rr._plan(path, rr.ARMS, rr.PROGRAMS, range(1, 4))
    assert len(plan) == 48
    assert all(run != 1 for _, _, run in plan)


def test_the_order_is_seed_major(tmp_path):
    """A batch stopped early must leave a balanced slice, not half an arm."""
    plan = rr._plan(str(tmp_path / "x.csv"), rr.ARMS, rr.PROGRAMS, range(1, 4))
    first_pass = plan[:24]
    assert {run for _, _, run in first_pass} == {1}
    assert {arm for arm, _, _ in first_pass} == set(rr.ARMS)
    assert {p for _, p, _ in first_pass} == set(rr.PROGRAMS)
    # and each seed is finished before the next begins
    assert [run for _, _, run in plan] == sorted(run for _, _, run in plan)


def test_scoping_to_one_arm_and_program(tmp_path):
    plan = rr._plan(str(tmp_path / "x.csv"), ("proteus_full",),
                    ("D2_pump_station_scheduler",), range(1, 4))
    assert plan == [("proteus_full", "D2_pump_station_scheduler", r) for r in (1, 2, 3)]


def test_unreadable_cost_values_do_not_crash_the_budget(tmp_path):
    path = _csv(tmp_path, _row("proteus_full", "A1_crc_telemetry", 1, cost="n/a"))
    assert rr._spent(path) == 0.0


def test_the_program_list_matches_the_arms_the_runner_accepts():
    assert rr.ARMS == ("proteus_full", "proteus_no_verif")
    assert len(rr.PROGRAMS) == 12


def test_each_run_gets_its_own_log_file():
    """Two hours unattended with the output thrown away is not debuggable.

    The batch used to capture each run's stdout and discard it, so a C3
    diagnosis had to be reconstructed afterwards from the CSV and the clone.
    """
    body = open(os.path.join(rr.__file__.rsplit("/", 1)[0], "rerun_proteus_arms.py")
                if False else os.path.join(ROOT, "rerun_proteus_arms.py"),
                encoding="utf-8").read()
    assert 'os.path.join(args.clone_dir, "logs")' in body
    assert "proc.stdout" in body and "proc.stderr" in body
    assert 'f"{arm}_{program}_run{run}.log"' in body


def test_the_default_clone_dir_never_collides_with_the_archive():
    """`final_v4/<config>/<program>_run<n>` is where the ARCHIVE lives.

    save_clone moves an existing clone aside rather than deleting it, so a
    rerun there loses nothing -- but every path in differential_fuzz.py and in
    this project's analysis would then resolve to the NEW clone, and the
    findings recorded against the archive would quietly stop reproducing.
    """
    body = open(os.path.join(ROOT, "rerun_proteus_arms.py"), encoding="utf-8").read()
    assert 'default="final_v4/fixed"' in body
    assert 'default="final_v4"' not in body
