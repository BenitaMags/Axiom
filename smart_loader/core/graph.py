"""
core/graph.py
──────────────
Builds the LangGraph StateGraph for AXIOM.

PIPELINE:

  CodeAugur analog        AXIOM node
  ─────────────────────   ─────────────────────────────────────
  [Emulator/Disasm]    →  [parser_agent]      deterministic AST walk
                          [security_agent]    OSV CVE scan + PyPI freshness
  [Stage 1 tool_caller]→  [resolver_agent]    LLM equivalence clustering
  [Stage 2 analyst]    →  [profiler_agent]    subprocess import benchmarks
  [Stage 3 verdict]    →  [axiom_agent]       weighted scoring (rewrite deferred when gated)
                          [approval_gate]     ← NEW: interrupt() pause for human review
                          [connector_agent]   API adapter shim generation

Graph topology:

  [parser]
     │
  [security]   ← OSV + PyPI scan (parallel HTTP, no LLM)
     │
  [resolver]   ← LLM #1: semantic equivalence clustering
     │
     ├──(no groups)──► [END]
     │
  [profiler]   ← subprocess benchmarking
     │
  [axiom]      ← LLM #2: weighted scoring; AST rewrite ONLY if require_approval=False
     │
     ├──(require_approval=True AND replacements exist)──► [approval_gate]
     │                                                          │
     │                                                   interrupt() — pauses graph,
     │                                                   surfaces decisions to caller.
     │                                                   On resume, applies the human's
     │                                                   accept/reject choice via
     │                                                   apply_decisions() BEFORE any
     │                                                   file write happens downstream.
     │                                                          │
     ├──(otherwise, old behavior)──────────────────────────────┤
     │                                                          ▼
  [connector]  ← LLM #3: generate API adapter shims (only when APIs are incompatible)
     │
    [END]

HUMAN-IN-THE-LOOP DESIGN
─────────────────────────
require_approval=True turns on a real LangGraph interrupt() — the graph
execution genuinely suspends (not just "the CLI happens to print a prompt
afterward"). This matters specifically for scan-dir: every file's pipeline
can run all the way through scoring, and the AST rewrite + file write are
held back until a human has seen ALL proposed changes across the whole
directory and explicitly approved them. Nothing gets written to disk
between "AXIOM decided X" and "you said yes."

A MemorySaver checkpointer persists graph state across the interrupt, so
resuming with Command(resume=...) continues exactly where execution paused
rather than re-running the whole pipeline.
"""

from __future__ import annotations
import dataclasses
import shutil
import uuid
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from smart_loader.core.state import LoaderState, LoadDecision, EquivalenceGroup, AgentEvent
from smart_loader.core.feedback_loop import approval_gate_node
from smart_loader.agents.parser_agent    import parser_agent
from smart_loader.agents.resolver_agent  import resolver_agent
from smart_loader.agents.profiler_agent  import profiler_agent
from smart_loader.agents.axiom_agent     import axiom_agent
from smart_loader.agents.security_agent  import security_agent, candidate_security_agent
from smart_loader.agents.connector_agent import connector_agent
from smart_loader.agents.first_party_agent import first_party_agent
from smart_loader.agents.logic_substitution_agent import logic_substitution_agent
from smart_loader.agents.verifier_agent  import (
    cleanup_verification_environment_cache,
    verifier_agent,
)


def _cleanup_disposable_environments(result: dict) -> dict:
    """Delete every per-case disposable environment after a terminal run.

    The profiler's mixed environment and verifier's successful-only cache
    must survive repair loops, because rebuilding the same historical app,
    target, and static-tool environments on every retry is both slow and
    less reproducible.  They have no purpose after a terminal graph result.
    Cleanup is intentionally idempotent for evaluator defensive cleanup.
    """
    if not isinstance(result, dict):
        return result

    env_dir = result.get("profiling_env_dir") if isinstance(result, dict) else None
    if env_dir:
        shutil.rmtree(env_dir, ignore_errors=True)
        result["profiling_env_dir"] = ""
        result["profiling_env_python"] = ""

    cleanup_verification_environment_cache(result.get("verification_environment_cache"))
    result["verification_environment_cache"] = {}
    return result


# Kept for compatibility with callers that used the previous private helper.
def _cleanup_profiling_environment(result: dict) -> dict:
    return _cleanup_disposable_environments(result)


