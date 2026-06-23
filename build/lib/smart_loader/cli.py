"""
cli.py  (UPDATED)
───────────────────
AXIOM CLI — Adaptive eXecution & Import Optimization Module

New commands / flags:
  axiom run <file> --security      — enable OSV/PyPI security scanning (default: on)
  axiom run <file> --connector     — generate API adapter shims (default: on)
  axiom run <file> --save-connectors  — write connector modules to disk
  axiom dashboard                  — start the token-usage dashboard (port 7788)
  axiom security <package>         — one-off security scan of a package

All other commands unchanged from the original.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from datetime import datetime

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.rule import Rule
from rich.table import Table

app = Console()
cli = typer.Typer(
    name="axiom",
    help="🧠 AXIOM — Adaptive eXecution & Import Optimization Module.",
    add_completion=False,
)

BANNER = """
[bold cyan]
   █████╗ ██╗  ██╗██╗ ██████╗ ███╗   ███╗
  ██╔══██╗╚██╗██╔╝██║██╔═══██╗████╗ ████║
  ███████║ ╚███╔╝ ██║██║   ██║██╔████╔██║
  ██╔══██║ ██╔██╗ ██║██║   ██║██║╚██╔╝██║
  ██║  ██║██╔╝ ██╗██║╚██████╔╝██║ ╚═╝ ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝ ╚═════╝ ╚═╝     ╚═╝
