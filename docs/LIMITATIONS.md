# Threats to Validity and Limitations

Four configurations, 12 programs, 3 seeds each (144 runs), all on
`claude-sonnet-5` at temperature 0.7. Results in `results_FOUR_ARM_FINAL.csv`.

| arm | migration_success | judged | CLEAN | CLEAN% | cost |
|---|---|---|---|---|---|
| `single_shot` | 32/36 | 32 | 2 | 6% | $0.79 |
| `single_shot_repair` | 34/36 | 34 | 7 | 21% | $0.96 |
| `proteus_no_verif` | 24/36 | 27 | 4 | 15% | $4.79 |
| `proteus_full` | 23/36 | 25 | 6 | 24% | $5.94 |

## 1. Passing the test suite does not mean the migration is correct

This is the most important limitation and it applies to every arm.

Of the migrations the harness accepted, **55–59% contained defects a blind
reviewer rated MAJOR or BROKEN** — and every one of those rows compiled and
passed 100% of its tests. The best arm is **24% behaviourally faithful**.

The cause is structural. Every corpus program dependency-injects its external
client so the suite can run without a live server:

```python
def _default_client(host, port=502):
    from pymodbus.client import ModbusTcpClient   # never executed by tests
    return ModbusTcpClient(host, port=port)
```

Migration rewrites exactly this code path, and the tests never execute it. A
worked example: `proteus_full`'s B2 adapter accepts a `unit` (Modbus slave id)
argument and silently discards it, so every read targets the wrong device on a
multi-drop bus. It passed compilation, the test suite, and patch integrity. The
blind reviewer caught it in one pass.

**Consequence:** `gen_success` and `migration_success` measure whether a
migration *happened*, not whether it is *correct*. Any claim of production
readiness would be unsupported by this data.

## 2. The equivalence oracle covers only half the corpus

The four-layer oracle (golden vectors, swept invariants, test suite, import
integrity) has recorded probes only for categories C and D. Categories A and B
— 6 of 12 programs — are gated on compilation and tests alone, which
Limitation 1 shows is weak. B2's dropped `unit` id sits in exactly this gap.

The oracle is also weaker than a proof even where it applies: a rewrite that
breaks a path neither the probes nor the tests visit passes. Verified
empirically — mutating an unused default argument (`roc_limit=5.0`) passed all
four layers, because every probe and test constructs the object with explicit
arguments.

## 3. Measurement defects materially distorted an earlier version of this result

Four infrastructure bugs were found and fixed. None were in the model; all
suppressed or misreported successful migrations:

1. **Uninstalled free-choice targets.** The harness pre-installed only the
   corpus's declared targets, but arms select their own. Migrations to
   uninstalled libraries were scored as model failures — 6 of 8 recorded
   failures in one arm were this artifact. Correcting it moved first-attempt
   success from 78% to 92%.
2. **Distribution name vs. import root.** `paho-mqtt` was looked up as
   `paho_mqtt`; the root is `paho`. The same wrong key appeared in four places
   (import rewrite, dead-import sweep, patch-integrity check, profiler), so
   each silently matched nothing.
3. **`oracle_passed` dropped when merging per-module reports.** Every C/D row
   recorded `oracle_equiv=N` beside a detail reading *"all four layers
   passed"*, forcing `gen_success=N` on 16 of 18 rows.
4. **NO-OP under-detection.** Defined as `loc_changed == 0`, it missed clones
   that changed only comments while never removing the dependency.

Correcting these moved `proteus_full` from 12/36 to 23/36 `migration_success`
with no change to any prompt. **A reader should treat pre-correction numbers
from this project as unreliable.** The re-measurement was applied identically
to all four arms.

## 4. Proteus cannot perform first-party reimplementation

C1 and C2 require removing a dependency and writing the behaviour as
first-party code. The pipeline selects among existing libraries and has no
path to this, so both agent arms score 0/6 there — roughly half their
remaining gap to the baselines. This is a stated capability boundary, not a
tuning failure. The single-shot arms handle it because their prompt states the
condition explicitly.

## 5. Verification helps quality, not quantity — and the effect is small

`proteus_full` vs `proteus_no_verif`: `migration_success` 23 vs 24
(indistinguishable), but CLEAN 24% vs 15% and BROKEN 3 vs 4. The verifier's
contribution is invisible in the count and visible only under review, on n=25
and n=27. Suggestive, not significant.

## 6. Statistical power

3 seeds per (program, configuration). Differences of 1–2 rows out of 36 are
not distinguishable from run-to-run variance at temperature 0.7. Only the
large effects — the ~10-row gap between single-shot and agent arms, the 12→23
correction — are safely outside noise. No confidence intervals are reported;
the design does not support them.

## 7. Judge reliability

A single blind reviewer (`claude-opus-5`, temperature 0), one pass per clone,
no inter-rater agreement measured. It is shown the original and the rewrite
with arm identity masked, and is instructed to report only concrete runtime
defects. Its findings were spot-checked and were specific and correct,
including defects no other gate detected. But severity grading is a single
model's judgement, and **4 of 144 rows returned unparseable or empty replies** and are
excluded — denominators are 25–34, not 36.

**The exclusions are not random, and one is known to be adverse.** Of the four,
three returned zero bytes after four retries; the fourth
(`single_shot_repair` / C3 run 3) returned valid JSON that was truncated
mid-object. The legible portion contains two BROKEN findings — an import of a
non-existent `smtplib_compat` module that would raise `ModuleNotFoundError` on
any default-transport construction. It is excluded because a truncated reply
cannot be scored without inventing the missing part, but its exclusion
**flatters `single_shot_repair`**, which is otherwise the only arm with zero
BROKEN clones. Read that arm's 0 BROKEN as "0 of 34 scored, with at least one
of the 2 unscored known to be BROKEN".

Judge verdicts are reported only over clones that actually migrated
(`judge_noop = N`). A clone that did not migrate is trivially faithful to the
original, so CLEAN on it carries no information; counting those would reward
not doing the work.

## 8. Corpus scope and generality

12 synthetic industrial-control programs, 46 tests total, one language, one
model, one provider. Programs are small (25–90 lines/module) and purpose-built
for this evaluation. Nothing here establishes behaviour on large real-world
codebases, other model families, or migrations outside this shape.

## 9. Cost figures are not a fair systems comparison

Per-arm costs reflect a research pipeline with retries, isolated profiling and
security lookups, not an optimised deployment. The ~6× gap between single-shot
and agent arms is real in this configuration but should not be read as an
inherent property of agent architectures.
