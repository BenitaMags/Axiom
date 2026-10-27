# Handoff — closing the gaps in AXIOM's program-isomorphism agent

Paste this into a fresh Claude Code session in the `axiom_v2_fixed 3` repo.

---

## Context you need before starting

AXIOM has a new feature: a **Logic Substitution Agent** that takes a program and produces
a *program-isomorphic variant* — structurally different, behaviourally identical, at a
user-requested line count. It follows **Auto-SPT** (Hooda et al., arXiv:2512.06042, 2025),
with Auto-SPT's clone-detector objective replaced by a size objective.

Relevant files:

```
smart_loader/isomorph/spt.py       12 hand-written LibCST transformations + registry + normalize()
smart_loader/isomorph/design.py    LLM designs/implements new transformations (Auto-SPT steps 1-2)
smart_loader/isomorph/oracle.py    4-layer equivalence gate A(x, x')
smart_loader/isomorph/search.py    length-targeted beam search (Auto-SPT Algorithm 1)
smart_loader/isomorph/metrics.py   4 diversity metrics + registry
smart_loader/isomorph/telemetry.py per-run JSONL logging
smart_loader/agents/logic_substitution_agent.py   LangGraph node (opt-in, not yet wired in)
generate_spts.py                   CLI to generate new transformations
run_experiment.py                  CLI sweep that produces the paper figures
ics_corpus/                        4 ICS modules + oracle + golden.json
baseline.csv / generated.csv       results of the 12- and 16-transformation sweeps
```

Read `ISOMORPHISM_EXPLAINED.md` in the repo root first — it explains the whole design.

**Ground rules.** Diagnose with real evidence (run the code, read the logs), never guess.
Be explicit about what you verified versus what you didn't. Every transformation must pass
all four oracle layers. Never loosen the oracle to make a number look better.

---

## Measured current state — do not re-derive, this is verified

Sweep: 6 length ratios (0.85, 0.90, 1.00, 1.10, 1.20, 1.30) x 4 modules x 2 catalogues
= 48 variants, all oracle-verified.

| | 12 transformations | 16 transformations |
|---|---|---|
| grow mean \|miss\| | 18.92 | 14.00 |
| grow diversity (`ast_structural`) | 0.0442 | 0.0940 |
| grow reach | +11 lines total | +31 lines total |
| shrink mean \|miss\| | 11.88 | **11.88 (identical)** |
| shrink reach | -6 lines | **-6 lines (identical)** |
| on-target (grow) | 0% | 8% |
| equivalence | 24/24 | 24/24 |

Transformation applicability across the 4 modules (measured directly):

```
call_argument_extraction     grow     4/4   +5.0   [LLM]
invert_if_else               neutral  4/4    0.0
demorgan_branch_swap         neutral  4/4    0.0   [LLM]
independent_assignment_swap  neutral  3/4    0.0   [LLM]
extract_subexpression        grow     2/4   +3.5
expand_aug_assign            grow     2/4    0.0
and_chain_nesting            grow     1/4   +1.0   [LLM]
collapse_aug_assign          shrink   1/4    0.0
expand_chained_compare       grow     1/4    0.0
if_else_assign_to_ternary    shrink   1/4   -5.0
merge_nested_if              shrink   1/4   -1.0
split_boolean_if             grow     1/4   +1.0
ternary_to_if_else           grow     1/4   +1.0
split_return_expression      grow     0/4    0.0   <-- NEVER FIRES
inline_single_use_return     shrink   0/4    0.0   <-- NEVER FIRES
rename_locals                neutral  0/4    0.0   <-- NEVER FIRES
```

Metric correlations across all 48 variants:

```
ncd        vs ast_structural   r = +0.967   (nearly redundant)
du_chain   vs ast_structural   r = +0.403
cfg_shape  vs ast_structural   r = -0.189   (ANTI-correlated)
```

---

## Task 1 — Revive the three dead transformations

**Problem.** `split_return_expression`, `inline_single_use_return` and `rename_locals`
never fire on any of the 4 modules. Effective catalogue is 13, not 16.

