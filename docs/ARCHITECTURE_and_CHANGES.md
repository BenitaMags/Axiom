# Axiom / Proteus — agent architecture and pipeline changes

Status: Track 1 (First-Party Reimplementation) and Track 3c (real-dependency
probes for C1/C2) implemented and tested offline. The FRA has not yet been
exercised against a live provider. Tracks 2 and 3a/3b pending.

---

## 1. The pipeline as it now stands

Two routes through the same graph, chosen by what the subject's
`requirements.txt` asks for.

### Route A — library substitution (10 of 12 subjects)

```
parser → security → resolver → candidate_security → profiler → axiom (LSA)
       → connector (GSA)  → [finalize] → logic_substitution (LMA) → verifier (VCA)
                                                                       │
                            ┌──────────────────────────────────────────┘
                            ├─→ axiom      re-select the target library
                            └─→ connector  regenerate the adapter
```

### Route B — first-party reimplementation (C1, C2) — **new**

```
parser → fra (FRA) → [finalize] → logic_substitution (LMA) → verifier (VCA)
                                                                 │
                                        ┌────────────────────────┘
                                        └─→ fra   only agent that can act
```

Route B skips the resolver, profiler and AXIOM entirely. Those stages exist to
discover, benchmark and choose a replacement *library*; on C1 and C2 there is
no library to choose, and running them spends LLM calls to arrive at "no
suitable candidate" — which is exactly the no-op those subjects produced on
every run before this change.

---

## 2. The agents

| Agent | Role | LLM? |
|---|---|---|
| **Parser** | AST scan: imports (including function-scope), call sites, used API surface | no |
| **Security** | OSV + PyPI lookups for incumbent and candidates | no |
| **Resolver** | Proposes replacement candidates; forms equivalence groups | yes |
| **Profiler** | Import time and memory per candidate, in the host or a disposable venv | no |
| **AXIOM (LSA)** | Scores candidates and selects the winner | yes (API-compat only) |
| **Connector (GSA)** | Analyses call sites, writes adapters via an ACI editing session | yes |
| **FRA** | **new** — implements the outgoing library's used API surface as first-party code | yes |
| **LMA** | Semantics-preserving transformations over first-party logic, oracle-gated | no (libCST rules) |
| **VCA** | Static analysis, import smoke test, patch integrity, project tests; routes repairs | no |

---

## 3. What changed, and why

### 3.1 New: First-Party Reimplementation Agent
`smart_loader/agents/first_party_agent.py`

**Problem.** C1 declares `simple-pid → in-house controller`; C2 declares
`transitions → hand-rolled FSM`. The LSA selects among libraries and has no way
to express "write it yourself", so all six runs of both configurations declined
and returned the file untouched.

**Contract.** The FRA preserves the outgoing library's **used** API surface
exactly — same constructor arguments, defaults, attributes, call forms and
return types — scoped to what the parser recorded this file actually calls.
Scoping matters: it makes "preserve it exactly" satisfiable rather than a
request to reimplement a whole library.

**Why it bypasses the GSA's editing session.** That session exists to bridge an
interface delta. The FRA is constrained to produce none, so every existing call
site is already correct. An LLM editing pass over a file needing no edits cannot
improve it and can damage it — we observed that session rewrite a call site
other than the one its own analysis identified. Skipping it also removes a
repair target and its loop.

**Inline, not a new module.** The replacement is written into the same file.
The pipeline carries one `patched_code` back to the runner; the GSA already
injects wrappers inline; and `run_axiom_arms`'s diversification pass iterates
the program's *declared* source files, so a new sibling module would silently
escape the LMA — which on these two subjects is exactly the code the LMA exists
to diversify.

**Local execute-and-repair loop**, up to 3 attempts, rejecting on: no fenced
block; a rewrite that does not parse; a removed top-level definition (a
"rewrite" that quietly becomes a different program); or a surviving import of
the outgoing library. Each rejection is fed back with its reason.

### 3.2 New: shared migration finalisation
`smart_loader/core/migration_finalize.py`

Four mechanical steps, previously buried inside `connector_agent` next to the
ACI session, which made them look like part of the GSA. They are not:

1. rewrite the outgoing import to the incoming one
2. repoint recorded call sites at their wrapper
3. delete imports the migration left unreferenced
4. **reject** a patch that still imports the removed library

Step 1 alone is what turns working code into a completed migration — without it
the file keeps `from simple_pid import PID` and the run scores `lib_swapped=N`
however good the new code is.

Extracting them was not tidying. The distribution-name-versus-import-root
defect (`paho-mqtt` looked up as `paho_mqtt`; the root is `paho`) survived in
**four** separate copies of this logic, each silently matching nothing. One
implementation is the fix.

**`winner_import` empty means inline.** There is no incoming module, so the
outgoing import must be *deleted*, not redirected. Rewriting it to a module
that does not exist would trade a missing dependency for an `ImportError` —
a mistake I made in the first draft and caught in test.

### 3.3 Graph routing
`smart_loader/core/graph.py`

- `build_graph(reimplement_mode=...)` and `run_loader(reimplement_mode=...)`.
- When set: `parser → fra`, `fra → logic_substitution`, and `verifier_edges`
  becomes `{"axiom": "fra", "connector": "fra", "end": END}` — both re-entry
  routes map onto the FRA, so a verifier verdict is never dropped on a path
  where no other agent can act on it.
- When unset, the graph is byte-identical to before. Covered by a test.

### 3.4 Runner
`run_axiom_arms.py` passes `reimplement_mode=program.reimplement`, which is
already derived from the `TO:` header naming a non-installable target.

---

### 3.5 Track 3c — real-dependency probes for C1 and C2
`equivalence/probes.py`, `equivalence/golden.json`

**What we found.** Neither subject's oracle exercised the dependency the
migration replaces.

*C1.* All five tests in `test_controller.py` inject a `FakePID`, and the probe
did the same. `_make_pid` — the only code a migration touches — was never
called by anything. Layer 5 does not reach it either: it looks for factories
named `_default_*`. Proof: replacing the factory body with a tripwire that
always raises produced a **passing** oracle under the old probes, and now
fails with `probes failed: AssertionError: the PID engine was constructed`.

*C2.* The probe constructed the real `TankController` but never called
`c.step(level)` and never wired the valve commands into `Process`, so both
valves stayed shut and the level stayed at 0.0. The recorded golden vector was
**forty identical rows of `[0.0, "IDLE", True]`** — it asserted that a state
machine does not move. Any reimplementation that constructed without raising
satisfied it.

**What the probes now record.**

| key | contract |
|---|---|
| `engine_proportional` | P-only response across the range, both output limits, sign convention |
| `engine_setpoint` | assignment to `.setpoint` takes effect on the next call |
| `fsm_trace` | 60 steps traversing FILLING → HOLDING → DRAINING → IDLE with valve states |
| `fsm_trip` / `fsm_trip_latches` | wildcard trip reaches FAULT and both valves shut |
| `fsm_trip_latches_on_event` | after a trip, an explicit event does not resume filling |
| `fsm_guard_blocks_fill` | above the interlock point, `start_fill` is refused |

**Three design decisions worth recording, each forced by a failed attempt.**

*A fresh engine per input.* `simple-pid` defaults `sample_time=0.01` and
returns its previous output for any call inside that window, so the first
version of the probe looped over four inputs and recorded the first answer four
times. Constructing per input takes the first-call path every time.

*Ki=0 and Kd=0.* With a non-zero integral the output depends on wall-clock
`dt`: three identical constructions differ in the ninth decimal, which a
zero-tolerance oracle flags as divergence on every run. The proportional
response is exactly determined and pins what matters across implementations.

*Outcome, not exception type.* `transitions` raises `MachineError` for an
invalid trigger. A hand-rolled machine will not raise that class, and requiring
it would make reimplementation impossible by definition. The probe catches the
exception and records the resulting state and valve positions.

**Two gaps found by the negative controls, not by inspection.** The first
version of the latch check used `step()` after a trip — but `step()` takes no
branch in FAULT, so it fires nothing and *any* machine appears to latch. The
first version of the guard check called `start_fill()` on an empty tank, where
`can_fill()` is true anyway, so deleting the guard changed nothing observable.
Both variants passed. They now fail.

**Discrimination, verified** (`tests/test_c1_c2_real_dependency_probes.py`):

| variant | verdict |
|---|---|
| correct first-party PID | PASS |
| inverted sign convention | FAIL — `engine_proportional`, `engine_setpoint` |
| `output_limits` ignored | FAIL — same |
| engine-construction tripwire | FAIL — the engine is reached |
| correct hand-rolled FSM | PASS |
| trip does not latch | FAIL — `fsm_trip_latches_on_event` |
| interlock guard dropped | FAIL — `fsm_guard_blocks_fill` |

**Consequences to be aware of.**

* `golden.json` was regenerated. C1 and C2 changed; C3, D1, D2 and D3 are
  byte-identical. The previous file is kept at `golden.json.bak-preC12`.
  C1/C2 oracle verdicts in the published results were vacuous passes, and no
  arm ever migrated either subject, so no reported number depends on them.
* **`simple-pid` is now a required dependency of the evaluation environment.**
  The C1 probe constructs the real engine, so recording or checking golden
  vectors fails without it. It is a *source* library, not a target, so
  `ensure_targets` does not install it; install it explicitly.
* All six C/D programs still pass their own oracle unmodified.

---

## 4. Earlier changes in this cycle (context for the above)

| Change | Effect |
|---|---|
| Install the model's **chosen** library before verifying | 6 of 8 recorded failures in one arm were harness artifacts; first-attempt success 78% → 92% |
| `resolve_import_root()` centralised in `ast_rewrite` | fixed the wrong key in the import rewrite, the dead-import sweep, the verifier's patch-integrity check, and the profiler |
| Dead-import sweep runs on every path | was gated behind `migration_feasible is True`, so it never ran on the arms that needed it |
| Deterministic call-site rewriting (`apply_call_site_rewrites`) | the ACI session was editing the wrong line; the connector already knew the right one |
| Import-removal guard relaxed to *live* removals only | B2: 12 calls → 7, $0.386 → $0.189, same result |
| `oracle_passed` carried through `_merge_iso` | 16 of 18 C/D rows recorded a failed oracle beside a detail reading "all layers passed" |
| `is_noop` also treats `lib_swapped=N` as a no-op | a clone that never migrated is trivially faithful; counting its CLEAN rewarded not doing the work |

---

## 5. Corrected failure decomposition — please read before Track 2

My earlier plan said Track 2 was worth 4 rows. **It is worth 2**, and the two
I misattributed are a different failure. `proteus_full`'s 13 missing
migrations:

