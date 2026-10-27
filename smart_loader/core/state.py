from __future__ import annotations
from typing import Annotated, Optional, TypedDict
from dataclasses import dataclass, field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ── Import metadata ────────────────────────────────────────────────────────────

@dataclass
class ImportInfo:
    module: str
    alias: Optional[str]
    names: list[str]
    line: int
    api_calls: list[str] = field(default_factory=list)


@dataclass
class EquivalenceGroup:
    role: str
    candidates: list[str]
    used_apis: list[str]
    reasoning: str
    pitfalls: list[str] = field(default_factory=list)
    crypto_required: bool = False
    requires_connector: bool = False


@dataclass
class BenchmarkResult:
    package: str
    import_time_ms: float
    available: bool
    memory_kb: float = 0.0
    runtime_ms: float = 0.0
    runtime_api: str = ""
    api_runtimes: dict[str, float] = field(default_factory=dict)
    runtime_probes: dict[str, dict] = field(default_factory=dict)
    # ``runtime_api`` is always labelled with the kind of probe performed
    # (for example ``call:ip_network`` or ``lookup:KafkaProducer``).  A blank
    # value means no safe, real probe was available; it is never a synthetic
    # timing substituted for a missing measurement.
    runtime_probe_kind: str = ""
    runtime_probe_detail: str = ""
    error: Optional[str] = None
    # ``available`` only says that a performance measurement exists.  The
    # structured profile status keeps an intentionally unavailable measurement
    # (for example, two candidates sharing one import root) distinct from a
    # hard isolated install/probe failure.  AXIOM omits unavailable performance
    # evidence rather than assigning a neutral prior; it must never select a
    # hard failure as a migration target.
    profile_status: str = "unattempted"


@dataclass
class RuleResult:
    rule: str
    package: str
    result: str
    confidence: str
    detail: dict = field(default_factory=dict)


@dataclass
class LoadDecision:
    role: str
    winner: str
    original: str
    # Some evidence dimensions are deliberately ``None`` when no honest,
    # comparable measurement exists (for example an external-service runtime
    # probe).  Keep that absence explicit instead of coercing it to a float.
    score: dict[str, object]
    rationale: str
    confidence: str = "HIGH"


@dataclass
class AgentEvent:
    stage: str
    event: str
    detail: dict = field(default_factory=dict)


# ── Verifier Agent (Reflexion-style self-correction) ────────────────────────────

@dataclass
class VerifierResult:
    """
    One verification check performed on a patched (or drop-in-replaced) file.

    check_type:
      "static_analysis" — pylint / bandit / mypy findings (prospector-style)
      "import_smoke"     — subprocess import/exec smoke test
      "llm_review"        — stage-2 LLM judgement over the combined evidence
      "connector_caveat"  — connector_agent's own migration_feasible=False signal

    failure_class (only meaningful when passed=False):
      "wrapper_fixable" — migration decision is sound, the *adapter code* has a
                           bug. Route back to connector_agent with the concrete
                           error so it can regenerate the wrapper.
      "decision_level"  — the winning package cannot structurally satisfy the
                           call sites no matter what glue code is written.
                           Route back to axiom_agent, excluding this candidate
                           for this role, so a different candidate (or the
                           original package) is scored instead.
    """
    role: str
    candidate: str
    check_type: str
    passed: bool
    failure_class: Optional[str] = None   # "wrapper_fixable" | "decision_level" | None
    detail: dict = field(default_factory=dict)


# ── Run-level cost / timing report ───────────────────────────────────────────────

@dataclass
class RunMetrics:
    """
    Wall-clock + token + cost accounting for a single `axiom run` invocation,
    printed once at the very end (after the human-in-the-loop feedback step
    has resolved) so the number reflects what actually got written to disk,
    not just what the pipeline proposed.
    """
    started_at:          float = 0.0
    finished_at:         float = 0.0
    elapsed_seconds:     float = 0.0
    total_input_tokens:  int = 0
    total_output_tokens: int = 0
    estimated_cost_usd:  float = 0.0
    by_agent:            dict = field(default_factory=dict)
    files_changed:       int = 0
    total_lines_added:   int = 0
    total_lines_removed: int = 0
    per_file_line_diff:  dict = field(default_factory=dict)  # file -> {added, removed}


# ── Master LangGraph state (MUST be TypedDict, not dict subclass) ──────────────

