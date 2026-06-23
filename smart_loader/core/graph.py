"""
core/graph.py
──────────────
Builds the LangGraph StateGraph for AXIOM.

PIPELINE:

  CodeAugur analog        AXIOM node
  ─────────────────────   ─────────────────────────────────────
  [Emulator/Disasm]    →  [parser_agent]      deterministic AST walk
  [Rules Engine]       →  [rules_agent]       is_installed, api_overlap, license
                          [security_agent]    OSV CVE scan + PyPI freshness
  [Stage 1 tool_caller]→  [resolver_agent]    LLM equivalence clustering
  [Stage 2 analyst]    →  [profiler_agent]    subprocess import benchmarks
  [Stage 3 verdict]    →  [axiom_agent]       weighted scoring + AST rewrite
                          [connector_agent]   API adapter shim generation

Graph topology:

  [parser]
     │
  [rules]      ← deterministic, no LLM
     │
  [security]   ← OSV + PyPI scan (parallel HTTP, no LLM)
     │
  [resolver]   ← LLM #1: semantic equivalence clustering
     │
     ├──(no groups)──► [END]
     │
  [profiler]   ← subprocess benchmarking
     │
  [axiom]      ← LLM #2: weighted scoring + AST rewrite
     │
  [connector]  ← LLM #3: generate API adapter shims (only when APIs are incompatible)
     │
    [END]
"""

from __future__ import annotations
import dataclasses
import uuid
from langgraph.graph import StateGraph, END

from smart_loader.core.state import LoaderState
from smart_loader.agents.parser_agent    import parser_agent
from smart_loader.agents.rules_agent     import rules_agent
from smart_loader.agents.resolver_agent  import resolver_agent
from smart_loader.agents.profiler_agent  import profiler_agent
from smart_loader.agents.axiom_agent     import axiom_agent
from smart_loader.agents.security_agent  import security_agent, candidate_security_agent
from smart_loader.agents.connector_agent import connector_agent


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
    """Route to connector when replacements need API adapters or aren't drop-in."""
    from smart_loader.core.state import LoadDecision

    decisions: list[LoadDecision] = state.get("decisions", [])
    groups = {g.role: g for g in state.get("equivalence_groups", [])}

    if not decisions:
        return "end"

    for decision in decisions:
        if decision.winner == decision.original:
            continue
        group = groups.get(decision.role)
        api_match = decision.score.get("api_match", 1.0) if decision.score else 1.0
        needs_connector = bool(
            getattr(decision, "requires_connector", False)
            or (group and group.requires_connector)
            or api_match < 0.95
        )
        if needs_connector:
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
        return resolver_agent(state)

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
    return resolver_agent(new_state)


# ── Graph factory ──────────────────────────────────────────────────────────────

def build_graph(
    enable_security:  bool = True,
    enable_connector: bool = True,
) -> object:
    """
    Build the AXIOM LangGraph pipeline.

    Parameters
    ----------
    enable_security  : include the OSV/PyPI security scan node
    enable_connector : include the API adapter code-generation node
    """
    graph = StateGraph(LoaderState)

    # ── Core nodes ─────────────────────────────────────────────────────────────
    graph.add_node("parser",   parser_agent)
    graph.add_node("rules",    rules_agent)
    graph.add_node("profiler", profiler_agent)
    graph.add_node("axiom",    axiom_agent)

    # ── Security node (optional) ───────────────────────────────────────────────
    if enable_security:
        graph.add_node("security", security_agent)
        graph.add_node("candidate_security", candidate_security_agent)
        graph.add_node("resolver", _security_aware_resolver)
    else:
        graph.add_node("resolver", resolver_agent)

    # ── Connector node (optional) ──────────────────────────────────────────────
    if enable_connector:
        graph.add_node("connector", connector_agent)

    # ── Entry point ────────────────────────────────────────────────────────────
    graph.set_entry_point("parser")

    graph.add_conditional_edges(
        "parser", _after_parser,
        {"continue": "rules", "end": END},
    )

    if enable_security:
        graph.add_edge("rules",    "security")
        graph.add_edge("security", "resolver")
        graph.add_conditional_edges(
            "resolver", _after_resolver,
            {"continue": "candidate_security", "end": END},
        )
        graph.add_edge("candidate_security", "profiler")
    else:
        graph.add_edge("rules", "resolver")
        graph.add_conditional_edges(
            "resolver", _after_resolver,
            {"continue": "profiler", "end": END},
        )

    graph.add_edge("profiler", "axiom")

    if enable_connector:
        graph.add_conditional_edges(
            "axiom", _after_axiom,
            {"connector": "connector", "end": END},
        )
        graph.add_edge("connector", END)
    else:
        graph.add_edge("axiom", END)

    return graph.compile()


# ── Public runner ──────────────────────────────────────────────────────────────

def run_loader(
    source_file:      str,
    llm_provider:     str  = "ollama",
    model:            str  = "qwen3-coder-next",
    enable_security:  bool = True,
    enable_connector: bool = True,
) -> dict:
    """
    Run the full AXIOM pipeline on a Python source file.

    Returns the final LangGraph state dict, which includes:
      - imports, rule_results, security_results
      - equivalence_groups, benchmarks
      - decisions, patched_code, connectors
      - agent_trace, messages
    """
    graph      = build_graph(
        enable_security=enable_security,
        enable_connector=enable_connector,
    )
    session_id = uuid.uuid4().hex[:8]

    return graph.invoke({
        "source_file":        source_file,
        "llm_provider":       llm_provider,
        "model":              model,
        "_session_id":        session_id,
        "source_code":        "",
        "imports":            [],
        "rule_results":       [],
        "security_results":   {},
        "equivalence_groups": [],
        "benchmarks":         {},
        "decisions":          [],
        "patched_code":       "",
        "connectors":         {},
        "agent_trace":        [],
        "messages":           [],
        "error":              None,
        "done":               False,
    })