`rename_locals` is a known regression: it was narrowed from renaming parameters to
renaming body-locals-only (to stop it breaking a `_lcg(seed=...)` keyword call), and was
narrowed past the point of matching anything.

**Do this.**

1. For each of the three, write a minimal snippet that *should* match, and confirm whether
   the transformation fires on it. That separates "pattern too narrow" from "matcher bug".
2. `rename_locals`: it must rename variables genuinely local to a function body while never
   touching parameter names, globals, attributes, or keyword-argument names. Verify against
   `pid_governor`, which has the `_lcg(seed=...)` call that broke it before.
3. `split_return_expression` / `inline_single_use_return`: check whether the corpus actually
   contains the pattern. If it genuinely doesn't, **delete them** rather than leaving dead
   entries inflating the catalogue count — and say so.

**Acceptance.** Re-run the applicability probe (the loop in Task 7). No transformation sits
at 0/4. Report the new effective catalogue size.

---

## Task 2 — Generate shrink transformations (highest value)

**Problem.** Only 2 of 16 transformations can remove a line, worth -5 and -1, on one module
each. `pid_governor` and `protective_relay` have **zero** shrink capability. Both catalogues
achieve exactly -1.5 mean lines for every shrink request. All 4 LLM designs came out grow or
neutral because nothing asked for shrink.

**Do this.**

1. Read `smart_loader/isomorph/design.py` and find where the design prompt is built.
2. Add a **direction bias** — either a `direction` parameter threaded through
   `generate_catalogue()`, or a prompt variant that explicitly demands line-reducing
   transformations. Ideas worth prompting for: merging adjacent compatible statements,
   collapsing `if x: return True / return False` into `return x`, folding single-use
   temporaries, combining sequential augmented assignments, collapsing redundant `else`
   after `return`.
3. Expose it as `generate_spts.py --direction shrink`.
4. Run: `python3 generate_spts.py --direction shrink --n-designs 10 --n-implementations 3`
   (needs the Anthropic API key; defaults are `claude` / `claude-sonnet-5`).
5. The catalogue **merges, keeping the higher reward** — do not overwrite. This has
   regressed before and lost a good transformation.

**Acceptance.** At least 3 new shrink transformations survive validation. Shrink reach
improves measurably beyond -6 lines. `pid_governor` and `protective_relay` gain a non-zero
shrink path.

---

## Task 3 — Add a second ranking signal, switchable, as an ablation

**Scope note — nothing is being removed.** All four metrics are already measured and
reported on every variant by `measure_all()`, and they stay that way. This task only
concerns `headline()` — the single number the beam search sorts candidates by. The search
needs one scalar to rank with; it cannot sort by four at once. This task **adds** a second
option for that scalar and makes the choice switchable, with `ast_structural` remaining
the default until evidence says otherwise.

**Do not compare diversity values across ranking modes as if one were "better".** They are
different scales measuring different things — `ast_structural = 0.0940` and a composite of
`0.065` are not a decline, any more than 20 C is colder than 68 F. The metrics that ARE
comparable across modes are the length miss, the on-target rate, and the oracle pass rate.
Compare those.

**Problem.** `ast_structural` as the sole ranking signal is biased toward statement
padding over control-flow restructuring. Evidence:

```
BEST by ast_structural:   protective_relay  ast 0.1261  cfg_shape 0.0000
                          (call_argument_extraction x2 | demorgan_branch_swap)
                          -> branching structure COMPLETELY unchanged

BEST by cfg_shape:        pump_controller   cfg 0.1429  ast 0.0290
                          (ternary_to_if_else | split_boolean_if | invert_if_else | ...)
                          -> genuinely restructured, but ranked near LAST
```

For a moving-target-defence argument, changed control flow is the more valuable diversity,
and the current ranking discards it.

**Do this.**

1. In `smart_loader/isomorph/metrics.py`, add a `composite` metric combining the two
   **rename-insensitive** metrics. Suggested starting point, but justify whatever you pick:
   `0.5 * ast_structural + 0.5 * cfg_shape`. Do **not** include `ncd` (r = +0.967 with
   `ast_structural`, so it adds almost no information) or `du_chain` (rename-sensitive —
   including it would let `rename_locals` inflate the ranking).
