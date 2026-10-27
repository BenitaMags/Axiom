#!/usr/bin/env python3
"""Emit the paper's result tables as LaTeX, computed from results_FINAL.csv.

Why a script and not a hand-typed table
───────────────────────────────────────
Every number in the paper should be reproducible from the CSV by running one
command. Transcribing 30 figures by hand the night before a deadline is how a
table ends up disagreeing with the data it summarises, and a reviewer who spots
one inconsistency reasonably doubts the rest.

    python3 gen_paper_tables.py --csv ~/Downloads/evaluation/reporting/results_FINAL.csv
    python3 gen_paper_tables.py --csv ... --out paper/tables.tex

Emits three tables:

  II   the scaffold's headline table — generation success, equivalence
       confirmation, divergence, repair cost, dollar cost
  III  the LLM-judge review, which the scaffold has no slot for yet
  IV   the finding that matters most: defect rate AMONG ACCEPTED clones

Table IV is the one to lead with. Tables II and III each tell half a story —
one arm accepts more, another is cleaner — and only their intersection answers
"when this system says a clone is good, how often is it right?"
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter

#: Display order and display names. proteus_no_verif is the scaffold's
#: "baseline 2" ablation (local execute-and-repair only, VCA and global oracle
#: disabled); proteus_full is the complete two-tier system.
ARMS = [
    ("single_shot",      r"Single-shot LLM (B1)"),
    ("proteus_no_verif", r"\emph{Proteus} w/o verification (B2)"),
    ("proteus_full",     r"\emph{Proteus} (full)"),
]

#: Verdicts that mean the reviewer found a defect serious enough that the clone
#: should not ship. MINOR is deliberately excluded: it denotes a real but
#: edge-case difference, and folding it in here would overstate the problem.
SERIOUS = ("BROKEN", "MAJOR")


def num(value):
    """Float, or None when the cell is blank or NA. Never 0.0 as a stand-in.

    ``NA`` means "not measurable for this row" — on this corpus that is a real
    and frequent state, e.g. oracle_equiv on categories A and B, which ship no
    golden vectors. Coercing it to zero would silently drag every mean down and
    make an absent measurement look like a bad one.
    """
    text = (value or "").strip()
    if not text or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def mean(values) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def fmt(value, spec: str = ".3f", dash: str = "--") -> str:
    return dash if value is None else format(value, spec)


def rows_for(rows: list, config: str) -> list:
    return [r for r in rows if r["config"] == config]


def table_ii(rows: list) -> str:
    """The scaffold's Table II."""
    out = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Clone-generation results on the industrial measurement and",
        r"control corpus. Twelve programs $\times$ three seeds $=$ 36 runs per",
        r"configuration. \emph{Equiv.\ conf.} is reported over categories C and D",
        r"only --- six subjects, 18 runs per configuration --- these being the",
        r"programs that ship golden vectors and invariants;",
        r"categories A and B have no equivalence probes and are excluded rather",
        r"than scored as passes.}",
        r"\label{tab:results}",
        r"\small",
        r"\begin{tabular}{lccccc}",
        r"\toprule",
        r"Configuration & Gen.\ success & Equiv.\ conf. & Divergence & "
        r"Repair iters & Cost (USD) \\",
        r"\midrule",
    ]
    for config, label in ARMS:
        rs = rows_for(rows, config)
        gen = sum(r["gen_success"] == "Y" for r in rs)
        cd = [r for r in rs if r["category"] in ("C", "D")]
        equiv = sum(r["oracle_equiv"] == "Y" for r in cd)
        diverg = mean(num(r["struct_composite"]) for r in rs)
        iters = sum(num(r["iters_total"]) or 0 for r in rs)
        reent = sum(num(r["vca_reentries"]) or 0 for r in rs)
        cost = sum(num(r["cost_usd"]) or 0 for r in rs)
        out.append(
            f"{label} & {gen}/{len(rs)} & {equiv}/{len(cd)} & "
            f"{fmt(diverg)} & {iters:.0f} ({reent:.0f} re-entry) & "
            f"\\${cost:.2f} \\\\"
        )
    out += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(out)


