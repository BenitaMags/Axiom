#!/usr/bin/env python3
"""Design and implement new SPTs with an LLM — Auto-SPT Steps 1 and 2.

Run this ONCE. It calls the LLM to design new transformation types and to
write their implementations, validates every candidate against the corpus
oracle, and saves the survivors to ``generated_spts.json``. After that the
transformations are ordinary deterministic Python and cost nothing to apply —
``run_isomorph_agent.py --use-generated`` picks them up with no LLM calls.

    # design 8 transformations, 3 implementation attempts each
    python3 generate_spts.py --llm claude --model claude-sonnet-5

    # cheaper first pass
    python3 generate_spts.py --llm claude --model claude-sonnet-5 \
        --n-designs 4 --n-implementations 2

Cost note from the paper: Auto-SPT used 20 designs x 20 implementations = 400
calls for well under $50. The defaults here are far smaller; raise them once
you can see the reward numbers are sensible.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.isomorph.design import generate_catalogue


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=os.path.join(here, "ics_corpus"))
    parser.add_argument("--llm", default="claude", help="provider (claude, nvidia, ...)")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--n-designs", type=int, default=8)
    parser.add_argument("--n-implementations", type=int, default=3)
    parser.add_argument("--direction", choices=["grow", "shrink", "neutral"],
                        default=None,
                        help="spend the whole run designing transformations in "
                             "one direction, with direction-specific guidance "
                             "in the prompt (default: the mixed grow-leaning "
                             "cycle). The recorded direction of an accepted "
                             "transformation is always the MEASURED one, not "
                             "the one requested here.")
    parser.add_argument("--reset", action="store_true",
                        help="discard the existing catalogue instead of adding to it")
    parser.add_argument("--out", default=None,
                        help="output JSON (default: smart_loader/isomorph/generated_spts.json)")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.corpus):
        print(f"error: corpus not found: {args.corpus}", file=sys.stderr)
        return 2

    print(f"designing {args.n_designs} transformations with "
          f"{args.llm}/{args.model}, {args.n_implementations} implementation "
          f"attempt(s) each\ndirection: {args.direction or 'mixed (grow-leaning cycle)'}"
          f"\ncorpus: {args.corpus}\n")
    try:
        accepted = generate_catalogue(
            corpus_dir=args.corpus, provider=args.llm, model=args.model,
            n_designs=args.n_designs, n_implementations=args.n_implementations,
            out_path=args.out, reset=args.reset, direction=args.direction,
        )
    except Exception as exc:
        print(f"generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not accepted:
        print("\nNo transformation survived validation. That is a real result, "
              "not a crash — every candidate either broke equivalence, produced "
              "invalid Python, or never applied to any corpus module.")
        return 1

    print("\ncatalogue contents:")
    for spec in sorted(accepted, key=lambda s: -s.reward):
        claim = (f"  (model claimed {spec.declared_direction})"
                 if spec.declared_direction else "")
        print(f"  {spec.name:34s} {spec.direction:8s} reward {spec.reward:.3f}  "
              f"Δlines {spec.mean_line_delta:+.1f}  "
              f"applies to {len(spec.applicable_modules)}/4{claim}")
    shrinkers = [s for s in accepted if s.direction == "shrink"]
    print(f"\nshrink transformations in the catalogue: {len(shrinkers)}"
          + (f" ({', '.join(s.name for s in shrinkers)})" if shrinkers else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