# ── Conditional edges ──────────────────────────────────────────────────────────

def _after_parser(state: LoaderState) -> str:
    if state.get("error"):
        return "end"
    if not state.get("imports"):
        return "end"
    return "continue"


def _after_resolver(state: LoaderState) -> str:
    if state.get("error"):
        return "end"
    if not state.get("equivalence_groups"):
        return "end"
    return "continue"

def _after_axiom(state: LoaderState) -> str:
    """
    Route to connector when replacements need API adapters or aren't drop-in.
    Drop-in-only replacements (and the no-op "nothing to change" case) now
    route to "verifier" instead of straight to END — a superficial rename
    can still change runtime behavior, and verifier_agent's own fast-exit
    (see agents/verifier_agent.py) makes the no-op case cheap: it checks
    patched_code == source_code and returns immediately.
    """
    from smart_loader.core.state import LoadDecision

    decisions: list[LoadDecision] = state.get("decisions", [])
    groups = {g.role: g for g in state.get("equivalence_groups", [])}

    if not decisions:
        return "end"

    for decision in decisions:
        if decision.winner == decision.original:
            continue
        group = groups.get(decision.role)
        api_match = decision.score.get("api_match") if decision.score else None
        # Unknown API compatibility must never be treated as a safe drop-in.
        # It goes through Connector and then the Verifier, rather than
        # allowing a missing/invalid model response to bypass repair.
        if not isinstance(api_match, (int, float)):
            api_match = 0.0
        needs_connector = bool(
            getattr(decision, "requires_connector", False)
            or (group and group.requires_connector)
            or api_match < 0.95
        )
        if needs_connector:
            return "connector"

    return "verifier"


def _after_axiom_gated(state: LoaderState) -> str:
    """
    Like _after_axiom, but when require_approval=True the rewrite hasn't
    happened yet (axiom_agent deferred it) — route through approval_gate
    first regardless of connector need, so a human sees BOTH drop-in and
    connector-needing replacements before anything is written.
    """
    decisions: list[LoadDecision] = state.get("decisions", [])
    has_real_replacement = any(d.winner != d.original for d in decisions)
    if not decisions or not has_real_replacement:
        return "end"
    return "approval_gate"


def _after_axiom_without_connector(state: LoaderState) -> str:
    """Keep the no-Connector graph's conditional-edge contract valid.

    AXIOM deliberately flags unknown/non-drop-in compatibility as needing a
    Connector.  When that optional node is disabled, hand the unchanged
    selection to Verifier instead; Verifier records the no-op and routes back
    to AXIOM with an exclusion rather than emitting an edge that does not
    exist in this graph.
    """
    route = _after_axiom(state)
    return "verifier" if route == "connector" else route


def _after_approval_gate(state: LoaderState) -> str:
    """
    After resuming from interrupt, route based on what the human decided.
    Like _after_axiom, drop-in-only accepted changes now go to "verifier"
    instead of straight to END — the human approved the *decision*, not a
    guarantee the AST rewrite behaves; verifier still checks it.
    """
    if state.get("error"):
        return "end"
    decisions: list[LoadDecision] = state.get("decisions", [])
    groups = {g.role: g for g in state.get("equivalence_groups", [])}
    for decision in decisions:
        if decision.winner == decision.original:
            continue
        group = groups.get(decision.role)
        api_match = decision.score.get("api_match") if decision.score else None
        if not isinstance(api_match, (int, float)):
            api_match = 0.0
        needs_connector = bool(
            getattr(decision, "requires_connector", False)
            or (group and group.requires_connector)
            or api_match < 0.95
        )
        if needs_connector:
            return "connector"
    return "verifier"


def _after_approval_gate_without_connector(state: LoaderState) -> str:
    """Gated counterpart of :func:`_after_axiom_without_connector`."""
    route = _after_approval_gate(state)
    return "verifier" if route == "connector" else route







