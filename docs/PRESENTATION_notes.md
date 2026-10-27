# Presentation notes — program isomorphism in AXIOM

Companion to `isomorphism_results.pptx` (3 slides).

---

## 1. The paragraph to present

> Since we last spoke I've built the Logic Substitution Agent and run a controlled
> experiment on the four ICS modules you gave me. The agent takes a program and produces
> a variant that is structurally different but behaviourally identical, at a line count
> the user asks for. Equivalence is enforced by a four-layer oracle — golden regression
> against recorded outputs, invariants checked over randomised inputs, a coverage proof
> that the probes actually reach every branch, and a script-level output comparison I
> added after discovering the first three layers never executed the `__main__` block at
> all. I ran a full sweep: six length targets across four modules, twice, once with my
> twelve hand-written transformations and once with sixteen after adding four the LLM
> designed following Auto-SPT. That's forty-eight variants, and all forty-eight passed
> every oracle layer. The headline result is that diversity scales superlinearly with the
> catalogue: a thirty-three percent larger catalogue produced a hundred and thirteen
> percent more structural diversity and nearly three times the reach. The honest caveat is
> that the system saturates — past about a ten percent size change both catalogues go
> flat, so a large requested target measures how many transformations I have rather than
> how well the search steers. And the whole catalogue can only remove six lines in total,
> so right now this is a grow engine rather than a length controller. That's the next
> thing I'm fixing, because all four of the LLM-designed transformations came out grow or
> neutral — nothing in the prompt asked for shrink.

Roughly 90 seconds. Slide 1 while saying it, slide 2 at "saturates", slide 3 at "grow
engine rather than a length controller".

**If you only get three sentences:** superlinear scaling with catalogue size; 48/48
oracle-verified; shrink is unimplemented and that's next.

---

## 2. Where the 690 and 1,496 candidates come from

These are **not** files, and they are **not** 690 separate experiments. They are the total
count of intermediate programs the beam search built and threw away.

### The arithmetic

Each of the 24 cells (6 ratios × 4 modules) runs one beam search with **beam width 3** and
up to **6 iterations**. Each iteration:

```
take every program currently in the beam  (up to 3)
  x apply every transformation in the catalogue  (12 or 16)
  = up to 36 or 48 new candidate programs per iteration
```

Iteration 1 starts from a single program, so it makes 12 (or 16). Iterations 2–6 work from
a beam of 3. Theoretical maximum per cell:

```
12-catalogue:  12 + (5 x 36) = 192
16-catalogue:  16 + (5 x 48) = 256
```

Across 24 cells that would be 4,608 and 6,144. The actual figures were far lower —
**690 and 1,496** — because three things prune heavily:

- a transformation that doesn't match anything returns the program unchanged, and no-ops
  are skipped
- a `seen` set discards duplicates, and different transformation orders often converge on
  the same program
- the search stops early once it lands within tolerance

Measured per cell:

| catalogue | total | mean/cell | min | max |
|---|---|---|---|---|
| 12 | 690 | 28.8 | 2 | 59 |
| 16 | 1,496 | 62.3 | 5 | 111 |

So a typical cell explored about 29 or 62 candidate programs to produce **one** winner.

### Are they variant files?

Each candidate **is** a complete, valid Python program — a full variant of the module,
held as a string in memory. Every one that reached the oracle was checked by all four
layers. But they were **not saved**: they lived for milliseconds and were discarded, and
only the winner survived into `result.variant_source`.

That was a real gap for a paper claiming "1,496 candidates verified equivalent," so
`--artifact-dir` now exists to persist every oracle-passing candidate under
`<dir>/<module>/<ratio>/` with a manifest and a `winner.py`. It is off by default because a
full sweep writes thousands of files.

**One-line answer if asked:** *"Not files — intermediate programs inside the search. Beam
of 3 times 16 transformations times 6 iterations across 24 cells. About 62 per cell. We now
have a flag to persist them."*

---

## 3. The Auto-SPT mathematics, as implemented

Three pieces of Auto-SPT are in AXIOM: the **equivalence oracle**, the **reward function**,
and **Algorithm 1** (beam search). One is used unchanged; two are deliberately substituted.

### 3.1 The equivalence oracle — used unchanged

Auto-SPT defines

$$A : X \times X \to \{0, 1\}$$

a binary function returning 1 if two programs are semantically equivalent, 0 otherwise,
"commonly implemented by checking identical outputs over a set of test inputs."

In AXIOM `A` is `CorpusOracle.check()`. It returns 1 only if **all four** layers pass:

```
A(x, x') = L_golden AND L_invariant AND L_coverage AND L_script
```

Being a **conjunction** matters: a variant must satisfy every layer, so adding layer 4
could only ever make `A` stricter, never looser.

### 3.2 The reward function — strength factor substituted

Auto-SPT scores a transformation `T` over a corpus of programs:

$$R(T) = \frac{1}{n}\sum_{i=1}^{n} A\big(x_i, T(x_i)\big) \cdot L\big(x_i, T(x_i)\big)$$

