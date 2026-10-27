"""
core/feedback_loop.py
──────────────────────
Single source of truth for all human-in-the-loop logic in AXIOM.

This module owns two related but distinct review flows:

  1. run_feedback_loop()        — post-pipeline, single-file review.
     Called by `cli.py`'s `axiom run --feedback` after graph.invoke()
     returns a finished result. Shows a table of proposed changes, a
     unified diff of the actual source, and an accept/reject prompt.
     Then runs the output-equivalence check (diff original vs patched).

  2. approval_gate_node()       — pre-write, LangGraph interrupt() node.
     Registered as the "approval_gate" node in core/graph.py when
     require_approval=True. Suspends graph execution BEFORE any AST
     rewrite via interrupt(), surfaces decisions + source diff to the
     caller, and resumes with the human's accept/reject choice applied.
     This is the correct implementation of what the professor described:
     feedback as an actual graph node sitting between axiom_agent
     (scoring) and the write step, using MemorySaver checkpointing so
     the graph can genuinely pause and resume rather than running to
     completion and reviewing after the fact.

     Used by:
       - `axiom run --feedback` (via graph.py when require_approval=True)
       - `axiom scan-dir --approve` shows per-file diffs before the
         project-wide y/n confirmation (via scan_dir_approval_prompt())

  3. scan_dir_approval_prompt() — project-wide pre-write diff review.
     Called by scan-dir's approval gate in cli.py. Shows a per-file
     unified diff for every file that would be modified, then asks one
     y/n question for the whole batch. This is the correct level of
     granularity for a multi-file scan: one consolidated review of all
     proposed changes rather than N separate per-file interrupts.

The professor's diff/perf superclass analogy maps cleanly here:
  - unified diff (this module) = the `diff` side
  - perf_report.json (diff_reporter.py) = the `perf stat` side
  - AxiomReport = the superclass combining both
"""

from __future__ import annotations
import ast
import dataclasses
import difflib
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.rule import Rule

console = Console()


# ── Output equivalence check ────────────────────────────────────────────────────

