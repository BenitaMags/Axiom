"""
agents/profiler_agent.py
─────────────────────────
Profiler Agent — Node 4 in the LangGraph pipeline.

Benchmarks every candidate package:
  - Import time (ms, averaged over N runs)
  - Memory footprint (KB) after import
  - Availability check

INSPIRED BY CodeAugur's distance metrics:
  CodeAugur computes multiple numeric distance scores between two binaries:
    - register_delta    → how different are the CPU register states?
    - trace_entropy     → how different is the complexity of execution?
    - energy_difference → how different is total register activity?

  We compute analogous distance metrics between package candidates:
    - import_time_ms    → analogous to energy_difference (execution cost)
    - memory_kb         → analogous to register_delta (resource footprint)
    - api_overlap_score → analogous to jaccard_opcode (structural similarity)

  CodeAugur runs emulation in isolated subprocess calls to avoid polluting
  the main process. We do exactly the same with subprocess benchmarking —
  each import test runs in its own child process, fresh cache, no side effects.
"""

from __future__ import annotations
import subprocess
import sys
import shutil
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from langchain_core.messages import AIMessage

from smart_loader.core.state import EquivalenceGroup, BenchmarkResult, AgentEvent, LoaderState

console = Console()

BENCHMARK_RUNS = 3
TIMEOUT_SEC    = 15


# ── Safe Python executable resolver ───────────────────────────────────────────

def _get_python() -> str:
    """
    Find the real Python executable path safely.
    Handles conda envs where sys.executable may point to a directory.
    """
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    for name in ("python3", "python3.12", "python3.11", "python3.10", "python"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found
    raise RuntimeError("No valid Python executable found.")


# ── Timer script (runs in subprocess) ─────────────────────────────────────────

_TIMER_SCRIPT = """
import sys, time, importlib, tracemalloc

pkg  = sys.argv[1]
runs = int(sys.argv[2])

times = []
for _ in range(runs):
    mods = [m for m in sys.modules if m == pkg or m.startswith(pkg + '.')]
    for m in mods:
        del sys.modules[m]

    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        importlib.import_module(pkg)
        t1 = time.perf_counter()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        times.append((t1 - t0) * 1000)
        mem_kb = peak / 1024
    except Exception as e:
        tracemalloc.stop()
        print(f"ERROR:{e}")
        sys.exit(1)

avg_ms = sum(times) / len(times)
print(f"OK:{avg_ms:.3f}:{mem_kb:.1f}")
"""


def _probe_package(package: str) -> BenchmarkResult:
    """Benchmark one package in a subprocess. Returns BenchmarkResult."""
    try:
        result = subprocess.run(
            [_get_python(), "-c", _TIMER_SCRIPT, package, str(BENCHMARK_RUNS)],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
        stdout = result.stdout.strip()
        if stdout.startswith("OK:"):
            parts = stdout[3:].split(":")
            avg_ms = float(parts[0])
            mem_kb = float(parts[1]) if len(parts) > 1 else 0.0
            return BenchmarkResult(
                package=package,
                import_time_ms=round(avg_ms, 3),
                memory_kb=round(mem_kb, 1),
                available=True,
            )
        return BenchmarkResult(
            package=package, import_time_ms=0.0, memory_kb=0.0,
            available=False, error=stdout.replace("ERROR:", ""),
        )
    except subprocess.TimeoutExpired:
        return BenchmarkResult(
            package=package, import_time_ms=0.0, memory_kb=0.0,
            available=False, error=f"Timed out after {TIMEOUT_SEC}s",
        )
    except Exception as e:
        return BenchmarkResult(
            package=package, import_time_ms=0.0, memory_kb=0.0,
            available=False, error=str(e),
        )


# ── Agent ──────────────────────────────────────────────────────────────────────

def profiler_agent(state: LoaderState) -> dict:
    console.rule("[bold magenta]⚡ Profiler Agent")

    groups: list[EquivalenceGroup] = state.get("equivalence_groups", [])
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="profiler", event="started"))

    if not groups:
        console.print("[yellow]No equivalence groups to profile.[/yellow]")
        return {
            "benchmarks":  {},
            "agent_trace": trace,
            "messages":    [AIMessage(content="Profiler: nothing to benchmark.")],
        }

    # Collect unique packages
    all_packages = list({pkg for g in groups for pkg in g.candidates})
    console.print(f"[dim]Probing {len(all_packages)} package(s) across {len(groups)} group(s)...[/dim]\n")

    # ── Parallel benchmarking ──────────────────────────────────────────────────
    benchmarks: dict[str, BenchmarkResult] = {}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        tasks = {pkg: progress.add_task(f"Probing [cyan]{pkg}[/cyan]...") for pkg in all_packages}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(_probe_package, pkg): pkg for pkg in all_packages}
            for future in as_completed(futures):
                pkg = futures[future]
                benchmarks[pkg] = future.result()
                progress.update(tasks[pkg], description=f"[green]✓[/green] {pkg}")

    # ── Results table ──────────────────────────────────────────────────────────
    table = Table(title="Benchmark Results", header_style="bold magenta")
    table.add_column("Package")
    table.add_column("Available", justify="center")
    table.add_column(f"Avg Import Time ({BENCHMARK_RUNS} runs)", justify="right")
    table.add_column("Memory (KB)", justify="right")
    table.add_column("Notes")

    for pkg, res in sorted(benchmarks.items(), key=lambda x: x[1].import_time_ms if x[1].available else 9999):
        avail = "[green]✓[/green]" if res.available else "[red]✗[/red]"
        time_ = f"{res.import_time_ms:.1f} ms" if res.available else "—"
        mem   = f"{res.memory_kb:.0f}" if res.available else "—"
        table.add_row(pkg, avail, time_, mem, res.error or "")

    console.print(table)

    available_count = sum(1 for r in benchmarks.values() if r.available)
    console.print(f"\n[green]✓ Profiled {len(benchmarks)} package(s), {available_count} available[/green]\n")

    trace.append(AgentEvent(
        stage="profiler", event="completed",
        detail={
            "packages": len(benchmarks),
            "available": available_count,
            "timings": {k: v.import_time_ms for k, v in benchmarks.items() if v.available},
        },
    ))

    return {
        "benchmarks":  benchmarks,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Profiler: {available_count}/{len(benchmarks)} available. "
                    + "; ".join(f"{k}:{v.import_time_ms:.1f}ms" for k, v in benchmarks.items() if v.available)
        )],
    }