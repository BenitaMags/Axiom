# AXIOM — complete project guide

*Everything built, every file, every result. Written to be read start to finish.*

---

# PART 1 — What this project is, for a five-year-old

Imagine a program is a recipe. This project does two different jobs to recipes.

**Job one: swap an ingredient.** The recipe says "use flour brand A". We want it to say "use flour brand B" instead — and the cake must still taste exactly the same. That is **library migration**.

**Job two: rewrite the recipe in different words.** Same ingredients, same cake, but the instructions look completely different. That is **program isomorphism**, and it matters because if every factory runs the same code, one attack breaks all of them. Different-looking code means an attack tuned to one version doesn't fit the others.

Both jobs share one hard rule: **the result must behave identically.** Anyone can change code. The difficulty is proving you didn't break it.

---

# PART 2 — The two experiments

They are separate, use different data, and answer different questions.

| | **Experiment 1: isomorphism** | **Experiment 2: migration** |
|---|---|---|
| data | 4 ICS modules (`ics_corpus/`) | 12 programs (`~/Downloads/evaluation/`) |
| question | can we rewrite code to a requested size, provably equivalent? | can AXIOM swap libraries better than one LLM prompt? |
| who asked | professor's isomorphism feature | professor's short-paper corpus |
| status | **done, 24/24 verified** | **done, 109 rows across 3 arms** |

---

# PART 3 — Experiment 1: program isomorphism

## The four test programs (`ics_corpus/`)

Industrial control code, each with a deliberately different control-flow shape.

| file | lines | what it is |
|---|---|---|
| `pid_governor.py` | 89 | engine speed controller; nested anti-windup clamp |
| `pump_controller.py` | 96 | water tank; 3-state machine with hysteresis |
| `protective_relay.py` | 134 | circuit breaker; 4-state machine, auto-reclose |
| `sensor_pipeline.py` | 114 | signal conditioning; ring buffer, elif chain |

## How the agent works

Given a program and a requested line count, it runs a **beam search**:

1. **expand** — apply all 17 transformation rules to each candidate
2. **filter** — drop anything that won't parse, then run the survivors through a 4-layer equivalence oracle
3. **select** — keep the best 3, repeat up to 6 rounds

Sorting is *lexicographic*: distance from the requested length first, diversity only as a tie-break. That ordering is what stops the search trading away the size you asked for.

## The 17 rules

**Grow (7):** `expand_aug_assign`, `expand_chained_compare`, `split_return_expression`, `split_boolean_if`, `extract_subexpression`, `ternary_to_if_else`, `for_range_to_while`

**Shrink (4):** `if_else_assign_to_ternary`, `collapse_aug_assign`, `merge_nested_if`, `inline_single_use_return`

**Neutral (2):** `invert_if_else`, `rename_locals`

**LLM-designed (4):** `call_argument_extraction` (reward 0.548), `and_chain_nesting` (0.026), `demorgan_branch_swap` (0.018), `independent_assignment_swap` (0.005)

Six pair as exact inverses, which is how the search moves a program in either direction. LLM candidates are rejected if they produce invalid Python, **alter any constant**, delete a comment, break equivalence, or never apply.

## The 4-layer oracle

1. **golden regression** — bit-exact match against recorded outputs (`TOL = 0.0`)
2. **invariants** — properties that hold for any input, over randomised values
3. **coverage** — proof the 20 probes actually reach every branch
4. **script I/O** — runs `python module.py` and compares output

Layer 4 was added after proving layers 1–3 never execute `if __name__ == "__main__"`: a `raise RuntimeError` there still gave "5 passed, 0 failed".

**The honest limit:** program equivalence is undecidable. The claim is *"equivalent with respect to these four checks"*, not *"proven equivalent"*. Counterexample from your own corpus: changing `pid_governor`'s anti-windup clamp from ±2500 to ±2400 passes all four layers, because the tests never drive the integral that high. That is why a separate **literal-preservation guard** rejects any rule that alters a constant — independent of the oracle.

## The 4 diversity metrics

| metric | measures | ranks? |
|---|---|---|
| `ast_structural` | AST node-type sequence distance, blind to names | **yes** |
| `cfg_shape` | control-flow constructs by nesting depth | no |
| `ncd` | compression distance over bytes | no |
| `du_chain` | define-use edges (dataflow) | no |

