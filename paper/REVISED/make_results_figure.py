#!/usr/bin/env python3
"""Render figs/results_figure.pdf from results_FOUR_ARM_FINAL.csv.

Regenerate with:  python3 figs/make_results_figure.py

The previous figure's panel (b) was titled "more clones accepted, more defects
shipped" and drew a declining defect-rate line (54%, 55%, 33%). Under the
corrected measurements the four defect rates are 55%, 52%, 54% and 59% --
statistically indistinguishable, every pairwise Fisher exact test above 0.75.
There is no trade-off left to draw, and drawing one would assert a relationship
the data does not contain.

Panel (b) instead shows what the data does say: how many migrations each
configuration completes varies widely (23 to 34 of 36), while the fraction of
its own accepted migrations carrying a serious defect does not move at all.
The flat line is the finding.
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CSV = os.path.join(ROOT, "results_FOUR_ARM_FINAL.csv")

ARMS = [("single_shot", "Single-shot\nLLM"),
        ("single_shot_repair", "Single-shot\n+ repair"),
        ("proteus_no_verif", "Proteus\nw/o verification"),
        ("proteus_full", "Proteus\n(full)")]

# Verdict order, worst first, so the eye reads severity bottom-up.
ORDER = ["BROKEN", "MAJOR", "MINOR", "CLEAN", "NO-OP", "ERROR"]
HATCH = {"BROKEN": "xxx", "MAJOR": "///", "MINOR": "\\\\\\",
         "CLEAN": "", "NO-OP": "...", "ERROR": ""}
FACE  = {"BROKEN": "0.25", "MAJOR": "0.55", "MINOR": "0.75",
         "CLEAN": "1.0", "NO-OP": "0.90", "ERROR": "1.0"}
LABEL = {"BROKEN": "broken", "MAJOR": "major", "MINOR": "minor",
         "CLEAN": "clean", "NO-OP": "no-op", "ERROR": "unreviewed"}

rows = list(csv.DictReader(open(CSV)))


def bucket(arm):
    """Verdict counts for one arm. A no-op is counted as a no-op, whatever
    verdict it received: a clone that never migrated is trivially faithful, so
    its CLEAN carries no information and must not be added to the clean bar."""
    out = dict.fromkeys(ORDER, 0)
    for r in (x for x in rows if x["config"] == arm):
        if r["judge_noop"] == "Y":
            out["NO-OP"] += 1
        elif r["judge_verdict"] in ("", "ERROR"):
            out["ERROR"] += 1
        else:
            out[r["judge_verdict"]] += 1
    return out


def accepted(arm):
    """(accepted migrations with a verdict, of those, how many are serious)."""
    rs = [r for r in rows if r["config"] == arm]
    acc = [r for r in rs if r["migration_success"] == "Y"
           and r["judge_verdict"] not in ("", "ERROR", "NO-OP")]
    bad = sum(r["judge_verdict"] in ("MAJOR", "BROKEN") for r in acc)
    return len(acc), bad


def migrations(arm):
    return sum(r["migration_success"] == "Y"
               for r in rows if r["config"] == arm)


fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.1))
x = range(len(ARMS))
names = [n for _, n in ARMS]

# ---- panel (a): every run, classified -------------------------------------
bottoms = [0] * len(ARMS)
for v in ORDER:
    vals = [bucket(a)[v] for a, _ in ARMS]
    ax1.bar(x, vals, bottom=bottoms, width=0.62, facecolor=FACE[v],
            edgecolor="black", linewidth=0.7, hatch=HATCH[v], label=LABEL[v])
    bottoms = [b + v2 for b, v2 in zip(bottoms, vals)]
ax1.set_xticks(list(x)); ax1.set_xticklabels(names, fontsize=8)
ax1.set_ylabel("runs (of 36)", fontsize=9)
ax1.set_ylim(0, 40)
ax1.set_title("(a) blind review of every generated clone", fontsize=9.5)
ax1.tick_params(labelsize=8)
ax1.legend(ncol=3, fontsize=7.2, loc="upper center", frameon=False,
           bbox_to_anchor=(0.5, -0.19))

# ---- panel (b): migrations vary, defect rate does not ---------------------
migs = [migrations(a) for a, _ in ARMS]
rates = [100.0 * accepted(a)[1] / accepted(a)[0] for a, _ in ARMS]

ax2.bar(x, migs, width=0.62, facecolor="0.88", edgecolor="black",
        linewidth=0.7, label="migrations completed")
ax2.set_ylabel("migrations completed (of 36)", fontsize=9)
ax2.set_ylim(0, 40)
ax2.set_xticks(list(x)); ax2.set_xticklabels(names, fontsize=8)
ax2.tick_params(labelsize=8)
# Count labels sit INSIDE the bar; the defect-rate line and its percentage
# labels live above them. Placing both above the bar made the two collide at
# Proteus, where the bar top and the line happen to coincide.
for xi, m in zip(x, migs):
    ax2.text(xi, m - 2.2, str(m), ha="center", va="top", fontsize=8)

ax3 = ax2.twinx()
ax3.plot(x, rates, "o-", color="black", linewidth=1.4, markersize=5,
         label="defect rate among accepted")
ax3.set_ylabel("defective, of accepted (%)", fontsize=9)
ax3.set_ylim(0, 100)
ax3.tick_params(labelsize=8)
for xi, r in zip(x, rates):
    ax3.text(xi, r + 6, f"{r:.0f}%", ha="center", fontsize=8,
             bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                       edgecolor="none"))

ax2.set_title("(b) migrations vary; defect rate does not", fontsize=9.5)
h1, l1 = ax2.get_legend_handles_labels()
h2, l2 = ax3.get_legend_handles_labels()
ax2.legend(h1 + h2, l1 + l2, fontsize=7.2, loc="upper center",
           frameon=False, bbox_to_anchor=(0.5, -0.19), ncol=2)

fig.tight_layout()
os.makedirs(os.path.join(HERE), exist_ok=True)
out = os.path.join(HERE, "results_figure.pdf")
fig.savefig(out, bbox_inches="tight")
fig.savefig(out.replace(".pdf", ".png"), dpi=200, bbox_inches="tight")
print("wrote", out)
for a, n in ARMS:
    acc, bad = accepted(a)
    print(f"  {a:20s} {bucket(a)}  migrations={migrations(a)}  "
          f"defect={bad}/{acc}")