def _after_verifier(state: LoaderState) -> str:
    """
    Reads the explicit "_verifier_route" field verifier_agent sets on every
    return statement (see agents/verifier_agent.py) rather than inferring
    routing from retry-counter deltas — simpler and less fragile.

    "axiom"     -> retry axiom_agent (verifier excluded a candidate via
                    verification_failures; axiom_agent's OWN existing
                    conditional edges then decide where to go next — through
                    approval_gate again if gated, straight to connector/
                    verifier otherwise — so the retry naturally re-surfaces
                    an updated decision to a human in gated mode).
    "connector" -> retry connector_agent with the concrete error attached
                    (state["_last_connector_error"]).
    "proceed"   -> done. In BOTH gated and non-gated mode this maps to END:
                    in gated mode verifier only ever runs AFTER approval_gate
                    (nothing is patched to verify before a human approves),
                    so by the time we're here the human step is already
                    behind us; in non-gated mode there was no approval step
                    to begin with.
    """
    route = state.get("_verifier_route", "proceed")
    if route == "axiom":
        return "axiom"
    if route == "connector":
        return "connector"
    return "end"


# ── Security-aware resolver ────────────────────────────────────────────────────

def _security_aware_resolver(state: LoaderState) -> dict:
    """
    Wrapper around resolver_agent that annotates high-risk imports with their
    CVE summary so the LLM can factor security posture into equivalence grouping.
    Produces new ImportInfo copies — does not mutate shared state objects.
    """
    security_results = state.get("security_results", {})
    if not security_results:
        return _seeded_or_resolver(state)

    annotated = []
    for imp in state.get("imports", []):
        pkg = imp.module.split(".")[0]
        sec = security_results.get(pkg)
        if sec and sec.risk_level in ("CRITICAL", "HIGH"):
            cve_ids = [v.id for v in sec.vulnerabilities[:2]]
            note    = f"[SECURITY:{sec.risk_level} CVEs:{','.join(cve_ids) or 'none'}]"
            imp = dataclasses.replace(imp, api_calls=list(imp.api_calls) + [note])
        annotated.append(imp)

    new_state = state.copy()
    new_state["imports"] = annotated
    return _seeded_or_resolver(new_state)


def _seeded_or_resolver(state: LoaderState) -> dict:
    """Use an explicit project migration pair when the caller supplied one.

    This is intentionally separate from normal resolver discovery.  A user
    asking AXIOM to migrate ``source -> target`` should not lose the request
    because a discovery model omitted the pair; evaluation can then measure
    discovery and execution as distinct quantities.
    """
    if not state.get("seed_target"):
        return resolver_agent(state)

    source = str(state.get("migration_source", "")).strip()
    target = str(state.get("migration_target", "")).strip()
    if not source or not target:
        return resolver_agent(state)
    source_import = str(state.get("migration_source_import", "")).strip() or source.replace("-", "_")
    used_apis = sorted({
        api for imp in state.get("imports", [])
        if imp.module.split(".")[0].casefold() == source_import.casefold()
        for api in imp.api_calls
    })
    group = EquivalenceGroup(
        role=f"{source} -> {target} (user-specified)",
        candidates=[source, target], used_apis=used_apis,
        reasoning="User supplied this migration pair; resolver discovery was intentionally bypassed.",
        pitfalls=[],
    )
    group.__dict__["original"] = source
    group.__dict__["original_import"] = source_import
    group.__dict__["candidate_imports"] = {
        source: source_import,
        target: target.replace("-", "_"),
    }
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(
        stage="resolver", event="seeded_pair",
        detail={"source": source, "target": target, "used_apis": used_apis},
    ))
    return {"equivalence_groups": [group], "agent_trace": trace}


# ── Graph factory ──────────────────────────────────────────────────────────────