def check_output_equivalence(
    original_code: str,
    patched_code:  str,
    source_file:   str = "",
    timeout:       int = 10,
) -> dict:
    """
    Run both original and patched code in isolated subprocesses.
    Compare stdout+stderr output.

    Returns a dict: equivalent, original_out, patched_out, diff_lines, error.
    Mirrors `diff <(python original.py) <(python patched.py)`.
    If equivalent → diff_lines is empty (null diff).
    """
    results = {
        "equivalent": False, "original_out": "", "patched_out": "",
        "diff_lines": [], "error": None, "skipped": False,
    }
    if "__main__" not in original_code and "if __name__" not in original_code:
        results["skipped"] = True
        results["skip_reason"] = "No __main__ block — cannot run equivalence check automatically"
        return results

    def _run(code: str) -> tuple[str, str, int]:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, dir="/tmp"
        ) as f:
            f.write(code)
            fname = f.name
        try:
            proc = subprocess.run(
                [sys.executable, fname],
                capture_output=True, text=True, timeout=timeout,
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", "TIMEOUT", -1
        except Exception as e:
            return "", str(e), -1
        finally:
            Path(fname).unlink(missing_ok=True)

    try:
        orig_out,  orig_err,  orig_rc  = _run(original_code)
        patch_out, patch_err, patch_rc = _run(patched_code)
        combined_orig  = (orig_out  + orig_err).strip()
        combined_patch = (patch_out + patch_err).strip()
        diff = list(difflib.unified_diff(
            combined_orig.splitlines(keepends=True),
            combined_patch.splitlines(keepends=True),
            fromfile="original output", tofile="patched output", lineterm="",
        ))
        results["equivalent"]   = len(diff) == 0
        results["original_out"] = combined_orig[:500]
        results["patched_out"]  = combined_patch[:500]
        results["diff_lines"]   = diff
        results["original_rc"]  = orig_rc
        results["patched_rc"]   = patch_rc
    except Exception as e:
        results["error"] = str(e)
    return results


# ── Shared UI helpers ────────────────────────────────────────────────────────────

def _print_source_diff(original: str, patched: str,
                       from_label: str = "original",
                       to_label:   str = "patched",
                       max_lines:  int = 60) -> None:
    """Print a coloured unified diff between two source strings."""
    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=from_label, tofile=to_label,
        lineterm="", n=3,
    ))
    if not diff_lines:
        console.print("[dim](no diff — files are identical)[/dim]")
        return
    for line in diff_lines[:max_lines]:
        if line.startswith("+") and not line.startswith("+++"):
            console.print(f"  [green]{line.rstrip()}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            console.print(f"  [red]{line.rstrip()}[/red]")
        elif line.startswith("@@"):
            console.print(f"  [cyan]{line.rstrip()}[/cyan]")
        else:
            console.print(f"  [dim]{line.rstrip()}[/dim]")
    if len(diff_lines) > max_lines:
        console.print(f"  [dim]… {len(diff_lines) - max_lines} more lines not shown[/dim]")


def _print_decisions_table(actual_changes: list) -> None:
    """Print the standard proposed-changes summary table."""
    table = Table(title="Proposed Changes", header_style="bold cyan", show_lines=True)
    table.add_column("#",          width=3, justify="right")
    table.add_column("Role")
    table.add_column("Replace")
    table.add_column("With")
    table.add_column("Conf.")
    table.add_column("Rationale")
    for i, d in enumerate(actual_changes, 1):
        cc = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(d.confidence, "white")
        reason = d.rationale[:65] + "…" if len(d.rationale) > 65 else d.rationale
        table.add_row(
            str(i), d.role,
            f"[red]{d.original}[/red]",
            f"[green]{d.winner}[/green]",
            f"[{cc}]{d.confidence}[/{cc}]",
            reason,
        )
    console.print(table)


def _revert_decision(patched_code: str, original: str, winner: str) -> str:
    """Revert one specific replacement in already-patched source."""
    try:
        tree   = ast.parse(patched_code)
        lines  = patched_code.splitlines()
        rewrites: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == winner:
                        rewrites[node.lineno - 1] = lines[node.lineno - 1].replace(winner, original)
            elif isinstance(node, ast.ImportFrom):
                if node.module == winner:
                    rewrites[node.lineno - 1] = lines[node.lineno - 1].replace(winner, original)
        for idx, new_line in rewrites.items():
            lines[idx] = new_line
        return "\n".join(lines)
    except Exception:
        return patched_code.replace(f"import {winner}", f"import {original}")


def _run_equivalence_display(original_code: str, patched_code: str,
                              source_file: str) -> None:
    """Run and display the output equivalence check."""
    console.print()
    console.rule("[bold]── Output Equivalence Check ──")
    console.print("[dim]Running original and patched code to compare outputs…[/dim]")
    result = check_output_equivalence(original_code, patched_code, source_file)
    if result.get("skipped"):
        console.print(f"[dim]Skipped: {result['skip_reason']}[/dim]")
        return
    if result.get("error"):
        console.print(f"[yellow]Equivalence check error: {result['error']}[/yellow]")
        return
    if result["equivalent"]:
        console.print(Panel(
            "[bold green]✓ EQUIVALENT[/bold green]\n"
            "Both original and patched produce identical output.\n"
            "[dim]diff original patched → (empty — no differences)[/dim]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            "[bold yellow]⚠ OUTPUT DIFFERS[/bold yellow]\n"
            "Original and patched produce different output.\n"
            "Review the diff below before saving.",
            border_style="yellow",
        ))
        for line in result["diff_lines"][:30]:
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"  [green]{line.rstrip()}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"  [red]{line.rstrip()}[/red]")


# ── 1. run_feedback_loop — single-file, post-pipeline, CLI prompt ─────────────────

def run_feedback_loop(
    original_code:   str,
    patched_code:    str,
    decisions:       list,
    source_file:     str  = "",
    non_interactive: bool = False,
) -> tuple[str, list]:
    """
    Post-pipeline single-file review (used by `axiom run --feedback`).
    Shows table + source diff + interactive accept/reject prompt.
    Returns (final_code, accepted_decisions).
    """
    console.print()
    console.rule("[bold cyan]── AXIOM Feedback Loop ──")

    actual_changes = [d for d in decisions if d.winner != d.original]

    if not actual_changes:
        console.print("[green]No replacements were made — nothing to review.[/green]")
        _run_equivalence_display(original_code, patched_code, source_file)
        return patched_code, decisions

    _print_decisions_table(actual_changes)

    console.print()
    console.print(f"[bold]Source diff:[/bold]  [dim]{source_file or 'in-memory'}[/dim]")
    _print_source_diff(original_code, patched_code)

    if non_interactive:
        console.print("[dim]Non-interactive mode — accepting all changes.[/dim]")
        _run_equivalence_display(original_code, patched_code, source_file)
        return patched_code, decisions

    console.print()
    console.print("[bold]Options:[/bold]")
    console.print("  [green]a[/green]       Accept all changes")
    console.print("  [red]r[/red]       Reject all changes (keep original)")
    console.print("  [yellow]1,2,3[/yellow]   Reject specific changes by number, accept the rest")
    console.print()

    while True:
        choice = Prompt.ask("[cyan]Your choice[/cyan]", default="a").strip().lower()

        if choice == "a":
            final_code, accepted = patched_code, decisions
            console.print("[green]✓ All changes accepted.[/green]")
            break

        elif choice == "r":
            final_code, accepted = original_code, []
            console.print("[yellow]✗ All changes rejected — keeping original.[/yellow]")
            break

        else:
            try:
                reject_nums = {int(x.strip()) for x in choice.split(",") if x.strip().isdigit()}
            except ValueError:
                console.print("[red]Invalid input. Enter 'a', 'r', or numbers like '1,3'[/red]")
                continue

            final_code = patched_code
            accepted: list = []
            for i, d in enumerate(actual_changes, 1):
                if i in reject_nums:
                    console.print(f"  [yellow]Reverting #{i}: {d.original} ← {d.winner}[/yellow]")
                    final_code = _revert_decision(final_code, d.original, d.winner)
                else:
                    accepted.append(d)
            accepted += [d for d in decisions if d.winner == d.original]
            n_accepted = sum(1 for d in accepted if d.winner != d.original)
            console.print(f"[green]✓ {n_accepted} change(s) accepted, {len(reject_nums)} rejected.[/green]")
            break

    _run_equivalence_display(original_code, final_code, source_file)
    return final_code, accepted


# ── 2. approval_gate_node — LangGraph interrupt() node ───────────────────────────

def approval_gate_node(state: dict) -> dict:
    """
    LangGraph node: pause BEFORE any AST rewrite and ask the human to
    accept/reject. Registered as the "approval_gate" node in core/graph.py
    when require_approval=True.

    Uses langgraph.types.interrupt() to genuinely suspend graph execution —
    this is not a UI illusion. The graph stops here; graph.invoke() returns
    an __interrupt__ key to the caller (cli.py / scan-dir). The caller
    shows the diff and prompt (for run) or batches this with other files
    (for scan-dir), then calls resume_loader() with Command(resume=<choice>)
    to continue from exactly this point with the MemorySaver checkpoint.

    Expected resume value (passed via Command(resume=...)):
        {"action": "accept_all"}
        {"action": "reject_all"}
        {"action": "reject_some", "reject_roles": ["role1", "role2"]}
    """
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage
    from smart_loader.core.state import AgentEvent
    from smart_loader.core.ast_rewrite import apply_import_replacements

    decisions    = state.get("decisions", [])
    drop_in      = state.get("_drop_in_replacements", {})
    source_code  = state.get("source_code", "")
    source_file  = state.get("source_file", "")
    trace        = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="approval_gate", event="interrupted"))

    real_decisions = [d for d in decisions if d.winner != d.original]

    # Pre-compute what the patched source WOULD look like so the diff can
    # be surfaced to whoever handles the interrupt (cli.py for `axiom run`,
    # scan_dir_approval_prompt for `axiom scan-dir`).
    preview_patched = source_code
    if drop_in:
        try:
            preview_patched = apply_import_replacements(source_code, drop_in)
        except Exception:
            pass

    payload = {
        "source_file":     source_file,
        "source_code":     source_code,
        "preview_patched": preview_patched,
        "decisions": [
            {
                "role":       d.role,
                "original":   d.original,
                "winner":     d.winner,
                "confidence": d.confidence,
                "rationale":  d.rationale,
                "drop_in":    d.original in drop_in,
            }
            for d in real_decisions
        ],
    }

    # ── Genuine suspend ── Execution stops here until resume_loader() is called.
    response = interrupt(payload)

    # ── Resume: apply the human's choice ─────────────────────────────────────
    action       = "accept_all"
    reject_roles: set[str] = set()
    if isinstance(response, dict):
        action       = response.get("action", "accept_all")
        reject_roles = set(response.get("reject_roles", []))

    if action == "reject_all":
        trace.append(AgentEvent(stage="approval_gate", event="rejected_all"))
        return {
            "decisions":    [dataclasses.replace(d, winner=d.original) for d in decisions],
            "patched_code": source_code,
            "agent_trace":  trace,
            "done":         True,
            "messages":     [AIMessage(content="Approval gate: all changes rejected.")],
        }

    accepted_decisions: list = []
    accepted_drop_in:   dict[str, str] = {}
    for d in decisions:
        if d.winner == d.original or d.role not in reject_roles:
            accepted_decisions.append(d)
            if d.winner != d.original and d.original in drop_in:
                accepted_drop_in[d.original] = d.winner
        else:
            accepted_decisions.append(dataclasses.replace(d, winner=d.original))

    patched_code = source_code
    if accepted_drop_in:
        try:
            patched_code = apply_import_replacements(source_code, accepted_drop_in)
        except Exception:
            patched_code = source_code

    trace.append(AgentEvent(
        stage="approval_gate", event="resumed",
        detail={"action": action, "rejected": sorted(reject_roles)},
    ))

    return {
        "decisions":    accepted_decisions,
        "patched_code": patched_code,
        "agent_trace":  trace,
        "messages":     [AIMessage(
            content=f"Approval gate: resumed with action={action}"
                    + (f", rejected={sorted(reject_roles)}" if reject_roles else "")
        )],
    }


