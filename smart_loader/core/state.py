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


@dataclass
class BenchmarkResult:
    package: str
    import_time_ms: float
    available: bool
    memory_kb: float = 0.0
    error: Optional[str] = None


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
    score: dict[str, float]
    rationale: str
    confidence: str = "HIGH"


@dataclass
class AgentEvent:
    stage: str
    event: str
    detail: dict = field(default_factory=dict)


# ── Master LangGraph state (MUST be TypedDict, not dict subclass) ──────────────

class LoaderState(TypedDict, total=False):
    """LangGraph state — TypedDict for proper type validation."""
    
    # INPUT
    source_file: str
    source_code: str
    llm_provider: str
    model: str
    
    # PARSER OUTPUT
    imports: list[ImportInfo]
    
    # RULES OUTPUT
    rule_results: list[RuleResult]
    
    # RESOLVER OUTPUT
    equivalence_groups: list[EquivalenceGroup]
    
    # PROFILER OUTPUT
    benchmarks: dict[str, BenchmarkResult]
    
    # AXIOM OUTPUT
    decisions: list[LoadDecision]
    patched_code: str
    
    # TELEMETRY
    agent_trace: list[AgentEvent]
    messages: Annotated[list[BaseMessage], add_messages]
    
    # CONTROL
    error: Optional[str]
    done: bool
    
    # Additional fields (if needed for security/connector agents)
    security_results: dict
    _session_id: str
