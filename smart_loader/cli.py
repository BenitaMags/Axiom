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
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime

# Load .env BEFORE anything else touches os.environ for API keys. This is
# also done in core/llm_factory.py, but doing it here too makes the
# pre-flight `ANTHROPIC_API_KEY` check below (which runs before run_loader
# is ever imported in some code paths) see the same environment that
# llm_factory will see — otherwise the two checks could disagree depending
# on import order, which is exactly the kind of inconsistency that turns
# into a confusing remote 401 instead of a clear local error.
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

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

_PROVIDER_API_KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-compat": "OPENAI_COMPAT_API_KEY",
}


def _apply_provider_api_key(provider: str, api_key: str) -> None:
    """Make an explicit --api-key reach the selected LLM provider."""
    if api_key and provider in _PROVIDER_API_KEY_ENV:
        os.environ[_PROVIDER_API_KEY_ENV[provider]] = api_key.strip()

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


def _canonical_distribution(name: str) -> str:
    """Return a PEP 503-style comparison key without importing project code."""
    return re.sub(r"[-_.]+", "-", str(name or "").casefold())


def _project_requirement_inputs(project_root: Path, raw: str) -> tuple[list[str], list[str]]:
    """Split --requirements into project files and explicit requirement specs."""
    files: list[str] = []
    specs: list[str] = []
    for item in (part.strip() for part in str(raw or "").split(",")):
        if not item:
            continue
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if candidate.is_file():
            files.append(str(candidate))
        else:
            specs.append(item)
    return files, specs


