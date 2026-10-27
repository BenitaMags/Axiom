"""
core/rules_engine.py
─────────────────────
Deterministic rules engine for Python package validation.

DIRECTLY INSPIRED BY CodeAugur's Rules Engine:
  CodeAugur runs deterministic checks on binary trace data BEFORE the LLM:
    - return_register_delta  → do both functions return the same value?
    - stack_balanced         → does the stack pointer return to its start?
    - callee_saved_preserved → are callee-saved registers restored?
    - instruction_count_ratio → are the programs roughly the same length?

  Each rule returns PASS | FAIL | UNKNOWN. UNKNOWN means data was unavailable
  — rules never crash the pipeline.

  We apply the exact same design to Python packages:
    - is_installed           → is the package available on this machine?
    - version_compatible     → is the installed version recent enough?
    - license_permissive     → is the license open source?
    - api_surface_overlap    → do the packages share key function names?

  Fast, cheap, no LLM tokens spent. High-confidence signals first.
"""

from __future__ import annotations
import importlib
import importlib.metadata
import subprocess
import sys
from typing import Callable
from rich.console import Console
from rich.table import Table

from smart_loader.core.state import RuleResult

console = Console()

# ── Rule registry ──────────────────────────────────────────────────────────────

_RULES: dict[str, Callable] = {}

def rule(name: str, confidence: str = "HIGH"):
    """Decorator to register a rule function."""
    def decorator(fn):
        _RULES[name] = (fn, confidence)
        return fn
    return decorator


# ── Built-in rules ─────────────────────────────────────────────────────────────

@rule("is_installed", confidence="HIGH")
def _is_installed(package: str) -> tuple[str, dict]:
    """Check if the package is importable on this machine."""
    try:
        importlib.import_module(package)
        return "PASS", {"message": f"{package} is installed and importable"}
    except ImportError:
        return "FAIL", {"message": f"{package} is not installed"}
    except Exception as e:
        return "UNKNOWN", {"message": str(e)}


@rule("has_metadata", confidence="MEDIUM")
def _has_metadata(package: str) -> tuple[str, dict]:
    """Check if the package has PyPI metadata (version, license)."""
    try:
        meta = importlib.metadata.metadata(package)
        version = meta.get("Version", "unknown")
        license_ = meta.get("License", "unknown")
        return "PASS", {"version": version, "license": license_}
    except importlib.metadata.PackageNotFoundError:
        return "UNKNOWN", {"message": f"No metadata found for {package}"}


@rule("license_permissive", confidence="MEDIUM")
def _license_permissive(package: str) -> tuple[str, dict]:
    """Check if the package uses a permissive open-source license."""
    PERMISSIVE = {"mit", "apache", "bsd", "isc", "mpl", "unlicense", "public domain"}
    try:
        meta = importlib.metadata.metadata(package)
        license_ = (meta.get("License") or "").lower()
        is_permissive = any(p in license_ for p in PERMISSIVE)
        result = "PASS" if is_permissive else "UNKNOWN"
        return result, {"license": license_, "permissive": is_permissive}
    except Exception as e:
        return "UNKNOWN", {"message": str(e)}


@rule("api_surface_overlap", confidence="MEDIUM")
def _api_surface_overlap(package: str, reference: str = "") -> tuple[str, dict]:
    """
    Check if the package exposes at least some overlapping public API names
    with a reference package. This is our analog to CodeAugur's
    jaccard_opcode metric — a structural similarity measure.
    """
    if not reference:
        return "UNKNOWN", {"message": "No reference package provided"}
    try:
        mod_a = importlib.import_module(package)
        mod_b = importlib.import_module(reference)
        api_a = set(dir(mod_a))
        api_b = set(dir(mod_b))
        intersection = api_a & api_b
        union = api_a | api_b
        jaccard = len(intersection) / len(union) if union else 0.0
        result = "PASS" if jaccard > 0.1 else "FAIL"
        return result, {
            "jaccard_api_overlap": round(jaccard, 3),
            "shared_names": sorted(list(intersection))[:10],  # top 10
        }
    except Exception as e:
        return "UNKNOWN", {"message": str(e)}


