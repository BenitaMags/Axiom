"""Command-line entry point for the isomorphism agent.

    python -m smart_loader.isomorph.cli --corpus ./ics --target-ratio 1.15
    python -m smart_loader.isomorph.cli --corpus ./ics --module pid_governor \
        --target-lines 100 --write-variants ./variants

``--target-ratio`` / ``--target-lines`` supply the size request
non-interactively, which is what the batch experiments need; the interactive
human-in-the-loop prompt is the other path and lives in the pipeline
integration, not here.
"""

from __future__ import annotations

import argparse
import os
import sys

from smart_loader.isomorph import telemetry
from smart_loader.isomorph.metrics import METRIC_REGISTRY
from smart_loader.isomorph.oracle import CorpusOracle
from smart_loader.isomorph.search import LengthTargetedSearch
from smart_loader.isomorph.spt import SPT_REGISTRY

DEFAULT_MODULES = ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate isomorphic program variants.")
    parser.add_argument("--corpus", required=True,
                        help="directory holding the modules plus oracle.py/test_corpus.py/golden.json")
    parser.add_argument("--module", action="append", default=None,
                        help="module to diversify (repeatable; default: all four ICS modules)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--target-lines", type=int, default=None,
                       help="absolute target line count")
    group.add_argument("--target-ratio", type=float, default=1.15,
                       help="target as a ratio of the original size (default 1.15)")
    parser.add_argument("--beam-size", type=int, default=4)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--tolerance", type=float, default=0.10,
                        help="acceptable miss as a fraction of the requested change")
    parser.add_argument("--write-variants", default=None,
                        help="directory to write accepted variants into")
    parser.add_argument("--log", default=None, help="telemetry JSONL path")
    args = parser.parse_args(argv)

    try:
        oracle = CorpusOracle(args.corpus)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    modules = args.module or DEFAULT_MODULES
    search = LengthTargetedSearch(
        oracle, beam_size=args.beam_size, max_iterations=args.max_iterations,
        tolerance_ratio=args.tolerance,
    )

    print(f"catalogue: {len(SPT_REGISTRY)} transformations | "
          f"metrics: {', '.join(METRIC_REGISTRY)}")
    print(f"oracle: {args.corpus}\n")

    failures = 0
    for module in modules:
        try:
            original = oracle.module_source(module)
        except FileNotFoundError:
            print(f"skip {module}: not found in corpus", file=sys.stderr)
            failures += 1
            continue
        base = len(original.splitlines())
        target = args.target_lines if args.target_lines is not None else \
            max(1, int(round(base * args.target_ratio)))

        result = search.run(module, target)
        telemetry.record_variant(result, session_id="cli", log_path=args.log)

        status = "PASS" if result.oracle_passed else "FAIL"
        print(result.summary())
        print(f"    oracle {status} | {result.candidates_equivalent}/"
              f"{result.candidates_generated} equivalent | {result.iterations} iters "
              f"| {result.duration_s:.1f}s")
        print("    " + "  ".join(f"{k}={v:.4f}" for k, v in result.diversity.items()))
        if not result.oracle_passed:
            failures += 1

        if args.write_variants and result.oracle_passed and result.applied:
            os.makedirs(args.write_variants, exist_ok=True)
            out = os.path.join(args.write_variants, f"{module}.py")
            with open(out, "w", encoding="utf-8") as handle:
                handle.write(result.variant_source)
            print(f"    wrote {out}")

    print("\nsummary:", telemetry.get_summary())
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
