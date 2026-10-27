"""Figures for results_fin_final.csv (144 rows, 4 arms x 12 programs x 3 seeds).

Two outputs:
  fig_outcomes.{png,pdf}  what each arm achieved, on four criteria + failures
  fig_cost.{png,pdf}      what each arm cost, in tokens and wall time

Every number is read from the CSV, not hard-coded, so the figures cannot
drift away from the data they describe.
"""
import csv, math, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "paper", "figs")
os.makedirs(OUT, exist_ok=True)

ARMS = ["single_shot", "single_shot_repair", "proteus_no_verif", "proteus_full"]
LABEL = {"single_shot": "single-shot", "single_shot_repair": "single-shot\n+ repair",
         "proteus_no_verif": "Proteus\nno verification", "proteus_full": "Proteus\nfull"}
# colourblind-safe; the system under test is the dark one
COLOR = {"single_shot": "#BBBBBB", "single_shot_repair": "#88CCEE",
         "proteus_no_verif": "#DDCC77", "proteus_full": "#332288"}

rows = list(csv.DictReader(open(os.path.join(ROOT, "results_fin_final.csv"))))
by = {a: [r for r in rows if r["config"] == a] for a in ARMS}
N = len(by[ARMS[0]])

judge_ok = lambda r: r["judge_verdict"] in ("CLEAN", "MINOR")
# `migration_success` is the full gate documented in migration_ok() and in
# implementation.tex: the five source-level oracle layers (imports, golden
# vectors, invariants, tests, factory-execution smoke check), plus that the
# source genuinely changed and the outgoing dependency is verifiably absent.
# The error contract ("layer 7") is NOT part of this gate -- it is recorded
# but advisory-only by design (tests/test_advisory_never_reverts.py), after
# gating on it was found to actively break working migrations.
CRIT = [("Deterministic\nmigration success", lambda r: r["migration_success"] == "Y"),
        ("Blind LLM judge", judge_ok),
        ("Both agree", lambda r: r["migration_success"] == "Y" and judge_ok(r))]


