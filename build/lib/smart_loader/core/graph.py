"""
core/graph.py
──────────────
Builds the LangGraph StateGraph for AXIOM.

INSPIRED BY CodeAugur's pipeline architecture:
  CodeAugur has a multi-stage pipeline where deterministic checks run
  BEFORE the expensive LLM stages. We mirror this exactly:

  CodeAugur:              AXIOM:
  ─────────────────       ──────────────────────
  [Emulator/Disasm]   →   [Parser Agent]
  [Rules Engine]      →   [Rules Agent]       ← NEW (inspired by CodeAugur)
  [Stage 1: tool_caller]→ [Resolver Agent]
  [Stage 2: analyst]  →   [Profiler Agent]
  [Stage 3: verdict]  →   [AXIOM Agent]

  Conditional edges short-circuit the pipeline when possible:
  - No imports found      → END immediately (parser)
  - No equivalence groups → END after resolver (nothing to optimize)

Graph topology:

  [parser_agent]
       │
       ▼
  [rules_agent]          ← deterministic, no LLM, filters unavailable packages
       │
       ▼
  [resolver_agent]       ← LLM: semantic equivalence clustering
       │
       ├──(no groups)──► [END]
       │
       ▼
  [profiler_agent]       ← subprocess benchmarking
       │
       ▼
  [AXIOM agent]          ← LLM scoring + AST rewrite + telemetry
       │
       ▼
      [END]
"""

from __future__ import annotations
from langgraph.graph import StateGraph, END

from smart_loader.core.state import LoaderState
from smart_loader.agents.parser_agent   import parser_agent
from smart_loader.agents.rules_agent    import rules_agent
from smart_loader.agents.resolver_agent import resolver_agent
from smart_loader.agents.profiler_agent import profiler_agent
from smart_loader.agents.axiom_agent    import axiom_agent


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


# ── Graph factory ──────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(dict)

    # Register all 5 nodes
    graph.add_node("parser",   parser_agent)
    graph.add_node("rules",    rules_agent)
    graph.add_node("resolver", resolver_agent)
    graph.add_node("profiler", profiler_agent)
    graph.add_node("axiom",    axiom_agent)

    # Entry point
    graph.set_entry_point("parser")

    # Parser → Rules OR END
    graph.add_conditional_edges(
        "parser", _after_parser,
        {"continue": "rules", "end": END}
    )

    # Rules → Resolver (always — rules inform but don't stop the pipeline)
    graph.add_edge("rules", "resolver")

    # Resolver → Profiler OR END
    graph.add_conditional_edges(
        "resolver", _after_resolver,
        {"continue": "profiler", "end": END}
    )

    # Profiler → AXIOM → END
    graph.add_edge("profiler", "axiom")
    graph.add_edge("axiom",    END)

    return graph.compile()


# ── Public runner ──────────────────────────────────────────────────────────────

def run_loader(
    source_file:  str,
    llm_provider: str = "ollama",
    model:        str = "qwen3-coder-next",
) -> dict:
    graph = build_graph()
    return graph.invoke({
        "source_file":       source_file,
        "llm_provider":      llm_provider,
        "model":             model,
        "source_code":       "",
        "imports":           [],
        "rule_results":      [],
        "equivalence_groups":[],
        "benchmarks":        {},
        "decisions":         [],
        "patched_code":      "",
        "agent_trace":       [],
        "messages":          [],
        "error":             None,
        "done":              False,
    })