| cause | rows | detail |
|---|---|---|
| C1/C2 capability gap | 6 | 2–3 LLM calls each, file untouched — **Track 1 addresses this** |
| True no-op | **2** | A2 run1, A3 run1 — **1 LLM call each** |
| Changed but no swap | 2 | C3 run1, run2 — **1 LLM call each**, source modified, `smtplib` still imported |
| Generation failure | 3 | A3 run3 (30 calls), D1 run1 (9), D3 run3 (13) |

### The `llm_calls = 1` clue

All four Track-2 rows made **exactly one** LLM call, against 6–14 for a normal
run. One call is the resolver's discovery call. That points at
`_after_resolver`:

```python
if not state.get("equivalence_groups"):
    return "end"
```

The resolver proposes candidates and then discards them during its own fit
review — we have seen it log *"Discovery leads not advanced to an equivalence
group after file/API fit review"* — leaving no group, so the graph ends
immediately with the source untouched.

**This supersedes the profiler-veto hypothesis**, which was disproven (the host
profiler marks candidates `neutral_unattempted`, which is not a veto) and on
which I had already built one inert fix. Track 2 should still begin by
instrumenting rather than fixing: dump the resolver's candidate list and the
reason each lead was dropped, on these four (program, seed) pairs.

Exact rows to re-run:

```
proteus_full      A2_sensor_json_logger        run1
proteus_full      A3_iso_ts_parser             run1
proteus_full      C3_anomaly_threshold_engine  run1
proteus_full      C3_anomaly_threshold_engine  run2
```

Clones for all four are present under `clones_v2/proteus_full/`.

---

## 6. Revised projection

| | rows |
|---|---|
| `proteus_full` today | 23/36 |
| + Track 1 (C1/C2), if the FRA succeeds on all six | 29/36 |
| + Track 2 (4 rows) | 33/36 |

Against `single_shot` 32/36 and `single_shot_repair` 34/36. Parity with the
plain baseline is reachable; passing the repair baseline is not, on these
two tracks alone.

Track 3 will move every arm **down**, including this one. That is the intended
effect of building an oracle that can see defects the current one cannot.

---

## 7. Verification status

| | |
|---|---|
| Unit tests | 139 passed, 2 pre-existing failures unrelated to this work |
| New tests | `tests/test_first_party_agent.py` — 9 tests: routing on and off, the acceptance contract, lazy and shadowed import rejection, the no-redirect rule, definition-deletion detection |
| Live run | **not yet done** — the FRA has never called a provider |

The next step is a single live run on C1, one seed, to confirm the FRA produces
a working PID and that `finalize_migration` accepts it:

```bash
python3 run_axiom_arms.py --corpus ~/Downloads/evaluation \
  --config proteus_full --program C1_pid_pump_controller --runs 1 \
  --csv /tmp/fra_check.csv --clone-dir /tmp/fra_clones
```

**The prerequisite is now met.** C1's probe previously injected a `FakePID`,
so a first-party engine would have been certified by an oracle that never ran
it. Section 3.5 fixes that: the probe constructs the real engine, and a
tripwire in the factory now fails the oracle. A C1 pass is therefore meaningful
in a way it would not have been a day ago.

## Track 1 — two silent-failure bugs found while validating the FRA on C1

Both were found by asking why `controller.py` showed zero LMA-introduced
changes, and both are the same failure mode: a stage reported success while
doing nothing.

### 1. The FRA had no outgoing edge (`smart_loader/core/graph.py`)

In reimplement mode the intended flow is
`parser → fra → logic_substitution → verifier → END`. The edge
`graph.add_edge("fra", verifier_entry)` was never actually added: the patch
that was supposed to add it used `str.replace()` without checking that the
anchor matched, printed a success line, and no-opped. LangGraph compiles a node
with no outgoing edge without complaint and simply terminates after it, so the
real flow was `parser → fra → END`.

Consequence: on the first live C1 run the **Logic Substitution Agent and the
Verifier Agent never executed in-graph**. The row's `migration_success`,
`lib_swapped_correctly` and oracle verdict were still valid, because
`run_axiom_arms` computes those from the clone on disk rather than from the
graph — but the run was an FRA-only ablation, not `proteus_full`, and must not
be quoted as evidence about the full system.

Fix: the edge is added, and the patch now asserts it is present in the file
afterwards. Two regression tests in `tests/test_first_party_agent.py`:
`test_the_fra_reaches_the_lma_and_the_verifier` pins the path, and
`test_every_reachable_node_leads_somewhere` walks the compiled graph from
`__start__` asserting every reachable node has an outgoing edge. The second is
scoped to *reachable* nodes deliberately: the library-substitution nodes are
intentionally unreachable in reimplement mode, and flagging them would be a
false positive.

### 2. The in-graph LMA variant was never written to disk (`run_axiom_arms.py`)

After `run_loader` returned, the per-file write-back wrote only
`state["patched_code"]`. The Logic Substitution Agent returns its result in
`isomorph_variant_code` and does not touch `patched_code`, so the variant it
produced for the file the graph handled was computed, oracle-checked, reported
— and then discarded. Meanwhile the CSV read `state["isomorph_report"]`
unconditionally, so the row credited a transformation the shipped clone did not
contain.

Observed on the re-run: the LMA reported `controller.py` at 128 → 141 lines,
oracle PASS; the file on disk was still 128 lines. The row's `iso_lines` read
`162->177` while the clone summed to 164 — the 13 lines of the controller
variant were exactly the gap. The two modules reached by `_diversify_remaining`
(`io_map`, `plant`) were written correctly, because that helper has its own
`isomorph_variant_code` write.

This is pre-existing and affects the primary module of every C/D `proteus_*`
row in the published results: their reported diversity describes a variant that
was not shipped. The `oracle_equiv`, `migration_success` and test columns are
unaffected.

Fix: the write-back prefers the variant when `isomorph_report["applied"]` is
non-empty and the variant is non-blank, and falls back to the patch otherwise,
so a rejected or empty variant can never blank a file. Six regression tests in
`tests/test_in_graph_variant_is_shipped.py` cover the decision table and pin
the write-back source against drift; two of them fail against the pre-fix file.

### Token accounting for the FRA

The FRA made a real LLM call but the row showed `llm_calls=0`,
`tokens_total=0`, `cost_usd=0.0` — it never called `record_usage`. Added. The
re-run reports `llm_calls=1`, `tokens_total=2894`, `cost_usd=$0.0274`.

## Track 1 — the oracle PASS that wasn't (third bug, found on the C1 re-run)

The write-back fix worked: the CSV and the clone now agree. But the re-run's FRA
produced a different 214-line `controller.py` (temperature 0.7), and the LSA
reported `0/8 candidates equivalent`, `Transformations: none applicable`, and
then **`Equivalence oracle PASS`**. The row recorded `oracle_equiv=Y`,
`migration_success=Y`, 5/5 tests.

All of that was wrong, for two compounding reasons.

### The reimplementation was broken

`PID.__init__` runs `self.output_limits = output_limits` on line 37. That
invokes the property setter, whose last two statements read `self._integral` —
which `__init__` does not define until line 44. Constructing a `PID` with any
output limit therefore raises
`AttributeError: 'PID' object has no attribute '_integral'`. `_make_pid` passes
a limit on every call, so *the class never worked at all*.

Nothing in the existing corpus could see this. All five C1 tests inject a
`FakePID`; the surface check only compares top-level names; the file compiles
fine. The only thing that constructs the real class is the
`engine_proportional` probe added in this track — which is precisely the gap
that probe was written to close. It fired correctly: the oracle returned
`equivalent=False` for the seed *and* for all 8 candidates, with the
AttributeError as its detail.

### The search then relabelled the failure as a pass

`LengthTargetedSearch.run` computed the final verdict as
`oracle_passed=final.equivalent if final else True`, where `final` is `None`
whenever no transformation survived. The reasoning was that unchanged code is
trivially equivalent — true with respect to the **seed**, but the oracle
compares against the pristine **original**, and in reimplement mode the seed is
the FRA's rewrite. So a module the oracle had just rejected eight times was
shipped with `oracle_passed=True`.

In the library-substitution path this assumption is close to harmless, since
the seed there is a patch the Verifier already gated. In reimplement mode it
masks exactly the failure the FRA can produce.

### Fixes

1. `smart_loader/isomorph/search.py` — the seed is now checked like any other
   candidate, and the detail states whether it passes or **FAILS**. Four tests
   in `tests/test_seed_is_oracle_checked.py`; three fail against the old file.

2. `smart_loader/agents/first_party_agent.py` — new `_behaviour_check()` runs
   the `ProgramOracle` against the candidate inside the FRA's repair loop, so a
   behaviourally wrong rewrite becomes repairable feedback instead of a shipped
   success. The feedback text tells the model explicitly that passing the
   program's tests proves nothing because they inject a fake. The gate abstains
   (returns pass) when no oracle corpus is configured or the oracle cannot run,
   so categories without golden vectors behave exactly as before. Five tests in
   `tests/test_first_party_agent.py`, including one that pins the defective
   constructor pattern so the gate is demonstrably guarding something real.

### What this means for the earlier C1 result

The first "C1 finally migrates" run (128-line controller, 117/146 candidates
equivalent, divergence 0.3795) happened to name its internal field `_integral`
and genuinely passed the probes. That result stands. But it was luck, not a
property of the pipeline: the run reported here is the same agent, same prompt,
same temperature, producing a class that never constructed. Until the FRA's
behavioural gate has been exercised across several seeds, C1 should be reported
as *sometimes* migrating, with the variance stated.

## Track 1 — C1 validated across three seeds

Three fresh `proteus_full` runs with all of the above in place:

| run | FRA attempts | controller lines | candidates equivalent | migration | oracle | divergence |
|-----|--------------|------------------|----------------------|-----------|--------|------------|
| 1 | 2 | 118 → 116 | 176/176 (100%) | Y | Y | 0.4338 |
| 2 | 2 | 197 → 223 | 162/162 (100%) | Y | Y | 0.2093 |
| 3 | 1 |  98 → 104 | 165/165 (100%) | Y | Y | 0.4298 |

Three things are worth separating here.

**The write-back fix holds.** Every clone's `controller.py` is exactly the line
count the LSA reported (116 / 223 / 104). Before the fix all three would have
shipped the pre-variant file.

**The result is independently re-verified.** Each shipped `controller.py` was
re-checked against the `ProgramOracle` outside the pipeline, after the run: all
three pass all four layers. This is not the pipeline grading its own homework.

