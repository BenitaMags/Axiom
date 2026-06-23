"""
core/feedback_loop.py
──────────────────────
Human-in-the-loop feedback after AXIOM patches a file.

Flow:
  1. AXIOM completes and produces patched_code + decisions
  2. feedback_loop() shows a summary and asks the user to:
       a) Accept all changes
       b) Reject specific replacements (by number)
       c) Reject all
  3. Accepted decisions stay in patched_code; rejected ones revert
  4. The final accepted patched_code is returned
  5. Output equivalence check runs automatically (diff original vs patched)

Output equivalence check:
  - Runs the original and patched files via subprocess
  - Captures stdout/stderr
  - If both produce identical output → "outputs are equivalent"
  - If different → shows the diff so the user can decide

This mirrors the professor's suggestion: diff gives null on two identical programs.
"""

from __future__ import annotations
import ast
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.rule import Rule

console = Console()


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