from __future__ import annotations
import uuid
from langgraph.graph import StateGraph, END

from smart_loader.core.state import LoaderState
from smart_loader.agents.parser_agent    import parser_agent
from smart_loader.agents.rules_agent     import rules_agent
from smart_loader.agents.resolver_agent  import resolver_agent
from smart_loader.agents.profiler_agent  import profiler_agent
from smart_loader.agents.axiom_agent     import axiom_agent

# New agents — import gracefully so the old graph still works if files aren't present
try:
    from smart_loader.agents.security_agent  import security_agent
    _HAS_SECURITY = True
except ImportError:
    _HAS_SECURITY = False

try:
    from smart_loader.agents.connector_agent import connector_agent
    _HAS_CONNECTOR = True
except ImportError:
    _HAS_CONNECTOR = False


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
    Decide: should we call Connector Agent?
    
    Connector is ONLY needed if there are GENUINELY incompatible APIs.
    """
    if not _HAS_CONNECTOR:
        return "end"
    
    from smart_loader.core.state import LoadDecision, EquivalenceGroup
    
    decisions: list[LoadDecision] = state.get("decisions", [])
    equivalence_groups: list[EquivalenceGroup] = state.get("equivalence_groups", [])
    
    if not decisions:
        return "end"
    
    # ✅ STRICT check: only connector if APIs are genuinely incompatible
    for decision in decisions:
        if decision.winner == decision.original:
            continue  # No actual replacement
        
        # Find the corresponding group
        group = None
        for g in equivalence_groups:
            if g.role == decision.role:
                group = g
                break
        
        if not group or not group.pitfalls:
            continue
        
        # ✅ Check for GENUINE incompatibility (not just minor notes)
        pitfalls_text = "\n".join(group.pitfalls).lower()
        
        # GENUINE incompatibilities that require adapters
        genuine_incompatibilities = [
            "returns bytes",          # orjson.dumps() returns bytes, not str
            "return type",            # Different return type
            "raises",                 # Different exceptions thrown
            "not supported",          # Method doesn't exist
            "does not have",          # Missing attribute
            "does not support",       # Doesn't support keyword
            "incompatible",           # Explicitly incompatible
            "different signature",    # Function signature differs
            "different parameters",   # Parameters differ
            "keyword arguments",      # Keyword args not supported
            "optional",               # Optional behavior differs
        ]
        
        needs_connector = any(keyword in pitfalls_text for keyword in genuine_incompatibilities)
        
        if needs_connector:
            return "connector"
    
    # No genuine incompatibilities — skip connector
    return "end"


# ── Security-aware resolver: inject security scores into state ─────────────────

def _security_aware_resolver(state: dict) -> dict:
    """
    Thin wrapper around resolver_agent that injects security context into
    the prompt so the LLM knows which packages have CVEs.

    When a package has CRITICAL/HIGH risk, we add a note to its import info
    so the resolver can factor it in when forming equivalence groups.
    """
    security_results = state.get("security_results", {})
    if not security_results:
        return resolver_agent(state)

    # Annotate imports with security notes so the LLM sees them
    imports = list(state.get("imports", []))
    for imp in imports:
        pkg = imp.module.split(".")[0]
        sec = security_results.get(pkg)
        if sec and sec.risk_level in ("CRITICAL", "HIGH"):
            cve_ids = [v.id for v in sec.vulnerabilities[:2]]
            note    = f"[SECURITY:{sec.risk_level} CVEs:{','.join(cve_ids) or 'none'}]"
            # Append security note to api_calls so it surfaces in the prompt
            imp.api_calls = list(imp.api_calls or []) + [note]

    augmented = dict(state)
    augmented["imports"] = imports
    return resolver_agent(augmented)


# ── Graph factory ──────────────────────────────────────────────────────────────

def build_graph(
    enable_security:  bool = True,
    enable_connector: bool = True,
):
    """
    Build the AXIOM LangGraph pipeline.

    Parameters
    ----------
    enable_security  : include the OSV/PyPI security scan node
    enable_connector : include the API adapter code-generation node
    """
    graph = StateGraph(LoaderState)

    # ── Core nodes (always present) ────────────────────────────────────────────
    graph.add_node("parser",   parser_agent)
    graph.add_node("rules",    rules_agent)
    graph.add_node("profiler", profiler_agent)
    graph.add_node("axiom",    axiom_agent)

    # ── Optional: security node ────────────────────────────────────────────────
    if enable_security and _HAS_SECURITY:
        graph.add_node("security",  security_agent)
        graph.add_node("resolver",  _security_aware_resolver)
    else:
        graph.add_node("resolver",  resolver_agent)

    # ── Optional: connector node ───────────────────────────────────────────────
    if enable_connector and _HAS_CONNECTOR:
        graph.add_node("connector", connector_agent)

    # ── Entry point ────────────────────────────────────────────────────────────
    graph.set_entry_point("parser")

    # parser → rules OR END
    graph.add_conditional_edges(
        "parser", _after_parser,
        {"continue": "rules", "end": END}
    )

    # rules → security (if enabled) → resolver
    if enable_security and _HAS_SECURITY:
        graph.add_edge("rules",    "security")
        graph.add_edge("security", "resolver")
    else:
        graph.add_edge("rules", "resolver")

    # resolver → profiler OR END
    graph.add_conditional_edges(
        "resolver", _after_resolver,
        {"continue": "profiler", "end": END}
    )

    # profiler → axiom → (connector OR END)
    graph.add_edge("profiler", "axiom")

    if enable_connector and _HAS_CONNECTOR:
        graph.add_edge("axiom",     "connector")
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
        # ── Input ──────────────────────────────────────────────────────────────
        "source_file":        source_file,
        "llm_provider":       llm_provider,
        "model":              model,
        "_session_id":        session_id,   # passed to connector for token tracking

        # ── Agent outputs (initialised empty) ─────────────────────────────────
        "source_code":        "",
        "imports":            [],
        "rule_results":       [],
        "security_results":   {},           # NEW: populated by security_agent
        "equivalence_groups": [],
        "benchmarks":         {},
        "decisions":          [],
        "patched_code":       "",
        "connectors":         {},           # NEW: populated by connector_agent

        # ── Telemetry ──────────────────────────────────────────────────────────
        "agent_trace":        [],
        "messages":           [],

        # ── Control ────────────────────────────────────────────────────────────
        "error":              None,
        "done":               False,
    })
