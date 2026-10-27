#!/usr/bin/env python3
"""Sweep length targets and report the diversity / accuracy trade-off.

This is the experiment for the paper, not a demo. A single run at one target
tells you almost nothing; the interesting question is how the system behaves
ACROSS requested sizes:

  * does achieved diversity rise with the size of the requested change?
  * where does the length target start being missed, and in which direction?
  * does equivalence ever break as transformations stack up?
  * do shrink targets behave differently from grow targets? (they should —
    the catalogue is much weaker at shrinking)

Usage
─────
    python3 run_experiment.py                       # hand-written only
    python3 run_experiment.py --use-generated       # + LLM-designed
    python3 run_experiment.py --use-generated --csv results.csv

Every accepted variant is checked by all four oracle layers, including the
script-level I/O comparison, so each row is a fully verified data point.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.isomorph.design import register_generated
from smart_loader.isomorph.metrics import DEFAULT_RANKING_METRIC, ranking_metrics
from smart_loader.isomorph.oracle import CorpusOracle
from smart_loader.isomorph.search import LengthTargetedSearch
from smart_loader.isomorph.spt import SPT_REGISTRY

MODULES = ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]
RATIOS = [0.85, 0.90, 1.00, 1.10, 1.20, 1.30]


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Correlation coefficient, or nan when either series is constant."""
    if len(xs) < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else float("nan")