@rule("not_transitive_dependency", confidence="HIGH")
def _not_transitive_dependency(package: str, reference: str = "") -> tuple[str, dict]:
    """
    Fail if one package is a declared dependency of the other.
    Directly mirrors CodeAugur's constraint that parent/child binary modules
    are not interchangeable alternatives.

    Examples caught: fastapi+starlette, requests+urllib3, flask+werkzeug.
    If package A lists package B in its install_requires (or vice versa),
    they should NEVER be grouped as equivalents — one is built on the other.
    """
    if not reference:
        return "UNKNOWN", {"message": "No reference package provided"}
    try:
        import re as _re

        def _norm(name: str) -> str:
            return _re.sub(r"[-_.]+", "_", name).lower()

        def _get_deps(pkg: str) -> set[str]:
            try:
                reqs = importlib.metadata.requires(pkg) or []
                # Each entry looks like: "starlette>=0.37.2" or "urllib3 ; extra == 'security'"
                deps = set()
                for r in reqs:
                    # Strip version specifiers and environment markers
                    name_part = _re.split(r"[>=<!;\s\[]", r)[0].strip()
                    if name_part:
                        deps.add(_norm(name_part))
                return deps
            except Exception:
                return set()

        pkg_norm = _norm(package)
        ref_norm = _norm(reference)

        # Check: is package a dependency of reference?
        ref_deps = _get_deps(reference)
        if pkg_norm in ref_deps:
            return "FAIL", {
                "message": f"{package} is a declared dependency of {reference} — not interchangeable",
                "relationship": f"{reference} depends on {package}",
            }

        # Check: is reference a dependency of package?
        pkg_deps = _get_deps(package)
        if ref_norm in pkg_deps:
            return "FAIL", {
                "message": f"{reference} is a declared dependency of {package} — not interchangeable",
                "relationship": f"{package} depends on {reference}",
            }

        return "PASS", {"message": f"No dependency relationship between {package} and {reference}"}

    except Exception as e:
        return "UNKNOWN", {"message": str(e)}


# ── Known Python stdlib module names (never scan for PyPI metadata or CVEs) ───────
# sys.stdlib_module_names is available in Python 3.10+; we maintain a fallback
# set for older versions covering the most commonly imported stdlib modules.
_STDLIB_MODULES: frozenset[str] = frozenset()
try:
    import sys as _sys
    if hasattr(_sys, "stdlib_module_names"):
        _STDLIB_MODULES = frozenset(_sys.stdlib_module_names)
    else:
        # Fallback for Python < 3.10
        _STDLIB_MODULES = frozenset({
            "abc", "ast", "asyncio", "base64", "binascii", "builtins",
            "collections", "concurrent", "configparser", "contextlib",
            "copy", "csv", "dataclasses", "datetime", "decimal",
            "email", "enum", "errno", "functools", "gc", "glob",
            "gzip", "hashlib", "hmac", "http", "importlib", "inspect",
            "io", "itertools", "json", "keyword", "linecache", "logging",
            "math", "multiprocessing", "operator", "os", "pathlib",
            "pickle", "platform", "pprint", "queue", "random", "re",
            "secrets", "shutil", "signal", "socket", "sqlite3",
            "ssl", "stat", "string", "struct", "subprocess",
            "sys", "tempfile", "threading", "time", "timeit",
            "tomllib", "traceback", "types", "typing", "unicodedata",
            "unittest", "urllib", "uuid", "warnings", "weakref",
            "xml", "zipfile", "zlib",
        })
except Exception:
    pass


def is_stdlib(package: str) -> bool:
    """Return True if package is a Python standard library module."""
    top_level = package.split(".")[0].lower()
    return top_level in _STDLIB_MODULES