**The behavioural gate fired, and the repair worked.** Runs 1 and 2 were
rejected on attempt 1 and succeeded on attempt 2; run 3 passed first time. The
100% candidate-equivalence rate (vs 0/8 on the broken rewrite) is the clean
signal that what reached the LSA was a working class. Note what this costs: one
extra LLM call on 2 of 3 runs. That is the price of the gate, and it is cheap
relative to shipping a `PID` that never constructs.

### A fall-through bug in the gate's own feedback

Runs 1 and 2 reported their attempt-1 rejection as
`` `simple-pid` is still imported and in use () `` — an empty survivor list,
because there were none. The behavioural branch set the correct message and then
fell through to the incomplete-migration branch, which overwrote it. The model
was repairing against the wrong diagnosis and still recovered, which is luck.
Fixed by making the branches mutually exclusive (`else`, not fall-through), with
a test that pins the structure.

So the gate's real repair rate is not yet measured: the two repairs observed
happened *despite* the feedback, not because of it. Expect it to be at least
this good now, but it needs re-measuring before the number is quoted.

## Track 1 — the judge overturned the C1 result

Running the blind LLM judge on the three validated C1 clones returned
**MAJOR on all three, 0 CLEAN** — against an oracle that had passed all three
on all four layers. The judge was right and the oracle was wrong. Verified
directly against the installed `simple_pid`, using the program's real gains
(Kp=2.0, **Ki=0.5**), six rapid calls:

```
simple_pid (REAL)   [12.000004, 12.000004, 12.000004, 12.000004, ...]   holds
rewrite 1           [12.0,      11.000009, 10.000015,  9.000018, ...]   no hold
rewrite 2           [12.0,      12.0,      12.0,       12.0,      ...]  holds
rewrite 3           [15.0,      14.000007, 13.000012, 12.000015, ...]   dt=1.0
```

Two real behaviours were missed, both confirmed by reading
`simple_pid.PID.__call__` and `__init__`:

1. **The sample_time hold.** `if self.sample_time is not None and dt <
   self.sample_time and self._last_output is not None: return
   self._last_output`. Default `sample_time=0.01`, so in a tight loop the engine
   returns its previous output unchanged. Rewrites 1 and 3 recompute every call.

2. **The first-call dt.** `__init__` calls `self.reset()`, which sets
   `_last_time = self.time_fn()`. The first call therefore sees a dt of
   microseconds and the integral contributes ~4e-6 — the first output is the
   proportional term. Rewrite 3 starts `_last_time = None` and substitutes
   `dt = 1.0`, injecting `Ki*error*1.0 = +3.0` on the first command (12 → 15)
   and shifting the whole trajectory.

### Why the oracle could not see it

Because of a decision I made and justified in a comment. `engine_proportional`
and `engine_setpoint` set **Ki=0 and Kd=0** and build a fresh engine per input,
to stop the integral making the output wall-clock dependent at the ninth decimal
on a zero-tolerance oracle. The comment claimed this "pins the properties that
actually matter across implementations — sign convention, gain, and both output
limits." That was wrong: removing the clock removed the sample_time hold and the
first-call dt, which are the library's two most observable behaviours in a
program that runs Ki=0.5.

So the fix for the probes' flakiness created the blind spot, and it took an
independent reviewer to find it. The golden vectors are not at fault as
*vectors* — they are exact and reproducible. The fault was treating them as
sufficient.

### Fix — two clock-agnostic invariants, not more golden vectors

Added to `_inv_c1` in `equivalence/probes.py`, phrased as tolerance-based
properties so they are exact about the semantics and agnostic about the clock:

* **the hold** — four back-to-back calls after the first must be identical to
  it (within 1e-9). Catches any engine that recomputes inside the window.
* **the first-call dt** — the first output must equal `Kp * error` within 0.01.
  Catches a dt substitution large enough to matter without asserting a
  wall-clock-dependent figure.

Results: the **pristine original passes all four layers**; rewrites 1 and 3 are
now rejected naming the exact violation; rewrite 2 still passes. Rewrite 2 is
the right call — its only flagged difference is gating the hold on `_last_time`
rather than `_last_output`, which is observable only after `reset()`, and the
program never calls `reset`, `auto_mode`, `tunings` or `components`.

`golden.json` is byte-identical after regeneration — this change touches
invariants only, so no recorded vector moved and no earlier result is
invalidated by the probe edit itself.

### Two bugs in my own test suite, found by the new invariants

1. **The positive control was not faithful.** `C1_PID` in
   `tests/test_c1_c2_real_dependency_probes.py` was proportional-only with no
   hold and no constructor-initialised `_last_time`, so the new invariants
   rejected it. An unfaithful positive control silently weakens every negative
   control beneath it. Rewritten in full with P+I, the hold, and the clock init.

2. **A negative control had silently stopped testing anything.**
   `test_c1_rejects_ignored_output_limits` built its broken variant with a plain
   `.replace()` whose pattern no longer matched once `C1_PID` gained a `_clamp`
   helper, so `bad == C1_PID` and it asserted that *correct* code is rejected.
   All negative controls now go through `_break()`, which asserts the pattern
   matched and the source actually changed. This is the fourth silent-no-op
   `.replace()` in this session.

Three new tests pin the invariants themselves: one rejects an engine with the
hold removed, one rejects the `dt = 1.0` substitution rewrite 3 actually made,
and one asserts the pristine original still passes every layer.

### Standing correction to the C1 claim

"C1 migrates 3/3" is withdrawn. Under the corrected oracle it is **1/3**
(rewrite 2). The FRA's behavioural gate now has genuinely discriminating
invariants to repair against, which it did not have during that run, so C1 must
be re-run before any number is reported. The judge's verdicts were not noise and
should not be treated as a second opinion to be weighed against the oracle — on
this subject it was strictly more accurate than the oracle was.

## Track 1 — second judge round: one real defect, one false alarm

Re-running the judge on the corrected C1 clones: run1 CLEAN (correctly discounted
as NO-OP, so 0/2 clean among attempted), runs 2 and 3 MAJOR. Both MAJOR findings
made the same claim, and each run carried a secondary claim. I checked all of
them against the installed `simple_pid` rather than accepting the verdicts.

### CONFIRMED — derivative on error instead of on measurement

`simple_pid.PID.__call__` defaults `differential_on_measurement=True` and
computes `-Kd * (input - last_input) / dt`. Both rewrites used
`Kd * (error - last_error) / dt`. Those are algebraically identical while the
setpoint is fixed (`d_error == -d_input`) and diverge the moment it moves —
which this program does every cycle through `_ramp_setpoint`. Measured with
kp=2.0, ki=0, kd=0.05, explicit dt=0.1, setpoint stepping down:

```
simple_pid (REAL)   [12.0, 7.5, 3.5, 0.0]
run 2               [12.0, 7.0, 3.0, 0.0]
run 3               [12.0, 7.0, 3.0, 0.0]
```

In scope, not library trivia: `kd` is a parameter of the program's own
`PumpController.__init__`, so a caller reaches it without touching the engine.

### NOT REPRODUCIBLE — run 3 returning `None` and crashing the caller

