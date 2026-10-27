"""
agents/axiom_agent.py  (UPDATED — security scoring + token tracking)
──────────────────────
Same decision logic as before, with two additions:

  1. Security penalty: packages with HIGH/CRITICAL CVEs get a score penalty
     proportional to their OSV overall_score. Mirrors CodeAugur's multi-signal
     evidence combination (assembly + traces + rules → combined score).

  2. Token tracking: records input/output tokens for each LLM call
     (API compat scoring) to the token_tracker JSONL log.
"""

from __future__ import annotations
import json
import time
from rich.console import Console
from rich.table import Table
from smart_loader.core.llm_factory import _get_llm
from smart_loader.core.llm_json import parse_llm_json, coerce_llm_text
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from smart_loader.core.ast_rewrite import apply_import_replacements
from smart_loader.core.constraints import filter_crypto_candidates
from smart_loader.core.state import (
    EquivalenceGroup, BenchmarkResult, RuleResult,
    LoadDecision, AgentEvent, LoaderState,
)
from smart_loader.core.telemetry import save_session

console = Console()


def _profiled_import_module(benchmark: BenchmarkResult | None) -> str | None:
    """Return the distribution's real import root discovered in the venv.

    Distribution and import names are not interchangeable (``pycryptodome``
    imports as ``Crypto``).  The profiler records this metadata after asking
    importlib.metadata, which is more reliable than lower-casing a PyPI name.
    """
    if not benchmark:
        return None
    note = benchmark.error or ""
    prefix = "import module: "
    if not note.startswith(prefix):
        return None
    # A shared-root or mixed-environment benchmark is deliberately
    # neutralized for scoring, but metadata still tells Connector the real
    # Python import spelling (for example ``Crypto`` for pycryptodome).
    return note[len(prefix):].split(";", 1)[0].strip() or None


# Statuses that are real, candidate-specific evidence: THIS package failed
# to install/resolve/probe in an otherwise working sandbox. These justify
# excluding the candidate from selection.
_CANDIDATE_SPECIFIC_HARD_FAILURES = frozenset({
    "hard_install_failure",
    "hard_probe_failure",
    "hard_target_resolution_failure",
    "hard_unsupported_candidate",
})

# Statuses where the *environment itself* failed — plan not runnable, a
# blanket sandbox exception, or the venv restore dying. These hit every
# candidate in the case identically (including the original, which only
# escapes the veto because the veto never applies to originals) and say
# nothing about any specific candidate's viability.
_ENVIRONMENT_WIDE_HARD_FAILURES = frozenset({
    "hard_environment_failure",
    "hard_environment_plan_failure",
    "hard_environment_restore_failure",
})


def _is_hard_profile_failure(benchmark: BenchmarkResult | None) -> bool:
    """Whether an isolated profiler disproved THIS candidate's availability.

    ``available=False`` alone is deliberately not enough: an intentionally
    neutralized mixed-environment measurement and a host-environment miss are
    not evidence that a candidate cannot work.  The profiler tags genuine
    isolated install/probe/environment failures explicitly.

    Environment-wide failures (``hard_environment_*``) are deliberately NOT
    treated as candidate evidence anymore. A real 30-case run exposed the
    systematic bias the old ``startswith("hard_")`` check produced: a venv
    infrastructure failure marked *every* candidate hard-failed, the veto
    below then excluded every candidate *except the original* (originals are
    exempt from the veto by construction), and AXIOM concluded "keep the
    original" — turning each environment hiccup into a false
    ``no_migration_selected`` outcome. An environment that couldn't be built
    is missing evidence about everyone equally, not proof the migration
    target is worse than the source; the candidate stays eligible, its
    performance dimensions are honestly omitted (the existing unmeasured
    path), and the verifier's own sandbox remains the real gate.
    """
    status = str(getattr(benchmark, "profile_status", "") or "") if benchmark else ""
    return status in _CANDIDATE_SPECIFIC_HARD_FAILURES


def _has_real_import_measurement(benchmark: BenchmarkResult | None) -> bool:
    """Whether a candidate completed a real import timing probe.

    This is deliberately narrower than ``BenchmarkResult.available``.  A
    neutralized mixed environment and a placeholder with no timing must not
    be treated as feasibility evidence.  The caller additionally has to opt
    in via ``allow_profile_feasibility`` before treating this as an isolated
    feasibility signal; that prevents normal host profiling from promoting a
    package merely because it happens to be installed on the operator's
    machine.
    """
    return bool(
        benchmark
        and benchmark.available
        and getattr(benchmark, "profile_status", "") == "measured"
        and isinstance(getattr(benchmark, "import_time_ms", None), (int, float))
        and benchmark.import_time_ms > 0
    )


def _isolated_feasibility_candidate(
    candidates: list[str],
    original: str,
    benchmarks: dict[str, BenchmarkResult],
    *,
    allow_profile_feasibility: bool,
) -> str | None:
    """Return one uniquely install-and-import feasible alternative, if safe.

    A successful isolated import proves only that the target distribution can
    be installed and imported in the reconstructed environment.  It does not
    prove API compatibility, so this fallback is intentionally narrow:

    * it is disabled unless the caller used isolated profiling;
    * the original must *not* have an equally real import measurement, or the
      ordinary comparative scorer should make the choice; and
    * exactly one alternative may have the evidence.  Multiple alternatives
      without a genuine comparison remain an honest "no selection" rather
      than an arbitrary first-candidate choice.

    The resulting decision is always LOW confidence and is routed through
    Connector and Verifier before it can be accepted.
    """
    if not allow_profile_feasibility or _has_real_import_measurement(benchmarks.get(original)):
        return None
    supported = [
        candidate for candidate in candidates
        if candidate != original and _has_real_import_measurement(benchmarks.get(candidate))
    ]
    return supported[0] if len(supported) == 1 else None


# ── Scoring weights ────────────────────────────────────────────────────────────
# Multi-metric combination in the spirit of He et al.'s library-migration
# ranking work ("A Multi-Metric Ranking Approach for Library Migration
# Recommendations", SANER 2021): rank targets by combining independent
# evidence dimensions, weighted by how much each actually says about
# migration quality. PyMigBench's own framing (Islam et al., MSR 2023) is
# that real-world migrations happen because libraries become obsolete,
# unmaintained, or insecure — which is why maintenance and security carry
# real weight here, and why a startup microbenchmark must not.
W_SPEED        = 0.35   # real *runtime-probe* performance evidence (full weight)
W_SPEED_IMPORT_ONLY = 0.10  # import-time-only evidence: a one-time startup
                            # cost, not runtime behavior. A real 30-case run
                            # showed the old flat 0.35 letting a 146ms import-
                            # time difference (kafka 20ms vs confluent-kafka
                            # 166ms, which front-loads librdkafka) single-
                            # handedly outvote clean security + perfect API
                            # fit and keep an unmaintained source library.
