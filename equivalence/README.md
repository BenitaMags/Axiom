# `equivalence/` — the deterministic checks behind `migration_success`

A migration is scored by running code, not by asking a model. This directory
holds the checkers. **The verdict itself does not live here** — it is
`migration_ok()` in [`smart_loader/eval/proteus_harness.py`](../smart_loader/eval/proteus_harness.py),
which calls into these modules and combines their answers.

That split matters when reading the code: `equivalence/` answers *"are these
two programs the same?"*, and the harness answers *"does this row count as a
success?"*

---

## The seven layers

| # | Question it answers | Implemented in | Called from |
|---|---|---|---|
| 1 | Do the recorded golden vectors still produce the same values? | `oracle.py`, `golden.json`, `probes.py` | `proteus_harness.oracle_verdict` |
| 2 | Do the program's stated invariants still hold? | `oracle.py` | `proteus_harness.oracle_verdict` |
| 3 | Does the project's own pytest suite pass? | — (pytest) | `proteus_harness.run_tests` |
| 4 | Does every module still import, with the old dependency gone and the new one actually called? | — (AST) | `proteus_harness.compiles`, `migrated` |
| 6 | Called against the **real** library with nothing injected, does the program's own API work? | `smoke_check.py` | `proteus_harness.smoke_regression` |
| 7 | Did the migration change what the program does when things **fail**? | `error_contract.py` | scored separately into the CSV |

Two more modules support the above:

- **`differential.py`** — runs the original and the patched module on the same
  inputs and compares. The original is ground truth by definition, so this
  needs no recorded vectors.
- **`make_golden.py`** — regenerates `golden.json` by executing the pristine
  corpus. Run it only against unmodified programs; it records whatever it sees.

---

## What `migration_ok()` actually returns

```python
compiles(clone)                      # layer 4a — the clone imports
and total > 0 and passed == total    # layer 3  — the suite passes
and oracle_verdict(...) != "N"       # layers 1+2 — vectors and invariants
and migrated(...)                    # layer 4b — dependency swapped AND called
and smoke_regression(...) != "DEFECT"  # layer 6 — real library, nothing injected
```

All five must hold. `migrated()` is stricter than it sounds: the old
dependency must be gone, the new one imported **and referenced somewhere other
than its own import statement**. A clone that imports `httpx` and still calls
`requests.Session()` compiles, passes its tests, and is not a migration.




---

## ⚠️ The published column is stricter than the code

Two different things are called `migration_success`:

| where | definition |
|---|---|
| a fresh run (`run_axiom_arms.py:706`) | exactly `migration_ok()` — layers 1,2,3,4,6 |
| `results_fin_final.csv` | that **plus layer 7** (`error_contract != DEFECT`) |


---

## Why there are seven and not three

One thread runs through the whole design: **the corpus programs inject their
collaborators, and their own tests inject fakes shaped like the OLD library.**
A clone can therefore be completely broken against the real dependency and
still pass every test it ships with.

- Layers 1–3 all execute through the injection seam, so none of them ever
  touch the migrated call path.
- **Layer 6 exists because of that.** It constructs the subject with nothing
  injected and calls the program's public API against the real library. A D2
  clone passed all five earlier layers and raised `NameError` the first time
  anything constructed it.
- **Layer 7 exists because even layer 6 only checks the happy path.** It asks
  what happens when things fail. A D3 clone wrapped its publish in
  `except Exception: return _PublishResult(1)`, turning a raised
  `ValueError` into a status code the caller discarded — a telemetry gateway
  that used to fail loudly began silently dropping data, with tests green.

Layer 7 is **recorded, never repaired**: it compares handlers against the
original, and a *faithful* adapter that translates an exception into the
status the original returned looks like new swallowing. Driving repair with it
made D1 strictly worse. The same rule now covers bandit `B113` and the
contract-erosion check — see the comments in
[`verifier_agent.py`](../smart_loader/agents/verifier_agent.py).

---

## What these checks deliberately do not decide

They answer *"does it behave the same?"*, never *"is it good code?"*. Style,
structure, naming and length are out of scope by design — the Logic
Substitution Agent is *supposed* to change those, and a check that objected
would fight the pipeline's purpose.

They are also not a proof of equivalence. Layers 1–2 test recorded inputs;
layer 6 tests one successful call per operation; layer 7 compares handler
shapes. A divergence on an input nobody recorded will pass all seven. That gap
is exactly what the blind LLM judge is for — and
[`differential_fuzz.py`](../differential_fuzz.py) at the repository root exists
to test the judge's findings back, by execution.

---

## Running them

```bash
# regenerate golden vectors from the pristine corpus (destructive — see above)
python3 equivalence/make_golden.py --corpus ~/Downloads/evaluation
```

```bash
# score one clone end to end, exactly as the harness does
python3 -c "
import sys; sys.path.insert(0,'.')
from smart_loader.eval import proteus_harness as H
p = {x.name: x for x in H.discover_programs('$HOME/Downloads/evaluation')}['D1_boiler_control_loop']
clone = 'final_v4/proteus_full/D1_boiler_control_loop_run1'
print('oracle   ', H.oracle_verdict(p, clone))
print('layer 6  ', H.smoke_regression(p, clone))
print('migrated ', H.migrated(p.path, clone, p))
print('VERDICT  ', H.migration_ok(p.path, clone, p))
"
```

Every layer is covered by tests in [`tests/`](../tests); the ones naming a
measured defect cite the program and seed it came from.