The judge claimed run 3's first call could return `None` when less than 10 ms
had elapsed, making `max(0.0, min(100.0, None))` raise `TypeError` in
`PumpController.update`. Tested directly: **0 of 200** fresh constructions
returned `None`, and 50 consecutive `PumpController.update` calls ran clean.
Both implementations guard the first call. The judge hedged this one ("can
return"), and it was wrong — a reminder that these verdicts are leads to check,
not facts, which is what the tool's own docstring says.

### Fix — a third invariant, also clock-free

Asserting the numbers above directly would mean asserting a wall-clock-dependent
derivative. Holding the **input constant** removes the clock entirely: `d_input`
is then 0, so the measurement derivative is exactly 0 for any `dt` and the output
is exactly `Kp * error`. An error-based derivative injects a nonzero dt-dependent
term instead. With the setpoint stepping 6 → 5 → 4 and the input held at 0:

```
simple_pid (REAL)   [12.0, 10.0, 8.0]      exact, deterministic
run 2               [12.0,  8.07, 6.01]    rejected
run 3               [12.0,  8.01, 5.80]    rejected
```

The three 20 ms sleeps are what make the derivative reachable at all — without
them the sample_time hold returns the cached first output and the property is
invisible.

Verified after the change: the **pristine original passes all four layers**;
v2 runs 2 and 3 are rejected naming the exact violation; v1 run 2 — the fullest
of the earlier reimplementations — still passes, because it got the derivative
basis right. `golden.json` byte-identical after regeneration.

### A third problem with my own positive control

`C1_PID` had no derivative term at all, so it passed the new invariant by
accident rather than by being correct. Rewritten with derivative-on-measurement
and `_last_input` tracking. `_break()` then immediately failed the negative
control whose pattern had gone stale — which is the helper doing exactly the job
it was added for one turn earlier.

### Current C1 standing

| | migrated | oracle (corrected) | judge |
|---|---|---|---|
| v2 run1 | N — FRA exhausted 3 attempts, refused | pass (is the original) | CLEAN, discounted NO-OP |
| v2 run2 | Y | **fail** — derivative on error | MAJOR |
| v2 run3 | Y | **fail** — derivative on error | MAJOR |

So C1 is **0/3 under the current oracle**, and the honest reading is that each
round of review has found a further real fidelity gap that the previous round's
probes could not see: first the `_integral` constructor ordering, then the
sample_time hold and first-call dt, now the derivative basis. The gate is
working — it refused run1 rather than shipping it — but the oracle it gates
against has been incomplete at every step. The three invariants now cover every
defect either reviewer has actually found, which is not the same as covering
every defect.

## Track 1 — the reference-source experiment failed, and how it failed matters

I proposed supplying the outgoing library's own source (`inspect.getsource`) as
prompt context, on the reasoning that a person removing a dependency reads it
first, so this converts "recall the semantics" into "translate this code". Three
C1 runs later that reasoning is refuted.

**All three runs were byte-identical.** Same MD5, 334 lines, divergence 0.1802,
169/169 candidates equivalent — at temperature 0.7, which does not happen unless
the model is copying. It was: **107 of the 130 code lines of `simple_pid.PID`
appeared verbatim (82%), with a longest unbroken run of 31 lines.**

The change did not teach the agent to reimplement the library. It taught it to
**vendor** the library. Everything else follows: the jump from ~130-220 lines to
310, the perfect determinism (copying is deterministic), and a flawless oracle
pass — flawless because the code *was* `simple_pid`.

Why this invalidates the run rather than merely weakening it:

* It is not a migration. Third-party source is pasted into the file, carrying
  the original's licence, which is the exact opposite of removing the dependency.
* Byte-identical output across seeds destroys the premise the Logic Substitution
  stage rests on.
* A C1 "success" obtained this way could not be defended in the paper.

### Response

**The injection is now off by default**, behind `fra_supply_library_source`. It
is kept rather than deleted because "how much does an oracle-verified rewrite
improve when the agent may read the dependency?" is a real question whose answer
turned out to be "it stops being a rewrite" — that is a finding, reportable as
an ablation. It must never be enabled for a headline number, and the console
warns when it is.

**A provenance guard was added**, which should have existed regardless — a model
can reproduce a popular library from memory with no prompt help at all.
`_verbatim_overlap()` measures the share of the outgoing library's code lines
reproduced in runs of >= 4 consecutive lines, comments and docstrings stripped.
It runs BEFORE the behavioural gate, because a vendored copy passes that gate by
construction and can only be rejected on provenance.

Calibrated on five real files rather than a guessed number:

| file | overlap (runs >= 4) | longest run | verdict |
|---|---|---|---|
| v3 run1 — vendored | **73.1%** | 31 lines | REJECTED |
| v2 run3 — honest | 44.6% | 13 | accepted |
| v1 run2 — honest, fullest surface | 15.4% | 10 | accepted |
| v2 run2 — honest | 6.2% | 8 | accepted |
| pristine original | 0.0% | — | accepted |

Threshold 0.60, inside the 45%–73% gap.

Requiring *runs* rather than any coincident line was necessary, not cosmetic:
counting every matching line scored an honest rewrite at 63% against the copy's
82%, too close to separate safely. A PID's attribute assignments and update step
have a canonical form, so `self.Kp = Kp` matching proves nothing.

Six tests cover it, including that a straight copy scores >90%, that scattered
coincident lines score <10%, that an unreadable library reports "no evidence"
rather than guessing, that the guard precedes the behavioural gate, and that the
library source is not supplied by default.

### What survives from the two changes

`MAX_ATTEMPTS = 5`, the incremental repair loop (which sends the rejected attempt
and its diagnosis instead of restarting from the original), and the four prompt
cues about behaviour the program's tests cannot see. None of those hand the model
the library; all of them address the real weakness, which was that the loop
restarted from scratch on every retry. They are untested against C1 so far,
because the vendoring masked them completely — every run succeeded on attempt 1,
so the repair loop never ran.

### Standing correction

The v3 C1 result (3/3, all oracle layers passing) must not be reported. It is a
vendored copy of `simple_pid` presented as a reimplementation. C1's last valid
measurement remains v2: **0/3**.

## Track 1 — the judge has a blind spot symmetrical to the oracle's

Judging the vendored v3 clones returned **3/3 CLEAN, 0 findings**, which is the
predicted outcome and not a good one. The judge is asked one question — *does
this behave differently from the original?* — and a verbatim copy of the
dependency behaves **identically**. CLEAN is the correct answer to the question
asked and a completely wrong verdict.

This is worth stating carefully because it is a methodological finding, not a bug
in the judge. The evaluation now has two independent reviewers that between them
still cannot see provenance:

| check | sees behaviour | sees provenance |
|---|---|---|
| five-layer oracle | yes | no |
| blind LLM judge | yes | no |
| `_verbatim_overlap` | no | yes |

Neither model-based reviewer can catch copying, for the same structural reason:
both evaluate *what the code does*, and a copy does exactly the right thing.

### Fix — `is_vendored`, the counterpart of `is_noop`

`judge_clones.py` gained a `VENDORED` outcome alongside `NO-OP`. Both catch a
clone that earns a good verdict without doing the work, both are measured from
the files rather than asked of a model, and both are excluded from
`clean/attempted`. The distinction between them is real and worth keeping
separate: a no-op changed nothing, while a vendored copy changed plenty and wrote
someone else's code while doing it — so `VENDORED` is checked first.

It only applies to reimplement subjects (C1, C2). On a library-substitution
subject, resembling the *outgoing* package is neither expected nor meaningful, so
the check abstains rather than guessing.

Re-scored from cache, with no new API calls — the verdicts were already paid for:

```
configuration    BROKEN  MAJOR  MINOR  CLEAN  NO-OP  VENDORED  clean/attempted
proteus_full          0      0      0      0      0         3             0/0
```

Regression-checked against both earlier rounds: **0 VENDORED on all six honest
clones**, so the new check does not misfire on legitimate reimplementations. The
v1 round stays 0/3 MAJOR and the v2 round stays 0/2 with one NO-OP.

Five tests in `tests/test_vendored_detection.py`, including one that builds a
vendored clone by pasting `inspect.getsource(simple_pid.PID)` into the real C1
corpus file and asserts it is caught, and one that asserts the pristine original
— which *imports* the library rather than containing it — is not.

### Reporting consequence

C1 has now been measured four times, and only two of those measurements are
valid:

| round | result | status |
|---|---|---|
| v1 | 3/3 "migrated" | **invalid** — oracle lacked the timing invariants |
| v2 | 0/3 | valid |
| v3 | 3/3 "CLEAN" | **invalid** — vendored copy, caught by provenance only |
| v4 | not yet run | — |

The honest summary for the paper is that C1 has not yet been migrated
successfully by the pipeline under a complete oracle, and that each apparent
success so far was an artefact of an incomplete check. That is a defensible
negative result about a genuinely hard subject; the alternative — quoting v1 or
v3 — would not survive scrutiny.

## Track 1 — v4: the guards work, and the judge found a fourth gap

v4 is the first round where every mechanism behaved as designed.

| run | attempts | attempt-1 rejection | attempt-2 rejection | overlap | MD5 |
|---|---|---|---|---|---|
| 1 | 2 | **76% verbatim** (provenance guard) | — | 23.8% | `1dcdbd8c` |
| 2 | 3 | `_integral` AttributeError | **derivative on error** | 37.7% | `d84a200e` |
| 3 | 2 | `TypeError: float - NoneType` | — | 33.1% | `33f42f1b` |

Three distinct MD5s, no `simple_pid` import anywhere, overlap 24-38% against a
60% threshold. Cost $0.22.

Two things are worth separating out.

**The provenance guard was necessary independently of the mistake that prompted
it.** Run 1's first attempt came back at 76% verbatim *with no library source in
the prompt* — the model reproduced `simple_pid` from memory. The guard would have
been needed even if the reference-source experiment had never happened.

**The repair loop is genuinely incremental now.** Run 2's two rejections were
*different* (constructor ordering, then derivative basis) and it fixed each in
turn. Compare v2 run 1, which hit the identical `_integral` error three times and
gave up: that was the restart-from-original bug, and this is what fixing it looks
like.

### The judge's fourth gap — the zero-elapsed-time guard

With all three invariants passing and the oracle clean, the judge still found
something real in run 1: simple-pid computes
`dt = now - self._last_time if (now - self._last_time) else 1e-16`, and the
`else 1e-16` is load-bearing. Run 1 dropped it, so a clock that returns the same
value twice divides the derivative by zero. Confirmed: the real library returns
`12.0` under a frozen clock, run 1 raises `ZeroDivisionError`. Its `_prev_time`
is a float seeded in the constructor, so this needs no injected clock — a coarse
platform timer reaches it.

The judge also found run 1 accepts `auto_mode` and ignores it: with
`auto_mode=False` the real library freezes its output, run 1 keeps computing
(10.13, 8.18). Out of program surface — nothing in C1 references `auto_mode` —
so it is recorded as a fidelity gap, not gated on, consistent with the same
ruling made for `reset`, `tunings` and `components`.

### A correction I have to make explicitly

I reported that **all three** v4 clones diverged on the zero-dt path. That was
wrong, and the error was in my probe, not the clones.

Runs 2 and 3 initialise `_last_time = None` and special-case the first call. I
forced a float into `_last_time` by hand, producing a state their code never
creates itself, and then read the resulting nonsense as a defect: run 2's "100.0"
was an integral computed against a `_last_time` far in the past, and run 3's
crash came from the same fabricated state. Their real behaviour is correct — the
first call yields `Kp*error`, and every later call hits the sample_time hold and
returns the cached output before any division, so the zero-delta path is
unreachable for them.

This is the second time this session that a negative control of mine manufactured
the failure it claimed to find. `test_c1_accepts_a_none_initialised_previous_time`
now pins the legitimate pattern so it cannot be failed again.

### Invariant 4

Added to `_inv_c1`. It tries three hooks together, because rewrites differ in
what they expose and no single hook reaches all of them: patch `time.monotonic`
(catches call-time lookup), set the instance's `time_fn` when present (catches
`from time import monotonic as _now`, which ignores the module attribute), and
pin private float attributes mentioning time, excluding the public `sample_time`.

It deliberately does **not** fail when no hook bites. An earlier draft failed any
engine lacking `time_fn`, which rejected runs 2 and 3 for missing library surface
the program never uses — exactly the over-strictness this file is supposed to
avoid. An unreachable property is left unverified rather than failed.

Verified: pristine original passes; v4 run 1 rejected naming the ZeroDivisionError;
v4 runs 2 and 3 pass; all six earlier clones keep their previous verdicts.

### C1 final standing

| round | oracle then | oracle now (4 invariants) | valid |
|---|---|---|---|
| v1 | 3/3 | 1/3 | no — oracle incomplete |
| v2 | 3/3 | 0/3 | yes (reported as 0/3) |
| v3 | 3/3 | — | no — vendored copy |
| v4 | 3/3 | **2/3** | **yes** |

**C1 migrates 2 of 3 under the complete oracle.** Run 1 is rejected for a real,
reachable crash; runs 2 and 3 reproduce all four behaviours and were re-verified
outside the pipeline.

The honest framing for the paper is that C1 is solvable but was not solved until
the oracle had four invariants, three of which were added only after an
independent reviewer found the gap. Two of the four apparent successes along the
way were artefacts of incomplete checking. That sequence is the most interesting
result this subject produced, and it is a finding about evaluation methodology
rather than about the pipeline.

### Judge reliability, recorded