class LoaderState(TypedDict, total=False):
    """LangGraph state — TypedDict for proper type validation."""
    
    # INPUT
    source_file: str
    source_code: str
    llm_provider: str
    model: str
    require_approval: bool   # if True, pause via interrupt() before any AST rewrite
    enable_connector: bool   # whether a verifier may route a repair to Connector
    enable_aci: bool         # Connector uses the multi-turn ACI editor (vs one-shot)
    isolated_profiling: bool # if True, profiler installs/probes only in a temporary venv
    base_requirements: list[str]  # dependency specs from the pre-migration client commit
    base_requirement_recovery_errors: list[str]  # unsafe/unrecoverable historical dependency metadata
    migration_source: str
    migration_source_import: str  # Python module provided by migration_source, when known
    verification_import_paths: list[str]  # project source roots for verifier subprocesses
    verification_package: str  # package context for relative imports in the migrated file
    verification_project_root: str  # historical project snapshot used for sandboxed verification
    verification_file_path: str  # path of source_file relative to verification_project_root
    # Plain JSON-safe plan produced from project metadata.  It records the
    # selected interpreter, dependency provenance, and target install specs;
    # a missing compatible interpreter is an environment result, never an
    # invitation to silently fall back to the operator's Python.
    environment_plan: dict
    environment_python: str
    migration_target: str
    migration_target_version: str
    migration_target_versions: dict[str, str]
    # Candidate -> exact target-release resolution error.  A target with this
    # evidence is unavailable to scoring; AXIOM must not fall back to pip's
    # newest release and claim a verified migration.
    target_resolution_failures: dict[str, str]
    test_command: str
    test_timeout_seconds: int
    seed_target: bool
    mandate_target: str   # non-empty = this target MUST win, not chosen
    require_substitution: bool  # True = AXIOM picks WHICH, but may not decline
    profiling_env_dir: str
    profiling_env_python: str
    # Per-case, successful-only verifier venv cache.  Entries are created by
    # verifier_agent, reused across Verifier → AXIOM/Connector repair cycles,
    # and removed by core.graph only after the graph/case reaches a terminal
    # state.  Keys are deterministic fingerprints of the source/base/target
    # installation inputs; values contain only disposable local paths and
    # interpreter/tool metadata.
    verification_environment_cache: dict[str, dict]
    connector_last_valid_code: str  # retained across verifier→connector retries
    base_environment_failures: dict[str, str]
    verification_tool_status: dict[str, str]  # static-tool install/preflight status in the sandbox
    
    # PARSER OUTPUT
    imports: list[ImportInfo]
    
    # RULES OUTPUT
    rule_results: list[RuleResult]
    
    # RESOLVER OUTPUT
    equivalence_groups: list[EquivalenceGroup]
    
    # PROFILER OUTPUT
    benchmarks: dict[str, BenchmarkResult]
    
    # SECURITY OUTPUT  (populated by security_agent; values are SecurityResult instances)
    security_results: dict

    # AXIOM OUTPUT
    decisions: list[LoadDecision]
    attempted_decision_history: list[list[dict]]  # immutable AXIOM selections before verifier reverts/retries
    # JSON-safe audit records explaining every selection/decline.  They keep
    # a measured-but-not-yet-verified target distinct from an unsupported or
    # vetoed one, so reports never have to infer intent from blank scores.
    decision_evidence: list[dict]
    decision_declines: dict[str, list[dict]]
    decision_evidence_history: list[list[dict]]  # one immutable entry per AXIOM/retry pass
    patched_code: str
    _drop_in_replacements: dict  # internal: original -> winner, computed but not yet applied

    # CONNECTOR OUTPUT  (populated by connector_agent; role → adapter metadata dict)
    connectors: dict

    # VERIFIER OUTPUT  (populated by verifier_agent — see agents/verifier_agent.py)
    verifier_results: list[VerifierResult]
    verification_failures: dict     # role -> [excluded candidates]
    _axiom_retry_count: int
    _connector_retry_count: int
    _repair_cycle_count: int       # total Verifier → repair-agent cycles; globally capped
    _repair_history: list[dict]    # immutable, structured evidence for every repair cycle
    _last_verifier_feedback: dict  # structured feedback for AXIOM and Connector
    _verifier_route: str            # "axiom" | "connector" | "proceed" — set explicitly
                                     # by verifier_agent on every return; _after_verifier
                                     # in graph.py just reads this back rather than
                                     # inferring routing from counter deltas.
    _last_connector_error: str      # concrete error text handed back to connector_agent
                                     # on a "wrapper_fixable" retry
    verification_status: str        # verified | rejected | environment_incomplete | not_applicable | not_run
    verification_environment_status: str  # ready | blocked | unknown

    # LOGIC SUBSTITUTION  (populated by logic_substitution_agent, which runs
    # between connector and verifier — see agents/logic_substitution_agent.py)
    # Every key is opt-in: with enable_isomorph False the node returns without
    # reading or writing any of them, so an ordinary migration run is
    # unaffected.
    enable_isomorph: bool            # master switch (default False)
    isomorph_corpus_dir: str         # directory holding the equivalence oracle
    isomorph_module: str             # module name inside that corpus
    isomorph_target_lines: int       # absolute line target, OR
    isomorph_target_ratio: float     # target as a multiple of the current size
    isomorph_interactive: bool       # prompt a human for the target line count
    isomorph_use_generated: bool     # include the LLM-designed transformations
    isomorph_skip_verification: bool # ABLATION ONLY — run without the oracle
    isomorph_program_dir: str        # ProgramOracle corpus directory
    isomorph_program_name: str       # key into that corpus's golden.json
    isomorph_generated_path: str     # override for generated_spts.json
    isomorph_ranking_metric: str     # rename-insensitive metric the search ranks by
    isomorph_artifact_dir: str       # persist oracle-passing candidates here
    isomorph_beam_size: int
    isomorph_max_iterations: int
    isomorph_variant_code: str       # the accepted variant ("" when none)
    isomorph_extra_files: dict       # modules the variant EXTRACTED, {name: source}
    isomorph_generator: str          # "llm" (default) or "spt" for the old search
    isomorph_llm_attempts: int       # tries before falling back to the search
    isomorph_report: dict            # requested/achieved/diversity/oracle detail

    # TELEMETRY
    agent_trace: list[AgentEvent]
    messages: Annotated[list[BaseMessage], add_messages]
    run_metrics: Optional[RunMetrics]   # wall-clock + token + cost, filled in by cli.py
                                          # after the human-in-the-loop step resolves

    # CONTROL
    error: Optional[str]
    done: bool
    _session_id: str
