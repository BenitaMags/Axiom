"""
agents/resolver_agent.py  (UPDATED — token tracking + security awareness)
─────────────────────────
Same as before, plus:
  - Records input/output token counts to token_tracker after each LLM call
  - Surfaces security risk in the system prompt so the LLM weighs it
"""

from __future__ import annotations
import json
import time
import os
from rich.console import Console
from rich.tree import Tree
from smart_loader.core.llm_factory import _get_llm
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from smart_loader.core.constraints import infer_group_flags
from smart_loader.core.state import ImportInfo, RuleResult, EquivalenceGroup, AgentEvent, LoaderState

console = Console()





SYSTEM_PROMPT = """You are an expert Python dependency analyst acting as a smart OS loader.

Your job: analyze Python imports and identify equivalence groups — clusters of packages
that serve the SAME functional purpose and where one could substitute for another.

For each group also identify:
- Known migration pitfalls (e.g. "httpx uses async by default, requests is sync")
- Which specific API methods are actually used in the code

SECURITY NOTE: Some imports may be annotated with [SECURITY:RISK CVEs:...] tags.
When a package has HIGH or CRITICAL security risk, prefer alternatives in the same
equivalence group. Always note security concerns in your reasoning.

Rules:
- Only group imports with genuine functional overlap
- Each group needs at least 2 candidates
- Standard library modules should not be grouped with third-party alternatives
  UNLESS both are explicitly in the import list
- Be specific about roles: "async HTTP client" not just "networking"
- If a package has known CVEs and a safer equivalent exists, flag it

Respond ONLY with a valid JSON array. No markdown, no preamble:
[
  {
    "role": "functional role description",
    "candidates": ["pkg1", "pkg2"],
    "used_apis": ["method1", "method2"],
    "reasoning": "why these are equivalent",
    "pitfalls": ["known migration issue 1", "known migration issue 2"],
    "security_notes": ["CVE concerns if any"],
    "crypto_required": false,
    "requires_connector": false
  }
]

If no groups exist, return: []
"""

HUMAN_TEMPLATE = """Analyze these imports and find equivalence groups.

IMPORTS (with API calls actually used in the code; [SECURITY:...] tags indicate CVE risk):
{imports_json}

PACKAGES DISQUALIFIED BY RULES ENGINE (not installed — exclude from groups):
{disqualified}

SECURITY SCAN SUMMARY:
{security_summary}

SOURCE CODE (first 100 lines for context):
{code_snippet}
"""

# Add this helper function after the HUMAN_TEMPLATE
def _parse_llm_json(raw: str, fallback: list = None) -> list:
    """Safely parse LLM JSON response with fallback."""
    if fallback is None:
        fallback = []
    
    raw = raw.strip()
    if not raw:
        console.print("[yellow]⚠️  LLM returned empty response, using fallback[/yellow]")
        return fallback
    
    try:
        # Try direct parse
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    
    try:
        # Try markdown code block
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            return json.loads(raw.strip())
    except:
        pass

    try:
        # Try finding the first '[' and last ']'
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start:end+1])
    except:
        pass
    
    console.print("[yellow]⚠️  Failed to parse LLM JSON, using fallback[/yellow]")
    return fallback

