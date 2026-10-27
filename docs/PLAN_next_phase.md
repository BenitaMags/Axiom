# Next phase — closing the gap, and making the oracle able to see

## Where the 13 missing migrations actually are

`proteus_full` completes 23 of 36. The shortfall decomposes exactly:

| cause | rows | tractable? |
|---|---|---|
| C1/C2 — first-party reimplementation, no capability | 6 | **yes, Track 1** |
| No-op: AXIOM declined, source untouched (A2 r1, A3 r1, C3 r1, C3 r2) | 4 | **yes, Track 2** |
| Genuine generation failures (A3 r3, D1 r1, D3 r3) | 3 | partly |

Tracks 1 and 2 together are worth **10 rows**: 23 → 33 of 36, against
`single_shot` 32 and `single_shot_repair` 34. That is a credible path to parity
and a plausible path to a narrow lead. Tracks 3 and 4 are measurement work and
will move numbers **down, for every arm**, which is the point of doing them.

---

## Track 1 — First-Party Reimplementation Agent (FRA)

**Problem.** C1 declares `simple-pid -> in-house controller`; C2 declares
`transitions -> hand-rolled FSM`. The LSA selects *among libraries*. When the
requirement is "write it yourself", no agent in the pipeline is asked to, so
the run declines and the file is returned untouched — 6 of 6 runs, both
configurations, every seed.

**Design.** A fifth agent, entered instead of the LSA when the requirement is
reimplementation, and **bypassing the ACI editing session entirely**.

```
if program.reimplement:            # TO: names no installable distribution
    parser -> FRA -> [deterministic post-processing] -> LMA -> VCA
else:
    parser -> LSA -> GSA(ACI) -> [deterministic post-processing] -> LMA -> VCA
```

The ACI session is skipped because there is nothing for it to do. Its job is to
bridge an interface delta, and the FRA is constrained to produce **no delta**:
it implements the outgoing library's used API surface exactly, so
`PID(kp, ki, kd, setpoint=, output_limits=)` and `pid(level)` keep their
signatures and every call site is already correct. Running an LLM editing pass
over a file that needs no edits cannot improve it and can damage it — we have
observed that session rewrite a call site other than the one its own analysis
identified. Skipping it also removes a repair target and the loop that goes
with it.

The **deterministic post-processing is not skipped**, because it is not the
ACI session and it is what actually completes a migration. All four steps still
run, unchanged:

1. `apply_import_replacements` — rewrite `from simple_pid import PID` to
   `from pid_engine import PID`, keyed on the module root resolved from the
   file's own imports;
2. `apply_call_site_rewrites` — a no-op here by construction, but it is the
   safety net if the FRA does drift from the surface;
3. `_drop_unused_source_imports` — delete the outgoing import once nothing
   references it;
4. the post-condition — reject any patch still importing the removed library.

Without step 1 and step 3 the file keeps `from simple_pid import PID`, the
dependency is not removed, and the run scores `lib_swapped=N` however good the
new module is. These live in `connector_agent.py` but none of them is an LLM
call; the cleanest refactor is to lift them into a small
`finalize_migration(source, decision)` helper that both paths invoke.

**API-surface preservation is a hard requirement, not a preference.** It is the
single assumption that makes the ACI session unnecessary. The FRA prompt must
state it the way the migration prompt already does — same module names, same
function and class names, same signatures, same return types — and the existing
suite is the check.

**Repair routing.** Each agent already carries its own local execute-and-repair
loop, so an FRA whose first attempt fails its tests repairs in place. For the
global tier, `verifier_edges` gains `"fra": "fra"` on this path, replacing
`"connector"`: a VCA rejection after the LMA routes back to the FRA, which is
the only agent that can act on it.

**A useful side effect.** C1 and C2 exist to test the LMA, and today it has
almost nothing to work with, because the PID and the FSM live inside a
third-party library. Once the FRA has written them as first-party code, the LMA
has real logic to diversify — which is what the category was designed to
measure in the first place.

The FRA receives, all of which the pipeline already computes:

1. the **used API surface** of the outgoing library — `imp.api_calls` from
   `parser_agent`, e.g. `PID(kp, ki, kd, setpoint=, output_limits=)` and
   `pid(level)`, `pid.setpoint = x`;
