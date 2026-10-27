#!/usr/bin/env python3
"""Record golden vectors for the C/D evaluation programs.

Run once against the pristine corpus. Every probe's output is frozen into
``golden.json``; from then on a rewritten program must reproduce those values
exactly or the oracle rejects it.

    python3 equivalence/make_golden.py --corpus ~/Downloads/evaluation

Re-run only when the corpus itself changes. Re-recording after a rewrite would
make the oracle agree with whatever it was given, which is the one failure mode
a golden-vector scheme must not have.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oracle import GOLDEN_PATH, ProgramOracle  # noqa: E402
from probes import PROBES  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", default=GOLDEN_PATH)
    args = ap.parse_args(argv)

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    golden: dict = {}
    failed: list[str] = []

    for program in sorted(PROBES):
        path = os.path.join(corpus, program)
        if not os.path.isdir(path):
            print(f"  {program:32s} SKIP — not found", file=sys.stderr)
            continue
        try:
            values = ProgramOracle(path, program).record_golden()
        except Exception as exc:
            print(f"  {program:32s} FAIL — {exc}", file=sys.stderr)
            failed.append(program)
            continue
        # Validate the invariants against the PRISTINE program before
        # trusting them. An invariant the original violates would reject a
        # perfect copy — the oracle would look rigorous and accept nothing.
        # This caught exactly that: an early C1 invariant asserted a clamp
        # that counts_to_level does not perform.
        try:
            violations = ProgramOracle(path, program)._run(
                ProgramOracle(path, program)._stage(), "invariants")[0]
        except Exception as exc:
            violations = [f"invariant check crashed: {exc}"]
        if violations:
            print(f"  {program:32s} BAD INVARIANT — the pristine program "
                  f"violates it: {violations[0][:90]}", file=sys.stderr)
            failed.append(program)
            continue

        golden[program] = values
        n = sum(len(v) if isinstance(v, list) else 1 for v in values.values())
        print(f"  {program:32s} {len(values)} probe group(s), {n} observation(s)")

    if not golden:
        print("no golden vectors recorded", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(golden, handle, indent=1, sort_keys=True)
    print(f"\nwrote {len(golden)} program(s) to {args.out}")
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
