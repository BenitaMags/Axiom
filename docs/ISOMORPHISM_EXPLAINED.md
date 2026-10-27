# Program isomorphism in AXIOM — explained from scratch

*Everything we have built, what it does, and how the numbers in the graphs are produced.*

---

## Part 1 — The idea, for a five-year-old

Imagine you write a story. Now imagine writing **the same story again, using different
words and different sentence order, but every single event still happens in exactly the
same order and the ending is identical.**

Two different-looking stories. One meaning.

That is what we are doing with computer programs. We take a program, and we produce a
second program that *looks* different — different variable names, different arrangement
of `if` statements, different lines — but that **does exactly the same thing, produces
exactly the same numbers, in exactly the same order.**

Two different-looking programs. One behaviour.

### Why would anyone want that?

Because of how attacks work. If a factory runs a pump-control program, and every factory
in the country runs the *identical* program, then an attacker who finds one weakness can
break into **all of them** with one attack. It is a monoculture — like a field where
every plant is genetically identical, so a single disease wipes out the whole crop.

But if every factory runs a *different-looking* version of the same program, an attack
tuned to one version doesn't reliably work on the others. The attacker has to do the work
again, every time. This is called **software diversity**, or a *moving-target defence*.

So the goal is:

> Same behaviour (so the factory still works correctly).
> Different structure (so one attack doesn't fit them all).

### Where this sits inside AXIOM

AXIOM already had a job: take an old, insecure Python program and migrate it to a safer,
maintained library. That removes **vulnerabilities we already know about**.

Your professor's new feature adds a second job right after that: take the freshly
migrated program and produce an isomorphic variant of it. That hardens against
**vulnerabilities nobody has found yet**.

```
parser -> security -> resolver -> profiler -> axiom -> connector -> [NEW: logic substitution] -> verifier
                                                          |                    |
                                              fixes known holes      hardens against unknown ones
```

---

## Part 2 — The four ICS files, explained

ICS means **Industrial Control System** — the software that runs physical equipment:
pumps, engines, circuit breakers, sensors. Your professor gave you four small Python
programs that imitate real industrial control logic. They are the **test subjects**. We
transform *these*.

They were chosen carefully. Each one contains a *different shape of control flow*, so
between them they exercise every kind of transformation we might invent.

### 1. `pid_governor.py` — 89 lines — an engine speed controller

A **PID controller** is the most common control algorithm in engineering. It keeps
something at a target value by continuously measuring the error and correcting.

Here it regulates an engine's **RPM** (crankshaft speed) toward a target by adjusting a
fuel/throttle command between 0 and 100%. Three terms combine:

- **P** (proportional) — react to the error *right now*
- **I** (integral) — react to error *accumulated over time*
- **D** (derivative) — react to how fast the error is *changing*

The interesting structure for us: an **anti-windup clamp**. If the engine can't keep up,
the integral term would grow forever ("winding up") and the controller would go crazy.
So the code clamps it to ±2500 using nested `min`/`max` branching. That nested branching
is exactly the kind of thing a transformation can restructure.

It also carries **state across iterations** (each step depends on the last), and uses
**floating-point arithmetic** — which matters, because reordering float maths is *not*
bit-exact, so certain transformations are forbidden here.

### 2. `pump_controller.py` — 96 lines — a water tank pump

A **state machine** with three modes: `OFF`, `ON`, `FAULT`.

The pump refills a tank while demand drains it. It uses **hysteresis** (a deadband): the
pump switches ON at a low water level and OFF at a high level. The gap between the two
prevents the pump rapidly switching on and off around a single threshold — real equipment
does this to avoid burning out motors.

Two safety interlocks force a **latched FAULT**: overflow, or running continuously too
long (a proxy for a dry-run or stuck valve). "Latched" means once it faults, it stays
faulted — it cannot recover on its own.

Interesting structure: an explicit finite state machine, a branch whose behaviour depends
on the *current* mode, and fault logic that overrides everything else.

### 3. `protective_relay.py` — 134 lines — an electrical circuit breaker

The biggest and most complex file. It monitors current, voltage and frequency on a power
feeder and drives a breaker through **four** states:

```
CLOSED -> TRIPPED -> RECLOSING -> CLOSED     (fault cleared, auto-reclose worked)
CLOSED -> TRIPPED -> ...        -> LOCKOUT   (fault persists, retries exhausted)
```

Auto-reclosing is real: most power-line faults (a branch touching a line) are temporary,
so the breaker opens, waits, and tries closing again. If the fault is still there after a
few attempts, it locks out permanently and a human must intervene.

It implements four protection functions: instantaneous overcurrent, **inverse-time**
overcurrent (an energy accumulator roughly `(I/pickup)² × t` — bigger overloads trip
faster), over-voltage, and under/over-frequency.

Interesting structure: a multi-state machine with a block per state, an accumulator with
decay carried across the loop, and several independent trip conditions OR-ed together.

### 4. `sensor_pipeline.py` — 114 lines — a measurement instrument

A **staged data pipeline** — a raw sensor reading passes through six stages in order:

```
raw -> calibrate (gain/offset) -> range clamp -> deadband
    -> moving-average filter (ring buffer) -> classify as NORMAL/WARN/ALARM
```

This is what a real gauge front-end does before showing you a number. Calibration
corrects the sensor's known error; clamping rejects impossible values; the deadband
ignores tiny jitter; the moving average smooths noise using a **ring buffer** (a
fixed-size circular array with modular index arithmetic).

Interesting structure: staged data flow, the ring-buffer loop, and a nested `elif` chain
for classification.

**Why four different files?** Because a transformation that works beautifully on one may
never apply to another. Testing on all four shows us how *general* each transformation
is — which is precisely what the fourth graph measures.

---

## Part 3 — What "transformations" are, and 12 vs 16

A **transformation** (formally a *Semantics-Preserving Transformation*, or **SPT**) is a
single, mechanical rewrite rule that changes how code looks without changing what it does.

The simplest example:

```python
x += 1          # before
x = x + 1       # after
```

Identical behaviour. One is a line longer in more complex cases, and structurally
different. That rule is called `expand_aug_assign`.

Each rule can also be run backwards, which gives us a *direction*:

- **grow** — makes the program longer
- **shrink** — makes the program shorter
- **neutral** — same length, different structure

### The 12 hand-written transformations

I wrote these by hand using **LibCST** (a library that edits Python code while preserving
formatting exactly, unlike the standard library which reformats everything):

| Name | Direction | What it does |
|---|---|---|
| `expand_aug_assign` | grow | `x += 1` → `x = x + 1` |
| `expand_chained_compare` | grow | `a < b < c` → `a < b and b < c` |
| `split_return_expression` | grow | `return f(x)` → `t = f(x)` then `return t` |
| `split_boolean_if` | grow | `if a and b:` → nested `if a:` / `if b:` |
| `extract_subexpression` | grow | pulls a sub-expression into its own named variable |
| `ternary_to_if_else` | grow | `x = a if c else b` → a full `if`/`else` block |
| `if_else_assign_to_ternary` | shrink | the reverse of the above |
| `collapse_aug_assign` | shrink | `x = x + 1` → `x += 1` |
| `merge_nested_if` | shrink | nested `if a:` / `if b:` → `if a and b:` |
| `inline_single_use_return` | shrink | `t = expr` then `return t` → `return expr` |
| `invert_if_else` | neutral | negate the condition, swap the branches |
| `rename_locals` | neutral | systematically rename local variables |

That is the **12-transformation catalogue** — the orange line in your graphs.

### The 4 LLM-designed transformations

Then, following the **Auto-SPT** paper, we let an LLM invent new ones. This is the
"hybrid" approach you chose: **the LLM designs and writes the rule; LibCST applies it.**
The LLM never touches your actual program — it only writes a reusable rewrite rule, which
is then tested to death before being allowed near anything.

The generation pipeline has three steps (Auto-SPT's structure):

1. **Design** — the LLM proposes a transformation in natural language
2. **Implement** — the LLM writes it as LibCST code
3. **Validate & measure** — we run it on all four modules; anything that breaks
   equivalence, produces invalid Python, or never applies is **thrown away**

Four survived:

| Name | Direction | Reward | Δlines | What it does |
|---|---|---|---|---|
| `call_argument_extraction` | grow | 0.548 | +5.0 | pulls function-call arguments into named temporaries |
| `and_chain_nesting` | grow | 0.026 | +1.0 | splits `if a and b and c:` into nested ifs |
| `demorgan_branch_swap` | neutral | 0.018 | 0.0 | applies De Morgan's law and swaps branches |
| `independent_assignment_swap` | neutral | 0.005 | 0.0 | reorders two adjacent statements that don't depend on each other |

**Reward** is Auto-SPT's scoring formula:

```
R(T) = mean over inputs of  A(x, T(x)) × L(x, T(x))
```

where `A` is the equivalence check (1 if still equivalent, 0 if not) and `L` measures how
much the transformation actually changed. A rule that breaks equivalence scores zero no
matter how much it changes; a rule that changes nothing also scores zero.

12 + 4 = the **16-transformation catalogue** — the blue line in your graphs.

**So: "12 vs 16" is the single variable in the experiment.** Same modules, same oracle,
same search, same targets. The only difference is whether the LLM-designed rules are
available. That is what makes it a controlled comparison.

---

## Part 4 — How we know a variant is still correct (the oracle)

This is the part that makes the work scientific rather than decorative. Anyone can shuffle
code around; the hard part is *proving you didn't break it*.

The **oracle** is a function `A(original, variant) → {0, 1}`. It runs four independent
checks, and all four must pass.

### Layer 1 — Golden regression

Before any transformation, `make_golden.py` ran each pristine module and recorded its
exact output into `golden.json`: the full scenario trace plus 20 hand-written probes
(2 for PID, 3 for pump, 7 for relay, 8 for sensor).

A variant must reproduce **every recorded value exactly**. The tolerance is `0.0` — not
"close enough", but bit-identical. This is why float reassociation is forbidden:
`(a + b) + c` and `a + (b + c)` can differ in the last bit, and this layer would catch it.

### Layer 2 — Invariants

Properties that must hold for **any** input, checked over hundreds of seeded
pseudo-random values:

- the pump's water level never exceeds the overflow threshold
- the relay's mode is always one of the four legal states
- once `FAULT` latches, it never un-latches
- the PID command always stays within 0–100

Layer 1 only checks inputs we thought of. Layer 2 catches variants that break on inputs
we didn't.

### Layer 3 — Coverage proof

This layer verifies that the probes **actually reach every branch and state**.

This is the subtle one, and it is what makes layer 1 meaningful. Imagine the golden
vectors never triggered the `FAULT` path. Then a variant could completely destroy the
fault logic and still pass layer 1 — because nothing ever ran it. Layer 3 rules that out
explicitly: it proves the tests exercise the code they claim to.

### Layer 4 — Script-level output *(added most recently)*

Layers 1–3 all work by **importing** each module and calling `run_scenario()`. They never
execute the `if __name__ == "__main__":` block at the bottom of the file.

I proved this was a real hole: I replaced a `print` in that block with
`raise RuntimeError`, and the test suite still reported **"5 passed, 0 failed."** Any
transformation landing in `__main__` was being counted toward the line target and the
diversity score while being **completely unverified** — 15 lines' worth.

The tempting fix was to forbid transforming `__main__`. I did the opposite: layer 4 runs
`python module.py` as a script and compares stdout, stderr and exit code against the
pristine baseline. Now those lines are genuinely verified, so they can keep counting
honestly. It only runs on candidates that already passed layers 1–3, so it costs almost
nothing.

### The honest limit — this matters for your paper

**A passing oracle is not a proof of equivalence.** Program equivalence is *undecidable*
(Rice's theorem — Auto-SPT says this explicitly too). The oracle tests a finite set of
behaviours. So the claim you can defend is:

> *"Equivalent with respect to the golden vectors, 20 branch-covering probes, randomised
> invariant checks, and script-level I/O."*

**Not** *"proven equivalent."*

I have a concrete counterexample from your own corpus: tightening the PID anti-windup
clamp from ±2500 to ±2400 **passes all four layers** — the scenario never drives the
integral past ~1669, the one probe that exceeds it has the command already saturated, and
the invariant is one-sided. That's why there is also a **literal-preservation guard** that
rejects any transformation altering a numeric constant, which catches that entire class
without relying on the oracle at all.

Be the one to state this limitation in the paper. A reviewer will find it otherwise.

---

## Part 5 — The four diversity metrics

Equivalence says the variant is *correct*. Diversity says it is *actually different*.
Both are needed, because each alone is trivial to fake: an exact copy is perfectly
equivalent with zero diversity; random text is maximally diverse and completely broken.
**Achieving both at once is the contribution**, so diversity must be a number, not a
claim.

All four return a value in **[0, 1]**, where 0 = identical and 1 = maximally different.

### `ast_structural` — the headline metric (rename-insensitive)

Parses both programs into their **Abstract Syntax Tree** — the structural skeleton of the
code, ignoring formatting — then flattens it into an ordered sequence of node *types*
(`FunctionDef`, `If`, `Assign`, `BinOp`, …), and measures the edit distance between the
two sequences using `difflib`.

Crucially it records **node types only, never names**. So renaming every variable in the
program scores exactly **0.0000**.

That is deliberate, and it is the single most important design decision in the metric set.
Renaming is the cheapest possible transformation — if it counted, we could inflate our
headline number without doing any real work. By making the headline metric blind to
names, **a good score can only be earned by genuine structural change.**

This is why `rename_locals` never wins in the search: it scores zero on the metric that
decides.

### `cfg_shape` — control-flow shape (rename-insensitive)

Counts control-flow constructs (`if`, `for`, `while`, `try`, …) **tagged by nesting
depth**, and compares the two profiles.

`ast_structural` could in principle be moved by lots of small local edits; `cfg_shape`
only moves when the *branching structure itself* changes. It is the stricter, narrower
signal — it answers "did the shape of the decision-making actually change?"

### `ncd` — Normalized Compression Distance (rename-sensitive)

An information-theoretic measure. Compress program A, compress program B, then compress
them concatenated. If they are similar, the pair compresses much better than the two
separately, because the compressor finds shared patterns.

```
NCD(a,b) = ( C(ab) − min(C(a), C(b)) ) / max(C(a), C(b))
```

It needs no parser and no language knowledge — it is a general-purpose "how similar are
these two byte strings" measure. It *is* affected by renaming, so we report it as
supporting evidence, never as the ranking signal.

### `du_chain` — Define-Use chains (rename-sensitive)

Builds the set of edges "variable *v* is defined here and used there", then computes
Jaccard distance between the two sets.

This captures **dataflow** rather than syntax: it answers "does information move through
this program along different paths?" A transformation that introduces a temporary
variable genuinely rewires the dataflow, and this metric sees that.

### Why four instead of one

Because any single metric can be gamed, and different metrics answer different questions.
Reporting four — two rename-insensitive, two rename-sensitive — means a reviewer can see
that the result isn't an artefact of one particular measure. This follows the two-axis
shape used by Baudry et al. (ISSTA 2014) for their "sosies" and by Galápagos
(Ron et al., TOSEM 2025), which reports both static difference and runtime divergence.

**Only `ast_structural` is used for ranking.** The other three are reported alongside.

---

## Part 6 — How the graphs were produced

### The protocol

`run_experiment.py` runs a full factorial sweep:

```
6 length targets  ×  4 modules  =  24 variants per catalogue
                                 ×  2 catalogues (12 and 16)
                                 =  48 variants total
```

The six targets are expressed as ratios of the original file length:
**0.85, 0.90** (shrink), **1.00** (unchanged), **1.10, 1.20, 1.30** (grow).

So for `pid_governor` (89 lines) at ratio 1.20, the request is "give me an equivalent
program of about 107 lines."

### What happens for each of the 48 cells

We use a **beam search** — Auto-SPT's Algorithm 1 — with beam width 3 and up to 6
iterations. Each iteration does three things:

1. **Expand** — apply every transformation in the catalogue to every program currently in
   the beam, producing many candidates
2. **Filter** — discard anything that isn't valid Python (cheap check first), then run the
   **four-layer oracle** on the rest and discard anything not equivalent
3. **Select** — sort the survivors and keep the best 3

The sort key is deliberately ordered:

```
(  |achieved_lines − requested_lines|,   −ast_structural_diversity  )
   ^ primary: the user's actual request   ^ secondary: tie-break only
```

Diversity **only** breaks ties between candidates equally close to the target. The search
can therefore never sacrifice the requested length in pursuit of a prettier diversity
number. That ordering is what keeps the length control honest.

The search stops early once it lands within tolerance, where tolerance is 10% **of the
requested change** — not of the file size. That distinction matters: 10% of a 97-line
target is ±9 lines, so a search asked to add 8 lines could stop "satisfied" having added
none. Sizing it off the change instead means asking for +20 lines gives a ±2 tolerance.

Across the two runs this generated **690 and 1496 candidate programs**, every one of which
was run through all four oracle layers.

### Reading each panel

**Panel 1 (metric tiles)** — the four headline comparisons between the two catalogues.

**Panel 2 (requested vs achieved)** — the x-axis is the requested ratio; the y-axis is the
**mean actual line change** across the four modules. The dashed grey line is what was
asked for; the two solid lines are what came back. The gap between dashed and solid *is*
the error.

**Panel 3 (diversity by ratio)** — mean `ast_structural` distance for each requested ratio,
both catalogues side by side.

**Panel 4 (applicability)** — for each transformation, how many of the four modules it
changes *at all* when applied to the pristine source. Measured directly, not inferred from
the search.

---

## Part 7 — What we actually found

### 1. Adding transformations pays off superlinearly

| | 12 transformations | 16 transformations | change |
|---|---|---|---|
| grow diversity | 0.0442 | **0.0940** | **+113%** |
| grow reach | +11 lines | **+31 lines** | **+182%** |
| grow mean \|miss\| | 18.92 | **14.00** | −26% |
| on-target (grow) | 0% | **8%** | first ever |
| equivalence | 24/24 | 24/24 | 100% |

A **33% larger** catalogue produced **113% more diversity** and **182% more reach**. This
reproduces Auto-SPT's central scaling claim on ICS code, and it means we are nowhere near
diminishing returns — more generation is the highest-value next step.

### 2. The system saturates, and the graph shows exactly where

In panel 2, the blue line tracks the request until ratio 1.10, then goes **flat at +7.75
lines** — identical at 1.20 and 1.30. Requesting +32 lines produces exactly what
requesting +11 produces.

This changes how the numbers should be reported. At ratio 1.10 the misses are −2, −3, −7,
−1 — the length controller is genuinely accurate. At 1.20 and 1.30 it isn't missing; it is
**out of catalogue**. So report *controller accuracy inside its reach* and *reach* as two
separate findings, rather than one averaged "mean miss" that conflates them.

### 3. We have a grow engine, not a length controller

The left half of panel 2 is the problem. Both arms sit at **−1.5 lines** for every shrink
request, perfectly overlapping. Adding four LLM transformations moved the grow ceiling
nearly 3× and the shrink ceiling **not at all**.

Panel 4 explains why: **only two transformations in the entire catalogue can remove a
line**, worth −5 and −1, on one module each. `pid_governor` and `protective_relay` have
**zero** shrink capability. That is why their achieved line counts are byte-identical at
ratios 0.85 and 0.90 — the search isn't approximating, it is hitting a wall.

All four LLM designs came out `grow` or `neutral`, because nothing in the generation
prompt asked for shrink.

### 4. Three transformations are dead code

`split_return_expression`, `inline_single_use_return` and `rename_locals` don't merely go
unselected — they **never fire on any module**. The first two are pattern mismatches
against this corpus. `rename_locals` is a regression I introduced: when I narrowed it from
renaming parameters to renaming body-locals only (to stop it breaking a `_lcg(seed=...)`
call), I narrowed it past the point of matching anything.

So the effective catalogue is **13**, not 16.

### 5. The 100% oracle pass rate needs careful wording

690/690 and 1496/1496 candidates passed. On its own this is **uninformative** — it is
equally consistent with "the transformations are sound" and "the oracle is weak."

What carries the claim is **fault injection**, not the pass rate: the oracle catches an
injected `RaiseRuntimeError` in `__main__`, catches a single changed output value, and
rejected `if_else_assign_to_ternary` on all four modules when it was destroying `elif`
chains. Report it that way, because a reviewer will otherwise ask exactly this question.

---

## Part 8 — What we have built, as files

```
smart_loader/isomorph/
    spt.py          the 12 hand-written transformations + registry + normalizer
    design.py       Auto-SPT steps 1 & 2 — LLM designs and implements new rules
    oracle.py       the four-layer equivalence gate A(x, x')
    search.py       length-targeted beam search (Auto-SPT Algorithm 1)
    metrics.py      the four diversity metrics + registry
    telemetry.py    records program metrics and LLM features per run

smart_loader/agents/
    logic_substitution_agent.py    the LangGraph node (opt-in, refuses unverified variants)

generate_spts.py       CLI — generate new transformations with an LLM
run_isomorph_agent.py  CLI — run the agent against one module
run_experiment.py      CLI — the full sweep that produced the graphs
ics_corpus/            the four modules + oracle + golden vectors
```

Every registry is **pluggable**: adding a metric or a transformation requires no change
anywhere else. That is the modularity your professor asked for, and it is what will let
the metric set grow as the study develops.

---

## Part 9 — Honest status

**Working and verified:**

- 48/48 variants equivalent under all four oracle layers
- diversity and reach both scale with catalogue size, superlinearly
- length control is accurate within the catalogue's reach
- the oracle demonstrably catches injected faults
- `__main__` transformations are now genuinely verified rather than silently unchecked

**Known gaps, all measured rather than guessed:**

- shrink is effectively unimplemented — 2 transformations, −6 lines, 2 of 4 modules
- reach saturates past ratio ~1.10, so large targets measure the catalogue not the controller
- 3 of 16 transformations never fire; effective catalogue is 13
- the anti-windup clamp gap means the oracle is not, and cannot be, a proof
- the interactive human-in-the-loop length prompt is not built yet (only the batch flag)
- the agent is not yet wired into `core/graph.py` as an edge after `connector`

**The single highest-value next action:** run `generate_spts.py` with a shrink-biased
design prompt. That is the one change that would turn the flat left half of panel 2 into
an actual curve.

---

### References

- Hooda et al., **Auto-SPT**, arXiv:2512.06042 (2025) — the generate/validate/measure loop
  and the beam search we follow
- Baudry et al., **ISSTA 2014** — computation diversity, "sosies"
- Ron et al., **Galápagos**, TOSEM 2025 — static difference plus runtime divergence