def _migration_import_files(project_root: Path, source_distribution: str) -> list[tuple[Path, str]]:
    """Find files that directly import a requested source distribution.

    This is intentionally an AST-only prefilter.  It avoids running any
    project code and prevents AXIOM from claiming it migrated files that do
    not actually import the selected source package.
    """
    from smart_loader.agents.profiler_agent import IMPORT_TO_DISTRIBUTION

    source_key = _canonical_distribution(source_distribution)
    ignored = {".git", ".venv", "venv", "env", "site-packages", "__pycache__"}
    matches: list[tuple[Path, str]] = []
    for path in sorted(project_root.rglob("*.py")):
        if any(part in ignored for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        for root in sorted(roots):
            mapped = IMPORT_TO_DISTRIBUTION.get(root.casefold(), root)
            if _canonical_distribution(mapped) == source_key or _canonical_distribution(root) == source_key:
                matches.append((path, root))
                break
    return matches


def _verify_combined_project_patches(
    project_root: Path,
    patches: list[tuple[Path, str]],
    *,
    plan: dict,
    source: str,
    target: str,
    target_versions: list[dict],
    test_command: str,
    test_timeout: int,
) -> dict:
    """Test all accepted file patches together before ``migrate --save``.

    Individual file verification is necessary, but it cannot demonstrate that
    multiple independently generated edits still work together in one project
    tree.  This transaction check creates an overlay copy, applies every
    accepted patch there, and runs the caller's explicit project tests in a
    fresh target-only environment.  It never writes to the real project.
    """
    if len(patches) < 2:
        return {"status": "not_needed", "output": "One verified file; per-file verification is authoritative."}
    if not test_command.strip():
        return {
            "status": "not_configured",
            "output": "Refusing multi-file --save without --tests: AXIOM cannot prove the combined patch set is safe.",
        }

    target_key = _canonical_distribution(target)
    resolved: set[str] = set()
    for version_map in target_versions:
        for name, version in (version_map or {}).items():
            text = str(version or "").strip()
            if _canonical_distribution(name) == target_key and text.casefold() not in {
                "", "auto", "latest", "latest-compatible",
            }:
                resolved.add(text)
    if len(resolved) != 1:
        return {
            "status": "target_version_unresolved",
            "output": (
                "Accepted file runs did not agree on one exact target release; "
                "refusing a multi-file write."
            ),
        }

    from smart_loader.agents.profiler_agent import create_verification_environment
    from smart_loader.agents.verifier_agent import _run_project_tests

    env_dir = ""
    try:
        with tempfile.TemporaryDirectory(prefix="axiom-combined-patch-") as temporary:
            overlay_root = Path(temporary) / "project"
            shutil.copytree(
                project_root,
                overlay_root,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "venv", "env", "__pycache__", "*.axiom-backup",
                ),
            )
            for file_path, patched in patches:
                destination = overlay_root / file_path.relative_to(project_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(patched, encoding="utf-8")

            env_dir, sandbox_python, failures, _ = create_verification_environment(
                list(plan.get("requirements", []) or []),
                source,
                [target],
                include_source=False,
                python_executable=str(plan.get("selected_python", "") or ""),
                target_versions={target: next(iter(resolved))},
            )
            if failures:
                detail = "; ".join(f"{name}: {reason}" for name, reason in failures.items())
                return {
                    "status": "environment_incomplete",
                    "output": f"Combined-patch test environment could not be prepared: {detail}",
                }
            return _run_project_tests(
                test_command,
                python_executable=sandbox_python,
                cwd=str(overlay_root),
                import_paths=[str(overlay_root), str(overlay_root / "src")],
                timeout=test_timeout,
            )
    except Exception as exc:
        return {"status": "error", "output": f"Combined-patch verification raised: {exc}"}
    finally:
        if env_dir:
            shutil.rmtree(env_dir, ignore_errors=True)


def _print_environment_plan(plan: dict) -> None:
    """Render a compact, human-checkable environment plan."""
    table = Table(title="AXIOM Environment Plan", header_style="bold cyan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Status", str(plan.get("interpreter_status", "unknown")))
    table.add_row("Selected Python", str(plan.get("selected_python", "") or "—"))
    table.add_row("Python contract", str(plan.get("python_constraint", "") or plan.get("detected_python", "") or "not declared"))
    table.add_row("Base requirements", str(len(plan.get("requirements", []) or [])))
    table.add_row("Target", str(plan.get("target_requirement", "") or plan.get("target_distribution", "") or "—"))
    table.add_row("Tests", str(plan.get("test_command", "") or "not configured"))
    app.print(table)
    for warning in plan.get("warnings", []) or []:
        app.print(f"[yellow]• {warning}[/yellow]")
    for hint in plan.get("recovery_hints", []) or []:
        app.print(f"[dim]• {hint}[/dim]")


@cli.command()
def run(
    file:             str  = typer.Argument(..., help="Path to the Python file to analyze"),
    save:             bool = typer.Option(False,  "--save",             "-s",  help="Save the optimized file (also writes versioned diff+perf report)"),
    output:           str  = typer.Option("",     "--output",           "-o",  help="Output path for saved file"),
    show_patch:       bool = typer.Option(True,   "--show-patch/--no-patch",   help="Print patched source"),
    llm:              str  = typer.Option("ollama","--llm",             "-l",  help="LLM provider: ollama | claude | nvidia | openai | openai-compat"),
    model:            str  = typer.Option("qwen3-coder-next", "--model","-m",  help="Model name"),
    api_key:          str  = typer.Option("",     "--api-key"),
    security:         bool = typer.Option(True,   "--security/--no-security",  help="Run OSV+PyPI security scan"),
    connector:        bool = typer.Option(True,   "--connector/--no-connector",help="Generate API adapter shims"),
    save_connectors:  bool = typer.Option(False,  "--save-connectors",         help="Write connector .py files to disk"),
    connector_dir:    str  = typer.Option("./axiom_connectors", "--connector-dir", help="Directory for connector files"),
    show_dashboard:   bool = typer.Option(False,  "--dashboard",               help="Print the dashboard URL after the run"),
    feedback:         bool = typer.Option(False,  "--feedback/--no-feedback",  help="Human-in-the-loop accept/reject review before saving (implies --save semantics)"),
    non_interactive:  bool = typer.Option(False,  "--non-interactive",         help="With --feedback, auto-accept all changes (for CI)"),
    enable_isomorph:  bool = typer.Option(False,  "--enable-isomorph",         help="After the connector, produce an equivalence-checked isomorphic variant of the patched source (opt-in; off by default)"),
    isomorph_target_lines: int = typer.Option(0,  "--isomorph-target-lines",   help="Absolute line count to aim the variant at. Takes precedence over --isomorph-target-ratio and skips the interactive prompt."),
    isomorph_target_ratio: float = typer.Option(0.0, "--isomorph-target-ratio", help="Variant size as a multiple of the current file (e.g. 1.15). Used when no absolute target is given."),
    isomorph_corpus_dir: str = typer.Option("",   "--isomorph-corpus-dir",     help="Directory holding the equivalence oracle (golden.json, test_corpus.py). Required with --enable-isomorph — the agent refuses to emit an unverified variant."),
    isomorph_use_generated: bool = typer.Option(False, "--isomorph-use-generated", help="Also use the LLM-designed transformations from generated_spts.json"),
    isomorph_ranking_metric: str = typer.Option("", "--isomorph-ranking-metric", help="Rename-insensitive metric the beam search ranks by (ast_structural | cfg_shape | composite). Default: ast_structural."),
    isomorph_artifact_dir: str = typer.Option("", "--isomorph-artifact-dir",   help="Persist every oracle-passing candidate here (off by default)"),
):
    """Analyze a Python file and optimize its imports."""

    file_path = Path(file).resolve()
    if not file_path.is_file():
        app.print(f"[red]Error: File not found or not a file:[/red] {file}")
        raise typer.Exit(1)

    _apply_provider_api_key(llm, api_key)
    if llm == "claude":
        from smart_loader.core.llm_factory import _clean_key
        if not _clean_key(os.environ.get("ANTHROPIC_API_KEY", "")):
            app.print(
                "[red]Error: ANTHROPIC_API_KEY not set in this process's "
                "environment.[/red]\n"
                "[dim]A key visible to `echo $ANTHROPIC_API_KEY` in your terminal "
                "doesn't guarantee axiom sees it — see the README for .env setup, "
                "or pass --api-key directly.[/dim]"
            )
            raise typer.Exit(1)

    app.print(BANNER)
    app.print(Panel(
        f"[bold]Target:[/bold]    [cyan]{file}[/cyan]\n"
        f"[bold]LLM:[/bold]       [magenta]{llm.upper()}[/magenta] / [yellow]{model}[/yellow]\n"
        f"[bold]Security:[/bold]  {'[green]ON[/green]' if security else '[dim]OFF[/dim]'}\n"
        f"[bold]Connector:[/bold] {'[green]ON[/green]' if connector else '[dim]OFF[/dim]'}\n"
        f"[bold]Feedback:[/bold]  {'[green]ON[/green]' if feedback else '[dim]OFF[/dim]'}\n"
        f"[bold]Time:[/bold]      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        title="AXIOM — Run", border_style="cyan",
    ))

    try:
        run_start = time.time()
        from smart_loader.core.graph import run_loader
        final_state = run_loader(
            file,
            llm_provider=llm,
            model=model,
            enable_security=security,
            enable_connector=connector,
            enable_isomorph=enable_isomorph,
            isomorph_target_lines=isomorph_target_lines,
            isomorph_target_ratio=isomorph_target_ratio,
            isomorph_corpus_dir=isomorph_corpus_dir,
            isomorph_use_generated=isomorph_use_generated,
            # The prompt is the point of the interactive path, so it is on
            # whenever the feature is and the run is not explicitly a batch
            # one. An explicit --isomorph-target-lines still bypasses it, and
            # the agent additionally requires a real tty before blocking.
            isomorph_interactive=(enable_isomorph and not non_interactive
                                  and not isomorph_target_lines),
            isomorph_ranking_metric=isomorph_ranking_metric,
            isomorph_artifact_dir=isomorph_artifact_dir,
        )
    except Exception as e:
        import traceback
        app.print(f"[bold red]Pipeline error:[/bold red] {e}")
        app.print(traceback.format_exc())
        raise typer.Exit(1)

    if final_state.get("error"):
        app.print(f"[bold red]Error:[/bold red] {final_state['error']}")
        raise typer.Exit(1)

    # ── Logic Substitution summary ─────────────────────────────────────────────
    # Requested vs achieved, stated plainly. The length target is best-effort,
    # so the miss is a result to publish rather than a failure to bury.
    iso = final_state.get("isomorph_report") or {}
    if iso:
        miss = iso.get("length_error", 0)
        app.print(Rule("[bold cyan]── Isomorphic Variant ──"))
        itable = Table(header_style="bold cyan", show_header=False)
        itable.add_column("Property")
        itable.add_column("Value", overflow="fold")
        itable.add_row("Lines (original → variant)",
                       f"{iso.get('original_lines')} → {iso.get('achieved_lines')}")
        itable.add_row("Requested", str(iso.get("requested_lines")))
        itable.add_row("Miss (achieved − requested)",
                       f"{miss:+d}" + ("   [ON TARGET]" if miss == 0 else
                                       f"   [{abs(miss)} line(s) "
                                       f"{'over' if miss > 0 else 'short'}]"))
        itable.add_row("Transformations",
                       ", ".join(iso.get("applied", [])) or "none applicable")
        itable.add_row("Equivalence oracle",
                       "[green]PASS[/green]" if iso.get("oracle_passed")
                       else "[red]FAIL[/red]")
        for name, value in (iso.get("diversity") or {}).items():
            mark = "  ← ranking signal" if name == iso.get("ranking_metric") else ""
            itable.add_row(f"diversity: {name}", f"{value:.4f}{mark}")
        app.print(itable)

    # ── Security summary ───────────────────────────────────────────────────────
    sec_results = final_state.get("security_results", {})
    if sec_results:
        app.print(Rule("[bold red]── Security Summary ──"))
        risk_color = {"CRITICAL":"red","HIGH":"red","MEDIUM":"yellow","LOW":"green","UNKNOWN":"dim"}
        stable = Table(header_style="bold red", show_header=True)
        stable.add_column("Package")
        stable.add_column("Version")
        stable.add_column("Risk")
        stable.add_column("Evidence")
        stable.add_column("CVEs", justify="right")
        stable.add_column("Score", justify="right")
        stable.add_column("Notes")
        seen_results: set[int] = set()
        for pkg, res in sorted(
            sec_results.items(),
            key=lambda x: (
                0 if getattr(x[1], "data_status", "available") == "available" else 1,
                getattr(x[1], "overall_score", 1),
            ),
        ):
            # Source import and distribution keys can intentionally point to
            # the same historical evidence (e.g. kafka -> kafka-python).
            if id(res) in seen_results:
                continue
            seen_results.add(id(res))
            risk  = getattr(res, 'risk_level', 'UNKNOWN')
            color = risk_color.get(risk, "white")
            status = getattr(res, "data_status", "available")
            evidence = {
                "available": "AVAILABLE", "partial": "PARTIAL", "unavailable": "UNAVAILABLE",
                "not_found": "NOT FOUND", "not_applicable": "N/A",
            }.get(status, str(status).upper())
            vulns = str(len(getattr(res, 'vulnerabilities', []))) if status == "available" else (
                "—" if status == "not_applicable" else "?"
            )
            score = f"{getattr(res, 'overall_score', 0.0):.2f}" if status == "available" else "—"
            notes = (getattr(res, 'notes', []) or [""])
            note  = notes[0][:60] if notes else ""
            version = getattr(res, 'version', '?')
            latest = getattr(res, "latest_version", None)
            if getattr(res, "version_origin", "") == "pinned" and latest:
                version = f"{version} (latest {latest})"
            stable.add_row(
                pkg, version,
                f"[{color}]{risk}[/{color}]",
                evidence, vulns, score, note,
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
    # connector_agent stores each entry as a metadata dict, not a raw string:
    #   { "functions": [str], "call_rewrites": [...], "compatible": bool }
    # The actual generated wrapper code lives in patched_code (already injected
    # inline), not in a separate per-package code string.
    connectors: dict = final_state.get("connectors", {})
    if connectors:
        app.print(Rule("[bold blue]── Generated Connectors ──"))
        for orig, meta in connectors.items():
            if not isinstance(meta, dict):
                continue
            winner = next((d.winner for d in decisions if d.original == orig), "?")
            fns = meta.get("functions", [])
            compatible = meta.get("compatible", True)
            status = "[green]drop-in[/green]" if compatible else "[yellow]adapter injected[/yellow]"
            fn_list = ", ".join(fns) if fns else "—"
            app.print(
                f"  [cyan]{orig}[/cyan] → [green]{winner}[/green]  "
                f"{status}  wrappers: [dim]{fn_list}[/dim]"
            )

        if save_connectors:
            # connector_agent injects wrappers inline into patched_code rather
            # than producing standalone .py files, so --save-connectors extracts
            # just the injected wrapper functions from the patched source and
            # writes them out as importable compat modules.
            cdir = Path(connector_dir)
            cdir.mkdir(parents=True, exist_ok=True)
            patched_for_connectors = final_state.get("patched_code", "")
            for orig, meta in connectors.items():
                if not isinstance(meta, dict):
                    continue
                fns = meta.get("functions", [])
                if not fns or not patched_for_connectors:
                    continue
                # Extract each wrapper function from the patched source by AST
                import ast as _ast
                try:
                    tree = _ast.parse(patched_for_connectors)
                    extracted = []
                    for node in _ast.walk(tree):
                        if isinstance(node, _ast.FunctionDef) and node.name in fns:
                            extracted.append(_ast.unparse(node))
                    if extracted:
                        fname = cdir / f"axiom_{orig.replace('-','_')}_compat.py"
                        header = (
                            f'"""AXIOM connector: {orig} → '
                            f'{next((d.winner for d in decisions if d.original==orig), "?")} '
                            f'— generated wrappers"""\n\n'
                        )
                        fname.write_text(header + "\n\n".join(extracted), encoding="utf-8")
                        app.print(f"  [green]Saved:[/green] {fname}")
                except Exception as e:
                    app.print(f"  [yellow]Could not extract connector for {orig}: {e}[/yellow]")

    # ── Patched source ─────────────────────────────────────────────────────────
    patched = final_state.get("patched_code", "")
    if show_patch and patched:
        app.print(Rule("[bold cyan]── Patched Source ──"))
        app.print(Syntax(patched, "python", theme="monokai", line_numbers=True))

    # ── Token usage / cost / lines-changed / timing summary ───────────────────
    # Deliberately NOT printed here — see the unified summary at the very end
    # of this function, which runs after --feedback (if any) has resolved, so
    # the numbers reflect what was actually accepted and written to disk.

    # ── Agent trace ────────────────────────────────────────────────────────────
    app.print(Rule("[dim]── Agent Trace ──"))
    for event in final_state.get("agent_trace", []):
        app.print(f"  [dim][{event.stage}] {event.event}[/dim]")

    # ── Save optimized file (with versioned diff+perf report, optional feedback) ─
    if save and patched:
        original_code = final_state.get("source_code", "")
        session_id    = final_state.get("_session_id", "unknown")

        final_code = patched
        accepted_decisions = decisions

        if feedback:
            from smart_loader.core.feedback_loop import run_feedback_loop
            final_code, accepted_decisions = run_feedback_loop(
                original_code=original_code,
                patched_code=patched,
                decisions=decisions,
                source_file=file,
                non_interactive=non_interactive,
            )
        else:
            # Even without the interactive feedback prompt, still run the
            # output-equivalence check so the user sees whether the patch
            # changed observable behavior — mirrors `diff <(orig) <(patched)`.
            from smart_loader.core.feedback_loop import check_output_equivalence
            eq = check_output_equivalence(original_code, final_code, file)
            if eq.get("skipped"):
                app.print(f"[dim]Output equivalence check skipped: {eq['skip_reason']}[/dim]")
            elif eq.get("error"):
                app.print(f"[yellow]Output equivalence check error: {eq['error']}[/yellow]")
            elif eq["equivalent"]:
                app.print("[green]✓ Output equivalence check: original and patched produce identical output.[/green]")
            else:
                app.print("[yellow]⚠ Output equivalence check: patched output differs from original — review before relying on this patch.[/yellow]")

        if not output:
            p = Path(file)
            output = str(p.parent / (p.stem + "_optimized" + p.suffix))
        Path(output).write_text(final_code, encoding="utf-8")
        app.print(f"\n[bold green]✓ Saved to:[/bold green] [cyan]{output}[/cyan]")

        # ── Versioned diff + perf report (professor's diff/perf superclass) ──────
        try:
            from smart_loader.core.diff_reporter import build_report
            benchmarks = final_state.get("benchmarks", {})
            report = build_report(
                session_id=session_id,
                source_file=file,
                original_code=original_code,
                patched_code=final_code,
                decisions=accepted_decisions,
                benchmarks=benchmarks,
            )
            if report.identical:
                app.print(f"[dim]Version {report.version} recorded — identical to previous version (no diff).[/dim]")
            else:
                added   = sum(1 for l in report.diff_lines if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in report.diff_lines if l.startswith("-") and not l.startswith("---"))
                app.print(
                    f"[dim]Version {report.version} recorded "
                    f"(+{added}/-{removed} lines, total speedup ×{report.total_speedup:.3f}). "
                    f"Use 'axiom diff {file} --to {report.version}' to inspect.[/dim]"
                )
        except Exception as e:
            app.print(f"[yellow]Versioned diff/perf report failed: {e}[/yellow]")

    # ── Unified run summary — lines changed, tokens, cost, elapsed time ────────
    # Printed LAST, after --feedback (if any) has resolved, so it reflects the
    # code that was actually accepted/written, not just what the pipeline
    # proposed before a human could reject part of it.
    if show_dashboard:
        app.print(f"\n  Dashboard: [bold cyan]http://localhost:7788[/bold cyan]  (start with: axiom dashboard)")

    elapsed = time.time() - run_start
    final_code_for_report = final_code if (save and patched) else patched
    original_code = final_state.get("source_code", "")
    from smart_loader.core.run_report import print_run_summary, line_diff
    diffs = {}
    if final_code_for_report:
        d = line_diff(original_code, final_code_for_report)
        if d.changed:
            diffs[file] = d
    print_run_summary(
        elapsed_seconds=elapsed,
        provider=llm,
        model=model,
        line_diffs=diffs,
    )


@cli.command()
def doctor(
    project: str = typer.Argument(..., help="Project directory to inspect without installing or running project code"),
    python: str = typer.Option("auto", "--python", help="Interpreter path/version, or auto from historical metadata"),
    requirements: str = typer.Option("", "--requirements", help="Requirements file(s) or comma-separated explicit specs"),
    target: str = typer.Option("", "--target", help="Optional migration target distribution"),
    target_version: str = typer.Option("auto", "--target-version", help="Target version policy or exact version"),
    tests: str = typer.Option("", "--tests", help="Optional direct project test command to record"),
    out: str = typer.Option("", "--out", help="Optional JSON path for the reproducibility plan"),
):
    """Inspect historical Python/dependency evidence before a migration run."""
    from smart_loader.core.environment_plan import plan_project_environment

    project_root = Path(project).resolve()
    requirement_files, explicit_specs = _project_requirement_inputs(project_root, requirements)
    plan = plan_project_environment(
        project_root,
        requested_python=python,
        requirement_files=requirement_files,
        base_requirements=explicit_specs or None,
        target_distribution=target,
        target_version=target_version,
        test_command=tests,
    ).to_dict()
    app.print(BANNER)
    _print_environment_plan(plan)
    if out:
        out_path = Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        app.print(f"[green]✓ Plan written:[/green] {out_path}")
    if plan.get("interpreter_status") != "ready":
        app.print("[red]AXIOM will not silently substitute a newer interpreter for this project.[/red]")
        raise typer.Exit(2)


@cli.command()
def migrate(
    project: str = typer.Argument(..., help="Project directory containing the source files to migrate"),
    source: str = typer.Option(..., "--source", help="Source PyPI distribution, e.g. kafka-python"),
    target: str = typer.Option(..., "--target", help="Target PyPI distribution, e.g. confluent-kafka"),
    target_version: str = typer.Option("auto", "--target-version", help="Exact target version or auto (newest compatible non-yanked release)"),
    python: str = typer.Option("auto", "--python", help="Historical Python executable/version, or auto"),
    requirements: str = typer.Option("", "--requirements", help="Requirements file(s) or comma-separated explicit specs"),
    tests: str = typer.Option("", "--tests", help="One direct test command, e.g. 'python -m pytest -q'"),
    test_timeout: int = typer.Option(120, "--test-timeout", min=1, help="Seconds allowed for each baseline/patched test run"),
    max_files: int = typer.Option(0, "--max-files", min=0, help="Cap matched files (0 means all)"),
    llm: str = typer.Option("ollama", "--llm", "-l", help="LLM provider"),
    model: str = typer.Option("qwen3-coder-next", "--model", "-m", help="LLM model"),
    api_key: str = typer.Option("", "--api-key"),
    connector: bool = typer.Option(True, "--connector/--no-connector", help="Allow Connector repair passes"),
    save: bool = typer.Option(False, "--save", help="Write only patches that AXIOM verified"),
    out: str = typer.Option("", "--out", help="Optional JSON report path"),
    auto_provision_python: bool = typer.Option(
        False, "--auto-provision-python",
        help="If no local interpreter satisfies this project's declared Python version, "
             "install one via uv instead of stopping. Falls back to pulling a matching "
             "official Docker image (e.g. for Python 2.x, which uv cannot provide at all) "
             "if uv can't reach that minor and Docker is available. Off by default so "
             "AXIOM never installs a Python build or pulls a container image unasked.",
    ),
):
    """Run a target-pinned, test-aware migration across matching project files.

    This command intentionally requires AXIOM's security/version-resolution
    stage.  It never replaces a source file unless the patch has survived the
    baseline comparison, static tools, import smoke test, and any supplied
    project tests in a disposable environment.
    """
    from smart_loader.core.environment_plan import plan_project_environment
    from smart_loader.core.graph import run_loader

    project_root = Path(project).resolve()
    if not project_root.is_dir():
        app.print(f"[red]Project directory does not exist:[/red] {project}")
        raise typer.Exit(1)
    _apply_provider_api_key(llm, api_key)
    if llm == "claude":
        from smart_loader.core.llm_factory import _clean_key
        if not _clean_key(os.environ.get("ANTHROPIC_API_KEY", "")):
            app.print("[red]Error: ANTHROPIC_API_KEY is not set; pass --api-key or configure .env.[/red]")
            raise typer.Exit(1)
    requirement_files, explicit_specs = _project_requirement_inputs(project_root, requirements)
    plan = plan_project_environment(
        project_root,
        requested_python=python,
        requirement_files=requirement_files,
        base_requirements=explicit_specs or None,
        target_distribution=target,
        target_version=target_version,
        test_command=tests,
        auto_provision_interpreter=auto_provision_python,
    )
    plan_dict = plan.to_dict()
    app.print(BANNER)
    _print_environment_plan(plan_dict)
    if not plan.usable:
        app.print("[red]Migration stopped: no compatible local interpreter is available for this project.[/red]")
        raise typer.Exit(2)

    matches = _migration_import_files(project_root, source)
    if max_files:
        matches = matches[:max_files]
    if not matches:
        app.print(f"[yellow]No Python files directly import the requested source distribution: {source}[/yellow]")
        raise typer.Exit(1)

    app.print(
        f"[bold cyan]Migrating {len(matches)} matching file(s):[/bold cyan] "
        f"{source} → {target} [dim](target release will be resolved before installation)[/dim]"
    )
    reports: list[dict] = []
    verified_writes: list[tuple[Path, str]] = []
    for index, (file_path, source_import) in enumerate(matches, 1):
        relative = file_path.relative_to(project_root)
        app.print(f"\n[bold cyan]→ [{index}/{len(matches)}] {relative}[/bold cyan]")
        state = run_loader(
            str(file_path),
            llm_provider=llm,
            model=model,
            enable_security=True,
            enable_connector=connector,
            isolated_profiling=True,
            base_requirements=list(plan.requirements),
            migration_source=source,
            migration_source_import=source_import,
            migration_target=target,
            migration_target_version=target_version,
            environment_plan=plan_dict,
            environment_python=plan.selected_python,
            test_command=tests,
            test_timeout_seconds=test_timeout,
            seed_target=True,
            verification_import_paths=[str(project_root), str(project_root / "src")],
            verification_project_root=str(project_root),
            verification_file_path=str(relative),
        )
        decisions = state.get("decisions", [])
        decision = next((item for item in decisions if item.winner != item.original), None)
        verification_status = str(state.get("verification_status", "not_run"))
        patched = str(state.get("patched_code", "") or "")
        original = str(state.get("source_code", "") or "")
        accepted = bool(decision and verification_status == "verified" and patched and patched != original)
        if accepted and save:
            verified_writes.append((file_path, patched))
        reports.append({
            "file": str(relative),
            "source_import": source_import,
            "selected": getattr(decision, "winner", ""),
            "verification_status": verification_status,
            "verification_environment_status": state.get("verification_environment_status", ""),
            "accepted": accepted,
            "repair_cycles": state.get("_repair_cycle_count", 0),
            "error": state.get("error", ""),
            "target_versions": state.get("migration_target_versions", {}),
            "target_resolution_failures": state.get("target_resolution_failures", {}),
            "decision_declines": state.get("decision_declines", []),
        })

    combined_project_test: dict = {"status": "not_requested", "output": "--save was not supplied."}
    write_blocked = False
    if save and len(verified_writes) > 1:
        combined_project_test = _verify_combined_project_patches(
            project_root,
            verified_writes,
            plan=plan_dict,
            source=source,
            target=target,
            target_versions=[report.get("target_versions", {}) for report in reports],
            test_command=tests,
            test_timeout=test_timeout,
        )
        if combined_project_test.get("status") != "passed":
            write_blocked = True
            app.print(
                "[red]AXIOM did not write the individually verified files because the combined "
                "project patch set was not verified.[/red]"
            )
            app.print(f"[yellow]{combined_project_test.get('output', '')}[/yellow]")
            verified_writes = []
        else:
            app.print("[green]✓ Combined project test passed; applying verified patches atomically.[/green]")
    elif save and len(verified_writes) == 1:
        combined_project_test = {
            "status": "not_needed",
            "output": "One verified file; per-file verification was used.",
        }

    if save:
        for file_path, patched in verified_writes:
            backup = file_path.with_suffix(file_path.suffix + ".axiom-backup")
            if not backup.exists():
                backup.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
            file_path.write_text(patched, encoding="utf-8")
        if verified_writes:
            app.print(f"[green]✓ Wrote {len(verified_writes)} verified patch(es); original files are preserved as *.axiom-backup.[/green]")
        else:
            app.print("[yellow]No files were written because none reached AXIOM's verified status.[/yellow]")

    summary = Table(title="AXIOM Project Migration Summary", header_style="bold green")
    summary.add_column("File")
    summary.add_column("Selected")
    summary.add_column("Verification")
    summary.add_column("Repair cycles", justify="right")
    summary.add_column("Reason")
    for report in reports:
        status = report["verification_status"]
        color = "green" if report["accepted"] else ("yellow" if status == "environment_incomplete" else "red")
        summary.add_row(
            report["file"], report["selected"] or "—", f"[{color}]{status}[/{color}]",
            str(report["repair_cycles"]), str(report["error"] or "")[:120],
        )
    app.print(summary)
    if out:
        out_path = Path(out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "plan": plan_dict,
            "results": reports,
            "combined_project_test": combined_project_test,
        }, indent=2, default=str) + "\n", encoding="utf-8")
        app.print(f"[green]✓ Migration report written:[/green] {out_path}")
    if not any(report["accepted"] for report in reports) or write_blocked:
        raise typer.Exit(1)


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

        risk_color = {"CRITICAL":"red","HIGH":"red","MEDIUM":"yellow","LOW":"green","UNKNOWN":"dim", "N/A":"dim"}
        color = risk_color.get(result.risk_level, "white")
        evidence_available = getattr(result, "data_status", "available") == "available"
        score = lambda name: f"{getattr(result, name):.2f}" if evidence_available else "—"
        freshness_days = (
            f"({result.days_since_update}d since last release)"
            if evidence_available else "(unavailable)"
        )

        app.print(Panel(
            f"[bold]Package:[/bold]      {result.package}\n"
            + (f"[bold]PyPI query:[/bold]   {result.queried_package}\n" if getattr(result, "queried_package", "") else "")
            + f"[bold]Evidence:[/bold]     {getattr(result, 'data_status', 'available').upper()}\n"
            f"[bold]Version:[/bold]      {result.version}\n"
            f"[bold]Risk level:[/bold]   [{color}]{result.risk_level}[/{color}]\n"
            f"[bold]CVEs found:[/bold]   {len(result.vulnerabilities) if evidence_available else '?'}\n"
            f"[bold]Vuln score:[/bold]   {score('vuln_score')}\n"
            f"[bold]Freshness:[/bold]    {score('freshness_score')}  {freshness_days}\n"
            f"[bold]Overall:[/bold]      {score('overall_score')}",
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
def diff(
    file:      str = typer.Argument(..., help="Path to the Python file (matches its versions/ slug)"),
    from_:     int = typer.Option(None, "--from", help="Source version (default: previous version)"),
    to:        int = typer.Option(None, "--to",   help="Target version (default: latest version)"),
):
    """
    Show a unified diff between two saved versions of a file.
    Mirrors `diff -u`. Two identical versions produce no output (null diff) —
    this is the professor's diff/perf superclass concept: AXIOM as `diff` for
    source tracking and `perf stat` for benchmark deltas (see `axiom versions`).
    """
    app.print(BANNER)
    from smart_loader.core.diff_reporter import DiffReporter

    reporter = DiffReporter(file)
    index = reporter.get_index()
    if not index:
        app.print(f"[yellow]No saved versions found for {file}. Run 'axiom run {file} --save' first.[/yellow]")
        raise typer.Exit(1)

    latest = index[-1].version
    v_to   = to if to is not None else latest
    v_from = from_ if from_ is not None else max(1, v_to - 1)

    if v_from > latest or v_to > latest or v_from < 1 or v_to < 1:
        app.print(f"[red]Version out of range. {file} has versions 1..{latest}.[/red]")
        raise typer.Exit(1)

    diff_lines = reporter.diff_between(v_from, v_to)

    app.print(Panel(
        f"[bold]File:[/bold]  [cyan]{file}[/cyan]\n"
        f"[bold]Diff:[/bold]  v{v_from} → v{v_to}",
        title="AXIOM — Diff", border_style="cyan",
    ))

    if not diff_lines:
        app.print("[green]✓ No differences — versions are identical (null diff).[/green]")
        return

    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            app.print(f"[green]{line}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            app.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            app.print(f"[cyan]{line}[/cyan]")
        else:
            app.print(f"[dim]{line}[/dim]")


@cli.command()
def versions(
    file: str = typer.Argument(..., help="Path to the Python file (matches its versions/ slug)"),
):
    """
    List every saved version of a file, with line-change and replacement
    summaries. Combined with `axiom diff`, this is the `diff` half of the
    diff+perf superclass — `axiom verify` plus the perf_report.json saved
    alongside each version covers the `perf stat` half.
    """
    app.print(BANNER)
    from smart_loader.core.diff_reporter import DiffReporter

    reporter = DiffReporter(file)
    index = reporter.get_index()
    if not index:
        app.print(f"[yellow]No saved versions found for {file}. Run 'axiom run {file} --save' first.[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Versions — {file}", header_style="bold cyan")
    table.add_column("Version", justify="right")
    table.add_column("Timestamp")
    table.add_column("Session")
    table.add_column("+/-", justify="right")
    table.add_column("Packages Replaced")
    for rec in index:
        change = f"+{rec.lines_added}/-{rec.lines_removed}"
        table.add_row(
            str(rec.version), rec.timestamp[:19], rec.session_id,
            change, ", ".join(rec.packages_replaced) or "—",
        )
    app.print(table)

    perf_path = reporter.version_dir / "perf_report.json"
    if perf_path.exists():
        import json as _json
        perf = _json.loads(perf_path.read_text())
        app.print(f"\n[dim]Latest perf_report.json — total speedup: ×{perf.get('total_speedup', 1.0):.3f}[/dim]")


@cli.command(name="scan-dir")
def scan_dir(
    directory:        str  = typer.Argument(..., help="Directory containing the multi-file project to analyze"),
    pattern:          str  = typer.Option("*.py", "--pattern", help="Glob pattern for files to include"),
    recursive:        bool = typer.Option(True,   "--recursive/--no-recursive", help="Recurse into subdirectories"),
    llm:              str  = typer.Option("ollama", "--llm",  "-l", help="LLM provider: ollama | claude | nvidia | openai | openai-compat"),
    model:            str  = typer.Option("qwen3-coder-next", "--model", "-m", help="Model name"),
    api_key:          str  = typer.Option("", "--api-key"),
    security:         bool = typer.Option(True, "--security/--no-security", help="Run OSV+PyPI security scan"),
    connector:        bool = typer.Option(True, "--connector/--no-connector", help="Generate API adapter shims"),
    save:             bool = typer.Option(False, "--save", "-s", help="Save optimized files + versioned diff/perf reports"),
    exclude:          str  = typer.Option("", "--exclude", help="Comma-separated glob patterns to exclude (e.g. 'test_*.py,*_optimized.py')"),
    approve:          bool = typer.Option(True,  "--approve/--no-approve", help="Require explicit human confirmation before writing ANY file (default ON — this is the safety gate for multi-file writes)"),
    non_interactive:  bool = typer.Option(False, "--non-interactive", help="With --approve, auto-accept all changes instead of prompting (for CI/scripted runs)"),
):
    """
    Analyze every Python file in a directory/project as a connected whole,
    instead of treating one main.py as the only entry point.

    Real projects spread equally-important logic across multiple files —
    a config module, a handful of service files, a CLI entry point — none
    of which is "the" main file. This command runs the full AXIOM pipeline
    per file, then merges equivalence-group decisions PROJECT-WIDE: if
    'pandas' loses to 'polars' in file_a.py, the same replacement is applied
    in file_b.py too, so the project ends up with one consistent dependency
    choice rather than each file independently re-litigating the same
    decision (and potentially disagreeing with itself).

    HUMAN-IN-THE-LOOP: by default (--approve, the default), nothing is
    written to disk until you've seen the full list of files that would be
    modified and explicitly confirmed. This is the multi-file equivalent of
    `axiom run --feedback` — except here it gates ALL files in the project
    as one batch, since a directory scan modifies many files atomically
    from the user's point of view and a single "yes" should mean "yes to
    everything I'm about to see," not "yes" repeated once per file. Use
    --no-approve to skip this (e.g. you've already reviewed via --no-save
    dry-run output and are scripting a second pass), or --non-interactive
    to auto-accept without a TTY prompt (CI pipelines).
    """
    app.print(BANNER)

    _apply_provider_api_key(llm, api_key)
    if llm == "claude":
        from smart_loader.core.llm_factory import _clean_key
        if not _clean_key(os.environ.get("ANTHROPIC_API_KEY", "")):
            app.print(
                "[red]Error: ANTHROPIC_API_KEY not set in this process's "
                "environment.[/red]\n"
                "[dim]A key visible to `echo $ANTHROPIC_API_KEY` in your terminal "
                "doesn't guarantee axiom sees it — see the README for .env setup, "
                "or pass --api-key directly.[/dim]"
            )
            raise typer.Exit(1)

    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        app.print(f"[red]Error: Not a directory:[/red] {directory}")
        raise typer.Exit(1)

    exclude_patterns = [p.strip() for p in exclude.split(",") if p.strip()]

    glob_fn = dir_path.rglob if recursive else dir_path.glob
    all_files = sorted(glob_fn(pattern))

    def _is_excluded(p: Path) -> bool:
        return any(p.match(pat) for pat in exclude_patterns)

    files = [f for f in all_files if f.is_file() and not _is_excluded(f)]

    if not files:
        app.print(f"[yellow]No files matched '{pattern}' under {directory}[/yellow]")
        raise typer.Exit(1)

    app.print(Panel(
        f"[bold]Project:[/bold]   [cyan]{directory}[/cyan]\n"
        f"[bold]Files:[/bold]     {len(files)} matched\n"
        f"[bold]LLM:[/bold]       [magenta]{llm.upper()}[/magenta] / [yellow]{model}[/yellow]\n"
        f"[bold]Security:[/bold]  {'[green]ON[/green]' if security else '[dim]OFF[/dim]'}\n"
        f"[bold]Connector:[/bold] {'[green]ON[/green]' if connector else '[dim]OFF[/dim]'}\n"
        f"[bold]Approval gate:[/bold] {'[green]ON[/green] (nothing written without confirmation)' if approve else '[red]OFF[/red] (writes immediately on --save)'}",
        title="AXIOM — Multi-File Project Scan", border_style="cyan",
    ))

    from smart_loader.core.graph import run_loader
    from smart_loader.core.state import LoadDecision
    import time as _time

    # ── Pass 1: run the full pipeline independently per file ──────────────────
    # This is sequential and can genuinely take minutes on a real project —
    # each file runs the full pipeline (security scan, resolver LLM call,
    # subprocess benchmarking, axiom LLM call, optional connector LLM calls),
    # with NO parallelism between files. Without visible progress this looks
    # indistinguishable from "hung," which is what led to Ctrl+C-ing a run
    # that was actually still working correctly (734 lines of real output
    # were captured in the log right up to the interrupt). The counter,
    # per-file timer, and total-elapsed line below exist specifically so you
    # can tell "still working, file 4 of 9, ~12s/file" from "stuck."
    #
    # NOTE on approval: each per-file run_loader() call here uses
    # require_approval=False (the default) — individual files are NOT
    # gated mid-pipeline. The approval gate for scan-dir is intentionally a
    # SINGLE project-wide checkpoint placed after Pass 3 (project-wide
    # reconciliation) and before any f.write_text() call, not N separate
    # per-file interrupts. Gating per-file here would mean pausing the
    # whole sequential scan N times for a project with N files, which is
    # worse UX than seeing one consolidated "here's everything, yes/no."
    per_file_state: dict[str, dict] = {}
    total_files = len(files)
    run_start = _time.time()
    interrupted = False

    try:
        for idx, f in enumerate(files, 1):
            rel = f.relative_to(dir_path)
            app.print(f"\n[bold cyan]→ [{idx}/{total_files}] {rel}[/bold cyan]")
            file_start = _time.time()
            try:
                state = run_loader(
                    str(f), llm_provider=llm, model=model,
                    enable_security=security, enable_connector=connector,
                )
                per_file_state[str(f)] = state
                elapsed = _time.time() - file_start
                if state.get("error"):
                    app.print(f"  [yellow]{state['error']}[/yellow]  [dim]({elapsed:.1f}s)[/dim]")
                else:
                    n_decisions = len(state.get("decisions", []))
                    app.print(f"  [dim]done in {elapsed:.1f}s — {n_decisions} decision(s)[/dim]")
            except Exception as e:
                app.print(f"  [red]Pipeline error: {e}[/red]")
                per_file_state[str(f)] = {"error": str(e), "decisions": [], "equivalence_groups": []}
    except KeyboardInterrupt:
        interrupted = True
        app.print(
            f"\n[yellow]⚠ Interrupted after {len(per_file_state)}/{total_files} file(s) "
            f"({_time.time() - run_start:.1f}s elapsed). "
            f"Showing partial results for files completed so far.[/yellow]"
        )

    if not interrupted:
        app.print(f"\n[dim]Pass 1 complete — {total_files} file(s) in {_time.time() - run_start:.1f}s total.[/dim]")

    # ── Pass 2: reconcile decisions project-wide ───────────────────────────────
    # If the same original→winner replacement appears with different winners
    # across files (rare, but possible since each file's resolver call is
    # independent), prefer the decision with the higher-confidence label so
    # the whole project converges on one answer instead of fragmenting.
    _CONF_RANK = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    project_decisions: dict[str, LoadDecision] = {}   # keyed by original package
    for state in per_file_state.values():
        for d in state.get("decisions", []):
            if d.winner == d.original:
                continue
            existing = project_decisions.get(d.original)
            if existing is None or _CONF_RANK.get(d.confidence, 0) > _CONF_RANK.get(existing.confidence, 0):
                project_decisions[d.original] = d

    # ── Pass 3: re-apply the reconciled, project-wide replacement set to every
    #            file that imports an affected package — even files whose own
    #            resolver call didn't independently surface that group (e.g. a
    #            file using only `import pandas` with no second candidate
    #            visible locally still gets the project's pandas→polars call).
    #            Files that don't reference any replaced package at all are
    #            skipped entirely — re-running ast.unparse on them would
    #            normalize quote style / formatting and produce a spurious
    #            "changed" diff for a file AXIOM made no real decision about.
    from smart_loader.core.ast_rewrite import apply_import_replacements
    replacements = {orig: d.winner for orig, d in project_decisions.items()}

    final_outputs: dict[str, str] = {}
    if replacements:
        for f in files:
            state = per_file_state.get(str(f), {})
            source_code = state.get("source_code", "")
            if not source_code:
                continue
            imported_packages = {
                imp.module.split(".")[0] for imp in state.get("imports", [])
            }
            if not imported_packages & replacements.keys():
                continue  # this file doesn't use any package AXIOM is replacing
            try:
                final_outputs[str(f)] = apply_import_replacements(source_code, replacements)
            except Exception as e:
                app.print(f"[yellow]Could not apply project-wide replacements to {f.name}: {e}[/yellow]")
                final_outputs[str(f)] = source_code

    # ── Summary ─────────────────────────────────────────────────────────────
    app.print(Rule("[bold green]── Project-Wide Decisions ──"))
    if project_decisions:
        table = Table(header_style="bold green")
        table.add_column("Original")
        table.add_column("Winner")
        table.add_column("Confidence")
        table.add_column("Role")
        table.add_column("Files Affected", justify="right")
        for orig, d in project_decisions.items():
            affected = sum(
                1 for f in files
                if orig in (per_file_state.get(str(f), {}).get("source_code", "") or "")
            )
            conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(d.confidence, "white")
            table.add_row(
                orig, f"[green]{d.winner}[/green]", f"[{conf_color}]{d.confidence}[/{conf_color}]",
                d.role, str(affected),
            )
        app.print(table)
    else:
        app.print("  [green]✅ No cross-file optimizations found — all imports already optimal project-wide.[/green]")

    # ── Approval gate — per-file diffs then single y/n (default ON) ──────────────
    proceed_with_save = True
    if save and final_outputs and approve:
        from smart_loader.core.feedback_loop import scan_dir_approval_prompt
        files_and_changes = [
            (
                str(f),
                per_file_state.get(str(f), {}).get("source_code", ""),
                final_outputs[str(f)],
            )
            for f in files if str(f) in final_outputs
        ]
        proceed_with_save = scan_dir_approval_prompt(
            files_and_changes=files_and_changes,
            project_decisions=project_decisions,
            non_interactive=non_interactive,
        )

    # ── Save ────────────────────────────────────────────────────────────────
    if save and final_outputs and proceed_with_save:
        from smart_loader.core.diff_reporter import build_report
        for f in files:
            code = final_outputs.get(str(f))
            if code is None:
                continue
            f.write_text(code, encoding="utf-8")
            state = per_file_state.get(str(f), {})
            try:
                build_report(
                    session_id=state.get("_session_id", "unknown"),
                    source_file=str(f),
                    original_code=state.get("source_code", ""),
                    patched_code=code,
                    decisions=[d for d in project_decisions.values()
                               if d.original in (state.get("source_code", "") or "")],
                    benchmarks=state.get("benchmarks", {}),
                )
            except Exception as e:
                app.print(f"[yellow]Versioned report failed for {f.name}: {e}[/yellow]")
        app.print(f"\n[bold green]✓ Saved {len(final_outputs)} file(s) with project-wide replacements applied.[/bold green]")
        app.print("[dim]Use 'axiom versions <file>' / 'axiom diff <file>' to inspect any individual file's history.[/dim]")
    elif save and final_outputs and not proceed_with_save:
        app.print(
            f"\n[dim]{len(final_outputs)} file(s) had proposed changes but were NOT written "
            f"(approval declined). Re-run with --no-approve to skip this gate, "
            f"or re-run and answer 'y' to write them.[/dim]"
        )

    # ── Unified run summary — lines changed per file + overall, tokens, cost,
    # elapsed time. Printed LAST, after the single project-wide approval gate
    # (if any) has resolved, mirroring `axiom run`'s end-of-run summary. Uses
    # what was actually decided (final_outputs), not per-file proposals that
    # may have been declined or never affected by a project-wide winner. ─────
    from smart_loader.core.run_report import print_run_summary, line_diff
    total_elapsed = _time.time() - run_start
    diffs = {}
    if save and proceed_with_save:
        for f in files:
            code = final_outputs.get(str(f))
            if code is None:
                continue
            original = per_file_state.get(str(f), {}).get("source_code", "")
            d = line_diff(original, code)
            if d.changed:
                diffs[str(f.relative_to(dir_path))] = d
    else:
        # Dry run (--no-save) or declined approval — still report the SIZE of
        # what was proposed, so `scan-dir --no-save` is useful for previewing
        # the diff/cost/time without writing anything.
        for f, code in final_outputs.items():
            original = per_file_state.get(f, {}).get("source_code", "")
            d = line_diff(original, code)
            if d.changed:
                diffs[str(Path(f).relative_to(dir_path))] = d

    print_run_summary(
        elapsed_seconds=total_elapsed,
        provider=llm,
        model=model,
        line_diffs=diffs,
    )


@cli.command(name="eval-pymigbench")
def eval_pymigbench(
    dataset_dir:   str  = typer.Argument(..., help="PyMigBench checkout dir (auto-cloned here if missing)"),
    repo_cache:    str  = typer.Option("./pymigbench_repos", "--repo-cache", help="Cache dir for cloned client repos"),
    llm:           str  = typer.Option("ollama", "--llm", "-l"),
    model:         str  = typer.Option("qwen3-coder-next", "--model", "-m"),
    api_key:       str  = typer.Option("", "--api-key"),
    max_cases:     int  = typer.Option(None, "--max-cases", help="Cap the number of migration cases evaluated"),
    case_offset:   int  = typer.Option(0, "--case-offset", help="Skip this many eligible cases before applying --max-cases (for non-overlapping batches)"),
    case_filter:   str  = typer.Option("", "--case-filter", help="Run only cases whose migration ID, repo, source/target, or file path contains this text"),
    full_pipeline: bool = typer.Option(False, "--full-pipeline", help="Run resolver through verifier; PyMigBench automatically uses disposable historical environments"),
    seed_target:   bool = typer.Option(False, "--seed-target", help="With --full-pipeline: bypass resolver's own discovery, seed the known ground-truth pair directly so profiler/axiom/connector/verifier all run"),
    require_approval: bool = typer.Option(False, "--require-approval", help="Exercise the approval_gate/interrupt() gated code path (auto-resumed with accept_all). Only meaningful with --full-pipeline."),
    compare_to_developer: bool = typer.Option(False, "--compare-to-developer", help="Replicate the PyMigBench-paper RQ1 methodology: migrate File_pre with AXIOM, compare against the developer's own File_dev at the code-change level. Overrides --full-pipeline/--seed-target."),
    auto_install: bool = typer.Option(False, "--auto-install", help="Legacy host-environment install for seeded runs; unnecessary (and ignored) with --isolated-profiling."),
    enable_security: bool = typer.Option(True, "--enable-security/--no-security", help="Resolve and scan exact compatible target releases during full-pipeline evaluation (adds network calls and time per case)."),
    isolated_profiling: bool = typer.Option(False, "--isolated-profiling", help="Explicitly request disposable environments (automatic with --full-pipeline)."),
    out:           str  = typer.Option("./pymigbench_eval_report", "--out", help="Output path prefix for .json/.csv report"),
):
    """
    Evaluate AXIOM's resolver against PyMigBench (Islam et al., MSR 2023 /
    FSE 2024) — the ground-truth benchmark of real Python library migrations.
    Reports what fraction of real, human-confirmed migrations AXIOM's
    resolver_agent independently detects as an equivalence group, broken
    down by domain, plus (with --full-pipeline) whether AXIOM's actual
    recommendation matches the human's chosen target. See
    smart_loader/eval/pymigbench_eval.py for the full methodology notes.
    """
    app.print(BANNER)
    _apply_provider_api_key(llm, api_key)
    if llm == "claude":
        from smart_loader.core.llm_factory import _clean_key
        if not _clean_key(os.environ.get("ANTHROPIC_API_KEY", "")):
            app.print(
                "[red]Error: ANTHROPIC_API_KEY not set in this process's "
                "environment.[/red]\n"
                "[dim]A key visible to `echo $ANTHROPIC_API_KEY` in your terminal "
                "doesn't guarantee axiom sees it — see the README for .env setup, "
                "or pass --api-key directly.[/dim]"
            )
            raise typer.Exit(1)

    import sys as _sys
    from smart_loader.eval.pymigbench_eval import main as _eval_main
    argv = ["run", dataset_dir, "--repo-cache", repo_cache, "--llm", llm, "--model", model, "--out", out]
    if max_cases:
        argv += ["--max-cases", str(max_cases)]
    if case_offset:
        argv += ["--case-offset", str(case_offset)]
    if case_filter:
        argv += ["--case-filter", case_filter]
    if full_pipeline:
        argv += ["--full-pipeline"]
    if seed_target:
        argv += ["--seed-target"]
    if require_approval:
        argv += ["--require-approval"]
    if compare_to_developer:
        argv += ["--compare-to-developer"]
    if auto_install:
        argv += ["--auto-install"]
    if enable_security:
        argv += ["--enable-security"]
    if isolated_profiling:
        argv += ["--isolated-profiling"]
    old_argv = _sys.argv
    try:
        _sys.argv = ["axiom-eval"] + argv
        _eval_main()
    finally:
        _sys.argv = old_argv


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
  │  security_agent (optional)                                  │
  │  → historical source CVEs, release freshness, yanks         │
  │  → no host is_installed gate                                │
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
  │  candidate_security_agent (when security is enabled)        │
  │  → selects an exact non-yanked target compatible with Python │
  │  → scans that same release; unresolved targets are blocked   │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  profiler_agent                                              │
  │  → disposable historical Python environment                 │
  │  → real import/memory + fixed-fixture runtime probes only    │
  │  → No LLM (deterministic)                                   │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  axiom_agent          [INSPIRED BY CodeAugur]               │
  │  → LLM call #2: API compat scoring                          │
  │  → scores only measured/available evidence                  │
  │  → records explicit selection/decline reasons               │
  │  → Confidence: HIGH/MEDIUM/LOW                              │
  │  → AST rewrite is deferred until verification                │
  │  → Token usage recorded                                     │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  connector_agent      ★ NEW                                  │
  │  → LLM call #3: generates adapter shim per replacement      │
  │  → Input/output transformer: old API → new package          │
  │  → LLM call #4: rewrites source imports to use shims        │
  │  → retried with concrete verifier evidence when repairable  │
  └──────────────────────┬──────────────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────────────┐
  │  verifier_agent                                              │
  │  → baseline vs patched static/smoke/project-test evidence   │
  │  → returns to AXIOM or Connector, globally capped at 5      │
  │  → accepts only verified patches; otherwise rejects/records │
  └─────────────────────────────────────────────────────────────┘
""",
        title="AXIOM — Graph v2", border_style="cyan",
    ))


def main():
    cli()

if __name__ == "__main__":
    main()
