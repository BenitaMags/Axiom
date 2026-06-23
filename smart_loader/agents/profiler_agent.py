"""
agents/profiler_agent.py
─────────────────────────
Profiler Agent — benchmarks import cost AND runtime API performance.
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
RUNTIME_RUNS     = 5
TIMEOUT_SEC    = 15

# Known runtime micro-benchmark snippets per (package, api_token).
# api_token is the last segment of used_apis entries (e.g. "sha256", "dumps").
RUNTIME_SNIPPETS: dict[tuple[str, str], str] = {
    ("hashlib", "sha256"): "import hashlib; hashlib.sha256(data).hexdigest()",
    ("xxhash", "sha256"): "import xxhash; xxhash.xxh3_128(data).hexdigest()",
    ("json", "dumps"): "import json; json.dumps(obj)",
    ("json", "loads"): "import json; json.loads(payload)",
    ("orjson", "dumps"): "import orjson; orjson.dumps(obj)",
    ("orjson", "loads"): "import orjson; orjson.loads(orjson.dumps(obj))",
}


def _api_token(used_api: str) -> str:
    return used_api.split(".")[-1].lower()


def _get_python() -> str:
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    for name in ("python3", "python3.12", "python3.11", "python3.10", "python"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found
    raise RuntimeError("No valid Python executable found.")


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

_RUNTIME_SCRIPT = """
import sys, time

snippet = sys.argv[1]
runs = int(sys.argv[2])

data = b"x" * 4096
obj = {"key": "value", "items": list(range(100)), "nested": {"a": 1, "b": "two"}}
payload = '{"key": "value", "items": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}'

times = []
for _ in range(runs):
    t0 = time.perf_counter()
    exec(snippet, {"data": data, "obj": obj, "payload": payload})
    times.append((time.perf_counter() - t0) * 1000)

print(f"OK:{sum(times) / len(times):.3f}")
"""


def _probe_package(package: str) -> BenchmarkResult:
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
            available=False, error=stdout.replace("ERROR:", "") or result.stderr[:80],
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


def _probe_runtime(package: str, api_token: str) -> tuple[float, str] | None:
    snippet = RUNTIME_SNIPPETS.get((package, api_token))
    if not snippet:
        return None
    try:
        result = subprocess.run(
            [_get_python(), "-c", _RUNTIME_SCRIPT, snippet, str(RUNTIME_RUNS)],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
        stdout = result.stdout.strip()
        if stdout.startswith("OK:"):
            return float(stdout[3:]), api_token
    except Exception:
        pass
    return None


def _attach_runtime_benchmarks(
    benchmarks: dict[str, BenchmarkResult],
    groups: list[EquivalenceGroup],
) -> None:
    """Benchmark primary used APIs for each equivalence group."""
    jobs: list[tuple[str, str]] = []
    for group in groups:
        if not group.used_apis:
            continue
        api_token = _api_token(group.used_apis[0])
        for pkg in group.candidates:
            if (pkg, api_token) in RUNTIME_SNIPPETS:
                jobs.append((pkg, api_token))

    if not jobs:
        return

    console.print(f"[dim]Runtime API benchmarks ({RUNTIME_RUNS} runs each)...[/dim]")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_probe_runtime, pkg, api): (pkg, api) for pkg, api in jobs}
        for future in as_completed(futures):
            pkg, api = futures[future]
            measured = future.result()
            if not measured or pkg not in benchmarks or not benchmarks[pkg].available:
                continue
            runtime_ms, api_token = measured
            bm = benchmarks[pkg]
            bm.api_runtimes[api_token] = round(runtime_ms, 3)
            if not bm.runtime_ms or runtime_ms < bm.runtime_ms:
                bm.runtime_ms = round(runtime_ms, 3)
                bm.runtime_api = api_token


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

    all_packages = list({pkg for g in groups for pkg in g.candidates})
    console.print(f"[dim]Probing {len(all_packages)} package(s) across {len(groups)} group(s)...[/dim]\n")

    benchmarks: dict[str, BenchmarkResult] = {}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        tasks = {pkg: progress.add_task(f"Probing [cyan]{pkg}[/cyan]...") for pkg in all_packages}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(_probe_package, pkg): pkg for pkg in all_packages}
            for future in as_completed(futures):
                pkg = futures[future]
                benchmarks[pkg] = future.result()
                progress.update(tasks[pkg], description=f"[green]✓[/green] {pkg}")

    _attach_runtime_benchmarks(benchmarks, groups)

    table = Table(title="Benchmark Results", header_style="bold magenta")
    table.add_column("Package")
    table.add_column("Available", justify="center")
    table.add_column(f"Import ({BENCHMARK_RUNS} runs)", justify="right")
    table.add_column("Runtime API", justify="right")
    table.add_column("Memory (KB)", justify="right")
    table.add_column("Notes")

    for pkg, res in sorted(benchmarks.items(), key=lambda x: x[1].import_time_ms if x[1].available else 9999):
        avail = "[green]✓[/green]" if res.available else "[red]✗[/red]"
        imp_t = f"{res.import_time_ms:.1f} ms" if res.available else "—"
        if res.runtime_ms:
            rt_t = f"{res.runtime_ms:.2f} ms ({res.runtime_api})"
        else:
            rt_t = "—"
        mem = f"{res.memory_kb:.0f}" if res.available else "—"
        table.add_row(pkg, avail, imp_t, rt_t, mem, res.error or "")

    console.print(table)

    available_count = sum(1 for r in benchmarks.values() if r.available)
    runtime_count = sum(1 for r in benchmarks.values() if r.runtime_ms)
    console.print(
        f"\n[green]✓ Profiled {len(benchmarks)} package(s), "
        f"{available_count} available, {runtime_count} with runtime benchmarks[/green]\n"
    )

    trace.append(AgentEvent(
        stage="profiler", event="completed",
        detail={
            "packages": len(benchmarks),
            "available": available_count,
            "runtime_benchmarks": runtime_count,
            "timings": {k: v.import_time_ms for k, v in benchmarks.items() if v.available},
        },
    ))

    return {
        "benchmarks":  benchmarks,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Profiler: {available_count}/{len(benchmarks)} available, "
                    f"{runtime_count} runtime benchmarks."
        )],
    }
