# AXIOM v2 — Architecture & Technical Logic

Reference document generated from a direct read of the codebase (not the README's summary
of itself). Every claim below cites the actual file and, where useful, the line range that
backs it, as of the fixes applied on 2026-07-26 (see §9). Where the code disagrees with
`readme-2.md`'s description, that's called out explicitly rather than silently resolved.

---

## 1. What AXIOM is

`readme-2.md` (lines 7–24) frames AXIOM as a multi-agent system that simulates an OS-level
dynamic loader for Python source instead of binaries: read the dependency manifest, resolve
which library serves the same purpose, measure cost, patch the binary to use the optimal
one. The stated intellectual reference is CodeAugur (Professor Agadakos) — functional
equivalence, not syntactic API compatibility — and `core/graph.py` (lines 8–16) repeats the
same OS-loader-phase → AXIOM-node table inline as a docstring, confirming this is a
load-bearing design frame, not just marketing copy in the README.

One correction to the README's own table: it lists `rules_agent` as the "Deterministic
library checks" node. It is **not** part of the compiled graph. `core/graph.py` lines
357–360 say so explicitly:

> "Deliberately no Rules Agent node here. Host-machine `is_installed` checks made migration
> decisions depend on whatever happened to be installed locally; Profiler/Verifier now
> reproduce and validate dependencies in disposable case environments instead."

`agents/rules_agent.py` and `core/rules_engine.py` (the `@rule` registry, lines 42–118)
still exist and are usable standalone, but `build_graph()` never adds a `"rules"` node or
wires it into any edge. Availability/license/API-surface checks happen implicitly now,
inside `profiler_agent`'s disposable-environment installs and `verifier_agent`'s
environment-completeness checks (§3.4, §3.7) — not as a separate deterministic gate before
the LLM ever runs.

---

## 2. Pipeline topology (`core/graph.py`)

The graph is built by `build_graph()` (lines 332–485) and driven by `run_loader()` /
`resume_loader()` (lines 490–643). Nodes, in execution order for a normal (non-gated) run:

```
parser → security → resolver → candidate_security → profiler → axiom
                                                                   │
                                              ┌────────────────────┴───────────────────┐
                                       needs_connector?                          drop-in / no-op
                                              │                                          │
                                          connector                                  verifier
                                              │                                          │
                                          verifier ◄──────────────────────────────────────┘
                                              │
                              route: "axiom" (retry) | "connector" (retry) | "end"
```

Key conditional edges, each a real Python function (not a prompt):

- `_after_parser` (114–119): no imports or a parse error → `END`.
- `_after_resolver` (122–127): no equivalence groups → `END`.
- `_after_axiom` (129–164): routes to `"connector"` if any decision has
  `requires_connector`, an unclassified/low `api_match` (< 0.95, and a missing score is
  treated as `0.0` — line 154–155, never a silent pass), otherwise to `"verifier"`.
  Comment at 132–136 explains why even drop-in renames still go through `verifier` rather
  than straight to `END`: "a superficial rename can still change runtime behavior."
- `_after_verifier` (233–259): reads the `_verifier_route` string verifier_agent puts
  directly on its return dict, rather than inferring routing from counters — `"axiom"` means
  retry scoring with a candidate excluded, `"connector"` means retry the adapter with
  concrete error feedback, `"proceed"` means done.

### 2.1 Human-in-the-loop mode (`require_approval=True`)

`axiom_agent` (see §3.5) computes decisions but does **not** touch `source_code` — it
returns early at lines 943–957 in `agents/axiom_agent.py` with `patched_code` unchanged and
prints "⏸ Changes computed — awaiting human approval." `_after_axiom_gated` (167–178) then
routes everyone — drop-in and connector-needing alike — through `approval_gate`
(`core/feedback_loop.py`, not fully read for this document but wired at graph.py:363) before
any AST rewrite happens. This is a genuine LangGraph `interrupt()`, backed by a `MemorySaver`
checkpointer with an explicit dataclass allowlist for msgpack serialization (graph.py
453–482) — the process really suspends; `resume_loader()` (618–643) continues from the exact
paused state via `Command(resume=response)`, not a re-run.

---

## 3. Agent-by-agent logic

### 3.1 Parser Agent — `agents/parser_agent.py`

`ImportVisitor` (31–58) and `CallSiteVisitor` (59–103) walk the AST once (`ast.walk`, no
LLM) to build the "Discovered Imports" table you see at the top of every log entry —
module, alias, imported names, and which API calls are actually used. `parser_agent()`
(104+) is the entry node; `_after_parser` ends the run here if there's nothing to analyze.

### 3.2 Security Agent — `agents/security_agent.py`

Two entry points: `security_agent` (741+) scans the file's *existing* imports, and
`candidate_security_agent` (902+) — run later, after `resolver_agent` — scans only the
specific migration targets the resolver proposed (`core/graph.py`'s `candidate_security`
node, line 391). Both query OSV (`_query_osv`, 254–291) and PyPI (`_query_pypi`, 292–329)
in parallel HTTP, no LLM. `_select_compatible_release` (358–404) is where the eval harness's
"target release unresolved" failures ultimately originate (§6) — it has to find a release
of the target package whose wheel/sdist actually claims support for the selected historical
Python version (`_release_accepts_python`, 330–357); if the selected interpreter is very old
and no compatible release metadata exists, this returns nothing to pin.

### 3.3 Resolver Agent — `agents/resolver_agent.py`

`resolver_agent()` (256+) is LLM call #1. `_discover_alternatives` (112+) proposes candidate
packages per import; the LLM then clusters them into `EquivalenceGroup`s — sets of packages
serving the same role, with `pitfalls` (free text) and `used_apis`. `core/constraints.py`'s
`infer_group_flags()` (32–83) then turns that free text into two structured booleans:

- `crypto_required` — true if the used APIs contain crypto tokens (`CRYPTO_API_TOKENS`,
  lines 16–19: sha256, md5, hmac, etc.) **unless** the resolver's own text explicitly says
  the hash use is non-security (`explicit_non_crypto_phrases`, 57–63) — this asymmetry is
  documented in-code (50–56) as a deliberate bug fix: the flag used to fire backwards and
  veto the *fast, correct* non-crypto alternative (xxhash) from a role that never needed
  crypto guarantees.
- `requires_connector` — true if the pitfall text matches `CONNECTOR_PITFALL_MARKERS`
  (21–25: "different api", "returns bytes", "incompatible", etc.), or automatically if
  `crypto_required` and one of the candidates is a non-crypto hash package (78–79).

`filter_crypto_candidates()` (86–91) is the actual veto: when `crypto_required`, it strips
`NON_CRYPTO_HASH_PACKAGES` (`xxhash`, `murmurhash`, `farmhash`, `mmh3` — line 14) from the
candidate list before scoring, falling back to the first candidate only if that would empty
the list entirely.

### 3.4 Profiler Agent — `agents/profiler_agent.py`

The largest agent file (~1,360 lines). For each candidate: creates a disposable venv
(`_create_disposable_venv`, 271–289) or isolated interpreter probe
(`_probe_isolated_package`, 490–513), installs it (`_pip_install`/`_pip_install_many`,
762–813), and measures real import time + memory + any registered runtime-API benchmark
(`_run_runtime_probe`, 1178–1239; `_attach_runtime_benchmarks`, 1240–1328). Nothing here is
estimated — README's claim that "an unavailable speed/memory/runtime probe is omitted from
the score rather than filled with a neutral estimate" is enforced by `_compute_scores`
consuming only measured `BenchmarkResult` fields (see §3.5). `create_verification_environment`
(941–1009) and `create_static_analysis_environment` (1010–1035) are the disposable sandboxes
`verifier_agent` later runs the patched code and static tools inside.

### 3.5 AXIOM Agent (scoring) — `agents/axiom_agent.py`

LLM call #2, plus deterministic scoring math. Weights, lines 116–121:

```python
W_SPEED        = 0.35   # real runtime/import performance evidence
W_MEMORY       = 0.10   # (was 0.15)
W_API_MATCH    = 0.15   # LLM compat
W_SECURITY     = 0.20   # OSV security score
```

These four sum to **0.80**, not 1.0 — availability is not a soft weight in this formula at
all, it's a hard filter applied before scoring even starts. `_compute_scores()` (238–504)
does, in order:

1. Exclude anything `verifier_agent` already vetoed for this role on a prior pass
   (`excluded_by_verifier`, 282–291) — this is exactly the mechanism the Kafka bug abused
   (§9.1): once a candidate lands in `verification_failures[role]`, it never gets a second
   chance in the same run.
2. `filter_crypto_candidates()` (294, from `constraints.py`).
3. Drop anything that hard-failed the profiler's install/import probe
   (`profile_vetoed`, 309–319) — "not merely not installed locally... ineligible for
   selection" (304–308).
4. If everything got vetoed, fall back to keeping the original package rather than ever
   auto-selecting an unreproducible migration (321–327).
5. Score survivors on the four weights above using only real measurements; a winner needs a
   score gap > 0.15 over the runner-up for `HIGH` confidence, otherwise `MEDIUM`; a single
   remaining candidate with no real comparison is `LOW` (per README's documented confidence
   levels, matching what's actually implemented here).

**The rewrite itself is conditionally deferred.** Lines 905–993: if every replacement in
this decision set is a clean drop-in (`api_match >= 0.95` and no `requires_connector`),
`axiom_agent` calls `apply_import_replacements()` (from `core/ast_rewrite.py`) immediately.
If any replacement needs a connector, the import swap is explicitly **not** done here —
line 969–975 prints "Replacements deferred to connector agent (preserving original call
sites for analysis)" and leaves `source_code` untouched, handing connector_agent the
*original* import so it can see real call sites to wrap. This deferred-rewrite design is
exactly what made the Kafka bug possible — see §9.1.

### 3.6 Approval Gate — `core/feedback_loop.py`

Referenced but not deep-read for this document; wired at `graph.py:363`
(`approval_gate_node`) and invoked only when `require_approval=True` (§2.1). Shows the full
per-file diff and blocks writes until a human types `a`/`r`/`1,2,3`.

### 3.7 Connector Agent — `agents/connector_agent.py`

LLM call #3. Entry point `connector_agent()` (519+). For each replacement decision:

- One deterministic special case first: `attr.ib()` → `dataclasses.field()`
  (`rewrite_simple_attrs_to_dataclass`, `core/ast_rewrite.py` 66–210) — narrow,
  validates the entire recipe before mutating anything, declines on any construct outside
  the simple no-argument form (documented safety decision, not a failed rewrite).
- Otherwise: an LLM analyzes call sites for compatibility, and for anything incompatible
  runs a multi-turn **ACI edit session** (`_run_aci_wrapper_injection`, referenced at line
  764) — `open`/`edit` tool calls that inject a wrapper function inline, shown in the logs
  as `ACI session: N edit(s) applied over M tool call(s)`.
- **Guard: `_connector_edit_candidate_violation()`** (426–497, rewritten today — see §9.1)
  compares the AST of the source before/after the ACI edit to catch two distinct failure
  modes and now labels them differently:
  - The edit touched/removed the *original* import it doesn't own → `"generation"`
    (repairable; the connector gets retried with explicit feedback not to touch the import).
  - The edit introduced or referenced a *different, unselected* candidate package →
    `"candidate_identity"` (genuine decision-level incompatibility; the candidate gets
    excluded and AXIOM re-scores).
- **Deterministic post-processing import rewrite** (836–859): after all wrapper generation
  is done, connector_agent — not the LLM — applies `apply_import_replacements()` itself,
  but **only** for decisions where `connectors[d.original]["migration_feasible"] is True`
  exactly (comment 846–854: `None` must never be treated as permission, because that would
  let a failed wrapper generation "look superficially clean to import/static checks" while
  the actual call-site transformation never happened).
- `_preserves_top_level_definitions` guard (867–878): if an edit deletes required top-level
  structure, revert and mark `migration_feasible=None, failure_kind="generation"` —
  the pattern §9.1's fix now mirrors for the import-touching case.

### 3.8 Verifier Agent — `agents/verifier_agent.py`

Not an LLM call by default — a deterministic gate, plus an optional LLM review step for
ambiguous cases. Central logic in the function spanning roughly lines 1590–2520. In order:

1. **Environment completeness gate** (1618–1692): if `environment_plan["interpreter_status"]
   != "ready"`, or historical dependency recovery was ambiguous, or a target release
   couldn't be pinned (`target_resolution_failures`) — stop here with
   `verification_status = "environment_incomplete"` and one of four
   `verification_environment_status` values (`"interpreter_unavailable"`, `"blocked"`,
   `"target_version_unresolved"`, `"dependency_recovery_incomplete"`). This is the exact
   code path behind the eval run's 55% figure — see §6.