def build_graph(
    enable_security:  bool = True,
    enable_connector: bool = True,
    enable_aci: bool = True,
    reimplement_mode: bool = False,
    require_approval: bool = False,
    isolated_profiling: bool = False,
    enable_verifier: bool = True,
) -> object:
    """
    Build the AXIOM LangGraph pipeline.

    Parameters
    ----------
    enable_security  : include the OSV/PyPI security scan node
    enable_connector : include the API adapter code-generation node
    require_approval : insert the approval_gate interrupt() node before any
                        AST rewrite or connector codegen happens. Requires a
                        checkpointer (added automatically here via
                        MemorySaver) since interrupt()/resume needs durable
                        state across the pause.
    """
    graph = StateGraph(LoaderState)

    # ── Core nodes ─────────────────────────────────────────────────────────────
    graph.add_node("parser",   parser_agent)
    graph.add_node("profiler", profiler_agent)
    graph.add_node("axiom",    axiom_agent)
    # Deliberately no Rules Agent node here.  Host-machine
    # ``is_installed`` checks made migration decisions depend on whatever
    # happened to be installed locally; Profiler/Verifier now reproduce and
    # validate dependencies in disposable case environments instead.

    if require_approval:
        graph.add_node("approval_gate", approval_gate_node)

    # ── Security node (optional) ───────────────────────────────────────────────
    if enable_security:
        graph.add_node("security", security_agent)
        graph.add_node("candidate_security", candidate_security_agent)
        graph.add_node("resolver", _security_aware_resolver)
    else:
        graph.add_node("resolver", _seeded_or_resolver)

    # ── Connector node (optional) ──────────────────────────────────────────────
    if enable_connector:
        graph.add_node("connector", connector_agent)

    # ── First-Party Reimplementation node ──────────────────────────────────────
    # Entered INSTEAD of the LSA/GSA pair when the requirement is to remove a
    # dependency and write its behaviour as first-party code, rather than to
    # swap one library for another. The LSA selects among libraries and has no
    # way to express "write it yourself", so before this node those subjects
    # declined and returned the file untouched on every run.
    #
    # The GSA's editing session is deliberately not on this path. Its job is to
    # bridge an interface delta, and the FRA is constrained to produce none --
    # it preserves the outgoing library's used API surface, so every call site
    # is already correct. An editing pass over a file needing no edits cannot
    # help and can hurt. The four DETERMINISTIC steps the connector performs
    # after its session still run: the FRA calls them directly through
    # migration_finalize.
    if reimplement_mode:
        graph.add_node("fra", first_party_agent)

    # ── Entry point ────────────────────────────────────────────────────────────
    graph.set_entry_point("parser")

    if reimplement_mode:
        # Straight from parsing to reimplementation. Resolver, profiler and
        # AXIOM exist to discover, benchmark and choose a replacement LIBRARY;
        # there is no library to choose here, and running them would spend LLM
        # calls to arrive at "no suitable candidate" -- which is exactly the
        # no-op these subjects produced before.
        graph.add_conditional_edges(
            "parser", _after_parser, {"continue": "fra", "end": END})
    else:
        graph.add_conditional_edges(
            "parser", _after_parser,
            {"continue": "security" if enable_security else "resolver", "end": END},
        )

    if enable_security:
        graph.add_edge("security", "resolver")
        graph.add_conditional_edges(
            "resolver", _after_resolver,
            {"continue": "candidate_security", "end": END},
        )
        graph.add_edge("candidate_security", "profiler")
    else:
        graph.add_conditional_edges(
            "resolver", _after_resolver,
            {"continue": "profiler", "end": END},
        )

    graph.add_edge("profiler", "axiom")

    # ── Verifier node — always present. Cheap no-op when nothing changed
    # (see verifier_agent's "nothing to verify" fast-exit), so there's no
    # meaningful cost to routing every path through it rather than making
    # it another optional flag. ─────────────────────────────────────────────
    graph.add_node("verifier", verifier_agent)

    # ── Logic Substitution node — sits on the edge INTO the verifier, i.e.
    # after connector_agent has produced the patched source and before that
    # source is verified. The migration removes known vulnerabilities; this
    # produces an equivalence-checked isomorphic variant of the result, which
    # hardens against unknown ones. Opt-in via ``enable_isomorph``: with the
    # flag unset the node returns an empty state update without printing or
    # allocating anything, so an ordinary PyMigBench run is byte-identical to
    # one built without it. Routing every verifier entry through it (rather
    # than only the connector edge) means --enable-isomorph also works when
    # the connector is disabled, instead of silently doing nothing. ────────
    graph.add_node("logic_substitution", logic_substitution_agent)
    verifier_entry = "logic_substitution"
    if enable_verifier:
        graph.add_edge("logic_substitution", "verifier")
    else:
        # ── No-verification ablation ────────────────────────────────────────
        # The evaluation protocol's second baseline runs "the agents ... but
        # the VCA and the equivalence oracle disabled (local execute-and-repair
        # only)". VCA is this verifier. With it in the graph the ablation is
        # not an ablation: it was producing byte-identical behaviour to the
        # full system, because the only thing being switched off was an agent
        # that never ran on this corpus.
        #
        # Routing to END here means the Connector's patch is accepted as-is:
        # no acceptance gate, no verifier-driven repair cycles. That is exactly
        # the contrast the paper needs — on B2 the verifier REFUSED to ship an
        # unvalidatable wrapper, and without it that wrapper reaches disk.
        #
        # Off by default. A caller has to ask for this explicitly.
        graph.add_edge("logic_substitution", END)

    if reimplement_mode:
        # FRA -> LMA -> VCA, the same tail every other path takes.
        #
        # This edge was missing on the first live run. LangGraph compiles a
        # node with no outgoing edge perfectly happily and simply terminates
        # after it, so the run produced a patched file, printed no error, and
        # scored well -- while the Logic-Mutation Agent and the Verification
        # Agent never executed. The only visible trace was the absence of two
        # banners in a long log.
        #
        # The LMA matters more on this path than anywhere else: on these
        # subjects the logic worth diversifying lived inside the third-party
        # library and only becomes first-party code once the FRA has written
        # it. Skipping it silently defeats the purpose of the category.
        graph.add_edge("fra", verifier_entry)

    # _after_verifier can route back to "axiom" (decision-level failure —
    # exclude a candidate, re-score) or "connector" (wrapper-fixable bug —
    # regenerate the adapter). "connector" is only a reachable outcome when
    # enable_connector=True, since wrapper_fixable failures can only arise
    # from a connector_agent run in the first place.
    verifier_edges = {"axiom": "axiom", "end": END}
    if enable_connector:
        verifier_edges["connector"] = "connector"
    if reimplement_mode:
        # On this path the FRA is the only agent that can act on a rejection:
        # there is no candidate to re-score and no adapter to regenerate. Map
        # both re-entry routes onto it so a verifier verdict is never dropped.
        verifier_edges = {"axiom": "fra", "connector": "fra", "end": END}

    if require_approval:
        # axiom_agent defers the rewrite; approval_gate decides whether to
        # apply it, then routes onward to connector (if still needed) or
        # straight to verifier for drop-in-only accepted changes. Verifier
        # runs AFTER approval_gate in gated mode (nothing is patched yet
        # before a human approves), and a verifier-triggered "axiom" retry
        # naturally flows back through approval_gate again via axiom's own
        # existing _after_axiom_gated edge — a human sees the updated
        # decision rather than a silently-changed one.
        edges = {"approval_gate": "approval_gate", "end": END}
        graph.add_conditional_edges("axiom", _after_axiom_gated, edges)

        approval_edges = {"verifier": verifier_entry, "end": END}
        if enable_connector:
            approval_edges["connector"] = "connector"
        graph.add_conditional_edges(
            "approval_gate",
            _after_approval_gate if enable_connector else _after_approval_gate_without_connector,
            approval_edges,
        )

        if enable_connector:
            graph.add_edge("connector", verifier_entry)
        graph.add_conditional_edges("verifier", _after_verifier, verifier_edges)
    elif enable_connector:
        graph.add_conditional_edges(
            "axiom", _after_axiom,
            {"connector": "connector", "verifier": verifier_entry, "end": END},
        )
        graph.add_edge("connector", verifier_entry)
        graph.add_conditional_edges("verifier", _after_verifier, verifier_edges)
    else:
        graph.add_conditional_edges(
            "axiom", _after_axiom_without_connector,
            {"verifier": verifier_entry, "end": END},
        )
        graph.add_conditional_edges("verifier", _after_verifier, verifier_edges)

    if require_approval:
        # interrupt()/Command(resume=...) requires a checkpointer to persist
        # state across the pause — without one, LangGraph cannot resume from
        # where execution suspended.
        #
        # Explicitly allowlist our own dataclasses (ImportInfo, RuleResult,
        # EquivalenceGroup, BenchmarkResult, LoadDecision, AgentEvent — all
        # defined in core/state.py and stored directly in LoaderState) for
        # msgpack checkpoint serialization. Without this, MemorySaver falls
        # back to "warn and allow" mode, which (a) prints a deprecation
        # warning on every resume and (b) will hard-fail once a future
        # LangGraph version defaults to strict mode (LANGGRAPH_STRICT_MSGPACK).
        # Since checkpoints here are in-memory-only and never leave this
        # process (MemorySaver, not Postgres/Sqlite), the security concern
        # the strict-mode advisory addresses — a compromised checkpoint
        # store reconstructing attacker-controlled objects — doesn't apply,
        # but allowlisting explicitly is still the forward-compatible fix
        # rather than leaving it on the deprecated implicit-allow path.
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        _STATE_DATACLASSES = (
            "ImportInfo", "EquivalenceGroup", "BenchmarkResult",
            "RuleResult", "LoadDecision", "AgentEvent", "VerifierResult",
        )
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("smart_loader.core.state", cls_name) for cls_name in _STATE_DATACLASSES
            ],
        )
        checkpointer = MemorySaver(serde=serde)
        return graph.compile(checkpointer=checkpointer)

    return graph.compile()


