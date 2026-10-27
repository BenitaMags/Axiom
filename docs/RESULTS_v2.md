# Results note — closing the isomorphism gaps

Re-measurement after Tasks 1–3 of `HANDOFF_isomorphism_gaps.md`. Every number here
was produced by running the code, not carried over. Protocol is unchanged from the
original sweep: 6 length ratios × 4 modules × 2 catalogues, beam 3, 6 iterations,
all four oracle layers including the script-level `__main__` check.

Reproduce with:

```bash
python3 run_experiment.py --csv baseline_v2.csv
python3 run_experiment.py --ranking-metric composite --csv baseline_v2_composite.csv
python3 run_experiment.py --use-generated --csv generated_v2.csv
python3 run_experiment.py --use-generated --ranking-metric composite --csv generated_v2_composite.csv
```

---

## 1. Effective catalogue size: 13 → 16

No transformation sits at 0/4 any more. The three dead entries had three
different causes, and only one of them was what the handoff predicted.

| transformation | before | after | what was actually wrong |
|---|---|---|---|
| `rename_locals` | 0/4 | **4/4** | A **crash**, not a narrow pattern |
| `split_return_expression` | 0/4 | **4/4** (+1.0) | Implementation narrower than its own description |
| `inline_single_use_return` | 0/4 | **1/4** (−1.0) | Matched only the literal shape `return t` |

**`rename_locals`** was never a narrowing regression. `node.params.star_arg` is
`MaybeSentinel.DEFAULT` when a function has no `*args` — a truthy enum whose
`.name` is the *string* `"DEFAULT"` — so `star_arg.name.value` raised
`AttributeError` for every function in the corpus. `SPT.__call__` catches all
exceptions and returns the input unchanged, which is correct behaviour for an
inapplicable transformation and is exactly why this read as "never fires" rather
than as a bug. Fixed by testing `isinstance(..., cst.Param)`.

**`split_return_expression`** matched only `return <BinaryOperation>`. Every
return in the corpus is a `Call` (`return PumpState(...)`) or a bare `Tuple`
(`return GovernorState(...), command`), so the corpus *does* contain the pattern
the rule documents — the implementation just did not. Broadened to any
non-atomic return expression.

**`inline_single_use_return`** required the return to be *nothing but* the
temporary. Broadened to "a temporary assigned immediately before a return and
used exactly once by it", which is its true inverse. This is the change that
gives `pid_governor` a shrink path it never had.

A collateral bug found on the way: the `_r` rename prefix collided with
`normalize()`'s "any name starting with `_` is a temporary" heuristic, so the
normaliser folded genuine renamed locals and turned a rename-only variant into a
structurally changed one. That silently breaks the invariant the whole metric
design rests on. The normaliser now matches only real extraction prefixes
(`_spt_`, `_gen_`, `_t`). **Rename-only now scores exactly `0.0000` on both
rename-insensitive metrics on all four modules** — and for the first time this is
a *testable* claim, because before the fix the transformation never fired.

---

## 2. Sweep results, before and after

| | 12 before | **12 after** | 16 before | **16 after** |
|---|---|---|---|---|
| grow mean \|miss\| | 18.92 | **17.92** | 14.00 | **13.08** |
| shrink mean \|miss\| | 11.88 | **10.62** | 11.88 | **10.62** |
| grow reach (total) | +11 | **+45** | +31 | **+103** |
| shrink reach (total) | −6 | **−22** | −6 | **−22** |
| modules that can shrink | 2/4 | **3/4** | 2/4 | **3/4** |
| grow diversity (`ast_structural`) | 0.0442 | **0.0511** | 0.0940 | **0.0967** |
| on-target (grow) | 0% | 0% | 8% | **17%** |
| equivalence | 24/24 | **24/24** | 24/24 | **24/24** |

Across all four sweeps: **96/96 variants** and **7,115 candidate programs** passed
all four oracle layers.

Reach improved far more than accuracy did, and the two should be reported
separately. Requested vs achieved, mean lines:

```
              requested    achieved (12)   achieved (16)
ratio 0.85      -16.0         -2.75           -2.75
ratio 0.90      -10.8         -2.75           -2.75
ratio 1.00       +0.0         +0.00           +0.00
ratio 1.10      +10.8         +3.75           +8.25
ratio 1.20      +21.8         +3.75           +8.75
ratio 1.30      +32.5         +3.75           +8.75
```