- `x_i` — the corpus programs. For us `n = 4`, the ICS modules.
- `A` — the oracle, 0 or 1.
- `L` — a *strength* factor: how much the transformation actually achieved.

**The multiplication is the important part.** `A` acts as a **gate**. If a transformation
breaks equivalence on a module, that term contributes `0 × L = 0` no matter how large `L`
is. Correctness cannot be traded for strength.

**What we substituted.** Auto-SPT is adversarial — its `L` is distance from a clone
detector, because it wants to fool one. We have no clone detector and no adversary, so `L`
is replaced by what our search actually consumes:

$$L(x, T(x)) = D_{\text{struct}}(x, T(x)) + \min\left(\frac{|\Delta_{\text{lines}}|}{10}, 1\right)$$

where `D_struct` is the rename-insensitive structural diversity and `Δlines` is the line
count change. The `min(·, 1)` caps the length term so a transformation cannot win on bulk
alone. A transformation that never applies contributes nothing and scores zero —
applicability is a precondition for strength, exactly as in the paper.

**Worked example — `call_argument_extraction`, reward 0.548.** It applies to all 4 modules
with `Δlines = +5` each:

```
per module:  D_struct + min(5/10, 1)  =  D_struct + 0.5
total     :  sum over 4 modules       =  sum(D_struct) + 2.0
reward    :  total / 4                =  mean(D_struct) + 0.5  =  0.548
```

which implies a mean structural diversity of ≈ 0.048. Compare `demorgan_branch_swap`
(reward 0.018): it also applies 4/4 but with `Δlines = 0`, so it earns only the diversity
term. And `and_chain_nesting` (reward 0.026) applies to just **1** of 4 modules, so its
single contribution is divided by 4 — the denominator is the corpus size, not the number of
modules it happened to match, which is what penalises narrow transformations.

**Three oracle-independent hard rejects** run before `A` is even consulted, because the
oracle cannot be trusted to catch these:

- `preserves_literals` — altering a numeric constant is rejected outright. This is what
  catches the anti-windup case (±2500 → ±2400) that passes all four oracle layers.
- `preserves_comments` — deleting comments shrinks the line count without transforming
  anything. That is the shrink-side equivalent of padding and would make the size target
  meaningless.
- `is_valid_python` — never emit code that doesn't parse.

### 3.3 Algorithm 1 — beam search, objective substituted

Auto-SPT composes transformations by beam search: **expand**, **filter**, **select**.

Let `x` be the original, `ℓ*` the requested line count, `B` the beam width (3), `C` the
catalogue, and `D` the ranking metric.

**Initialise** `B₀ = {x}`

**Expand** — apply every transformation to every beam member:

$$E_t = \{\ \text{normalize}(T(c))\ :\ c \in B_t,\ T \in C\ \}$$

`normalize` folds trivial temporaries such as `_t1 = -2500.0` back into their single use,
so composition doesn't accumulate padding.

**Filter** — cheap syntactic check first, expensive oracle second:

$$F_t = \{\ e \in E_t\ :\ \text{IsValid}(e) \wedge A(x, e) = 1\ \}$$

Ordering matters for cost: `IsValid` is a parse, `A` is four subprocess runs.

**Select** — keep the best `B` under a **lexicographic** key:

$$\text{key}(c) = \Big(\ \big|\,\text{lines}(c) - \ell^*\,\big|,\ -D(x, c)\ \Big)$$

$$B_{t+1} = \text{Top}_B(F_t,\ \text{key})$$

This is the second substitution. Auto-SPT selects to *maximise distance from a clone
detector*; we select to *minimise distance from a requested size*, with diversity as a
tie-break only.

**Why lexicographic and not a weighted sum.** Because the two terms are lexicographically
ordered, diversity can only separate candidates that are **equally** close to the target.
The search can never trade away the user's requested size for a prettier diversity number.
A weighted sum would allow exactly that trade, and the length target would stop meaning
anything.

**Stop** when

$$\big|\,\text{lines}(\text{best}) - \ell^*\,\big| \le \tau, \qquad
\tau = \max\!\left(1,\ \left\lceil 0.10 \cdot \left|\ell^* - \text{lines}(x)\right| \right\rceil\right)$$

**Tolerance is 10% of the requested *change*, not of the file size.** Sizing it off the
absolute file makes it meaninglessly loose: 10% of a 97-line target is ±9 lines, so a
search asked to add 8 lines could stop "satisfied" having added none. Off the change
instead, asking `pid_governor` (89 lines) for 107 gives τ = ±2.

### 3.4 Summary of what we kept and what we changed

| Auto-SPT component | In AXIOM |
|---|---|
| Oracle `A: X × X → {0,1}` | **unchanged** — implemented as the 4-layer corpus checker |
| Reward `R(T) = mean A·L` | **structure kept**, `L` substituted: clone-detector distance → diversity + capped line delta |
| 3-step design/implement/compose | **unchanged** |
| `IsValid` pre-filter | **unchanged** |
| Beam search Algorithm 1 | **structure kept**, selection objective substituted: adversarial distance → length target |
| — | **added**: literal- and comment-preservation guards, independent of the oracle |