W_MEMORY       = 0.10   # memory_kb
W_API_MATCH    = 0.15   # LLM compat
W_SECURITY     = 0.20   # OSV security score (1.0 = clean, 0.0 = critical CVEs)
W_MAINTENANCE  = 0.15   # freshness_score from the security agent's existing
                        # PyPI release-recency evidence — the "is this library
                        # still alive" dimension the migration literature
                        # identifies as a primary real-world migration driver.
                        # Already computed per candidate; previously only
                        # diluted 30% into the security overall, too weak to
                        # matter (slackclient 0.76 vs slack-sdk 1.00 total).


_COMPAT_PROMPT = """You are a Python API compatibility expert.
Rate each package's drop-in compatibility for the specific methods listed under the given role.

Do not estimate runtime performance. AXIOM records runtime performance only
from an executed, fixed-fixture profiler probe.

Return ONLY a JSON object:
{
  "compatibility": {
    "package_name": <float 0.0_to_1.0>  # 1.0 = perfect drop-in, 0.5 = minor adaptation needed, 0.0 = incompatible
  }
}
No markdown, no explanation."""


def _score_api_compat(
    candidates: list[str],
    used_apis:  list[str],
    role:       str,
    provider:   str,
    model:      str,
    session_id: str = "unknown",
    source_file: str = "",
) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    if not used_apis:
        return None, None
    try:
        llm = _get_llm(provider, model)
        t0  = time.time()
        resp = llm.invoke([
            SystemMessage(content=_COMPAT_PROMPT),
            HumanMessage(content=f"Role: {role}\nMethods used: {used_apis}\nCandidates: {candidates}"),
        ])
    except Exception as exc:
        console.print(f"[yellow]⚠️  API compatibility evidence unavailable: {exc}[/yellow]")
        return None, None
    latency_ms = (time.time() - t0) * 1000

    # Token tracking
    try:
        from smart_loader.core.token_tracker import record_usage
        usage = getattr(resp, "usage_metadata", None) or {}
        record_usage(
            session_id=session_id,
            agent="axiom",
            model=model,
            provider=provider,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            source_file=source_file,
            latency_ms=latency_ms,
        )
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        console.print(f"[dim]  API compat tokens — input: {inp:,}  output: {out:,}  latency: {latency_ms:.0f}ms[/dim]")
    except Exception:
        pass

    raw = coerce_llm_text(resp.content).strip()
    data = parse_llm_json(raw, expect=dict, fallback=None)
    if not isinstance(data, dict):
        console.print(
            f"[yellow]⚠️  API compat scoring: could not extract JSON from "
            f"{provider}/{model} response — omitting API compatibility evidence for this group.[/yellow]"
        )
        return None, None

    compat = data.get("compatibility", {})
    try:
        # A partial model answer is evidence for neither side of a comparison.
        # Do not fill missing candidates with a friendly-looking default.
        compat_out = {c: float(compat[c]) for c in candidates}
    except (KeyError, TypeError, ValueError):
        console.print(
            "[yellow]⚠️  API compatibility response was incomplete; omitting its weight rather than filling defaults.[/yellow]"
        )
        return None, None
    if any(value < 0.0 or value > 1.0 for value in compat_out.values()):
        console.print("[yellow]⚠️  API compatibility response had invalid ranges; omitting its weight.[/yellow]")
        return None, None
    # Preserve the former tuple shape for callers/tests, but never return a
    # model-estimated runtime factor. Runtime is evidence only when the
    # profiler executed a fixed semantic probe.
    return compat_out, None


def _measured_runtime_scores(
    candidates: list[str],
    benchmarks: dict[str, BenchmarkResult],
 ) -> tuple[dict[str, float] | None, str | None]:
    """Return a score only for a probe measured for *every* candidate."""
    if len(candidates) < 2:
        return None, None
    probe_sets = []
    for candidate in candidates:
        benchmark = benchmarks.get(candidate)
        if not benchmark or not benchmark.available:
            return None, None
        probe_sets.append({
            probe_id for probe_id, value in benchmark.api_runtimes.items()
            if isinstance(value, (int, float)) and value > 0
            and benchmark.runtime_probes.get(probe_id, {}).get("status") == "measured"
        })
    common_probes = set.intersection(*probe_sets) if probe_sets else set()
    if not common_probes:
        return None, None
    # Stable choice keeps report rows reproducible if a group has >1 valid
    # shared probe.  All candidates use the exact same fixture/probe id.
    probe_id = sorted(common_probes)[0]
    times = {candidate: benchmarks[candidate].api_runtimes[probe_id] for candidate in candidates}
    min_time = min(times.values()) or 0.001
    return {candidate: min_time / timing for candidate, timing in times.items()}, probe_id


