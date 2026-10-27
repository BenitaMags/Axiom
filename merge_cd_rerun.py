#!/usr/bin/env python3
"""Fold the C/D re-run into results_FINAL.csv, replacing the stale rows.

The proteus_full rows for categories C and D were recorded while the Logic
Substitution Agent was being blocked by a guard that fired before its oracle
was ever consulted, so ``logic_mutated`` reads NA on all eighteen of them. The
re-run produces those rows properly; this script swaps them in rather than
appending, because appending would leave two rows per (program, run) and every
rate in the summary would silently be computed over a doubled denominator.

    python3 merge_cd_rerun.py --into ~/Downloads/evaluation/reporting/results_FINAL.csv \
                              --from cd_rerun.csv

A timestamped backup of the target is written first. Rows are keyed on
(program, config, run_id), so re-running is safe: the same keys are replaced
again rather than accumulating.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--into", required=True)
    ap.add_argument("--from", dest="source", required=True)
    args = ap.parse_args(argv)

    with open(args.into, encoding="utf-8", newline="") as fh:
        target = list(csv.DictReader(fh))
        fields = list(target[0].keys())
    with open(args.source, encoding="utf-8", newline="") as fh:
        fresh = list(csv.DictReader(fh))

    if not fresh:
        print("source CSV has no rows — nothing to merge")
        return 1

    backup = f"{args.into}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy(args.into, backup)

    def key(row):
        return (row["program"], row["config"], str(row["run_id"]))

    # Columns the re-run added (the iso_* diversity metrics) are appended to
    # the header rather than dropped. Dropping them would silently discard the
    # one thing the re-run exists to capture, and the loss would be invisible:
    # the file would still have 108 well-formed rows. Existing rows get a
    # blank in the new columns, which is honest — they were never measured.
    added_cols = [c for c in fresh[0].keys() if c not in fields]
    fields = fields + added_cols

    incoming = {key(r): r for r in fresh}
    replaced = 0
    merged = []
    for row in target:
        new = incoming.pop(key(row), None)
        if new is None:
            merged.append(row)
            continue
        merged.append({f: new.get(f, row.get(f, "")) for f in fields})
        replaced += 1
    for leftover in incoming.values():
        merged.append({f: leftover.get(f, "") for f in fields})

    with open(args.into, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)

    print(f"backup   : {os.path.basename(backup)}")
    print(f"replaced : {replaced} row(s)")
    print(f"added    : {len(incoming)} row(s)")
    print(f"total    : {len(merged)} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