2. Register it in `METRIC_REGISTRY` like any other metric, so it is also *reported*
   alongside the existing four. Nothing is deleted; the report grows from 4 numbers to 5.
3. **Make the ranking signal switchable, defaulting to the current behaviour.** Add a
   `ranking_metric` parameter to `LengthTargetedSearch` (default `"ast_structural"`), thread
   it through `logic_substitution_agent` via state, and expose `--ranking-metric` on
   `run_experiment.py` and `run_isomorph_agent.py`. `headline()` takes the name as an
   argument rather than hard-coding one.

   This is the revert path: `--ranking-metric ast_structural` reproduces today's numbers
   exactly. Verify that it does, byte-for-byte, before moving on — if the default path
   changes any of the 48 existing results, the parameterisation has a bug.
4. Confirm the invariant still holds under **both** modes: **a rename-only variant must
   score exactly 0.0.** Test it explicitly. This is the property that stops the cheapest
   possible transformation from inflating the number, and it must survive the change.
5. Run the sweep under both modes and report them side by side.

**Acceptance.** `--ranking-metric ast_structural` reproduces the existing 48 results exactly.
Rename-only scores 0.0 in both modes. Under `composite`, at least one module selects a
variant with non-zero `cfg_shape` where it previously selected one with 0.0.

**Report it as an ablation, and keep both.** The output of this task is a table:

```
ranking signal    mean |miss|   on-target   oracle pass   mean cfg_shape of winners
ast_structural        ?             ?            ?                  ?
composite             ?             ?            ?                  ?
```

Do **not** pick the mode by which produces a larger diversity decimal — those decimals are
on different scales and the comparison is meaningless. Pick on the comparable columns
(length miss, on-target rate, oracle pass rate), and on the argument for which kind of
diversity matters for a moving-target defence. If `composite` is worse on the comparable
columns, keep `ast_structural` as the default and report the ablation anyway — a negative
ablation result is a legitimate finding and strengthens the paper by showing the choice
was tested rather than assumed.

Never select a ranking signal after seeing which gives nicer numbers without disclosing
that both were run. Reporting both is what makes the choice defensible.

---

## Task 4 — Persist candidate variants

**Problem.** The sweeps generated 690 and 1,496 candidate programs. All were oracle-verified,
and all were discarded — they exist only in memory inside `search.py`. Only the winner
survives. For a paper claiming "1,496 candidates verified equivalent," the artefacts should
be inspectable.

**Do this.**

1. Add an optional `artifact_dir` to `LengthTargetedSearch`. When set, write every candidate
   that **passes** the oracle to
   `<artifact_dir>/<module>/<ratio>/<NNN>_<transformation_chain>.py`.
2. Write a per-variant `manifest.json`: transformation chain, line count, all four metric
   values, oracle verdict and detail, timing.
3. Also save the **winner** separately as `winner.py` so it is trivially findable.
4. Add `--artifact-dir` to `run_experiment.py` and `run_isomorph_agent.py`.
5. Default to **off** so normal runs stay fast — this writes thousands of files.

**Acceptance.** `python3 run_experiment.py --use-generated --artifact-dir variants/` produces
a browsable tree. Spot-check that a saved variant, run through the oracle standalone, still
passes.

---

## Task 5 — Wire the agent into the pipeline

**Problem.** `logic_substitution_agent` exists and works standalone but is **not** in the
LangGraph. It never runs during a normal AXIOM invocation.

**Do this.**

1. In `smart_loader/core/graph.py`, add the node on the edge **after `connector_agent` and
   before `verifier_agent`**.
2. It must be a no-op pass-through when `enable_isomorph` is False (the default) — confirm
   an ordinary PyMigBench run is completely unaffected.
3. Add the CLI flags that populate state: `--enable-isomorph`, `--isomorph-target-lines`,
   `--isomorph-target-ratio`, `--isomorph-corpus-dir`, `--isomorph-use-generated`.
