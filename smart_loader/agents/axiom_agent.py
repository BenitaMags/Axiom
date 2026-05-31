"""
agents/axiom_agent.py  (UPDATED — security scoring + token tracking)
──────────────────────
Same decision logic as before, with two additions:

  1. Security penalty: packages with HIGH/CRITICAL CVEs get a score penalty
     proportional to their OSV overall_score. Mirrors CodeAugur's multi-signal
     evidence combination (assembly + traces + rules → combined score).

  2. Token tracking: records input/output tokens for each LLM call
     (API compat scoring) to the token_tracker JSONL log.
"""

from __future__ import annotations
import ast
import json
import time
from rich.console import Console
from rich.table import Table
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from smart_loader.core.state import (
    EquivalenceGroup, BenchmarkResult, RuleResult,
    LoadDecision, AgentEvent, LoaderState,
)
from smart_loader.core.telemetry import save_session

console = Console()


def _get_llm(provider: str, model: str):
    if provider == "claude":
        return ChatAnthropic(
            model=model if "claude" in model else "claude-sonnet-4-20250514",
            max_tokens=500, temperature=0,
        )
    return ChatOllama(model=model, temperature=0)


# ── Scoring weights ────────────────────────────────────────────────────────────
# Now includes a security signal — mirrors CodeAugur's multi-metric combination.
W_SPEED        = 0.35   # import_time_ms     (was 0.45 — reduced to make room for security)
W_MEMORY       = 0.10   # memory_kb          (was 0.15)
W_AVAILABILITY = 0.20   # installed?
W_API_MATCH    = 0.15   # LLM compat
W_SECURITY     = 0.20   # NEW: OSV security score (1.0 = clean, 0.0 = critical CVEs)


_COMPAT_PROMPT = """You are a Python API compatibility expert.
Rate each package's drop-in compatibility for the specific methods listed.
Return ONLY a JSON object: {"package_name": 0.0_to_1.0}
1.0 = perfect drop-in, 0.5 = minor adaptation needed, 0.0 = incompatible.
No markdown, no explanation."""


def _score_api_compat(
    candidates: list[str],
    used_apis:  list[str],
    role:       str,
    provider:   str,
    model:      str,
    session_id: str = "unknown",
    source_file: str = "",
) -> dict[str, float]:
    if not used_apis:
        return {c: 0.8 for c in candidates}
    llm = _get_llm(provider, model)
    t0  = time.time()
    resp = llm.invoke([
        SystemMessage(content=_COMPAT_PROMPT),
        HumanMessage(content=f"Role: {role}\nMethods used: {used_apis}\nCandidates: {candidates}"),
    ])
    latency_ms = (time.time() - t0) * 1000

    # Token tracking
    try:
        from smart_loader.core.token_tracker import record_usage
        usage = getattr(resp, "usage_metadata", None) or {}
        record_usage(
            session_id=session_id,
            agent="axiom",
            model=model,
            provider=provider,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            source_file=source_file,
            latency_ms=latency_ms,
        )
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        console.print(f"[dim]  API compat tokens — input: {inp:,}  output: {out:,}  latency: {latency_ms:.0f}ms[/dim]")
    except Exception:
        pass

    raw = resp.content.strip()
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return {c: 0.8 for c in candidates}