2. every **call site**, from `_extract_call_sites`;
3. the subject's **own test suite**, which is the behavioural specification the
   reimplementation must satisfy;
4. the module **docstring**, which states the requirement in prose.

It emits a new first-party module (`pid_engine.py`, `statekit.py`) implementing
exactly that surface, and nothing wider. Call-site rewriting then reuses
`apply_call_site_rewrites` unchanged — the FRA's module is a drop-in for the
same constructor call, so the deterministic path already built for the GSA
applies without modification.

**Acceptance.** Identical to any other migration: the outgoing import must be
gone (post-condition already enforced), the suite must pass, and the oracle
must certify. No new acceptance rule.

**The coupling that makes or breaks this.** C1's probe reads:

```python
c = ctrl.PumpController(setpoint=5.0, ramp=0.5, pid=FakePID(1.0))
```

It injects a fake PID. A first-party PID engine would therefore be certified by
an oracle that never executes it — the exact defect class the blind review
found. **Track 1 is not complete without the C1/C2 half of Track 3.** Ship
them together or the new capability produces unverifiable output.

**Effort.** ~1 day for the agent, ~half a day for routing and the graph edge.
**Risk.** Moderate. The model can write a PID controller — `single_shot` does,
and migrates on those subjects. What it gets wrong is fidelity: the blind
reviewer rated all six such single-shot clones MAJOR. Passing C1/C2 is likely;
passing them *faithfully* depends on Track 3.

---

## Track 2 — Eliminate the residual no-ops

**Problem.** Four runs returned the source byte-identical with the note
`NO-OP: source unchanged; tests pass vacuously`, having spent 6–14 LLM calls.
These are the population I previously hypothesised were caused by the profiler
veto emptying the candidate pool. That hypothesis is **disproven** — the host
profiler marks candidates `neutral_unattempted`, which is not a veto — so the
cause is still unknown and these four rows are now the only instances left to
diagnose.

**Plan.** Instrument rather than guess. Add a `--trace-decisions` flag to
`run_axiom_arms.py` that dumps, per run: the resolver's candidate list, the
scores table, `selection_basis`, and the value of `decision.winner`. Re-run the
four failing (program, seed) pairs — 4 runs, roughly $0.80 — and read the
basis directly. There are only three ways to reach a no-op through
`_after_axiom`, and the basis string distinguishes them:

* `no_alternative_candidate` — the resolver returned only the incumbent;
* `insufficient_comparative_evidence` — `require_substitution` was somehow off;
* `winner == original` with a normal basis — a tie the incumbent won.

Each has a different, small fix. **Do not build a fix before this run.** I have
already built one speculative fix on this population that turned out to be
inert.

**Effort.** Half a day plus $0.80.
**Risk.** Low, and it is the cheapest 4 rows available.

---

## Track 3 — An oracle that executes the substituted path

This is the scientifically important one, and it is the paper's own stated
future work.

**Problem, restated precisely.** It is not that A and B lack probes. It is that
**every** probe injects a fake — `FakePID`, `FakeModbusClient`, `FakeSession`,
`FakeMqttClient`, 13 uses across the file. So on C and D as well, the four
main oracle layers certify a migration whose migrated code they never run.
Layer 5 (factory execution) is the only check that touches it, and it fires on
5 of 144 runs.

### 3a. Pure-computation probes for A1, A2, A3 — easy, do first

These programs have no I/O in the migrated path. Golden vectors are direct:

* **A1** `crc_telemetry` — CRC-32 over a fixed byte-vector set including empty
  input, a single byte, 0xFF runs, and a 64 KB block. Any correct
  `zlib`/`binascii`/`fastcrc`/`crc32c` swap reproduces them bit-exactly; a
  wrong one cannot.
* **A2** `sensor_json_logger` — serialise records exercising key order, float
  formatting (`1.0` vs `1`), non-ASCII, nested containers, and the exception
  type raised on an unserialisable object. This is precisely where the reviewer
  found `orjson` raising `JSONEncodeError` where `json` raised `TypeError`.
* **A3** `iso_ts_parser` — parse valid timestamps with and without timezone and
  fractional seconds, **and assert the exception type and message shape on
  malformed input**, which is the reviewer's recurring `dateutil` finding.

**Effort.** One day for all three. No new infrastructure.