def _compute_scores(
    group:            EquivalenceGroup,
    benchmarks:       dict[str, BenchmarkResult],
    security_results: dict,
    provider:         str,
    model:            str,
    session_id:       str = "unknown",
    source_file:      str = "",
    verification_failures: dict | None = None,
    allow_profile_feasibility: bool = False,
    mandate_target: str = "",
    require_substitution: bool = False,
) -> tuple[str, str, dict[str, dict], list[str], list[str]]:
    """
    Returns (winner, confidence, scores, crypto_vetoed, not_installed).

    crypto_vetoed lists candidates that were excluded specifically because
    group.crypto_required is True and they're non-cryptographic hash
    packages (see core/constraints.py) — surfaced so the caller can record
    *why* a package never had a chance to win, not just that it lost.

    The final list contains candidates profiler_agent could not measure
    comparably in this environment. They remain eligible—an unavailable
    profiler is not proof a migration is impossible—but every unavailable
    performance dimension is omitted, never replaced by a neutral or
    model-generated number. A winner without comparable profiling evidence
    is LOW confidence and must still pass the target verification sandbox.

    verification_failures: {role: [candidate, ...]} — candidates verifier_agent
    already tried and rejected as a DECISION_LEVEL failure for this role on a
    prior pass through this node (see agents/verifier_agent.py). Excluded
    from consideration up front so a retry doesn't re-pick the same loser.
    Passed explicitly (rather than read from `state` inside this helper) to
    keep this function's dependencies explicit, consistent with the rest of
    this module.

    allow_profile_feasibility: permits one deliberately bounded fallback for
    an isolated run: when no comparative score exists, the original has not
    been import-measured, and exactly one alternative completed a real
    isolated install/import probe, select that alternative at LOW confidence.
    This records feasibility, not compatibility; Connector and Verifier still
    have to prove that the migrated code works.
    """
    candidates = list(group.candidates)
    original = getattr(group, "original", None) or group.candidates[0]

    verification_failures = verification_failures or {}
    excluded_by_verifier = set(verification_failures.get(group.role, []))
    if excluded_by_verifier:
        before = list(candidates)
        candidates = [c for c in candidates if c not in excluded_by_verifier]
        if len(candidates) != len(before):
            console.print(
                f"[yellow]  Verifier veto: excluded {', '.join(excluded_by_verifier & set(before))} "
                f"from '{group.role}' (failed verification on a prior pass)[/yellow]"
            )

    pre_veto_candidates = list(candidates)
    candidates = filter_crypto_candidates(group, candidates)
    crypto_vetoed: list[str] = []
    if group.crypto_required:
        crypto_vetoed = [c for c in pre_veto_candidates if c not in candidates]
        if crypto_vetoed:
            console.print(
                f"[yellow]  Crypto veto: excluded {', '.join(crypto_vetoed)} "
                f"from '{group.role}'[/yellow]"
            )

    # A candidate that failed to install or import in AXIOM's disposable
    # sandbox is not merely "not installed locally".  It is ineligible for
    # selection.  Keep the original as a safe fallback if *everything* failed
    # so AXIOM never turns an unreproducible environment into an automatic
    # migration recommendation.
    profile_vetoed = [
        candidate for candidate in candidates
        if candidate != original and _is_hard_profile_failure(benchmarks.get(candidate))
    ]
    if profile_vetoed:
        candidates = [candidate for candidate in candidates if candidate not in profile_vetoed]
        details = "; ".join(
            f"{candidate}: {benchmarks[candidate].profile_status}"
            for candidate in profile_vetoed if candidate in benchmarks
        )
        console.print(f"[yellow]  Profiler veto: excluded {details}[/yellow]")

    if not candidates:
        # Every candidate has been vetoed by the crypto/verifier checks —
        # fall back to the ORIGINAL package (the one with existing active
        # call sites), not just group.candidates[0], since after a verifier
        # exclusion that position may now hold a different, also-excluded
        # candidate.
        return original, "LOW", {}, crypto_vetoed, []

    # Performance signals are usable only if the same isolated profiler
    # measured *every* candidate in this comparison.  A missing candidate,
    # a shared-import-root collision, or a failed probe is evidence we do not
    # have—not a reason to manufacture a friendly 0.5 placeholder.
    benchmarked = [
        candidate for candidate in candidates
        if benchmarks.get(candidate) and benchmarks[candidate].available
    ]
    unmeasured = [candidate for candidate in candidates if candidate not in benchmarked]
    performance_comparable = bool(candidates) and not unmeasured
    if unmeasured:
        detail = ", ".join(
            f"{candidate} ({getattr(benchmarks.get(candidate), 'profile_status', 'not profiled')})"
            for candidate in unmeasured
        )
        console.print(
            "[dim]  Performance evidence omitted for this group; no real, comparable isolated "
            f"measurement exists for: {detail}.[/dim]"
        )

    import_s: dict[str, float] | None = None
    if performance_comparable:
        times = {candidate: benchmarks[candidate].import_time_ms for candidate in candidates}
        if all(isinstance(value, (int, float)) and value > 0 for value in times.values()):
            min_time = min(times.values())
            import_s = {candidate: min_time / value for candidate, value in times.items()}
        else:
            console.print("[dim]  Import-time evidence omitted: at least one measured timing was invalid.[/dim]")

    runtime_s, runtime_probe_id = _measured_runtime_scores(candidates, benchmarks)

    # The LLM may assess API compatibility, but it is never allowed to
    # provide a synthetic runtime number.  ``None`` means that dimension has
    # no evidence and is omitted from the weighted total.
    api_s, _ = _score_api_compat(
        candidates, group.used_apis, group.role,
        provider, model, session_id, source_file,
    )

    mem_s: dict[str, float] | None = None
    if performance_comparable:
        mems = {candidate: benchmarks[candidate].memory_kb for candidate in candidates}
        if all(isinstance(value, (int, float)) and value > 0 for value in mems.values()):
            min_mem = min(mems.values())
            mem_s = {candidate: min_mem / value for candidate, value in mems.items()}
        else:
            console.print("[dim]  Memory evidence omitted: at least one measured value was invalid.[/dim]")

    speed_s: dict[str, float] | None = None
    speed_evidence: str | None = None
    if runtime_s is not None and import_s is not None:
        speed_s = {
            candidate: 0.2 * import_s[candidate] + 0.8 * runtime_s[candidate]
            for candidate in candidates
        }
        speed_evidence = f"runtime:{runtime_probe_id}+import"
    elif runtime_s is not None:
        speed_s = dict(runtime_s)
        speed_evidence = f"runtime:{runtime_probe_id}"
    elif import_s is not None:
        speed_s = dict(import_s)
        speed_evidence = "import-only"

    # Security is only comparable when evidence exists for every candidate in
    # the group.  Giving an unscanned package a numeric "neutral" score would
    # silently reward missing data over a package with a real advisory.
    security_evidence_by_candidate: dict[str, bool] = {}
    sec_s: dict[str, float | None] = {}
    for c in candidates:
        sec = security_results.get(c)
        has_evidence = (
            sec is not None
            and getattr(sec, "data_status", "available") == "available"
            and isinstance(getattr(sec, "overall_score", None), (int, float))
        )
        security_evidence_by_candidate[c] = has_evidence
        sec_s[c] = float(sec.overall_score) if has_evidence else None
    security_comparable = bool(candidates) and all(security_evidence_by_candidate.values())
    if not security_comparable and security_results:
        console.print(
            "[dim]  Security evidence is incomplete for this group; omitting the security weight "
            "rather than treating missing data as a score.[/dim]"
        )

    # Maintenance evidence: the security agent's freshness_score (PyPI
    # release recency) per candidate. A separate dimension from the security
    # overall it's 30%-diluted into, because library staleness is a
    # first-class migration driver in its own right (He et al. SANER 2021;
    # PyMigBench's obsolete/unmaintained framing), not merely a security
    # tiebreaker. Same comparability rule as every other dimension: omitted
    # entirely unless real evidence exists for every candidate.
    maint_s: dict[str, float | None] = {}
    for c in candidates:
        sec = security_results.get(c)
        freshness = getattr(sec, "freshness_score", None) if sec is not None else None
        maint_s[c] = (
            float(freshness)
            if security_evidence_by_candidate.get(c) and isinstance(freshness, (int, float))
            else None
        )
    maintenance_comparable = bool(candidates) and all(
        maint_s[c] is not None for c in candidates
    )

    api_comparable = api_s is not None and all(candidate in api_s for candidate in candidates)
    if not api_comparable and group.used_apis:
        console.print(
            "[dim]  API compatibility evidence is incomplete for this group; omitting the API weight.[/dim]"
        )

    # Weighted total is dynamically re-normalized to the evidence that really
    # exists.  In particular, no ``availability`` or estimated-runtime number
    # is used as a substitute for an executed measurement.
    scores = {}
    for c in candidates:
        speed    = speed_s.get(c) if speed_s is not None else None
        mem      = mem_s.get(c) if mem_s is not None else None
        api      = api_s.get(c) if api_comparable and api_s is not None else None
        security = sec_s[c] if security_comparable else None
        maintenance = maint_s[c] if maintenance_comparable else None
        components: list[tuple[float, float]] = []
        if speed is not None:
            # Import-only timing is a one-time startup cost, not runtime
            # behavior — a fraction of the weight a real executed runtime
            # probe earns. See W_SPEED_IMPORT_ONLY's definition for the
            # real case where the flat weight chose an unmaintained source
            # over a clean, API-compatible successor on import time alone.
            speed_weight = W_SPEED_IMPORT_ONLY if speed_evidence == "import-only" else W_SPEED
            components.append((speed_weight, float(speed)))
        if mem is not None:
            components.append((W_MEMORY, float(mem)))
        if api is not None:
            components.append((W_API_MATCH, float(api)))
        if security is not None:
            components.append((W_SECURITY, float(security)))
        if maintenance is not None:
            components.append((W_MAINTENANCE, float(maintenance)))
        total_weight = sum(weight for weight, _value in components)
        total = (
            sum(weight * value for weight, value in components) / total_weight
            if total_weight else 0.0
        )
        scores[c] = {
            "speed":        round(float(speed), 3) if speed is not None else None,
            "speed_evidence": speed_evidence,
            "runtime_probe": runtime_probe_id,
            "runtime_evidence": runtime_s is not None,
            "memory":       round(float(mem), 3) if mem is not None else None,
            "memory_evidence": mem_s is not None,
            "availability": 1.0 if c in benchmarked else None,
            "api_match":    round(float(api), 3) if api is not None else None,
            "api_evidence": api_comparable,
            "security":     round(float(security), 3) if security is not None else None,
            "security_evidence": security_comparable,
            "maintenance":  round(float(maintenance), 3) if maintenance is not None else None,
            "maintenance_evidence": maintenance_comparable,
            "evidence_weight": round(total_weight, 3),
            "total":        round(total, 3),
            "installed":    c in benchmarked,
            "profile_status": str(getattr(benchmarks.get(c), "profile_status", "not_profiled")),
            "import_measured": _has_real_import_measurement(benchmarks.get(c)),
        }

    any_evidence = any(scores[c]["evidence_weight"] > 0 for c in candidates)
    selection_basis = "weighted_comparative_evidence"

    # ── Mandated migration ──────────────────────────────────────────────────
    # AXIOM normally *chooses* the best library. Some evaluations instead
    # *require* a specific one: the Proteus corpus states the substitution as
    # the requirement, not as a suggestion. Under optimisation semantics AXIOM
    # correctly declined nearly all of them — zlib imports in 0.8ms against
    # fastcrc's 7.1ms, so keeping zlib is the better engineering call — and
    # every program came back byte-identical, passing its tests as a no-op and
    # scoring as a 100% success. That number would be meaningless.
    #
    # When mandated, the target wins by instruction and the selection basis
    # says so, so no reader can mistake a requirement for a judgement. The
    # experiment then measures what it means to measure: whether the connector
    # and verifier can *execute* a substitution, not whether AXIOM agrees with
    # it. Off by default — normal runs keep optimisation semantics.
    mandated = str(mandate_target or "").strip()
    mandated_match = next(
        (c for c in candidates if c.casefold() == mandated.casefold()), ""
    ) if mandated else ""

    # ``require_substitution`` is the third mode, and it exists because the
    # other two do not express what the migration corpus actually asks.
    #
    #   mandate_target="httpx"   the answer is given; AXIOM does not choose
    #   neither                  optimisation semantics; AXIOM may decline
    #   require_substitution     AXIOM chooses WHICH, but not WHETHER
    #
    # Dropping the mandate to "free the resolver" silently selected mode two,
    # and mode two declines almost everything: every candidate scores 1.0 on a
    # corpus this small, and the tie-break below prefers ``c == original``, so
    # the incumbent wins every tie. The measured result was gen_success 36/36
    # with lib_swapped 5/36 — a 100% success rate built out of byte-identical
    # no-ops, exactly as the comment above this block warned.
    #
    # The requirement is "remove the dependency on X". Which library replaces
    # it is AXIOM's judgement and stays free; whether to bother is not a
    # judgement the task leaves open. Excluding the incumbent from the pool
    # encodes that distinction, and an empty pool becomes an honest refusal
    # rather than a silent no-op.
    pool = candidates
    if require_substitution and not mandated_match:
        pool = [c for c in candidates if c.casefold() != original.casefold()]
        if not pool and profile_vetoed:
            # The profiler vetoed every alternative, so the pool is empty and
            # the incumbent would win by default -- producing a byte-identical
            # no-op that then PASSES, because an unmodified program passes its
            # own tests by construction. Seven proteus rows spent 6-14 LLM
            # calls reaching exactly this state and were recorded as
            # successes.
            #
            # A ``hard_install_failure`` says the sandbox could not install
            # the candidate. That is a fact about our environment, not
            # evidence the library is unfit -- the same conflation that made
            # six of eight single_shot_repair failures spurious, where every
            # "unavailable" package installed fine on a plain pip install.
            # The comment above _is_hard_profile_failure already reasons this
            # way for environment-WIDE failures; the candidate-specific case
            # has the identical failure mode once it empties the pool.
            #
            # So the vetoed alternatives come back as a last resort. The
            # Connector still has to write a working patch and the Verifier
            # still has to accept it -- the real gate, as that comment says --
            # and a candidate the verifier can reject is strictly better than
            # a no-op nothing can reject.
            pool = list(profile_vetoed)
            for candidate in pool:
                scores.setdefault(candidate, {"total": 0.0})
            console.print(
                f"[yellow]  Substitution required and every alternative was "
                f"profile-vetoed; reinstating {', '.join(pool)} as unverified "
                f"candidates rather than recording a no-op[/yellow]")
        if not pool:
            # Genuinely nothing to substitute WITH: the resolver returned only
            # the incumbent. Still not a success -- it is a refusal, and the
            # basis says so so the runner can record it as one rather than
            # letting an unmodified file pass its own tests.
            # ``unmeasured``, not ``not_installed``: no such name exists in this
            # scope, so reaching this refusal raised NameError instead of
            # returning it. run_axiom_arms catches the exception into a note and
            # records the row with a falsy state, which reads as converged=N and
            # a byte-identical no-op. Three proteus_full runs -- C3 r3, D3 r1,
            # D3 r2 -- were lost to it, reported as "nonconvergence" with zero
            # repair cycles, which is exactly what a crash on this line looks
            # like from the outside.
            return (original, "LOW",
                    {original: {**scores.get(original, {}),
                                "selection_basis": "no_alternative_candidate"}},
                    crypto_vetoed, unmeasured)

    if mandated_match:
        winner = mandated_match
        selection_basis = "mandated_by_caller"
    elif any_evidence:
        # The ``c == original`` tie-break is dropped under require_substitution:
        # the incumbent is not in the pool, and keeping it would silently
        # re-privilege whichever candidate happened to share its name.
        winner = (max(pool, key=lambda c: scores[c]["total"])
                  if require_substitution else
                  max(pool, key=lambda c: (scores[c]["total"], c == original)))
        if require_substitution:
            selection_basis = "free_choice_substitution_required"
    else:
        # Do not turn missing metrics into a made-up numeric score.  The one
        # permitted exception is a *unique* successful isolated install/import
        # measurement for an alternative while the source lacks the same
        # evidence.  It gives Connector/Verifier a real candidate to examine,
        # but it never claims the candidate is API-compatible.
        feasibility_winner = _isolated_feasibility_candidate(
            candidates,
            original,
            benchmarks,
            allow_profile_feasibility=allow_profile_feasibility,
        )
        if feasibility_winner:
            winner = feasibility_winner
            selection_basis = "isolated_import_feasibility"
        elif require_substitution:
            # No comparative evidence, but a substitution is required and a
            # real alternative exists. Picking the best-scoring alternative is
            # honest ONLY if the basis says the evidence was thin — recorded
            # here so the row is auditable and never reads as a measured win.
            winner = max(pool, key=lambda c: scores[c]["total"])
            selection_basis = "substitution_required_evidence_thin"
        else:
            winner = original
            selection_basis = "insufficient_comparative_evidence"

    # Keep the reason beside the winning score so downstream consumers do not
    # have to reverse-engineer a selection from a table of absent metrics.
    # The values are deliberately JSON-safe primitives because evaluator and
    # report code serialize decisions directly.
    for candidate, candidate_score in scores.items():
        candidate_score["selection_basis"] = selection_basis if candidate == winner else None
        candidate_score["feasibility_selected"] = bool(
            candidate == winner and selection_basis == "isolated_import_feasibility"
        )

    if len(candidates) == 1 or not any_evidence or not performance_comparable:
        confidence = "LOW"
    else:
        scores_list = sorted([scores[c]["total"] for c in candidates], reverse=True)
        gap = scores_list[0] - scores_list[1]
        confidence = "HIGH" if gap > 0.15 else "MEDIUM"

    if winner in unmeasured:
        confidence = "LOW"

    return winner, confidence, scores, crypto_vetoed, unmeasured


