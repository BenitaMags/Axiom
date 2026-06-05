"""
agents/connector_agent.py
──────────────────────────
Connector Agent — Node 6 in the LangGraph pipeline (runs after AXIOM).

When AXIOM decides to replace one package with another, the APIs often
differ in subtle ways. The Connector Agent generates a thin adapter/bridge
module that makes the replacement package work with the existing code's
call patterns — zero manual refactoring needed.

Examples:
  requests → httpx   : httpx.Client() context manager wrapping, same method names
  pandas   → polars  : .groupby() → .group_by(), .sort_values() → .sort()
  json     → orjson  : orjson.dumps() returns bytes, wrap with .decode()

Output:
  - connector code (Python module string)
  - usage instructions
  - list of transformed call sites
"""

from __future__ import annotations
import json
import time
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

console = Console()


# ── Prompts ────────────────────────────────────────────────────────────────────

CONNECTOR_SYSTEM = """You are an expert Python API compatibility engineer.

When a user migrates from one package to another, you generate a THIN ADAPTER MODULE
(a "connector") that bridges the old API surface to the new package's API.

The connector module:
1. Wraps the new package to match the OLD package's function signatures exactly
2. Handles input/output type differences (e.g. bytes→str, sync→async context manager)
3. Is importable as a drop-in replacement for the original package
4. Has NO external dependencies beyond the new package itself
5. Includes docstrings explaining each adaptation

IMPORTANT RULES:
- Match the EXACT function signatures from the original_code usage
- Handle type coercions silently (e.g. orjson bytes → str auto-decode)
- Preserve keyword argument names
- Keep the adapter thin — no business logic, pure delegation
- Add a module docstring explaining what it adapts and why

Respond with ONLY valid Python code. No markdown fences, no preamble.
The module should be importable as `{connector_module_name}`.
"""

CONNECTOR_HUMAN = """
Generate a connector module for this migration:

FROM PACKAGE: {original}
TO PACKAGE:   {winner}
FUNCTIONAL ROLE: {role}
KNOWN PITFALLS: {pitfalls}

ACTUAL API CALLS USED IN THE CODE:
{used_apis}

RELEVANT SOURCE CODE EXCERPT (showing how the original package is used):
{code_excerpt}

The connector module name should be: {connector_module_name}
It will be imported INSTEAD of {original} in the patched code.

Generate the connector now:
"""

TRANSFORM_SYSTEM = """You are an expert Python code transformer.

Given:
1. Original source code using package A
2. A connector module that bridges A → B (where B is the optimized package)
3. Transformation rules

Rewrite ONLY the import statements and any usage patterns that need updating
to use the connector module instead of the original package.

The connector module provides the SAME interface as the original — so most code
stays identical. Only imports and any direct package.submodule references need updating.

Respond with ONLY the complete transformed Python source. No markdown, no explanation.
"""


# ── LLM factory ───────────────────────────────────────────────────────────────

def _get_llm(provider: str, model: str, max_tokens: int = 2000):
    if provider == "claude":
        return ChatAnthropic(
            model=model if "claude" in model else "claude-sonnet-4-20250514",
            max_tokens=max_tokens, temperature=0,
        )
    return ChatOllama(model=model, temperature=0)


# ── Code excerpt extractor ─────────────────────────────────────────────────────

def _extract_usage_excerpt(source_code: str, package: str, max_lines: int = 30) -> str:
    """Extract lines that reference a specific package — gives the LLM focused context."""
    lines = source_code.splitlines()
    relevant = []
    for i, line in enumerate(lines):
        if package in line:
            # Include 2 lines of context around each match
            start = max(0, i - 1)
            end   = min(len(lines), i + 3)
            relevant.extend(lines[start:end])
            relevant.append("...")
    return "\n".join(relevant[:max_lines]) or source_code[:500]


# ── Agent ──────────────────────────────────────────────────────────────────────