def resolver_agent(state: LoaderState) -> dict:
    console.rule("[bold yellow]🔗 Resolver Agent")

    imports:         list[ImportInfo]  = state.get("imports", [])
    rule_results:    list[RuleResult]  = state.get("rule_results", [])
    security_results: dict             = state.get("security_results", {})
    source_code:     str               = state.get("source_code", "")
    llm_provider:    str               = state.get("llm_provider", "ollama")
    model:           str               = state.get("model", "qwen3-coder-next")
    session_id:      str               = state.get("_session_id", "unknown")
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="resolver", event="started"))

    if not imports:
        return {
            "equivalence_groups": [],
            "agent_trace": trace,
            "messages": [AIMessage(content="Resolver: no imports, skipped.")],
        }

    disqualified = {
        r.package for r in rule_results
        if r.rule == "is_installed" and r.result == "FAIL"
    }

    imports_data = [
        {
            "module":    imp.module,
            "alias":     imp.alias,
            "names":     imp.names,
            "api_calls": imp.api_calls,
            "line":      imp.line,
        }
        for imp in imports
        if imp.module.split(".")[0] not in disqualified
    ]

    code_snippet = "\n".join(source_code.splitlines()[:100])

    # Build security summary string for the prompt
    sec_lines = []
    for pkg, res in security_results.items():
        if hasattr(res, 'risk_level'):
            risk  = res.risk_level
            vulns = len(res.vulnerabilities) if hasattr(res, 'vulnerabilities') else 0
            score = getattr(res, 'overall_score', 1.0)
            sec_lines.append(f"  {pkg}: {risk} (score={score:.2f}, CVEs={vulns})")
    security_summary = "\n".join(sec_lines) or "  No security scan results available."

    llm = _get_llm(llm_provider, model)
    console.print(f"[dim]Querying {llm_provider.upper()} / {model}...[/dim]")

    t0 = time.time()
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=HUMAN_TEMPLATE.format(
            imports_json=json.dumps(imports_data, indent=2),
            disqualified=", ".join(disqualified) or "none",
            security_summary=security_summary,
            code_snippet=code_snippet,
        ))
    ])
    latency_ms = (time.time() - t0) * 1000

    # ── Token tracking ─────────────────────────────────────────────────────────
    try:
        from smart_loader.core.token_tracker import record_usage
        usage = getattr(response, "usage_metadata", None) or {}
        record_usage(
            session_id=session_id,
            agent="resolver",
            model=model,
            provider=llm_provider,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            source_file=state.get("source_file", ""),
            latency_ms=latency_ms,
        )
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        console.print(f"[dim]Tokens — input: {inp:,}  output: {out:,}  latency: {latency_ms:.0f}ms[/dim]")
    except Exception:
        pass

    raw = response.content.strip()

    try:
        groups_data = _parse_llm_json(raw, fallback=[])
        if not groups_data and raw:  # If parsing failed and there was content
            console.print(f"[yellow]⚠️  LLM response: {raw[:100]}...[/yellow]")
    except Exception as e:
        console.print(f"[red]JSON parse error: {e}[/red]")
        groups_data = []

    groups: list[EquivalenceGroup] = []
    for g in groups_data:
        candidates = [c for c in g.get("candidates", []) if c not in disqualified]
        if len(candidates) >= 2:
            group = EquivalenceGroup(
                role=g.get("role", "unknown"),
                candidates=candidates,
                used_apis=g.get("used_apis", []),
                reasoning=g.get("reasoning", ""),
                pitfalls=g.get("pitfalls", []),
            )
            groups.append(infer_group_flags(
                group,
                llm_crypto=g.get("crypto_required"),
                llm_connector=g.get("requires_connector"),
            ))

    if groups:
        tree = Tree("[bold yellow]Equivalence Groups Found")
        for g in groups:
            branch = tree.add(f"[cyan]{g.role}[/cyan]")
            branch.add(f"Candidates : {', '.join(g.candidates)}")
            branch.add(f"Used APIs  : {', '.join(g.used_apis) or 'none detected'}")
            if g.pitfalls:
                branch.add(f"[red]Pitfalls   : {'; '.join(g.pitfalls)}[/red]")
            flags = []
            if g.crypto_required:
                flags.append("crypto_required")
            if g.requires_connector:
                flags.append("requires_connector")
            if flags:
                branch.add(f"[yellow]Constraints: {', '.join(flags)}[/yellow]")
            branch.add(f"[dim]{g.reasoning}[/dim]")
        console.print(tree)
    else:
        console.print("[green]No overlapping dependencies — nothing to optimize.[/green]")

    console.print(f"\n[green]✓ {len(groups)} equivalence group(s) found[/green]\n")

    trace.append(AgentEvent(
        stage="resolver", event="completed",
        detail={"groups": len(groups), "roles": [g.role for g in groups]},
    ))

    return {
        "equivalence_groups": groups,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Resolver: {len(groups)} group(s): "
                    + ", ".join(f"'{g.role}'" for g in groups)
        )],
    }