2. **No-op patch check** (1709–1799): if the patch equals the source, look at
   `connectors[...]["failure_kind"]`. `"provider"` → stop, unrecoverable. `"candidate_identity"`
   → exclude the candidate, route back to `axiom` (1749–1783; this is the exact block that
   permanently excluded `confluent-kafka` in the logged run). Anything else → route back to
   `connector` with concrete feedback text (1784–1799) — this is the path the §9.1 fix now
   sends premature-import-edit failures through instead.
3. Static toolchain checks, baseline/patched execution, adapter-contract checks, and an
   optional LLM review for genuinely ambiguous smoke-test output (lines ~1970–2450) — these
   feed `decision_level_roles` vs. `wrapper_fixable_roles` (2398–2408), which route to
   `axiom` or `connector` respectively (2458–2501), each consuming one slot of a globally
   bounded repair budget (`_reserve_repair_cycle`).

---

## 4. Environment reproducibility subsystem — `core/environment_plan.py`

`plan_project_environment()` (originally 566–685, now 642+ after today's edit) is
deliberately documented as side-effect-free: it reads project metadata and probes locally
available interpreters, never installs anything, by default. `_python_metadata()` (128–260)
recovers a declared Python version from `pyproject.toml`, `setup.cfg`, `setup.py`,
`.python-version`/`runtime.txt`, `tox.ini`, a `Dockerfile`'s `FROM python:X.Y`, or a GitHub
Actions workflow's `python-version:` — with explicit conflict warnings if sources disagree
(244–260). `available_interpreters()` (originally 274–285) only ever looked at
`shutil.which()` results for a fixed command list (`python3.13` down to `python3.5`) plus
`sys.executable` — i.e., only interpreters already on the machine running AXIOM.

**This was the second root cause of the 0-verified-migrations result** (§9.2): when no
locally installed interpreter satisfied a historical project's declared version, the plan
computed `interpreter_status = "interpreter_unavailable"` and `environment_backend =
"docker_recommended"` (line ~706–707 pre-fix) — but nothing anywhere in `smart_loader/`
ever executed against that `"docker_recommended"` value. It was a label with no executor
behind it, confirmed by grep returning zero matches for Docker invocation anywhere in the
codebase outside this one string.

---

## 5. Telemetry & diff/perf reporting

- `core/telemetry.py`: `save_session()` (50+) and `verify_session()` (139+) write
  `axiom_logs/sessions/{id}.json` (lean event/decision log with a hash) and
  `axiom_logs/details/sha256_{h}.json` (full decision content, content-addressed). `_hash()`
  (46+) is the SHA-256 used for the tamper-detection `integrity` field the README describes.
- `core/diff_reporter.py`: `AxiomReport` (67+) is the "diff/perf superclass" — `DiffReporter`
  (85+) and `PerfReporter` (191+) — that writes `axiom_logs/versions/{filename}/` records:
  `v{n}_original.py`, `v{n}_patched.py`, `diff_v{n-1}_to_v{n}.txt`, `perf_report.json`.
  `VersionRecord` (44+) and `PerfDelta` (56+) are the underlying dataclasses.

---

## 6. Evaluation harness — `eval/pymigbench_eval.py`

This is what actually produced `output_pymigbench_verified_20_final.log`. ~3,100 lines;
the parts most relevant to the numbers you're presenting:

- `build_eval_cases()` (736–822): for every PyMigBench migration, clones the client repo
  (`ensure_client_repo`, 123–146), materializes a **detached historical worktree** at
  `commit^` (`_historical_project_snapshot`, 182–218 — this is what populates
  `pymigbench_repos/.axiom_historical/...` you can see on disk), recovers the file content
  immediately before the migration commit (`get_file_before_commit`, 149–161), and recovers
  the historical dependency set scoped to that specific file/component
  (`_requirements_at_pre_migration_commit`, 221–555 — deliberately narrow: "does not claim
  to reproduce an entire monorepo," per its own docstring at 236–241).
- `_build_case_environment_plan()` (1038–1112, now updated — §9.2): calls
  `plan_project_environment()` per case using the historically-recovered requirements, never
  the checkout's current-day dependency file.
- `EvalResult` (954–1036) and `_result_from_state()` (1715+): this is where the outcome
  taxonomy in your summary table comes from — `environment_incomplete`,
  `verification_rejected`, `resolver_miss`, `no_migration_selected`, `target_infeasible`,
  `ground_truth_verified`, `verified_alternative`, etc. (full enumeration around line 1613).
  `_target_infeasibility_evidence()` (1682–1712) is, specifically, what turned the Kafka
  case into `"target_infeasible"` in the report you have — it reads `verification_failures`,
  which is exactly the dict `_compute_scores()` (§3.5) and `verifier_agent`'s
  `candidate_identity` branch (§3.8) populate.
- `ensure_pip_installed()` (665–695) is the existing "best-effort install, silent on
  failure, cached via a module-level set" pattern this document's fix to interpreter
  provisioning (§9.2) deliberately mirrors, rather than inventing a new convention.

---

## 7. Data model — `core/state.py`

`LoaderState` (141+, a `TypedDict`) is the single dict every graph node reads/writes a
subset of. Supporting dataclasses: `ImportInfo` (10–18), `EquivalenceGroup` (19–29, carries
`crypto_required`/`requires_connector` set by `constraints.py`), `BenchmarkResult` (30–55),
`RuleResult` (56–64), `LoadDecision` (65–77, the `winner`/`original`/`score`/`confidence`
record scoring produces), `AgentEvent` (78–86, the `agent_trace` entries you see printed as
`stage="..." event="..."`), `VerifierResult` (87–117), `RunMetrics` (118–140).

---

## 8. CLI surface — `cli.py`

- `doctor` (551–584): pure inspection — runs `plan_project_environment()` with no
  auto-provisioning, prints the plan, exits non-zero if `interpreter_status != "ready"`.
  Intentionally left side-effect-free even after today's changes (§9.2) — it's a diagnostic
  command, not a migration run, so it should show the true unaided state.
- `migrate` (587+): the real target-pinned migration command. Now accepts
  `--auto-provision-python` (added today, default `False` — opt-in, since installing a
  Python build without being asked would be a surprising side effect for an interactive CLI).

---

## 9. Fixes applied today

### 9.1 `agents/connector_agent.py` — premature-import-edit misclassification

**Symptom in the log:** the `kafka → confluent-kafka` case (case 1/20) selected the exact
PyMigBench target, but the ACI edit session touched the `import kafka` line while injecting
the wrapper. The guard at the time reverted the whole edit and set
`migration_feasible=False, failure_kind="candidate_identity"` — the same label reserved for
"this candidate is genuinely incompatible." `verifier_agent`'s identity-failure branch
(1749–1783) then permanently excluded `confluent-kafka` from `_compute_scores()` on retry
(§3.5, step 1), AXIOM reverted to keeping `kafka`, and the evaluator correctly-but-misleadingly
recorded `outcome = "target_infeasible"`.

**Root cause:** `axiom_agent.py` (905–993) *deliberately* defers the import rewrite to
connector_agent for any decision needing a connector, but `connector_agent.py`'s guard
treated connector doing exactly that as a violation — two parts of the same pipeline
disagreed about who owns the import line.

**Fix:** `_connector_edit_candidate_violation()` (426–497) now returns `(message,
failure_kind)` instead of a bare string, distinguishing:

- import-line-touched → `"generation"` (repairable — matches the existing pattern used a
  few lines below for `_preserves_top_level_definitions` failures)
- foreign-candidate-introduced → `"candidate_identity"` (unchanged — still a genuine veto)

The caller (809–831) now sets `migration_feasible=None` (not `False`) for the `"generation"`
case, which routes verifier's retry back through `connector_agent` with explicit feedback
("leave the import statement unchanged...") instead of blacklisting the candidate.

**Verified:** compiles clean (`py_compile`); a standalone behavioral test reproducing the
exact before/after source from the log confirms the premature-import-edit case now returns
`failure_kind == "generation"` while a genuine foreign-candidate substitution still returns
`"candidate_identity"` — i.e. the legitimate guard is untouched, only the misclassified case
changed.

### 9.2 `core/environment_plan.py`, `eval/pymigbench_eval.py`, `cli.py` — interpreter auto-provisioning

**Symptom:** 55% of the 20-case run (11 cases) ended `environment_incomplete` — either
`"blocked"` (base historical dependency install failed) or `"target_version_unresolved"`
(no target release resolvable). Both trace back to `available_interpreters()` only ever
finding interpreters already on the evaluator's machine; `environment_backend =
"docker_recommended"` was computed but never acted on anywhere in the codebase.

**Fix:**

- `_uv_managed_interpreter_paths()` + extended `available_interpreters()` (274–315): also
  discovers interpreters `uv` has already installed (still read-only).
- `provision_interpreter(version)` (318–366, new): explicit, opt-in — shells out to
  `uv python install <version>` then `uv python find <version>`. Best-effort and silent on
  failure (mirrors `ensure_pip_installed`'s existing convention, §6), because very old
  versions (2.x, and some early 3.x) predate `python-build-standalone`'s coverage and must
  still fall back to Docker or a manually supplied `--python`.
- `plan_project_environment(..., auto_provision_interpreter=False)`: new opt-in parameter
  (default `False`, so the function's side-effect-free contract holds for any caller that
  doesn't explicitly ask). When `True` and no local interpreter satisfies the project's
  declared version, it now calls `provision_interpreter()` before giving up (700–709).
- `eval/pymigbench_eval.py`'s `_build_case_environment_plan()` now passes
  `auto_provision_interpreter=True` — the harness already does best-effort installs
  elsewhere (`ensure_pip_installed`), so this is consistent with its existing behavior.
- `cli.py migrate` gained `--auto-provision-python` (default off) for the same recovery path
  on real user runs, without changing `doctor`'s pure-inspection behavior.

**Verified:** compiles clean; confirmed `available_interpreters()` and
`provision_interpreter()` behave safely with no `uv` installed (returns `{}`/`None`, no
crash); confirmed `plan_project_environment(..., auto_provision_interpreter=True)` degrades
identically to the default when provisioning isn't possible. Installed `uv` in the test
sandbox and confirmed `provision_interpreter("3.9")` correctly attempts the real
`uv python install` call and fails closed (returns `None`, no crash) when the sandbox's
network policy blocks the GitHub release download — this sandbox's outbound network is
locked down, so the actual download step could not be exercised end-to-end here. On your
machine, with normal internet access, that same call should succeed for any CPython version
`python-build-standalone` publishes (in practice, roughly 3.8+); confirm this on a real
re-run before trusting the 55% figure to drop significantly.

### 9.3 `agents/connector_agent.py` — wrapper defined but call site never rewritten

**Symptom:** confirmed by actually re-running the Kafka case after §9.1/§9.2: the false
`target_infeasible` was gone, but the patch still didn't verify. The ACI edit session
correctly injected `_create_confluent_producer(...)` and correctly left the import alone
(the deterministic post-processing rewrite fired: `Applying migration import rewrites:
{'kafka': 'confluent_kafka'}`) — but the actual call site, `self.producer =
KafkaProducer(bootstrap_servers=..., value_serializer=...)`, was never redirected to call
the new wrapper. Once the import pointed at `confluent_kafka`, that line referenced a
symbol (`KafkaProducer`) which doesn't exist there. Verifier's static/adapter-contract
checks caught this correctly (`"confluent_kafka does not expose KafkaProducer"`), so nothing
false was reported this time — but the patch was still broken.

**Root cause:** `ACI_EDIT_SYSTEM` (289–320) explicitly instructs the model to do two things
in one session — inject the wrapper *and* rewrite the incompatible call site to use it — but
nothing enforced that both halves actually happened. `_run_aci_wrapper_injection` (964–1035)
only validated syntax and that top-level structure was preserved; a wrapper that got defined
and then never called passed both checks cleanly.

**Fix:** new `_unused_wrapper_names(source, wrapper_names)` helper (~426–443) walks the
patched AST for `ast.Name` references to each wrapper name; any wrapper with zero references
outside its own `def` is provably dead — its call site was never rewritten. Wired into
`connector_agent()` right after both the ACI and legacy-fallback paths converge (~860–878):
if any wrapper comes back unused, the edit is reverted and classified
`migration_feasible=None, failure_kind="generation"` — the same repairable-retry pattern as
§9.1, so Verifier sends the connector back with concrete feedback ("wrapper X was defined
but never called...") instead of either silently shipping the broken patch or wrongly
vetoing the candidate.

**Verified:** compiles clean; a standalone test reproduces the exact buggy pattern from the
retest log (wrapper defined, `self.producer = KafkaProducer(...)` left unrewritten) and
confirms it's now flagged, while a correctly-wired version (`self.producer =
_create_confluent_producer(...)`) passes through with no false positive.

### 9.4 `eval/pymigbench_eval.py` — unbounded historical requirements resolve against today's PyPI

**Symptom:** same retest run — the case landed on `environment_incomplete` / `blocked`
because the *baseline* (unpatched original file) failed to import in the disposable venv:
`ModuleNotFoundError: No module named 'locust.wait_time'`. Nothing to do with the connector
fixes; the historical project's own `setup.py` declares `install_requires=["locustio>=1.0",
...]` (confirmed by reading `pymigbench_repos/.axiom_historical/.../setup.py` directly) —
no upper bound. `pip install locustio>=1.0` today resolves against PyPI's *current* index,
which can land on a release published years after this project (2019-era, targets Python
3.6/3.7) was written, with a different module layout.

**Root cause:** `_requirements_at_pre_migration_commit()` faithfully recovers exactly what
the project declared — that part was already correct — but faithfully reproducing an
unbounded floor from 2019 against 2026's package index isn't the same as reproducing the
environment the project actually ran in.

**Fix:** new `_commit_date()` (~182–200) gets the pre-migration commit's author date via
`git log -1 --format=%aI`. New `_pin_requirement_as_of_date(spec, as_of_date)` (~246–327)
queries PyPI (reusing `agents/security_agent.py`'s existing `_fetch_url`/`PYPI_API_URL`
rather than duplicating HTTP/SSL handling) and, for any requirement without an exact `==`
pin, selects the newest non-yanked release that both (a) existed on or before the commit
date and (b) still satisfies whatever bound the project itself declared (so `>=1.0` is
respected as a floor, not overridden). Wired into `_requirements_at_pre_migration_commit`'s
`_finish()` (352–355) so every collection path gets it. Fails open by design: no parseable
requirement, no PyPI data, or no candidate satisfying both the date and the declared bound
all fall back to the original unpinned spec unchanged — this can only ever tighten a spec
that was already unreproducible, never break one that worked.

**Verified:** compiles clean; a standalone test against a mocked PyPI response confirms all
four cases behave correctly — pins to the latest release before the cutoff when no floor
conflicts, leaves an already-`==`-pinned spec untouched, and correctly fails open (spec
unchanged) both when no date is available and when the date cutoff and the project's own
version floor can't both be satisfied.

**Caveat, stated plainly:** I could not verify against live PyPI data (no network access in
this sandbox) whether `locustio` specifically ever published a `>=1.0` release before this
project's commit date — if it didn't, this fix will correctly and honestly leave the spec
unpinned rather than guess, and this exact case may still fail the same way. The fix is
correct for the general class of bug (unbounded historical floors resolving against today's
index); whether it resolves *this specific* case depends on `locustio`'s real release
history, which only a live re-run can confirm.

### 9.5 `agents/profiler_agent.py` — unrelated declared dependency blocking verification

**Symptom:** confirmed by re-running after §9.1–§9.4: the false infeasibility and the
`locust.wait_time` bug were both gone (base_requirements in the retest report correctly
showed `psycogreen==1.0.2`, `psycopg2-binary==2.8.5`, `websocket-client==0.57.0`,
`python-dateutil==2.8.1`, `pymongo==3.10.1`, `kafka-python==2.0.1`, with only `locustio>=1.0`
correctly left unpinned per §9.4's own honest caveat). But a new blocker appeared:
`psycopg2-binary==2.8.5` — a Postgres driver `locust_plugins/kafka.py` never imports at all
— has no prebuilt wheel for the verifier's platform/Python, so pip tries to build it from
source and fails needing `pg_config`. Outcome stayed `environment_incomplete`/`blocked`.

**Root cause / decision:** `_profile_in_temporary_venv` (profiler_agent.py) installs a
historical project's *entire* declared `install_requires`/`requirements.txt` as one
all-or-nothing pip transaction (`_base_specs_and_constraints` → `_pip_install_many`, ~1059–
1066 pre-fix), regardless of whether the file actually under migration imports each package.
Two directions were considered: (a) prefer an available wheel when a historical pin has none,
or (b) only require packages the migrated file's own imports actually need, retrying narrower
only when the full install fails. Chose (b): it's the more broadly applicable fix across a
large, varied PyMigBench sample (many historical projects bundle unrelated integrations in
one dependency list), it doesn't weaken the historical-accuracy guarantee §9.4 just added
(the full, unpinned-nothing-dropped install remains the default and only path tried first),
and the codebase already had the exact classification logic needed
(`_declared_support_packages`, used elsewhere to tell "declared PyPI dependency this file
uses" apart from "project-local module") — extending it was lower-risk than inventing new
wheel-fallback logic.

**Fix:** new `_narrow_base_specs_to_needed_imports(base_specs, needed_modules)` (~741–770).
Wired into `_profile_in_temporary_venv` (~1091–1116): only on failure of the full base
install, retry using just the specs matching `support_packages` (imports the file itself
references that aren't the migration source/target — already computed by the existing
caller at profiler_agent.py:1369). Dropped packages are logged explicitly (never silent). If
the narrowed retry succeeds, `base_failures` stays empty and verification proceeds; if it
also fails, the original full-install error is preserved (no information lost, no
regression versus before this fix).

**Verified:** compiles clean; unit-tested against the exact locust-plugins scenario —
confirms the narrowed retry keeps only `locustio>=1.0` (matching the file's `import locust`)
and correctly drops all five unrelated packages, confirms an empty `needed_modules` narrows
to nothing (so the caller correctly keeps the original failure rather than attempting a
useless empty retry), and confirms a file needing two of six declared packages keeps exactly
those two.

### 9.6 `agents/profiler_agent.py` — §9.5's narrowing fix hadn't reached the environment that actually decides the outcome

**Symptom:** re-running the Kafka case after §9.5 still ended `environment_incomplete` /
`blocked` on `psycopg2-binary` — the exact failure §9.5 was supposed to fix.

**Root cause:** two separate functions build disposable venvs from the same base-requirement
install logic: `_profile_in_temporary_venv` (the profiler's mixed measurement venv — used for
cheap import/memory numbers, not authoritative) and `create_verification_environment` (the
verifier's baseline/target sandboxes — the one whose failures actually set
`verification_status = environment_incomplete`). §9.5 only touched the profiler's copy. The
retest log's error was prefixed `baseline:base-requirements:`, which traces to
`create_verification_environment`, not the function I'd fixed.

**Fix:** extracted the shared install-then-narrow logic out of both into one function,
`_install_base_layer(python, base_requirements, explicit_packages, env_dir,
needed_modules=...)` (~762–830 pre-§9.7). Both callers now call this one implementation.
`create_verification_environment` gained a `needed_modules` parameter; `verifier_agent.py`'s
`isolated_profiling` block now computes it (the migrated file's own imports, minus stdlib and
minus the source/target packages themselves) and passes it to both the baseline and target
`create_verification_environment(...)` calls (~1843–1917).

**Verified:** unit-tested the shared helper directly (full install fails → narrowed retry with
only needed imports succeeds → returns clean); confirmed via a real re-run that the Kafka
case's baseline now installs successfully with `psycopg2-binary` dropped.

### 9.7 50-case run — root-causing the 70% `environment_incomplete` rate

A 50-case run (`pymigbench_verified_50.json`) came back with 35/50 (70%) `environment_incomplete`:
23 `blocked`, 12 `target_version_unresolved`. Categorizing all 35 against the JSON's
`verification_environment_status` and full `error` text (not just the truncated CLI summary)
found two genuinely distinct problems, only one of which is fixable in this architecture:

**`target_version_unresolved` (12/12, confirmed, not a bug):** every one of these 12 cases has
`environment_plan.interpreter_status == "interpreter_unavailable"` with a `python_constraint`
of `>=2.6,<2.7`, `>=2.7,<2.8`, `>=2.7,<3`, or `>=3.5,<3.6` — all Python 2.x or 3.5 projects.
`uv`'s `python-build-standalone` catalog (what §9.2's auto-provisioning uses) has never shipped
Python 2 builds, and doesn't reliably cover 3.5. This is a hard tooling limit, not a bug: no
amount of narrowing or pin-widening fixes a missing interpreter. It's already reported as its
own distinct sub-status, separate from genuine environment bugs — see the decision below.

**`blocked` (23/23, root-caused individually):** with full error text, these split into three
groups:

1. **Unrelated declared dependency, old native package fails to build for today's platform**
   (locust-plugins/psycopg2, petisco×2/Cython-distutils, throat/pycairo+missing system
   `pkg-config cairo`, archery/mysqlclient+missing system `mysql_config`,
   project-alice-assistant×6/numpy, nwb-conversion-tools×7/numpy, skll/`pkg_resources`+numpy —
   19 of 23 cases). §9.6's narrowing already excludes these when the migrated file doesn't
   import them. Two of these (throat, archery) fail on a *missing system library*, not a
   Python packaging issue — narrowing still resolves them if the migrated file doesn't need
   the package at all, but if it does, no amount of Python-level fixing installs a system
   `cairo`/`mysql_config` binary; that would need a container.
2. **The migration source/target package's own exact historical pin has no installable
   release for this platform** — distinct from (1) because narrowing can't drop the package
   under test itself. Found directly: `PyQt5==5.13.1` (nwb-conversion-tools) returns "No
   matching distribution found" while 5.14.0+ do have wheels; `gevent`
   (flask-socketio) fails to *build* at its exact pin (an old Cython-generated `.c` file using
   Python-2-style `print >>stream` syntax the current toolchain rejects) but newer patches in
   the same major line ship prebuilt wheels.
3. **Declared-dependency recovery gaps** (talkpython/`bs4`, one isolated static-analysis-tool
   venv failure and one undeclared/unpublished `spamwatch` import on userge-plugins).

**Fixes shipped for groups 2 and 3:**

- `_pip_install_source_pin()` (profiler_agent.py, new, ~952–1024): installs the migration
  source at its exact historical pin first; if that fails, retries once within the same major
  version (`name>=X,<X+1` via new `_same_major_version_fallback()`) instead of jumping to an
  unconstrained latest release. Wired into both `create_verification_environment` and
  `_profile_in_temporary_venv`'s source-install call sites, replacing the bare `_pip_install`
  call each previously made for the source package.
- `_install_base_layer()` gained a third fallback tier: when the narrowed retry (§9.6) *still*
  fails — meaning the failing package is genuinely needed by the file under test, not unrelated
  bloat narrowing can drop — widen every exact pin in the narrowed set to its same major
  version and retry once more. Directly targets group-1 cases where the needed package (e.g.
  skll's numpy) is real but its one exact historical patch fails to build.
- `bs4` added to `IMPORT_TO_DISTRIBUTION` (was missing → `_declared_support_packages`
  misclassified `import bs4` as project-local instead of mapping it to the declared
  `beautifulsoup4` requirement, so it was never installed).

**Verified:** all three additions unit-tested directly (mocked `_pip_install`/
`_pip_install_many`) against the exact failure/success sequences observed in the log —
same-major fallback succeeding after an exact-pin "no matching distribution" failure, both
exact pin and fallback failing (error concatenated, nothing hidden), an already-open
specifier skipping the fallback attempt entirely, the base-layer's three-tier
full→narrowed→widened sequence resolving a "needed, not unrelated" old-pin build failure, and
confirming no fallback retry fires when there's nothing to widen (pure ranges, already-open
specifiers). **Not yet confirmed against a real re-run** — that requires burning the 50 cases
again, which wasn't done in this session; expect most of the 19 group-1 cases and both group-2
cases to flip, `bs4` to fix talkpython, and the two remaining edge cases
(throat/archery's *missing system libraries*, if the migrated file needs the package;
userge-plugins' `spamwatch`) to still need a container-based backend or individual triage.

**Decision on "if it's not fixable, take it away" (target_version_unresolved / Python
2-3.5):** did not remove environment verification — an unverified migration isn't a
deliverable worth trusting, and 38/50 cases (everything outside these 12) are legitimately
Python 3.6+ and answerable by this architecture. What *is* already true, and is the honest
answer to "take it away": AXIOM does not conflate this with a real bug. `verification_status`
is `environment_incomplete` for both, but `verification_environment_status` splits them —
`target_version_unresolved` (with an explicit `interpreter_status: interpreter_unavailable`
warning: "Install/select a compatible project Python or use the declared container backend")
vs `blocked` (an actual install/build failure). The CLI summary already prints this
breakdown (`Environment reasons: blocked=23, target_version_unresolved=12`). Considered adding
a pre-flight skip so these 12 don't burn a full LLM-driven pipeline run before landing here —
rejected: `environment_plan` is computed before `run_loader`, but `verifier_agent.py` only
checks `interpreter_status` *if* AXIOM actually selected a replacement (`if replacements and
plan_status != "ready"`, verifier_agent.py:1618-1619). Some Python-2/3.5 cases correctly end
in `no_migration_selected` or `resolver_miss` instead — skipping before the resolver/axiom
agents run would misclassify those as `environment_incomplete` they never were. Real Docker
support is the only way to actually verify these 12; that remains unbuilt (§9.8).

### 9.8 `core/environment_plan.py`, `agents/profiler_agent.py`, `cli.py` — real Docker backend

**Why this, now:** §9.8 (below) had flagged `environment_backend = "docker_recommended"` as
a label with no executor behind it — the only remaining path to the 12 `target_version_unresolved`
cases (§9.7), all Python 2.x/3.5, none of which `uv` can ever provide (python-build-standalone
never shipped Python 2 builds; that's not a coverage gap that closes later, it's by
construction). Asked directly whether this was worth building versus removing the feature:
it's the only thing that actually solves that specific 12/50-case slice — not the other 22
fixed cases, and not the general `environment_incomplete` rate — so building it was the
right call, scoped to exactly what it solves.

**Design constraint:** every install/execution call site in `profiler_agent.py` and
`verifier_agent.py` (pip installs, runtime probes, static analysis tools, running the migrated
code itself) calls `subprocess.run([python, ...], cwd=..., ...)` against a plain executable
path string, with zero awareness of *how* that executable is provided. Rewriting all of them
to be container-aware would be large and risky to ship untested. Instead, the fix makes the
"python executable path" a real, host-side, executable **wrapper script** that internally
re-invokes `docker run` against a fixed image every time — every existing caller keeps
calling `subprocess.run([python, ...])` completely unchanged.

**Fix:**

- `core/environment_plan.py`: new `_docker_image_candidates(version)` (tries `python:X.Y-slim`
  then `python:X.Y` — Docker Hub's official image), `_docker_available()` (binary + daemon
  check via `docker info`), and `provision_docker_interpreter(version)` — pulls a candidate
  image, returns a `docker://<image>` sentinel string (not a filesystem path) on success, `None`
  on any failure (no `docker` binary, daemon not running, no matching tag for that ancient a
  minor). Same best-effort/silent-on-failure convention as `provision_interpreter` (§9.2).
- `plan_project_environment(..., auto_provision_interpreter=True)`: after a local interpreter
  and uv (§9.2) both come up empty — which for Python 2.x is immediate, since uv is skipped
  for 2.x entirely — now also tries `provision_docker_interpreter`. On success,
  `interpreter_status="ready"`, `environment_backend="docker"` (not `"docker_recommended"`),
  `selected_python` is the `docker://` sentinel. The existing "Python 2 can't run local
  `venv`" guard (§1 background) is skipped specifically when the interpreter came from Docker,
  since that path doesn't use a local `venv` invocation at all. No new parameter — this reuses
  the same `auto_provision_interpreter=True` flag the eval harness and `cli.py migrate
  --auto-provision-python` already pass, so **neither needed a code change** to get this for
  free.
- `agents/profiler_agent.py`: `_get_python` passes a `docker://` sentinel through unresolved
  instead of failing an `os.path.isfile` check. New `_create_docker_venv(env_dir, image)`:
  runs `python -m venv` inside a throwaway container bind-mounting `env_dir` to `/axiom-env`
  (falling back to installing and using `virtualenv` for Python 2 images, since `venv` is
  Python 3.3+ only and the official 2.7 image doesn't have it); then writes an executable
  wrapper script to `env_dir/docker-python.sh` that forwards all arguments to
  `docker run --rm -v "$(pwd)":"$(pwd)" -v {tmp_root}:{tmp_root} -v {env_dir}:/axiom-env -w
  "$(pwd)" {image} /axiom-env/venv/bin/python "$@"`, with a `trap`-based cleanup so a killed
  wrapper doesn't orphan a running container. Mounting `$(pwd)` (the host `cwd` the wrapper
  itself inherits from whatever `subprocess.run(..., cwd=...)` call invoked it) and the system
  temp root identically inside the container — rather than translating paths — is what lets
  `verifier_agent.py`'s existing `cwd=`/file-path-argument calls (e.g. running bandit against
  a real file path) work against a container with zero changes on that side; every temp
  directory this codebase creates (`_project_overlay`, disposable venvs, etc.) lives under one
  of those two mounted roots already. `_create_disposable_venv` (the single choke point all
  three profiler/verifier venv-creation call sites already went through, per §9.6) now branches
  to this when handed a `docker://` sentinel.
- `cli.py migrate --auto-provision-python`: help text updated to mention the Docker fallback;
  no functional change needed, since it already threads into the same
  `auto_provision_interpreter` flag.

**Verified:** compiles clean. Unit-tested `provision_docker_interpreter` against mocked
`docker`/`subprocess` calls — slim tag succeeds, slim fails and falls back to the full tag,
no `docker` binary returns `None`, daemon-down (`docker info` fails) returns `None`. Unit-tested
`plan_project_environment` end-to-end for a simulated Python 2 project: resolves to
`interpreter_status="ready"`, `environment_backend="docker"` when Docker succeeds; degrades to
the exact prior `"interpreter_unavailable"`/`"unavailable"` behavior, unchanged, when Docker
also fails; confirmed zero side effects (Docker never even attempted) when
`auto_provision_interpreter=False`, preserving the module's side-effect-free-by-default
contract. Unit-tested `_get_python`/`_create_disposable_venv`'s sentinel routing and confirmed
the generated wrapper script passes `sh -n` and `dash -n` syntax validation (POSIX-compliant),
and that both the "no docker binary" and "image pull/venv-creation failed" paths raise
`RuntimeError` with the real docker stderr attached rather than failing silently or hanging.

**Not verified, and cannot be from this sandbox:** there is no `docker` binary or daemon here
at all, so none of this has actually pulled a real image, created a real container venv, or
run real code inside one. The design is sound and every piece that *can* be tested without a
daemon has been — but, exactly like §9.2's `uv python install`, the real proof is a live run
on a machine with Docker installed. Recommend testing against a single known Python-2 case
(e.g. `jimfunk/django-postgresql-netfields`, `netaddr→py2-ipaddress`) before trusting this
against the full 12-case slice.

### 9.9b `agents/profiler_agent.py` — base install failing on the *interpreter*, not the package version

**Symptom, found in a real re-run after §9.6–§9.8:** narrowing (§9.6) and same-major widening
(§9.7) were both confirmed firing correctly — narrowing alone fixed locust-plugins and
upstox-python outright. But petisco, throat, archery, and all of project-alice-assistant and
nwb-conversion-tools still failed, now with the widened retry itself also failing, on a
*different* traceback than the original.

**Root cause:** these all resolve to `numpy` (or a Cython/setuptools-vendored build system)
failing under Python 3.12 specifically — `numpy.distutils` depends on the stdlib `distutils`
module, which Python 3.12 removed entirely (PEP 632). No numpy 1.x release installs cleanly
under 3.12; widening to `numpy>=1,<2` just finds a *different* release that fails a *different*
way for the same underlying reason. None of these projects declare a Python constraint at all,
so `plan_project_environment`'s honest fallback — "no historical metadata is not an error; the
current interpreter is the only honest default" — was landing on the evaluation machine's
newest local Python (3.12), which is *newer* than what these old build systems ever needed to
support.

**Fix:** `_looks_like_legacy_build_incompatibility(detail)` (new): a deliberately broad
substring check (`distutils`, `ccompiler`, `cython_sources`) for "this is an interpreter
incompatibility, not a wrong-package-version problem." `_older_local_interpreters(exclude)`
(new): already-installed local Pythons older than the one just used (`python3.11` down to
`python3.8`, via `shutil.which` — never downloads anything, unlike `provision_interpreter`/
`provision_docker_interpreter`). `_install_base_layer` gained a fourth tier: when the widened
retry (§9.7) still fails with a legacy-build signature *and* the current interpreter is 3.12+,
rebuild `env_dir`'s venv under each older local candidate in turn (reusing
`_create_disposable_venv`, which already `--clear`s and rebuilds in place) and retry the same
specs there. `_install_base_layer`'s return signature changed from `(constraints_path,
failures)` to `(constraints_path, failures, python)` — both call sites now use the returned
`python` for everything downstream, since a successful fallback means the rest of that case's
installs and execution need to happen on the new interpreter, not the one that just failed. If
every candidate fails too, the venv is explicitly rebuilt back under the *original* interpreter
before returning, so a failed fallback attempt never leaves the caller holding a `python` path
that doesn't match what's actually in `env_dir`.

**Known simplification, stated plainly:** this does not check whether the project's declared
constraints (if any) actually permit an older interpreter — it only fires when *no* constraint
was found at all (the "defaulted to newest" case). For the failing cases this session, that's
exactly right — old attrs/PyQt5-era projects don't declare `>=3.12`. A project that genuinely
does declare `>=3.12` and also happens to trip this exact signature (unlikely, but not
impossible) would not be affected by this bug, since the tier only fires when the base install
already failed on the interpreter `plan_project_environment` selected, and that selection logic
already respects the true declared constraint when one exists (§4). Threading the raw
constraint string all the way into `_install_base_layer` for an extra belt-and-suspenders check
was left out to keep this fix scoped and reviewable.

**Verified:** unit-tested directly — the signature detector against the real traceback text
from the log; `_older_local_interpreters` ordering and self-exclusion; the full four-tier
sequence reproducing the exact project-alice-assistant/nwb-conversion-tools failure pattern
(full install fails → narrowed fails, since numpy is genuinely needed → widened fails with a
*different* traceback → interpreter-swap succeeds under a mocked 3.9); the case where no older
interpreter exists at all (falls through cleanly, `python` unchanged); the case where the
older-interpreter retry *also* fails (venv correctly restored to the original interpreter, not
left on a broken intermediate state); and confirmed a non-legacy-build failure (e.g. a missing
system library) never even attempts this tier. **Not yet confirmed on a live re-run** — same
caveat as every fix this session: unit tests prove the logic, not that numpy genuinely installs
cleanly under a real local Python 3.9/3.10/3.11 on your machine.

### 9.9d Fix 10 had nothing to fall back to on a machine with only Python 3.12 installed

**Symptom, found in a real re-run against the filtered (no-Python-2) dataset:** `catalystneuro/
nwb-conversion-tools` — a project that declares no Python version at all — still hit
`environment_incomplete` on the exact numpy/`distutils`/`CCompiler` signature Fix 10 (§9.9b)
exists to catch. No "retrying the whole base layer under X" message appeared anywhere in the
log, meaning tier 4 never even started an attempt.

**Root cause, traced to two places.** First, `environment_plan.py`'s `plan_project_environment`
has a third branch, separate from the "declared but unsatisfiable" path that already tries `uv`
and Docker (§9.8): when a project declares *no* Python version at all, it goes straight to
`sys.executable` — whatever's newest on the machine — and never considers any fallback:

```python
elif not detected:
    # No historical metadata is not an error; the current interpreter
    # is the only honest default for an ordinary user project.
    selected_version = _interpreter_version(sys.executable)
    selected = sys.executable if selected_version else ""
