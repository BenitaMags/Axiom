"""
agents/rules_agent.py
──────────────────────
Rules Agent — Node 2 in the LangGraph pipeline.

DIRECTLY INSPIRED BY CodeAugur's Rules Engine:
  In CodeAugur, before any LLM reasoning runs, a deterministic rules engine
  fires against the binary trace data:
    - return_register_delta  → PASS/FAIL/UNKNOWN
    - stack_balanced         → PASS/FAIL/UNKNOWN
    - callee_saved_preserved → PASS/FAIL/UNKNOWN

  These rules are FAST (no LLM), HIGH CONFIDENCE, and can short-circuit
  the expensive LLM pipeline early if the answer is obvious.

  We apply the same pattern to Python packages:
    - is_installed       → is this package even available?
    - has_metadata       → does it have version/license info?
    - license_permissive → is it safe to use?
    - api_surface_overlap → does it share function names with the reference?

  Any package that FAILs is_installed is immediately disqualified
  before the LLM ever sees it — saving tokens and time.
"""

from __future__ import annotations
from rich.console import Console
from langchain_core.messages import AIMessage

from smart_loader.core.state import ImportInfo, EquivalenceGroup, AgentEvent, LoaderState
from smart_loader.core.rules_engine import run_rules

console = Console()


def rules_agent(state: LoaderState) -> dict:
    """
    LangGraph node function.
    Runs deterministic rule checks on all imported packages.
    Disqualifies unavailable packages before the LLM pipeline runs.
    """
    console.rule("[bold blue]⚙️  Rules Agent")

    imports:  list[ImportInfo] = state.get("imports", [])
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="rules", event="started"))

    if not imports:
        console.print("[yellow]No imports — skipping rules.[/yellow]")
        return {
            "rule_results": [],
            "agent_trace": trace,
            "messages": [AIMessage(content="Rules Agent: no imports, skipped.")],
        }

    # Collect all unique top-level package names
    packages = list({imp.module.split(".")[0] for imp in imports})

    # Build reference map for api_surface_overlap rule
    # e.g. if we have both "requests" and "httpx", compare httpx against requests
    reference_map: dict[str, str] = {}
    for i, pkg in enumerate(packages):
        for j, other in enumerate(packages):
            if i != j:
                reference_map.setdefault(pkg, other)

    # Run all rules
    rule_results = run_rules(packages, reference_map)

    # ── Identify disqualified packages (FAIL on is_installed) ─────────────────
    disqualified = {
        r.package for r in rule_results
        if r.rule == "is_installed" and r.result == "FAIL"
    }

    if disqualified:
        console.print(f"[red]Disqualified (not installed): {', '.join(disqualified)}[/red]")
    else:
        console.print("[green]All packages installed and available[/green]")

    trace.append(AgentEvent(
        stage="rules",
        event="completed",
        detail={
            "packages_checked": len(packages),
            "disqualified": list(disqualified),
            "rules_run": len(rule_results),
        },
    ))

    return {
        "rule_results": rule_results,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Rules Agent: checked {len(packages)} packages, "
                    f"disqualified {len(disqualified)}: {disqualified or 'none'}"
        )],
    }