def wilson(k, n, z=1.96):
    """95% CI for a proportion — honest error bars on 36 samples."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, c - h) * n, min(1, c + h) * n


# ══ Figure 1: outcomes ════════════════════════════════════════════════════
fig = plt.figure(figsize=(13.5, 8.2))
gs = fig.add_gridspec(2, 2, height_ratios=[1.35, 1], hspace=0.52, wspace=0.26,
                      left=0.07, right=0.98, top=0.88, bottom=0.09)

# — panel A: the four criteria —
ax = fig.add_subplot(gs[0, :])
x = np.arange(len(CRIT))
w = 0.2
for i, arm in enumerate(ARMS):
    vals = [sum(1 for r in by[arm] if f(r)) for _, f in CRIT]
    err = np.array([[v - wilson(v, N)[0] for v in vals],
                    [wilson(v, N)[1] - v for v in vals]])
    b = ax.bar(x + (i - 1.5) * w, vals, w, label=LABEL[arm].replace("\n", " "),
               color=COLOR[arm], edgecolor="white", linewidth=0.8,
               yerr=err, capsize=2.5, error_kw={"lw": 1, "ecolor": "#555"})
    ax.bar_label(b, fmt="%d", padding=5, fontsize=9,
                 fontweight="bold" if arm == "proteus_full" else "normal")
ax.set_xticks(x); ax.set_xticklabels([c for c, _ in CRIT], fontsize=10)
ax.set_ylabel(f"runs accepted  (of {N})", fontsize=10)
ax.set_ylim(0, N * 1.12); ax.axhline(N, ls=":", c="#999", lw=1)
ax.set_title("A.  Proteus-full leads the deterministic gate — and does not lead the independent judge",
             fontsize=11.5, fontweight="bold", loc="left", pad=12)
ax.legend(ncol=4, fontsize=9, frameon=False, loc="upper center",
          bbox_to_anchor=(0.5, -0.17))
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", alpha=0.25, lw=0.6)
# mark where the story turns
ax.annotate("independent measure:\nno advantage here",
            xy=(1.30, 17.8), xytext=(1.50, N * 0.86), fontsize=8.5, color="#B03A2E",
            ha="left", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#B03A2E", lw=1.2,
                            connectionstyle="arc3,rad=-0.15"))

# — panel B: catastrophic failures —
ax = fig.add_subplot(gs[1, 0])
bad = lambda r: r["judge_verdict"] in ("BROKEN", "NO-OP")
order = sorted(ARMS, key=lambda a: sum(1 for r in by[a] if bad(r)))
vals = [sum(1 for r in by[a] if bad(r)) for a in order]
b = ax.barh([LABEL[a].replace("\n", " ") for a in order], vals,
            color=[COLOR[a] for a in order], edgecolor="white", height=0.62)
ax.bar_label(b, fmt=" %d", fontsize=10, fontweight="bold")
ax.set_xlabel("runs the judge called BROKEN or NO-OP", fontsize=9.5)
ax.set_xlim(0, max(vals) * 1.3)
ax.set_title("B.  Catastrophic failures — fewest for Proteus-full",
             fontsize=10.5, fontweight="bold", loc="left", pad=8)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="x", alpha=0.25, lw=0.6)
ax.tick_params(labelsize=9)

# — panel C: do reverts land on real defects? —
ax = fig.add_subplot(gs[1, 1])
pf = by["proteus_full"]
rev = [r for r in pf if (r["revert_reason"] or "").strip()]
non = [r for r in pf if not (r["revert_reason"] or "").strip()]
groups = [("verifier\nreverted", rev), ("no revert", non)]
pcts = [100 * sum(1 for r in g if r["judge_verdict"] in ("MAJOR", "BROKEN")) / len(g)
        for _, g in groups]
b = ax.bar([g for g, _ in groups], pcts, width=0.5,
           color=["#B03A2E", "#BBBBBB"], edgecolor="white")
for rect, (lab, g), p in zip(b, groups, pcts):
    n_bad = sum(1 for r in g if r["judge_verdict"] in ("MAJOR", "BROKEN"))
    ax.text(rect.get_x() + rect.get_width() / 2, p + 2.5,
            f"{p:.0f}%\n({n_bad}/{len(g)})", ha="center", fontsize=9.5, fontweight="bold")
ax.set_ylabel("judge found MAJOR or BROKEN", fontsize=9.5)
ax.set_ylim(0, 100); ax.set_yticks([0, 25, 50, 75, 100])
ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=9)
ax.set_title("C.  The verifier's reverts fire on real defects",
             fontsize=10.5, fontweight="bold", loc="left", pad=8)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", alpha=0.25, lw=0.6)
ax.tick_params(labelsize=9)

fig.suptitle("Migration outcomes by arm — 12 programs × 3 seeds, paired",
             fontsize=13.5, fontweight="bold", x=0.07, ha="left", y=0.965)
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(OUT, f"fig_outcomes.{ext}"), dpi=200,
                bbox_inches="tight", facecolor="white")
plt.close(fig)

# ══ Figure 2: cost ════════════════════════════════════════════════════════
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.0))
fig.subplots_adjust(left=0.08, right=0.97, top=0.79, bottom=0.14, wspace=0.28)

tok = [sum(float(r["tokens_total"] or 0) for r in by[a]) / 1e6 for a in ARMS]
tin = [sum(float(r["tokens_in"] or 0) for r in by[a]) / 1e6 for a in ARMS]
tout = [sum(float(r["tokens_out"] or 0) for r in by[a]) / 1e6 for a in ARMS]
wall = [np.mean([float(r["wall_time_s"] or 0) for r in by[a]]) for a in ARMS]
wsd = [np.std([float(r["wall_time_s"] or 0) for r in by[a]]) for a in ARMS]
cost = [sum(float(r["cost_usd"] or 0) for r in by[a]) for a in ARMS]
names = [LABEL[a] for a in ARMS]

b1 = ax1.bar(names, tin, color=[COLOR[a] for a in ARMS], edgecolor="white", label="input")
b2 = ax1.bar(names, tout, bottom=tin, color=[COLOR[a] for a in ARMS], alpha=0.45,
             edgecolor="white", hatch="///", label="output")
for i, (t, c) in enumerate(zip(tok, cost)):
    ax1.text(i, t + max(tok) * 0.035, f"{t:.2f}M\n\\${c:.2f}", ha="center",
             fontsize=9.5, fontweight="bold" if ARMS[i] == "proteus_full" else "normal")
ax1.set_ylabel("tokens across all 36 runs  (millions)", fontsize=10)
ax1.set_ylim(0, max(tok) * 1.25)
ax1.set_title("Tokens and API cost", fontsize=11.5, fontweight="bold", loc="left", pad=10)
ax1.legend(handles=[b1, b2], labels=["input", "output"], fontsize=9, frameon=False, loc="upper left")
ax1.spines[["top", "right"]].set_visible(False)
ax1.grid(axis="y", alpha=0.25, lw=0.6); ax1.tick_params(labelsize=9)

b = ax2.bar(names, wall, color=[COLOR[a] for a in ARMS], edgecolor="white",
            yerr=wsd, capsize=4, error_kw={"lw": 1, "ecolor": "#555"})
for i, (w_, sd_) in enumerate(zip(wall, wsd)):
    ax2.text(i, w_ + sd_ + max(wall) * 0.04, f"{w_:.0f}s", ha="center", fontsize=9.5,
             fontweight="bold" if ARMS[i] == "proteus_full" else "normal")
ax2.set_ylabel("wall-clock seconds per run  (mean ± SD)", fontsize=10)
ax2.set_ylim(0, (max(w + s_ for w, s_ in zip(wall, wsd))) * 1.16)
ax2.set_title("Wall-clock time per run", fontsize=11.5, fontweight="bold", loc="left", pad=10)
ax2.spines[["top", "right"]].set_visible(False)
ax2.grid(axis="y", alpha=0.25, lw=0.6); ax2.tick_params(labelsize=9)

base_t, base_c = tok[0], cost[0]
fig.suptitle("What each arm cost", fontsize=13.5, fontweight="bold", x=0.08, ha="left", y=0.955)
fig.text(0.08, 0.875,
         f"Proteus-full spends {tok[-1]/base_t:.0f}× the tokens and {wall[-1]/wall[0]:.0f}× the wall time of single-shot "
         f"(\\${cost[-1]:.2f} vs \\${base_c:.2f}) — for a blind-judge score it does not lead.",
         fontsize=9.5, color="#555", ha="left")
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(OUT, f"fig_cost.{ext}"), dpi=200,
                bbox_inches="tight", facecolor="white")
plt.close(fig)

print("wrote:")
for f in ("fig_outcomes.png", "fig_outcomes.pdf", "fig_cost.png", "fig_cost.pdf"):
    p = os.path.join(OUT, f)
    print(f"   {p}  ({os.path.getsize(p)//1024} KB)")


# ══ Figure 3: confusion matrices for layers 6 and 7 ═══════════════════════
#
# POSITIVE = "this migration is defective". Ground truth is the blind judge:
# MAJOR / BROKEN / NO-OP = defective; CLEAN / MINOR = acceptable. Rows the
# judge could not score (ERROR, NO-VERDICT) are excluded.
#
# Layer 6's SKIPPED and BASELINE verdicts are counted as "not a defect",
# because that is exactly how migration_ok() treats them.
from matplotlib.colors import to_rgb

scored = [r for r in rows if r["judge_verdict"] not in ("ERROR", "NO-VERDICT")]
defective = lambda r: r["judge_verdict"] in ("MAJOR", "BROKEN", "NO-OP")

DETECTORS = [
    ("Layer 6 — real-library smoke",
     lambda r: r["smoke_path"] == "DEFECT"),
    ("Layer 7 — error contract",
     lambda r: r["error_contract"] == "DEFECT"),
    ("Layer 6 OR layer 7",
     lambda r: r["smoke_path"] == "DEFECT" or r["error_contract"] == "DEFECT"),
    ("Full gate (migration_success)",
     lambda r: r["migration_success"] == "N"),
]


def _cells(pred):
    tp = sum(1 for r in scored if pred(r) and defective(r))
    fp = sum(1 for r in scored if pred(r) and not defective(r))
    fn = sum(1 for r in scored if not pred(r) and defective(r))
    tn = sum(1 for r in scored if not pred(r) and not defective(r))
    return tp, fp, fn, tn


def _shade(base, frac):
    """Blend `base` toward white; more of the base colour for a bigger cell."""
    b = np.array(to_rgb(base))
    return tuple(1 - (1 - b) * (0.12 + 0.88 * frac))


CORRECT, WRONG = "#2E6FB0", "#C0392B"
n_all = len(scored)

fig, axes = plt.subplots(2, 2, figsize=(11.5, 11.8))
fig.subplots_adjust(left=0.13, right=0.97, top=0.845, bottom=0.05,
                    hspace=0.52, wspace=0.34)

for ax, (title, pred) in zip(axes.flat, DETECTORS):
    tp, fp, fn, tn = _cells(pred)
    n = tp + fp + fn + tn
    grid = [[("TP", tp, CORRECT), ("FP", fp, WRONG)],
            [("FN", fn, WRONG), ("TN", tn, CORRECT)]]
    for i in range(2):
        for j in range(2):
            lab, v, base = grid[i][j]
            frac = v / n if n else 0
            ax.add_patch(plt.Rectangle((j, 1 - i), 1, 1,
                                       facecolor=_shade(base, min(1, frac * 1.8)),
                                       edgecolor="white", lw=3))
            dark = frac * 1.8 > 0.55
            ink = "white" if dark else "#222"
            ax.text(j + 0.5, 1 - i + 0.60, f"{v}", ha="center", va="center",
                    fontsize=26, fontweight="bold", color=ink)
            ax.text(j + 0.5, 1 - i + 0.30, f"{lab}  ·  {frac:.0%}", ha="center",
                    va="center", fontsize=10, color=ink)
    ax.set_xlim(0, 2); ax.set_ylim(0, 2); ax.set_aspect("equal")
    ax.set_xticks([0.5, 1.5])
    ax.set_xticklabels(["defective", "acceptable"], fontsize=10)
    ax.set_yticks([1.5, 0.5])
    ax.set_yticklabels(["says\nDEFECT", "says\nOK"], fontsize=10)
    ax.xaxis.set_ticks_position("top"); ax.xaxis.set_label_position("top")
    ax.set_xlabel("blind judge", fontsize=9.5, color="#555", labelpad=6)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=34)

    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    acc = (tp + tn) / n
    spec = tn / (tn + fp) if tn + fp else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else float("nan")
    ax.text(1.0, -0.20,
            f"precision {prec:.1%}    recall {rec:.1%}\n"
            f"accuracy {acc:.1%}    specificity {spec:.1%}    F1 {f1:.3f}",
            ha="center", va="top", fontsize=9.5, transform=ax.transData,
            linespacing=1.6)

fig.suptitle("Confusion matrices against the blind LLM judge",
             fontsize=14, fontweight="bold", x=0.13, ha="left", y=0.985)
fig.text(0.13, 0.952,
         f"Positive = defective (judge MAJOR / BROKEN / NO-OP).  n = {n_all} scored runs, "
         f"{sum(1 for r in scored if defective(r))} defective.  "
         "Blue = correct, red = error; darker = more runs.",
         fontsize=9.5, color="#555", ha="left")
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(OUT, f"fig_confusion.{ext}"), dpi=200,
                bbox_inches="tight", facecolor="white")
plt.close(fig)
for f in ("fig_confusion.png", "fig_confusion.pdf"):
    p = os.path.join(OUT, f)
    print(f"   {p}  ({os.path.getsize(p)//1024} KB)")


# ══ Figure 4: blind review outcomes by arm, with migration-success captions ═
# The figure embedded in results.tex (Figure~\ref{fig:results}): each arm's
# blind-judge verdict distribution, stacked, with the deterministic migration
# success rate given below its bar.
VERDICT_ORDER = ["CLEAN", "MINOR", "MAJOR", "BROKEN", "NO-OP"]
VERDICT_COLOR = {"CLEAN": "tab:blue", "MINOR": "tab:orange", "MAJOR": "tab:green",
                  "BROKEN": "tab:red", "NO-OP": "tab:purple"}
ARM_LABEL4 = {"single_shot": "Single-shot", "single_shot_repair": "Single-shot repair",
              "proteus_no_verif": "Proteus no verification", "proteus_full": "Proteus full"}

fig, ax = plt.subplots(figsize=(13, 8))
bottoms = np.zeros(len(ARMS))
for verdict in VERDICT_ORDER:
    vals = np.array([sum(1 for r in by[a] if r["judge_verdict"] == verdict) for a in ARMS])
    b = ax.bar(range(len(ARMS)), vals, bottom=bottoms, color=VERDICT_COLOR[verdict],
               edgecolor="white", linewidth=0.8, label=verdict, width=0.6)
    for i, v in enumerate(vals):
        if v > 0:
            ax.text(i, bottoms[i] + v / 2, str(int(v)), ha="center", va="center",
                    fontsize=12, fontweight="bold", color="black")
    bottoms += vals

success_pct = [100 * sum(1 for r in by[a] if r["migration_success"] == "Y") / len(by[a]) for a in ARMS]
ax.set_xticks(range(len(ARMS)))
ax.set_xticklabels([f"{ARM_LABEL4[a]}\n{success_pct[i]:.1f}%" for i, a in enumerate(ARMS)], fontsize=11)
ax.set_ylabel("Number of Outcomes", fontsize=11)
ax.set_ylim(0, 36)
ax.set_title("Blind Review Outcomes by Arm", fontsize=16, fontweight="bold")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=5, frameon=False, fontsize=10)
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "blind_review_by_arm.png"), dpi=200, facecolor="white", bbox_inches="tight")
plt.close(fig)
print(f"   {os.path.join(OUT, 'blind_review_by_arm.png')}  "
      f"({os.path.getsize(os.path.join(OUT, 'blind_review_by_arm.png'))//1024} KB)")