```

That's correct as a *default* — an undeclared project has no historical contract to honor, so
defaulting to the newest interpreter is the honest choice at planning time, before anything has
actually failed. Fix 10 exists precisely to catch it reactively, after a real install failure
proves that default was wrong for this specific package. Second, `_older_local_interpreters`
(§9.9b) only ever checks `shutil.which` — genuinely never downloads anything, by design, so tier
4 had nothing to offer on a machine with no Python installed older than 3.12.

**Fix:** `_older_local_interpreters` stays exactly as it was (free, instant, tried first). A new
`_older_interpreter_candidates(exclude)` generator wraps it: yields every already-installed local
candidate first, and only if the caller keeps asking after those are exhausted, falls back to
`provision_interpreter` (`core/environment_plan.py`'s existing `uv python install` mechanism,
previously only reachable from the "declared but unsatisfiable" path) for `3.11`, `3.10`, `3.9`,
`3.8` in turn. Generator laziness matters here specifically: a case that succeeds on a local
interpreter must never pay for a download it didn't need. Tier 4's loop now iterates
`_older_interpreter_candidates(python)` instead of `_older_local_interpreters(python)` directly —
a one-line swap, since the loop body (venv rebuild, constraints regeneration from §9.9c,
`_pip_install_many` retry) was already written to work over an arbitrary sequence of candidate
interpreters.

**Verified:** unit-tested with real `_install_base_layer` calls (not just the generator in
isolation). Confirmed a working local candidate short-circuits before `provision_interpreter` is
ever called (laziness, checked via a call-count mock). Confirmed the exact
`nwb-conversion-tools` shape — zero local candidates, `uv` fails for `3.11` then succeeds for
`3.10` — resolves the case and returns the provisioned interpreter with a valid, freshly
regenerated constraints file. Confirmed the fully-exhausted path (no local interpreter, `uv`
fails for all four versions) still degrades cleanly: `base-requirements` failure recorded, and
the venv is explicitly rebuilt back under the *original* interpreter before returning, so a
totally failed provisioning attempt never leaves `env_dir` on a stale/wrong interpreter. **Not
yet confirmed on a live re-run** — same standing caveat as every fix this session: this proves
the retry logic and laziness are correct, not that `uv` can actually fetch Python 3.10/3.9/3.8
standalone builds for your specific machine's OS/architecture, which needs a real run to know.

### 9.9e Repeated venv rebuilds in the same directory corrupted `env_dir/bin/python`

**Symptom, found in a real re-run immediately after Fix 12 (§9.9d) landed:** the good news
first — Fix 12 genuinely works. The log showed real `uv`-downloaded interpreters
(`cpython-3.11.14-macos-aarch64-none`, `-3.10.19-`, `-3.9.25-`, `-3.8.20-`) actually being
fetched and tried. But every one of them failed on the *identical* distutils signature, which
is itself the tell — an old, pure-Python-ish package like `slackclient`/`eventlet` failing
identically under four different interpreters, all the way down to 3.8, isn't a real per-
interpreter build incompatibility. And the case's *later*, unrelated install (the migration
source itself, via `_pip_install_source_pin`) then failed with pip's generic
`[Errno 62] Too many levels of symbolic links: '.../axiom-profile-xxx/bin/python'`.

**Root cause:** tier 4 calls `_create_disposable_venv(env_dir, candidate)` against the *same*
`env_dir` path for every candidate in sequence — up to four fallback attempts plus a final
restore, five `venv --clear` invocations against one directory in quick succession.
`--clear`'s own documented behavior ("delete the contents of the environment directory... before
environment creation") is `venv`'s internal cleanup logic, not a guaranteed full wipe — and
repeated in-place clearing across different base interpreters left `env_dir/bin/python` as a
broken/circular symlink by the last attempt. Once that happens, every subsequent operation
against that path — including the tier-4 attempts themselves, and any later install call that
reuses the same `python` executable — fails with ELOOP, which looks nothing like the original
distutils problem and gets misattributed to "no installable release for this platform."

**Fix:** `_create_disposable_venv` no longer relies on `venv --clear` alone. Before invoking
`venv`, it now explicitly clears `env_dir` itself: `shutil.rmtree` for an ordinary directory,
`os.unlink` for the case where the path itself is a broken/circular symlink (caught via
`os.path.lexists` + a guarded `isdir`/`islink` check, since `isdir`/`exists` themselves follow
symlinks and can raise `OSError(ELOOP)` against exactly this kind of corrupted path), with a
`shutil.rmtree(ignore_errors=True)` fallback if neither branch cleanly applies. Every call site
gets this for free — it's the one function every disposable venv in the codebase already goes
through, not a tier-4-specific patch.

**Verified:** reproduced the exact failure directly — created a self-referential symlink at
`env_dir/bin/python` and confirmed `open()` against it raises `OSError` with `ELOOP` (errno 40 on
Linux; errno 62 on macOS, same underlying condition, different platform errno numbering — matches
the log precisely). Confirmed `_create_disposable_venv` against that corrupted directory now
recovers cleanly and returns a real, working interpreter. Confirmed no regression: normal venv
creation still works, and five repeated rebuilds of the same directory in a row (the exact tier-4
access pattern) all succeed. **Not yet confirmed against the specific `nwb-conversion-tools`/
`petisco`/`throat` cases on a live re-run** — the reproduction proves the corruption mechanism and
the fix's recovery, not that these specific cases will now verify (they still may fail for other,
legitimate reasons once the venv itself stops being the problem).

### 9.9f `core/ast_rewrite.py` — the deepest any case has ever reached, and why it still failed

**Context:** across every run this session, zero cases have reached `Verified exact PyMigBench
migration`. Traced the single case that got furthest of all of them — `svenskaspel/locust-plugins`,
`kafka` → `confluent-kafka` — all the way through Stage 1 differential static analysis (pylint,
bandit, mypy, import smoke test, all real, all executed). It failed there, and the failure is a
genuine, specific, deterministic bug, not an environment or resolver problem.

**Root cause:** `ImportRewriter.visit_ImportFrom` (the AST pass behind
`apply_import_replacements`, applied once Connector has produced an accepted patch) only ever
rewrote the *module path* of a `from X import Y` statement — `kafka` → `confluent_kafka` — never
the imported name `Y` itself. The original file did `from kafka import KafkaProducer`. The
Connector's own generated wrapper (`_create_confluent_producer`) correctly does its own local
`from confluent_kafka import Producer` — the real class name under the new library — and every
call site was rewritten to go through the wrappers. That left the top-level `KafkaProducer`
import completely unused, and the blind module-path rewrite turned it into
`from confluent_kafka import KafkaProducer` — a class that doesn't exist under the new module,
since it's genuinely never called anywhere in the patched file. The verifier's import smoke test
caught this correctly (`confluent_kafka does not expose KafkaProducer`) — the guard did its job;
the bug was upstream of it, in a rewrite step with no awareness of whether the name it just
touched was still needed.

**Fix:** `apply_import_replacements` now computes the set of identifiers actually referenced
anywhere else in the source (the exact same technique `agents/connector_agent.py`'s
`_unused_wrapper_names` already uses for the analogous "wrapper defined but never called" check)
and passes it into `ImportRewriter`. An imported name whose binding has gone unused after
Connector's edits is dropped instead of rewritten to a symbol that may not exist under the new
module. A name that's still directly referenced — the ordinary drop-in case, where the old and
new library share the same class name (e.g. `WebClient` for `slack` → `slack_sdk`) — is rewritten
exactly as before; this only changes behavior for names nothing references anymore. Wildcard
imports (`from X import *`) are deliberately exempted from the unused-check entirely, since their
bound names can't be enumerated from this file alone — dropping one would be wrong on every
rewrite, not just the safe cases.

**Verified:** four unit tests directly against `apply_import_replacements`. The exact failing
shape (dead `KafkaProducer` import, wrapper uses its own local `Producer` import) — confirmed
dropped, no invalid symbol left in the output. The ordinary case where both the class and its
error type are genuinely still referenced (`WebClient` / `SlackApiError`) — confirmed rewritten
unchanged, no regression. A single `from X import A, B` statement with one used and one now-dead
name — confirmed only the dead one is dropped, not the whole statement. A wildcard import —
confirmed preserved and its module path still rewritten, not dropped. **Not yet confirmed on a
live re-run** — same standing caveat as everything else this session: this proves the rewrite
logic is now correct for the exact case that exposed it, not that `locust-plugins`/kafka (or any
other case) will now reach `Verified exact PyMigBench migration` — Stage 1 checks other things
too (pylint/mypy new-finding deltas, adapter contracts), any of which could still legitimately
reject a patch for an unrelated reason.

### 9.9g Fix 15 — Docker as a third fallback layer in tier 4, alongside local and `uv`

**Context:** with Docker Desktop confirmed running on this machine (`docker info` showing a live
daemon, `Server Version: 29.4.3`), tier 4's older-interpreter retry had a real gap: it already fell
back from local interpreters to `uv`-provisioned ones (Fix 12), but `provision_docker_interpreter`
(`core/environment_plan.py`) — already written, already used elsewhere for a project's *declared*
Python version — was never wired into this path at all. On a machine or network where `uv` can't
fetch a given standalone build (not every old minor has one, and corporate/restricted networks can
block the download), Docker was sitting there unused as a strictly more robust alternative for the
exact same failure class, since a `python:3.9-slim` image sidesteps the host's toolchain entirely
instead of needing a matching prebuilt interpreter for this specific platform.

**Fix:** `_older_interpreter_candidates` now tries three layers per version, cheapest first: local
interpreters (free, instant), then `uv` (`provision_interpreter`), then — only if `uv` fails for
that specific version — Docker (`provision_docker_interpreter`), yielding its `docker://<image>`
sentinel directly. `_create_disposable_venv`/`_get_python` already recognized this sentinel before
this fix (used for declared-version provisioning), so no changes were needed on the consuming side
— tier 4's loop body accepts whatever `_older_interpreter_candidates` yields without caring which
of the three layers it came from. Docker is deliberately the last resort, not tried in parallel or
before `uv`: pulling and running a container is slower than either a local binary or a `uv`
download, and a case that succeeds on either of those should never pay for a container pull it
didn't need.