def table_iii(rows: list) -> str:
    """LLM-judge review of every clone."""
    out = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Blind LLM review of all 108 generated clones. An independent",
        r"model (\texttt{claude-opus-5}, distinct from the \texttt{claude-sonnet-5}",
        r"used for generation) was shown each original and its clone with all",
        r"system-identifying markers removed, and asked for concrete behavioural",
        r"defects. \textsc{no-op} denotes a clone byte-identical to its original:",
        r"trivially faithful because nothing was attempted, and therefore excluded",
        r"from the clean rate rather than counted as a success.}",
        r"\label{tab:judge}",
        r"\small",
        r"\begin{tabular}{lcccccc}",
        r"\toprule",
        r"Configuration & \textsc{broken} & \textsc{major} & \textsc{minor} & "
        r"\textsc{clean} & \textsc{no-op} & Clean rate \\",
        r"\midrule",
    ]
    for config, label in ARMS:
        rs = rows_for(rows, config)
        c = Counter(r["judge_verdict"] for r in rs)
        attempted = len(rs) - c["NO-OP"]
        rate = f"{c['CLEAN']}/{attempted}" if attempted else "--"
        out.append(
            f"{label} & {c['BROKEN']} & {c['MAJOR']} & {c['MINOR']} & "
            f"{c['CLEAN']} & {c['NO-OP']} & {rate} \\\\"
        )
    out += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(out)


def table_iv(rows: list) -> str:
    """Defect rate among accepted clones — the headline finding.

    This is the only table that crosses the system's own judgement with an
    independent one. Generation success says how often each arm produced
    something; this says how often what it produced and certified was actually
    sound. They point in opposite directions, which is the result.
    """
    out = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Precision of each configuration's own success signal. Of the",
        r"clones a configuration certified (compiles, passes the full suite, and",
        r"genuinely substituted the dependency), the fraction an independent",
        r"blind reviewer found to carry \textsc{broken} or \textsc{major}",
        r"behavioural defects. Accepting more clones and accepting sound clones",
        r"are different capabilities, and they trade off against one another.}",
        r"\label{tab:precision}",
        r"\small",
        r"\begin{tabular}{lccc}",
        r"\toprule",
        r"Configuration & Clones accepted & With serious defects & Defect rate \\",
        r"\midrule",
    ]
    for config, label in ARMS:
        accepted = [r for r in rows_for(rows, config)
                    if r["migration_success"] == "Y"]
        bad = sum(r["judge_verdict"] in SERIOUS for r in accepted)
        rate = f"{100 * bad / len(accepted):.0f}\\%" if accepted else "--"
        out.append(f"{label} & {len(accepted)}/36 & {bad} & \\textbf{{{rate}}} \\\\")
    out += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(out)


def prose(rows: list) -> str:
    """Plain-language readback, so the LaTeX can be sanity-checked at a glance."""
    lines = ["", "=" * 74, "SUMMARY (check these against the tables above)", "=" * 74]
    for config, label in ARMS:
        rs = rows_for(rows, config)
        gen = sum(r["gen_success"] == "Y" for r in rs)
        accepted = [r for r in rs if r["migration_success"] == "Y"]
        bad = sum(r["judge_verdict"] in SERIOUS for r in accepted)
        c = Counter(r["judge_verdict"] for r in rs)
        rate = f"{100 * bad / len(accepted):.0f}%" if accepted else "n/a"
        lines.append(
            f"  {config:<18} gen {gen}/36 | accepted {len(accepted)}/36 | "
            f"defective {bad} ({rate}) | no-op {c['NO-OP']}"
        )
    lines.append("")
    lines.append("  Headline: the arm that accepts the MOST clones ships the")
    lines.append("  HIGHEST defect rate. Verification trades recall for precision.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="", help="write LaTeX here; default stdout")
    args = ap.parse_args(argv)

    with open(args.csv, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    missing = [c for c in ("judge_verdict", "migration_success", "struct_composite")
               if c not in (rows[0] if rows else {})]
    if missing:
        print(f"CSV is missing {missing} — run backfill_from_clones.py and "
              f"judge_clones.py first")
        return 2

    latex = "\n\n".join([table_ii(rows), table_iii(rows), table_iv(rows)])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(latex + "\n")
        print(f"wrote {args.out}")
    else:
        print(latex)
    print(prose(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