def _decision_evidence_record(
    group: EquivalenceGroup,
    *,
    original: str,
    winner: str,
    confidence: str,
    scores: dict[str, dict],
    benchmarks: dict[str, BenchmarkResult],
    crypto_vetoed: list[str],
    verification_failures: dict,
    base_environment_failures: dict | None = None,
) -> dict[str, object]:
    """Create a serializable audit record for one AXIOM decision.

    The score table is useful to a human, but a blank cell used to make it
    impossible for evaluation/reporting code to distinguish "not profiled",
    "vetoed", and "lost a real comparison".  This record keeps those facts
    separate without inserting synthetic evidence into the scorer.
    """
    base_environment_failures = base_environment_failures or {}
    environment_failures = {
        str(name): str(detail)[:500]
        for name, detail in base_environment_failures.items()
    }
    winner_score = scores.get(winner, {})
    selection_basis = str(winner_score.get("selection_basis", ""))
    if not selection_basis:
        selection_basis = "all_candidates_vetoed" if not scores else "original_retained"

    if selection_basis == "isolated_import_feasibility":
        selection_reason = (
            "The only alternative with a successful isolated install/import measurement was selected. "
            "This is feasibility evidence only; Connector and Verifier must still establish API correctness."
        )
    elif selection_basis == "weighted_comparative_evidence" and winner != original:
        selection_reason = "Selected by the available, comparable evidence weights."
    elif selection_basis == "weighted_comparative_evidence":
        selection_reason = "The original package had the strongest available comparative evidence."
    elif selection_basis == "all_candidates_vetoed":
        selection_reason = "No candidate remained eligible after policy, verifier, or profiler vetoes."
    elif environment_failures:
        selection_reason = (
            "No migration was selected because the reconstructed base environment is incomplete; a target "
            "import in that partial environment is not sufficient migration evidence."
        )
    else:
        selection_reason = (
            "No migration was selected because there was no sufficient comparable evidence and no uniquely "
            "feasible isolated alternative."
        )

    verifier_vetoes = set(verification_failures.get(group.role, []))
    candidate_evidence: list[dict[str, object]] = []
    declines: list[dict[str, object]] = []
    for candidate in dict.fromkeys(group.candidates):
        benchmark = benchmarks.get(candidate)
        score = scores.get(candidate, {})
        profile_status = str(getattr(benchmark, "profile_status", "not_profiled"))
        profile_error = str(getattr(benchmark, "error", "") or "")[:500]
        candidate_evidence.append({
            "candidate": candidate,
            "in_scoring_pool": candidate in scores,
            "profile_status": profile_status,
            "import_measured": _has_real_import_measurement(benchmark),
            "hard_profile_failure": _is_hard_profile_failure(benchmark),
            "verifier_vetoed": candidate in verifier_vetoes,
            "crypto_vetoed": candidate in crypto_vetoed,
            "evidence_weight": score.get("evidence_weight"),
            "score_total": score.get("total"),
            "profile_error": profile_error or None,
        })

        # The source/original is the baseline, not a declined migration
        # candidate.  Record only alternatives AXIOM did not select.
        if candidate == original or candidate == winner:
            continue
        if candidate in verifier_vetoes:
            code = "verifier_decision_veto"
            detail = "Verifier previously rejected this candidate at decision level."
        elif candidate in crypto_vetoed:
            code = "crypto_policy_veto"
            detail = "Candidate does not meet the role's cryptographic requirement."
        elif _is_hard_profile_failure(benchmark):
            code = "hard_profile_failure"
            detail = profile_status
        elif candidate not in scores:
            code = "candidate_not_eligible"
            detail = "Candidate was removed before scoring."
        elif selection_basis == "weighted_comparative_evidence" and score.get("evidence_weight", 0) > 0:
            code = "lower_comparative_score"
            detail = f"score={score.get('total')}, winner_score={winner_score.get('total')}"
        elif not _has_real_import_measurement(benchmark):
            code = "no_real_import_measurement"
            detail = profile_status
        elif environment_failures:
            code = "base_environment_incomplete"
            detail = "; ".join(f"{name}: {reason}" for name, reason in environment_failures.items())
        elif selection_basis == "insufficient_comparative_evidence":
            code = "insufficient_comparative_evidence"
            detail = "A successful import alone was not enough to choose among the remaining candidates."
        else:
            code = "lower_comparative_score"
            detail = f"score={score.get('total')}, winner_score={winner_score.get('total')}"
        declines.append({"candidate": candidate, "code": code, "detail": detail})

    return {
        "role": group.role,
        "original": original,
        "winner": winner,
        "outcome": "selected_alternative" if winner != original else "kept_original",
        "confidence": confidence,
        "selection_basis": selection_basis,
        "selection_reason": selection_reason,
        "selected_score_total": winner_score.get("total"),
        "base_environment_failures": environment_failures,
        "candidate_evidence": candidate_evidence,
        "declines": declines,
    }