**Verified:** four unit tests directly against `_older_interpreter_candidates`, mocking
`provision_interpreter` and `provision_docker_interpreter` (real temp files stood in for local/uv
paths so the existing `os.path.isfile` check passes honestly rather than being bypassed).
Confirmed: (1) a successful `uv` provision for a version skips Docker for that version entirely;
(2) when `uv` fails for a version, Docker is tried next and its sentinel is yielded unchanged,
with call order proving Docker only fires after `uv`'s failure, not before or in parallel; (3)
laziness holds across the whole three-layer chain — pulling only the first candidate via `next()`
(mirroring how tier 4 actually consumes this generator, stopping as soon as one candidate's install
succeeds) confirms later versions' `uv`/Docker attempts are never made once an earlier version
succeeds; (4) total failure across all three layers for all four candidate versions returns an
empty generator cleanly, no exception. **Not yet confirmed on a live re-run** — same standing
caveat as every fix this session: this proves the fallback chain is wired correctly and lazy, not
that any specific stuck case (`nwb-conversion-tools`, `project-alice-assistant`,
`ethereum/py-evm`'s declared-3.5 constraint, etc.) will now resolve — that depends on whether a
matching Docker image tag still exists for the version each of those cases needs, which only a
real run against the network will show.

**Still open:** this only reaches tier 4's *reactive* retry, triggered after a legacy-build failure
signature is detected. `plan_project_environment`'s `elif not detected:` branch — projects that
declare no Python version at all — still goes straight to `sys.executable` at the planning stage
with no `uv`/Docker fallback considered up front; those cases only get rescued if they first fail
loudly enough for tier 4 to catch, not proactively. Wiring Docker/`uv` into that branch directly is
a separate, not-yet-started change with its own tradeoffs (it would change planning-stage behavior
for a very common case) and hasn't been proposed as a concrete next step yet.

### 9.9h Fix 16 — a declared dependency's own install had no fallback, and threw away Layer 1's success

**Context:** a real 30-case re-run after Fix 15 landed, uploaded as `output30.log`. Headline
unchanged at 0% verified, but the *last* case in the run (`nwbn_conversion-tools`, `PyQt5` →
`PyQt6`) reached furthest into the pipeline of anything in this log and is the clearest evidence
yet of a genuinely fixable, specific bug rather than a fundamental limitation. No `uv`/Docker
provisioning message appears anywhere in the log — that's expected and correct, not a regression:
tier 4 (Fix 12/15) only fires when tiers 1–3 all fail, and this case's own log shows tier 3 (same-
major-version widening) succeeding for the base layer (`✓ Same-major-version base install
succeeded.`), so tier 4 correctly never ran.

