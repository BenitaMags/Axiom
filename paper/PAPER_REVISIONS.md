# Paper revisions — verified against the implementation and the new data

Every claim below was checked against the code path named in parentheses.
Numbers come from `results_FOUR_ARM_FINAL.csv` (144 rows, 4 arms x 12 programs
x 3 seeds, `claude-sonnet-5`, temperature 0.7).

---

## 1. Fig. 1 — the repair loop is drawn wrong

**Verified in** `smart_loader/core/graph.py`:

```python
verifier_edges = {"axiom": "axiom", "end": END}
if enable_connector:
    verifier_edges["connector"] = "connector"
```

and `_after_verifier()` returns only `"axiom"`, `"connector"`, or `"end"`.

So the VCA re-enters **two** stages — the LSA (`axiom`) and the GSA
(`connector`) — and **never the LMA**. The figure currently draws a dashed
repair edge into all three.

There is a second error: the pipeline order in code is
`LSA -> GSA -> LMA -> VCA` (`verifier_entry = "logic_substitution"`, and
`graph.add_edge("logic_substitution", "verifier")`). The LMA sits *between*
the GSA and the VCA, and the repair edges skip back past it. The figure draws
the VCA as spanning all three agents, which reads as if it observes them in
parallel.

Replacement TikZ (drop-in for the figure body):

```latex
\begin{tikzpicture}[
  font=\small,
  box/.style={draw,rounded corners=2pt,minimum height=9mm,align=center,inner sep=3pt},
  agent/.style={box,fill=green!8},
  oracle/.style={box,fill=teal!8},
  io/.style={box,fill=blue!6},
  flow/.style={-{Latex[length=2mm]},thick},
  repair/.style={-{Latex[length=2mm]},dashed,thick,gray!70}]

  \node[io]    (src)   {Source Program\\+ Requirements};
  \node[agent] (lsa) [right=7mm of src]  {\textbf{LSA}\\Library\\Substitution};
  \node[agent] (gsa) [right=7mm of lsa]  {\textbf{GSA}\\Glue\\Synthesis};
  \node[agent] (lma) [right=7mm of gsa]  {\textbf{LMA}\\Logic\\Mutation};
  \node[box,fill=orange!10] (vca) [right=7mm of lma] {\textbf{VCA}\\Verification \&\\Consistency};
  \node[oracle](orc) [right=7mm of vca]  {Equivalence\\Oracle\\(5 layers)};
  \node[io]    (out) [right=7mm of orc]  {Clone\\+ Provenance};

  \draw[flow] (src) -- (lsa);
  \draw[flow] (lsa) -- (gsa);
  \draw[flow] (gsa) -- (lma);
  \draw[flow] (lma) -- (vca);
  \draw[flow] (vca) -- (orc);
  \draw[flow] (orc) -- (out);

  % the ONLY two repair edges the implementation has
  \draw[repair] (vca.north) .. controls +(0,7mm) and +(0,7mm) .. (gsa.north)
        node[midway,above,font=\scriptsize]{repair};
  \draw[repair] (vca.south) .. controls +(0,-8mm) and +(0,-8mm) .. (lsa.south)
        node[midway,below,font=\scriptsize]{re-select target};
\end{tikzpicture}
```

Caption should state the asymmetry, because it is a design decision worth
defending: *the VCA returns a failed clone to the stage that can act on the
evidence — the GSA when an adapter is wrong, the LSA when the chosen library
itself cannot satisfy the interface. The LMA is not a repair target: its
output is gated by the oracle before the VCA ever sees it, so a rejected
mutation is discarded rather than repaired.*

**Observed, 36 runs of full Proteus:** 7 VCA re-entries on 3 runs (A3 run2: 1,
A3 run3: 4, D3 run3: 2). 33 of 36 runs needed no repair at all.

---

## 2. "Execution and sandboxing" — rewritten plainly

The original paragraph is accurate but dense. Verified against
`proteus_harness.stage()` (`tempfile.mkdtemp`, file copy) and `_run_suite()`
(`subprocess.run(..., timeout=120)`).

> **Execution and sandboxing.** Local verification has to run code that a
> language model just wrote, so every candidate clone is executed rather than
> inspected. Each candidate is copied into a fresh temporary directory and run
> there with the host Python interpreter under a wall-clock limit, so a clone
> that hangs is abandoned instead of stalling the batch. We capture four
> things: whether the file compiles, the process exit status, everything
> written to standard output and standard error, and the text of any assertion
> that failed. That output is handed back to the responsible agent **unedited**
> — the agent repairs against the actual traceback, not against a summary of
> it.
>
> The isolation is filesystem-level, not a container: each run gets its own
> directory, but a clone that opened a network connection or wrote outside that
> directory would not be stopped. This is acceptable here because the corpus is
> our own and every generated file is read before it is executed. Applying
> Proteus to untrusted programs would require a container or VM boundary, and
> we state this as a limitation rather than a property of the design.