def metric_cross_check(rows: list[dict], ranking_metric: str) -> None:
    """Audit the ranking signal against the metrics that do not steer the search.

    A single metric cannot audit itself. The search optimises one number, so if
    that number is a poor proxy for the property we actually care about, every
    result looks good by construction and nothing in the run says otherwise.
    The other metrics exist to make that failure visible.

    Two symptoms are worth catching, and both have been real here:

    *A metric that is anti-correlated with the ranking signal.* If variants
    scoring high on the ranking metric score LOW on control-flow change, the
    search is rewarding statement padding over restructuring — which undercuts
    the claim that the variant is structurally different. Measured at r = -0.189
    for cfg_shape against ast_structural, which is what prompted adding a
    loop-restructuring transformation to the catalogue.

    *A metric that is nearly redundant.* ncd correlates with ast_structural at
    r = +0.967, so reporting it as independent supporting evidence overstates
    the evidence base. Better to say so than to let a reader assume four
    independent measurements where there are closer to three.

    The top-by-each-metric rows below are the concrete form of the same point:
    when the best variant by the ranking signal has zero control-flow change,
    the ranking signal is measuring the wrong thing.
    """
    div_cols = sorted(c for c in rows[0] if c.startswith("div_"))
    rank_col = f"div_{ranking_metric}"
    if rank_col not in div_cols or len(rows) < 3:
        return

    print("\n" + "=" * 78)
    print(f"METRIC CROSS-CHECK — are the non-ranking metrics telling a "
          f"different story?")
    base = [float(r[rank_col]) for r in rows]
    print(f"{'metric':24s} {'r vs ' + ranking_metric:>22s}   interpretation")
    for col in div_cols:
        if col == rank_col:
            continue
        r = _pearson(base, [float(x[col]) for x in rows])
        if r != r:                      # nan
            note = "constant — no signal"
        elif r < 0:
            note = "ANTI-CORRELATED — ranking may reward the wrong property"
        elif r > 0.9:
            note = "near-duplicate — adds little independent evidence"
        elif r > 0.5:
            note = "partially redundant"
        else:
            note = "independent"
        print(f"  {col[4:]:22s} {r:>+22.3f}   {note}")

    # The concrete version of the correlation: what does the top variant by each
    # metric actually look like under the other metrics?
    print("\n  best variant by each metric (same 24 runs, different orderings)")
    for col in div_cols:
        best = max(rows, key=lambda r: float(r[col]))
        others = "  ".join(f"{c[4:]} {float(best[c]):.4f}"
                           for c in div_cols if c != col)
        star = " <- ranking signal" if col == rank_col else ""
        print(f"    by {col[4:]:18s} {best['module']:18s} "
              f"{col[4:]} {float(best[col]):.4f}{star}")
        print(f"       {'':18s} {'':18s} {others}")


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=os.path.join(here, "ics_corpus"))
    parser.add_argument("--use-generated", action="store_true")
    parser.add_argument("--ratios", default=",".join(str(r) for r in RATIOS))
    parser.add_argument("--beam-size", type=int, default=3)
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument("--csv", default=None, help="write per-row results here")
    parser.add_argument("--ranking-metric", default=DEFAULT_RANKING_METRIC,
                        choices=ranking_metrics(),
                        help="which rename-insensitive metric breaks ties in the "
                             "beam search. All metrics are reported either way; "
                             "this only selects the one that STEERS the search. "
                             "Diversity values are not comparable across modes — "
                             "compare length miss, on-target rate and oracle pass "
                             "rate (default: %(default)s)")
    parser.add_argument("--artifact-dir", default=None,
                        help="persist every oracle-passing candidate under "
                             "<dir>/<module>/<ratio>/, with a manifest.json and "
                             "winner.py per cell. Off by default — a full sweep "
                             "writes thousands of files.")
    args = parser.parse_args(argv)

    oracle = CorpusOracle(args.corpus)
    n_generated = register_generated() if args.use_generated else 0
    ratios = [float(r) for r in args.ratios.split(",")]
    search = LengthTargetedSearch(oracle, beam_size=args.beam_size,
                                  max_iterations=args.max_iterations,
                                  ranking_metric=args.ranking_metric,
                                  artifact_dir=args.artifact_dir)

    print(f"catalogue: {len(SPT_REGISTRY)} transformations "
          f"({n_generated} LLM-designed)")
    print(f"oracle   : 4 layers incl. script-level __main__ check")
    print(f"ranking  : {args.ranking_metric}"
          + (f"   artefacts -> {args.artifact_dir}" if args.artifact_dir else ""))
    print(f"sweep    : {len(ratios)} targets x {len(MODULES)} modules "
          f"= {len(ratios)*len(MODULES)} variants\n")

    rows = []
    print(f"{'ratio':>6} {'module':18s} {'orig':>5} {'want':>5} {'got':>5} "
          f"{'miss':>5} {'diversity':>10} {'#tf':>4}  oracle")
    print("-" * 78)
    for ratio in ratios:
        for module in MODULES:
            base = len(oracle.module_source(module).splitlines())
            target = max(1, int(round(base * ratio)))
            r = search.run(module, target, label=f"ratio_{ratio:.2f}")
            rows.append({
                "ratio": ratio, "module": module,
                "ranking_metric": r.ranking_metric,
                "original_lines": r.original_lines,
                "requested_lines": r.requested_lines,
                "achieved_lines": r.achieved_lines,
                "length_error": r.length_error,
                "abs_error": abs(r.length_error),
                "headline_diversity": round(r.headline_diversity, 4),
                "transformations": len(r.applied),
                "applied": "|".join(r.applied),
                "oracle_passed": r.oracle_passed,
                "candidates_generated": r.candidates_generated,
                "candidates_equivalent": r.candidates_equivalent,
                "duration_s": round(r.duration_s, 2),
                **{f"div_{k}": round(v, 4) for k, v in r.diversity.items()},
            })
            print(f"{ratio:>6.2f} {module:18s} {r.original_lines:5d} "
                  f"{r.requested_lines:5d} {r.achieved_lines:5d} "
                  f"{r.length_error:+5d} {r.headline_diversity:10.4f} "
                  f"{len(r.applied):4d}  {'PASS' if r.oracle_passed else 'FAIL'}",
                  flush=True)

    # ── analysis ─────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("BY REQUESTED DIRECTION")
    print(f"{'direction':12s} {'n':>3} {'mean|miss|':>11} {'mean div':>9} "
          f"{'on target':>10}")
    for label, subset in (
        ("shrink", [r for r in rows if r["ratio"] < 1.0]),
        ("same",   [r for r in rows if r["ratio"] == 1.0]),
        ("grow",   [r for r in rows if r["ratio"] > 1.0]),
    ):
        if not subset:
            continue
        on = sum(1 for r in subset if r["abs_error"] <= 1) / len(subset)
        print(f"{label:12s} {len(subset):3d} "
              f"{statistics.mean(r['abs_error'] for r in subset):11.2f} "
              f"{statistics.mean(r['headline_diversity'] for r in subset):9.4f} "
              f"{on:9.0%}")

    print("\nDOES DIVERSITY RISE WITH THE SIZE OF THE REQUESTED CHANGE?")
    for ratio in ratios:
        subset = [r for r in rows if r["ratio"] == ratio]
        delta = statistics.mean(abs(r["requested_lines"] - r["original_lines"])
                                for r in subset)
        div = statistics.mean(r["headline_diversity"] for r in subset)
        bar = "#" * int(div * 200)
        print(f"  ratio {ratio:.2f}  mean requested delta {delta:5.1f} lines  "
              f"diversity {div:.4f}  {bar}")

    # ── ablation row ─────────────────────────────────────────────────────
    # The columns that are comparable ACROSS ranking modes. Diversity is
    # deliberately not among them: ast_structural and composite are different
    # scales, so ordering their decimals says nothing. cfg_shape of the
    # selected winners IS comparable, and it is the thing the composite is
    # meant to move — whether the search now picks variants whose branching
    # structure actually changed.
    on_target = sum(1 for r in rows if r["abs_error"] <= 1) / len(rows)
    print("\n" + "=" * 78)
    print("ABLATION ROW — comparable across ranking modes")
    print(f"{'ranking signal':18s} {'mean|miss|':>11} {'on-target':>10} "
          f"{'oracle pass':>12} {'mean cfg_shape of winners':>27} "
          f"{'winners w/ cfg>0':>18}")
    nonzero_cfg = sum(1 for r in rows if r.get("div_cfg_shape", 0.0) > 0.0)
    print(f"{args.ranking_metric:18s} "
          f"{statistics.mean(r['abs_error'] for r in rows):11.2f} "
          f"{on_target:9.0%} "
          f"{sum(1 for r in rows if r['oracle_passed'])}/{len(rows):<11d} "
          f"{statistics.mean(r.get('div_cfg_shape', 0.0) for r in rows):27.4f} "
          f"{nonzero_cfg:>13d}/{len(rows)}")

    print("\nREACH (largest achieved change, per direction)")
    for label, subset in (
        ("shrink", [r for r in rows if r["ratio"] < 1.0]),
        ("grow",   [r for r in rows if r["ratio"] > 1.0]),
    ):
        if not subset:
            continue
        deltas = [r["achieved_lines"] - r["original_lines"] for r in subset]
        extreme = min(deltas) if label == "shrink" else max(deltas)
        total = sum(d for d in deltas if (d < 0 if label == "shrink" else d > 0))
        movers = sorted({r["module"] for r, d in zip(subset, deltas)
                         if (d < 0 if label == "shrink" else d > 0)})
        print(f"  {label:7s} best single {extreme:+d} lines, total {total:+d} lines, "
              f"modules that moved: {', '.join(movers) or 'none'}")

    metric_cross_check(rows, args.ranking_metric)

    passed = sum(1 for r in rows if r["oracle_passed"])
    print(f"\nEQUIVALENCE: {passed}/{len(rows)} variants passed all four oracle "
          f"layers ({passed/len(rows):.0%})")
    print(f"SEARCH     : {sum(r['candidates_generated'] for r in rows)} candidates "
          f"generated, {sum(r['candidates_equivalent'] for r in rows)} equivalent")
    print(f"TIME       : {sum(r['duration_s'] for r in rows):.1f}s total")

    used: dict[str, int] = {}
    for r in rows:
        for name in filter(None, r["applied"].split("|")):
            used[name] = used.get(name, 0) + 1
    print("\nTRANSFORMATION USAGE (how often each was selected):")
    for name, count in sorted(used.items(), key=lambda kv: -kv[1]):
        tag = "" if name in SPT_REGISTRY and not SPT_REGISTRY[name].description.startswith("[LLM") else "  [LLM]"
        print(f"  {count:3d}x  {name}{tag}")
    unused = sorted(set(SPT_REGISTRY) - set(used))
    if unused:
        print(f"  never selected: {', '.join(unused)}")

    if args.csv:
        with open(args.csv, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