**Root cause:** `_profile_in_temporary_venv` installs a case's dependencies in two separate layers
against the same venv. Layer 1 (`_install_base_layer`, shared with the verifier per Fix 5–6's
docstring) installs the full declared base-requirements set, narrows to only what this file's
imports need on failure, and widens exact pins to same-major ranges on failure after that — all
three tiers, plus the Fix 12/15 interpreter-swap tier, are available here. The log confirms this
succeeded: narrowed down to the one package this file needs (`numpy`, dropping fifteen unrelated
declared dependencies), then widened its pin and installed successfully. Layer 2 then separately
re-installs each of `support_packages` — declared dependencies this file directly imports, `numpy`
among them — via `_pinned_spec(package, base_requirements)`, the *exact* historical pin
(`numpy==1.17.3`), through a **plain `_pip_install` call with no fallback of any kind**: no
narrowing (a single package, nothing to narrow to), no same-major widening, no tier-4 interpreter
swap. `numpy==1.17.3` hits the identical stdlib-distutils-removal failure Fix 10/12/13/15 exist for
(`NameError: name 'CCompiler' is not defined`) — a failure Layer 1 had *already* solved for this
exact package, in this exact venv, moments earlier — and Layer 2's un-retried re-install fails,
recording `base_failures["numpy"] = detail` and discarding a working install the venv already had.
The migration source's own exact pin already had this same fallback
(`_pip_install_source_pin`/`_same_major_version_fallback`, used since Fix 7); declared support
packages were never given the same treatment, so this is the one install path in the file with no
recovery from an unavailable exact historical release at all.