### 3b. Real local servers for B1, B2, B3 — the real fix

Replace the injected fake with a genuine endpoint on `127.0.0.1`, so the
migrated client actually connects:

| subject | server | basis |
|---|---|---|
| B1 `scada_poller` | `http.server` with a fixed JSON handler | works for `requests`, `httpx`, `aiohttp`, `urllib3` alike |
| B2 `modbus_meter_reader` | `pymodbus.server` with a fixed register map | already a dependency; serves any Modbus TCP client |
| B3 `mqtt_telemetry_bridge` | `amqtt` broker, in-process | already installed; speaks MQTT 3.1.1 to any client |

The probe then drives the program end-to-end and records the observed values,
status codes, retry behaviour and error types. **This is what would have caught
B2's discarded `unit` id**: point the server at two slave ids with different
register contents, and a client that ignores `unit` reads the wrong one.

### 3c. Real-dependency probes for C1, C2

Same principle without a network: construct `PumpController` and `TankFSM`
with the **real** engine rather than `FakePID`, and record the closed-loop
trace. Required by Track 1.

**Effort.** 2–3 days for 3b, one day for 3c.
**Risk.** Low technically. High for the numbers: this will fail clones that
currently pass, in every arm. `single_shot` has 16 MAJOR and 2 BROKEN clones
that presently score as successes; many should stop doing so. Expect all four
arms to drop, and expect the ordering to change in ways I cannot predict.

---

## Track 4 — A real clone detector

**Problem.** The paper claims a binary clone detector is used as the global
oracle. It is not, anywhere. Integrating a real detector makes a true claim
available and gives an independent check on the divergence result.

**Recommendation: `copydetect`** (pip-installable, pure Python, winnowing
fingerprints in the Moss tradition). Alternatives considered: **Dolos**
(actively maintained, tree-sitter, but Node/TS — adds a toolchain), **JPlag**
and **SourcererCC** (both JVM, heavier), **NiCad** (TXL, awkward to install).
`copydetect` costs one `pip install` and has a Python API.

**What it can and cannot do — state this in the paper.** A clone detector
measures *similarity*, not *equivalence*. It answers RQ2 (are the clones
structurally divergent enough to resist detection?) and says nothing about
RQ1 (are they correct?). It must not be described as an equivalence oracle.

**Use.** Report, per clone, the detector's similarity against its original,
alongside the existing `struct_composite`. The interesting claim is
falsifiable in a useful direction: if Proteus's clones (0.207 divergence)
score materially lower on detector similarity than the single-shot clones
(0.141), the diversification result is corroborated by an instrument we did
not write. If they do not, the divergence metric is measuring something a real
detector does not care about, and that is worth knowing before a reviewer finds
it.

**Effort.** Half a day.
**Risk.** It may weaken the one axis on which Proteus currently leads.

---

## Sequencing, and an honest projection

1. **Track 2** — half a day, $0.80, 4 rows. Diagnose first, fix second.
2. **Track 1 + 3c** — 2 days, 6 rows, shipped together so the new capability
   is verifiable. Includes lifting the deterministic post-processing out of
   `connector_agent` into a helper both routes call.
3. **Track 3a** — 1 day. Cheap, and directly targets the reviewer's most
   common finding (exception-type drift).
4. **Track 4** — half a day. Replaces a false claim with a true one.
5. **Track 3b** — 2–3 days. The most valuable and the most disruptive.

After 1–2: `proteus_full` plausibly **33/36**, against `single_shot` 32 and
`single_shot_repair` 34. Parity, possibly a narrow lead over the plain
baseline, probably still behind the repair baseline.

After 3: every arm drops, by an amount nobody can predict. That is the correct
outcome of building an instrument that can see defects the previous one could
not — and it is the finding the paper already argues for. It would be worth
re-running `single_shot_repair` under the new oracle before concluding
anything about the ordering.

One caution I want on the record: Tracks 3 and 4 are the ones that make the
evaluation trustworthy, and they are also the ones most likely to make Axiom
look worse. If the goal is a stronger paper, do them. If the goal is a higher
number for Axiom specifically, Tracks 1 and 2 are the honest way to get it and
Tracks 3 and 4 work against it. Those are different objectives and it is worth
being explicit about which one is driving.