[/bold cyan]
[bold white]  Adaptive eXecution & Import Optimization Module[/bold white]
[dim]  Multi-Agent OS-Loader Simulation · LangGraph + Ollama/Claude[/dim]
"""


@cli.command()
def run(
    file:             str  = typer.Argument(..., help="Path to the Python file to analyze"),
    save:             bool = typer.Option(False,  "--save",             "-s",  help="Save the optimized file"),
    output:           str  = typer.Option("",     "--output",           "-o",  help="Output path for saved file"),
    show_patch:       bool = typer.Option(True,   "--show-patch/--no-patch",   help="Print patched source"),
    llm:              str  = typer.Option("ollama","--llm",             "-l",  help="LLM provider: ollama | claude"),
    model:            str  = typer.Option("qwen3-coder-next", "--model","-m",  help="Model name"),
    api_key:          str  = typer.Option("",     "--api-key",  envvar="ANTHROPIC_API_KEY"),
    security:         bool = typer.Option(True,   "--security/--no-security",  help="Run OSV+PyPI security scan"),
    connector:        bool = typer.Option(True,   "--connector/--no-connector",help="Generate API adapter shims"),
    save_connectors:  bool = typer.Option(False,  "--save-connectors",         help="Write connector .py files to disk"),
    connector_dir:    str  = typer.Option("./axiom_connectors", "--connector-dir", help="Directory for connector files"),
    show_dashboard:   bool = typer.Option(False,  "--dashboard",               help="Print the dashboard URL after the run"),
):
    """Analyze a Python file and optimize its imports."""

    file_path = Path(file).resolve()
    if not file_path.is_file():
        app.print(f"[red]Error: File not found or not a file:[/red] {file}")
        raise typer.Exit(1)

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if llm == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        app.print("[red]Error: ANTHROPIC_API_KEY not set.[/red]")
        raise typer.Exit(1)

    app.print(BANNER)
    app.print(Panel(
        f"[bold]Target:[/bold]    [cyan]{file}[/cyan]\n"
        f"[bold]LLM:[/bold]       [magenta]{llm.upper()}[/magenta] / [yellow]{model}[/yellow]\n"
        f"[bold]Security:[/bold]  {'[green]ON[/green]' if security else '[dim]OFF[/dim]'}\n"
        f"[bold]Connector:[/bold] {'[green]ON[/green]' if connector else '[dim]OFF[/dim]'}\n"
        f"[bold]Time:[/bold]      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        title="AXIOM — Run", border_style="cyan",
    ))

    try:
        from smart_loader.core.graph import run_loader
        final_state = run_loader(
            file,
            llm_provider=llm,
            model=model,
            enable_security=security,
            enable_connector=connector,
        )
    except Exception as e:
        import traceback
        app.print(f"[bold red]Pipeline error:[/bold red] {e}")
        app.print(traceback.format_exc())
        raise typer.Exit(1)

    if final_state.get("error"):
        app.print(f"[bold red]Error:[/bold red] {final_state['error']}")
        raise typer.Exit(1)

    # ── Security summary ───────────────────────────────────────────────────────
    sec_results = final_state.get("security_results", {})
    if sec_results:
        app.print(Rule("[bold red]── Security Summary ──"))
        risk_color = {"CRITICAL":"red","HIGH":"red","MEDIUM":"yellow","LOW":"green","UNKNOWN":"dim"}
        stable = Table(header_style="bold red", show_header=True)
        stable.add_column("Package")
        stable.add_column("Version")
        stable.add_column("Risk")
        stable.add_column("CVEs", justify="right")
        stable.add_column("Score", justify="right")
        stable.add_column("Notes")
        for pkg, res in sorted(sec_results.items(), key=lambda x: getattr(x[1],'overall_score',1)):
            risk  = getattr(res, 'risk_level', 'UNKNOWN')
            color = risk_color.get(risk, "white")
            vulns = len(getattr(res, 'vulnerabilities', []))
            score = getattr(res, 'overall_score', 0.0)
            notes = (getattr(res, 'notes', []) or [""])
            note  = notes[0][:60] if notes else ""
            stable.add_row(
                pkg, getattr(res,'version','?'),
                f"[{color}]{risk}[/{color}]",
                str(vulns), f"{score:.2f}", note,
            )
        app.print(stable)

    # ── Decisions summary ──────────────────────────────────────────────────────
    app.print(Rule("[bold green]── Optimization Decisions ──"))
    from smart_loader.core.state import LoadDecision
    decisions: list[LoadDecision] = final_state.get("decisions", [])

    if decisions:
        table = Table(header_style="bold green")
        table.add_column("Role")
        table.add_column("Original")
        table.add_column("Winner")
        table.add_column("Confidence")
        table.add_column("Security")
        table.add_column("Rationale")
        for d in decisions:
            conf_color = {"HIGH":"green","MEDIUM":"yellow","LOW":"red"}.get(d.confidence,"white")
            sec_val    = d.score.get("security", "—") if d.score else "—"
            sec_color  = "green" if isinstance(sec_val, float) and sec_val >= 0.75 else "yellow"
            table.add_row(
                d.role, d.original,
                f"[green]{d.winner}[/green]" if d.winner != d.original else d.winner,
                f"[{conf_color}]{d.confidence}[/{conf_color}]",
                f"[{sec_color}]{sec_val}[/{sec_color}]",
                d.rationale[:70] + "..." if len(d.rationale) > 70 else d.rationale,
            )
        app.print(table)
    else:
        app.print("  [green]✅ All imports already optimal.[/green]")

    # ── Connector summary ──────────────────────────────────────────────────────
    connectors: dict = final_state.get("connectors", {})
    if connectors:
        app.print(Rule("[bold blue]── Generated Connectors ──"))
        for orig, code in connectors.items():
            module_name = f"axiom_{orig.replace('-','_')}_compat"
            line_count  = code.count("\n") + 1
            app.print(f"  [cyan]{module_name}.py[/cyan]  ({line_count} lines)  "
                      f"[dim]{orig} → {next((d.winner for d in decisions if d.original==orig), '?')}[/dim]")

        if save_connectors:
            cdir = Path(connector_dir)
            cdir.mkdir(parents=True, exist_ok=True)
            for orig, code in connectors.items():
                fname = cdir / f"axiom_{orig.replace('-','_')}_compat.py"
                fname.write_text(code, encoding="utf-8")
                app.print(f"  [green]Saved:[/green] {fname}")

        # Show first connector inline if show_patch is on
        if show_patch and connectors:
            first_orig  = next(iter(connectors))
            first_code  = connectors[first_orig]
            module_name = f"axiom_{first_orig.replace('-','_')}_compat"
            app.print(Rule(f"[bold blue]── {module_name}.py ──"))
            app.print(Syntax(first_code[:2000] + ("\n..." if len(first_code) > 2000 else ""),
                             "python", theme="monokai", line_numbers=True))

    # ── Patched source ─────────────────────────────────────────────────────────
    patched = final_state.get("patched_code", "")
    if show_patch and patched:
        app.print(Rule("[bold cyan]── Patched Source ──"))
        app.print(Syntax(patched, "python", theme="monokai", line_numbers=True))

    # ── Token usage summary ────────────────────────────────────────────────────
    try:
        from smart_loader.core.token_tracker import get_summary
        summary = get_summary()
        if summary.get("total_calls", 0) > 0:
            app.print(Rule("[dim]── Token Usage (this run) ──"))
            by_agent = summary.get("by_agent", {})
            for agent, stats in by_agent.items():
                app.print(f"  [dim]{agent:25s}  in={stats['input']:>6,}  out={stats['output']:>5,}  calls={stats['calls']}[/dim]")
            total_in  = summary.get("total_input", 0)
            total_out = summary.get("total_output", 0)
            app.print(f"  [dim]{'TOTAL':25s}  in={total_in:>6,}  out={total_out:>5,}[/dim]")
            if show_dashboard:
                app.print(f"\n  Dashboard: [bold cyan]http://localhost:7788[/bold cyan]  (start with: axiom dashboard)")
    except Exception:
        pass

    # ── Agent trace ────────────────────────────────────────────────────────────
    app.print(Rule("[dim]── Agent Trace ──"))
    for event in final_state.get("agent_trace", []):
        app.print(f"  [dim][{event.stage}] {event.event}[/dim]")

    # ── Save optimized file ────────────────────────────────────────────────────
    if save and patched:
        if not output:
            p = Path(file)
            output = str(p.parent / (p.stem + "_optimized" + p.suffix))
        Path(output).write_text(patched, encoding="utf-8")
        app.print(f"\n[bold green]✓ Saved to:[/bold green] [cyan]{output}[/cyan]\n")


@cli.command()
def dashboard(
    port: int = typer.Option(7788, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
):
    """
    Start the AXIOM token-usage dashboard.
    Opens at http://localhost:7788 by default.
    Shows live token usage charts, security results, and session history.
    """
    app.print(BANNER)
    app.print(Panel(
        f"[bold]Dashboard URL:[/bold] [cyan]http://{host}:{port}[/cyan]\n"
        f"[bold]Logs dir:[/bold]      [dim]{os.environ.get('AXIOM_LOGS_DIR', './axiom_logs')}[/dim]\n\n"
        f"[dim]Auto-refreshes every 15 seconds. Ctrl+C to stop.[/dim]",
        title="AXIOM — Dashboard", border_style="cyan",
    ))
    try:
        from smart_loader.dashboard.app import serve
        serve(host=host, port=port)
    except ImportError:
        # Fallback: try direct import path
        sys.path.insert(0, str(Path(__file__).parent))
        try:
            import uvicorn
            # Build a minimal app if dashboard module not installed yet
            app.print("[yellow]Running dashboard from standalone module...[/yellow]")
            uvicorn.run(
                "smart_loader.dashboard.app:app",
                host=host, port=port, log_level="warning",
                reload=False,
            )
        except Exception as e:
            app.print(f"[red]Dashboard failed to start: {e}[/red]")
            app.print("[dim]Install: pip install fastapi uvicorn[/dim]")
            raise typer.Exit(1)


@cli.command()
def security(
    package: str = typer.Argument(..., help="Package name to scan"),
):
    """One-off security scan for a single package via OSV + PyPI."""
    app.print(BANNER)
    try:
        from smart_loader.agents.security_agent import _compute_security_result
        app.print(f"[dim]Scanning [cyan]{package}[/cyan] via OSV + PyPI...[/dim]\n")
        result = _compute_security_result(package)

        risk_color = {"CRITICAL":"red","HIGH":"red","MEDIUM":"yellow","LOW":"green","UNKNOWN":"dim"}
        color = risk_color.get(result.risk_level, "white")

        app.print(Panel(
            f"[bold]Package:[/bold]      {result.package}\n"
            f"[bold]Version:[/bold]      {result.version}\n"
            f"[bold]Risk level:[/bold]   [{color}]{result.risk_level}[/{color}]\n"
            f"[bold]CVEs found:[/bold]   {len(result.vulnerabilities)}\n"
            f"[bold]Vuln score:[/bold]   {result.vuln_score:.2f}\n"
            f"[bold]Freshness:[/bold]    {result.freshness_score:.2f}  "
              f"({result.days_since_update}d since last release)\n"
            f"[bold]Overall:[/bold]      {result.overall_score:.2f}",
            title=f"Security Report — {package}",
            border_style=color,
        ))

        if result.vulnerabilities:
            table = Table(title="Vulnerabilities", header_style=f"bold {color}")
            table.add_column("ID")
            table.add_column("Severity")
            table.add_column("CVSS")
            table.add_column("Summary")
            table.add_column("Fixed In")
            for v in result.vulnerabilities:
                sev_color = risk_color.get(v.severity, "white")
                table.add_row(
                    v.id,
                    f"[{sev_color}]{v.severity}[/{sev_color}]",
                    str(v.cvss_score) if v.cvss_score else "—",
                    v.summary[:60],
                    v.fixed_version or "—",
                )
            app.print(table)

        if result.notes:
            app.print("\n[bold]Notes:[/bold]")
            for note in result.notes:
                app.print(f"  {note}")

    except ImportError:
        app.print("[red]security_agent module not found. Ensure smart_loader is installed.[/red]")
        raise typer.Exit(1)


@cli.command()
def verify(
    session_id: str = typer.Argument(..., help="Session ID to verify"),
):
    """Verify a session's integrity (mirrors CodeAugur's /sessions/{id}/verify)."""
    app.print(BANNER)
    from smart_loader.core.telemetry import verify_session
    result = verify_session(session_id)
    if result.get("integrity_ok"):
        app.print(f"[bold green]✓ Session {session_id}: integrity OK[/bold green]")
    else:
        app.print(f"[bold red]✗ Session {session_id}: integrity FAILED[/bold red]")
        app.print(f"  Stored:     {result.get('stored')}")
        app.print(f"  Recomputed: {result.get('recomputed')}")


@cli.command()
def experiment(
    llm:       str  = typer.Option("ollama", "--llm",      "-l"),
    model:     str  = typer.Option("qwen3-coder-next", "--model", "-m"),
    security:  bool = typer.Option(True,  "--security/--no-security"),
    connector: bool = typer.Option(True,  "--connector/--no-connector"),
):
    """Run all experiment files as a batch test."""
    app.print(BANNER)
    from smart_loader.core.graph import run_loader

    exp_dir = Path(__file__).parent / "experiment_libs"
    files   = sorted(exp_dir.glob("*.py"))

    app.print(Panel(
        f"Running {len(files)} experiment file(s)\n"
        f"LLM: {llm.upper()} / {model}\n"
        f"Security: {'ON' if security else 'OFF'}  Connector: {'ON' if connector else 'OFF'}",
        title="AXIOM — Experiment Batch", border_style="cyan",
    ))

    results = []
    for f in files:
        app.print(f"\n[bold cyan]→ {f.name}[/bold cyan]")
        try:
            state = run_loader(
                str(f), llm_provider=llm, model=model,
                enable_security=security, enable_connector=connector,
            )
            decisions   = state.get("decisions", [])
            groups      = state.get("equivalence_groups", [])
            connectors  = state.get("connectors", {})
            sec_results = state.get("security_results", {})
            high_risk   = [p for p, r in sec_results.items()
                           if getattr(r, 'risk_level', '') in ("CRITICAL","HIGH")]
            results.append({
                "file":       f.name,
                "groups":     len(groups),
                "decisions":  len(decisions),
                "connectors": len(connectors),
                "high_risk":  high_risk,
                "error":      state.get("error"),
            })
        except Exception as e:
            results.append({"file": f.name, "groups": 0, "decisions": 0,
                            "connectors": 0, "high_risk": [], "error": str(e)})

    from rich.rule import Rule
    app.print(Rule("[bold]── Experiment Batch Summary ──"))
    table = Table(header_style="bold")
    table.add_column("File")
    table.add_column("Groups", justify="right")
    table.add_column("Decisions", justify="right")
    table.add_column("Connectors", justify="right")
    table.add_column("High-Risk Pkgs")
    table.add_column("Status")
    for r in results:
        status = "[red]ERROR[/red]" if r["error"] else "[green]OK[/green]"
        risk   = ", ".join(r.get("high_risk", [])) or "—"
        table.add_row(r["file"], str(r["groups"]), str(r["decisions"]),
                      str(r["connectors"]), risk, status)
    app.print(table)


@cli.command()
def visualize():
    """Print the AXIOM pipeline graph topology."""
    app.print(BANNER)
    app.print(Panel(
        """[bold cyan]AXIOM LangGraph Pipeline Topology (v2)[/bold cyan]

  ┌─────────────────────────────────────────────────────────────┐
  │  parser_agent                                                │
  │  → Reads .py file, extracts imports via AST                 │
  │  → No LLM (deterministic)                                   │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  rules_agent          [INSPIRED BY CodeAugur]               │
  │  → is_installed, has_metadata, api_surface_overlap          │
  │  → Disqualifies unavailable packages (no LLM)               │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  security_agent       ★ NEW                                  │
  │  → OSV API: CVE scan per package + version                  │
  │  → PyPI API: release freshness, yanked versions             │
  │  → Parallel HTTP (no LLM) — CRITICAL/HIGH flagged           │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  resolver_agent                                              │
  │  → LLM call #1: equivalence groups + pitfalls               │
  │  → Security-annotated: CVE context in prompt                │
  │  → Token usage recorded → axiom_logs/token_usage.jsonl      │
  └──────────────────────┬──────────────────────────────────────┘
                         │
         ┌───────────────▼──────────────────────┐
         │  equivalence groups found?            │
         │  YES → continue   NO → END           │
         └───────────────┬──────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  profiler_agent                                              │
  │  → subprocess benchmarking (import time + memory)           │
  │  → No LLM (deterministic)                                   │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  axiom_agent          [INSPIRED BY CodeAugur]               │
  │  → LLM call #2: API compat scoring                          │
  │  → Security score now part of weighted formula:             │
  │      speed(35%) + memory(10%) + avail(20%)                  │
  │      + api_match(15%) + security(20%)   ← NEW weight        │
  │  → Confidence: HIGH/MEDIUM/LOW                              │
  │  → AST rewrite + telemetry session saved                    │
  │  → Token usage recorded                                     │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  connector_agent      ★ NEW                                  │
  │  → LLM call #3: generates adapter shim per replacement      │
  │  → Input/output transformer: old API → new package          │
  │  → LLM call #4: rewrites source imports to use shims        │
  │  → Connector files saved to ./axiom_connectors/             │
  │  → Token usage recorded                                     │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  DASHBOARD                                                   │
  │  → http://localhost:7788  (run: axiom dashboard)            │
  │  → Live token charts: timeline, by-agent, per-session       │
  │  → Security risk table, session audit log                   │
  └─────────────────────────────────────────────────────────────┘
""",
        title="AXIOM — Graph v2", border_style="cyan",
    ))


def main():
    cli()

if __name__ == "__main__":
    main()