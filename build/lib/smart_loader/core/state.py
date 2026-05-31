"""
core/state.py
─────────────
Shared LangGraph state for the AXIOM pipeline.

INSPIRED BY CodeAugur:
  - CodeAugur uses a structured result object (SimilarityResult) that carries
    verdict, confidence, distances, token_usage, and provenance through its pipeline.
  - We adopt the same pattern: every agent writes into a single shared state dict
    that accumulates results, and the final state IS the output — fully auditable.
  - CodeAugur also tracks token usage per stage. We mirror that with
    agent_trace: a list of AgentEvent objects recording what each agent did.
"""

from __future__ import annotations
from typing import Annotated, Optional
from dataclasses import dataclass, field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ── Import metadata ────────────────────────────────────────────────────────────

@dataclass
class ImportInfo:
    """
    One import statement found in the target Python file.
    Captured by the Parser Agent via AST analysis.
    """
    module: str            # e.g. "numpy", "requests"
    alias:  Optional[str]  # e.g. "np", "pd"
    names:  list[str]      # e.g. ["array", "zeros"] from `from X import a, b`
    line:   int            # source line number
    api_calls: list[str] = field(default_factory=list)  # e.g. ["get", "post"]


# ── Equivalence group ──────────────────────────────────────────────────────────

@dataclass
class EquivalenceGroup:
    """
    A cluster of packages that serve the same functional role.
    Produced by the Resolver Agent via LLM reasoning.

    INSPIRED BY CodeAugur's ISA knowledge system:
      CodeAugur has structured descriptors per architecture that list
      'similarity_pitfalls' and 'normalization_rules'.
      Our EquivalenceGroup is the Python-level equivalent:
      it describes WHY two packages are equivalent and what pitfalls to watch for.
    """
    role:       str        # e.g. "HTTP client", "JSON serializer"
    candidates: list[str]  # e.g. ["requests", "httpx", "urllib3"]
    used_apis:  list[str]  # actual call sites found in the AST
    reasoning:  str        # LLM's explanation of equivalence
    pitfalls:   list[str] = field(default_factory=list)  # known migration pitfalls


# ── Benchmark result ───────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """
    Import timing and availability for one package.
    Produced by the Profiler Agent via subprocess benchmarking.

    INSPIRED BY CodeAugur's distance metrics:
      CodeAugur computes multiple numeric scores (jaccard_opcode, register_delta,
      trace_entropy) between two binaries. We compute analogous metrics between
      package candidates: import_time_ms is our primary distance signal.
    """
    package:        str
    import_time_ms: float
    available:      bool
    memory_kb:      float = 0.0   # memory footprint after import
    error:          Optional[str] = None


# ── Rules result ───────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    """
    Result of one deterministic rule check.

    DIRECTLY INSPIRED BY CodeAugur's Rules Engine:
      CodeAugur runs deterministic checks (return_register_delta, stack_balanced,
      callee_saved_preserved) BEFORE the LLM pipeline. These are fast, cheap,
      and give high-confidence signals without spending tokens.
      We do exactly the same for Python packages:
      check install status, version compatibility, license, before calling the LLM.
    """
    rule:       str              # e.g. "is_installed", "version_compatible"
    package:    str
    result:     str              # "PASS" | "FAIL" | "UNKNOWN"
    confidence: str              # "HIGH" | "MEDIUM" | "LOW"
    detail:     dict = field(default_factory=dict)


# ── Load decision ──────────────────────────────────────────────────────────────

@dataclass
class LoadDecision:
    """
    Final optimization decision for one equivalence group.
    Produced by AXIOM Agent.

    INSPIRED BY CodeAugur's verdict system:
      CodeAugur produces YES/NO/UNCERTAIN with a confidence level and a summary.
      Our LoadDecision is the Python loader equivalent:
      winner + confidence scores + rationale, fully auditable.
    """
    role:       str
    winner:     str              # the chosen package
    original:   str              # what was in the source file
    score:      dict[str, float] # speed, availability, api_match, total
    rationale:  str              # human-readable explanation
    confidence: str = "HIGH"     # "HIGH" | "MEDIUM" | "LOW"


# ── Agent event (telemetry) ────────────────────────────────────────────────────

@dataclass
class AgentEvent:
    """
    One event in the agent execution trace.

    DIRECTLY INSPIRED BY CodeAugur's telemetry system:
      CodeAugur logs every step as a structured event with a type, stage, and
      content-addressed detail. It stores a lean log (just hashes + event sequence)
      and a full detail store. We mirror this with AgentEvent objects that record
      what each agent did, when, and what it produced.
    """
    stage:   str    # "parser" | "rules" | "resolver" | "profiler" | "axiom"
    event:   str    # "started" | "completed" | "rule_fired" | "llm_called" | "decision_made"
    detail:  dict = field(default_factory=dict)


# ── Master LangGraph state ─────────────────────────────────────────────────────

class LoaderState(dict):
    """
    The shared state dict flowing through all LangGraph nodes.

    INSPIRED BY CodeAugur's pipeline state:
      CodeAugur's pipeline passes a config + accumulated results through each stage.
      Each stage reads from and writes to this shared object.
      We use LangGraph's StateGraph with a plain dict for the same effect.
    """

    # ── Input ──────────────────────────────────────────────────────────────────
    source_file:   str       # path to target .py file
    source_code:   str       # raw source text
    llm_provider:  str       # "ollama" | "claude"
    model:         str       # e.g. "qwen3-coder-next"

    # ── Parser Agent output ────────────────────────────────────────────────────
    imports:       list[ImportInfo]

    # ── Rules Engine output (runs before LLM) ─────────────────────────────────
    rule_results:  list[RuleResult]

    # ── Resolver Agent output ──────────────────────────────────────────────────
    equivalence_groups: list[EquivalenceGroup]

    # ── Profiler Agent output ──────────────────────────────────────────────────
    benchmarks:    dict[str, BenchmarkResult]

    # ── AXIOM Agent output ─────────────────────────────────────────────────────
    decisions:     list[LoadDecision]
    patched_code:  str

    # ── Telemetry (append-only event log) ──────────────────────────────────────
    agent_trace:   list[AgentEvent]
    messages:      Annotated[list[BaseMessage], add_messages]

    # ── Control ────────────────────────────────────────────────────────────────
    error:         Optional[str]
    done:          bool