# ── 3. scan_dir_approval_prompt — per-file diffs then single y/n ─────────────────

def scan_dir_approval_prompt(
    files_and_changes: list[tuple[str, str, str]],  # (path, original_code, patched_code)
    project_decisions: dict,                         # {original_pkg: LoadDecision}
    non_interactive:   bool = False,
) -> bool:
    """
    Project-wide approval prompt for `axiom scan-dir --save`.

    Shows:
      1. The project-wide decisions table (what packages were replaced)
      2. A per-file unified diff for EVERY file that would be modified
         (so you can see exactly what AXIOM is about to write, not just
         a list of filenames)
      3. A single y/n question for the whole batch

    Returns True (proceed with writes) or False (abort all writes).

    Design note: this is the "diff before you say yes" feature you asked
    for — showing the full diff of every affected file so the approval
    prompt is genuinely informative rather than just a list of filenames.
    """
    if not files_and_changes:
        return True

    console.print()
    console.rule("[bold yellow]── Approval Required Before Writing ──")

    # ── Project-wide decisions table ──────────────────────────────────────────
    if project_decisions:
        from rich.table import Table as RTable
        dtable = RTable(title="Project-Wide Decisions", header_style="bold green")
        dtable.add_column("Original")
        dtable.add_column("Winner")
        dtable.add_column("Confidence")
        dtable.add_column("Role")
        for orig, d in project_decisions.items():
            cc = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(d.confidence, "white")
            dtable.add_row(
                orig,
                f"[green]{d.winner}[/green]",
                f"[{cc}]{d.confidence}[/{cc}]",
                d.role,
            )
        console.print(dtable)
        console.print()

    # ── Per-file diffs ─────────────────────────────────────────────────────────
    console.print(f"[bold]{len(files_and_changes)} file(s) would be modified:[/bold]")
    console.print()

    for file_path, original_code, patched_code in files_and_changes:
        rel = file_path
        try:
            rel = str(Path(file_path).name)
        except Exception:
            pass

        # Count changed lines so the header is immediately informative
        diff_lines = list(difflib.unified_diff(
            original_code.splitlines(keepends=True),
            patched_code.splitlines(keepends=True),
            lineterm="",
        ))
        added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

        console.print(
            f"[bold cyan]✎ {file_path}[/bold cyan]  "
            f"[dim](+{added}/-{removed} lines)[/dim]"
        )
        _print_source_diff(original_code, patched_code,
                           from_label=f"{rel} (original)",
                           to_label=f"{rel} (patched)",
                           max_lines=50)
        console.print()

    # ── Single y/n confirmation ────────────────────────────────────────────────
    if non_interactive:
        console.print("[dim]--non-interactive: auto-accepting all changes.[/dim]")
        return True

    choice = Prompt.ask(
        f"[bold cyan]Write all {len(files_and_changes)} file(s)?[/bold cyan]",
        choices=["y", "n"], default="n",
    )
    if choice == "y":
        console.print("[green]✓ Approved — writing changes.[/green]")
        return True
    else:
        console.print("[yellow]✗ Declined — no files were written.[/yellow]")
        return False



