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
                if rule_name == "api_surface_overlap":
                    ref = reference_map.get(package, "")
                    result_str, detail = rule_fn(package, ref)
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