The saturation finding still holds and is still the honest way to read the miss:
past ratio ~1.10 both arms go flat, so a large "miss" there measures catalogue
size, not controller accuracy.

---

## 3. Shrink: better, but still the weak direction

This is the gap that is **not** closed.

Shrink capability went from 2 transformations on 2 modules to **3 transformations
on 3 modules**, and total shrink reach from −6 to −22 lines. But mean achieved
shrink is −2.75 lines against requests of −10.8 and −16.0, and on-target for
shrink requests is still **0%**. `protective_relay` still has no shrink path at
all.

Crucially, **all of that improvement came from Task 1, not Task 2** — from
repairing `inline_single_use_return`, not from generating anything new.

### What the generation run actually produced

`generate_spts.py --direction shrink --n-designs 10 --n-implementations 3`
(claude / claude-sonnet-5) designed 10 transformations and accepted **1**, not the
3 the handoff asked for. Eight of ten designs failed with "never applicable" —
the designs were plausible, the implementations did not match the corpus shapes.

The one that passed, `adjacent_assignment_tuple_fusion` (reward 0.876, −9.5 mean
lines, 4/4 modules), was **excluded after inspection**. It is genuinely correct —
its no-forward-reference guard is sound, it preserves literals, and it passes all
four oracle layers — but it hits part of its line target by deleting comments:

```
module              Δtotal   Δblank   Δcomment-tokens   Δcode
pid_governor           -12       -3                -4      -7
pump_controller         -8       -1                +0      -7
protective_relay       -13       -1                +0     -12
sensor_pipeline         -5       -1                +0      -4
```

30 of the 38 removed lines are real statement fusion; the rest are comments and
blank lines that happened to sit between the fused statements. Deleting a comment
reduces the physical line count, passes every oracle layer (comments do not
execute), and is not a program transformation at all — it is the exact shrink-side
mirror of the padding the grow prompt already forbids under "NO DEAD CODE".

So the design-time validation gained a `preserves_comments` guard, alongside the
existing `preserves_literals` one. Both are oracle-*independent*: they close
classes of defect the corpus test suite structurally cannot observe. The prompt
now states the constraint explicitly and tells the implementer to concatenate the
fused statements' `leading_lines`.

The rejected implementation is preserved at
`smart_loader/isomorph/generated_spts.json.unguarded-shrink` for inspection. To
reinstate it deliberately:

```bash
cp smart_loader/isomorph/generated_spts.json.unguarded-shrink smart_loader/isomorph/generated_spts.json
```

### Why the guarded re-run did not finish

The re-run under the new guard reached design 1 of 12 and stopped: the Anthropic
account ran out of credits. Falling back to NVIDIA NIM
(`openai/gpt-oss-120b`, the strongest model reachable on that key —
`qwen3-coder-480b`, `kimi-k2`, `deepseek-v3.1` and `qwen3-235b` all return 404/410)
produced **0 accepted from 12 designs × 3 implementations = 36 attempts** — 17
crashed outright, 19 were never applicable. The OpenAI key is also out of credits.

The conclusion is about model capability, not about the mechanism: writing a
correct LibCST transformer is hard, and the gap between Sonnet (1 accepted in 30
attempts) and gpt-oss-120b (0 in 36) is large. Because nothing was accepted, the
catalogue is unchanged at four entries and the §2 and §4 sweeps stand. To resume
once credits exist:

```bash
python3 generate_spts.py --direction shrink --n-designs 12 --n-implementations 3
```

The catalogue merges and keeps the higher reward, so this adds to the existing
four rather than replacing them.

---

## 4. Ablation: `ast_structural` vs `composite`

`composite = 0.5 · ast_structural + 0.5 · cfg_shape`, registered as a fifth metric
and reported on every variant. `ncd` is excluded (r = +0.967 with `ast_structural`
— it would mostly double-count one signal); `du_chain` is excluded because it is
rename-sensitive and would let `rename_locals` inflate the ranking.