---

## 4. Likely questions, and answers

**"Is the variant actually equivalent, or does it just pass your tests?"**
It passes our tests, and that's the strongest claim available — program equivalence is
undecidable, so no finite check can prove it. What we can say is: equivalent with respect
to the golden vectors, 20 branch-covering probes, randomised invariant checks, and
script-level I/O. I have a concrete counterexample: tightening `pid_governor`'s anti-windup
clamp from ±2500 to ±2400 passes all four layers, because the scenario never drives the
integral that high. That's why there's a separate literal-preservation guard that rejects
any transformation altering a constant, independent of the oracle.

**"All 1,496 candidates passed. Isn't your oracle just weak?"**
A 100% pass rate on its own is uninformative — I agree. What carries the claim is fault
injection, not the pass rate. The oracle catches an injected `RaiseRuntimeError` in
`__main__`, catches a single changed output value, and rejected `if_else_assign_to_ternary`
on all four modules when it was destroying `elif` chains. The high pass rate is because
transformations are validated at design time, so by the time the search runs, broken ones
are already gone.

**"Why is the mean length miss 14 lines? That seems poor."**
It conflates two things. Inside the catalogue's reach the controller is accurate — at ratio
1.10 the misses are −2, −3, −7 and −1. At 1.20 and 1.30 it isn't missing, it's saturated:
the achieved line count is identical at both, so the number measures catalogue size rather
than control accuracy. I'd report accuracy-within-reach and reach as two separate findings.

**"Why can't it shrink?"**
Only 2 of 16 transformations can remove a line, worth −5 and −1, on one module each.
`pid_governor` and `protective_relay` have no shrink path at all. All four LLM-designed
transformations came out grow or neutral because nothing in the design prompt asked for
shrink. The fix is a direction-biased generation run, which is my next task.

**"Is the LLM writing the variant?"**
No — and this is deliberate. The LLM designs and implements a *reusable transformation
rule*; LibCST applies it. The LLM never touches your program. That means every rewrite is
deterministic, inspectable, and validated once rather than trusted each time. It's the
hybrid arrangement we discussed.

**"Why not just use `ast.unparse` instead of LibCST?"**
`ast.unparse` discards comments and reformats the whole file, so every variant would
differ everywhere and the diversity measurement would be meaningless. LibCST preserves
formatting exactly, so a measured difference reflects a real structural change.

**"Why is `ast_structural` your headline metric and not compression distance?"**
Because it's blind to variable names — a rename-only variant scores exactly 0.0000.
Renaming is the cheapest possible transformation, so if it counted, we could inflate the
headline without doing real work. Making the ranking metric rename-insensitive means a good
score can only be earned by genuine structural change. I do report `ncd` alongside, but
it's rename-sensitive and correlates at r = +0.967 with `ast_structural`, so it adds little
independent information.

**"Are you sure `ast_structural` is measuring the right thing?"**
Partly, and I found a real bias. Across the 48 variants it's *anti*-correlated with
`cfg_shape` (r = −0.189). The best variant by `ast_structural` scores exactly 0.0 on
`cfg_shape` — its branching structure is completely unchanged, and all the diversity is
temporary-variable extraction. Meanwhile the variant that genuinely restructured control
flow ranks near last. For a moving-target-defence argument, changed control flow is
arguably the more valuable diversity, so I've made the ranking signal switchable and plan
to report both as an ablation.

**"How does this connect to the migration pipeline?"**
It sits immediately after the Connector. The migration removes vulnerabilities we already
know about; the diversification hardens against ones nobody has found yet. The agent exists
and works standalone, but isn't yet wired into the graph — that's on my list.

**"Why only four modules?"**
They're the ones you supplied, and they were chosen to cover different control-flow shapes:
a PID loop with nested clamping, a three-state machine, a four-state machine with an
accumulator, and a staged pipeline with a ring buffer. It's enough to show a transformation
generalises or doesn't — panel 3 on slide 3 is exactly that measurement — but it is a small
corpus, and I wouldn't claim external validity beyond ICS-style control code.

**"What would you need to make this publishable?"**
Working shrink, so length control is bidirectional; the ranking ablation; and persisted
variant artefacts so the equivalence claims are inspectable. All three are scoped.

---

## 5. Numbers to have ready

```
48 variants, 48 oracle-passed (24 per catalogue)
2,186 candidate programs generated across both sweeps (690 + 1,496)
4 modules: 89, 96, 134, 114 lines
6 ratios: 0.85 0.90 1.00 1.10 1.20 1.30
20 probes: 2 PID, 3 pump, 7 relay, 8 sensor
4 oracle layers, TOL = 0.0 (bit-exact)
12 -> 16 transformations (+33%)
diversity 0.0442 -> 0.0940 (+113%)
reach +11 -> +31 lines (+182%)
grow mean |miss| 18.92 -> 14.00 (-26%)
shrink reach -6 -> -6 (unchanged)
3 transformations never fire (effective catalogue 13)
2 transformations can shrink
runtime: 94s and 164s for a full sweep
```
