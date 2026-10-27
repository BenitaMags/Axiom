#!/usr/bin/env python3
"""Prove that every CSV row describes the clone still sitting on disk.

    python3 verify_clones.py --csv results_FOUR_ARM_FINAL.csv --clones clones

Why
───
`clones/` is not automatically a faithful archive of the CSV beside it. The
runners used to create the clone directory with ``exist_ok=True`` and copy files
in one at a time, so a later run into the same ``--clone-dir`` overwrote matching
names, left non-matching ones behind, and produced a directory that was a MIXTURE
of two runs. Seven of the 144 rows in ``results_FOUR_ARM_FINAL.csv`` ended up
describing clones that no longer matched what was on disk -- one row dated
2026-09-02 beside files dated 2026-08-29 -- and nothing surfaced it until the
clones were re-scored weeks later. Two findings were briefly reported off those
stale directories before the disagreement was noticed.

`save_clone` now prevents the overwrite and records a `clone_sha`. This script is
the other half: it checks the recorded hash against the files, so the next
mismatch is a loud failure rather than a silent one.

Rows written before `clone_sha` existed have no hash to check. Those fall back to
re-running the program's tests and comparing against `tests_passed`/`tests_total`
-- weaker, since a different clone can coincidentally pass the same number of
tests, but it is what caught the original seven.

Exit status is 1 if any row disagrees, so this can gate a release or a paper
table.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import smart_loader.eval.proteus_harness as H  # noqa: E402


def clone_path(clones_root: str, row: dict) -> str:
    return os.path.join(clones_root, row["config"],
                        f"{row['program']}_run{row['run_id']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--clones", required=True)
    ap.add_argument("--corpus", default="~/Downloads/evaluation")
    ap.add_argument("--quiet", action="store_true",
                    help="print only disagreements and the summary")
    args = ap.parse_args(argv)

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    programs = {p.name: p for p in H.discover_programs(corpus)}
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))

    by_hash = by_tests = missing = unknown = 0
    bad: list[tuple] = []

    for row in rows:
        program = programs.get(row["program"])
        if program is None:
            unknown += 1
            continue
        path = clone_path(args.clones, row)
        if not os.path.isdir(path):
            missing += 1
            bad.append((row, "clone directory is missing", path))
            continue

        recorded = (row.get("clone_sha") or "").strip()
        if recorded:
            actual = H.clone_fingerprint(path)
            by_hash += 1
            if actual != recorded:
                bad.append((row, f"clone_sha {recorded} but files hash "
                                 f"{actual}", path))
            continue

        # No recorded hash: fall back to the test counts.
        by_tests += 1
        passed, total, _, _ = H.run_tests(path, program)
        if (str(passed) != row["tests_passed"]
                or str(total) != row["tests_total"]):
            bad.append((row, f"csv says tests {row['tests_passed']}/"
                             f"{row['tests_total']}, clone now scores "
                             f"{passed}/{total}", path))

    for row, why, path in bad:
        print(f"DISAGREES  {row['config']:<20}{row['program']:<30}"
              f"run{row['run_id']}: {why}")
        if not args.quiet:
            print(f"           {path}")

    print(f"\n{len(rows)} row(s): {by_hash} checked by clone_sha, "
          f"{by_tests} by test counts, {missing} clone(s) missing, "
          f"{unknown} program(s) not in the corpus")
    if bad:
        print(f"{len(bad)} row(s) DISAGREE with the clone on disk. Those rows "
              f"and their clones are not the same artefact; do not re-score "
              f"them without re-running.")
        return 1
    print("every row agrees with its clone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