# ── Output equivalence check ───────────────────────────────────────────────────

def check_output_equivalence(
    original_code: str,
    patched_code:  str,
    source_file:   str = "",
    timeout:       int = 10,
) -> dict:
    """
    Run both original and patched code in isolated subprocesses.
    Compare stdout+stderr output.
    Returns a dict with keys: equivalent, original_out, patched_out, diff_lines, error
    
    Mirrors: diff <(python original.py) <(python patched.py)
    If equivalent → diff produces null (empty diff_lines).
    """
    results = {"equivalent": False, "original_out": "", "patched_out": "", 
               "diff_lines": [], "error": None, "skipped": False}

    # Only run if the file has a __main__ block or is a script
    if "__main__" not in original_code and "if __name__" not in original_code:
        results["skipped"] = True
        results["skip_reason"] = "No __main__ block — cannot run equivalence check automatically"
        return results

    def _run(code: str) -> tuple[str, str, int]:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, dir="/tmp"
        ) as f:
            f.write(code)
            fname = f.name
        try:
            proc = subprocess.run(
                [sys.executable, fname],
                capture_output=True, text=True, timeout=timeout,
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", "TIMEOUT", -1
        except Exception as e:
            return "", str(e), -1
        finally:
            Path(fname).unlink(missing_ok=True)

    try:
        orig_out, orig_err, orig_rc = _run(original_code)
        patch_out, patch_err, patch_rc = _run(patched_code)

        combined_orig  = (orig_out  + orig_err).strip()
        combined_patch = (patch_out + patch_err).strip()

        import difflib
        diff = list(difflib.unified_diff(
            combined_orig.splitlines(keepends=True),
            combined_patch.splitlines(keepends=True),
            fromfile="original output",
            tofile="patched output",
            lineterm="",
        ))

        results["equivalent"]    = len(diff) == 0
        results["original_out"]  = combined_orig[:500]
        results["patched_out"]   = combined_patch[:500]
        results["diff_lines"]    = diff
        results["original_rc"]   = orig_rc
        results["patched_rc"]    = patch_rc

    except Exception as e:
        results["error"] = str(e)

    return results


# ── Revert helper ──────────────────────────────────────────────────────────────

def _revert_decision(patched_code: str, original: str, winner: str) -> str:
    """
    Revert a specific replacement in the patched code.
    Swaps winner back to original in import statements.
    """
    try:
        tree     = ast.parse(patched_code)
        lines    = patched_code.splitlines()
        rewrites = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == winner:
                        rewrites[node.lineno - 1] = lines[node.lineno - 1].replace(winner, original)
            elif isinstance(node, ast.ImportFrom):
                if node.module == winner:
                    rewrites[node.lineno - 1] = lines[node.lineno - 1].replace(winner, original)

        for idx, new_line in rewrites.items():
            lines[idx] = new_line

        return "\n".join(lines)
    except Exception:
        # Simple string replacement fallback
        return patched_code.replace(f"import {winner}", f"import {original}")


# ── Feedback loop ──────────────────────────────────────────────────────────────

def run_feedback_loop(
    original_code: str,
    patched_code:  str,
    decisions:     list,           # list[LoadDecision]
    source_file:   str = "",
    non_interactive: bool = False, # for CI / automated runs
) -> tuple[str, list]:             # (final_code, accepted_decisions)
    """
    Show a summary of AXIOM's changes and ask the human to accept/reject.
    Then run output equivalence check.
    Returns (final patched code, list of accepted decisions).
    """
    console.print()
    console.rule("[bold cyan]── AXIOM Feedback Loop ──")

    actual_changes = [d for d in decisions if d.winner != d.original]

    if not actual_changes:
        console.print("[green]No replacements were made — nothing to review.[/green]")
        _run_equivalence_display(original_code, patched_code, source_file)
        return patched_code, decisions

    # ── Summary table ──────────────────────────────────────────────────────────
    console.print()
    table = Table(title="Proposed Changes", header_style="bold cyan", show_lines=True)
    table.add_column("#", width=3, justify="right")
    table.add_column("Role")
    table.add_column("Replace")
    table.add_column("With")
    table.add_column("Confidence")
    table.add_column("Key Reason")

    for i, d in enumerate(actual_changes, 1):
        conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(d.confidence, "white")
        reason = d.rationale[:70] + "..." if len(d.rationale) > 70 else d.rationale
        table.add_row(
            str(i),
            d.role,
            f"[red]{d.original}[/red]",
            f"[green]{d.winner}[/green]",
            f"[{conf_color}]{d.confidence}[/{conf_color}]",
            reason,
        )

    console.print(table)

    # ── Diff preview ───────────────────────────────────────────────────────────
    import difflib
    diff_lines = list(difflib.unified_diff(
        original_code.splitlines(keepends=True),
        patched_code.splitlines(keepends=True),
        fromfile="original",
        tofile="patched",
        lineterm="", n=2,
    ))
    if diff_lines:
        console.print()
        console.print("[bold]Import diff:[/bold]")
        for line in diff_lines[:40]:
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"  [green]{line.rstrip()}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"  [red]{line.rstrip()}[/red]")
            elif line.startswith("@@"):
                console.print(f"  [dim]{line.rstrip()}[/dim]")

    if non_interactive:
        console.print("[dim]Non-interactive mode — accepting all changes.[/dim]")
        _run_equivalence_display(original_code, patched_code, source_file)
        return patched_code, decisions

    # ── Interactive prompt ─────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Options:[/bold]")
    console.print("  [green]a[/green]  Accept all changes")
    console.print("  [red]r[/red]  Reject all changes (keep original)")
    console.print("  [yellow]1,2,3[/yellow]  Reject specific changes by number (comma-separated)")
    console.print()

    while True:
        choice = Prompt.ask(
            "[cyan]Your choice[/cyan]",
            default="a",
        ).strip().lower()

        if choice == "a":
            accepted   = decisions
            final_code = patched_code
            console.print("[green]✓ All changes accepted.[/green]")
            break

        elif choice == "r":
            accepted   = []
            final_code = original_code
            console.print("[yellow]✗ All changes rejected — keeping original.[/yellow]")
            break

        else:
            # Parse comma-separated rejection list
            try:
                reject_nums = {int(x.strip()) for x in choice.split(",") if x.strip().isdigit()}
            except ValueError:
                console.print("[red]Invalid input. Enter 'a', 'r', or numbers like '1,3'[/red]")
                continue

            final_code = patched_code
            accepted   = []
            for i, d in enumerate(actual_changes, 1):
                if i in reject_nums:
                    console.print(f"  [yellow]Reverting #{i}: {d.original} ← {d.winner}[/yellow]")
                    final_code = _revert_decision(final_code, d.original, d.winner)
                else:
                    accepted.append(d)
            # Also include unchanged decisions
            accepted += [d for d in decisions if d.winner == d.original]
            console.print(f"[green]✓ {sum(1 for d in accepted if d.winner != d.original)} change(s) accepted, {len(reject_nums)} rejected.[/green]")
            break

    # ── Output equivalence check ───────────────────────────────────────────────
    _run_equivalence_display(original_code, final_code, source_file)

    return final_code, accepted


def _run_equivalence_display(original_code: str, patched_code: str, source_file: str):
    """Run and display the output equivalence check."""
    console.print()
    console.rule("[bold]── Output Equivalence Check ──")
    console.print("[dim]Running original and patched code to compare outputs...[/dim]")

    result = check_output_equivalence(original_code, patched_code, source_file)

    if result.get("skipped"):
        console.print(f"[dim]Skipped: {result['skip_reason']}[/dim]")
        return

    if result.get("error"):
        console.print(f"[yellow]Equivalence check error: {result['error']}[/yellow]")
        return

    if result["equivalent"]:
        console.print(
            Panel(
                "[bold green]✓ EQUIVALENT[/bold green]\n"
                "Both original and patched produce identical output.\n"
                "[dim]diff original patched → (empty — no differences)[/dim]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold yellow]⚠ OUTPUT DIFFERS[/bold yellow]\n"
                "Original and patched produce different output.\n"
                "Review the diff below before saving.",
                border_style="yellow",
            )
        )
        if result["diff_lines"]:
            console.print("[bold]Output diff:[/bold]")
            for line in result["diff_lines"][:30]:
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"  [green]{line.rstrip()}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"  [red]{line.rstrip()}[/red]")
        if result.get("original_out"):
            console.print(f"\n[dim]Original output: {result['original_out'][:200]}[/dim]")
        if result.get("patched_out"):
            console.print(f"[dim]Patched  output: {result['patched_out'][:200]}[/dim]")