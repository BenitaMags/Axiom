"""
agents/security_agent.py
─────────────────────────
Security Agent — runs after Parser and before Resolver in the LangGraph pipeline.

Checks each package for known vulnerabilities using:
  1. OSV (Open Source Vulnerabilities) API — https://osv.dev/
  2. PyPI JSON API — checks release dates, yanked versions

This agent reports registry/advisory evidence only.  It does not claim an LLM
assessment of whether a CVE is reachable from a particular call site.

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
import sys
import ssl
import math
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
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
    # Latest PyPI release, retained when ``version`` is a historical pinned
    # release selected from the project's dependency metadata.
    latest_version: Optional[str] = None
    version_origin: str = "latest"             # latest | pinned | stdlib | unknown
    # The distribution queried can differ from the import root (for example,
    # ``kafka`` is supplied by the ``kafka-python`` distribution).
    queried_package: str = ""
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    last_updated: Optional[str] = None          # ISO date of most recent release
    days_since_update: int = 0
    is_yanked: bool = False
    vuln_score: float = 1.0                     # 1.0 = clean, 0.0 = critical CVEs
    freshness_score: float = 1.0               # 1.0 = updated recently
    overall_score: float = 1.0                 # combined
    confidence: str = "HIGH"
    risk_level: str = "LOW"                    # or "UNKNOWN" / "N/A"
    # Scores are authoritative only when both PyPI metadata and OSV advisory
    # data were fetched.  Partial or unavailable data is displayed as such
    # and is not used as a security ranking signal by AXIOM.
    data_status: str = "available"             # available | partial | unavailable | not_found | not_applicable
    notes: list[str] = field(default_factory=list)
    error: Optional[str] = None
    # One-level transitive dependency scan (SCA) — see
    # _scan_transitive_dependencies. Scored only when scan_status=="scanned";
    # partial results are display-only, never a ranking signal.
    transitive_advisory_count: int = 0
    transitive_worst_severity: str = ""
    transitive_scan_status: str = "not_scanned"  # scanned | partial | no_dependencies | not_scanned


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

# A current AXIOM interpreter may list these as standard library modules even
# when the selected historical project Python needs the PyPI backport.
_STDLIB_MIN_MINOR: dict[str, tuple[int, int]] = {
    "contextvars": (3, 7),
    "dataclasses": (3, 7),
    "zoneinfo": (3, 9),
    "graphlib": (3, 9),
    "tomllib": (3, 11),
    "exceptiongroup": (3, 11),
}


def _is_stdlib_for_python(package: str, python_version: str | None = None) -> bool:
    """Whether a module is supplied by the chosen interpreter, not the host."""
    root = package.split(".")[0].casefold()
    if root not in getattr(sys, "stdlib_module_names", frozenset()):
        return False
    minimum = _STDLIB_MIN_MINOR.get(root)
    if minimum is None:
        return True
    raw = str(python_version or f"{sys.version_info.major}.{sys.version_info.minor}")
    match = re.match(r"^(\d+)\.(\d+)", raw)
    return bool(match and (int(match.group(1)), int(match.group(2))) >= minimum)


@dataclass(frozen=True)
class _FetchResult:
    """A JSON response or the diagnostic that prevented one.

    The previous helper returned ``None`` for every exception.  That made an
    unavailable network indistinguishable from a clean package and produced
    the synthetic ``LOW / 1.00 / 0.20 / 0.76`` output in the evaluation log.
    """

    data: dict | None
    error: str | None = None


def _security_ssl_context() -> ssl.SSLContext:
    """Use certifi's current CA bundle when the Python framework lacks one.

    On the macOS framework Python used for this project, direct urllib calls
    can fail with ``CERTIFICATE_VERIFY_FAILED`` even though pip itself works.
    Certifi provides the standard public CA bundle without disabling TLS
    verification.  If it is not installed, retain Python's secure default and
    surface its error to the caller.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch_url(url: str, payload: dict | None = None, timeout: int = 8) -> _FetchResult:
    """Fetch JSON while preserving an actionable failure diagnostic."""
    try:
        headers = {"Accept": "application/json", "User-Agent": "AXIOM-Security/1.1"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers=headers, method="POST",
            )
        else:
            req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_security_ssl_context()) as response:
            decoded = json.loads(response.read().decode())
        if not isinstance(decoded, dict):
            return _FetchResult(None, "response was not a JSON object")
        return _FetchResult(decoded)
    except urllib.error.HTTPError as exc:
        return _FetchResult(None, f"HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        return _FetchResult(None, f"network error: {exc.reason}")
    except TimeoutError:
        return _FetchResult(None, f"timed out after {timeout}s")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _FetchResult(None, f"{type(exc).__name__}: {exc}")


# Successful metadata is stable for the duration of a run.  Cache successes
# only: a transient outage is not frozen across every migration case.
_PYPI_CACHE: dict[str, dict] = {}
_OSV_CACHE: dict[tuple[str, str], tuple[Vulnerability, ...]] = {}


def _severity_for_cvss(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


def _cvss_v3_base_score(vector: str) -> float:
    """Calculate a CVSS v3 base score when OSV supplies a vector string.

    OSV commonly represents ``CVSS_V3`` as ``CVSS:3.1/AV:N/...`` rather
    than a numeric value.  The old ``float(...)`` conversion crashed the
    entire scan as soon as it encountered a real advisory.
    """
    if not vector.startswith("CVSS:3."):
        return 0.0
    metrics = {
        key: value for part in vector.split("/")[1:]
        if ":" in part for key, value in [part.split(":", 1)]
    }
    try:
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[metrics["AV"]]
        ac = {"L": 0.77, "H": 0.44}[metrics["AC"]]
        scope = metrics["S"]
        pr = ({"N": 0.85, "L": 0.62, "H": 0.27} if scope == "U"
              else {"N": 0.85, "L": 0.68, "H": 0.5})[metrics["PR"]]
        ui = {"N": 0.85, "R": 0.62}[metrics["UI"]]
        impact_values = {"H": 0.56, "L": 0.22, "N": 0.0}
        c, integrity, availability = (impact_values[metrics[key]] for key in ("C", "I", "A"))
    except (KeyError, ValueError):
        return 0.0

    impact_subscore = 1 - (1 - c) * (1 - integrity) * (1 - availability)
    if impact_subscore <= 0:
        return 0.0
    impact = (
        6.42 * impact_subscore if scope == "U"
        else 7.52 * (impact_subscore - 0.029) - 3.25 * ((impact_subscore - 0.02) ** 15)
    )
    exploitability = 8.22 * av * ac * pr * ui
    raw = min(impact + exploitability, 10.0) if scope == "U" else min(1.08 * (impact + exploitability), 10.0)
    return math.ceil((raw - 1e-9) * 10) / 10


def _advisory_severity(vulnerability: dict) -> tuple[str, float]:
    """Extract a severity without assuming OSV's score is numeric."""
    words = {
        "CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM",
        "MODERATE": "MEDIUM", "LOW": "LOW",
    }
    for container in (vulnerability.get("database_specific", {}), vulnerability.get("ecosystem_specific", {})):
        severity = str(container.get("severity", "")).upper()
        if severity in words:
            return words[severity], 0.0
    for entry in vulnerability.get("severity", []):
        raw_score = str(entry.get("score", ""))
        try:
            score = float(raw_score)
        except ValueError:
            score = _cvss_v3_base_score(raw_score)
        if score:
            return _severity_for_cvss(score), score
    return "UNKNOWN", 0.0


def _query_osv(package: str, version: str) -> tuple[list[Vulnerability], str | None]:
    """Query OSV for known vulnerabilities in a specific package+version."""
    cache_key = (package.casefold(), version)
    if cache_key in _OSV_CACHE:
        return list(_OSV_CACHE[cache_key]), None
    payload = {
        "queries": [
            {"package": {"name": package, "ecosystem": "PyPI"}, "version": version}
        ]
    }
    response = _fetch_url(OSV_BATCH_URL, payload)
    if response.data is None:
        return [], response.error or "OSV returned no data"

    vulns = []
    for result in response.data.get("results", []):
        for vuln in result.get("vulns", []):
            severity, cvss = _advisory_severity(vuln)

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
    _OSV_CACHE[cache_key] = tuple(vulns)
    return vulns, None


# ── One-level transitive dependency scanning (Software Composition Analysis) ──
# A package's own advisory record says nothing about what it *ships with*: a
# candidate scoring 1.00 on its own advisories can still pull in a vulnerable
# direct dependency, and until this existed AXIOM would never know — the
# "composition" half of SCA was missing entirely. Deliberately one level deep:
# direct dependencies from PyPI's requires_dist metadata (already present in
# the cached PyPI JSON this agent fetches anyway), each resolved at its
# current latest release (what a fresh unpinned install would most plausibly
# take), each checked against OSV through the same cached query path used for
# the package itself. Full-tree resolution belongs to pip, not a scanner with
# an evaluation budget. Real-time/continuous monitoring — the other headline
# SCA feature — is intentionally NOT implemented: it exists for long-running
# deployed systems, while AXIOM queries OSV live at scan time, which is
# already as current as a batch evaluation can meaningfully be.
_MAX_TRANSITIVE_DEPS = 12
_TRANSITIVE_DEP_CACHE: dict[str, tuple[str, tuple, str | None]] = {}
# Conservative, evidence-gated caps applied to the parent's vuln_score when a
# COMPLETE transitive scan finds advisories. Unknown is never treated as
# evidence: a partial scan (any dep lookup failed) applies no penalty and no
# bonus, mirroring this agent's existing fail-closed comparability rules.
_TRANSITIVE_SEVERITY_CAP = {"CRITICAL": 0.5, "HIGH": 0.7}


def _direct_dependencies(distribution: str) -> tuple[list[str], str | None]:
    """Direct runtime dependencies from PyPI ``requires_dist`` metadata.

    Optional-extra requirements (marker mentioning ``extra ==``) are skipped —
    they are not installed by a plain ``pip install`` and counting their
    advisories against a package would be manufacturing risk it doesn't ship.
    Reads the latest release's metadata (what the cached PyPI JSON carries).
    """
    from packaging.requirements import InvalidRequirement, Requirement

    cache_key = distribution.casefold()
    data = _PYPI_CACHE.get(cache_key)
    if data is None:
        response = _fetch_url(PYPI_API_URL.format(package=distribution))
        if response.data is None:
            return [], response.error or "PyPI returned no data"
        data = response.data
        _PYPI_CACHE[cache_key] = data
    raw_requires = (data.get("info") or {}).get("requires_dist") or []
    dependencies: list[str] = []
    for entry in raw_requires:
        try:
            requirement = Requirement(str(entry))
        except InvalidRequirement:
            continue
        if requirement.marker is not None and "extra ==" in str(requirement.marker):
            continue
        name = requirement.name.strip()
        if name and name.casefold() not in {d.casefold() for d in dependencies}:
            dependencies.append(name)
    return dependencies, None


def _scan_transitive_dependencies(
    distribution: str,
) -> tuple[int, str, str, list[str]]:
    """Return (advisory_count, worst_severity, status, notes) for direct deps.

    ``status`` is ``scanned`` (every dep checked), ``partial`` (at least one
    lookup failed — findings are reported but never scored), or
    ``no_dependencies``.
    """
    severity_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
    dependencies, dep_error = _direct_dependencies(distribution)
    if dep_error:
        return 0, "", "partial", [f"🧬 Transitive scan unavailable: {dep_error[:80]}"]
    if not dependencies:
        return 0, "", "no_dependencies", []
    truncated = len(dependencies) > _MAX_TRANSITIVE_DEPS
    scan_list = dependencies[:_MAX_TRANSITIVE_DEPS]
    advisory_count = 0
    worst = ""
    worst_via = ""
    status = "scanned"
    for dep in scan_list:
        cache_key = dep.casefold()
        cached = _TRANSITIVE_DEP_CACHE.get(cache_key)
        if cached is None:
            dep_version, _, _, _, pypi_error = _query_pypi(dep)
            if pypi_error:
                cached = (dep_version, (), pypi_error)
            else:
                dep_vulns, osv_error = _query_osv(dep, dep_version)
                cached = (dep_version, tuple(dep_vulns), osv_error)
            _TRANSITIVE_DEP_CACHE[cache_key] = cached
        dep_version, dep_vulns, lookup_error = cached
        if lookup_error:
            status = "partial"
            continue
        for vuln in dep_vulns:
            advisory_count += 1
            if severity_rank.get(vuln.severity, 0) > severity_rank.get(worst, -1):
                worst = vuln.severity
                worst_via = f"{dep}=={dep_version}"
    notes: list[str] = []
    if advisory_count:
        notes.append(
            f"🧬 {advisory_count} advisory(ies) in direct dependencies "
            f"(worst: {worst} via {worst_via}; deps resolved at latest)"
        )
    if status == "partial":
        notes.append(
            "🧬 Transitive scan incomplete (a dependency lookup failed); "
            "findings shown but NOT scored"
        )
    if truncated:
        notes.append(
            f"🧬 Transitive scan covered first {_MAX_TRANSITIVE_DEPS} of "
            f"{len(dependencies)} direct dependencies"
        )
    return advisory_count, worst, status, notes


def _query_pypi(
    package: str, requested_version: str | None = None,
) -> tuple[str, str, str, bool, str | None]:
    """Return selected version, latest version/date, yanked state, and error.

    The selected version is the historical pin when one is known; otherwise it
    is PyPI's current release.  Freshness always describes the newest release.
    """
    cache_key = package.casefold()
    data = _PYPI_CACHE.get(cache_key)
    if data is None:
        response = _fetch_url(PYPI_API_URL.format(package=package))
        if response.data is None:
            return "unknown", "unknown", "", False, response.error or "PyPI returned no data"
        data = response.data
        _PYPI_CACHE[cache_key] = data

    info = data.get("info", {})
    latest_version = str(info.get("version", "unknown"))
    latest_date = ""
    releases = data.get("releases", {})
    for files in releases.values():
        for file_info in files:
            upload_time = file_info.get("upload_time_iso_8601", "")
            if upload_time > latest_date:
                latest_date = upload_time

    selected_version = requested_version or latest_version
    selected_files = releases.get(selected_version, [])
    if requested_version and not selected_files:
        return (
            selected_version, latest_version, latest_date[:10], False,
            f"PyPI has no release files for pinned version {selected_version}",
        )
    yanked = bool(selected_files) and all(bool(file_info.get("yanked", False)) for file_info in selected_files)
    return selected_version, latest_version, latest_date[:10], yanked, None


def _release_accepts_python(files: list[dict], python_version: str) -> bool:
    """Return whether at least one non-yanked release artifact supports Python.

    PyPI stores ``requires_python`` on individual artifacts, not necessarily
    on the release itself.  Selecting a release based solely on
    ``info.version`` is therefore insufficient for historical projects.
    Missing metadata is treated as unconstrained—the registry has no basis to
    claim incompatibility—but yanked-only files are never accepted.
    """
    try:
        version = Version(str(python_version))
    except InvalidVersion:
        return False
    for file_info in files:
        if file_info.get("yanked", False):
            continue
        constraint = str(file_info.get("requires_python", "") or "").strip()
        if not constraint:
            return True
        try:
            if SpecifierSet(constraint).contains(version, prereleases=True):
                return True
        except InvalidSpecifier:
            # Invalid PyPI metadata should not be mistaken for compatibility.
            continue
    return False


def _select_compatible_release(
    package: str,
    python_version: str,
    requested_version: str | None = None,
) -> tuple[str, str | None]:
    """Select an exact non-yanked stable release compatible with Python.

    The function intentionally returns a diagnostic instead of falling back to
    the registry's latest version.  A latest target that cannot run in the
    selected historical interpreter is not a verified migration target.
    """
    cache_key = package.casefold()
    data = _PYPI_CACHE.get(cache_key)
    if data is None:
        response = _fetch_url(PYPI_API_URL.format(package=package))
        if response.data is None:
            return "", response.error or "PyPI returned no data"
        data = response.data
        _PYPI_CACHE[cache_key] = data
    releases = data.get("releases", {})
    if not isinstance(releases, dict):
        return "", "PyPI response did not contain release metadata"

    if requested_version:
        files = releases.get(requested_version, [])
        if not files:
            return "", f"PyPI has no release files for requested version {requested_version}"
        if not _release_accepts_python(files, python_version):
            return "", f"{package}=={requested_version} has no non-yanked artifact compatible with Python {python_version}"
        return requested_version, None

    candidates: list[tuple[Version, str]] = []
    for raw_version, files in releases.items():
        try:
            parsed = Version(str(raw_version))
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        if not isinstance(files, list) or not _release_accepts_python(files, python_version):
            continue
        candidates.append((parsed, str(raw_version)))
    if not candidates:
        return "", f"No stable non-yanked {package} release is compatible with Python {python_version}"
    return max(candidates, key=lambda item: item[0])[1], None


def _score_freshness(days_since: int) -> float:
    if days_since < 180:
        return 1.0
    if days_since < 365:
        return 0.85
    if days_since < 730:
        return 0.65
    if days_since < 1460:
        return 0.4
    return 0.2


def _unavailable_result(
    package: str,
    distribution: str,
    *,
    status: str,
    error: str,
    version: str = "unknown",
    last_updated: str = "",
    days_since: int = -1,
    freshness: float = 0.0,
    latest_version: str = "",
    version_origin: str = "unknown",
) -> SecurityResult:
    """Make missing evidence visible rather than fabricating a clean score."""
    return SecurityResult(
        package=package,
        queried_package=distribution,
        version=version,
        latest_version=latest_version or None,
        version_origin=version_origin,
        last_updated=last_updated or None,
        days_since_update=days_since,
        # Preserve float fields for backwards compatibility.  Renderers and
        # scoring consult data_status and intentionally hide these values.
        vuln_score=0.0,
        freshness_score=round(freshness, 3),
        overall_score=0.0,
        confidence="UNAVAILABLE" if status == "unavailable" else status.upper(),
        risk_level="UNKNOWN",
        data_status=status,
        notes=[f"⚠ Security data {status}: {error}"],
        error=error,
    )


def _stdlib_result(package: str) -> SecurityResult:
    """A standard-library import is not a PyPI/OSV package."""
    return SecurityResult(
        package=package,
        version="stdlib",
        version_origin="stdlib",
        vuln_score=0.0,
        freshness_score=0.0,
        overall_score=0.0,
        confidence="N/A",
        risk_level="N/A",
        data_status="not_applicable",
        notes=["Python standard-library module; no PyPI/OSV package scan applies."],
    )


def _distribution_for_import(module: str) -> str:
    """Resolve an import root to its PyPI distribution.

    ``importlib.metadata.packages_distributions()`` is asked first, because it
    is the only source that actually knows. ``IMPORT_TO_DISTRIBUTION`` is a
    hand-maintained table and it has no entry for paho, simple_pid or
    modbus_tk, so every one of them fell through to the module name -- and
    ``paho`` is not a distribution, which is why B3 and D3 reported
    "Security data not_found: PyPI metadata for paho: HTTP" on every single
    run. The scanner was querying a name that cannot exist on PyPI, so the
    package it was actually migrating away from was never checked for
    advisories at all.

    The static table stays as the fallback: it carries historical names for
    distributions that are no longer installed here, which the runtime mapping
    cannot know about.
    """
    root = module.split(".")[0]
    try:
        import importlib.metadata as _md
        holders = _md.packages_distributions().get(root)
        if holders:
            return sorted(holders)[0]
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from smart_loader.agents.profiler_agent import IMPORT_TO_DISTRIBUTION
        return IMPORT_TO_DISTRIBUTION.get(root.casefold(), root)
    except Exception:
        return root


def _first_party_modules(source_file: str) -> set[str]:
    """Module names that are files sitting beside ``source_file``.

    These are the project's OWN modules and must never be scanned as PyPI
    packages. B3 imports ``telemetry`` and ``mqtt_config``, which are
    ``telemetry.py`` and ``mqtt_config.py`` in the same directory -- and
    ``telemetry`` is also a real, unrelated distribution on PyPI. The scanner
    looked it up, found version 2.0.8, and reported "not updated in 2y 6mo"
    against the project's own file, feeding a stale-dependency penalty into
    AXIOM's scoring. The Resolver then went looking for replacements for it:
    "telemetry -> opentelemetry-api, sentry-sdk, prometheus-client".

    The failure mode is worse than noise. Had a first-party module shared a
    name with a genuinely vulnerable package, the report would have claimed a
    CVE in code that has none -- a security scanner asserting a vulnerability
    in the wrong software.
    """
    if not source_file:
        return set()
    directory = os.path.dirname(os.path.abspath(source_file))
    if not os.path.isdir(directory):
        return set()
    names: set[str] = set()
    try:
        for entry in os.listdir(directory):
            if entry.endswith(".py") and not entry.startswith("test_"):
                names.add(entry[:-3])
            elif os.path.isdir(os.path.join(directory, entry)) and os.path.isfile(
                    os.path.join(directory, entry, "__init__.py")):
                names.add(entry)
    except OSError:
        return set()
    return names


_EXACT_REQUIREMENT_PIN = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*={2,3}\s*([A-Za-z0-9][A-Za-z0-9.+!_-]*)"
)


def _pinned_version_for_distribution(requirements: list[str], distribution: str) -> str | None:
    """Return the project's exact historical pin for a queried distribution."""
    normalized_distribution = re.sub(r"[-_.]+", "-", distribution.casefold())
    for spec in requirements:
        match = _EXACT_REQUIREMENT_PIN.match(spec)
        if not match:
            continue
        name, version = match.groups()
        if re.sub(r"[-_.]+", "-", name.casefold()) == normalized_distribution:
            return version
    return None


def _canonical_distribution_key(name: str) -> str:
    """PEP 503 comparison key for a package/import label."""
    return re.sub(r"[-_.]+", "-", name.casefold())


def _mapped_target_version(values: dict, candidate: str) -> str | None:
    """Find a requested target version despite import/distribution spelling.

    Pipeline callers can name a target as ``confluent_kafka`` while Resolver
    carries ``confluent-kafka``.  PEP 503 treats those names as equivalent;
    version resolution must do the same.  Prefer an exact non-auto pin when
    both the original request and a later resolved candidate entry exist.
    """
    if not isinstance(values, dict):
        return None
    candidate_keys = {
        _canonical_distribution_key(candidate),
        _canonical_distribution_key(_distribution_for_import(candidate)),
    }
    fallback: str | None = None
    for raw_name, raw_value in values.items():
        if _canonical_distribution_key(str(raw_name)) not in candidate_keys:
            continue
        value = str(raw_value or "").strip()
        if value.casefold() not in {"", "auto", "latest", "latest-compatible"}:
            return value
        fallback = value
    return fallback


def _clear_mapped_target_failure(values: dict, candidate: str) -> None:
    """Remove stale equivalent-name failures after a target resolves."""
    if not isinstance(values, dict):
        return
    candidate_keys = {
        _canonical_distribution_key(candidate),
        _canonical_distribution_key(_distribution_for_import(candidate)),
    }
    for raw_name in list(values):
        if _canonical_distribution_key(str(raw_name)) in candidate_keys:
            values.pop(raw_name, None)


def _alias_source_security_results(existing: dict, state: dict) -> list[tuple[str, str]]:
    """Expose a pinned source scan under the resolver's distribution key.

    Source scans begin from parsed Python imports (``kafka``), whereas the
    resolver and AXIOM correctly score distributions (``kafka-python``).  If
    the candidate-security pass rescans the latter at latest, it destroys the
    historical source-versus-current-target comparison.  Alias the original
    result instead; candidate scans remain latest-version scans.
    """
    roots = {
        imp.module.split(".")[0]
        for imp in state.get("imports", [])
        if getattr(imp, "module", "")
    }
    root_for_distribution = {
        _canonical_distribution_key(_distribution_for_import(root)): root
        for root in roots
    }
    migration_source = str(state.get("migration_source", ""))
    migration_import = str(state.get("migration_source_import", ""))
    aliases: list[tuple[str, str]] = []

    for group in state.get("equivalence_groups", []):
        candidates = list(getattr(group, "candidates", []) or [])
        if not candidates:
            continue
        original = str(getattr(group, "original", "") or candidates[0])
        original_import = str(getattr(group, "original_import", ""))
        if not original_import and migration_import and migration_source and (
            _canonical_distribution_key(original) == _canonical_distribution_key(migration_source)
        ):
            original_import = migration_import
        if not original_import:
            original_import = root_for_distribution.get(_canonical_distribution_key(original), "")

        source_result = existing.get(original_import) if original_import else None
        if source_result is None:
            source_result = next(
                (
                    result for result in existing.values()
                    if _canonical_distribution_key(str(getattr(result, "queried_package", "")))
                    == _canonical_distribution_key(original)
                ),
                None,
            )
        if source_result is not None and original not in existing:
            existing[original] = source_result
            aliases.append((original, original_import or getattr(source_result, "package", "")))
    return aliases


def _compute_security_result(
    package: str,
    distribution: str | None = None,
    requested_version: str | None = None,
    python_version: str | None = None,
    select_compatible_release: bool = True,
) -> SecurityResult:
    """Full security scan for one package or import root.

    The historical source package is queried at its exact project pin when
    available.  Migration candidates select a current, compatible release.
    ``python_version`` still controls version-aware stdlib detection when
    ``select_compatible_release`` is false for a historical source scan.
    """
    distribution = distribution or package
    if _is_stdlib_for_python(package, python_version):
        return _stdlib_result(package)

    selected_requested_version = requested_version
    version_origin = "pinned" if requested_version else "latest"
    if python_version and select_compatible_release:
        selected, selection_error = _select_compatible_release(
            distribution, str(python_version), requested_version=requested_version,
        )
        if selection_error:
            status = "not_found" if selection_error.startswith("HTTP 404") else "unavailable"
            return _unavailable_result(
                package, distribution, status=status,
                error=(
                    f"Target release selection for Python {python_version}: "
                    f"{selection_error}"
                ),
                version=requested_version or "unknown", version_origin="requested" if requested_version else "unresolved",
            )
        selected_requested_version = selected
        version_origin = "requested" if requested_version else "latest_compatible_non_yanked"

    version, latest_version, last_updated, yanked, pypi_error = _query_pypi(
        distribution, selected_requested_version,
    )
    if pypi_error:
        status = "not_found" if pypi_error.startswith("HTTP 404") else "unavailable"
        return _unavailable_result(
            package, distribution, status=status,
            error=f"PyPI metadata for {distribution}: {pypi_error}",
            version=version, latest_version=latest_version, version_origin=version_origin,
        )

    vulns, osv_error = _query_osv(distribution, version)
    days_since = -1
    if last_updated:
        try:
            release_dt = datetime.fromisoformat(last_updated)
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            days_since = (now_dt - release_dt).days
        except ValueError:
            pass
    freshness = _score_freshness(days_since) if days_since >= 0 else 0.0

    if osv_error:
        # Freshness is useful context, but it cannot prove an absence of
        # advisories.  Treat the combined security score as unavailable.
        return _unavailable_result(
            package, distribution, status="partial",
            error=f"OSV advisory lookup for {distribution}=={version}: {osv_error}",
            version=version, last_updated=last_updated, days_since=days_since,
            freshness=freshness, latest_version=latest_version, version_origin=version_origin,
        )

    vuln_score = min((_SEVERITY_WEIGHT.get(v.severity, 0.6) for v in vulns), default=1.0)
    if yanked:
        vuln_score = min(vuln_score, 0.1)

    # One-level transitive scan (see _scan_transitive_dependencies). Scored
    # only when COMPLETE: a package whose composition demonstrably ships a
    # CRITICAL/HIGH-advisory dependency has its vuln_score capped
    # conservatively; a partial scan reports findings in notes but never
    # scores them — unknown is not evidence, same rule as everywhere else in
    # this agent.
    transitive_count, transitive_worst, transitive_status, transitive_notes = (
        _scan_transitive_dependencies(distribution)
    )
    if transitive_status == "scanned" and transitive_worst in _TRANSITIVE_SEVERITY_CAP:
        vuln_score = min(vuln_score, _TRANSITIVE_SEVERITY_CAP[transitive_worst])

    overall = 0.7 * vuln_score + 0.3 * freshness

    if overall < 0.3 or any(v.severity == "CRITICAL" for v in vulns):
        risk = "CRITICAL"
    elif overall < 0.55 or any(v.severity == "HIGH" for v in vulns):
        risk = "HIGH"
    elif overall < 0.75:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    notes = []
    if distribution != package:
        notes.append(f"PyPI distribution: {distribution}")
    if version_origin == "pinned":
        notes.append(f"Historical project pin: {version} (latest PyPI release: {latest_version})")
    elif version_origin == "requested":
        notes.append(f"Requested migration target release: {version} (latest PyPI release: {latest_version})")
    elif version_origin == "latest_compatible_non_yanked":
        notes.append(f"Selected newest stable non-yanked release compatible with Python {python_version}: {version}")
    if yanked:
        notes.append("⚠️  Package version YANKED from PyPI")
    if days_since > 730:
        notes.append(f"📅 Not updated in {days_since // 365}y {(days_since % 365) // 30}mo")
    for vuln in vulns:
        fix = f" → fixed in {vuln.fixed_version}" if vuln.fixed_version else " (no fix available)"
        notes.append(f"🔴 {vuln.id} [{vuln.severity}] {vuln.summary[:60]}{fix}")
    notes.extend(transitive_notes)

    return SecurityResult(
        package=package,
        queried_package=distribution,
        version=version,
        latest_version=latest_version,
        version_origin=version_origin,
        vulnerabilities=vulns,
        last_updated=last_updated or None,
        days_since_update=days_since,
        is_yanked=yanked,
        vuln_score=round(vuln_score, 3),
        freshness_score=round(freshness, 3),
        overall_score=round(overall, 3),
        confidence="HIGH",
        risk_level=risk,
        data_status="available",
        notes=notes,
        transitive_advisory_count=transitive_count,
        transitive_worst_severity=transitive_worst,
        transitive_scan_status=transitive_status,
    )


# ── Agent ──────────────────────────────────────────────────────────────────────

def _security_sort_key(result: SecurityResult) -> tuple[int, float, str]:
    """Put authoritative risk results first; unavailable data is not a score."""
    return (
        0 if result.data_status == "available" else 1,
        result.overall_score if result.data_status == "available" else 0.0,
        result.package.casefold(),
    )


def _score_cell(result: SecurityResult, field: str) -> str:
    """Do not display placeholder numbers as evidence."""
    return f"{getattr(result, field):.2f}" if result.data_status == "available" else "—"


def _evidence_label(result: SecurityResult) -> str:
    return {
        "available": "AVAILABLE",
        "partial": "PARTIAL",
        "unavailable": "UNAVAILABLE",
        "not_found": "NOT FOUND",
        "not_applicable": "N/A",
    }.get(result.data_status, result.data_status.upper())


def _scan_exception(package: str, exc: Exception) -> SecurityResult:
    return _unavailable_result(
        package, package, status="unavailable",
        error=f"security scan raised {type(exc).__name__}: {exc}",
    )

def security_agent(state: dict) -> dict:
    """
    LangGraph node: security scan all packages in parallel.
    Runs after parser_agent and before resolver_agent.
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

    # First-party modules are not dependencies. Scanning them queries PyPI for
    # a name the project happens to use locally, and a match is worse than a
    # miss: it attributes another project's version, staleness and advisories
    # to code that is sitting in the same directory.
    first_party = _first_party_modules(state.get("source_file", ""))
    scanned = {imp.module.split(".")[0] for imp in imports}
    skipped = sorted(scanned & first_party)
    packages = sorted(scanned - first_party)
    if skipped:
        console.print(f"[dim]Skipping {len(skipped)} first-party module(s), not "
                      f"dependencies: {', '.join(skipped)}[/dim]")
    base_requirements = state.get("base_requirements", [])

    # ── Full-manifest SCA ──────────────────────────────────────────────────
    # Scanning only the imports of the file under analysis is narrower than
    # what Software Composition Analysis means: a project with twenty declared
    # dependencies had exactly the handful this file happened to import
    # checked for advisories. Everything else — including packages that only
    # appear transitively at runtime — went unexamined, so "no known CVEs"
    # described one module rather than the project.
    #
    # Declared requirements are added to the scan set. They are already
    # distribution names, so they bypass the import->distribution mapping that
    # the import-derived entries need.
    manifest_distributions: set[str] = set()
    if base_requirements:
        try:
            from packaging.requirements import InvalidRequirement, Requirement

            for spec in base_requirements:
                text = str(spec or "").strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    manifest_distributions.add(Requirement(text).name)
                except InvalidRequirement:
                    continue
        except ImportError:
            # packaging absent: fall back to a conservative split rather than
            # silently scanning nothing.
            for spec in base_requirements:
                head = re.split(r"[<>=!~\[; ]", str(spec).strip(), 1)[0]
                if head and not head.startswith("#"):
                    manifest_distributions.add(head)

    already = {_distribution_for_import(p).lower() for p in packages}
    manifest_only = sorted(d for d in manifest_distributions
                           if d.lower() not in already)
    if manifest_only:
        console.print(f"[dim]Full-manifest SCA: +{len(manifest_only)} declared "
                      f"dependency/ies not imported by this file "
                      f"({', '.join(manifest_only[:6])}"
                      f"{'...' if len(manifest_only) > 6 else ''})[/dim]")
    raw_plan = state.get("environment_plan", {})
    environment_plan = dict(raw_plan) if isinstance(raw_plan, dict) else {}
    # Source scans need the project's interpreter for stdlib-backport
    # detection (for example dataclasses is PyPI-backed on Python 3.6), but
    # must keep the recovered historical pin rather than selecting a newer
    # compatible release.
    source_python = str(environment_plan.get("selected_python_version", "") or "")
    console.print(f"[dim]Scanning {len(packages)} package(s) via OSV + PyPI...[/dim]\n")

    # ── Parallel scan ──────────────────────────────────────────────────────────
    security_results: dict[str, SecurityResult] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(
                _compute_security_result,
                pkg,
                _distribution_for_import(pkg),
                _pinned_version_for_distribution(base_requirements, _distribution_for_import(pkg)),
                source_python or None,
                False,
            ): pkg
            for pkg in packages
        }
        # Manifest-only entries are already distribution names, so the
        # import->distribution mapping is skipped for them.
        futures.update({
            ex.submit(
                _compute_security_result,
                dist,
                dist,
                _pinned_version_for_distribution(base_requirements, dist),
                source_python or None,
                False,
            ): dist
            for dist in manifest_only
        })
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                security_results[pkg] = future.result()
            except Exception as e:
                security_results[pkg] = _scan_exception(pkg, e)

    # ── Results table ──────────────────────────────────────────────────────────
    table = Table(title="Security Scan Results", header_style="bold red")
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("CVEs", justify="right")
    table.add_column("Risk", justify="center")
    table.add_column("Evidence", justify="center")
    table.add_column("Vuln Score", justify="right")
    table.add_column("Freshness", justify="right")
    table.add_column("Overall", justify="right")
    table.add_column("Notes")

    risk_color = {
        "CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "green",
        "UNKNOWN": "dim", "N/A": "dim",
    }
    for pkg, res in sorted(security_results.items(), key=lambda item: _security_sort_key(item[1])):
        color    = risk_color.get(res.risk_level, "white")
        cve_str  = str(len(res.vulnerabilities)) if res.data_status == "available" else (
            "—" if res.data_status == "not_applicable" else "?"
        )
        note_str = res.notes[0][:55] if res.notes else ("✓ Clean" if not res.error else res.error[:40])
        package_label = pkg if not res.queried_package or res.queried_package == pkg else f"{pkg} → {res.queried_package}"
        table.add_row(
            package_label,
            res.version,
            cve_str,
            f"[{color}]{res.risk_level}[/{color}]",
            _evidence_label(res),
            _score_cell(res, "vuln_score"),
            _score_cell(res, "freshness_score"),
            f"[{color}]{_score_cell(res, 'overall_score')}[/{color}]",
            note_str,
        )
    console.print(table)

    high_risk = [p for p, r in security_results.items() if r.risk_level in ("CRITICAL", "HIGH")]
    unavailable = [p for p, r in security_results.items() if r.data_status not in {"available", "not_applicable"}]
    if high_risk:
        console.print(f"\n[bold red]⚠️  High-risk packages detected: {', '.join(high_risk)}[/bold red]")
    elif unavailable:
        console.print(
            f"\n[yellow]⚠ Security conclusion unavailable for {len(unavailable)} package(s): "
            f"{', '.join(unavailable)}. These packages receive no security bonus or penalty.[/yellow]"
        )
    else:
        console.print("\n[green]✓ No critical vulnerabilities found[/green]")

    trace.append(AgentEvent(
        stage="security", event="completed",
        detail={
            "scanned": len(packages),
            "high_risk": high_risk,
            "total_vulns": sum(len(r.vulnerabilities) for r in security_results.values()),
            "data_status": {
                status: sum(1 for r in security_results.values() if r.data_status == status)
                for status in sorted({r.data_status for r in security_results.values()})
            },
        },
    ))

    return {
        "security_results": security_results,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Security: {len(packages)} scanned, {len(high_risk)} high-risk, {len(unavailable)} unavailable: "
                    + (", ".join(high_risk) or "none")
        )],
    }


def _scan_packages(
    packages: list[str],
    label: str,
    *,
    target_python: str = "",
    requested_versions: dict[str, str] | None = None,
    distributions: dict[str, str] | None = None,
) -> dict[str, SecurityResult]:
    """Scan packages, optionally pinning candidates to a compatible release."""
    results: dict[str, SecurityResult] = {}
    if not packages:
        return results

    console.print(f"[dim]{label} {len(packages)} package(s) via OSV + PyPI...[/dim]\n")
    requested_versions = requested_versions or {}
    distributions = distributions or {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(
                _compute_security_result,
                pkg,
                distributions.get(pkg) or None,
                requested_versions.get(pkg) or None,
                target_python or None,
            ): pkg
            for pkg in packages
        }
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                results[pkg] = future.result()
            except Exception as e:
                results[pkg] = _scan_exception(pkg, e)
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

    source_aliases = _alias_source_security_results(existing, state)
    if source_aliases:
        rendered = ", ".join(f"{source} ← {import_root}" for source, import_root in source_aliases)
        console.print(f"[dim]Reusing pinned source security evidence: {rendered}[/dim]")

    candidates = sorted({pkg for g in groups for pkg in g.candidates})
    originals = {
        str(getattr(group, "original", "") or (group.candidates[0] if group.candidates else ""))
        for group in groups
    }
    target_candidates = {candidate for candidate in candidates if candidate and candidate not in originals}
    # A migration target is not allowed to drift to whichever release pip
    # happens to resolve later.  Select the newest stable, non-yanked release
    # compatible with the planned application interpreter, scan *that exact
    # release*, and hand its exact version to Profiler/Verifier.  Historical
    # source packages remain on their recovered pins from ``security_agent``.
    raw_plan = state.get("environment_plan", {})
    environment_plan = dict(raw_plan) if isinstance(raw_plan, dict) else {}
    plan_status = str(environment_plan.get("interpreter_status", "ready") or "ready")
    target_python = str(environment_plan.get("selected_python_version", "") or "")
    if not target_python and plan_status == "ready":
        target_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    raw_requested_versions = dict(state.get("migration_target_versions", {}) or {})
    # Target candidates must be re-scanned even if their import root appeared
    # in the source project.  The pre-migration result describes a historical
    # pin; it cannot supply the current target's release or CVE evidence.
    target_to_scan = sorted(target_candidates, key=str.casefold)
    other_to_scan = [
        package for package in candidates
        if package not in target_candidates and package not in existing
    ]
    to_scan = list(dict.fromkeys([*other_to_scan, *target_to_scan]))
    requested_map = {
        package: version
        for package in target_to_scan
        if (version := _mapped_target_version(raw_requested_versions, package))
        and version.casefold() not in {"", "auto", "latest", "latest-compatible"}
    }

    # Do not use the operator's Python as an invisible substitute when the
    # historical project plan has no runnable interpreter.  Doing so yields
    # target metadata for a runtime that cannot verify the migration.
    if plan_status != "ready":
        detail = (
            f"Cannot resolve target releases because the environment plan is {plan_status}. "
            "Install/select a compatible project Python or use the declared container backend."
        )
        resolved_versions = dict(raw_requested_versions)
        resolution_failures = dict(state.get("target_resolution_failures", {}) or {})
        releases: dict[str, dict] = dict(environment_plan.get("target_releases", {}) or {})
        for candidate in sorted(target_candidates, key=str.casefold):
            resolution_failures[candidate] = detail
            releases[candidate] = {
                "distribution": _distribution_for_import(candidate),
                "selection": "blocked_environment_plan",
                "python": target_python,
                "security_status": "not_run",
                "error": detail,
            }
        if releases:
            environment_plan["target_releases"] = releases
        console.print(f"[yellow]Candidate target resolution blocked: {detail}[/yellow]\n")
        trace.append(AgentEvent(
            stage="candidate_security", event="blocked",
            detail={"environment_plan_status": plan_status, "target_candidates": sorted(target_candidates)},
        ))
        return {
            "security_results": existing,
            "migration_target_versions": resolved_versions,
            "target_resolution_failures": resolution_failures,
            "environment_plan": environment_plan,
            "agent_trace": trace,
            "messages": [AIMessage(content="Candidate security: target resolution blocked by environment plan.")],
        }

    if not to_scan:
        console.print("[dim]All non-target candidates already scanned; no migration target requires resolution.[/dim]\n")
        trace.append(AgentEvent(
            stage="candidate_security", event="completed",
            detail={"scanned": 0, "source_aliases": source_aliases},
        ))
        return {"security_results": existing, "agent_trace": trace, "messages": []}

    new_results: dict[str, SecurityResult] = {}
    if other_to_scan:
        new_results.update(_scan_packages(other_to_scan, "Scanning candidate"))
    if target_to_scan:
        distributions = {package: _distribution_for_import(package) for package in target_to_scan}
        new_results.update(_scan_packages(
            target_to_scan,
            "Selecting and scanning compatible migration target",
            target_python=target_python,
            requested_versions=requested_map,
            distributions=distributions,
        ))
    merged = {**existing, **new_results}

    resolved_versions = dict(state.get("migration_target_versions", {}) or {})
    resolution_failures = dict(state.get("target_resolution_failures", {}) or {})
    releases: dict[str, dict] = dict(environment_plan.get("target_releases", {}) or {})
    for candidate in sorted(target_candidates):
        result = merged.get(candidate)
        if result is None:
            continue
        if result.data_status == "not_applicable" and result.version == "stdlib":
            # A standard-library target has no PyPI release to pin.  It is
            # supplied by the selected interpreter and is therefore a valid
            # migration target rather than an unresolved distribution.
            resolved_versions.pop(candidate, None)
            _clear_mapped_target_failure(resolution_failures, candidate)
            releases[candidate] = {
                "distribution": candidate,
                "selection": "stdlib",
                "python": target_python,
                "security_status": result.data_status,
                "risk_level": result.risk_level,
            }
        elif (
            result.data_status == "available"
            and result.version
            and result.version != "unknown"
            and not result.is_yanked
        ):
            resolved_versions[candidate] = result.version
            _clear_mapped_target_failure(resolution_failures, candidate)
            releases[candidate] = {
                "distribution": result.queried_package or candidate,
                "version": result.version,
                "install_spec": f"{result.queried_package or candidate}=={result.version}",
                "selection": result.version_origin,
                "python": target_python,
                "security_status": result.data_status,
                "risk_level": result.risk_level,
            }
        else:
            detail = result.error or "; ".join(result.notes) or "target release could not be resolved"
            resolution_failures[candidate] = detail
            releases[candidate] = {
                "distribution": result.queried_package or candidate,
                "selection": "unresolved",
                "python": target_python,
                "security_status": result.data_status,
                "error": detail,
            }
    if releases:
        environment_plan["target_releases"] = releases

    table = Table(title="Candidate Security Scan", header_style="bold red")
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("Risk", justify="center")
    table.add_column("Evidence", justify="center")
    table.add_column("Overall", justify="right")
    risk_color = {
        "CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "green",
        "UNKNOWN": "dim", "N/A": "dim",
    }
    for pkg in to_scan:
        res = new_results[pkg]
        color = risk_color.get(res.risk_level, "white")
        table.add_row(
            pkg, res.version, f"[{color}]{res.risk_level}[/{color}]",
            _evidence_label(res), f"[{color}]{_score_cell(res, 'overall_score')}[/{color}]",
        )
    console.print(table)
    console.print(f"\n[green]✓ Scanned {len(to_scan)} candidate package(s)[/green]\n")

    trace.append(AgentEvent(
        stage="candidate_security", event="completed",
        detail={"scanned": len(to_scan), "packages": to_scan, "source_aliases": source_aliases},
    ))

    return {
        "security_results": merged,
        "migration_target_versions": resolved_versions,
        "target_resolution_failures": resolution_failures,
        "environment_plan": environment_plan,
        "agent_trace": trace,
        "messages": [AIMessage(content=f"Candidate security: scanned {len(to_scan)} package(s)")],
    }