def _compute_scores(
    group:            EquivalenceGroup,
    benchmarks:       dict[str, BenchmarkResult],
    security_results: dict,
    provider:         str,
    model:            str,
    session_id:       str = "unknown",
    source_file:      str = "",
) -> tuple[str, str, dict[str, dict]]:
    available = [c for c in group.candidates
                 if benchmarks.get(c) and benchmarks[c].available]

    if not available:
        return group.candidates[0], "LOW", {}

    # Speed score
    times    = {c: benchmarks[c].import_time_ms for c in available}
    min_time = min(times.values()) or 0.001
    speed_s  = {c: min_time / t for c, t in times.items()}

    # Memory score
    mems     = {c: benchmarks[c].memory_kb for c in available}
    min_mem  = min(mems.values()) or 0.001
    mem_s    = {c: min_mem / m if m > 0 else 1.0 for c, m in mems.items()}

    # Security score — NEW ──────────────────────────────────────────────────────
    sec_s: dict[str, float] = {}
    for c in available:
        sec = security_results.get(c)
        if sec and hasattr(sec, 'overall_score'):
            sec_s[c] = sec.overall_score
        else:
            sec_s[c] = 0.8   # assume moderate if no scan data

    # API compat score
    api_s = _score_api_compat(
        available, group.used_apis, group.role,
        provider, model, session_id, source_file,
    )

    # Weighted total (now includes security)
    scores = {}
    for c in available:
        speed    = speed_s.get(c, 0.0)
        mem      = mem_s.get(c, 0.0)
        avail    = 1.0
        api      = api_s.get(c, 0.8)
        security = sec_s.get(c, 0.8)
        total    = (W_SPEED    * speed
                  + W_MEMORY   * mem
                  + W_AVAILABILITY * avail
                  + W_API_MATCH * api
                  + W_SECURITY * security)
        scores[c] = {
            "speed":        round(speed, 3),
            "memory":       round(mem, 3),
            "availability": 1.0,
            "api_match":    round(api, 3),
            "security":     round(security, 3),
            "total":        round(total, 3),
        }

    winner = max(scores, key=lambda c: scores[c]["total"])

    if len(available) == 1:
        confidence = "LOW"
    else:
        scores_list = sorted([scores[c]["total"] for c in available], reverse=True)
        gap = scores_list[0] - scores_list[1]
        confidence = "HIGH" if gap > 0.15 else "MEDIUM"

    return winner, confidence, scores


class ImportRewriter(ast.NodeTransformer):
    def __init__(self, replacements: dict[str, str]):
        self.replacements = replacements
        self.seen_imports = set()  # Track seen imports to avoid duplicates

    def visit_Import(self, node):
        """Rewrite: import json → import ujson (deduplicate)"""
        new_names = []
        for alias in node.names:
            new_name = self.replacements.get(alias.name, alias.name)
            
            # Create a unique key for this import
            import_key = (new_name, alias.asname)
            
            # Skip if we've already seen this exact import
            if import_key not in self.seen_imports:
                self.seen_imports.add(import_key)
                new_names.append(ast.alias(name=new_name, asname=alias.asname))
        
        # If all names were duplicates, remove this import statement entirely
        if not new_names:
            return None
        
        node.names = new_names
        return node

    def visit_ImportFrom(self, node):
        """Rewrite: from json import → from ujson import (deduplicate)"""
        if node.module in self.replacements:
            node.module = self.replacements[node.module]
        return node

    def visit_Attribute(self, node):
        """✨ Rewrite usage like json.dumps → ujson.dumps"""
        self.generic_visit(node)
        
        # Check if this is a module.function call (e.g., json.dumps)
        if isinstance(node.value, ast.Name):
            old_module = node.value.id
            new_module = self.replacements.get(old_module)
            
            if new_module:
                # Replace the module name
                node.value.id = new_module
        
        return node


