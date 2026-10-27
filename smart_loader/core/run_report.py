"""
core/run_report.py
────────────────────
Lines-changed + cost/token/time summary for an `axiom run`, printed once at
the very end — after the human-in-the-loop feedback step (if any) has
resolved — so the numbers reflect what actually got written to disk, not
just what the pipeline proposed before a human could reject some of it.

Three things this module answers, matching the professor's diff/perf
superclass framing (this is the "cost" sibling of diff_reporter.py's
diff+perf report):

  1. Lines changed — per file and overall (added/removed via unified diff).
  2. Token usage + estimated dollar cost — per agent and total, using
     token_tracker's per-process accumulator plus a small pricing table.
  3. Wall-clock elapsed time for the whole `axiom run` invocation.

Pricing is necessarily an approximation — provider price lists change more
often than this file will be updated. Local providers (Ollama) are free by
construction; cost is $0 and only latency/tokens are meaningful for them.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ── Pricing table (USD per 1M tokens) ────────────────────────────────────────────
# Anthropic pricing per https://www.anthropic.com/pricing as of this file's last
# update — re-check before quoting these numbers in anything published, prices
# change. Unknown/local models default to $0 (Ollama, vLLM, etc. are free to run
# here even though the underlying hardware obviously isn't free).
PRICING_USD_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # model_substring: (input_price, output_price)
    "claude-opus-4":    (15.00, 75.00),
    "claude-sonnet-4":  (3.00, 15.00),
    "claude-haiku-4":   (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku":  (0.80, 4.00),
    "claude-3-opus":    (15.00, 75.00),
    "gpt-4o":           (2.50, 10.00),
    "gpt-4o-mini":      (0.15, 0.60),
    "gpt-4-turbo":      (10.00, 30.00),
}


def _price_for_model(model: str) -> tuple[float, float] | None:
    model_l = model.lower()
    for key, price in PRICING_USD_PER_1M_TOKENS.items():
        if key in model_l:
            return price
    return None


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns 0.0 for local/unrecognized providers or models — cost is only
    meaningful for metered cloud APIs we have a price for."""
    if provider.lower() in ("ollama", "vllm"):
        return 0.0
    price = _price_for_model(model)
    if not price:
        return 0.0
    in_price, out_price = price
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


# ── Lines changed ────────────────────────────────────────────────────────────────

@dataclass
class LineDiffStat:
    added:   int = 0
    removed: int = 0

    @property
    def changed(self) -> int:
        return self.added + self.removed


def line_diff(original: str, patched: str) -> LineDiffStat:
    """Count +/- lines via unified diff (ignoring the +++/--- header lines)."""
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        lineterm="",
    ))
    added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return LineDiffStat(added=added, removed=removed)


# ── Run timer ─────────────────────────────────────────────────────────────────

@dataclass
class RunTimer:
    """`with RunTimer() as t: ...` then read t.elapsed_seconds."""
    started_at:  float = field(default_factory=time.time)
    finished_at: float = 0.0

    def __enter__(self) -> "RunTimer":
        self.started_at = time.time()
        return self

    def __exit__(self, *exc):
        self.finished_at = time.time()

    def stop(self) -> float:
        self.finished_at = time.time()
        return self.elapsed_seconds

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.1f}s"


# ── The printed summary ────────────────────────────────────────────────────────

def print_run_summary(
    *,
    elapsed_seconds:  float,
    provider:         str,
    model:            str,
    line_diffs:       dict[str, LineDiffStat],   # {file_path: LineDiffStat}
    console_override: Console | None = None,
) -> dict:
    """
    Call this ONCE, at the very end of `axiom run` (after --feedback /
    --save has resolved), or at the end of `axiom scan-dir` after the
    project-wide approval prompt resolves. Reads token_tracker's
    in-process accumulator for "this run"'s token usage — do NOT call
    this before the run is fully done, or the numbers will be partial.

    Returns the computed summary dict as well as printing it, so callers
    (e.g. a future `--json` output mode) can consume it programmatically.
    """
    app = console_override or console
    from smart_loader.core.token_tracker import get_summary
    token_summary = get_summary()

    total_added   = sum(d.added for d in line_diffs.values())
    total_removed = sum(d.removed for d in line_diffs.values())

    by_agent_cost: dict[str, float] = {}
    total_cost = 0.0
    for agent, stats in token_summary.get("by_agent", {}).items():
        cost = estimate_cost(provider, model, stats["input"], stats["output"])
        by_agent_cost[agent] = cost
        total_cost += cost

    app.print()
    app.print(Panel(
        f"[bold]Elapsed time:[/bold]      {_fmt_elapsed(elapsed_seconds)}\n"
        f"[bold]Files changed:[/bold]     {len(line_diffs)}\n"
        f"[bold]Lines added:[/bold]       [green]+{total_added}[/green]\n"
        f"[bold]Lines removed:[/bold]     [red]-{total_removed}[/red]\n"
        f"[bold]Total input tokens:[/bold]  {token_summary.get('total_input', 0):,}\n"
        f"[bold]Total output tokens:[/bold] {token_summary.get('total_output', 0):,}\n"
        f"[bold]Estimated cost:[/bold]    "
        + (f"${total_cost:.4f}" if total_cost > 0 else "$0.00 (local/free provider, or no pricing entry for this model)"),
        title="AXIOM — Run Summary",
        border_style="bold cyan",
    ))

    if len(line_diffs) > 1:
        table = Table(title="Lines Changed by File", header_style="bold cyan")
        table.add_column("File")
        table.add_column("Added", justify="right")
        table.add_column("Removed", justify="right")
        table.add_column("Total", justify="right")
        for f, d in line_diffs.items():
            table.add_row(f, f"[green]+{d.added}[/green]", f"[red]-{d.removed}[/red]", str(d.changed))
        app.print(table)

    if token_summary.get("total_calls", 0) > 0:
        table = Table(title="Token Usage & Cost by Agent", header_style="bold cyan")
        table.add_column("Agent")
        table.add_column("Calls", justify="right")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Est. Cost", justify="right")
        for agent, stats in token_summary.get("by_agent", {}).items():
            cost = by_agent_cost.get(agent, 0.0)
            table.add_row(
                agent, str(stats["calls"]), f"{stats['input']:,}", f"{stats['output']:,}",
                f"${cost:.4f}" if cost > 0 else "—",
            )
        app.print(table)

    return {
        "elapsed_seconds": elapsed_seconds,
        "files_changed":   len(line_diffs),
        "lines_added":     total_added,
        "lines_removed":   total_removed,
        "per_file":        {f: {"added": d.added, "removed": d.removed} for f, d in line_diffs.items()},
        "total_input_tokens":  token_summary.get("total_input", 0),
        "total_output_tokens": token_summary.get("total_output", 0),
        "estimated_cost_usd":  round(total_cost, 6),
        "by_agent_cost_usd":   {k: round(v, 6) for k, v in by_agent_cost.items()},
    }
