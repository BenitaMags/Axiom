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
import json
import time
from rich.console import Console
from rich.table import Table
from smart_loader.core.llm_factory import _get_llm
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from smart_loader.core.ast_rewrite import apply_import_replacements
from smart_loader.core.constraints import filter_crypto_candidates
from smart_loader.core.state import (
    EquivalenceGroup, BenchmarkResult, RuleResult,
    LoadDecision, AgentEvent, LoaderState,
)
from smart_loader.core.telemetry import save_session

console = Console()


# ── Scoring weights ────────────────────────────────────────────────────────────
# Now includes a security signal — mirrors CodeAugur's multi-metric combination.
W_SPEED        = 0.35   # import_time_ms     (was 0.45 — reduced to make room for security)
W_MEMORY       = 0.10   # memory_kb          (was 0.15)
W_AVAILABILITY = 0.20   # installed?
W_API_MATCH    = 0.15   # LLM compat
W_SECURITY     = 0.20   # NEW: OSV security score (1.0 = clean, 0.0 = critical CVEs)


_COMPAT_PROMPT = """You are a Python API compatibility and performance expert.
Rate each package's drop-in compatibility and estimated runtime execution speed compared to the original package for the specific methods listed under the given role.

Return ONLY a JSON object:
{
  "compatibility": {
    "package_name": <float 0.0_to_1.0>  # 1.0 = perfect drop-in, 0.5 = minor adaptation needed, 0.0 = incompatible
  },
  "runtime_speed_factor": {
    "package_name": <float 1.0_to_20.0>  # Estimated runtime execution speed factor (1.0 = baseline/slowest in the group, e.g. orjson is 5.0 compared to json's 1.0 for loads/dumps)
  }
}
No markdown, no explanation."""


def _score_api_compat(
    candidates: list[str],
    used_apis:  list[str],
    role:       str,
    provider:   str,
    model:      str,
    session_id: str = "unknown",
    source_file: str = "",
) -> tuple[dict[str, float], dict[str, float]]:
    if not used_apis:
        return {c: 0.8 for c in candidates}, {c: 1.0 for c in candidates}
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
    data = None
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        data = json.loads(raw)
    except Exception:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(raw[start:end+1])
        except Exception:
            pass

    if isinstance(data, dict):
        compat = data.get("compatibility", {})
        speed = data.get("runtime_speed_factor", {})
        compat_out = {c: float(compat.get(c, 0.8)) for c in candidates}
        speed_out = {c: float(speed.get(c, 1.0)) for c in candidates}
        return compat_out, speed_out

    return {c: 0.8 for c in candidates}, {c: 1.0 for c in candidates}


def _primary_api_token(used_apis: list[str]) -> str:
    if not used_apis:
        return ""
    return used_apis[0].split(".")[-1].lower()


def _measured_runtime_scores(
    available: list[str],
    benchmarks: dict[str, BenchmarkResult],
    api_token: str,
) -> dict[str, float] | None:
    """Return normalized runtime scores when profiler measured the primary API."""
    if not api_token:
        return None
    times: dict[str, float] = {}
    for c in available:
        bm = benchmarks.get(c)
        if not bm or not bm.available:
            continue
        rt = bm.api_runtimes.get(api_token)
        if rt and rt > 0:
            times[c] = rt
    if len(times) < 2:
        return None
    min_time = min(times.values()) or 0.001
    return {c: min_time / t for c, t in times.items()}


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

    available = filter_crypto_candidates(group, available)
    if group.crypto_required:
        vetoed = [c for c in group.candidates
                  if c not in available and benchmarks.get(c) and benchmarks[c].available]
        if vetoed:
            console.print(
                f"[yellow]  Crypto veto: excluded {', '.join(vetoed)} "
                f"from '{group.role}'[/yellow]"
            )

    if not available:
        return group.candidates[0], "LOW", {}

    api_token = _primary_api_token(group.used_apis)

    # Import speed score
    times    = {c: benchmarks[c].import_time_ms for c in available}
    min_time = min(times.values()) or 0.001
    import_s = {c: min_time / t for c, t in times.items()}

    measured_runtime = _measured_runtime_scores(available, benchmarks, api_token)

    api_s, runtimes = _score_api_compat(
        available, group.used_apis, group.role,
        provider, model, session_id, source_file,
    )

    if measured_runtime:
        runtime_s = measured_runtime
    else:
        max_runtime = max(runtimes.values()) if runtimes else 1.0
        if max_runtime <= 0.0:
            max_runtime = 1.0
        runtime_s = {c: runtimes.get(c, 1.0) / max_runtime for c in available}

    speed_s = {c: 0.2 * import_s[c] + 0.8 * runtime_s[c] for c in available}

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
        decision.__dict__["requires_connector"] = group.requires_connector

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

    # Decisions only — connector patches source; drop-in rewrites happen when connector is skipped.
    patched_code = source_code
    drop_in_replacements: dict[str, str] = {}
    for d in decisions:
        if d.winner == d.original:
            continue
        group = next((g for g in groups if g.role == d.role), None)
        api_match = d.score.get("api_match", 0.0) if d.score else 0.0
        needs_connector = bool(getattr(d, "requires_connector", False) or (group and group.requires_connector))
        if api_match >= 0.95 and not needs_connector:
            drop_in_replacements[d.original] = d.winner

    if drop_in_replacements:
        console.print("\n[bold]Drop-in import patches (no connector needed):[/bold]")
        for orig, repl in drop_in_replacements.items():
            console.print(f"  [red]{orig}[/red]  →  [green]{repl}[/green]")
        try:
            patched_code = apply_import_replacements(source_code, drop_in_replacements)
        except Exception as e:
            console.print(f"[red]AST rewrite failed: {e}[/red]")
            patched_code = source_code
    elif replacements:
        console.print(
            "\n[dim]Replacements deferred to connector agent "
            "(preserving original call sites for analysis).[/dim]"
        )
        for orig, repl in replacements.items():
            console.print(f"  [red]{orig}[/red]  →  [green]{repl}[/green]  [dim](pending)[/dim]")
    else:
        console.print("\n[green]All imports already optimal — no changes needed.[/green]")

    # ── Save telemetry session ─────────────────────────────────────────────────
    trace.append(AgentEvent(
        stage="axiom", event="completed",
        detail={"decisions": len(decisions), "replacements": replacements},
    ))

    try:
        groups_by_role = {g.role: g for g in groups}
        needs_connector = False
        for d in decisions:
            if d.winner == d.original:
                continue
            group = groups_by_role.get(d.role)
            api_match = (d.score or {}).get("api_match", 1.0)
            if (
                getattr(d, "requires_connector", False)
                or (group and group.requires_connector)
                or api_match < 0.95
            ):
                needs_connector = True
                break
        if not needs_connector:
            saved_id = save_session(
                source_file=source_file,
                decisions=decisions,
                agent_trace=trace,
                patched_code=patched_code,
                llm_provider=provider,
                model=model,
            )
            console.print(f"\n[dim]Session saved: {saved_id}[/dim]")
        else:
            console.print("\n[dim]Session save deferred to connector agent.[/dim]")
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