def axiom_agent(state: LoaderState) -> dict:
    console.rule("[bold green]🎯 AXIOM — Adaptive eXecution & Import Optimization Module")

    groups:           list[EquivalenceGroup]     = state.get("equivalence_groups", [])
    benchmarks:       dict[str, BenchmarkResult] = state.get("benchmarks", {})
    rule_results:     list[RuleResult]           = state.get("rule_results", [])
    security_results: dict                       = state.get("security_results", {})
    source_code:      str                        = state.get("source_code", "")
    source_file:      str                        = state.get("source_file", "")
    provider:         str                        = state.get("llm_provider", "ollama")
    model:            str                        = state.get("model", "qwen3-coder-next")
    session_id:       str                        = state.get("_session_id", "unknown")
    trace = list(state.get("agent_trace", []))

    console.print(f"[dim]Provider: {provider.upper()} / {model}  |  Session: {session_id}[/dim]")

    # Show security warnings for any HIGH/CRITICAL packages in scope
    if security_results:
        for pkg, res in security_results.items():
            if hasattr(res, 'risk_level') and res.risk_level in ("CRITICAL", "HIGH"):
                console.print(f"[bold red]⚠️  Security warning: {pkg} is {res.risk_level} risk "
                               f"(score={getattr(res,'overall_score',0):.2f})[/bold red]")

    trace.append(AgentEvent(stage="axiom", event="started"))

    if not groups:
        console.print("[green]All imports already optimal — no changes needed.[/green]")
        return {
            "decisions":    [],
            "patched_code": source_code,
            "done":         True,
            "agent_trace":  trace,
            "messages":     [AIMessage(content="AXIOM: nothing to optimize.")],
        }

    decisions:    list[LoadDecision] = []
    replacements: dict[str, str]     = {}

    # ── Score table — now includes Security column ─────────────────────────────
    table = Table(title="AXIOM Optimization Decisions", header_style="bold green")
    table.add_column("Role")
    table.add_column("Original")
    table.add_column("Winner")
    table.add_column("Conf.")
    table.add_column("Speed")
    table.add_column("Memory")
    table.add_column("Security")   # NEW
    table.add_column("API")
    table.add_column("Total")

    for group in groups:
        winner, confidence, scores = _compute_scores(
            group, benchmarks, security_results,
            provider, model, session_id, source_file,
        )
        original      = group.candidates[0]
        winner_scores = scores.get(winner, {})

        # Build rationale including security
        sec_note = ""
        w_sec = security_results.get(winner)
        if w_sec and hasattr(w_sec, 'risk_level') and w_sec.risk_level not in ("LOW",):
            sec_note = f" [Security:{w_sec.risk_level}]"
        o_sec = security_results.get(original)
        if o_sec and hasattr(o_sec, 'risk_level') and o_sec.risk_level in ("CRITICAL","HIGH"):
            sec_note += f" [Original {original} has {o_sec.risk_level} CVEs — switched]"

        rationale = (
            f"'{winner}' wins '{group.role}'. "
            f"Confidence: {confidence}. "
            f"Total: {winner_scores.get('total', '?')} "
            f"(speed={winner_scores.get('speed', '?')}, "
            f"mem={winner_scores.get('memory', '?')}, "
            f"sec={winner_scores.get('security', '?')}, "
            f"api={winner_scores.get('api_match', '?')})"
            + sec_note
        )
        if group.pitfalls:
            rationale += f". Migration note: {group.pitfalls[0]}"

        # Attach used_apis to decision for connector agent
        decision = LoadDecision(
            role=group.role,
            winner=winner,
            original=original,
            score=winner_scores,
            rationale=rationale,
            confidence=confidence,
        )
        # Stash used_apis on the object so connector_agent can access them
        decision.__dict__["used_apis"] = group.used_apis

        decisions.append(decision)

        if winner != original:
            replacements[original] = winner

        conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(confidence, "white")
        sec_val    = winner_scores.get("security", "—")
        sec_color  = "green" if isinstance(sec_val, float) and sec_val >= 0.75 else "red"
        table.add_row(
            group.role,
            original,
            f"[green]{winner}[/green]" if winner != original else winner,
            f"[{conf_color}]{confidence}[/{conf_color}]",
            str(winner_scores.get("speed", "—")),
            str(winner_scores.get("memory", "—")),
            f"[{sec_color}]{sec_val}[/{sec_color}]",
            str(winner_scores.get("api_match", "—")),
            str(winner_scores.get("total", "—")),
        )

    console.print(table)

    # ── AST rewrite ────────────────────────────────────────────────────────────
    if replacements:
        console.print("\n[bold]Patching imports:[/bold]")
        for orig, repl in replacements.items():
            console.print(f"  [red]{orig}[/red]  →  [green]{repl}[/green]")
        try:
            tree     = ast.parse(source_code)
            new_tree = ImportRewriter(replacements).visit(tree)
            ast.fix_missing_locations(new_tree)
            patched_code = ast.unparse(new_tree)
        except Exception as e:
            console.print(f"[red]AST rewrite failed: {e}[/red]")
            patched_code = source_code
    else:
        console.print("\n[green]All imports already optimal — no changes needed.[/green]")
        patched_code = source_code

    # ── Save telemetry session ─────────────────────────────────────────────────
    trace.append(AgentEvent(
        stage="axiom", event="completed",
        detail={"decisions": len(decisions), "replacements": replacements},
    ))

    try:
        saved_id = save_session(
            source_file=source_file,
            decisions=decisions,
            agent_trace=trace,
            patched_code=patched_code,
            llm_provider=provider,
            model=model,
        )
        console.print(f"\n[dim]Session saved: {saved_id}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Telemetry save failed: {e}[/yellow]")

    console.print(f"\n[bold green]✓ AXIOM complete — {len(decisions)} decision(s)[/bold green]\n")

    return {
        "decisions":    decisions,
        "patched_code": patched_code,
        "done":         True,
        "agent_trace":  trace,
        "messages":     [AIMessage(
            content="AXIOM: " + "; ".join(f"{d.role}→{d.winner}[{d.confidence}]" for d in decisions)
        )],
    }