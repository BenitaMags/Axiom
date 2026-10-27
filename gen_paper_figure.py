#!/usr/bin/env python3
"""Emit the paper's results figure as a vector PDF, computed from results_FINAL.csv.

Two panels, because the result is a trade-off and one panel cannot show a
trade-off honestly.

  (a) what each configuration DELIVERED, every run classified
  (b) the precision of each configuration's own acceptance signal

Panel (a) alone would suggest the single-shot baseline wins: it has the most
accepted clones. Panel (b) alone would suggest full Proteus wins: it has the
lowest defect rate. Neither is the finding. The finding is that these move in
opposite directions, and only showing both makes that visible.

    python3 gen_paper_figure.py --csv ~/Downloads/evaluation/reporting/results_FINAL.csv \\
        --out paper/results_figure.pdf

Vector PDF, no rasterisation, greyscale-safe hatching so it survives a
black-and-white print of the proceedings.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ARMS = [
    ("single_shot",      "Single-shot LLM"),
    ("proteus_no_verif", "Proteus\nw/o verification"),
    ("proteus_full",     "Proteus (full)"),
]

#: Worst first, so the stack reads top-to-bottom as severity descends and the
#: eye lands on the failures rather than on a bar of undifferentiated height.
#: Greyscale values chosen so the ordering survives a monochrome print; the
#: hatching carries the same information again for anyone printing without
#: greys at all.
BANDS = [
    ("BROKEN", "broken",  "#3b3b3b", "xxx"),
    ("MAJOR",  "major",   "#6e6e6e", "///"),
    ("MINOR",  "minor",   "#a8a8a8", "\\\\\\"),
    ("CLEAN",  "clean",   "#dcdcdc", ""),
    ("NO-OP",  "no-op",   "#ffffff", "..."),
    # One single_shot run has no verdict: the judge returned an empty
    # completion on four attempts with backoff. Shown rather than omitted --
    # a bar that stops at 35 of 36 with no explanation invites the reader to
    # assume a plotting error, and quietly dropping an unreviewed run would
    # shrink that arm's denominator in its own favour.
    ("ERROR",  "unreviewed", "#ffffff", ""),
]

SERIOUS = ("BROKEN", "MAJOR")


def load(path: str) -> list:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="paper/results_figure.pdf")
    args = ap.parse_args(argv)

    rows = load(args.csv)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.5))

    # ── (a) every run, classified ──────────────────────────────────────────
    x = range(len(ARMS))
    bottoms = [0.0] * len(ARMS)
    for verdict, label, colour, hatch in BANDS:
        heights = []
        for config, _ in ARMS:
            counts = Counter(r["judge_verdict"] for r in rows
                             if r["config"] == config)
            heights.append(counts.get(verdict, 0))
        ax1.bar(x, heights, bottom=bottoms, label=label, color=colour,
                edgecolor="black", linewidth=0.6, hatch=hatch, width=0.62)
        bottoms = [b + h for b, h in zip(bottoms, heights)]

    ax1.set_xticks(list(x))
    ax1.set_xticklabels([label for _, label in ARMS], fontsize=8)
    ax1.set_ylabel("runs (of 36)", fontsize=9)
    ax1.set_title("(a) blind review of every generated clone", fontsize=9.5)
    ax1.legend(fontsize=7, ncol=6, loc="upper center",
               bbox_to_anchor=(0.5, -0.13), frameon=False, columnspacing=1.0)
    ax1.tick_params(labelsize=8)
    ax1.set_ylim(0, 40)
    ax1.spines[["top", "right"]].set_visible(False)

    # ── (b) the trade-off ──────────────────────────────────────────────────
    # Accepted count on the left axis, defect rate among those accepted on the
    # right. Plotted together deliberately: read either alone and you get the
    # wrong answer about which configuration is better.
    accepted, defect_rate = [], []
    for config, _ in ARMS:
        acc = [r for r in rows
               if r["config"] == config and r["migration_success"] == "Y"]
        bad = sum(r["judge_verdict"] in SERIOUS for r in acc)
        accepted.append(len(acc))
        defect_rate.append(100.0 * bad / len(acc) if acc else 0.0)

    ax2.bar(x, accepted, color="#dcdcdc", edgecolor="black", linewidth=0.6,
            width=0.62, label="clones accepted")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([label for _, label in ARMS], fontsize=8)
    ax2.set_ylabel("clones accepted (of 36)", fontsize=9)
    ax2.set_ylim(0, 40)
    ax2.tick_params(labelsize=8)
    ax2.spines[["top"]].set_visible(False)

    ax3 = ax2.twinx()
    ax3.plot(x, defect_rate, marker="o", color="black", linewidth=1.6,
             markersize=6, label="defect rate among accepted")
    for xi, rate in zip(x, defect_rate):
        ax3.annotate(f"{rate:.0f}%", (xi, rate), textcoords="offset points",
                     xytext=(0, 9), ha="center", fontsize=8.5)
    ax3.set_ylabel("defective, of accepted (%)", fontsize=9)
    ax3.set_ylim(0, 100)
    ax3.tick_params(labelsize=8)
    ax3.spines[["top"]].set_visible(False)

    ax2.set_title("(b) more clones accepted, more defects shipped", fontsize=9.5)
    handles = [ax2.patches[0], ax3.lines[0]]
    ax2.legend(handles, ["clones accepted", "defect rate"], fontsize=7,
               ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.13),
               frameon=False)

    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}")
    for (config, _), acc, rate in zip(ARMS, accepted, defect_rate):
        print(f"  {config:<18} accepted {acc:>2}/36   defect rate {rate:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
