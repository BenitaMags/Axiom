#!/usr/bin/env python3
"""Score B2 and D1 as negative controls and recompute the headline rates.

Why
───
B2 and D1 both specify ``pymodbus`` -> ``minimalmodbus``. The originals speak
Modbus over TCP (``ModbusTcpClient(host, port=502)``); ``minimalmodbus`` is a
serial-only library whose ``Instrument(port, slaveaddress, mode='rtu')`` takes a
device path and exposes no TCP or socket entry point at all. There is no correct
migration to write.

Scored as ordinary tasks, these two punish the only behaviour that is actually
right. A system that declines them looks identical to a system that tried and
failed, while a system that ships a plausible-looking but non-functional swap
scores full marks. This script separates the two.

Criterion
─────────
On a negative control the correct outcome is: **do not complete the substitution
to an impossible target**. That is exactly ``lib_swapped_correctly != "Y"``.
We deliberately reuse an existing column rather than inventing a new judgement,
so the scoring can be audited against the raw data.

We additionally record *how* the outcome was reached, because "declined with a
recorded stage failure" and "silently did nothing" are not the same quality of
behaviour even when the outcome matches. An arm that does nothing on every
program would score 100% on negative controls, and that has to stay visible.

    python3 score_negative_controls.py --csv ~/Downloads/evaluation/reporting/results_FINAL.csv

Idempotent: re-running recomputes the same values from the same rows.
"""

from __future__ import annotations

import argparse
import csv

#: Programs whose stated migration target cannot satisfy the original's usage.
#: Keyed to the reason, which is written into the CSV so the decision travels
#: with the data rather than living only in a paper.
NEGATIVE_CONTROLS = {
    "B2_modbus_meter_reader":
        "minimalmodbus is serial-only; original uses Modbus TCP",
    "D1_boiler_control_loop":
        "minimalmodbus is serial-only; original uses Modbus TCP",
}

#: Preferred display order. Any arm present in the CSV but not listed here is
#: appended, so adding a fourth configuration does not silently drop it from
#: the table — which is exactly what a hard-coded list did when
#: ``single_shot_free`` was added.
CONFIG_ORDER = ("single_shot", "single_shot_free",
                "proteus_no_verif", "proteus_full")


def configs_in(rows) -> list:
    present = {r["config"] for r in rows}
    ordered = [c for c in CONFIG_ORDER if c in present]
    return ordered + sorted(present - set(ordered))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write the CSV")
    args = ap.parse_args(argv)

    with open(args.csv, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys())

    for column in ("negative_control", "control_correct", "control_basis"):
        if column not in fields:
            fields.append(column)

    for row in rows:
        program = row["program"]
        if program not in NEGATIVE_CONTROLS:
            row["negative_control"] = "N"
            row["control_correct"] = "NA"
            row["control_basis"] = ""
            continue

        row["negative_control"] = "Y"
        shipped = row.get("lib_swapped_correctly") == "Y"
        row["control_correct"] = "N" if shipped else "Y"
        if shipped:
            row["control_basis"] = "completed a substitution that cannot work"
        elif (row.get("failing_stage") or "").strip() not in ("", "none", "NA"):
            row["control_basis"] = f"declined at {row['failing_stage']}"
        else:
            row["control_basis"] = "did not complete the substitution"

    # ── report ───────────────────────────────────────────────────────────────
    configs = configs_in(rows)
    pos = [r for r in rows if r["negative_control"] == "N"]
    neg = [r for r in rows if r["negative_control"] == "Y"]
    # Per-arm counts, not a division by an assumed arm count.
    n_pos = len([r for r in pos if r["config"] == configs[0]])
    n_neg = len([r for r in neg if r["config"] == configs[0]])

    print(f"positive programs : {n_pos} runs per arm")
    print(f"negative controls : {n_neg} runs per arm "
          f"({', '.join(sorted(NEGATIVE_CONTROLS))})\n")

    head = (f"{'configuration':<18}{'migrations':>12}{'controls':>11}"
            f"{'declined w/ reason':>21}{'correct overall':>17}")
    print(head)
    print("-" * len(head))
    for cfg in configs:
        p = [r for r in pos if r["config"] == cfg]
        n = [r for r in neg if r["config"] == cfg]
        ok_p = sum(1 for r in p if r["migration_success"] == "Y")
        ok_n = sum(1 for r in n if r["control_correct"] == "Y")
        reasoned = sum(1 for r in n if r["control_basis"].startswith("declined"))
        total = ok_p + ok_n
        print(f"{cfg:<18}{f'{ok_p}/{len(p)}':>12}{f'{ok_n}/{len(n)}':>11}"
              f"{f'{reasoned}/{len(n)}':>21}{f'{total}/{len(p) + len(n)}':>17}")

    print("\nper-run detail on the controls:")
    for cfg in configs:
        n = [r for r in neg if r["config"] == cfg]
        bases: dict = {}
        for r in n:
            bases[r["control_basis"]] = bases.get(r["control_basis"], 0) + 1
        summary = "; ".join(f"{v}x {k}" for k, v in sorted(bases.items()))
        print(f"  {cfg:<18} {summary}")

    if args.dry_run:
        print("\ndry run — CSV not written")
        return 0

    with open(args.csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {len(rows)} rows with 3 added columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