4. Confirm `LoaderState` carries every key the agent reads and writes.

**Acceptance.** A full pipeline run without the flag behaves exactly as before. A run with
`--enable-isomorph` produces `isomorph_variant_code` and `isomorph_report` in the final state.

---

## Task 6 — Interactive human-in-the-loop length prompt

**Problem.** Only the batch path exists (`isomorph_target_ratio` from the CLI). The professor
specifically asked for a second HITL gate **after the Connector** where the user is asked how
many lines they want the variant to be. Note the *existing* HITL gate is
`run_feedback_loop()` after the verifier — this is a new, separate one.

**Do this.**

1. In `logic_substitution_agent`, before the search, prompt interactively for a target line
   count when running in interactive mode. Show the current line count so the user has an
   anchor.
2. Accept an absolute count (`120`) or a relative change (`+20`, `-15`).
3. `isomorph_target_lines` from the CLI, or non-interactive mode, must bypass the prompt
   entirely — batch runs cannot block waiting for a human.
4. After the search, report **requested vs achieved** clearly, including the signed miss.
   The professor explicitly asked for this.

**Acceptance.** Interactive run prompts and honours the input. Batch run never prompts.

---

## Task 7 — Re-measure everything and write up

Both catalogues must be re-measured after Tasks 1-3, because the numbers above predate them
and are no longer a fair comparison.

```bash
python3 run_experiment.py --csv baseline_v2.csv
python3 run_experiment.py --use-generated --csv generated_v2.csv
```

Applicability probe:

```python
import sys; sys.path.insert(0, '.')
from smart_loader.isomorph.oracle import CorpusOracle
from smart_loader.isomorph.design import register_generated
from smart_loader.isomorph.spt import spts
register_generated()
o = CorpusOracle('ics_corpus')
for s in spts():
    hits, d = [], []
    for m in ["pid_governor", "pump_controller", "protective_relay", "sensor_pipeline"]:
        src = o.module_source(m)
        try: out = s(src)
        except Exception: continue
        if out != src:
            hits.append(m); d.append(len(out.splitlines()) - len(src.splitlines()))
    print(f"{s.name:32s} {s.direction:8s} {len(hits)}/4 {sum(d)/len(d) if d else 0:+.1f}")
```

**Produce a short results note** covering: before/after for both catalogues; whether shrink
now works; whether the composite metric changed which variants get selected; the new
effective catalogue size.

---

## Framing constraints for anything written up

These are honesty requirements, not stylistic preferences.

- **The oracle is not a proof.** Program equivalence is undecidable. The defensible claim is
  *"equivalent with respect to the golden vectors, 20 branch-covering probes, randomised
  invariant checks, and script-level I/O"* — never "proven equivalent." A known counterexample
  exists: tightening `pid_governor`'s anti-windup clamp from +/-2500 to +/-2400 passes all
  four layers. A separate literal-preservation guard catches that class.
- **A 100% oracle pass rate is uninformative on its own.** What carries the claim is fault
  injection: the oracle catches an injected `RaiseRuntimeError` in `__main__`, catches a
  single changed output value, and rejected `if_else_assign_to_ternary` on all 4 modules when
  it was destroying elif chains. Cite that, not the pass rate.
- **Reach and accuracy are different findings.** Past ratio ~1.10 the system saturates, so a
  large "miss" there measures catalogue size, not controller accuracy. Report accuracy inside
  the reach, and reach separately.
- **Never loosen the oracle to improve a number.** When the `__main__` gap was found, the fix
  was to extend the oracle with a 4th layer, not to forbid transforming `__main__`. Keep that
  direction.
- **Never reorder float arithmetic.** `(a+b)+c != a+(b+c)` in IEEE 754, and the golden check
  is bit-exact (`TOL = 0.0`).

---

## Suggested order

1. Task 1 (quick, unblocks an honest catalogue count)
2. Task 2 (highest value — the biggest measured gap)
3. Task 3 (changes what "best" means, so before re-measuring)
4. Task 7 (re-measure)
5. Tasks 4, 5, 6 (infrastructure; independent of the above)
