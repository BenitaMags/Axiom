# Proteus evaluation — results and clones

108 runs: 12 programs × 3 seeds × 3 configurations, on the industrial
measurement and control corpus.

## Contents

| path | what it is |
|---|---|
| `results_FINAL.csv` | one row per run, 51 columns |
| `clones/<config>/<program>_run<n>/` | the delivered clone for that row |
| `judge_verdicts.jsonl` | full LLM-judge findings (file, line, severity, problem) |

Every CSV row has exactly one clone directory and every clone directory has
exactly one row — verified, no orphans on either side.

## Configurations

- **`single_shot`** — one LLM prompt, no agents, no verification. The prompt
  names the library to be *removed* and never the replacement; the model
  chooses its own target. Any hint of the corpus's declared target is stripped
  from the copy the model sees.
- **`proteus_no_verif`** — the full agent pipeline with the VCA and the
  equivalence oracle disabled (local execute-and-repair only).
- **`proteus_full`** — the complete two-tier system.

All three arms choose their own replacement library. Neither Proteus arm is
told the corpus's declared target.

## Reading the CSV

The columns that answer "did it work", in increasing strictness:

- `compiles` — parses and imports
- `gen_success` — compiles **and** passes the full test suite (plus, on C/D,
  the four-layer oracle)
- `migrated` — the source actually changed (`loc_changed > 0`)
- `lib_swapped_correctly` — the old dependency is genuinely gone
- `migration_success` — all three of the above. **This is the acceptance
  signal.**

`oracle_equiv` is the four-layer source-level oracle (imports → golden vectors
→ invariants → pytest). It is `NA` on categories A and B, which ship no
equivalence probes; scoring those as passes would dress a test run up as an
equivalence proof.

`judge_verdict` is an independent blind review of every clone by a *different*
model (`claude-opus-5`; generation used `claude-sonnet-5`). System-identifying
markers were removed from both programs before review, and the batch aborts
rather than proceeding if any marker survives. `NO-OP` means the clone is
byte-identical to the original: trivially faithful because nothing was
attempted, and therefore not counted as a success.

## Headline numbers

| configuration | accepted | with serious defects | defect rate | sound clones /36 |
|---|---|---|---|---|
| `single_shot` | 28/36 | 15 | 54% | 12 |
| `proteus_no_verif` | 11/36 | 6 | 55% | 5 |
| `proteus_full` | 15/36 | 5 | 33% | 10 |

**These differences are not statistically significant.** Fisher's exact test:
`single_shot` vs `proteus_full` p = 0.34; `single_shot` vs `proteus_no_verif`
p = 1.00. 95% CIs overlap substantially (54% [36–70], 55% [28–79], 33%
[15–58]). Separating 33% from 54% at p < 0.05 needs roughly 3–4× this corpus.

Two observations that do not depend on significance:

1. **The no-verification ablation performs the same as the single-shot
   baseline (55% vs 54%).** The agent architecture on its own buys nothing on
   this corpus; whatever gain exists is attributable to the verification tier.
2. **Proteus does not produce more sound clones than one prompt here** — 10
   against 12 per 36 attempts. Its acceptance signal is more precise (67% of
   accepted clones are sound, vs 46%), but it accepts far fewer.

## Known limitations in this data

- **`single_shot/D3_telemetry_gateway_run1` has no judge verdict.** The judge
  model returned an empty completion on four attempts with exponential
  backoff, deterministically, on an ordinary 9.4 KB prompt. Unexplained. The
  judge denominator for that arm is 35, not 36.
- **`single_shot/D3_telemetry_gateway_run3` is `oracle_equiv = NA`.** Its clone
  imports a module the run created that clone-saving did not persist, so the
  oracle cannot execute it. The reason is recorded in
  `oracle_divergence_loc`. `gen_success` for that row is the verdict measured
  during the run, against the complete working directory — it is deliberately
  not overwritten, because "we could not re-check this" and "this failed" are
  different facts.
- **Proteus cannot reimplement a dependency as first-party code.** On C1
  (`simple-pid` → in-house controller) and C2 (`transitions` → hand-rolled
  FSM) the resolver selects among libraries or keeps the incumbent; it never
  synthesises the replacement. `single_shot` does. This is a capability gap,
  not a configuration issue.
- **B2 and D1 declare `pymodbus` → `minimalmodbus`.** Both originals speak
  Modbus TCP (`ModbusTcpClient(host, port=502)`); `minimalmodbus` is
  serial-only and exposes no TCP entry point. There is no correct migration to
  the declared target. Runs that decline these are behaving correctly.
- **`iso_*` columns are blank for `single_shot`**, which has no mutation agent,
  and for runs where no variant was applied.

## Reproducing the derived columns

Nothing below calls an API except the judge, which is cached by clone content.

```
python3 backfill_from_clones.py --csv results_FINAL.csv --corpus <corpus> --clones clones
python3 judge_clones.py        --csv results_FINAL.csv --corpus <corpus> --clones clones
python3 gen_paper_tables.py    --csv results_FINAL.csv --out tables.tex
```