# ── Rules engine runner ────────────────────────────────────────────────────────

def run_rules(packages: list[str], reference_map: dict[str, str] = None) -> list[RuleResult]:
    """
    Run all registered rules against a list of packages.
    Returns a flat list of RuleResult objects.

    reference_map: { "httpx": "requests" } — used for api_surface_overlap rule
    """
    reference_map = reference_map or {}
    results: list[RuleResult] = []

    console.rule("[bold blue]⚙️  Rules Engine")

    table = Table(title="Rule Results", header_style="bold blue")
    table.add_column("Package")
    table.add_column("Rule")
    table.add_column("Result", justify="center")
    table.add_column("Confidence")
    table.add_column("Detail")

    for package in packages:
        for rule_name, (rule_fn, confidence) in _RULES.items():
            try:
                if rule_name in ("api_surface_overlap", "not_transitive_dependency"):
                    ref = reference_map.get(package, "")
                    result_str, detail = rule_fn(package, ref)
                elif rule_name == "is_installed" and is_stdlib(package):
                    # Skip is_installed for stdlib modules — they are always available
                    # and have no PyPI metadata; treating them as uninstalled is wrong.
                    result_str, detail = "PASS", {"message": f"{package} is a stdlib module"}
                elif rule_name in ("has_metadata", "license_permissive") and is_stdlib(package):
                    # Stdlib modules have no PyPI metadata — skip these checks
                    result_str = "UNKNOWN"
                    detail = {"message": f"{package} is a stdlib module — no PyPI metadata"}
                else:
                    result_str, detail = rule_fn(package)
            except Exception as e:
                result_str, detail = "UNKNOWN", {"error": str(e)}

            color = {"PASS": "green", "FAIL": "red", "UNKNOWN": "yellow"}.get(result_str, "white")
            detail_str = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:2])

            table.add_row(
                package,
                rule_name,
                f"[{color}]{result_str}[/{color}]",
                confidence,
                detail_str,
            )

            results.append(RuleResult(
                rule=rule_name,
                package=package,
                result=result_str,
                confidence=confidence,
                detail=detail,
            ))

    console.print(table)
    console.print(f"\n[green]✓ Rules engine: {len(results)} checks across {len(packages)} packages[/green]\n")
    return results

def rules_agent(state) -> dict:
    """LangGraph node — runs deterministic rule checks on all imported packages."""
    from rich.console import Console
    from langchain_core.messages import AIMessage
    from smart_loader.core.state import ImportInfo, AgentEvent
    from smart_loader.core.rules_engine import run_rules

    console = Console()
    console.rule("[bold blue]⚙️  Rules Agent")

    imports = state.get("imports", [])
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="rules", event="started"))

    if not imports:
        console.print("[yellow]No imports — skipping rules.[/yellow]")
        return {
            "rule_results": [],
            "agent_trace": trace,
            "messages": [AIMessage(content="Rules Agent: no imports, skipped.")],
        }

    packages = list({imp.module.split(".")[0] for imp in imports})

    reference_map: dict[str, str] = {}
    for i, pkg in enumerate(packages):
        for j, other in enumerate(packages):
            if i != j:
                reference_map.setdefault(pkg, other)

    rule_results = run_rules(packages, reference_map)

    disqualified = {
        r.package for r in rule_results
        if r.rule == "is_installed" and r.result == "FAIL"
    }

    if disqualified:
        console.print(f"[red]Disqualified (not installed): {', '.join(disqualified)}[/red]")
    else:
        console.print("[green]All packages installed and available[/green]")

    trace.append(AgentEvent(
        stage="rules", event="completed",
        detail={"packages_checked": len(packages), "disqualified": list(disqualified)},
    ))

    return {
        "rule_results": rule_results,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Rules Agent: checked {len(packages)} packages, "
                    f"disqualified {len(disqualified)}: {disqualified or 'none'}"
        )],
    }
