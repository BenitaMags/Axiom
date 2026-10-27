#!/usr/bin/env python3
"""Drive the Logic Substitution Agent alone, against the ICS corpus.

This exercises the AGENT NODE (``agents/logic_substitution_agent.py``) through
its real state contract — the same way ``core/graph.py`` will call it — rather
than calling the search engine directly. So it tests the thing that actually
goes in the pipeline, including the enable switch, the oracle guard, the
target resolution, telemetry, and the returned state.

Usage
─────
    # all four modules, 15% longer
    python run_isomorph_agent.py

    # one module, exact line target, write the variant out
    python run_isomorph_agent.py --module pid_governor --target-lines 105 \
        --write-variants ./variants

    # shrink instead of grow
    python run_isomorph_agent.py --target-ratio 0.9

Every accepted variant is re-checked against the corpus oracle at the end, in
a scratch copy, so the summary reflects an independent verification rather
than trusting the agent's own report.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.agents.logic_substitution_agent import logic_substitution_agent
from smart_loader.isomorph import telemetry
from smart_loader.isomorph.metrics import DEFAULT_RANKING_METRIC, ranking_metrics

MODULES = ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]


def independent_recheck(corpus: str, variants: dict[str, str]) -> tuple[bool, str]:
    """Drop every accepted variant into a scratch corpus and run the oracle.

    Deliberately independent of the agent: the agent validates each variant
    one module at a time, so this additionally proves the variants are
    correct *together*, which is what a merged deliverable would ship.
    """
    if not variants:
        return True, "no variants to re-check"
    work = tempfile.mkdtemp(prefix="isomorph-recheck-")
    try:
        for entry in os.listdir(corpus):
            src = os.path.join(corpus, entry)
            if os.path.isfile(src):
                shutil.copy(src, work)
        for module, source in variants.items():
            with open(os.path.join(work, f"{module}.py"), "w") as handle:
                handle.write(source)
        proc = subprocess.run([sys.executable, "test_corpus.py"], cwd=work,
                              capture_output=True, text=True, timeout=600)
        tail = (proc.stdout or "").strip().splitlines()
        return proc.returncode == 0, tail[-1] if tail else "(no output)"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _print_diff(module: str, original: str, variant: str) -> None:
    """Unified diff of original vs variant, coloured if rich is available.

    Worth reading rather than trusting: this is where you can see that the
    added lines are load-bearing computation (named intermediates that get
    used) rather than padding, which is the property the whole length-target
    design depends on.
    """
    import difflib

    lines = list(difflib.unified_diff(
        original.splitlines(), variant.splitlines(),
        fromfile=f"{module}.py (original)", tofile=f"{module}.py (variant)",
        lineterm="", n=2,
    ))
    if not lines:
        print(f"\n--- {module}: variant is byte-identical to the original ---")
        return
    try:
        from rich.console import Console
        from rich.syntax import Syntax

        Console().print(Syntax("\n".join(lines), "diff", theme="ansi_dark",
                               word_wrap=True))
    except Exception:
        print("\n".join(lines))


def _print_source(module: str, variant: str) -> None:
    try:
        from rich.console import Console
        from rich.syntax import Syntax

        Console().print(Syntax(variant, "python", theme="ansi_dark",
                               line_numbers=True))
    except Exception:
        print(variant)


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=os.path.join(here, "ics_corpus"),
                        help="directory with the ICS modules + oracle (default: ./ics_corpus)")
    parser.add_argument("--module", action="append", default=None,
                        help="module to diversify (repeatable; default: all four)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--target-lines", type=int, default=None)
    group.add_argument("--target-ratio", type=float, default=1.15)
    parser.add_argument("--beam-size", type=int, default=4)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--write-variants", default=None)
    parser.add_argument("--show-diff", action="store_true",
                        help="print a unified diff of original vs variant")
    parser.add_argument("--show-variant", action="store_true",
                        help="print the complete variant source")
    parser.add_argument("--use-generated", action="store_true",
                        help="also use the LLM-designed transformations from "
                             "generated_spts.json (run generate_spts.py first)")
    parser.add_argument("--generated-path", default=None)
    parser.add_argument("--ranking-metric", default=DEFAULT_RANKING_METRIC,
                        choices=ranking_metrics(),
                        help="rename-insensitive metric the beam search ranks by "
                             "(default: %(default)s). All metrics are reported "
                             "regardless; this only selects the tie-break.")
    parser.add_argument("--artifact-dir", default=None,
                        help="persist every oracle-passing candidate, with a "
                             "manifest.json and winner.py per module (off by "
                             "default)")
    parser.add_argument("--interactive", action="store_true",
                        help="ask for the target line count instead of taking it "
                             "from --target-lines/--target-ratio")
    args = parser.parse_args(argv)

    corpus = os.path.abspath(args.corpus)
    if not os.path.isdir(corpus):
        print(f"error: corpus directory not found: {corpus}", file=sys.stderr)
        return 2

    modules = args.module or MODULES
    accepted: dict[str, str] = {}
    rejected: list[str] = []

    for module in modules:
        path = os.path.join(corpus, f"{module}.py")
        if not os.path.isfile(path):
            print(f"skip {module}: not in corpus", file=sys.stderr)
            continue
        with open(path) as handle:
            source = handle.read()

        # The state dict the graph node will receive in the real pipeline.
        state = {
            "enable_isomorph": True,
            "isomorph_corpus_dir": corpus,
            "isomorph_module": module,
            "source_file": f"{module}.py",
            "patched_code": source,          # standing in for Connector's output
            "isomorph_beam_size": args.beam_size,
            "isomorph_max_iterations": args.max_iterations,
            "_session_id": "standalone",
            "isomorph_use_generated": args.use_generated,
            "isomorph_generated_path": args.generated_path,
            "isomorph_ranking_metric": args.ranking_metric,
            "isomorph_artifact_dir": args.artifact_dir,
            "isomorph_interactive": args.interactive,
        }
        if args.target_lines is not None:
            state["isomorph_target_lines"] = args.target_lines
        elif not args.interactive:
            state["isomorph_target_ratio"] = args.target_ratio

        out = logic_substitution_agent(state)
        variant = out.get("isomorph_variant_code", "")
        if variant:
            accepted[module] = variant
            if args.show_diff:
                _print_diff(module, source, variant)
            if args.show_variant:
                _print_source(module, variant)
        else:
            rejected.append(module)

    if args.write_variants and accepted:
        os.makedirs(args.write_variants, exist_ok=True)
        for module, source in accepted.items():
            with open(os.path.join(args.write_variants, f"{module}.py"), "w") as handle:
                handle.write(source)
        print(f"\nwrote {len(accepted)} variant(s) to {args.write_variants}")

    print("\n" + "=" * 70)
    print("INDEPENDENT RE-CHECK — all accepted variants installed together")
    ok, detail = independent_recheck(corpus, accepted)
    print(f"  {'PASS' if ok else 'FAIL'}: {detail}")
    print("\ntelemetry:", telemetry.get_summary())
    if rejected:
        print("rejected (no verified variant):", ", ".join(rejected))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