def connector_agent(state: dict) -> dict:
    """
    LangGraph node: for each replacement decision, generate a connector module
    and a fully-transformed source code that uses it.
    """
    from smart_loader.core.state import AgentEvent, LoadDecision

    console.rule("[bold blue]🔌 Connector Agent — Input/Output Transformer")

    decisions:    list[LoadDecision] = state.get("decisions", [])
    source_code:  str                = state.get("source_code", "")
    patched_code: str                = state.get("patched_code", source_code)
    provider:     str                = state.get("llm_provider", "ollama")
    model:        str                = state.get("model", "qwen3-coder-next")
    session_id:   str                = state.get("_session_id", "unknown")
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="connector", event="started"))

    # Only generate connectors for actual replacements
    replacements = [d for d in decisions if d.winner != d.original]

    if not replacements:
        console.print("[green]No package replacements — no connectors needed.[/green]")
        return {
            "connectors":    {},
            "agent_trace":   trace,
            "patched_code":  patched_code,
            "messages":      [AIMessage(content="Connector: no replacements, nothing to bridge.")],
        }

    llm    = _get_llm(provider, model)
    connectors: dict[str, str] = {}   # {original_pkg: connector_code}

    for decision in replacements:
        original = decision.original
        winner   = decision.winner
        role     = decision.role
        pitfalls = getattr(decision, "score", {})  # reuse score dict for context

        # Derive connector module name — e.g. "requests" → "axiom_requests_compat"
        connector_module = f"axiom_{original.replace('-','_')}_compat"

        console.print(f"\n[bold]Generating connector:[/bold] "
                      f"[red]{original}[/red] → [green]{winner}[/green] "
                      f"([dim]{connector_module}[/dim])")

        code_excerpt  = _extract_usage_excerpt(source_code, original)
        used_apis_str = ", ".join(getattr(decision, "used_apis", []) or []) or "see excerpt"
        pitfalls_str  = decision.rationale[:200]

        # ── LLM call: generate connector ──────────────────────────────────────
        t0 = time.time()
        try:
            resp = llm.invoke([
                SystemMessage(content=CONNECTOR_SYSTEM.format(
                    connector_module_name=connector_module
                )),
                HumanMessage(content=CONNECTOR_HUMAN.format(
                    original=original,
                    winner=winner,
                    role=role,
                    pitfalls=pitfalls_str,
                    used_apis=used_apis_str,
                    code_excerpt=code_excerpt,
                    connector_module_name=connector_module,
                ))
            ])
            connector_code = resp.content.strip()
            # Strip markdown fences if model adds them anyway
            if connector_code.startswith("```"):
                connector_code = connector_code.split("```")[1]
                if connector_code.startswith("python"):
                    connector_code = connector_code[6:]
            latency = (time.time() - t0) * 1000

            # Track token usage
            try:
                from smart_loader.core.token_tracker import record_usage
                usage = getattr(resp, "usage_metadata", None) or {}
                record_usage(
                    session_id=session_id,
                    agent="connector",
                    model=model,
                    provider=provider,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    latency_ms=latency,
                )
            except Exception:
                pass

            connectors[original] = connector_code

            console.print(
                Panel(
                    Syntax(connector_code[:1500] + ("\n..." if len(connector_code) > 1500 else ""),
                           "python", theme="monokai", line_numbers=True),
                    title=f"[cyan]{connector_module}.py[/cyan]",
                    border_style="blue",
                )
            )

        except Exception as e:
            console.print(f"[red]Connector generation failed for {original}: {e}[/red]")
            # Fallback: trivial pass-through
            connectors[original] = (
                f'"""\nAXIOM connector: {original} → {winner}\nGeneration failed — using pass-through.\n"""\n'
                f"from {winner} import *  # noqa — pass-through fallback\n"
            )

    # ── Transform the patched source to use connector imports ─────────────────
    if connectors:
        console.print("\n[bold]Rewriting imports to use connector modules...[/bold]")
        t0 = time.time()
        try:
            connector_summary = "\n\n".join(
                f"# Connector for {orig}:\n{code[:300]}"
                for orig, code in connectors.items()
            )
            resp2 = llm.invoke([
                SystemMessage(content=TRANSFORM_SYSTEM),
                HumanMessage(content=(
                    f"Original source code:\n```python\n{patched_code}\n```\n\n"
                    f"Connector modules available:\n{connector_summary}\n\n"
                    f"Replace imports of: {', '.join(connectors.keys())} "
                    f"with their respective axiom_*_compat connector modules.\n"
                    f"Return ONLY the complete transformed source code."
                ))
            ])
            transformed = resp2.content.strip()
            if transformed.startswith("```"):
                transformed = transformed.split("```")[1]
                if transformed.startswith("python"):
                    transformed = transformed[6:]
                # strip trailing fence
                transformed = transformed.rstrip("`").strip()

            latency2 = (time.time() - t0) * 1000
            try:
                from smart_loader.core.token_tracker import record_usage
                usage2 = getattr(resp2, "usage_metadata", None) or {}
                record_usage(
                    session_id=session_id,
                    agent="connector",
                    model=model,
                    provider=provider,
                    input_tokens=usage2.get("input_tokens", 0),
                    output_tokens=usage2.get("output_tokens", 0),
                    latency_ms=latency2,
                )
            except Exception:
                pass

            patched_code = transformed
            console.print("[green]✓ Source transformed to use connector modules[/green]")

        except Exception as e:
            console.print(f"[yellow]Source transform failed: {e} — keeping prior patched code[/yellow]")

    trace.append(AgentEvent(
        stage="connector", event="completed",
        detail={"connectors_generated": list(connectors.keys())},
    ))

    console.print(f"\n[bold blue]✓ Connector Agent complete — {len(connectors)} connector(s)[/bold blue]\n")

    return {
        "connectors":   connectors,
        "patched_code": patched_code,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Connector: generated {len(connectors)} adapter(s): "
                    + ", ".join(f"axiom_{o}_compat" for o in connectors)
        )],
    }