---

## 3. The fifth oracle layer — what it is, and a correction

**Verified in** `equivalence/factory_check.py`.

The four source-level layers cannot see the code the migration actually
changes. Every corpus program builds its outside-world client in a small
factory (`_default_client`, `_default_smtp`) that runs *only* when the caller
injects nothing — and both the test suite and the oracle's probes inject a
fake. So the substituted library is never constructed by any check.

The fifth layer calls that factory directly against `192.0.2.1`, an address
reserved for documentation (RFC 5737) that is routable-looking but guaranteed
dead, and classifies what comes back:

| verdict | trigger | treated as |
|---|---|---|
| `MISUSE` | `TypeError`, `AttributeError`, `NameError`, `ValueError`; an import of a library that is neither the declared source nor target; or an OS-level "no such file" naming the address we passed as a host | **failure** |
| `UNREACHABLE` | connection refused, timeout, DNS failure | recorded, never failed |
| `OK` | constructed without raising | pass |
| `SKIPPED` | no factory found, or the declared library is not installed here | not scored |

**Correction to the current text:** the paper lists only `TypeError`,
`AttributeError`, `ValueError`. The implementation also flags `NameError`,
invented-dependency imports, and the "host opened as a file" signature — which
is the case that motivated the layer (a single prompt passed a TCP hostname to
`minimalmodbus.Instrument`, whose first parameter is a serial device path; it
scored 5/5 on the suite and passed all four earlier layers).

**Correction to the claim "This layer caught defects on clones that had passed
all four preceding layers":** true, but rarer than the sentence implies. Across
144 runs it returned `MISUSE` **5 times** (1 single_shot_repair, 3
proteus_no_verif, 1 proteus_full) and `SKIPPED` on 98 — most often because the
declared library is not installed on the evaluation machine. State the counts.

---

## 4. "Toward binary-level confirmation" — replace, we did not do this

The current paragraph says the architecture "anticipates reusing our prior
binary clone detector [5] as the global oracle, disassembling the compiled
original and clone, generating emulator execution traces, and prompting an LLM
to decide semantic equivalence." It then concedes the integration "is not
exercised in the results reported here."

No binary detector was run at any point. Replace the paragraph with what was
actually built:

> **What the global oracle actually is.** The global tier is source-level, not
> binary. It is the five-layer checker of Section IV: import resolution and
> confirmed absence of the migrated-away distribution; recorded golden vectors
> replayed at zero numerical tolerance; program invariants swept over ranges;
> the subject's own `pytest` suite; and a direct call into the factory function
> the suite never reaches. All five must pass, and the first to fail names the
> responsible stage.
>
> Extending this to binary-level confirmation — disassembling original and
> clone and comparing execution traces — is a plausible next step and would
> address the corpus's central weakness, that dependency-injected tests never
> execute the migrated code path. We have not implemented it, and no result in
> this paper depends on it.

---

## 5. Missing: the ACI editing session

Not described anywhere, though it is how every glue patch is produced.
**Verified in** `smart_loader/agents/connector_agent.py`
(`ACI_EDIT_SYSTEM`, `_run_aci_wrapper_injection`).

Add to Section IV, after the GSA description:

> **How glue is applied.** The GSA does not emit a rewritten file. It first
> analyses each call site that touches the outgoing library and decides which
> ones need an adapter; it then enters an *agent–computer interface* (ACI)
> editing session over the real file, with `open`, `search_file`, `goto`,
> `scroll`, `edit` and `submit` operations. Each `edit` is checked before it is
> applied and is rejected outright if the replacement would not parse, so the
> model receives the syntax error and retries rather than committing a broken
> file. Working on line ranges in the actual file, rather than regenerating the
> module, keeps the diff small and makes an unrelated rewrite visible.
>
> Two invariants are enforced outside the model. The session may not edit
> import statements — that rewrite is performed deterministically afterwards,
> using the module root resolved from the file's own imports rather than the
> distribution name, because the two differ (`paho-mqtt` is imported as
> `paho`, `simple-pid` as `simple_pid`). And after the session, any call site
> the analysis recorded is repointed at its wrapper by an AST transformation,
> matching the recorded call text exactly and passing arguments through
> unchanged; imports the migration has left unreferenced are then deleted, and
> a patch that still imports the removed library is rejected and returned to
> the GSA with the offending line. We adopted the deterministic path after
> observing the editing session rewrite a call site other than the one the
> analysis had identified.

