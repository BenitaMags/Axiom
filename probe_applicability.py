#!/usr/bin/env python3
"""Which transformations actually fire, on how many modules, for how many lines.

Measured directly against the pristine corpus — not inferred from what the
search happened to select. A transformation sitting at 0/4 is dead weight
inflating the catalogue count, so this is the check that keeps the reported
catalogue size honest.

    python3 probe_applicability.py                 # hand-written only
    python3 probe_applicability.py --use-generated # + LLM-designed
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.isomorph.design import register_generated
from smart_loader.isomorph.oracle import CorpusOracle
from smart_loader.isomorph.spt import spts

MODULES = ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=os.path.join(here, "ics_corpus"))
    parser.add_argument("--use-generated", action="store_true")
    args = parser.parse_args(argv)

    oracle = CorpusOracle(args.corpus)
    n_generated = register_generated() if args.use_generated else 0

    print(f"{'transformation':32s} {'dir':8s} {'hits':>5} {'Δlines':>7}  modules")
    print("-" * 96)
    dead: list[str] = []
    shrinkers: list[str] = []
    for spt in spts():
        hits, deltas = [], []
        for module in MODULES:
            source = oracle.module_source(module)
            try:
                produced = spt(source)
            except Exception:
                continue
            if produced != source:
                hits.append(module)
                deltas.append(len(produced.splitlines()) - len(source.splitlines()))
        mean = sum(deltas) / len(deltas) if deltas else 0.0
        if not hits:
            dead.append(spt.name)
        if mean < 0:
            shrinkers.append(spt.name)
        print(f"{spt.name:32s} {spt.direction:8s} {len(hits)}/4   {mean:+7.1f}  "
              f"{', '.join(hits) or '— NEVER FIRES'}")

    print("-" * 96)
    print(f"catalogue: {len(spts())} transformations ({n_generated} LLM-designed)")
    print(f"effective: {len(spts()) - len(dead)} "
          f"({'no dead entries' if not dead else 'DEAD: ' + ', '.join(dead)})")
    print(f"can remove a line: {len(shrinkers)} "
          f"({', '.join(shrinkers) or 'none'})")
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
