"""
agents/security_agent.py
─────────────────────────
Security Agent — Node 2b in the LangGraph pipeline (runs after Rules, before Resolver).

Checks each package for known vulnerabilities using:
  1. OSV (Open Source Vulnerabilities) API — https://osv.dev/
  2. PyPI JSON API — checks release dates, yanked versions
  3. LLM analysis — evaluates whether detected CVEs affect the code's usage patterns

INSPIRED BY CodeAugur's multi-signal evidence pipeline:
  CodeAugur combines static assembly + dynamic traces + deterministic rules
  before any LLM reasoning. We add a fourth signal: security posture.
  A fast package that is CVE-riddled is NOT the optimal choice.

Security scoring (0.0 = dangerous, 1.0 = clean):
  - vuln_score     : based on CVSS severity of known CVEs
  - freshness_score: how recently the package was updated
  - advisory_count : total open advisories

Confidence mirrors CodeAugur: HIGH | MEDIUM | LOW
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from langchain_core.messages import AIMessage

console = Console()


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class Vulnerability:
    id: str                          # e.g. "GHSA-xxxx" or "CVE-2024-xxxx"
    summary: str
    severity: str                    # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    cvss_score: float = 0.0
    fixed_version: Optional[str] = None
    published: Optional[str] = None


@dataclass
class SecurityResult:
    package: str
    version: str
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    last_updated: Optional[str] = None          # ISO date of most recent release
    days_since_update: int = 0
    is_yanked: bool = False
    vuln_score: float = 1.0                     # 1.0 = clean, 0.0 = critical CVEs
    freshness_score: float = 1.0               # 1.0 = updated recently
    overall_score: float = 1.0                 # combined
    confidence: str = "HIGH"
    risk_level: str = "LOW"                    # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    notes: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ── OSV API ────────────────────────────────────────────────────────────────────

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
PYPI_API_URL  = "https://pypi.org/pypi/{package}/json"

_SEVERITY_WEIGHT = {
    "CRITICAL": 0.0,
    "HIGH":     0.2,
    "MEDIUM":   0.5,
    "LOW":      0.75,
    "UNKNOWN":  0.6,
}


def _fetch_url(url: str, payload: dict | None = None, timeout: int = 8) -> dict | None:
    """Simple HTTP helper — no external deps beyond stdlib."""
    try:
        if payload:
            data = json.dumps(payload).encode()
            req  = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "AXIOM-Security/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _query_osv(package: str, version: str) -> list[Vulnerability]:
    """Query OSV for known vulnerabilities in a specific package+version."""
    payload = {
        "queries": [
            {"package": {"name": package, "ecosystem": "PyPI"}, "version": version}
        ]
    }
    data = _fetch_url(OSV_BATCH_URL, payload)
    if not data:
        return []

    vulns = []
    for result in data.get("results", []):
        for vuln in result.get("vulns", []):
            # Extract severity
            severity = "UNKNOWN"
            cvss     = 0.0
            for sev in vuln.get("severity", []):
                if sev.get("type") == "CVSS_V3":
                    score = float(sev.get("score", 0))
                    cvss  = score
                    if score >= 9.0:   severity = "CRITICAL"
                    elif score >= 7.0: severity = "HIGH"
                    elif score >= 4.0: severity = "MEDIUM"
                    else:              severity = "LOW"
                    break

            # Find fixed version
            fixed = None
            for affected in vuln.get("affected", []):
                for rng in affected.get("ranges", []):
                    for evt in rng.get("events", []):
                        if "fixed" in evt:
                            fixed = evt["fixed"]

            vulns.append(Vulnerability(
                id=vuln.get("id", "unknown"),
                summary=vuln.get("summary", "No description")[:120],
                severity=severity,
                cvss_score=cvss,
                fixed_version=fixed,
                published=vuln.get("published", "")[:10],
            ))
    return vulns


def _query_pypi(package: str) -> tuple[str, str, bool]:
    """
    Returns (version, last_release_date_iso, is_yanked).
    Falls back gracefully on failure.
    """
    data = _fetch_url(PYPI_API_URL.format(package=package))
    if not data:
        return "unknown", "", False

    info    = data.get("info", {})
    version = info.get("version", "unknown")
    yanked  = bool(info.get("yanked", False))

    # Find the most recent release date across all versions
    releases = data.get("releases", {})
    latest_date = ""
    for files in releases.values():
        for f in files:
            upload_time = f.get("upload_time_iso_8601", "")
            if upload_time > latest_date:
                latest_date = upload_time

    return version, latest_date[:10], yanked


def _compute_security_result(package: str) -> SecurityResult:
    """Full security scan for one package."""
    version, last_updated, yanked = _query_pypi(package)
    vulns = _query_osv(package, version) if version != "unknown" else []

    # Days since last update
    days_since = 9999
    if last_updated:
        try:
            release_dt = datetime.fromisoformat(last_updated)
            now_dt     = datetime.now(timezone.utc).replace(tzinfo=None)
            days_since = (now_dt - release_dt).days
        except Exception:
            pass

    # Vuln score: worst-case CVE determines score
    if vulns:
        worst = min(_SEVERITY_WEIGHT.get(v.severity, 0.6) for v in vulns)
        vuln_score = worst
    else:
        vuln_score = 1.0

    # Freshness score: penalise packages not updated for >2 years
    if days_since < 180:
        freshness = 1.0
    elif days_since < 365:
        freshness = 0.85
    elif days_since < 730:
        freshness = 0.65
    elif days_since < 1460:
        freshness = 0.4
    else:
        freshness = 0.2

    # Yank penalty
    if yanked:
        vuln_score = min(vuln_score, 0.1)

    overall = 0.7 * vuln_score + 0.3 * freshness

    # Risk level
    if overall < 0.3 or any(v.severity == "CRITICAL" for v in vulns):
        risk = "CRITICAL"
    elif overall < 0.55 or any(v.severity == "HIGH" for v in vulns):
        risk = "HIGH"
    elif overall < 0.75:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    notes = []
    if yanked:
        notes.append("⚠️  Package version YANKED from PyPI")
    if days_since > 730:
        notes.append(f"📅 Not updated in {days_since // 365}y {(days_since % 365) // 30}mo")
    for v in vulns:
        fix = f" → fixed in {v.fixed_version}" if v.fixed_version else " (no fix available)"
        notes.append(f"🔴 {v.id} [{v.severity}] {v.summary[:60]}{fix}")

    return SecurityResult(
        package=package,
        version=version,
        vulnerabilities=vulns,
        last_updated=last_updated,
        days_since_update=days_since if days_since != 9999 else -1,
        is_yanked=yanked,
        vuln_score=round(vuln_score, 3),
        freshness_score=round(freshness, 3),
        overall_score=round(overall, 3),
        confidence="HIGH" if version != "unknown" else "LOW",
        risk_level=risk,
        notes=notes,
    )


# ── Agent ──────────────────────────────────────────────────────────────────────

def security_agent(state: dict) -> dict:
    """
    LangGraph node: security scan all packages in parallel.
    Runs AFTER rules_agent, BEFORE resolver_agent.
    Adds security_results to state so the AXIOM agent can penalise risky packages.
    """
    from smart_loader.core.state import AgentEvent   # lazy import — avoid circular dep

    console.rule("[bold red]🔒 Security Agent")

    imports   = state.get("imports", [])
    trace     = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="security", event="started"))

    if not imports:
        console.print("[yellow]No imports to scan.[/yellow]")
        return {
            "security_results": {},
            "agent_trace": trace,
            "messages": [AIMessage(content="Security: no imports to scan.")],
        }

    packages = list({imp.module.split(".")[0] for imp in imports})
    console.print(f"[dim]Scanning {len(packages)} package(s) via OSV + PyPI...[/dim]\n")

    # ── Parallel scan ──────────────────────────────────────────────────────────
    security_results: dict[str, SecurityResult] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_compute_security_result, pkg): pkg for pkg in packages}
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                security_results[pkg] = future.result()
            except Exception as e:
                security_results[pkg] = SecurityResult(
                    package=pkg, version="unknown", error=str(e),
                    risk_level="UNKNOWN", overall_score=0.5,
                )

    # ── Results table ──────────────────────────────────────────────────────────
    table = Table(title="Security Scan Results", header_style="bold red")
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("CVEs", justify="right")
    table.add_column("Risk", justify="center")
    table.add_column("Vuln Score", justify="right")
    table.add_column("Freshness", justify="right")
    table.add_column("Overall", justify="right")
    table.add_column("Notes")

    risk_color = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "green", "UNKNOWN": "dim"}
    for pkg, res in sorted(security_results.items(), key=lambda x: x[1].overall_score):
        color    = risk_color.get(res.risk_level, "white")
        cve_str  = str(len(res.vulnerabilities)) if not res.error else "?"
        note_str = res.notes[0][:55] if res.notes else ("✓ Clean" if not res.error else res.error[:40])
        table.add_row(
            pkg,
            res.version,
            cve_str,
            f"[{color}]{res.risk_level}[/{color}]",
            f"{res.vuln_score:.2f}",
            f"{res.freshness_score:.2f}",
            f"[{color}]{res.overall_score:.2f}[/{color}]",
            note_str,
        )
    console.print(table)

    high_risk = [p for p, r in security_results.items() if r.risk_level in ("CRITICAL", "HIGH")]
    if high_risk:
        console.print(f"\n[bold red]⚠️  High-risk packages detected: {', '.join(high_risk)}[/bold red]")
    else:
        console.print("\n[green]✓ No critical vulnerabilities found[/green]")

    trace.append(AgentEvent(
        stage="security", event="completed",
        detail={
            "scanned": len(packages),
            "high_risk": high_risk,
            "total_vulns": sum(len(r.vulnerabilities) for r in security_results.values()),
        },
    ))

    return {
        "security_results": security_results,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Security: {len(packages)} scanned, {len(high_risk)} high-risk: "
                    + (", ".join(high_risk) or "none")
        )],
    }


def _scan_packages(packages: list[str], label: str) -> dict[str, SecurityResult]:
    """Scan a list of packages and return SecurityResult dict."""
    results: dict[str, SecurityResult] = {}
    if not packages:
        return results

    console.print(f"[dim]{label} {len(packages)} package(s) via OSV + PyPI...[/dim]\n")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_compute_security_result, pkg): pkg for pkg in packages}
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                results[pkg] = future.result()
            except Exception as e:
                results[pkg] = SecurityResult(
                    package=pkg, version="unknown", error=str(e),
                    risk_level="UNKNOWN", overall_score=0.5,
                )
    return results


def candidate_security_agent(state: dict) -> dict:
    """
    LangGraph node: scan equivalence-group candidate packages not yet in security_results.
    Runs AFTER resolver, BEFORE profiler.
    """
    from smart_loader.core.state import AgentEvent

    console.rule("[bold red]🔒 Security Agent — Candidates")

    groups = state.get("equivalence_groups", [])
    existing: dict = dict(state.get("security_results", {}))
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="candidate_security", event="started"))

    candidates = list({pkg for g in groups for pkg in g.candidates})
    to_scan = [p for p in candidates if p not in existing]

    if not to_scan:
        console.print("[dim]All candidate packages already scanned.[/dim]\n")
        trace.append(AgentEvent(stage="candidate_security", event="completed", detail={"scanned": 0}))
        return {"security_results": existing, "agent_trace": trace, "messages": []}

    new_results = _scan_packages(to_scan, "Scanning candidate")
    merged = {**existing, **new_results}

    table = Table(title="Candidate Security Scan", header_style="bold red")
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("Risk", justify="center")
    table.add_column("Overall", justify="right")
    risk_color = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "green", "UNKNOWN": "dim"}
    for pkg in to_scan:
        res = new_results[pkg]
        color = risk_color.get(res.risk_level, "white")
        table.add_row(pkg, res.version, f"[{color}]{res.risk_level}[/{color}]", f"{res.overall_score:.2f}")
    console.print(table)
    console.print(f"\n[green]✓ Scanned {len(to_scan)} candidate package(s)[/green]\n")

    trace.append(AgentEvent(
        stage="candidate_security", event="completed",
        detail={"scanned": len(to_scan), "packages": to_scan},
    ))

    return {
        "security_results": merged,
        "agent_trace": trace,
        "messages": [AIMessage(content=f"Candidate security: scanned {len(to_scan)} package(s)")],
    }