---

## 6. Page 7 — sentence to delete

Remove, as instructed:

> *"An earlier protocol supplied the corpus's declared target to Proteus while
> the baseline chose freely; that made the comparison asymmetric and, on the
> two negative controls, forced Proteus toward a library that cannot work."*

Keep the preceding sentence ("the replacement library is never named ... Both
must select a target themselves"), which remains true and is the claim that
matters.

---

## 7. Results — new tables

All four arms measured under one corrected harness. **These supersede
Tables II–IV and Fig. 2 entirely.**

### Table II — clone generation

| Configuration | Gen. success | Equiv. conf. (C/D) | Divergence | Repair re-entries | Tokens | Cost |
|---|---|---|---|---|---|---|
| Single-shot LLM (B1) | 35/36 | 17/18 | 0.824 | 0 | 94,762 | $0.79 |
| Single-shot + repair (B1r) | 34/36 | 17/18 | 0.825 | 0 | 118,178 | $0.96 |
| Proteus w/o verification (B2) | 33/36 | 18/18 | 0.705 | 0 | 892,056 | $4.79 |
| Proteus (full) | 33/36 | 16/18 | 0.734 | 7 | 1,163,450 | $5.94 |

### Table IIb — migration success (new, and the one that separates the arms)

Generation success counts a clone that compiles and passes its tests, which an
*unmodified* file also does. Migration success additionally requires that the
old dependency is verifiably gone and the source actually changed.

| Configuration | Migration success |
|---|---|
| Single-shot LLM | **32/36** |
| Single-shot + repair | **34/36** |
| Proteus w/o verification | 24/36 |
| Proteus (full) | 23/36 |

### Table III — blind review

Reviewed by `claude-opus-5` at temperature 0 with all system-identifying
markers removed. NO-OP marks a clone that never migrated; its verdict is
excluded, since a clone that changed nothing is trivially faithful. ERROR rows
returned empty or truncated replies and are excluded.

| Configuration | BROKEN | MAJOR | MINOR | CLEAN | NO-OP | ERROR | Clean rate |
|---|---|---|---|---|---|---|---|
| Single-shot LLM | 2 | 16 | 12 | 2 | 3 | 1 | 2/32 (6%) |
| Single-shot + repair | 0 | 18 | 9 | 7 | 0 | 2 | 7/34 (21%) |
| Proteus w/o verification | 4 | 12 | 7 | 4 | 9 | 0 | 4/27 (15%) |
| Proteus (full) | 3 | 12 | 4 | 6 | 10 | 1 | 6/25 (24%) |

### Table IV — defect rate among each configuration's own accepted clones

| Configuration | Accepted | With serious defects | Defect rate |
|---|---|---|---|
| Single-shot LLM | 31 | 17 | 55% |
| Single-shot + repair | 33 | 17 | 52% |
| Proteus w/o verification | 24 | 13 | 54% |
| Proteus (full) | 22 | 13 | 59% |

**This table no longer supports the paper's current claim.** The previous
version reported 54% / 55% / 33% and concluded that verification buys
precision. Under the corrected harness the four rates are 55%, 52%, 54% and
59% — flat, and full Proteus is nominally the *worst*. The precision claim
must be withdrawn.

### Fifth-layer outcomes

| Configuration | OK | MISUSE | SKIPPED |
|---|---|---|---|
| Single-shot LLM | 11 | 0 | 25 |
| Single-shot + repair | 11 | 1 | 24 |
| Proteus w/o verification | 8 | 3 | 25 |
| Proteus (full) | 11 | 1 | 24 |

---

## 8. Claims that must change

1. **Withdraw:** "full Proteus is the most precise configuration" (33% vs 54%).
   Not reproduced. Defect rates are flat at 52–59%.
2. **Withdraw:** "the ablation performs like the single-shot baseline; whatever
   Proteus contributes is attributable to verification." Migration success is
   24 vs 23 — the verification tier does not separate them.
3. **Keep, now better supported:** test-suite passage is a weak correctness
   signal. Across all four arms, 52–59% of self-accepted migrations carry
   BROKEN or MAJOR defects, every one of them compiling and passing its full
   suite.
4. **New, and the strongest result:** one prompt plus a single verifier-fed
   repair call (B1r) is the best configuration tested — 34/36 migrations, zero
   no-ops, zero BROKEN clones, 21% clean, at $0.96 against $5.94.
5. **New, and required for honesty:** four defects in the evaluation harness
   were suppressing migrations. Correcting them moved full Proteus from 12/36
   to 23/36 migration success with no prompt change. Pre-correction figures
   should not be cited.