**The parameterisation is a proven no-op.** Running the same 24-cell sweep with
`metrics.headline` monkeypatched back to its pre-change form, against the
parameterised code at its default, gave **0 differing cells out of 24** — identical
transformation chains, line counts, candidate counts and variant SHA-256s. So
`--ranking-metric ast_structural` reproduces the prior behaviour exactly, and the
changes in §2 are attributable to Tasks 1–2 alone.

| catalogue | ranking signal | mean \|miss\| | on-target | oracle pass | mean cfg_shape of winners | winners with cfg > 0 |
|---|---|---|---|---|---|---|
| 12 | `ast_structural` | 12.50 | 17% | 24/24 | 0.0352 | 7/24 |
| 12 | `composite` | 12.50 | 17% | 24/24 | 0.0352 | 7/24 |
| 16 | `ast_structural` | 10.08 | 25% | 24/24 | 0.0352 | 7/24 |
| 16 | `composite` | 10.08 | 25% | 24/24 | 0.0352 | 7/24 |

Diversity values are deliberately absent from this table. `ast_structural` and
`composite` are different scales measuring different things, so ordering their
decimals says nothing — the comparable columns are the length miss, the on-target
rate and the oracle pass rate.

**This is a negative result, and it is reported as one.** `composite` changed the
selected winner in 3 of 24 cells in each catalogue, but:

* it changed **no** achieved line count, in any cell, in either catalogue;
* it moved **no** cell from `cfg_shape = 0.0` to `cfg_shape > 0`;
* mean `cfg_shape` of winners is identical to four decimal places.

The Task 3 acceptance criterion — "at least one module selects a variant with
non-zero `cfg_shape` where it previously selected one with 0.0" — is therefore
**not met**. The three cells that changed (`pump_controller` at ratios 1.10, 1.20,
1.30) already had `cfg_shape = 0.1429` under both rankings; composite reordered
equally-scoring chains without reaching a differently-shaped variant.

The likely reason is that the tie-break is the *second* sort key. Candidates are
ordered by line-count error first, so a differently-shaped variant can only win
when it is exactly as close to the target as the incumbent. Changing which
diversity number breaks that tie cannot surface a variant the length ordering has
already ranked below. If control-flow diversity is genuinely the objective, the
change that would matter is at the *catalogue* level — more transformations that
restructure branching — not at the ranking level.

**Recommendation: keep `ast_structural` as the default.** `composite` stays
registered, reported, and selectable, and the ablation stands as evidence the
choice was tested rather than assumed.

---

## 5. Honest status

**Closed:**

* effective catalogue is 16/16; nothing is dead
* rename-only scores exactly 0.0 on both rename-insensitive metrics — now
  verifiable, because the transformation actually runs
* grow reach roughly tripled on both catalogues (+11 → +45, +31 → +103)
* shrink reach improved from −6 to −22 lines, 2 → 3 modules
* every oracle-passing candidate can be persisted and re-verified standalone
  (`--artifact-dir`)
* the agent runs inside the LangGraph, byte-identical no-op when disabled
* interactive line-target prompt exists, honours `120` / `+20` / `-15`, and is
  bypassed entirely by `--isomorph-target-lines` or a non-tty

**Still open:**

* shrink remains the weak direction: 0% on-target, `protective_relay` has no
  shrink path, and the shrink gain came from a bug fix rather than from generation
* only 1 of 3 requested shrink transformations was generated, and it was excluded
  for deleting comments; the guarded re-run is blocked on API credits
* reach still saturates past ratio ~1.10
* `composite` did not improve the comparable columns — a negative ablation

**Unchanged limitations, which the write-up must keep stating:**

* The oracle is **not a proof**. Program equivalence is undecidable. The
  defensible claim is "equivalent with respect to the golden vectors, 20
  branch-covering probes, randomised invariant checks, and script-level I/O".
  Tightening `pid_governor`'s anti-windup clamp from ±2500 to ±2400 still passes
  all four layers; `preserves_literals` catches that class independently.
* A 100% oracle pass rate is uninformative alone. What carries the claim is fault
  injection — the oracle catches an injected `RaiseRuntimeError` in `__main__`,
  catches a single changed output value, and rejected `if_else_assign_to_ternary`
  on all four modules when it was destroying `elif` chains. `preserves_comments`
  now closes a third class the suite cannot see.
* Reach and accuracy are different findings. Report accuracy inside the reach,
  and reach separately.