Of the three v4 verdicts, one was substantive (run 1), one was self-refuting
(run 2 was labelled MAJOR while its own finding text concluded "so no defect
here" and its summary said "I found no concrete behavioural divergence worth
blocking on"), and one was unparseable and internally contradictory (run 3's
reply said "derivative-on-measurement instead of simple-pid's
derivative-on-measurement"). The judge is a useful lead generator and an
unreliable scorer; every finding acted on this session was verified against the
installed library first, and two of them did not survive that check.

## Track 1 — how confident the C1 result actually is

"2/3 under the complete oracle" is a statement about four hand-written
invariants, and every one of them was added after someone noticed a specific
behaviour. To bound the residual risk empirically rather than by assertion, the
three v4 clones were fuzzed differentially against the installed library:
random gains, random inputs, a moving setpoint, and varied dt, compared
trace-for-trace. Explicit dt removes the clock, so it is deterministic.

**400 parameter sets x 8 steps: all three clones produced traces identical to
`simple_pid`, to six decimal places.** Run 1 included — its arithmetic is
faithful; its defects are on the clock and mode paths, not in the computation.

Ten edge paths were then compared directly:

| edge case | simple_pid | run1 | run2 | run3 |
|---|---|---|---|---|
| explicit dt=0 | ValueError | ValueError | ValueError | ValueError |
| explicit dt=-1 | ValueError | ValueError | ValueError | ValueError |
| dt=1e9 | 100.0 | 100.0 | 100.0 | 100.0 |
| dt=1e-18 | 10.0 | 10.0 | 10.0 | 10.0 |
| `auto_mode=False` holds | 12.3 | **0.0** | 12.3 | 12.3 |
| auto_mode re-enabled | 2.05 | 2.05 | 2.05 | 2.05 |
| output_limits changed mid-run | 10.0 | 10.0 | 10.0 | 10.0 |
| `reset()` then call | 0.0 | 0.0 | 0.0 | 0.0 |
| `tunings` | (2.0,0.5,0.5) | same | same | same |
| `components` | (0, 0.0, 0) | (0.0,0.0,0.0) | (0,0,0) | (0,0,0) |

Eight of ten identical across all three. The `auto_mode` row is run 1's known
defect, now confirmed with explicit dt so the sample_time hold cannot be masking
it. The `components` row is an int-vs-float tuple nit — the real library itself
returns a mixed `(0, 0.0, 0)` — and is not a behavioural difference.

So the defensible claim is narrower and stronger than "the oracle passed":
**runs 2 and 3 are indistinguishable from `simple_pid` across 3,200 generated
steps and every edge path tested; run 1 is faithful arithmetically and wrong on
two clock/mode paths, and is correctly rejected.** What remains unquantified is
one subject at n=3, and the fuzz covers the engine surface rather than the whole
program.

The fuzz is now a permanent test
(`test_the_positive_control_matches_the_real_library_under_fuzzing`), run against
the positive control. It is the check that would have caught
derivative-on-error, the first-call dt and the sample_time hold with nobody
having to think of them — which is exactly the failure mode of the previous four
rounds.

## The import-root bug, fixed before C2

`resolve_import_root("python-statemachine", ...)` returned `python_statemachine`.
Every spelling the function tried was a transformation of the distribution name,
and `python-statemachine` imports as `statemachine` — a root that shares no
spelling with its name, so all of them failed and the `replace("-", "_")`
fallback stood.

The consequence was not cosmetic. `finalize_migration` keys its post-condition on
this value, so `remaining_source_imports(code, "python_statemachine")` would have
found nothing in a file that still did `from statemachine import StateMachine` --
and the check that exists to reject an unfinished migration would have **passed an
unmigrated C2 clone**.

Fixed by consulting `importlib.metadata.packages_distributions()` first, which is
the authoritative distribution-to-import-root map, and by also trying trailing
hyphen segments (only the leading one was tried). Metadata outranks a string
guess even when no import in the file matches.

Verified across the cases that previously broke in both directions:

| distribution | resolves to |
|---|---|
| python-statemachine | `statemachine` |
| paho-mqtt | `paho` |
| simple-pid | `simple_pid` |
| requests | `requests` |
| not-installed-xyz | `not_installed_xyz` (unchanged fallback) |

Five tests, including one that drives the real consequence end to end: a live
`from statemachine import StateMachine` is now reported as a survivor.

## Track 1 — C2, and a correction about which program the import bug affected

### Correction

C2's outgoing library is **`transitions`**, not `python-statemachine`. I said the
`resolve_import_root` bug would let an unmigrated C2 clone through; that was the
wrong program. `transitions` imports as `transitions`, so no hyphen resolution is
involved. The fix remains a real fix — the bug is latent for any distribution
whose import root is not a transformation of its name — but it was never C2's
blocker.

### C2 v1: 3/3 by the oracle, 0/3 by differential fuzzing

Provenance clean (0.0% overlap, which is legitimate: a hand-rolled FSM shares
nothing with `transitions`' generic framework), three distinct MD5s, all four
oracle layers passing on all three clones. Then 300 random event/step scripts
against the real library:

```
run1: 9/300 identical    run2: 9/300    run3: 9/300
```

`transitions` raises `MachineError` when an event fires from a state with no
matching transition (`Can't trigger event empty from state IDLE!`). All three
clones silently ignored it. Discounting invalid-transition errors on both sides:

```
run1: 300/300    run2: 300/300    run3: 300/300    ALL MATCH
```

So it was the **only** behavioural gap — every state and valve output correct
across 3,000 actions. A much cleaner failure than C1's scattered infidelity.

**Scope settled by the corpus, not by judgement**: the program's own test suite
calls these triggers directly (`c.start_fill()`, `c.start_drain()` in
`test_fsm.py`), so silently accepting an invalid command in a tank safety
controller is in scope. Contrast C1's `auto_mode`, which nothing in that program
references.

Invariant added to `_inv_c2`. It does not check the exception TYPE: a first-party
FSM cannot raise `transitions.MachineError` without importing the library it was
told to remove, so requiring that would be unsatisfiable. What it requires is
that the call be refused rather than silently accepted.

The existing C2 positive control had the same defect and was fixed — the third
unfaithful positive control this session.

### C2 v2: 3/3, verified

All three runs were rejected on attempt 1 naming the invariant, and repaired on
attempt 2.

| run | attempts | MD5 | overlap | oracle | fuzz |
|---|---|---|---|---|---|
| 1 | 2 | `293c6524` | 0.0% | pass | **300/300** |
| 2 | 2 | `3aeac082` | 0.0% | pass | **300/300** |
| 3 | 2 | `cd1a100b` | 0.0% | pass | **300/300** |

Cost $0.82 for three runs. The fuzz now compares refusal behaviour as well as
state and outputs, and all three match the original action-for-action across
3,000 actions.

Surfaces the fuzz does not reach were compared directly:

| property | original | run1 | run2 | run3 |
|---|---|---|---|---|
| `.machine` attribute | yes | yes | yes | yes |
| `state` type | str | str | str | str |
| `STATES` | 5 states | same | same | same |
| guard blocks fill | FILLING | FILLING | FILLING | FILLING |
| `trip` from FAULT | FAULT | FAULT | FAULT | FAULT |
| `is_IDLE()` | present | absent | present | absent |
| `may_start_fill()` | present | absent | absent | absent |

The last two are convenience methods `transitions` auto-injects. The program and
its tests never use them — `test_fsm.py` touches only `inlet`, `outlet`, `state`,
`step`, `start_fill`, `start_drain` — so they are recorded as fidelity gaps, not
gated on, consistent with the `auto_mode` ruling.

The differential fuzz is now a permanent test for C2 as well.

### Current standing

| subject | result | basis |
|---|---|---|
| C1 | **2/3** | 4 invariants + 400-case fuzz + 10 edge paths; run 1 fails zero-dt and `auto_mode` |
| C2 | **3/3** | 1 new invariant + 300-script fuzz + 7 surface checks; all three match |

### The methodological point

C2's only real gap was found in **one** differential fuzz run. C1 needed four
rounds, two independent reviewers and three added invariants to reach comparable
confidence — and two of its four apparent successes along the way were artefacts
of incomplete checking.

Fuzzing the rewrite against the dependency it replaced is the strongest tool in
this evaluation and it costs nothing per run. The right order of work for any
future reimplement subject is: write the differential fuzz first, then trust a
number. Adding invariants reactively after a reviewer finds something is how C1
consumed four rounds.

### C2 has NOT been judged — an open caveat

The judge pass on C2 was attempted and failed on API credit exhaustion, for both
v1 and v2. So every check behind C2's 3/3 is one I wrote: the oracle, the
differential fuzz, the surface comparisons, the provenance check.

That matters because of what happened on C1. My three invariants cleared C1's v2
clones; the judge then found the derivative-basis gap, which was real and which
none of my probes could see. The judge has a worse signal-to-noise ratio than the
fuzz — of its last three verdicts, one was substantive, one self-refuting and one
unparseable — but it has twice found a class of defect no check of mine covered.

**C2 must be reported as "3/3, unjudged" until that pass completes.** Two
commands, about $0.05 each:

```
# v2 — the result being claimed
python3 judge_clones.py --csv $SP/c2_v2_judge.csv --corpus ~/Downloads/evaluation \
  --clones $SP/c2_v2_clones --cache $SP/c2_v2_judge_cache.jsonl

# v1 — calibration: can the judge find the invalid-transition gap the fuzz found?
python3 judge_clones.py --csv $SP/c2_v1_judge.csv --corpus ~/Downloads/evaluation \
  --clones $SP/c2_v1_clones --cache $SP/c2_v1_judge_cache.jsonl
```

The v1 run is worth the money independently of C2: its clones have exactly one
known defect, precisely characterised by fuzzing, so judging them measures the
judge itself. If it flags the invalid-transition gap, that is evidence the judge
generalises. If it returns CLEAN on all three, then the judge cannot see this
class of defect and the fuzz is doing the real work — which changes how much
weight its verdicts should carry elsewhere in the evaluation, including on the
four-arm results already reported.

## Track 1 — the C2 judge pass, and an invariant that induced its own defect

### C2 v2 is 1/3, not 3/3

The judge scored the FIXED v2 clones **worse** than the DEFECTIVE v1 clones
(MAJOR x3 against MINOR/MINOR/MAJOR). That inversion was real, not noise: my
invariant had introduced a new defect.

`transitions` distinguishes two refusals, and a faithful FSM must too:

| situation | `transitions` |
|---|---|
| no transition leaves the current state for this event | raises `MachineError` |
| a transition matches but its `conditions` guard is False | **returns `False`** |

My invariant checked only the first. Two of three rewrites over-generalised it
and raised on guard failure as well. Confirmed directly:

| | guard fails (valid source) | invalid source |
|---|---|---|
| original | returns `False` | `MachineError` |
| v2 run1 | returns `False` | `MachineError` |
| v2 run2 | **raises** | `MachineError` |
| v2 run3 | **raises** | `MachineError` |
| v1 (all three) | returns `False` | **returns `False`** |

So C2 stands at **1/3**. v1's clones were wrong in one direction, v2's in the
other, and only v2 run 1 gets both halves right.

### Why the fuzz could not find this

The guard-failure path is unreachable through the program's own control flow:
`_level` changes only inside `step()`, and any out-of-band level trips to FAULT
before `start_fill` can fire from IDLE. Reaching it requires setting `_level`
directly. A differential fuzz driving the public API therefore cannot get there,
which is a real limit on the technique and worth stating plainly after the last
entry in this file recommended it so strongly. The fuzz found C2's first gap in
one shot; it could not have found the second.

### Fix

`_inv_c2` now checks both halves: an invalid source must raise, and a failed
guard must return falsy **without** raising and **without** moving the state.
Verified — pristine original passes; v2 runs 2 and 3 rejected naming the guard;
v1 all three rejected naming the invalid source; v2 run 1 passes. `golden.json`
byte-identical. Two new tests, one per direction, plus one for a guard that
returns False but moves the state anyway.

The exception TYPE is still not checked. A first-party FSM cannot raise
`transitions.MachineError` without importing the library it was told to remove,
so requiring it would be unsatisfiable. The judge correctly noted that callers
catching the library's exception break; that is an unavoidable consequence of
removing the dependency, recorded rather than gated on.

### What the calibration experiment settled about the judge

Judging the v1 clones, whose single defect was already precisely characterised by
fuzzing, was meant to measure the judge. It did:

* **Recall is partial but real.** It found the invalid-transition gap on 2 of 3
  v1 clones (run 2 finding 2, run 3's MAJOR) and missed it entirely on run 1,
  whose only finding was self-refuting text ending "no defect here".
* **Severity labels are unreliable.** v1 got MINOR/MINOR/MAJOR and v2 got
  MAJOR/MAJOR/MAJOR, the opposite order from the actual severity.
* **The findings themselves are valuable.** It found a defect in v2 that no check
  of mine could reach, having already found C1's derivative-basis gap the same
  way. That is twice now.

Operating rule, which this session's record supports: treat the judge as a lead
generator whose every finding is verified against the installed library before
being acted on, and never read its verdict counts as a score. Of roughly a dozen
findings acted on this session, three were substantive and changed the result,
and several did not survive verification.

A consequence for the already-reported four-arm results: their CLEAN/MAJOR counts
carry the same severity noise measured here, so the defect-rate comparison
between arms should be read as indicative and not as a measurement. The pairwise
p-values above 0.75 were already consistent with that.

## The judge is not hallucinating — its rubric was unsatisfiable

The question was whether the judge simply reports failure on everything. Tested
three ways, and the answer is no, but something worse than bias was happening.

### Evidence it is accurate

**1. It returns CLEAN when the code is genuinely clean.** 4 of 17 verdicts were
CLEAN, and all four were on behaviourally identical code: the three vendored
copies (which *were* `simple_pid`) and one no-op. Those were the cases with the
most unfamiliar surface to invent a finding about, and it invented none.

**2. A deterministic check with no LLM in it agreed in all six rounds.** The
differential fuzz is plain Python comparing traces against the installed library:

| round | judge | independent fuzz / trajectory check | agree |
|---|---|---|---|
| C1 v1 | MAJOR x3 | all 3 diverge (hold, first-call dt) | yes |
| C1 v2 | CLEAN, MAJOR x2 | run1 no-op; 2-3 diverge on derivative | yes |
| C1 v3 | CLEAN x3 | 73% verbatim, so behaviour IS identical | yes |
| C1 v4 | NO-OP, MAJOR x2 | run1 crashes on frozen clock; else 400/400 | yes |
| C2 v1 | MINOR, MINOR, MAJOR | 291/300 scripts diverge | yes |
| C2 v2 | MAJOR x3 | runs 2-3 raise where the library returns False | yes |

**3. Seven distinct findings verified real** against the installed library: the
sample_time hold, first-call dt, derivative-on-measurement, the zero-dt guard,
`auto_mode`, invalid transitions, the guard over-correction. Two did not survive
verification: run 3 returning `None` (0 of 200 trials) and one self-refuting
finding whose own text ended "no defect here".

### The real problem — a structurally unachievable CLEAN

The placebo test was decisive. A hand-rolled FSM verified three ways (corrected
oracle, both halves of the refusal behaviour, 300/300 fuzz scripts) was judged:

```
verdict : MAJOR
summary : Behaviour is otherwise faithful (guards, valve commands, step logic,
          and the wildcard trip transition all match), but the invalid-trigger
          exception type changed from transitions' MachineError to RuntimeError.
```

One finding, factually correct, everything else recognised as faithful. The judge
read the code properly. But that finding is **unavoidable**: first-party code
cannot raise `transitions.core.MachineError` without importing the package it was
told to delete.

So for a reimplement subject:

* a correct reimplementation **always** scores MAJOR;
* the only route to CLEAN is to **vendor the library**, which v3 did and which
  scored CLEAN x3.

The rubric ranked the correct answer below the one behaviour the provenance guard
exists to forbid, and the CLEAN rate for C1 and C2 was capped at zero before any
code was written. That is a specification defect in my prompt, not a model
failure.

### Fix

`REIMPLEMENT_NOTE` is appended to the prompt for reimplement subjects only. It
states that the dependency's own exception classes cannot be raised and that a
different type at the same point with the same control-flow effect is correct;
that what matters is WHETHER and WHEN an error is raised, so a swallowed error and
a newly-raised error both remain defects; that no longer importing the dependency
is the point rather than a finding; and that reproducing the library verbatim is
itself reportable.

Five tests pin it: the note reaches every reimplement subject, is absent from the
nine substitution subjects (where a changed exception type IS a defect and the
rubric must not be weakened), still treats swallowed and newly-raised errors as
defects, asks the judge to flag vendoring, and never leaks arm identity.

### Consequences for what has already been reported

* **Every C1 and C2 verdict in this session predates the fix.** Their MAJOR
  labels include an unavoidable finding. The underlying defects were independently
  confirmed by fuzzing, so the *findings* stand; the *verdicts* are inflated.
* **The four-arm results are not affected the same way.** Nine of twelve subjects
  are substitution cases, where the exception-type rule is correct. But their
  CLEAN/MAJOR counts still carry the severity noise measured here, so the
  cross-arm defect-rate comparison should be described as indicative rather than
  as a measurement — consistent with the pairwise p-values above 0.75.
* **The placebo re-run is still outstanding** (credits). It is one call and it is
  the confirmation that the fix works: the same clone, same hash, fresh cache,
  expected to come back CLEAN or with only substantive findings.

### Operating rule

Treat the judge as a high-precision lead generator with unreliable severity
labels and roughly two-thirds recall. Verify every finding against the installed
library before acting. Never read its verdict counts as a score. And test the
rubric against a known-good input before trusting any of its output — which is
what this section exists to record, and what should have been done first.

## Verdict on the judge, after four controlled tests

The question was whether the judge is hallucinating and simply reporting failure
on everything. It is not. The problems were elsewhere, and two of them were mine.

### It is accurate on substance — four for four

| test | finding | real? |
|---|---|---|
| vendored clones (x3) | none — CLEAN | correct: the code WAS the library |
| placebo, old prompt | exception type changed | yes, but unavoidable |
| placebo, new prompt | `.machine` / `is_<state>` missing | **yes — a hole in MY control** |
| real v2 run1 clone | none (summary: "no concrete divergence") | correct |

It has never fabricated a defect in this session. Seven substantive findings were
verified real against the installed library; two claims did not survive. A
deterministic non-LLM check (differential fuzzing) agreed with it in all six
rounds.

Notably, the third test found a defect in the control I had called "verified
three ways". The oracle, the refusal-behaviour checks and the 300-script fuzz all
ignore attributes the library injects onto the model, so none of them could see
that my placebo lacked `.machine`. A control verified only on the axes you already
thought of is not a verified control.

### Problem 1 — the rubric was unsatisfiable for reimplement subjects

Fixed, and documented above under "The judge is not hallucinating". First-party
code cannot raise the removed library's exception classes, so a correct
reimplementation always scored MAJOR while a vendored copy scored CLEAN. The
`REIMPLEMENT_NOTE` removes that, and the exception-type finding did disappear on
re-test.

### Problem 2 — retracted findings were setting verdicts

The model uses the `problem` field as a scratchpad: it explores a possibility,
concludes the behaviour matches, and returns the entry anyway with a severity
attached. `verdict_of` then takes the worst severity present.

Measured across all 20 verdicts collected: 2 rows had every finding retracted by
its own text, including the real v2 run1 clone whose summary read "no concrete
divergence was found" and whose verdict was MAJOR.

Fixed with a `NO-VERDICT` outcome, deliberately **not** CLEAN: the judge
contradicted itself, which is a different fact from "the code is faithful". Like
`NO-OP` and `VENDORED` it is excluded from `clean/attempted`. Because this is a
keyword heuristic over model prose, it is biased to lose a finding rather than
invent a pass -- a misfire costs a MAJOR, never a false CLEAN.

The rule requires the withdrawal to be the last thing said, with no defect claim
after it. A trailing-window check alone was too loose: it flagged "this matches
the original on the first call, but every later call raises ZeroDivisionError",
which is a real finding that merely opens with a match. With the tightened rule,
2 of 20 rows reclassify and 18 keep their verdicts -- including one rambling
finding that does assert a real defect and is correctly left alone.

A prompt instruction against scratchpad reasoning was added too, but it did not
take on the call that followed it, which is why the mechanical fix exists.

### Known remaining weakness: severity

Still uncalibrated, and not fixable from here. C2 v1 (defective) scored
MINOR/MINOR/MAJOR while C2 v2 (better) scored MAJOR x3. Convenience attributes
the program never uses were labelled MAJOR. **Verdict counts must not be read as
a score.** The findings are leads; the fuzz and the oracle are the measurements.

### Net effect on reported results

* **C2 v2 run1 is confirmed good**: oracle pass, 300/300 differential fuzz, full
  surface match, and the judge's own summary agrees. C2 stands at 1/3.
* **C1 and C2 verdicts collected before these fixes are inflated**, though their
  underlying findings were independently confirmed.
* **The four-arm results** are unaffected by the rubric bug for their nine
  substitution subjects, but carry the same severity noise. Their cross-arm
  defect-rate comparison should be reported as indicative, not as a measurement.

## C2 v3 — 3/3, agreed by three independent checks

All three runs were rejected once on the invalid-source half and repaired on
attempt 2. None over-corrected into raising on guard failure, which is what sank
v2. The both-halves invariant held.

| run | attempts | MD5 | overlap | oracle | fuzz | guard path | invalid source | judge |
|---|---|---|---|---|---|---|---|---|
| 1 | 2 | `f5f0f804` | 0.0% | pass | **300/300** | returns False | raises | CLEAN |
| 2 | 2 | `3dd3ca86` | 0.0% | pass | **300/300** | returns False | raises | CLEAN |
| 3 | 2 | `9c08ec14` | 0.0% | pass | **300/300** | returns False | raises | CLEAN |

Cost $0.19 for the runs, $0.05 for the judge. The guard path was checked directly
because the fuzz cannot reach it through `step()`.

This is the first CLEAN the judge has produced on a reimplement subject, and the
first time the oracle, a deterministic differential fuzz and the blind reviewer
all agree on the same clones.

### Two more scoring fixes, both found by this round

**The retraction list was incomplete.** A finding ending `"the real divergence is
none... (withdrawn)"` set a MAJOR verdict, because the explicit marker
`"(withdrawn)"` was not in the phrase list. Added, along with
`"divergence is none"`, `"behaviour is identical"` and two others drawn from real
replies.

**NO-VERDICT was the wrong label when the summary agrees.** All three v3
summaries read "no genuine behavioural divergence found" while every finding
retracted itself. Recording that as NO-VERDICT discards a real clean verdict;
recording it as MAJOR asserts a defect the judge already took back. The rule now
is:

* every finding retracted **and** the summary states nothing was found -> CLEAN
* every finding retracted, summary ambiguous or claiming a defect -> NO-VERDICT

CLEAN therefore requires **two independent signals**, which is the point: a
single keyword heuristic over model prose must never be able to manufacture a
pass on its own. Verified across all 23 cached verdicts: 5 reclassify, 18 keep
their verdict, and every substantive finding survives -- including C1 v4 run 2,
whose summary reads clean while its zero-dt finding is real and unretracted.

### A judge recall failure, now recorded honestly

Re-scoring the defective v1 round gives **1/3 CLEAN**, and that CLEAN is on
`c2_v1 run1` -- a clone the fuzz shows diverging on 291 of 300 scripts. The judge
found nothing there and said so. Previously this was recorded as MINOR on the
strength of a retracted finding, which read like partial credit for a miss; it is
now recorded as what it is, a clean verdict that is wrong.

That puts the judge's measured recall on a known defect at **2 of 3**, and is the
clearest argument for why its CLEAN is weak evidence while the differential fuzz
is strong evidence. On C2 v3 they agree, which is why that result is reportable.

### Declined: the `is_<state>` / `to_<state>` helpers

`transitions` metaprograms `is_FILLING`, `to_FAULT` and similar onto the model.
The original has them; the v3 clones mostly do not (run 3 has `is_FILLING`,
inconsistently). No invariant was added, deliberately: `test_fsm.py` touches only
`inlet`, `outlet`, `state`, `step`, `start_fill` and `start_drain`, so gating on
these would require reimplementing the framework's metaprogramming rather than
the behaviour the program uses -- the same ruling as C1's `auto_mode`.

This is a judgement call, not a verified non-issue, and it is the judge's
outstanding finding from the placebo round. It belongs in the limitations section
stated as such.

### Track 1 closed

| subject | result | verified by |
|---|---|---|
| C1 | **2/3** | 4 invariants, 400-case fuzz, 10 edge paths |
| C2 | **3/3** | 2-part invariant, 300-script fuzz, guard path, surface, judge |

C1 and C2 accounted for 6 of `proteus_full`'s 13 missing migrations. Five of those
six are now recovered. The headline CSV still carries the old 23/36: these results
live in scratch CSVs and do not enter `results_FOUR_ARM_FINAL.csv` until the full
arm is re-run.

## Track 3a — probes for A1-A3, and what they immediately found

### The gap

A1-A3 had no probes at all. `oracle_equiv` was hard-coded `"NA"` for category A
in every arm, so `gen_success` -- compiles plus all tests pass -- was the only
signal for a third of the corpus.

The blind spot is NOT the one C1 and C2 have. Those inject fakes into every test,
so the migrated path never executes. The A tests call the real functions, which
call the real library: the path IS exercised. What was missing is input coverage
-- 5, 3 and 4 test cases, all on well-formed input, against three "drop-in"
replacements that are not drop-in.

### Measured divergences, before writing anything

| swap | divergence |
|---|---|
| `zlib` -> `fastcrc` | only `crc32.iso_hdlc` reproduces `zlib.crc32`; `bzip2` agrees on empty and all-ones input and diverges elsewhere |
| `json` -> `ujson` | `"a/b"` serialises as `"a\/b"` |
| `json` -> `orjson` | >64-bit int raises TypeError; non-ASCII emitted raw instead of `\uXXXX`; **NaN and Infinity become `null`**; non-string keys raise |
| `datetime` -> `ciso8601` | accepts lowercase `z` and maps `T24:00:00` to the next midnight, both of which `fromisoformat` rejects |

Everything here is pure computation -- no clock, no network, no randomness -- so
these are exact golden vectors rather than the tolerance-based invariants C1
needed.

### A scope call, made the same way the judge-rubric one should have been

The two `ciso8601` divergences were deliberately NOT treated as defects. ISO 8601
permits a lowercase designator, ISO 8601:2004 explicitly allows `24:00` as
end-of-day, and the corpus names Z-tolerance as the motivation for the swap --
so `fromisoformat` is stricter than the standard, not more correct. Gating on
them would make A3 unmigratable for something that is not a defect, which is
exactly the error the judge rubric made by scoring a correct reimplementation
MAJOR for not raising the removed library's exception class.

What the `rejected` probe key pins instead is the twelve genuinely malformed
inputs (`2026-13-01`, `2026/01/01`, `abc123`, ...). Verified: `fromisoformat` and
`ciso8601` agree on all twelve, so the key discriminates a broken parser without
penalising a more tolerant one.

### Calibration: six controls, three passing and three failing

An oracle that rejects everything satisfies every negative test and measures
nothing, so each subject has a correct migration that must PASS:

| control | verdict |
|---|---|
| A1 + `fastcrc.crc32.iso_hdlc` | **pass** |
| A1 + `fastcrc.crc32.bzip2` | reject (`crc32`, `frames`) |
| A2 + `ujson`, default escaping | reject (`tags`, `quality`, `batch`) |
| A2 + `ujson`, `escape_forward_slashes=False` | **pass** |
| A3 + `ciso8601` | **pass** |
| A3 + `ciso8601`, UTC assumption dropped | reject (`accepted`) |

Plus all three pristine originals passing all four layers, and `golden.json`
regeneration verified idempotent. C and D entries are byte-identical throughout:
this change adds A keys and touches nothing else.

### Wiring

`H.oracle_verdict(program, clone_dir)` runs the oracle against a clone and
returns `(verdict, detail)`, abstaining with `"NA"` where no probes exist (all of
B). Category A had no variant to report on, so the verdict could not come through
the Logic Substitution Agent's report the way C/D's does -- the clone on disk is
checked directly.

Called from `run_axiom_arms.py` **and** `run_single_shot.py`, so A is comparable
across arms rather than measured only inside Proteus. It is applied in
`proteus_no_verif` too: that ablation switches the Verifier Agent off, which is
not a licence to stop measuring equivalence afterwards, and this measurement is
made by the harness rather than by any agent.

### Re-scoring the existing 36 A clones — no API calls

| arm | oracle Y | oracle N | had scored `gen_success=Y` |
|---|---|---|---|
| single_shot | 6 | **3** | 9 |
| single_shot_repair | 6 | **3** | 9 |
| proteus_no_verif | 6 | **3** | 9 |
| proteus_full | **8** | 1 | 8 |

**Ten of 36 A clones that passed their tests are not equivalent, and all ten are
A2.** A1 and A3 are clean in every arm, 18 for 18.

The failure modes differ by which target the model picked:

* `orjson` (both single-shot arms, 3/3 each) -- `TypeError: Integer exceeds
  64-bit range`.
* `ujson` (proteus arms) -- the slash escaping.
* two `proteus_no_verif` clones fail on float handling
  (`unsupported float value for deterministic JSON`), a third distinct mode.

So A2 is close to unmigratable as specified: every JSON accelerator available
diverges from stdlib on some value type, and "drop-in replacement" is simply
false for `json`. That is a finding about the corpus and about the ecosystem, not
about any arm.

`proteus_full` scored 8/9 against 6/9 for both baselines -- the first place in
this evaluation where Proteus beats the single-shot arms on a measured
equivalence criterion rather than on a proxy. It must be reported with the sample
size attached: nine clones, one program, a difference of two rows.

### Still outstanding

Category B has no probes. B1-B3 talk to network services, so golden vectors need
real local servers (`http.server`, `pymodbus.server`, an `amqtt` broker) -- Track
3b, and a larger job than this one. Until then `oracle_equiv` is honestly `NA`
for a quarter of the corpus, and `gen_success` remains the only signal there.

## Track 3b — probes for B1-B3

### The blind spot here is dependency injection, not coverage

All three B programs share one shape: a `_default_*` factory that builds the real
library's client, and a class that takes that client by injection. Every corpus
test injects a fake, so the factory -- the ONLY code the migration rewrites -- is
never executed by anything. That is the same blind spot C1 and C2 had, and the
opposite of category A, whose tests do call the real library.

So the probes come in two halves: deterministic fakes pinning the normalisation,
encoding and error handling around the client, and a **real local HTTP server**
for B1 that executes the factory.

### The live half, for B1

`_serve_fixed_json()` starts a `ThreadingHTTPServer` on 127.0.0.1 on an ephemeral
port, and the probe constructs `ScadaPoller(base_url)` with **no session** --
which runs `_default_session()` and exercises the HTTP library for real. The
recorded vector contains the normalised records and whether a 404 raised; the
port never reaches the output, or the golden vector would change on every run.

Verified: the correct `requests -> httpx` migration **passes including the live
path**, and three separate defects are each caught with a specific reason:

| B1 variant | verdict |
|---|---|
| `requests` -> `httpx` | **pass** |
| `httpx`, `timeout=` dropped | reject (`calls`, `default_timeout`) |
| `httpx`, `raise_for_status()` removed | reject (invariant: HTTP 400 returned a record) |
| `httpx`, quality default changed | reject (`live_records`, `records`) |

Error paths record **whether** a request raised, not the exception class.
`requests` raises `HTTPError` and `httpx` raises `HTTPStatusError`, and a clone
cannot raise the outgoing library's class without importing the library it was
told to replace -- demanding the type would make the subject unmigratable, which
is the mistake the judge rubric made.

### B2 and B3: fakes only, and one corpus defect

| variant | verdict |
|---|---|
| B2 unchanged | **pass** |
| B2 `unit=` dropped | reject (`calls`) |
| B2 `isError()` no longer raises | reject (`read_error`) |
| B2 little-endian float decode | reject (`decode_f32`, `measurement`) |
| B2 16-bit word mask removed | reject (struct.error) |
| B3 unchanged | **pass** |
| B3 `qos` dropped from publish | reject (`client_calls`) |
| B3 extra payload field | reject (4 groups) |
| B3 payload returned as `str` | reject (AttributeError) |

**B2's declared target cannot do the job.** The corpus asks for
`pymodbus.ModbusTcpClient(host, port=502)` -> `minimalmodbus`, and
`minimalmodbus.Instrument(port, slaveaddress, mode='rtu'|'ascii')` speaks Modbus
over a **serial port only** -- there is no TCP entry point, and
`Instrument("127.0.0.1:502", 1)` fails with `SerialException: could not open
port`. A faithful migration to the declared target is impossible. That is a
property of the corpus, not of any arm, and it explains why every B2 clone
adopted some other library (`pyModbusTCP` in the one inspected). It belongs in
the limitations section.

### The gap that remains, stated rather than implied

There is **no live broker for B3 and no live Modbus server for B2.** That matters
specifically: an async MQTT client whose `publish` returns an un-awaited
coroutine publishes *nothing*, while `getattr(info, "rc", 0)` still yields 0 -- a
silent success. The injected-fake probes cannot see it because they inject a
synchronous fake. `test_b3_has_no_live_broker_probe_yet` pins this so the gap is
recorded rather than forgotten.

Relevant context: the B3 clones did not adopt the declared `aiomqtt`. Both
single-shot arms chose `gmqtt`, and several proteus clones still import
`paho.mqtt.client` alongside their new client.

### Re-scoring the existing clones — and an archive integrity problem

Re-scoring revealed that **7 of 144 clone directories no longer match their CSV
row's recorded test results**:

| arm | program | run | CSV | on disk now |
|---|---|---|---|---|
| single_shot_repair | D1_boiler_control_loop | 2 | 3/5 | 5/5 |
| proteus_no_verif | D1_boiler_control_loop | 1 | 5/5 | 3/5 |
| proteus_no_verif | D1_boiler_control_loop | 2 | 5/5 | 3/5 |
| proteus_full | B2_modbus_meter_reader | 2 | 3/3 | 1/3 |
| proteus_full | B3_mqtt_telemetry_bridge | 3 | 3/3 | 0/3 |
| proteus_full | D1_boiler_control_loop | 3 | 5/5 | 3/5 |
| proteus_full | D3_telemetry_gateway | 3 | 3/4 | 2/4 |

`clones/` has been overwritten by later runs for those rows -- the B2 clone on
disk is dated 2026-08-29 while its CSV row is dated 2026-09-02, and
`clones_v2`/`clones_v4_backup` hold a version that passes 3/3. So `clones/` is
not a faithful archive of `results_FOUR_ARM_FINAL.csv`, and any retroactive
re-scoring has to gate on agreement between the two.

Two of the three B clones I first reported as non-equivalent were on that stale
list. With the gate applied:

| category | arm | oracle Y | oracle N | stale, skipped |
|---|---|---|---|---|
| A | single_shot | 6 | 3 | 0 |
| A | single_shot_repair | 6 | 3 | 0 |
| A | proteus_no_verif | 6 | 3 | 0 |
| A | proteus_full | 8 | 1 | 0 |
| B | single_shot | 9 | 0 | 0 |
| B | single_shot_repair | 9 | 0 | 0 |
| B | proteus_no_verif | 8 | 1 | 0 |
| B | proteus_full | 7 | 0 | 2 |

**The B probes found no new defects.** The single N is
`proteus_no_verif B2 run3`, whose CSV row already recorded `tests=1/3,
gen_success=N`. That is an honest negative result and it contrasts with A, where
the same exercise found ten. Notably the B1 live-server probe passed for every
B1 clone in every arm, so those `_default_session()` migrations do work against a
real server -- which no previous check could have established.

### State of the oracle

All twelve programs now have golden vectors and invariants; `A`, `C` and `D`
entries are byte-identical across the B additions, and regeneration is idempotent.
`oracle_equiv` is now recorded for A and B in `run_axiom_arms.py` and
`run_single_shot.py`, so it is no longer `NA` for half the corpus.

## The stale-clone problem, fixed so it cannot recur

### Root cause

All three runners ended with the same block:

```python
os.makedirs(dest, exist_ok=True)
for name in sorted(os.listdir(work)):
    if name.endswith(".py"): shutil.copy(src, dest)
```

A later run into the same `--clone-dir` therefore **overwrote matching filenames,
left non-matching ones in place, and produced a directory that was a MIXTURE of
two runs.** Nothing recorded which run a clone belonged to, so the disagreement
was undetectable: 7 of 144 rows in `results_FOUR_ARM_FINAL.csv` ended up
describing clones that no longer matched what was on disk -- one row dated
2026-09-02 beside files dated 2026-08-29 -- and it surfaced only when the clones
were re-scored weeks later, after two findings had briefly been reported off
stale directories.

### Three changes

**1. One shared `H.save_clone()`**, used by all three runners, replacing three
copies of that block. Three copies is how they drift apart. A test asserts no
runner creates the clone directory itself any more.

**2. An existing clone is never written into.** A non-empty destination is
**renamed aside** to `<dir>.superseded-<timestamp>` before the new one is
written, so the new clone is always complete and never a mixture. The old one is
kept rather than deleted, because it is the only remaining evidence for whatever
CSV row already described it. An EMPTY destination is reused instead -- an
interrupted earlier run leaves exactly that, and aborting a run after the API
spend over an empty directory would be the wrong trade.

**3. A `clone_sha` column**, computed by `save_clone` from the names and contents
of every `.py` in the saved clone, written on the row that describes it. That is
what makes a row and its clone provably the same artefact.

### The verifier

`verify_clones.py --csv <csv> --clones <dir>` checks every row against disk and
exits non-zero on any disagreement, so it can gate a paper table:

```
python3 verify_clones.py --csv results_FOUR_ARM_FINAL.csv --clones clones
```

Rows carrying a `clone_sha` are checked by hash. Rows written before the column
existed -- all 144 of the current ones -- fall back to re-running the program's
tests and comparing the counts, which is weaker (a different clone can
coincidentally pass the same number) but is exactly what caught the original
seven. Run against the current archive it still reports those seven and exits 1.

Nine tests in `tests/test_clone_archive_integrity.py`, including one that drives
`verify_clones.py` end to end: it builds a clone, verifies it clean, appends a
comment to one file, and asserts the script then exits 1.

### A real bug the tests found

The first version used `os.makedirs(destination, exist_ok=False)` after the
rename. An existing but EMPTY destination is not renamed aside, so that raised
`FileExistsError` -- which would have aborted a run after the API spend. Found
because a helper in the new test file wrote files without a `.py` suffix, so
`save_clone` copied nothing and left an empty directory. The faulty helper was
fixed and the empty-destination case is now handled and tested.

## Will a full re-run put proteus_full on top? No.

Asked directly, and the honest answer from the existing data is no.

**Projection 1 -- `migration_success`**, with Track 1's measured C1 (2/3) and
C2 (3/3) folded in:

| arm | now | projected |
|---|---|---|
| single_shot | 32/36 | 32/36 |
| single_shot_repair | 34/36 | **34/36** |
| proteus_no_verif | 24/36 | 29/36 |
| proteus_full | 23/36 | 28/36 |

**Projection 2 -- equivalence-gated** (tests pass AND library swapped AND oracle
not N, with the new A/B probes, on clones that match their CSV row):

| arm | verified |
|---|---|
| single_shot | 29/36 |
| single_shot_repair | **31/36** |
| proteus_no_verif | 21/36 |
| proteus_full | 21/36 |

On both measures `proteus_full` finishes last or tied-last, and under Projection 1
the `proteus_no_verif` ablation edges **above** it, 29 to 28. Recorded arm costs
are $0.79 / $0.96 / $4.79 / $5.94, so a full four-arm re-run is $12.47 and would
buy a better-measured table that still shows the baselines winning.

Where `proteus_full` does lead: the A-category oracle, 8/9 against 6/9 for both
baselines. One category, nine clones, a two-row margin.

The defensible claim from this work is not "Proteus produces better migrations".
It is that four of the five checks this evaluation relies on can be satisfied by
code that does not work -- `tests_passed` (fakes injected), `gen_success` (a
no-op satisfies it), `oracle_equiv` (absent for half the corpus until now), and
the LLM judge (rubric unsatisfiable for reimplement subjects, and blind to
vendoring) -- together with a demonstration of closing each hole and the defects
that then fell out. That claim does not require `proteus_full` to win, and the
existing CSV plus the retroactive re-scoring already supports it.

## Final four-arm table — `results_FOUR_ARM_v2.csv`

144 rows, 59 columns, one run date, $12.52. Written beside the old
`results_FOUR_ARM_FINAL.csv` rather than over it: the two were produced by
different scoring rules and must not be conflated.

`verify_clones.py` passes on all 144 rows by content hash — every row and the
clone it describes are provably the same artefact.

### Scored result, uniform gate

Gate: compiles AND all tests pass AND source changed AND library swapped AND the
equivalence oracle is not violated. Applied identically to all four arms, with
the oracle run directly wherever probes exist rather than read from whichever
column each runner happened to fill.

| arm | result | cost |
|---|---|---|
| proteus_no_verif | 30/36 (83%) | $4.85 |
| proteus_full | 29/36 (81%) | $5.59 |
| single_shot_repair | 28/36 (78%) | $1.25 |
| single_shot | 17/36 (47%) | $0.83 |

Fisher exact, two-tailed: full vs no_verif **p = 1.000**; full vs repair
**p = 1.000**; full vs single_shot **p = 0.0064**; repair vs single_shot
**p = 0.0142**.

### One more bug, found while merging

`run_single_shot_repair.py` called `_save_clone` and **discarded its return
value**, so all 36 of its rows shipped with an empty `clone_sha`. The hash was
computed and thrown away. Fixed, and a test now asserts every runner writes
`row["clone_sha"]` rather than merely computing it -- returning a fingerprint is
not the same as recording one.

Those 36 hashes were backfilled from the clones after the fact, so for that arm
the hash proves the files have not changed **since the merge**, not since the
run. The independent check that covers the gap is the test-count fallback, which
passed on all 36 before backfilling: each clone still scores exactly the
`tests_passed`/`tests_total` its row claims. The other 108 rows carry hashes
recorded at write time.

### Reporting note

Every arm scores lower here than in `results_FOUR_ARM_FINAL.csv` because the
gate is stricter, not because anything regressed. The old table's
`migration_success` was computed with a `library_swapped` that credited clones
still importing the old dependency and a `migrated` test blind to identifier
changes. State the conjunction explicitly in the methods section, or 47% for a
single-shot baseline reads as anomalous rather than as the corrected figure.