def axiom_agent(state: LoaderState) -> dict:
    console.rule("[bold green]🎯 AXIOM — Adaptive eXecution & Import Optimization Module")

    groups:           list[EquivalenceGroup]     = state.get("equivalence_groups", [])
    benchmarks:       dict[str, BenchmarkResult] = state.get("benchmarks", {})
    rule_results:     list[RuleResult]           = state.get("rule_results", [])
    security_results: dict                       = state.get("security_results", {})
    source_code:      str                        = state.get("source_code", "")
    source_file:      str                        = state.get("source_file", "")
    provider:         str                        = state.get("llm_provider", "ollama")
    model:            str                        = state.get("model", "qwen3-coder-next")
    session_id:       str                        = state.get("_session_id", "unknown")
    verification_failures: dict                   = state.get("verification_failures", {}) or {}
    base_environment_failures: dict               = state.get("base_environment_failures", {}) or {}
    verifier_feedback: dict                       = state.get("_last_verifier_feedback", {})
    trace = list(state.get("agent_trace", []))

    console.print(f"[dim]Provider: {provider.upper()} / {model}  |  Session: {session_id}[/dim]")
    if verification_failures:
        console.print(
            f"[yellow]↩ Re-scoring after verifier rejection — excluded candidates: "
            f"{verification_failures}[/yellow]"
        )
    if verifier_feedback and verifier_feedback.get("route") == "axiom":
        feedback_messages = [
            str(item.get("message", item)) if isinstance(item, dict) else str(item)
            for item in verifier_feedback.get("errors", [])
        ]
        console.print(
            f"[yellow]↩ Repair cycle {verifier_feedback.get('cycle', '?')}/"
            f"{verifier_feedback.get('max_cycles', '?')}: AXIOM received verifier evidence — "
            f"{' ; '.join(feedback_messages)[:400]}[/yellow]"
        )

    # Show security warnings for any HIGH/CRITICAL packages in scope
    if security_results:
        for pkg, res in security_results.items():
            if hasattr(res, 'risk_level') and res.risk_level in ("CRITICAL", "HIGH"):
                console.print(f"[bold red]⚠️  Security warning: {pkg} is {res.risk_level} risk "
                               f"(score={getattr(res,'overall_score',0):.2f})[/bold red]")

    trace.append(AgentEvent(stage="axiom", event="started"))

    if not groups:
        console.print("[green]All imports already optimal — no changes needed.[/green]")
        return {
            "decisions":    [],
            "decision_evidence": [],
            "decision_declines": {},
            "patched_code": source_code,
            "done":         True,
            "agent_trace":  trace,
            "messages":     [AIMessage(content="AXIOM: nothing to optimize.")],
        }

    decisions:         list[LoadDecision] = []
    decision_evidence: list[dict]         = []
    replacements:      dict[str, str]     = {}

    # ── Score table — now includes Security + Install columns ──────────────────
    table = Table(title="AXIOM Optimization Decisions", header_style="bold green")
    table.add_column("Role")
    table.add_column("Original")
    table.add_column("Winner")
    table.add_column("Conf.")
    table.add_column("Speed")
    table.add_column("Memory")
    table.add_column("Security")
    table.add_column("API")
    table.add_column("Total")
    table.add_column("Installed?")

    packages_to_install: list[str] = []   # collected across all groups, printed once at the end

    for group in groups:
        original      = getattr(group, "original", None) or group.candidates[0]
        profile_vetoed = {
            candidate: getattr(benchmarks.get(candidate), "profile_status", "")
            for candidate in group.candidates
            if candidate != original and _is_hard_profile_failure(benchmarks.get(candidate))
        }
        winner, confidence, scores, crypto_vetoed, not_installed = _compute_scores(
            group, benchmarks, security_results,
            provider, model, session_id, source_file,
            verification_failures=verification_failures,
            # A successful import is a useful feasibility fact only in a
            # healthy disposable environment.  In normal host profiling it
            # could merely reflect a package the operator happened to have
            # installed; after a base-environment failure it would describe a
            # partial project environment rather than the migration context.
            allow_profile_feasibility=bool(
                state.get("isolated_profiling", False)
                and not base_environment_failures
            ),
            # Empty in normal runs, so optimisation semantics are unchanged.
            mandate_target=str(state.get("mandate_target", "") or ""),
            # False in normal runs, so optimisation semantics are unchanged.
            require_substitution=bool(state.get("require_substitution", False)),
        )
        winner_scores = scores.get(winner, {})
        selection_basis = str(winner_scores.get("selection_basis", ""))
        # Only meaningful when AXIOM is actually recommending a CHANGE — if
        # winner == original, nothing new needs installing, regardless of
        # whether the original itself happened to be unbenchmarkable here.
        winner_unmeasured = (winner in not_installed) and (winner != original)

        # Build rationale including security
        sec_note = ""
        w_sec = security_results.get(winner)
        if (
            w_sec and getattr(w_sec, "data_status", "available") == "available"
            and hasattr(w_sec, 'risk_level') and w_sec.risk_level not in ("LOW",)
        ):
            sec_note = f" [Security:{w_sec.risk_level}]"
        o_sec = security_results.get(original)
        if (
            o_sec and getattr(o_sec, "data_status", "available") == "available"
            and hasattr(o_sec, 'risk_level') and o_sec.risk_level in ("CRITICAL", "HIGH")
        ):
            sec_note += f" [Original {original} has {o_sec.risk_level} CVEs — switched]"

        rationale = (
            f"'{winner}' wins '{group.role}'. "
            f"Confidence: {confidence}. "
            f"Total: {winner_scores.get('total', '?')} "
            f"(speed={winner_scores.get('speed', '?')}, "
            f"mem={winner_scores.get('memory', '?')}, "
            f"sec={winner_scores.get('security', '?')}, "
            f"api={winner_scores.get('api_match', '?')})"
            + sec_note
        )
        if crypto_vetoed:
            rationale += (
                f". Crypto veto: excluded {', '.join(crypto_vetoed)} "
                f"(non-cryptographic hash, but role requires crypto-grade hashing)."
            )
        if profile_vetoed:
            rationale += (
                ". Profiler veto: "
                + ", ".join(f"{candidate} ({status})" for candidate, status in profile_vetoed.items())
            )
        if group.pitfalls:
            rationale += f". Migration note: {group.pitfalls[0]}"
        if winner_unmeasured:
            rationale += (
                f". NOTE: '{winner}' has no comparable isolated profiler result — performance "
                f"dimensions were omitted, not estimated. Ensure requirements.txt contains the target: {winner}"
            )
            packages_to_install.append(winner)
        if selection_basis == "isolated_import_feasibility":
            rationale += (
                ". Feasibility-first selection: this was the only alternative that completed a real "
                "isolated install/import measurement while the original lacked one. This is not API "
                "compatibility proof; Connector and Verifier must validate the patch"
            )
        elif winner == original and selection_basis == "insufficient_comparative_evidence":
            rationale += (
                ". No migration selected: no sufficient comparable evidence and no uniquely feasible "
                "isolated alternative were available"
            )
        elif winner == original and not scores:
            rationale += ". No migration selected: all alternatives were vetoed before scoring"
        if w_sec and getattr(w_sec, "data_status", "available") != "available":
            rationale += ". Security evidence was unavailable and did not affect this ranking"
        if verifier_feedback and verifier_feedback.get("route") == "axiom":
            feedback_summary = "; ".join(
                str(item.get("message", item)) if isinstance(item, dict) else str(item)
                for item in verifier_feedback.get("errors", [])
            )
            if feedback_summary:
                rationale += f". Verifier feedback considered: {feedback_summary[:240]}"

        # Attach used_apis to decision for connector agent
        decision = LoadDecision(
            role=group.role,
            winner=winner,
            original=original,
            score=winner_scores,
            rationale=rationale,
            confidence=confidence,
        )
        # Stash extra fields on the object so connector_agent/telemetry can access them
        decision.__dict__["used_apis"] = group.used_apis
        decision.__dict__["requires_connector"] = group.requires_connector
        decision.__dict__["crypto_vetoed"] = crypto_vetoed
        decision.__dict__["profile_vetoed"] = profile_vetoed
        decision.__dict__["needs_install"] = winner_unmeasured
        candidate_imports = getattr(group, "candidate_imports", {})
        decision.__dict__["original_import"] = candidate_imports.get(
            original, original.replace("-", "_")
        )
        decision.__dict__["winner_import"] = (
            _profiled_import_module(benchmarks.get(winner))
            or candidate_imports.get(winner)
            or winner.replace("-", "_")
        )
        decision.__dict__["selection_basis"] = selection_basis or (
            "all_candidates_vetoed" if not scores else "original_retained"
        )

        decisions.append(decision)
        evidence_record = _decision_evidence_record(
            group,
            original=original,
            winner=winner,
            confidence=confidence,
            scores=scores,
            benchmarks=benchmarks,
            crypto_vetoed=crypto_vetoed,
            verification_failures=verification_failures,
            base_environment_failures=base_environment_failures,
        )
        evidence_record["profiling_mode"] = (
            "isolated" if state.get("isolated_profiling", False) else "host"
        )
        decision_evidence.append(evidence_record)
        if evidence_record["selection_basis"] in {
            "isolated_import_feasibility",
            "insufficient_comparative_evidence",
            "all_candidates_vetoed",
        }:
            console.print(
                f"[dim]  Decision evidence: {evidence_record['selection_reason']}[/dim]"
            )

        if winner != original:
            replacements[original] = winner

        conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(confidence, "white")
        sec_val = (
            winner_scores.get("security", "—")
            if winner_scores.get("security_evidence", False) else "—"
        )
        sec_color = "green" if isinstance(sec_val, float) and sec_val >= 0.75 else "dim"
        winner_actually_installed = bool(winner_scores.get("installed", False))
        table.add_row(
            group.role,
            original,
            f"[green]{winner}[/green]" if winner != original else winner,
            f"[{conf_color}]{confidence}[/{conf_color}]",
            str(winner_scores.get("speed") if winner_scores.get("speed") is not None else "—"),
            str(winner_scores.get("memory") if winner_scores.get("memory") is not None else "—"),
            f"[{sec_color}]{sec_val}[/{sec_color}]",
            str(winner_scores.get("api_match") if winner_scores.get("api_match") is not None else "—"),
            str(winner_scores.get("total", "—")),
            "[green]✓[/green]" if winner_actually_installed else "[yellow]not installed[/yellow]",
        )

    console.print(table)

    # Verifier may later revert a rejected decision to its original package.
    # Keep a plain immutable snapshot of every AXIOM selection so evaluation
    # can distinguish "selected the target but verification rejected it" from
    # "never selected the target".
    attempted_decision_history = list(state.get("attempted_decision_history", []))
    attempted_decision_history.append([
        {
            "role": decision.role,
            "original": decision.original,
            "winner": decision.winner,
            "confidence": decision.confidence,
        }
        for decision in decisions
    ])
    decision_declines = {
        str(record["role"]): list(record["declines"])
        for record in decision_evidence
    }
    decision_evidence_history = list(state.get("decision_evidence_history", []))
    decision_evidence_history.append(decision_evidence)

    if packages_to_install:
        console.print(
            f"\n[bold yellow]⚠ Add to requirements.txt:[/bold yellow] "
            f"{', '.join(sorted(set(packages_to_install)))}"
        )
        console.print(
            "[dim]  Performance dimensions were omitted because those candidates had no comparable "
            "isolated measurement. No estimated values were used; re-run after the profiling environment "
            "is reproducible to obtain real benchmark numbers.[/dim]"
        )

    # ── One replacement per SOURCE package ─────────────────────────────────
    # The resolver can raise two equivalence groups rooted at the same import,
    # and this function then picks a winner for each. Both are coherent as
    # advice and incoherent as an edit: a file imports `pymodbus` once, and it
    # cannot be replaced by two different libraries.
    #
    # Measured on D1 seed 1, which cost 68 LLM calls and $3.27 and shipped
    # nothing. AXIOM emitted::
    #
    #   Modbus TCP client for reading/writing holding registers
    #       pymodbus -> modbus-tk        api 0.1   total 0.702
    #   Modbus client (serial-focused, weak fit for TCP use case)
    #       pymodbus -> minimalmodbus    api 0.0   total 0.606
    #
    # The Connector applied both to the same file in sequence. The first
    # rewired `ModbusTcpClient(...)` to `_axiom_modbustk_client_wrapper`, so
    # the second could no longer find the call site it was written for, defined
    # `_axiom_minimalmodbus_client_wrapper` with nothing calling it, and the
    # uncalled-wrapper guard correctly reverted the whole edit. Every repair
    # cycle then re-ran both and reproduced it -- the run alternated targets
    # four times and ended `migration_success=N` with the source untouched.
    #
    # The losing decision is dropped rather than merged: the second group here
    # scored 0.0 on API fit and its own role text says "weak fit for TCP use
    # case", so there is nothing to preserve. Ties keep the first, which is the
    # order the resolver ranked them in.
    best_by_source: dict = {}
    for d in decisions:
        if d.winner == d.original:
            continue
        total = (d.score or {}).get("total", 0.0) if isinstance(d.score, dict) else 0.0
        kept = best_by_source.get(d.original)
        if kept is None or total > kept[0]:
            best_by_source[d.original] = (total, d)
    superseded = [d for d in decisions
                  if d.winner != d.original
                  and best_by_source.get(d.original, (0, None))[1] is not d]
    if superseded:
        for d in superseded:
            winner = best_by_source[d.original][1].winner
            console.print(
                f"[yellow]  ⚠ {d.original} already replaced by {winner}; "
                f"dropping the lower-scoring decision {d.original} → "
                f"{d.winner} (one replacement per source package).[/yellow]")
        dropped = {id(d) for d in superseded}
        decisions = [d for d in decisions if id(d) not in dropped]

    # ── Compute drop-in vs connector-needed replacements (no mutation yet) ──────
    # NOTE: actual source rewriting now happens in core.graph.apply_decisions,
    # called either immediately (require_approval=False, the old behavior) or
    # after a human approves via the approval_gate node's interrupt() (see
    # core/graph.py). This function only classifies which replacements are
    # drop-in vs connector-needed; it does not mutate source_code itself.
    drop_in_replacements: dict[str, str] = {}
    for d in decisions:
        if d.winner == d.original:
            continue
        group = next((g for g in groups if g.role == d.role), None)
        api_match = d.score.get("api_match") if d.score else None
        # A missing compatibility assessment is not permission to do a
        # drop-in rewrite. Route it through Connector and Verifier instead.
        if not isinstance(api_match, (int, float)):
            api_match = 0.0
        needs_connector = bool(getattr(d, "requires_connector", False) or (group and group.requires_connector))
        if api_match >= 0.95 and not needs_connector:
            original_import = getattr(d, "original_import", d.original.replace("-", "_"))
            winner_import = getattr(d, "winner_import", d.winner.replace("-", "_"))
            drop_in_replacements[original_import] = winner_import

    require_approval = bool(state.get("require_approval", False))

    if require_approval and (drop_in_replacements or replacements):
        # Defer the actual AST rewrite. The approval_gate node (added to the
        # graph only when require_approval=True) will interrupt() here with
        # the full decision list, then call apply_decisions() itself once a
        # human responds. axiom_agent's job stops at "here is what I would
        # do and why" — it never touches source_code in this mode.
        console.print(
            "\n[yellow]⏸  Changes computed — awaiting human approval before "
            "rewriting source (require_approval=True).[/yellow]"
        )
        trace.append(AgentEvent(
            stage="axiom", event="completed_pending_approval",
            detail={"decisions": len(decisions), "drop_in": list(drop_in_replacements.keys())},
        ))
        return {
            "decisions":            decisions,
            "patched_code":         source_code,   # unchanged until approved
            "_drop_in_replacements": drop_in_replacements,
            "attempted_decision_history": attempted_decision_history,
            "decision_evidence":    decision_evidence,
            "decision_declines":    decision_declines,
            "decision_evidence_history": decision_evidence_history,
            "done":                 False,
            "agent_trace":          trace,
            "messages": [AIMessage(
                content="AXIOM: scored " + "; ".join(f"{d.role}→{d.winner}[{d.confidence}]" for d in decisions)
                        + " — awaiting approval"
            )],
        }

    patched_code = source_code
    if drop_in_replacements:
        console.print("\n[bold]Drop-in import patches (no connector needed):[/bold]")
        for orig, repl in drop_in_replacements.items():
            console.print(f"  [red]{orig}[/red]  →  [green]{repl}[/green]")
        try:
            patched_code = apply_import_replacements(source_code, drop_in_replacements)
        except Exception as e:
            console.print(f"[red]AST rewrite failed: {e}[/red]")
            patched_code = source_code
    elif replacements:
        console.print(
            "\n[dim]Replacements deferred to connector agent "
            "(preserving original call sites for analysis).[/dim]"
        )
        for orig, repl in replacements.items():
            console.print(f"  [red]{orig}[/red]  →  [green]{repl}[/green]  [dim](pending)[/dim]")
    else:
        console.print("\n[green]All imports already optimal — no changes needed.[/green]")

    # ── Save telemetry session ─────────────────────────────────────────────────
    trace.append(AgentEvent(
        stage="axiom", event="completed",
        detail={"decisions": len(decisions), "replacements": replacements},
    ))

    # NOTE: telemetry save is deferred to verifier_agent, which now runs
    # after every axiom_agent completion (drop-in-only included) and after
    # every connector_agent completion, and is the only node that knows
    # whether the decisions/patched_code it's looking at are final (verifier
    # can still trigger a retry loop back through here or through connector).
    # Saving here would risk persisting a pre-verification patch.
    console.print("\n[dim]Session save deferred to verifier agent.[/dim]")

    console.print(f"\n[bold green]✓ AXIOM complete — {len(decisions)} decision(s)[/bold green]\n")

    return {
        "decisions":    decisions,
        "attempted_decision_history": attempted_decision_history,
        "decision_evidence": decision_evidence,
        "decision_declines": decision_declines,
        "decision_evidence_history": decision_evidence_history,
        "patched_code": patched_code,
        "done":         True,
        "agent_trace":  trace,
        "messages":     [AIMessage(
            content="AXIOM: " + "; ".join(f"{d.role}→{d.winner}[{d.confidence}]" for d in decisions)
        )],
    }