Only `ast_structural` ranks, and it is identifier-blind on purpose: a rename-only variant scores exactly **0.0000**, so the cheapest possible transformation cannot inflate the headline.

`ncd` and `du_chain` are rename-*sensitive*, so ranking on them would let `rename_locals` win by doing nothing real. They exist as independent evidence — and correlating them against the ranking signal is how the padding bias was found.

## Results

6 length targets (0.85× to 1.30×) × 4 modules = 24 variants.

| | value |
|---|---|
| equivalence | **24/24 passed all four layers** |
| mean length miss | 8.88 lines |
| within 1 line | 7 of 24 (4 are the "hold the length" cells; on cells asking for a change it's **3 of 20**) |
| variants with real control-flow change | **16 of 24** |

### The finding that mattered

`cfg_shape` was 0.0000 on most winners — the code was being *padded*, not restructured. Measuring per-rule applicability showed **zero of 16 rules could change control flow on `pid_governor` or `protective_relay`**. Both contain `for ... in range(...)`, so `for_range_to_while` was added.

| | before | after |
|---|---|---|
| winners with control-flow change | 7/24 | **16/24** |
| mean `cfg_shape` | 0.035 | **0.126** |
| mean length miss | 10.08 | **8.88** |

### The ablation

Two ranking signals, same everything else:

| ranking | mean \|miss\| | on-target | oracle | cfg of winners |
|---|---|---|---|---|
| `ast_structural` | 8.88 | 29% | 24/24 | 0.1259 |
| `composite` | 8.88 | 29% | 24/24 | 0.1281 |

Identical. **A negative result worth reporting**: the bottleneck was the catalogue, not the selection criterion.

### Known gaps

- only 2 rules can remove lines; `protective_relay` has no shrink path, so shrink targets never land
- length control saturates past about +10%: asking for +30 lines gives what +11 gives

---

# PART 4 — Experiment 2: library migration

## The corpus (12 programs, `~/Downloads/evaluation/`)

| dir | from → to | cat | files | tests |
|---|---|---|---|---|
| A1_crc_telemetry | `zlib` → `fastcrc` | A | 1 | 5 |
| A2_sensor_json_logger | `json` → `ujson` | A | 1 | 3 |
| A3_iso_ts_parser | `datetime` → `ciso8601` | A | 1 | 4 |
| B1_scada_poller | `requests` → `httpx` | B | 1 | 2 |
| B2_modbus_meter_reader | `pymodbus` → `minimalmodbus` | B | 2 | 3 |
| B3_mqtt_telemetry_bridge | `paho-mqtt` → `aiomqtt` | B | 3 | 3 |
| C1_pid_pump_controller | `simple-pid` → in-house | C | 3 | 5 |
| C2_tank_level_fsm | `transitions` → hand-rolled | C | 3 | 5 |
| C3_anomaly_threshold_engine | `smtplib` → `apprise` | C | 4 | 4 |
| D1_boiler_control_loop | `pymodbus` → `minimalmodbus` | D | 3 | 5 |
| D2_pump_station_scheduler | `requests` → `httpx` | D | 3 | 3 |
| D3_telemetry_gateway | `paho-mqtt` → `aiomqtt` | D | 3 | 4 |

**Categories:** A = drop-in swap. B = swap needing glue. C = remove the dependency and reimplement. D = all of the above.

## The three arms

| config | what runs |
|---|---|
| `single_shot` | one LLM prompt, no agents, no verification |
| `proteus_no_verif` | AXIOM's agents, **verifier disabled** |
| `proteus_full` | AXIOM's agents **with** verification |

All three are measured by the *same* code (`proteus_harness.py`) — same test runner, same divergence metric, same success rule. If the baseline were scored differently, any gap between arms could be the scoring rather than the system.

## Results

| config | n | gen_success | LLM calls | repair iters | cost |
|---|---|---|---|---|---|
| `single_shot` | 36 | **31/36 = 86%** | 36 | 0 | $0.94 |
| `proteus_no_verif` | 36 | **32/36 = 89%** | 146 | 0 | $2.74 |
| `proteus_full` | 37 | **28/37 = 76%** | 193 | 9 | $4.49 |

**By category:**

| config | A | B | C | D |
|---|---|---|---|---|
| single_shot | 6/9 | 9/9 | 7/9 | 9/9 |
| proteus_no_verif | 6/9 | 8/9 | 9/9 | 9/9 |
| proteus_full | 6/9 | 6/9 | 9/9 | 7/10 |

**Other columns:**

| config | divergence | lib swapped | hallucinations |
|---|---|---|---|
| single_shot | 0.653 | 36/36 | **2** |
| proteus_no_verif | 0.928 | 19/36 | 0 |
| proteus_full | 0.861 | 22/37 | 1 |

---

# PART 5 — What the results actually mean

## Read this before quoting any number

**`proteus_full` scores lowest on `gen_success` — 76% against the baseline's 86%.** That is the honest headline, and it needs explaining rather than hiding.

The reason is that `gen_success` is defined as *"compiles AND all tests pass"*. On this corpus **the original already passes its own tests**. So:

- a clone that was never modified passes → counts as success
- a migration the verifier **refuses to ship** leaves the original in place → also counts as success in `no_verif`, but `full` records the refusal

`gen_success` therefore cannot tell a successful migration from no migration at all. It is the wrong headline metric for this corpus, and that is a finding about the protocol, not a defect in AXIOM.

## The four results that do mean something

**1. A1 — AXIOM wins outright.**

| | single_shot | no_verif | full |
|---|---|---|---|
| A1 | **0/3** | **3/3** | **3/3** |

Three single-shot models across three vendors (nemotron, llama-3.3-70b, north-mini-code) all wrote `fastcrc.crc32(payload)` — a function that does not exist. AXIOM's connector wrote `fastcrc.crc32.iso_hdlc(payload)`, the correct API, in one pass with zero repair cycles, and documented the masking contract. **This is the clearest demonstration of the architecture's value in the whole dataset.**

**2. The verifier trades success rate for correctness — visibly.**

| | no_verif | full |
|---|---|---|
| B2 | 2/3 | **0/3** |
| D1 | 3/3 | **0/3** |

Both target `minimalmodbus`, which has **no TCP transport at all**. The connector said so explicitly — *"glue code cannot fix this; it requires a transport layer minimalmodbus does not implement"* — and the verifier refused to ship. Without the verifier, a wrapper that treats a hostname as a serial port reaches disk and scores as a pass.

That is the ablation working exactly as intended: **verification costs you 13 percentage points of apparent success and buys you not shipping broken code.**

**3. Single-shot hallucinates; AXIOM largely does not.** 2 hallucinations vs 0 and 1. Small numbers, but consistent with A1.

**4. A3 is a regression and should be reported as one.**

| | single_shot | no_verif | full |
|---|---|---|---|
| A3 | **3/3** | **0/3** | **0/3** |

The `datetime` → `ciso8601` migration works under one prompt and fails under AXIOM in both arms. This is a genuine loss and the most useful thing in the dataset for improving the system.

## Honest limitations

- **C1 and C2 do nothing under AXIOM.** Their requirement is "remove the dependency and reimplement it" — AXIOM has no reimplementation mode, and the agent that would do it needs an equivalence corpus this dataset does not provide. Their 9/9 scores are the originals passing untouched.
- **B2/D1 may be unsatisfiable.** `pymodbus` → `minimalmodbus` crosses a transport boundary. Worth asking whether the pair belongs in the corpus.
- **The verifier ran on degraded evidence.** `bandit` fails with a version incompatibility on every run; the import smoke test cannot see sibling modules on multi-file programs; project tests report "no tests ran". None blocked the pipeline, but `proteus_full`'s verification was weaker than designed.
- **`divergence_score` is nearly blind to pure library swaps.** Replacing `import requests` with `import httpx` changes identifiers, not AST node types. B1 scored exactly 1.0 while swapping correctly. The column is meaningful for C and D, not for A and B.
- **n = 3 seeds.** Enough to see a pattern, not enough for a significance claim.

---

# PART 6 — Every file, and what it does

## Experiment 2 — migration (the short paper)

| file | lines | role |
|---|---|---|
| `run_single_shot.py` | 600 | **Baseline arm.** One LLM prompt, no agents. Category-aware (diversification only for C/D, per the corpus README). Provider-agnostic with per-provider CSVs, rate-limit backoff, wall-clock guard, raw reply logging. |
| `run_axiom_arms.py` | 330 | **Both AXIOM arms.** Drives `run_loader` per program, skips files that don't import the source library, flags no-ops, and switches the verifier off for the ablation. |
| `smart_loader/eval/proteus_harness.py` | 522 | **Shared measurement.** Reads `# FROM:`/`# TO:` headers, stages a throwaway copy, runs the program's pytest suite, computes divergence, classifies failures, detects hallucinations, writes the CSV. Used identically by all three arms. |

## Experiment 1 — isomorphism

| file | lines | role |
|---|---|---|
| `smart_loader/isomorph/spt.py` | 1011 | The 13 hand-written LibCST rules + registry + `normalize()` |
| `smart_loader/isomorph/design.py` | 698 | Auto-SPT generation: LLM designs and implements new rules, then they are validated |
| `smart_loader/isomorph/oracle.py` | 207 | The 4-layer equivalence gate, plus `NullOracle` for the ablation |
| `smart_loader/isomorph/search.py` | 329 | Length-targeted beam search (Auto-SPT Algorithm 1) |
| `smart_loader/isomorph/metrics.py` | 275 | The 4 diversity metrics + composite + registry |
| `smart_loader/isomorph/telemetry.py` | 134 | Per-run JSONL logging |
| `run_experiment.py` | 287 | The 24-cell sweep + ablation row + metric cross-check |
| `run_isomorph_agent.py` | 213 | Runs the agent standalone, writes variants and diffs |
| `generate_spts.py` | 94 | CLI for generating new rules with an LLM |

## Agents changed inside AXIOM

| file | what changed |
|---|---|
| `agents/logic_substitution_agent.py` | new agent (347 lines); the LMA |
| `agents/connector_agent.py` | fixed missing `coerce_llm_text` import (broke every connector call); fixed dotted-import call-site detection (`paho.mqtt.client`) |
| `agents/axiom_agent.py` | added `mandate_target` so a protocol can *require* a substitution instead of AXIOM choosing |
| `core/graph.py` | wired in `logic_substitution`; added `enable_verifier` and `mandate_target` |
| `core/llm_factory.py` | added OpenRouter provider; request timeouts at construction; `max_retries=1` |

## Data and outputs

| path | contents |
|---|---|
| `~/Downloads/evaluation/` | the 12 corpus programs |
| `~/Downloads/evaluation/reporting/results_claude.csv` | **109 result rows, all three arms** |
| `results_claude_ARCHIVE_prefix.csv` | superseded AXIOM rows from before the fixes |
| `results_claude_v1_overspecified.csv` | the first baseline, before the prompt was matched to the spec |
| `ics_corpus/` | the 4 isomorphism modules + oracle + golden vectors |
| `clones/` | every generated clone, by config and run |
| `pipeline_variants/` | isomorphic variants + `all_winner_diffs.txt` |
| `results_17.csv`, `rank_ast.csv`, `rank_comp.csv` | isomorphism sweeps |

---

# PART 7 — Bugs found along the way

Worth keeping: several are AXIOM bugs the experiment exposed, which is itself a result.

| bug | effect | fixed |
|---|---|---|
| `coerce_llm_text` imported inside two other functions | **every** connector call raised `NameError`; 5 wasted repair cycles per program | ✅ |
| dotted imports not matched (`paho.mqtt.client` vs `paho_mqtt`) | B3 and D3 silently no-op | ✅ |
| AXIOM *chooses* rather than obeys | every mandated migration declined; 100% "success" as no-ops | ✅ `mandate_target` |
| pipeline run on files not using the library | ~60% of LLM calls wasted | ✅ |
| oracle never executed `__main__` | 15 lines counted as verified while unchecked | ✅ layer 4 |
| `ast_structural` rewarded padding | winners had zero control-flow change | ✅ `for_range_to_while` |
| verifier's `bandit` version incompatible | static analysis degraded every run | ❌ open |
| import smoke can't see siblings | multi-file verification weakened | ❌ open |

---

# PART 8 — What to do next

1. **Fix A3.** It's the only case where AXIOM is worse than one prompt. Understanding it is worth more than any new feature.
2. **Report `gen_success` beside `lib_swapped_correctly`.** On a corpus where the original passes, "tests pass" cannot mean "migrated".
3. **Ask about B2/D1.** If `pymodbus` → `minimalmodbus` is unsatisfiable, two of twelve programs can never succeed in any arm.
4. **Fix the verifier's environment** — bandit, sibling imports, project tests — so `proteus_full` is judged on full evidence.
5. **Decide what C1/C2 mean.** AXIOM has no reimplementation mode; either build one or report the gap.
