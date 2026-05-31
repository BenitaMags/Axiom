"""
cli.py
───────
AXIOM CLI — Adaptive eXecution & Import Optimization Module

Commands:
  axiom run <file>        — run the full pipeline on a Python file
  axiom verify <session>  — verify a session's integrity (like CodeAugur)
  axiom visualize         — print the pipeline graph topology
  axiom experiment        — run all experiment files as a batch test

INSPIRED BY CodeAugur's CLI:
  CodeAugur's CLI can run the pipeline directly (no server) or via HTTP server.
  It also has session audit commands: session list, session verify, session detail.
  We add the same verify command to AXIOM.
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

from smart_loader.core.graph import run_loader
from smart_loader.core.telemetry import verify_session
from smart_loader.core.state import LoadDecision

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
    file:       str  = typer.Argument(..., help="Path to the Python file to analyze"),
    save:       bool = typer.Option(False, "--save", "-s", help="Save the optimized file"),
    output:     str  = typer.Option("", "--output", "-o", help="Output path for saved file"),
    show_patch: bool = typer.Option(True, "--show-patch/--no-patch", help="Print patched source"),
    llm:        str  = typer.Option("ollama", "--llm", "-l", help="LLM provider: ollama | claude"),
    model:      str  = typer.Option("qwen3-coder-next", "--model", "-m", help="Model name"),
    api_key:    str  = typer.Option("", "--api-key", envvar="ANTHROPIC_API_KEY"),
):
    """Analyze a Python file and optimize its imports."""
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if llm == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        app.print("[red]Error: ANTHROPIC_API_KEY not set.[/red]")
        raise typer.Exit(1)

    app.print(BANNER)
    app.print(Panel(
        f"[bold]Target:[/bold] [cyan]{file}[/cyan]\n"
        f"[bold]LLM:[/bold]    [magenta]{llm.upper()}[/magenta] / [yellow]{model}[/yellow]\n"
        f"[bold]Time:[/bold]   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        title="AXIOM — Run", border_style="cyan",
    ))

    try:
        final_state = run_loader(file, llm_provider=llm, model=model)
    except Exception as e:
        import traceback
        app.print(f"[bold red]Pipeline error:[/bold red] {e}")
        app.print(traceback.format_exc())
        raise typer.Exit(1)

    if final_state.get("error"):
        app.print(f"[bold red]Error:[/bold red] {final_state['error']}")
        raise typer.Exit(1)

    # ── Summary ────────────────────────────────────────────────────────────────
    app.print(Rule("[bold green]── Final Summary ──"))
    decisions: list[LoadDecision] = final_state.get("decisions", [])

    if decisions:
        table = Table(header_style="bold green")
        table.add_column("Role")
        table.add_column("Original")
        table.add_column("Winner")
        table.add_column("Confidence")
        table.add_column("Rationale")
        for d in decisions:
            conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(d.confidence, "white")
            table.add_row(
                d.role,
                d.original,
                f"[green]{d.winner}[/green]" if d.winner != d.original else d.winner,
                f"[{conf_color}]{d.confidence}[/{conf_color}]",
                d.rationale[:80] + "..." if len(d.rationale) > 80 else d.rationale,
            )
        app.print(table)
    else:
        app.print("  [green]✅ All imports already optimal.[/green]")

    # ── Agent trace summary ────────────────────────────────────────────────────
    app.print(Rule("[dim]── Agent Trace ──"))
    for event in final_state.get("agent_trace", []):
        app.print(f"  [dim][{event.stage}] {event.event}[/dim]")

    # ── Patched source ─────────────────────────────────────────────────────────
    patched = final_state.get("patched_code", "")
    if show_patch and patched:
        app.print(Rule("[bold cyan]── Patched Source ──"))
        app.print(Syntax(patched, "python", theme="monokai", line_numbers=True))

    # ── Save ───────────────────────────────────────────────────────────────────
    if save and patched:
        if not output:
            p = Path(file)
            output = str(p.parent / (p.stem + "_optimized" + p.suffix))
        Path(output).write_text(patched, encoding="utf-8")
        app.print(f"\n[bold green]✓ Saved to:[/bold green] [cyan]{output}[/cyan]\n")


@cli.command()
def verify(
    session_id: str = typer.Argument(..., help="Session ID to verify"),
):
    """
    Verify a session's integrity.
    Inspired by CodeAugur's /sessions/{id}/verify endpoint.
    """
    app.print(BANNER)
    result = verify_session(session_id)
    if result.get("integrity_ok"):
        app.print(f"[bold green]✓ Session {session_id}: integrity OK[/bold green]")
    else:
        app.print(f"[bold red]✗ Session {session_id}: integrity FAILED[/bold red]")
        app.print(f"  Stored:     {result.get('stored')}")
        app.print(f"  Recomputed: {result.get('recomputed')}")


@cli.command()
def experiment(
    llm:   str = typer.Option("ollama", "--llm", "-l"),
    model: str = typer.Option("qwen3-coder-next", "--model", "-m"),
):
    """Run all experiment files as a batch test."""
    app.print(BANNER)
    exp_dir = Path(__file__).parent / "experiment_libs"
    files   = sorted(exp_dir.glob("*.py"))

    app.print(Panel(
        f"Running {len(files)} experiment file(s)\nLLM: {llm.upper()} / {model}",
        title="AXIOM — Experiment Batch", border_style="cyan",
    ))

    results = []
    for f in files:
        app.print(f"\n[bold cyan]→ {f.name}[/bold cyan]")
        try:
            state = run_loader(str(f), llm_provider=llm, model=model)
            decisions = state.get("decisions", [])
            groups    = state.get("equivalence_groups", [])
            results.append({
                "file":      f.name,
                "groups":    len(groups),
                "decisions": len(decisions),
                "error":     state.get("error"),
            })
        except Exception as e:
            results.append({"file": f.name, "groups": 0, "decisions": 0, "error": str(e)})

    # ── Batch summary ──────────────────────────────────────────────────────────
    app.print(Rule("[bold]── Experiment Batch Summary ──"))
    table = Table(header_style="bold")
    table.add_column("File")
    table.add_column("Groups Found", justify="right")
    table.add_column("Optimizations", justify="right")
    table.add_column("Status")
    for r in results:
        status = "[red]ERROR[/red]" if r["error"] else "[green]OK[/green]"
        table.add_row(r["file"], str(r["groups"]), str(r["decisions"]), status)
    app.print(table)


@cli.command()
def visualize():
    """Print the AXIOM pipeline graph topology."""
    app.print(BANNER)
    app.print(Panel(
        """[bold cyan]AXIOM LangGraph Pipeline Topology[/bold cyan]

  ┌─────────────────────────────────────────────────────┐
  │  parser_agent                                        │
  │  → Reads .py file, extracts imports via AST         │
  │  → No LLM (deterministic)                           │
  └──────────────────────┬──────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────┐
  │  rules_agent          [INSPIRED BY CodeAugur]        │
  │  → is_installed, has_metadata, api_surface_overlap  │
  │  → Disqualifies unavailable packages (no LLM)       │
  └──────────────────────┬──────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────┐
  │  resolver_agent                                      │
  │  → LLM: finds equivalence groups + pitfalls         │
  └──────────────────────┬──────────────────────────────┘
                         │
         ┌───────────────▼────────────────────┐
         │  equivalence groups found?          │
         │  YES → continue   NO → END         │
         └───────────────┬────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────┐
  │  profiler_agent                                      │
  │  → subprocess benchmarking (import time + memory)   │
  │  → No LLM (deterministic)                           │
  └──────────────────────┬──────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────┐
  │  AXIOM agent          [INSPIRED BY CodeAugur]        │
  │  → LLM: API compat scoring                          │
  │  → Weighted scoring: speed+memory+availability+api  │
  │  → Confidence: HIGH/MEDIUM/LOW                      │
  │  → AST rewrite + telemetry session saved            │
  └──────────────────────┬──────────────────────────────┘
                         │
                        END
""",
        title="AXIOM — Graph", border_style="cyan",
    ))


def main():
    cli()

if __name__ == "__main__":
    main()