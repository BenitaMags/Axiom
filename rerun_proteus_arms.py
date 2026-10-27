#!/usr/bin/env python3
"""Re-run both Proteus arms across the whole corpus, then judge every clone.

Why only two arms
─────────────────
``single_shot`` and ``single_shot_repair`` never touch the Connector, the
Verifier or the Logic Substitution Agent, so none of the fixes this re-run
exists to measure can reach them. Leaving their 72 rows untouched is not a
shortcut -- it is what keeps them a control. Copy them from the existing
results file when you assemble the final table.

    2 arms x 12 programs x 3 seeds = 72 runs

Order: SEED-MAJOR
─────────────────
The loop runs seed 1 for every program and both arms, then seed 2, then seed 3.
Arm-major order would mean that a batch dying at unit 40 leaves one complete arm
and nothing of the other -- unusable, and unusable after paying for 40 runs.
Seed-major means every completed pass is a balanced 12-program, 2-arm slice that
stands on its own; a batch stopped after 24 units is a complete 1-seed study.

Resumability
────────────
A unit is (config, program, run_id). On startup the CSV is read and finished
units are skipped, so an interrupted batch resumes where it stopped and no paid
run is repeated.

    A run that made ZERO LLM calls does not count as finished.

That is not hypothetical. Earlier in this project two runs "completed" with exit
code 0, printed a summary, appended a row and did no work at all, because the
API account was out of credits -- the failure was swallowed and the row recorded
``migration_success=N, failure_mode=nonconvergence``. Left alone those rows look
exactly like a genuine failure of the pipeline. So void rows are pruned at
startup (after a backup) and their units re-run, and two void runs in a row abort
the batch rather than burning through the remaining units producing fiction.

Nothing is ever deleted: the clone directory and the CSV are new paths, and the
prune step backs the CSV up first.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time

ARMS = ("proteus_full", "proteus_no_verif")
PROGRAMS = (
    "A1_crc_telemetry", "A2_sensor_json_logger", "A3_iso_ts_parser",
    "B1_scada_poller", "B2_modbus_meter_reader", "B3_mqtt_telemetry_bridge",
    "C1_pid_pump_controller", "C2_tank_level_fsm", "C3_anomaly_threshold_engine",
    "D1_boiler_control_loop", "D2_pump_station_scheduler", "D3_telemetry_gateway",
)


def _rows(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _unit(row: dict) -> tuple:
    return (row.get("config", ""), row.get("program", ""), str(row.get("run_id", "")))


def _is_void(row: dict) -> bool:
    """A row whose run made no LLM calls: the work never happened."""
    try:
        return int(float(row.get("llm_calls") or 0)) == 0
    except (TypeError, ValueError):
        return True


def _prune_void(path: str) -> int:
    """Drop rows that recorded no LLM calls, so their units are re-run."""
    rows = _rows(path)
    if not rows:
        return 0
    keep = [r for r in rows if not _is_void(r)]
    if len(keep) == len(rows):
        return 0
    shutil.copy(path, path + ".with-void-rows")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(keep)
    return len(rows) - len(keep)


def _spent(path: str) -> float:
    total = 0.0
    for row in _rows(path):
        try:
            total += float(row.get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _plan(csv_path: str, arms, programs, seeds) -> list[tuple]:
    done = {_unit(r) for r in _rows(csv_path) if not _is_void(r)}
    return [(arm, program, run)                     # seed-major: see module docstring
            for run in seeds
            for program in programs
            for arm in arms
            if (arm, program, str(run)) not in done]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-run both Proteus arms over the corpus, then judge.")
    ap.add_argument("--corpus", default=os.path.expanduser("~/Downloads/evaluation"))
    ap.add_argument("--csv", default="results_rerun.csv")
    # A subdirectory, never `final_v4` itself: the archive's clones live at
    # final_v4/<config>/<program>_run<n>, which is exactly where a rerun would
    # write. save_clone moves an existing clone aside rather than deleting it,
    # so nothing is lost -- but every path in differential_fuzz.py and in this
    # project's analysis would then resolve to the NEW clone, and today's
    # findings would quietly stop reproducing.
    ap.add_argument("--clone-dir", default="final_v4/fixed")
    ap.add_argument("--config", action="append", choices=ARMS,
                    help="limit to one arm (repeatable); default is both")
    ap.add_argument("--program", action="append",
                    help="limit to one program (repeatable); default is all 12")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--max-cost", type=float, default=60.0,
                    help="stop before a run that would push spend past this")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds to wait between runs")
    ap.add_argument("--judge", dest="judge", action="store_true", default=True)
    ap.add_argument("--no-judge", dest="judge", action="store_false")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the estimate; run nothing")
    args = ap.parse_args(argv)

    arms = tuple(args.config) if args.config else ARMS
    programs = tuple(args.program) if args.program else PROGRAMS
    seeds = range(1, args.seeds + 1)

    if not os.path.isdir(args.corpus):
        print(f"corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    # The program list is spelled out above so the plan is readable and the
    # dry run works anywhere, but a name that does not exist in the corpus
    # would fail 3 times before anyone noticed. Check it once, up front.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from smart_loader.eval.proteus_harness import discover_programs
        found = {p.name for p in discover_programs(args.corpus)}
    except Exception as exc:                                   # noqa: BLE001
        print(f"could not read the corpus: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    missing = sorted(set(programs) - found)
    if missing:
        print("these programs are not in the corpus: " + ", ".join(missing),
              file=sys.stderr)
        print("corpus has: " + ", ".join(sorted(found)), file=sys.stderr)
        return 2
    extra = sorted(found - set(PROGRAMS))
    if extra:
        print(f"note: corpus also contains {', '.join(extra)}, not in this "
              f"script's list — add them to PROGRAMS to include them.\n")

    pruned = 0 if args.dry_run else _prune_void(args.csv)
    if pruned:
        print(f"pruned {pruned} void row(s) (no LLM calls) -> "
              f"{args.csv}.with-void-rows; those units will re-run\n")

    todo = _plan(args.csv, arms, programs, seeds)
    total = len(arms) * len(programs) * args.seeds
    spent = _spent(args.csv)

    print(f"corpus   : {args.corpus}")
    print(f"csv      : {args.csv}")
    print(f"clones   : {args.clone_dir}")
    print(f"units    : {len(todo)} to run, {total - len(todo)} already done, "
          f"{total} total")
    print(f"spent    : ${spent:.2f}   cap ${args.max_cost:.2f}")
    # Historical means from the previous full study: proteus_full $0.354,
    # proteus_no_verif $0.173 per run. The current pipeline runs a little
    # richer, so this is a floor rather than a forecast.
    estimate = sum(0.354 if a == "proteus_full" else 0.173 for a, _, _ in todo)
    print(f"estimate : ~${estimate:.2f} for the runs (historical means; the "
          f"current pipeline costs somewhat more)\n")

    if args.dry_run:
        for i, (arm, program, run) in enumerate(todo, 1):
            print(f"  {i:>3}. seed {run}  {arm:18s} {program}")
        print("\ndry run — nothing executed")
        return 0

    consecutive_void = 0
    failures: list[str] = []
    started = time.time()

    for i, (arm, program, run) in enumerate(todo, 1):
        spent = _spent(args.csv)
        if spent >= args.max_cost:
            print(f"\nSTOP: ${spent:.2f} spent, cap is ${args.max_cost:.2f}. "
                  f"{len(todo) - i + 1} unit(s) left. Raise --max-cost to continue.")
            break

        label = f"[{i}/{len(todo)}] seed {run}  {arm:18s} {program}"
        print(f"{label}  ... ", end="", flush=True)
        before = {_unit(r) for r in _rows(args.csv)}

        # Every run's full pipeline output goes to its own file. Without this
        # the batch captured the output and threw it away, and a diagnosis
        # afterwards had to be reconstructed from the CSV and the clone --
        # which is no way to spend two hours unattended. The log is where you
        # see which checks fired, which repair cycles ran, and what the
        # Connector actually wrote.
        log_dir = os.path.join(args.clone_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{arm}_{program}_run{run}.log")

        proc = subprocess.run(
            [sys.executable, "run_axiom_arms.py",
             "--corpus", args.corpus, "--config", arm, "--program", program,
             "--runs", "1", "--start-run", str(run),
             "--csv", args.csv, "--clone-dir", args.clone_dir],
            capture_output=True, text=True)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(f"$ run_axiom_arms.py --config {arm} --program {program} "
                     f"--runs 1 --start-run {run}\n\n")
            fh.write(proc.stdout or "")
            if proc.stderr:
                fh.write("\n--- stderr ---\n" + proc.stderr)

        rows = _rows(args.csv)
        new = [r for r in rows if _unit(r) not in before]
        if proc.returncode != 0 and not new:
            failures.append(f"{arm}/{program}/run{run}: exit {proc.returncode}")
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
            print(f"FAILED (exit {proc.returncode}) {tail[0][:60] if tail else ''}"
                  f"  -> {log_path}")
            consecutive_void = 0
            continue

        if new and _is_void(new[0]):
            consecutive_void += 1
            print("VOID — 0 LLM calls (out of credits?)")
            if consecutive_void >= 2:
                print("\nSTOP: two void runs in a row. The API is not doing work; "
                      "the rows recorded so far are real, the void ones are pruned "
                      "on the next start. Fix billing and re-run this script.")
                break
            continue

        consecutive_void = 0
        row = new[0] if new else {}
        print(f"succ={row.get('migration_success','?')} "
              f"${float(row.get('cost_usd') or 0):.3f} "
              f"({row.get('llm_calls','?')} calls, {row.get('wall_time_s','?')}s)")
        if args.sleep:
            time.sleep(args.sleep)

    spent = _spent(args.csv)
    done = len([r for r in _rows(args.csv) if not _is_void(r)])
    print(f"\nruns: {done}/{total} complete   spend: ${spent:.2f}   "
          f"elapsed: {(time.time() - started) / 60:.1f} min")
    print(f"logs: {os.path.join(args.clone_dir, 'logs')}/")
    for f in failures:
        print(f"  FAILED  {f}")

    if not args.judge:
        return 0
    if done < total:
        print(f"\n{total - done} unit(s) still missing — judging what exists. "
              f"Re-run this script to finish, then judge again (cached verdicts "
              f"are free).")

    print("\njudging clones ...")
    judged = subprocess.run(
        [sys.executable, "judge_clones.py", "--csv", args.csv,
         "--corpus", args.corpus, "--clones", args.clone_dir])
    return judged.returncode


if __name__ == "__main__":
    raise SystemExit(main())