# ── Public runner ──────────────────────────────────────────────────────────────

def run_loader(
    source_file:      str,
    llm_provider:     str  = "ollama",
    model:            str  = "qwen3-coder-next",
    enable_security:  bool = True,
    enable_connector: bool = True,
    enable_aci: bool = True,
    require_approval: bool = False,
    isolated_profiling: bool = False,
    base_requirements: list[str] | None = None,
    base_requirement_recovery_errors: list[str] | None = None,
    migration_source: str = "",
    migration_source_import: str = "",
    migration_target: str = "",
    migration_target_version: str = "auto",
    environment_plan: dict | None = None,
    environment_python: str = "",
    test_command: str = "",
    test_timeout_seconds: int = 120,
    seed_target: bool = False,
    mandate_target: str = "",
    require_substitution: bool = False,
    reimplement_mode: bool = False,
    verification_import_paths: list[str] | None = None,
    verification_package: str = "",
    verification_project_root: str = "",
    verification_file_path: str = "",
    enable_isomorph: bool = False,
    isomorph_target_lines: int = 0,
    isomorph_target_ratio: float = 0.0,
    isomorph_corpus_dir: str = "",
    isomorph_use_generated: bool = False,
    isomorph_skip_verification: bool = False,
    isomorph_program_dir: str = "",
    isomorph_program_name: str = "",
    enable_verifier: bool = True,
    isomorph_interactive: bool = False,
    isomorph_ranking_metric: str = "",
    isomorph_artifact_dir: str = "",
    thread_id:        str | None = None,
) -> dict:
    """
    Run the full AXIOM pipeline on a Python source file.

    When require_approval=False (default): behaves exactly as before, runs
    to completion, returns the final LoaderState dict.

    When require_approval=True: the graph may pause mid-run via interrupt()
    before any AST rewrite. The returned dict will then contain an
    "__interrupted__" key set to True and an "__interrupt_payload__" key
    with the proposed decisions for the caller (CLI, scan-dir, etc.) to
    show a human. Call resume_loader(thread_id, response, ...) with the
    SAME thread_id to continue execution from exactly where it paused —
    do not call run_loader again, that would restart the whole pipeline.

    Returns the final LangGraph state dict (or interrupt marker), which
    includes: imports, rule_results, security_results, equivalence_groups,
    benchmarks, decisions, patched_code, connectors, agent_trace, messages.
    """
    graph      = build_graph(
        enable_security=enable_security,
        enable_connector=enable_connector,
        enable_aci=enable_aci,
        require_approval=require_approval,
        enable_verifier=enable_verifier,
        reimplement_mode=reimplement_mode,
    )
    session_id = uuid.uuid4().hex[:8]
    thread_id  = thread_id or session_id

    initial_state = {
        "source_file":        source_file,
        "llm_provider":       llm_provider,
        "model":              model,
        "require_approval":   require_approval,
        "enable_connector":   enable_connector,
        "enable_aci":         enable_aci,
        "isolated_profiling": isolated_profiling,
        "base_requirements":   base_requirements or [],
        "base_requirement_recovery_errors": base_requirement_recovery_errors or [],
        "migration_source":    migration_source,
        "migration_source_import": migration_source_import,
        "migration_target": migration_target,
        "migration_target_version": migration_target_version,
        "migration_target_versions": (
            {migration_target: migration_target_version}
            if migration_target and migration_target_version else {}
        ),
        "target_resolution_failures": {},
        "environment_plan": environment_plan or {},
        "environment_python": environment_python,
        "test_command": test_command,
        "test_timeout_seconds": max(1, int(test_timeout_seconds)),
        "seed_target": seed_target,
        # Non-empty means the caller REQUIRES this distribution to win,
        # rather than asking AXIOM to choose. Evaluation protocols that
        # state a substitution as a requirement need this; ordinary runs
        # leave it empty and keep optimisation semantics.
        "mandate_target": mandate_target,
        # True means AXIOM chooses WHICH library replaces the incumbent but
        # may not choose to keep it. Distinct from mandate_target, which
        # removes the choice entirely. Ordinary runs leave this False and
        # keep optimisation semantics, where declining is a valid answer.
        "require_substitution": require_substitution,
        "reimplement_mode": reimplement_mode,
        "verification_import_paths": verification_import_paths or [],
        "verification_package": verification_package,
        "verification_project_root": verification_project_root,
        "verification_file_path": verification_file_path,
        "verification_environment_cache": {},
        # Logic Substitution. Off unless asked for; the node reads nothing
        # else when enable_isomorph is False.
        "enable_isomorph":         enable_isomorph,
        "isomorph_target_lines":   max(0, int(isomorph_target_lines)),
        "isomorph_target_ratio":   float(isomorph_target_ratio or 0.0),
        "isomorph_corpus_dir":     isomorph_corpus_dir,
        "isomorph_use_generated":  isomorph_use_generated,
        # Only the no-verification ablation may set this. It is threaded
        # through explicitly rather than left to a state default so that a
        # caller cannot disable the equivalence oracle by omission.
        "isomorph_skip_verification": isomorph_skip_verification,
        # Points the LMA at a ProgramOracle-style corpus (golden
        # vectors recorded per program) instead of the ICS layout.
        "isomorph_program_dir": isomorph_program_dir,
        "isomorph_program_name": isomorph_program_name,
        "isomorph_interactive":    isomorph_interactive,
        "isomorph_ranking_metric": isomorph_ranking_metric,
        "isomorph_artifact_dir":   isomorph_artifact_dir,
        "isomorph_variant_code":   "",
        "isomorph_report":         {},
        "_session_id":        session_id,
        "source_code":        "",
        "imports":            [],
        "rule_results":       [],
        "security_results":   {},
        "equivalence_groups": [],
        "benchmarks":         {},
        "decisions":          [],
        "attempted_decision_history": [],
        "patched_code":       "",
        "connectors":         {},
        "verifier_results":       [],
        "verification_failures": {},
        "_axiom_retry_count":     0,
        "_connector_retry_count": 0,
        "_repair_cycle_count":    0,
        "_repair_history":        [],
        "_last_verifier_feedback": {},
        "_verifier_route":        "",
        "_last_connector_error":  "",
        "verification_status": "not_run",
        "verification_environment_status": "unknown",
        "verification_tool_status": {},
        "agent_trace":        [],
        "messages":           [],
        "error":              None,
        "done":               False,
    }

    if not require_approval:
        return _cleanup_disposable_environments(graph.invoke(initial_state))

    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(initial_state, config=config)

    if "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        payload = interrupts[0].value if interrupts else {}
        return {
            "__interrupted__":      True,
            "__interrupt_payload__": payload,
            "_session_id":          session_id,
            "_thread_id":           thread_id,
            "_graph":               graph,   # caller passes this to resume_loader
        }

    return _cleanup_disposable_environments(result)


def resume_loader(graph: object, thread_id: str, response: dict) -> dict:
    """
    Resume a paused (require_approval=True) AXIOM run after a human has
    decided what to do with the proposed changes.

    Parameters
    ----------
    graph     : the compiled graph object returned alongside the interrupt
                payload from run_loader() (under the "_graph" key) — reusing
                it (rather than rebuilding) keeps the same MemorySaver
                instance, which is required for the checkpointer to find
                the paused state.
    thread_id : must match the thread_id used in the original run_loader()
                call — this is the checkpointer's key for finding the
                suspended execution to continue.
    response  : the human's decision, one of:
                  {"action": "accept_all"}
                  {"action": "reject_all"}
                  {"action": "reject_some", "reject_roles": [...]}

    Returns the final LoaderState dict once the (now-resumed) graph run
    completes — same shape as a non-gated run_loader() return.
    """
    config = {"configurable": {"thread_id": thread_id}}
    return _cleanup_disposable_environments(graph.invoke(Command(resume=response), config=config))