**Fix:** Layer 2's support-package branch now retries with `_same_major_version_fallback` — the
same widening function `_pip_install_source_pin` already uses — when the exact pin fails, mirroring
that existing, already-proven pattern instead of introducing a new one. Only re-attempted, never
skipped: an exact pin that already installs cleanly never pays for an extra call.

**Verified:** three unit tests directly against `_profile_in_temporary_venv`, mocking only the pip-
subprocess boundary (`_pip_install`) and the two upstream layers that don't need re-testing here
(`_install_base_layer`, already covered by Fix 11/12/13/15's own tests; `_create_disposable_venv`).
Reproduced the exact real shape from the log — `numpy==1.17.3` failing with the CCompiler signature,
a same-major-version release succeeding — and confirmed `base_failures` ends up empty rather than
blocking the case. Confirmed a genuine failure (both the exact pin and the widened fallback fail)
is still reported, not silently swallowed — this fix recovers a specific known-recoverable shape, it
does not turn support-package failures into an ignored no-op. Confirmed no wasted work: an exact pin
that already installs cleanly triggers exactly one `_pip_install` call, never a redundant fallback
attempt. **Not yet confirmed on a live re-run** — same standing caveat as every fix this session:
this proves the widening logic recovers the exact numpy/CCompiler shape found in the log, not that
`nwbn_conversion-tools`'s PyQt5→PyQt6 case reaches `Verified exact PyMigBench migration` — Stage 1's
own static-analysis/import-smoke checks run after environment setup and could still reject the case
for an unrelated reason once the environment itself stops being the blocker.

### 9.9q Fix 25 — one-level transitive dependency scanning in the security agent (SCA)

**Context:** prompted by a question about adopting ideas from an IEEE SCA paper ("Software
Composition Analysis for Proactive Threat Detection in Software Dependencies with Real-Time
Security Monitoring", Kowsik et al.). Honest scoping against what the agent already does: OSV
advisory queries with CVSS-v3 vector parsing, severity-weighted scoring, PyPI freshness, yanked
detection, Python-version-compatible release selection, and fail-closed evidence rules were all
already present — but everything scored only the package *itself*. A candidate scoring 1.00 on its
own advisories while shipping a vulnerable direct dependency was invisible: the "composition" half
of SCA was missing entirely. The paper's other headline feature, real-time/continuous monitoring,
was deliberately NOT adopted — it exists for long-running deployed systems, while AXIOM queries OSV
live at scan time, which is already as current as a batch evaluation can meaningfully be.

**Fix:** `_direct_dependencies` reads `requires_dist` from the PyPI JSON the agent already fetches
and caches (skipping optional-extra requirements — not installed by a plain `pip install`, so
counting their advisories would manufacture risk the package doesn't ship), and
`_scan_transitive_dependencies` checks each direct dependency at its current latest release through
the same cached OSV path used for the package itself, capped at 12 deps, one level deep — full-tree
resolution belongs to pip, not a scanner with an evaluation budget. Scoring is conservative and
evidence-gated: a COMPLETE scan finding a CRITICAL/HIGH advisory in a direct dep caps the parent's
`vuln_score` at 0.5/0.7 respectively; a partial scan (any dep lookup failed) reports findings in
notes but never scores them — unknown is not evidence, the same rule this agent applies everywhere
else. New `SecurityResult` fields (`transitive_advisory_count`, `transitive_worst_severity`,
`transitive_scan_status`) with backward-compatible defaults; findings surface as `🧬` notes.

**Verified:** five unit tests against the real `_compute_security_result` (HIGH transitive →
capped 0.7; CRITICAL → 0.5; partial scan → no penalty + explicit "NOT scored" note; clean scan →
untouched score; extras-filtering and dedup against a real `Requirement`-parsed payload), plus a
live end-to-end check against real PyPI/OSV: `slack-sdk` correctly reports zero direct
dependencies (its 1.00 was already honest), and a genuinely failed dep lookup in a restricted
network correctly degraded to partial-unscored rather than penalizing on incomplete data. Existing
suite 25/25. **Not yet observed changing a live winner selection** — by design it only moves
scores when a complete scan finds real HIGH/CRITICAL transitive advisories.

### 9.9p Fix 24 — support packages were never installed in the verifier's own environment

**Context:** the full log of the pre-Fix-22/23 run. Its headline is 0/30 verified, but its structure
is the best evidence yet that the pipeline's decision half now works: 13/30 cases selected the
correct PyMigBench target (selection rate 7% → 46% vs earlier runs), Connector ran 8 full sessions
(7 wrappers injected in one projectalice case alone), and every produced patch died at exactly one
wall — historical baseline reproduction. Accounting for all 15 `environment_incomplete` outcomes:
ruamel ×3 and gevent ×1 are Fix 22's exact targets; nwb ×7, projectalice ×6, petisco ×2, and
archery ×1 are Fix 23's exact targets (all landed after this run started, so none were exercised);
py-evm ×1 is a genuine Python-3.5 target-resolution ceiling; and talkpython ×1 was this new bug.

**Root cause:** Fix 17 correctly classifies undeclared external imports (`bs4` in a
requirements-less tutorial repo) as installable support packages, and the *profiler's* measurement
venv has always installed them in its Layer 2 — but `create_verification_environment`, the
authoritative baseline/target sandbox that actually decides `environment_incomplete`, installed
only base requirements + source + targets. Support packages were passed in (`needed_modules`) but
used solely for narrowing. Net effect, live: talkpython survived profiling, AXIOM selected the
correct target, the drop-in patch was produced — and the baseline import smoke failed on a bare
`ModuleNotFoundError: No module named 'bs4'` because nothing ever installed it in the one
environment that counts. The same asymmetry Fix 5–6 fixed for the narrowing retry ("only the
profiler's copy got the fix"), recurring for support installation.

**Fix:** `create_verification_environment` now installs `needed_modules` after source/targets —
skipping stdlib and anything already covered by source/target, using the same policy as the
profiler's support path (historical pin when declared, bare name resolved via the alias table when
not — `bs4` → `beautifulsoup4` — with Fix 16's same-major fallback), and recording genuine failures
in `failures` so the fail-closed contract is unchanged.

**Verified:** three unit tests against the real function reproducing the exact talkpython shape:
undeclared `bs4`/`colorama` now installed (with correct alias mapping to `beautifulsoup4`); a
genuine support install failure still blocks with a clear reason; source/target never
double-installed as support. Existing suite 25/25. **Not yet live-confirmed.**

**Known remaining walls, honestly:** (1) target-version resolution ignores the planned
interpreter's `Requires-Python` — throat resolved `gevent==26.7.0` for an env whose Python can't
take it, and py-evm's 3.5 resolution 404s; picking the newest *compatible* release at security-
resolution time is the right fix and a larger change, not yet started. (2) Some
`no_migration_selected` outcomes are sincere scoring judgments, not bugs — e.g. kafka→confluent-
kafka where the LLM's API-fit rating for confluent-kafka's genuinely-different callback API pulls
its total below kafka's; PyMigBench scores that a miss, but "AXIOM optimizes on evidence" and
"PyMigBench expects this specific historical migration" are different objectives, worth an explicit
discussion in any writeup rather than silent tuning until the benchmark passes.

### 9.9o Fix 23 — the restore rebuilt under the wrong interpreter, and narrowing refused to narrow to zero

**Context:** the run after Fix 22, plus the user's correct observation that Docker Desktop shows no
AXIOM containers. On Docker first: that's the cheapest-first ladder working as designed, not a
malfunction — local interpreters are tried before uv, uv before Docker, and this machine now has
real local 3.11/3.10/3.9/3.8 (left by earlier uv runs), so the ladder never gets past step 1.
Docker remains wired but unexercised on this machine as long as local candidates exist.

**Bug A — Fix 20's restore restored the *last failed candidate*, not the original interpreter.**
The restore called `_create_disposable_venv(env_dir, python)` where `python` is env_dir's own
`bin/python` — a symlink whose target changes with every tier-4 rebuild. By restore time it pointed
at the last candidate tried (3.8), and Fix 20's realpath resolution dutifully resolved it "outside
env_dir"… to uv's Python 3.8. The live log proves the consequence twice over: pip inside the
"restored" venv only offered PyQt6 releases up to 6.7.1 (6.8+ require Python ≥3.9 and were
silently filtered), so the resolved target `pyqt6==6.11.0` was invisible and every candidate died
with `No matching distribution found` — and the pyqt5 probe failed on
`importlib.metadata.packages_distributions`, an API added in Python 3.10. Every candidate was
marked `hard_install_failure` (a candidate-specific status Fix 21 correctly still vetoes on) for a
version-skew reason that had nothing to do with the candidates. Fixed by capturing
`os.path.realpath(python)` at `_install_base_layer` entry — before any tier-4 rebuild re-targets
the symlink — and restoring under that captured original, never the stale env_dir path.

**Bug B — an empty narrowed set was treated as "narrowing unavailable."** The nwb-conversion-tools
GUI files import only `PyQt5` and project-local modules — they need *nothing* from the project's
declared base requirements. `_narrow_base_specs_to_needed_imports` correctly returned `[]`, but the
guard `if narrowed_specs and len(...) < len(...)` treated empty as falsy and fell through to
installing the entire historical requirements — including `pyalsaaudio`, which needs Linux-only
ALSA headers (`alsa/asoundlib.h`) and can never build on macOS under any interpreter — burning the
full four-interpreter tier-4 ladder per case on packages the file under test never imports. Now an
empty narrowed set skips the base layer entirely and succeeds: one pip call instead of the whole
ladder, and the case proceeds to candidate installation where the actual migration evidence lives.
(If a project-local import transitively needs one of the skipped packages, the verifier's baseline
import smoke test will say so honestly — which is strictly more informative than dying in the base
layer on a package the file never touches.)

**Verified:** three unit tests against the real `_install_base_layer`. Bug A's exact shape — enter
with env_dir's venv python symlinked to the original 3.12, tier 4 rebuilds under a 3.8 candidate
(re-targeting the symlink exactly as live), all candidates fail — confirms the restore rebuild uses
the original 3.12, not 3.8. Bug B's exact shape (pyalsaaudio failing, `needed_modules` empty)
confirms success after a single pip call with no widening/tier-4 ladder. Ordinary non-empty
narrowing confirmed unchanged. Existing suite 25/25. **Not yet live-confirmed** — the
nwb-conversion-tools cases (24–29, all the same project) are the direct beneficiaries: their
candidate installs should now run under the real 3.12, where `pyqt6==6.11.0`/`pyside6==6.11.1`
resolve normally.

### 9.9n Fix 22 — a transitive dependency's historical pin could strangle every install retry, and gevent-style Cython failures never triggered tier 4

**Context:** the first run after Fixes 20/21 was structurally the best yet — environments stopped
crashing, winners flipped to the correct PyMigBench targets (`eventlet`, `pyyaml`), and the
gevent→eventlet case became the first ever to complete a full Connector ACI session and produce a
complete patched source. The remaining wall is baseline reproduction: the verifier must install the
*historical* source package, and two distinct failure shapes blocked that in the live log.

**Root cause 1 (cloud-custodian ×3, ruamel.yaml→pyyaml):** the failing build was never
`ruamel.yaml` itself — pip's own error names `ruamel.yaml.clib`, a compiled transitive dependency
whose 2020-era pin the project's requirements carry and the constraints file faithfully forces into
every install. That old clib has no wheel for macOS-arm64/Python 3.12, pip falls back to a C build,
and `cc` fails on pre-modern-clang code. Every existing retry tier widens the package *being
installed* and re-runs against the same constraints — so the strangling pin survived every tier.
Modern clib (same major, 0.2.x) ships wheels and satisfies the same requirement.

**Root cause 2 (flask-socketio, gevent→eventlet):** `gevent==1.0.1` (2014) fails to build with
`Cython.Compiler.Errors.CompileError: src/gevent/libev/corecext.pyx` — the exact ancient-native-
build failure class tier 4's older-interpreter retry exists for, but the trigger heuristic
(`distutils` / `ccompiler` / `cython_sources`) matched none of that error text, so tier 4 silently
never fired.

**Fix:** (1) new `_widen_failing_transitive_constraint`: parses which distribution actually failed
out of pip's own stable error shapes (`Failed building wheel for X`, `╰─> X`), and — only when that
distribution is NOT the requested package — widens its `==` pin in the constraints file to the
same-major range (`_same_major_version_fallback`, the identical policy every other fallback uses).
Wired as a last tier into both `_pip_install_source_pin` and the profiler's support-package install
path (Fix 16's branch), retrying the EXACT historical pin first — best baseline fidelity: the real
ruamel case installs at its exact 0.16.10 once the clib constraint admits a wheel-shipping release
— then the same-major fallback. Deliberately never applied when the failing distribution IS the
requested package (its own fallbacks cover that, and widening a constraint can't fix a package
whose every same-major release fails to build). (2) `_LEGACY_BUILD_FAILURE_SIGNATURES` extended
with `.pyx` (the file extension only Cython builds emit in errors) and `cython.compiler` so
gevent-style failures now trigger tier 4's interpreter fallback where it's wired.

**Verified:** five unit tests. The live ruamel error text widens exactly the clib pin (requested
package's pin and unrelated pins untouched); the requested package's own failure never triggers
widening; missing-file/unparsable-detail cases are clean no-ops; gevent's live `.pyx` error now
matches the tier-4 heuristic while the three original signatures still match and `mysql_config not
found` (a genuine system-library gap, not a legacy-build case) still doesn't; and an end-to-end
run through the real `_pip_install_source_pin` with a fake pip that fails while the old clib pin
is present and succeeds once widened confirms the source installs at its exact historical pin with
the widening note recorded. Existing suite 25/25. **Not yet live-confirmed** — same standing
caveat; gevent 1.x may genuinely fail to build under every available interpreter on arm64 even
with tier 4 now firing, in which case that case's honest ceiling is `environment_incomplete`
until the Docker path (Fix 15) can provide an x86/linux build environment for it.

### 9.9l Fix 20 — the restore step deleted its own base interpreter (the real cause of the live `[Errno 2]` wall)

**Context:** the very next live run after Fix 19 showed the identical `[Errno 2]` symptom — proving
Fix 19's retry never even engaged. The log's error status was the tell: packages were marked
`hard_environment_failure` (the profiler's blanket `except Exception` path), not Fix 19's
`hard_environment_restore_failure` — meaning the exception *escaped* `_install_base_layer` entirely
rather than being caught by the retry loop.

**Root cause — a three-part interaction:** (1) tier 4's restore calls
`_create_disposable_venv(env_dir, python)` where `python` is `env_dir/bin/python`, the venv's own
interpreter *inside the directory about to be wiped*; (2) Fix 13's explicit wipe (added to cure the
ELOOP corruption) deletes that interpreter before `subprocess.run([base_python, "-m", "venv", ...])`
ever launches it, raising `FileNotFoundError` — before Fix 13, `venv --clear` deleted contents only
after the subprocess had already loaded the binary, which is why this restore pattern used to work
by accident; (3) `FileNotFoundError` is an `OSError`, and Fix 19's retry caught only `RuntimeError`,
so it escaped on the first attempt, propagated through `_install_base_layer` (no try there), and hit
the profiler's blanket handler — every package falsely marked `hard_environment_failure` with the
raw errno. This only surfaced now because tier 4's candidate loop had never actually *run and
exhausted* real local interpreters on this machine before: the earlier `uv python install` runs left
`~/.local/bin/python3.11/3.10/3.9/3.8` behind, so this run was the first to reach the restore path
at all.

**Fix, two layers:** `_create_disposable_venv` now refuses to use a base interpreter that lives
inside the directory it is about to wipe — it resolves the real base via `os.path.realpath` (a
venv's `bin/python` is a symlink to its base on POSIX), falls back to `pyvenv.cfg`'s documented
`home` key for copies-based venvs, and falls back to the default interpreter if both fail; it also
resolves outside-env symlinks to their real targets so a new venv never chains to a
soon-to-be-deleted link. Separately, Fix 19's retry now catches `(RuntimeError, OSError)` so any
similar escape is contained as a clear restore failure instead of a fake per-package one.

**Verified:** real reproduction with zero mocks on the venv machinery — built a genuine venv, then
called `_create_disposable_venv` with that venv's own `bin/python` as the base (exactly tier 4's
restore call shape). Before the fix this raises `FileNotFoundError`; after, it returns a real,
working interpreter (confirmed by executing it). Also confirmed an ordinary outside-base rebuild is
unaffected. Full existing test suite: 25/25 + 69/69 passing (the 2 pre-existing failures are in
files untouched all session).

### 9.9m Fix 21 — winner selection: infrastructure failures no longer masquerade as migration decisions, and a startup microbenchmark no longer outvotes the migration literature's actual criteria

**Context:** the same live run showed AXIOM selecting "keep the original" on nearly every case, for
two reasons visible directly in the log. First, the environment crash (Fix 20) marked every
candidate `hard_environment_failure`, and `_is_hard_profile_failure`'s `startswith("hard_")` check
then vetoed every candidate *except the original* — originals are exempt from the profiler veto by
construction — so each infrastructure hiccup was systematically converted into a confident-looking
"keep the original" decision (`slackclient` winning at total 0.434 with API 0.0 in the log is this
exact pattern). Second, in the one case with a fully working environment (`kafka` →
`confluent-kafka`), the target lost purely on **import time**: 20ms vs 166ms (confluent-kafka
front-loads librdkafka at import), weighted at 0.35 — a one-time startup microbenchmark
single-handedly outvoting clean security (1.00 vs 0.72 with 2 CVEs) and perfect API compatibility.

**Research grounding:** He et al., "A Multi-Metric Ranking Approach for Library Migration
Recommendations" (SANER 2021) ranks migration targets by combining independent evidence dimensions
drawn from real-world migration histories — not runtime microbenchmarks. PyMigBench's own paper
(Islam et al., MSR 2023) frames why real migrations happen: libraries become obsolete, unmaintained,
or insecure. Package-level deprecation studies in the Python ecosystem make the same point:
staleness/abandonment is a first-class migration driver. Nothing in this literature supports import
time as a primary selection criterion.

**Fix, three parts, all in `axiom_agent.py`:** (1) `_is_hard_profile_failure` now distinguishes
candidate-specific evidence (`hard_install_failure`, `hard_probe_failure`,
`hard_target_resolution_failure`, `hard_unsupported_candidate` — these still veto, correctly) from
environment-wide failures (`hard_environment_failure`, `hard_environment_plan_failure`,
`hard_environment_restore_failure` — these no longer veto anyone: they're missing evidence about
every candidate equally, and the verifier's own sandbox remains the real gate). (2) Import-only
speed evidence now carries `W_SPEED_IMPORT_ONLY = 0.10` instead of the full `W_SPEED = 0.35`;
a real executed runtime probe keeps full weight — the change is scoped precisely to the
startup-microbenchmark case. (3) A new `W_MAINTENANCE = 0.15` dimension sourced from the security
agent's *existing* per-candidate `freshness_score` (PyPI release recency) — previously this signal
was only 30%-diluted into the security overall, too weak to matter; it is now a first-class
dimension, with the same strict comparability rule as every other dimension (omitted entirely
unless real evidence exists for every candidate — never a manufactured neutral score).

**Verified:** five unit tests replaying the exact cases from the live log through the real
`_compute_scores`. The petisco slackclient→slack-sdk case with both candidates marked
`hard_environment_failure`: winner flips from slackclient (the old forced fallback) to slack-sdk
(1.00 vs 0.664, decided on real security+maintenance evidence). A genuine candidate-specific
`hard_install_failure` still vetoes correctly — no regression in the honest case. The kafka case
with the log's exact numbers (20.1ms/165.9ms imports, 4270/547KB memory, 0.72/1.00 security):
winner flips from kafka to confluent-kafka (0.874 vs 0.795) via the import-only downweight alone.
The maintenance dimension is recorded in the per-candidate evidence trail. The runtime-probe path
is untouched (evidence label confirms the change is scoped to `import-only`). Full suite: 25 + 69
passing, same 2 pre-existing unrelated failures as before the change. **Not yet confirmed on a live
re-run** — these tests prove the selection logic now behaves as designed on the exact observed
inputs, not that the affected cases will reach `Verified exact PyMigBench migration`; Connector and
Verifier still have to do their jobs downstream once a migration is actually selected.

### 9.9k Fix 19 — tier 4's restore step could return a dangling interpreter path

**Context:** a live 30-case re-run on the user's own machine, the first time tier 4's older-
interpreter fallback (Fix 12/15) actually had real local candidates to cycle through
(`~/.local/bin/python3.11/3.10/3.9/3.8`, left behind by an earlier run's `uv python install`). The
user reported "the environments are not working at all" — multiple cases in a row showed every
candidate package failing with `[Errno 2] No such file or directory` on a path inside the
profiler's own temp `env_dir`, immediately after all four fallback interpreters were tried and none
produced an explicit success message.

**Root cause:** tier 4 rebuilds the *same* `env_dir` up to five times per case — once per candidate
(3.11 → 3.10 → 3.9 → 3.8), then a final rebuild to restore the original 3.12 interpreter if none of
the four worked. That restore called `_create_disposable_venv` exactly once, caught a `RuntimeError`
if it failed, logged the error into `detail` — and then returned the *original, pre-restore* `python`
string regardless. That string is `env_dir/bin/python`, the same path for every interpreter that
ever lived in `env_dir`; the moment the final restore's own `_create_disposable_venv` call wiped
`env_dir` (its first step, before creating anything) and then failed to create a working venv there,
that path stopped pointing to anything real. Every subsequent install/probe call against the
returned `python` then failed with a generic filesystem error that looked like a probe-specific
problem rather than what it actually was: the case's entire environment no longer existed. Five
rapid wipe-and-rebuild cycles against one directory in quick succession — exactly the access pattern
Fix 13 already flagged as fragile — is a plausible trigger (a lingering file handle from the just-
failed pip install, a filesystem timing hiccup), though the precise OS-level cause wasn't
reproducible in a single-Python sandbox; the returned-path contract was broken regardless of the
exact trigger.

**Fix:** the restore step now retries itself up to three times with a short pause between attempts,
verifying with `os.path.isfile` that each attempt actually produced a real interpreter before
trusting it — self-healing the transient case instead of returning a dangling path after the first
failure. If all three attempts still fail, the function returns an explicit, checkable `""` instead
of the stale original path, with `failures["base-requirements"]` stating plainly that the
environment could not be restored. Both callers (`_profile_in_temporary_venv`, the profiler's
measurement venv, and `create_verification_environment`, the authoritative baseline/target sandbox
that actually decides `environment_incomplete`) now check for this signal immediately after calling
`_install_base_layer` and short-circuit cleanly — marking every candidate/package with one clear
`hard_environment_restore_failure` reason instead of attempting further installs against a
nonexistent interpreter and producing a wall of per-package `[Errno 2]` messages.

**Verified:** four unit tests directly against the real functions. Reproduced a transient restore
failure (fails once, succeeds on retry) and confirmed the function self-heals and returns a real,
usable interpreter; reproduced total restore failure (fails all three attempts) and confirmed the
function returns `""` with a clear failure message instead of a dangling path; confirmed both
`_profile_in_temporary_venv` and `create_verification_environment` detect an unusable `python` and
return immediately without a single wasted `_pip_install`/`_pip_check` call. **Not yet confirmed
whether this actually resolves the specific cases from the live run** (`petisco` ×2, `throat`) on a
fresh re-run — the fix makes the restore self-heal and fail loudly instead of silently, which is a
real improvement either way, but whether those specific cases' base installs succeed once the
environment itself stops being the problem depends on their own dependencies, same standing caveat
as every fix this session.

### 9.9i Fix 17 — undeclared external imports were silently assumed to be local files

**Context:** prompted by a design question about whether AXIOM should adopt `johnnydep`
(MigrateLib's package-metadata inspection tool). `johnnydep` resolves *distribution → import
names*; the real bug found by working through the question needed the reverse direction (*import
name → distribution name*), which pointed at a deeper problem than a missing tool: `output30.log`'s
`talkpython/async-techniques-python-course` case (`requests`→`httpx`) failed with a plain
`ModuleNotFoundError: No module named 'bs4'` at the baseline import smoke test, even though `bs4`
is a completely ordinary, resolvable PyPI package.

**Root cause:** `_declared_support_packages` decided whether an imported module needed a `pip
install` using only one signal — whether it (or its alias) appeared in the project's own
declared-requirements text. Anything not found there fell through to "local" (assumed to be a
sibling file the checkout would supply), unconditionally, with no check of whether a file by that
name actually existed anywhere in the checkout. Old/informal PyMigBench repos routinely import
third-party packages — `bs4`, `colorama`, `websocket` all hit this in a single 30-case run — without
ever declaring them in a requirements.txt at all, so each got silently reclassified as local, never
installed, and failed three stages later with an error that looked like an unrelated environment
problem rather than the actual cause.

**Fix:** added `_module_exists_in_project(module, project_root)` — a bounded walk of
`verification_project_root` (the same historical checkout `_project_overlay` already copies
wholesale for verification, so this is a real filesystem check against the exact snapshot the case
runs against, not a guess) looking for `module.py` or `module/__init__.py` at any level, skipping
noise directories (`.git`, `venv`, `__pycache__`, etc.) so a vendored copy inside a virtualenv can't
produce a false local match. `_declared_support_packages` now uses this as the tiebreaker: not
declared *and* a real project file exists → still local, unchanged from before; not declared *and*
no such file exists → now attempted as an external package (best-effort, via the same alias table
and unpinned-latest fallback every other support package already uses), instead of assumed local by
default. Separately, and independent of this fix, expanded `IMPORT_TO_DISTRIBUTION` from 10 to 33
entries using well-established, unambiguous import/distribution mismatches (the same kind of list
tools like `pipreqs` curate) — a cheap, deterministic, zero-network, zero-new-dependency
improvement, deliberately conservative about excluding anything ambiguous rather than guessing.

**Verified:** three unit tests directly against `_declared_support_packages` using a real temporary
project directory. Reproduced the exact case shape (`bs4`/`websocket` undeclared and genuinely
external, `colorama` declared, `engine`/`keylib` genuinely local files) and confirmed the split is
now correct; confirmed a vendored `bs4` copy inside a `venv/` subdirectory is correctly ignored, not
mistaken for a real local module; confirmed the safer default when no `project_root` is available at
all (an old call site, or a genuinely empty value) is to attempt an install rather than silently
assume local. **Not yet confirmed on a live re-run.**

### 9.9j Fix 18 — import rewriting now preserves comments and formatting (LibCST)

**Context:** the same design question about MigrateLib raised its use of LibCST instead of
regex/AST-based rewriting, specifically to avoid changing anything but the intended lines. Checking
AXIOM's own `core/ast_rewrite.py` against that standard found a real, verifiable gap: every call to
`apply_import_replacements` ended in `ast.unparse(new_tree)`, which regenerates the *entire* file
from the abstract syntax tree — comments, docstrings, exact whitespace, and quote style are not part
of that tree, so this discarded them file-wide on every patch, not just on the lines that changed. A
migration that silently reformats an unrelated hundred lines is a worse comparison against
PyMigBench's own minimal-diff ground truth even when semantically correct, and makes it harder for a
human to see what AXIOM actually changed.

**Fix:** rewrote `core/ast_rewrite.py`'s import-rewriting path (`ImportRewriter` and
`apply_import_replacements`) on LibCST — Meta's format-preserving concrete syntax tree, the same
tool class MigrateLib's own paper uses for this exact reason. Every one of Fix 14's original
behaviors is preserved (dead-import dropping via `referenced_names`, submodule-prefix rewriting,
wildcard-import exemption, `module.attr` usage rewriting, `import X` dedup after a rename collision)
— `apply_import_replacements`'s signature and contract are unchanged, so none of its five call sites
(`connector_agent.py`, `axiom_agent.py`, `cli.py`, `feedback_loop.py` ×2) needed to change. New:
untouched comments, docstrings, blank lines, and even unusual comma spacing in an unmodified
multi-name import now survive byte-for-byte, and only a statement whose name list actually shrank
(an unused name genuinely dropped) has its comma placement normalized — there's no "original"
spacing left to preserve for a name that no longer exists. `rewrite_simple_attrs_to_dataclass`
(the separate `attr`→`dataclass` transform in the same file) deliberately stays on stdlib `ast`: it
rewrites whole class bodies, not import lines, so there's little original formatting to preserve
either way, and its validation-heavy walk fits `ast`'s simpler node model better. Added `libcst>=1.1`
to `pyproject.toml`'s core dependencies.

**A real bug found and fixed during this port, not present in the original `ast` version:** LibCST
represents a dotted import path (e.g. `kafka.errors`) using the exact same `Attribute`/`Name` node
types it uses for an ordinary attribute-access *expression* elsewhere in code (`ast.ImportFrom`'s
`module` is a plain string, with no such overlap) — so the module-attribute usage rewriter
(`leave_Attribute`) fired on the import statement's own module path during LibCST's normal
bottom-up child traversal, rewriting it to the new name *before* the statement-level rewrite/dedup
logic (`leave_ImportFrom`/`leave_Import`) ever ran. By the time that logic checked "did the module
change," it already had — so it silently no-op'd and returned every name unfiltered, including ones
that should have been dropped as unused. Caught directly by a real automated test (`from
kafka.errors import KafkaError, KafkaTimeoutError` with only `KafkaError` referenced elsewhere
returned both names, not just the referenced one) rather than by inspection — traced by re-running
with instrumented `leave_ImportFrom`/`leave_Attribute` calls until the actual call order and each
method's inputs were visible, confirming the module string was already rewritten by the time the
dedup/filter logic ran. Fixed by having `ImportRewriter` refuse to descend into `Import`/`ImportFrom`
subtrees at all (`visit_Import`/`visit_ImportFrom` returning `False`) — the statement-level methods
already rewrite the dotted path directly via string manipulation, so the generic attribute walk has
nothing correct left to do inside an import statement and only risked racing ahead of it.

**Verified:** eight unit tests directly against `apply_import_replacements`, re-running all four of
Fix 14's original scenarios (dead-import drop, still-referenced rewrite preserved, mixed used/dead
names in one statement, wildcard-import exemption) against the new implementation and confirming
identical outcomes, plus four new tests specific to the format-preservation capability itself:
comments/docstrings/blank-lines/odd-trailing-comment-spacing surviving byte-for-byte around a
changed import; unusual (non-standard) comma spacing preserved on a multi-name import when nothing
was dropped; `module.attr` usage rewriting still working correctly now that it's properly scoped
away from import statements; and `import X` dedup after two originals collide on the same rename
target. Also confirmed `rewrite_simple_attrs_to_dataclass` is unaffected, and that a genuinely
invalid-syntax input raises an exception (`libcst.ParserSyntaxError`, not `SyntaxError`) that every
real call site's existing broad `except Exception` still catches correctly, so error-handling
behavior at every caller is unchanged. **Not yet confirmed on a live re-run** — this proves the
rewrite is now both behaviorally equivalent to Fix 14 and format-preserving, not that any specific
case's patch now scores differently; PyMigBench's diff/scoring semantics themselves weren't touched
in this change.

### 9.9c Fix 10 regenerated a "no difference" result — and a real bug it introduced

**Symptom, found in a real 30-case re-run right after Fix 10 landed:** the headline numbers
looked unchanged, which read as "nothing improved." Two separate things were going on, and only
one of them is actually about whether the fixes work.

First, the run itself never reached the cases Fix 10 targets. It was invoked with
`--max-cases 30`, not `50` — PyMigBench's case ordering is deterministic, and
project-alice-assistant / nwb-conversion-tools / upstox-python (the cases that actually hit the
numpy/distutils/3.12 failure Fix 10 fixes) are all past case 30. Comparing this run's headline
rate to the earlier 50-case run's rate was never a fair test of Fix 10 — it's a test of the first
30 cases only, most of which were never broken by that failure class. 27 of 30 case outcomes
were identical to the equivalent 30-case slice of the prior run (confirmed by direct diff), which
is exactly what you'd expect from a fix that only touches a failure class none of these 30 cases
hit.

Second — and this is a real bug this run surfaced — 3 of the 30 cases (petisco ×2, throat)
*changed*, and not for the better: they flipped from `environment_incomplete` in the prior run to
AXIOM declining to migrate at all (`Nothing to verify — AXIOM selected no package replacement`),
driven by a spurious `hard_install_failure` veto on the migration source package itself
(`slackclient`, `eventlet`). Root cause: `_create_disposable_venv` runs
`python -m venv --clear env_dir` — the venv lives directly in `env_dir`, not a subdirectory, and
`--clear` deletes everything already there first. Tier 4 (§9.9b) calls this function to rebuild
the venv under an older interpreter candidate, and — critically — calls it *again* to restore the
original interpreter whenever the loop doesn't return early, including when
`_older_local_interpreters` finds zero candidates and the loop body never executes at all (no
console message, so this was invisible in the log). Both calls silently deleted
`axiom-base-constraints.txt`, which `_base_specs_and_constraints` had written to that same
directory before tier 1 ever ran. `constraints_path` — the in-memory string — kept pointing at
the now-deleted file, so the next `--constraint <path>` pip invocation (installing the migration
source itself, in `_pip_install_source_pin`) failed with pip's generic
`Could not open requirements file: [Errno 2] No such file or directory`, which the profiler then
misreported as an ordinary install failure and vetoed the candidate over — corrupting an
unrelated, previously-working code path as a side effect of a tier investigating a completely
different package.

This also explains why Fix 10's own tier never printed its "retrying the whole base layer under
X" message anywhere in either re-run: the eval machine most likely has no interpreter installed
older than the one already in use (`_older_local_interpreters` returns `[]`), so the loop body
that prints that message never executes — but the corrupting restore call still does, on every
case whose build failure happens to match the intentionally broad
`_looks_like_legacy_build_incompatibility` substring check (`distutils`, `ccompiler`,
`cython_sources`), whether or not that failure has anything to do with Python 3.12 specifically.
`aiohttp`/`greenlet`-style native-extension build failures under old pins can trip that same
substring check for unrelated reasons.

**Fix:** regenerate the constraints file (re-run `_base_specs_and_constraints`, same inputs, same
path, idempotent) immediately after every `_create_disposable_venv` call inside tier 4 — both the
per-candidate attempt before its `_pip_install_many` call, and the final restore before returning
failure to the caller.

**Verified:** two targeted unit tests against the actual fixed code. One reproduces the exact
zero-older-interpreters, legacy-signature, restore-only path and confirms the constraints file
exists and holds correct content on disk afterward (previously would have been missing — traced
directly to the `_create_disposable_venv` implementation, which deletes `env_dir`'s contents via
`venv --clear`). The second reproduces the successful-candidate path with a fake
`_create_disposable_venv` that actually wipes the directory (mirroring real `--clear` behavior)
and confirms the candidate's own install call receives a constraints file that exists, where
before the fix it would have received a path to a file the immediately-preceding venv rebuild had
just deleted. **Not yet confirmed on a live re-run** — same caveat as everything else in this
section; the next full re-run is what actually proves this end to end.

### 9.9 What's *still not* fixed

- The Docker backend (§9.8) fixes the interpreter-availability half of the 12
  `target_version_unresolved` cases but has not been proven end-to-end on real Docker — see
  its "not verified" note above. A historical project's own dependencies still have to
  actually install and run inside a 15+-year-old Python 2.7 container, which can surface new,
  different failures (ancient sdist-only packages needing period-correct system libraries)
  that a modern Python 3 sandbox never hits.
- `rules_agent`/`rules_engine.py` remain disconnected from the compiled graph (§1) — not a
  bug, but worth knowing if you were expecting the README's OS-loader table to describe the
  live pipeline exactly.
- The `resolver_miss` / `no_migration_selected` cases were not individually investigated —
  today's fixes target `target_infeasible` misclassification, the `environment_incomplete`
  majority, the wrapper call-site gap, and unbounded historical requirement pins
  specifically, not every non-verified outcome.
- §9.4's fix adds one PyPI network call per unpinned base requirement per case (cached by
  package name across a run). For a full run this is a modest number of extra requests, but
  it does add latency versus the previous zero-network requirement recovery.
- throat (missing system `cairo`)/archery (missing system `mysql_config`) from §9.7 group 1
  are only fixed by narrowing when the migrated file doesn't need the failing package; if it
  does, no Python-level fix installs a missing OS library — same underlying limit as Docker
  above.
- userge-plugins' `spamwatch` import and one isolated pylint/bandit tool-venv failure (§9.7
  group 3) were not resolved — too low-frequency (1/50 each) in this sample to justify a
  systemic fix versus individual triage.
