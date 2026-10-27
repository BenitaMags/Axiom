"""
eval/pymigbench_eval.py
──────────────────────────
Evaluates AXIOM against PyMigBench (Islam, Jha, Nadi, Akhmetov — "PyMigBench:
A Benchmark for Python Library Migration", MSR 2023; extended in "Characterizing
Python Library Migrations", FSE 2024). This is the ground-truth dataset of real,
manually-confirmed Python library migrations mined from GitHub — 59+ analogous
library pairs (141 in v2) with real client code showing a human replacing
package A with package B.

For each PyMigBench migration we recover the client file's content *before*
the migration commit, feed it to AXIOM, and check whether AXIOM independently
arrives at the same call: "yes, package A should become package B here."

Two metrics, because they require different amounts of environment setup:

  1. DETECTION RATE (default, always run) — does resolver_agent's equivalence-
     group clustering put the ground-truth source and target library in the
     SAME group? This only needs resolver_agent's LLM call — no benchmarking,
     no package installation required. This is AXIOM's analog of the "library
     mapping" task the PyMigBench paper itself evaluates recommendation
     techniques on.

  2. FULL-PIPELINE EVALUATION (opt-in, --full-pipeline) — does the complete
     resolver -> profiler -> axiom -> connector -> verifier pipeline select
     and validate the ground-truth target? Profiling and verification run in
     a disposable historical environment. AXIOM uses only genuinely measured
     comparable dimensions: an unavailable speed, memory, or runtime probe is
     omitted from the score rather than filled with a neutral estimate. A
     selected migration is credited only after its final patch passes the
     deterministic verifier; unreproducible environments are reported
     separately rather than counted as patch failures.

Usage
──────
    # one-time: fetch the PyMigBench dataset + library
    pip install pymigbench --break-system-packages
    axiom-eval fetch-dataset ./pymigbench_data

    # detection-rate only (fast, no package installs needed)
    axiom-eval run ./pymigbench_data --repo-cache ./pymigbench_repos \\
        --llm ollama --model qwen3-coder-next --max-cases 100

    # full pipeline (slower; creates isolated profiling/verification environments)
    axiom-eval run ./pymigbench_data --repo-cache ./pymigbench_repos \\
        --full-pipeline --llm claude --model claude-sonnet-4-6

Output: a JSON report + CSV (one row per migration case) + a printed summary
table with per-domain percentage breakdowns, suitable for citing directly in
a research paper / comparison table against SWE-bench / SWE-agent-style
baselines.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import csv
import json
import math
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from rich.console import Console
from rich.table import Table

console = Console()

PYMIGBENCH_REPO_URL = "https://github.com/ualberta-smr/PyMigBench.git"
PYMIGBENCH_YAML_SUBDIR = "data/migration"   # confirmed via github.com/ualberta-smr/PyMigBench README


def _safe_session_id(migration_id: str, max_len: int = 40) -> str:
    """
    telemetry.py writes to LOGS_DIR/sessions/{session_id}.json — a raw
    slash in session_id (migration_id embeds the repo's "owner/repo" name,
    e.g. "eventlet__gevent__phuks-co/throat__...") silently breaks that
    write with "No such file or directory" since it's interpreted as a path
    separator, not a filename character. Every other character migration_id
    can contain (letters, digits, underscore, hyphen) is already filesystem-
    safe, so this only needs to handle "/" — replaced rather than stripped,
    so session IDs built from different repos can't collide onto the same
    string.
    """
    return f"eval_{migration_id}".replace("/", "-")[:max_len]


# ── Dataset acquisition ──────────────────────────────────────────────────────────

def fetch_dataset(dest: Path, ref: str = "main") -> Path:
    """Shallow-clone PyMigBench itself (the YAML metadata, not client repos)."""
    dest = Path(dest)
    if (dest / PYMIGBENCH_YAML_SUBDIR).exists():
        console.print(f"[dim]PyMigBench already present at {dest} — skipping clone.[/dim]")
        return dest / PYMIGBENCH_YAML_SUBDIR
    dest.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Cloning PyMigBench ({ref}) into {dest}...[/cyan]")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, PYMIGBENCH_REPO_URL, str(dest)],
        check=True,
    )
    yaml_root = dest / PYMIGBENCH_YAML_SUBDIR
    if not yaml_root.exists():
        raise RuntimeError(
            f"Cloned PyMigBench but {PYMIGBENCH_YAML_SUBDIR} doesn't exist — "
            f"the repo layout may have changed; check {dest} manually."
        )
    return yaml_root


def _client_repo_dir(repo_cache: Path, repo_full_name: str) -> Path:
    return repo_cache / repo_full_name.replace("/", "__")


def ensure_client_repo(repo_full_name: str, repo_cache: Path) -> Path | None:
    """
    Full-clone (not shallow — we need arbitrary historical commits, not just
    HEAD) a PyMigBench client repository, caching it so re-running the eval
    doesn't re-clone. Returns None (and prints a warning) if the clone fails
    — e.g. the repo was deleted/made private since PyMigBench was built,
    which the PyMigBench paper itself notes happens for a small fraction of
    entries. Callers should skip that case rather than fail the whole run.
    """
    repo_dir = _client_repo_dir(repo_cache, repo_full_name)
    if repo_dir.exists():
        return repo_dir
    repo_cache.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo_full_name}.git"
    console.print(f"[dim]Cloning client repo {repo_full_name}...[/dim]")
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(repo_dir)],
            check=True, timeout=300,
        )
        return repo_dir
    except Exception as e:
        console.print(f"[yellow]⚠ Could not clone {repo_full_name}: {e}[/yellow]")
        return None


def get_file_before_commit(repo_dir: Path, commit: str, path: str) -> str | None:
    """`git show {commit}^:{path}` — the client file's content immediately
    BEFORE the migration commit was applied."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{commit}^:{path}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except Exception:
        return None


def get_file_at_commit(repo_dir: Path, commit: str, path: str) -> str | None:
    """`git show {commit}:{path}` — the client file's content AT the migration
    commit, i.e. the developer's own post-migration version (File_dev in the
    PyMigBench/MigrateLib papers' notation). Used only by the benchmark-
    comparison mode (run_benchmark_comparison_case) — the other eval modes
    only ever need the pre-migration file."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except Exception:
        return None


def _commit_date(repo_dir: Path, commit: str) -> str | None:
    """Return the migration commit's author date as an ISO-8601 string.

    Used to pin otherwise-unbounded historical requirements (e.g.
    ``locustio>=1.0`` with no upper bound) to the release that was actually
    current when this migration happened, rather than whatever ``>=1.0``
    resolves to against today's PyPI index — see
    ``_pin_requirement_as_of_date``.
    """
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%aI", commit],
            cwd=repo_dir, capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            return None
        date = proc.stdout.strip()
        return date or None
    except Exception:
        return None


def _historical_project_snapshot(repo_dir: Path, commit: str) -> Path | None:
    """Materialize a cached, detached checkout at ``commit^`` for verification.

    The evaluator previously read File_pre with ``git show`` but imported
    local modules from the repository's *current HEAD*.  A detached worktree
    gives smoke tests the exact project layout and local code that existed
    before the migration without mutating the cached client clone.
    """
    parent = f"{commit}^"
    try:
        resolved = subprocess.run(
            ["git", "rev-parse", parent], cwd=repo_dir, capture_output=True,
            text=True, timeout=20, check=True,
        ).stdout.strip()
    except Exception as exc:
        console.print(f"[yellow]⚠ Cannot resolve historical commit for {repo_dir.name}: {exc}[/yellow]")
        return None

    snapshots_root = repo_dir.parent / ".axiom_historical"
    snapshot = (snapshots_root / repo_dir.name / resolved[:16]).resolve()
    if snapshot.is_dir() and (snapshot / ".git").exists():
        return snapshot
    if snapshot.exists():
        console.print(f"[yellow]⚠ Incomplete historical snapshot exists at {snapshot}; skipping this case.[/yellow]")
        return None

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]Materializing historical project snapshot {repo_dir.name}@{resolved[:12]}...[/dim]")
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(snapshot), resolved],
            cwd=repo_dir, capture_output=True, text=True, timeout=90, check=True,
        )
        return snapshot
    except Exception as exc:
        console.print(f"[yellow]⚠ Could not materialize historical snapshot for {repo_dir.name}: {exc}[/yellow]")
        return None


# Per-package PyPI release metadata, cached across every case in a run —
# many PyMigBench projects share base dependencies (e.g. requests, six).
_PYPI_RELEASE_CACHE: dict[str, dict | None] = {}


def _pin_requirement_as_of_date(spec: str, as_of_date: str) -> str:
    """Pin an unbounded/loose historical requirement to the release that was
    actually current on ``as_of_date`` (the migration commit's date).

    Problem this fixes: a project's own ``install_requires`` frequently has
    no upper bound (e.g. ``locustio>=1.0``, declared circa 2019). Installing
    that literally today resolves ``>=1.0`` against PyPI's index *now*,
    which can land on a release published years after this historical
    project was written — different module layout, removed submodules, etc.
    (this is exactly what broke the baseline import smoke test for
    ``locust_plugins/kafka.py``: ``locust.wait_time`` didn't exist yet, or
    doesn't exist anymore, in whichever release ``locustio>=1.0`` resolves
    to today). Pinning to the newest non-yanked release that existed on the
    commit date reproduces what the author actually built and tested
    against, not what an unbounded floor happens to mean years later.

    Best-effort: any pin already present (``==``) is left untouched; on any
    parse/network failure this returns ``spec`` unchanged rather than
    guessing or blocking collection — the eval already fails closed in
    ``verifier_agent`` if the resulting environment still doesn't work.
    """
    if not as_of_date:
        return spec
    try:
        requirement = Requirement(spec)
    except InvalidRequirement:
        return spec
    if any(s.operator == "==" for s in requirement.specifier):
        return spec  # already exactly pinned — nothing to do

    cache_key = requirement.name.casefold()
    data = _PYPI_RELEASE_CACHE.get(cache_key, "__unset__")
    if data == "__unset__":
        try:
            from smart_loader.agents.security_agent import PYPI_API_URL, _fetch_url
            response = _fetch_url(PYPI_API_URL.format(package=requirement.name))
            data = response.data
        except Exception:
            data = None
        _PYPI_RELEASE_CACHE[cache_key] = data
    if not data:
        return spec

    releases = data.get("releases", {}) if isinstance(data, dict) else {}
    if not isinstance(releases, dict):
        return spec

    candidates: list[tuple[Version, str]] = []
    for raw_version, files in releases.items():
        if not isinstance(files, list) or not files:
            continue
        try:
            parsed = Version(str(raw_version))
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        if requirement.specifier and not requirement.specifier.contains(parsed, prereleases=True):
            continue  # respect the project's own declared floor (e.g. >=1.0)
        # A release "exists as of" as_of_date if its earliest non-yanked
        # upload predates the cutoff. Multiple files (wheel + sdist) can have
        # slightly different timestamps; use the earliest.
        upload_times = [
            f.get("upload_time_iso_8601", "") for f in files if not f.get("yanked", False)
        ]
        upload_times = [t for t in upload_times if t]
        if not upload_times:
            continue
        if min(upload_times) > as_of_date:
            continue
        candidates.append((parsed, str(raw_version)))

    if not candidates:
        return spec
    pinned_version = max(candidates, key=lambda item: item[0])[1]
    return f"{requirement.name}=={pinned_version}"


def _requirements_at_pre_migration_commit(
    repo_dir: Path,
    commit: str,
    file_path: str = "",
    *,
    include_diagnostics: bool = False,
) -> list[str] | tuple[list[str], list[str]]:
    """Recover runtime dependency specs from the client just before migration.

    PyMigBench projects predate a single packaging convention.  A literal
    ``setup(install_requires=[...])`` parser misses both variable-backed setup
    files (the sedge case) and modern pyproject/setup.cfg projects.

    For a monorepo, only the root metadata plus metadata nearest to the file
    under migration belong to that file's runtime slice.  Reading every
    ``tools/*/requirements.txt`` incorrectly tried to build unrelated apps,
    native extensions, test suites, and Python-2-only plugins before AXIOM
    could verify one source file.  This function deliberately does *not*
    claim to reproduce an entire monorepo; it recovers the dependency scope
    relevant to the migrated component and records it as a file-level check.
    """
    recovery_errors: list[str] = []
    # The pre-migration commit's date — used to pin any requirement that has
    # no exact version (e.g. "locustio>=1.0") to the release that actually
    # existed then, rather than whatever an unbounded floor resolves to
    # against today's PyPI index. See _pin_requirement_as_of_date.
    as_of_date = _commit_date(repo_dir, f"{commit}^")
    if not as_of_date:
        recovery_errors.append(
            "Could not determine the pre-migration commit date; unbounded requirement "
            "versions will resolve against PyPI's current index rather than a historical pin."
        )

    def _finish(requirements: list[str]) -> list[str] | tuple[list[str], list[str]]:
        deduped = list(dict.fromkeys(spec for spec in requirements if spec))
        pinned = [_pin_requirement_as_of_date(spec, as_of_date or "") for spec in deduped]
        return (pinned, recovery_errors) if include_diagnostics else pinned

    parent = f"{commit}^"
    try:
        files = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", parent], cwd=repo_dir,
            capture_output=True, text=True, timeout=20,
            check=True,
        ).stdout.splitlines()
    except Exception as exc:
        recovery_errors.append(f"Cannot enumerate pre-migration dependency metadata: {exc}")
        return _finish([])
    metadata_specs_by_scope: dict[Path, list[str]] = {}
    requirements_specs_by_scope: dict[Path, list[str]] = {}
    metadata_names = {"setup.py", "setup.cfg", "pyproject.toml", "pipfile"}
    file_set = set(files)

    # Root metadata is useful for shared project dependencies.  Add only
    # ancestor directories of the migrated file that advertise their own
    # packaging metadata; sibling tools in a monorepo are intentionally out
    # of scope.  With no file path (the public helper's legacy use), root
    # metadata is the only defensible scope.
    scope_dirs: set[Path] = {Path(".")}
    if file_path:
        cursor = Path(file_path).parent
        while cursor != Path("."):
            if any((cursor / name).as_posix() in file_set for name in metadata_names):
                scope_dirs.add(cursor)
            cursor = cursor.parent

    def _belongs_to_scope(path: str, *, allow_requirements_subdir: bool = False) -> bool:
        candidate = Path(path)
        parent = candidate.parent
        for scope in scope_dirs:
            if parent == scope:
                return True
            if allow_requirements_subdir:
                requirements_dir = scope / "requirements"
                try:
                    if parent.is_relative_to(requirements_dir):
                        return True
                except ValueError:  # pragma: no cover - defensive for foreign paths
                    pass
        return False

    def _scope_for_path(path: str, *, allow_requirements_subdir: bool = False) -> Path | None:
        """Return the deepest selected component scope containing ``path``."""
        candidate = Path(path)
        parent = candidate.parent
        matches: list[Path] = []
        for scope in scope_dirs:
            if parent == scope:
                matches.append(scope)
                continue
            if allow_requirements_subdir:
                try:
                    if parent.is_relative_to(scope / "requirements"):
                        matches.append(scope)
                except ValueError:  # pragma: no cover - defensive for foreign paths
                    pass
        return max(matches, key=lambda item: len(item.parts)) if matches else None

    def _is_runtime_requirements_path(path: str) -> bool:
        """Keep production requirements, not every test/lint dependency file."""
        lowered = path.casefold().replace("\\", "/")
        candidate = Path(lowered)
        name = candidate.name
        excluded = ("dev", "test", "docs", "lint", "tox", "nox", "ci", "pre-commit")
        if any(token in name for token in excluded):
            return False
        conventional_name = (
            name in {"requirements.txt", "requirements-base.txt", "requirements-prod.txt", "requirements-production.txt"}
            or (name.startswith("requirements") and name.endswith(".txt"))
        )
        in_requirements_dir = False
        for scope in scope_dirs:
            try:
                if candidate.parent.is_relative_to(scope / "requirements") and name.endswith(".txt"):
                    in_requirements_dir = True
                    break
            except ValueError:  # pragma: no cover - defensive for foreign paths
                pass
        return _belongs_to_scope(path, allow_requirements_subdir=True) and (conventional_name or in_requirements_dir)

    def _literal_strings(node: ast.AST, names: dict[str, list[str]]) -> list[str]:
        if isinstance(node, ast.Name):
            return names.get(node.id, [])
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [element.value for element in node.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)]
        return []

    def _canonical_requirement_include(current_path: str, include: str) -> str | None:
        """Resolve a relative repository path without allowing scope escape."""
        text = include.strip()
        if not text or "://" in text or text.startswith(("/", "~")):
            return None
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(current_path), text))
        if resolved in {"", "."} or resolved.startswith("../") or resolved.startswith("/"):
            return None
        return resolved

    def _include_value(line: str, short: str, long: str) -> str | None:
        if line.startswith(f"{long}="):
            return line.split("=", 1)[1].strip()
        if line.startswith(short + " ") or line.startswith(long + " "):
            return line.split(maxsplit=1)[1].strip()
        return None

    def _read_scoped_include(
        current_path: str, include: str, scope: Path, seen: set[str], *, kind: str,
    ) -> tuple[str, str] | None:
        include_path = _canonical_requirement_include(current_path, include)
        if include_path is None:
            recovery_errors.append(
                f"Unsafe {kind} include '{include}' in {current_path}; refusing to escape the selected component scope."
            )
            return None
        include_scope = _scope_for_path(include_path, allow_requirements_subdir=True)
        if include_scope != scope or not _is_runtime_requirements_path(include_path):
            recovery_errors.append(
                f"Out-of-scope or non-runtime {kind} include '{include_path}' from {current_path}."
            )
            return None
        if include_path in seen:
            return None
        seen.add(include_path)
        included = get_file_at_commit(repo_dir, parent, include_path)
        if included is None:
            recovery_errors.append(
                f"Required dependency include '{include_path}' referenced by {current_path} is missing at the historical commit."
            )
            return None
        return include_path, included

    def _constraint_lines(text: str, current_path: str, scope: Path, seen: set[str]) -> list[str]:
        """Recover exact pins from a scoped constraints file.

        Constraints are returned as additional requirements.  Pip receives the
        combined list in one transaction, so an exact constraint narrows a
        broad project dependency instead of being silently ignored.
        """
        extracted: list[str] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            include = _include_value(line, "-c", "--constraint") or _include_value(line, "-r", "--requirement")
            if include is not None:
                resolved = _read_scoped_include(current_path, include, scope, seen, kind="constraint")
                if resolved is not None:
                    included_path, included = resolved
                    extracted.extend(_constraint_lines(included, included_path, scope, seen))
                continue
            if line.startswith(("--", "-f ", "--find-links", "-e ", ".", "/")):
                continue
            extracted.append(line)
        return extracted

    def _requirement_lines(text: str, current_path: str, scope: Path, seen: set[str]) -> list[str]:
        extracted: list[str] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            include = _include_value(line, "-r", "--requirement")
            if include is not None:
                resolved = _read_scoped_include(current_path, include, scope, seen, kind="requirement")
                if resolved is not None:
                    included_path, included = resolved
                    extracted.extend(_requirement_lines(included, included_path, scope, seen))
                continue
            constraint = _include_value(line, "-c", "--constraint")
            if constraint is not None:
                resolved = _read_scoped_include(current_path, constraint, scope, seen, kind="constraint")
                if resolved is not None:
                    included_path, included = resolved
                    extracted.extend(_constraint_lines(included, included_path, scope, seen))
                continue
            if line.startswith(("--", "-f ", "--find-links")):
                continue
            if line.startswith("-e "):
                line = line[3:].strip()
            if line and not line.startswith((".", "/")):
                extracted.append(line)
        return extracted

    def _spec_key(spec: str) -> str:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", spec)
        return re.sub(r"[-_.]+", "-", match.group(1).casefold()) if match else ""

    def _toml_dependency_spec(package: str, constraint: object, origin: str) -> str | None:
        if isinstance(constraint, str):
            return package if constraint.strip() in {"", "*"} else f"{package}{constraint.strip()}"
        if not isinstance(constraint, dict):
            recovery_errors.append(f"Unsupported dependency declaration for {package} in {origin}.")
            return None
        if any(key in constraint for key in ("path", "git", "url")):
            recovery_errors.append(f"Non-registry dependency {package} in {origin} cannot be safely replayed.")
            return None
        version = str(constraint.get("version", "")).strip()
        extras = constraint.get("extras", [])
        if extras and not isinstance(extras, list):
            recovery_errors.append(f"Unsupported extras declaration for {package} in {origin}.")
            return None
        name = package + ("[" + ",".join(str(extra) for extra in extras) + "]" if extras else "")
        marker = str(constraint.get("markers", constraint.get("marker", ""))).strip()
        spec = name if version in {"", "*"} else f"{name}{version}"
        return f"{spec}; {marker}" if marker else spec

    for path in files:
        name = Path(path).name.lower()
        if not (
            (name.startswith("requirements") and name.endswith(".txt"))
            or name in {"setup.py", "setup.cfg", "pyproject.toml", "pipfile"}
        ):
            continue
        if name in metadata_names and not _belongs_to_scope(path):
            continue
        if name.endswith(".txt") and not _is_runtime_requirements_path(path):
            continue
        text = get_file_at_commit(repo_dir, parent, path)
        if not text:
            recovery_errors.append(f"Could not read declared dependency metadata '{path}' at the historical commit.")
            continue
        if name.endswith(".txt"):
            scope = _scope_for_path(path, allow_requirements_subdir=True)
            if scope is not None:
                requirements_specs_by_scope.setdefault(scope, []).extend(
                    _requirement_lines(text, path, scope, {path})
                )
        elif name == "setup.py":
            scope = _scope_for_path(path)
            if scope is None:
                continue
            destination = metadata_specs_by_scope.setdefault(scope, [])
            try:
                tree = ast.parse(text)
                names: dict[str, list[str]] = {}
                for node in tree.body:
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        value = _literal_strings(node.value, names) if node.value else []
                        for target in targets:
                            if isinstance(target, ast.Name) and value:
                                names[target.id] = value
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setup"):
                        continue
                    for keyword in node.keywords:
                        if keyword.arg == "install_requires":
                            destination.extend(_literal_strings(keyword.value, names))
            except SyntaxError as exc:
                recovery_errors.append(f"Cannot parse setup.py dependency metadata '{path}': {exc}")
        elif name == "setup.cfg":
            scope = _scope_for_path(path)
            if scope is None:
                continue
            destination = metadata_specs_by_scope.setdefault(scope, [])
            parser = configparser.ConfigParser()
            try:
                parser.read_string(text)
                destination.extend(
                    line.strip() for line in parser.get("options", "install_requires", fallback="").splitlines()
                    if line.strip()
                )
            except configparser.Error as exc:
                recovery_errors.append(f"Cannot parse setup.cfg dependency metadata '{path}': {exc}")
        else:  # pyproject.toml / Pipfile
            scope = _scope_for_path(path)
            if scope is None:
                continue
            destination = metadata_specs_by_scope.setdefault(scope, [])
            try:
                try:
                    import tomllib  # Python 3.11+
                except ModuleNotFoundError:  # pragma: no cover - Python 3.10 support
                    import tomli as tomllib  # type: ignore[no-redef]
                data = tomllib.loads(text)
                if name == "pyproject.toml":
                    destination.extend(data.get("project", {}).get("dependencies", []) or [])
                    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
                    for package, constraint in poetry.items():
                        if package.casefold() == "python":
                            continue
                        spec = _toml_dependency_spec(package, constraint, path)
                        if spec:
                            destination.append(spec)
                else:
                    packages = data.get("packages", {}) or {}
                    for package, constraint in packages.items():
                        spec = _toml_dependency_spec(package, constraint, path)
                        if spec:
                            destination.append(spec)
            except Exception as exc:
                recovery_errors.append(f"Cannot parse TOML dependency metadata '{path}': {exc}")

    # Packaging metadata provides declared dependencies while scoped runtime
    # requirement files usually carry the historical pins.  Merge both per
    # component, letting a requirements entry for the same distribution
    # override the broad metadata declaration rather than discarding either
    # source wholesale.
    selected_specs: list[str] = []
    for scope in sorted(scope_dirs, key=lambda item: (len(item.parts), item.as_posix())):
        metadata_specs = metadata_specs_by_scope.get(scope, [])
        requirement_specs = requirements_specs_by_scope.get(scope, [])
        requirement_keys = {_spec_key(spec) for spec in requirement_specs}
        selected_specs.extend(spec for spec in metadata_specs if _spec_key(spec) not in requirement_keys)
        selected_specs.extend(requirement_specs)
    return _finish(selected_specs)


# ── PyPI package name -> import module name normalization ───────────────────────
# PyMigBench's source/target fields are PyPI distribution names, but the code
# imports the *module* name, and these frequently differ (cheetah3 -> Cheetah,
# PyYAML -> yaml, beautifulsoup4 -> bs4, ...). This is the same package-name-vs-
# import-name gap every migration tool has to deal with; importlib.metadata can
# resolve it when the package is actually installed (see rules_engine.py's
# is_installed check elsewhere in AXIOM), but eval cases are run against
# uninstalled historical code, so we fall back to a small curated alias table
# plus case-insensitive matching. Cases that still don't match are honestly
# reported as errors ("source package not actually imported") rather than
# silently dropped or force-matched — undercounting detection rate is safer
# for a research metric than overcounting it.
_PYPI_TO_IMPORT_ALIASES: dict[str, str] = {
    "cheetah3": "cheetah", "pyyaml": "yaml", "beautifulsoup4": "bs4",
    "pillow": "pil", "python-dateutil": "dateutil", "protobuf": "google",
    "python-docx": "docx", "python-pptx": "pptx", "opencv-python": "cv2",
    "scikit-learn": "sklearn", "scikit-image": "skimage", "msgpack-python": "msgpack",
    "attrs": "attr", "django-rest-framework": "rest_framework",
    # Added after the first live run against real PyMigBench cases surfaced
    # them as false "not imported" skips:
    "pycrypto": "Crypto", "pycryptodome": "Crypto",   # both provide the `Crypto` namespace
    "slackclient": "slack",                            # legacy slackclient SDK imports as `slack`
    "leveldb": "leveldb", "plyvel": "plyvel",           # no-op entries, kept explicit for clarity
    "ruamel.yaml": "ruamel",                             # namespace package — imports as `ruamel.yaml`,
                                                          # top-level segment is `ruamel`
}


def _import_name_matches(module_first_segment: str, pypi_name: str) -> bool:
    seg = module_first_segment.casefold()
    pypi = pypi_name.casefold()
    if seg == pypi:
        return True
    alias = _PYPI_TO_IMPORT_ALIASES.get(pypi)
    if alias and alias.casefold() == seg:
        return True
    # normalize separators: some packages differ only by - vs _ vs .
    # (the last matters for namespace packages like ruamel.yaml, whose
    # PyPI name literally contains the dot that's also its import path
    # separator — without stripping it here, "ruamel" (the actual
    # top-level import segment) would never match "ruamel.yaml" (the
    # PyPI name) even via the fallback path, only via an explicit alias)
    return seg.replace("_", "") == pypi.replace("-", "").replace("_", "").replace(".", "")


def _import_name_for_distribution(distribution: str) -> str:
    """Best known Python import root for a PyPI distribution name."""
    return _PYPI_TO_IMPORT_ALIASES.get(distribution.casefold(), distribution.replace("-", "_"))


def _source_import_for_case(case: "EvalCase") -> str:
    """Preserve the exact import spelling used by the historical file.

    Canonical PyPI names are case-insensitive while Python import roots are
    not (``pycryptodome`` provides ``Crypto``).  The parser's AST is the
    source of truth for connector call-site matching.
    """
    try:
        tree = ast.parse(case.before_code)
    except SyntaxError:
        return _import_name_for_distribution(case.source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if _import_name_matches(root, case.source):
                    return root
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if _import_name_matches(root, case.source):
                return root
    return _import_name_for_distribution(case.source)


def _verification_import_paths(case: "EvalCase") -> list[str]:
    """Expose the historical project's local modules to verifier subprocesses."""
    if not case.project_root:
        return []
    root = Path(case.project_root)
    source_parent = root / Path(case.file_path).parent
    paths = [root / "src", root, source_parent]
    return list(dict.fromkeys(str(path) for path in paths if path.is_dir()))


def _verification_package(case: "EvalCase") -> str:
    """Return the package needed to execute this file's relative imports."""
    try:
        tree = ast.parse(case.before_code)
    except SyntaxError:
        return ""
    if not any(isinstance(node, ast.ImportFrom) and node.level > 0 for node in ast.walk(tree)):
        return ""
    root = Path(case.project_root)
    parent = root / Path(case.file_path).parent
    package_parts: list[str] = []
    while parent != root.parent and (parent / "__init__.py").is_file():
        package_parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(package_parts))


_install_attempted: set[str] = set()   # module-level — avoid re-attempting the same package
                                         # across every case in a batch run that happens to
                                         # share it (e.g. "gevent" and "eventlet" both appear
                                         # as source AND target across several PyMigBench pairs)


def ensure_pip_installed(package: str, timeout: int = 90) -> None:
    """
    Best-effort `pip install <package>` for the seeded pipeline
    (run_pipeline_from_known_pair) — without this, profiler_agent correctly
    (and silently, from the eval's perspective) reports almost every
    PyMigBench package pair as unavailable, since none of them are
    installed in a fresh environment by default. This is what
    --auto-install turns on. Failures are swallowed on purpose: some
    packages need system libraries (e.g. confluent-kafka needs librdkafka)
    and won't build in every environment — profiler_agent will just report
    those as unavailable same as before, which is the correct honest
    outcome for a package that genuinely can't be verified here.
    """
    if package in _install_attempted:
        return
    _install_attempted.add(package)
    try:
        console.print(f"[dim]  pip install {package}...[/dim]")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", package],
            timeout=timeout, capture_output=True, text=True,
        )
        if proc.returncode != 0 and "externally-managed-environment" in (proc.stderr or ""):
            # PEP 668 environments (common on recent Debian/Ubuntu) refuse a
            # plain pip install — retry with the explicit override flag.
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", package],
                timeout=timeout, capture_output=True,
            )
    except Exception:
        pass


# ── Eval case construction ───────────────────────────────────────────────────────

@dataclass
class CCRecord:
    """
    A snapshot of one PyMigBench CodeChange, detached from the live
    pymigbench.database object graph (which holds back-references to
    .file/.mig we don't need to carry around). line_expr is the raw
    "source:target" notation from the paper (e.g. "15:15-16") — source is
    the line range in the PRE-migration file, target is the line range in
    the DEV (post-migration) file, per pymigbench.line_replacement.
    """
    line_expr:        str
    target_line_nums: list[int]     # 1-based line numbers in dev_code this change produced
    source_line_nums: list[int]     # 1-based line numbers in before_code this change replaced
    source_apis:      list[str]
    target_apis:      list[str]
    is_removal_only:  bool          # target is empty — source API deleted, nothing added


@dataclass
class EvalCase:
    migration_id: str
    repo:         str
    domain:       str
    source:       str
    target:       str
    file_path:    str
    before_code:  str
    base_requirements: list[str] = field(default_factory=list)
    # Any dependency-recovery ambiguity blocks isolated verification rather
    # than silently claiming a partial environment is the historical project.
    base_requirement_recovery_errors: list[str] = field(default_factory=list)
    project_root: str = ""  # detached historical client snapshot; used by isolated verification
    dev_code:      str | None = None          # only populated by build_benchmark_cases
    code_changes:  list[CCRecord] | None = None  # only populated by build_benchmark_cases


def build_eval_cases(
    yaml_root: Path,
    repo_cache: Path,
    max_cases: int | None = None,
    case_offset: int = 0,
    case_filter: str | None = None,
    collection_skips: list[dict[str, str]] | None = None,
) -> list[EvalCase]:
    """
    Walk every PyMigBench Migration -> MigrationFile, recover the file's
    pre-migration content via git, and produce one EvalCase per (migration,
    file) pair that has real code changes (skips migrations that only touched
    a dependency file with no client code change, which PyMigBench also
    tracks but which AXIOM has nothing to analyze for).
    """
    from pymigbench.database import Database

    db = Database.load_from_dir(yaml_root)
    migs = db.migs()
    console.print(f"[cyan]Loaded {len(migs)} migrations from PyMigBench.[/cyan]")

    if case_offset < 0:
        raise ValueError("case_offset must be zero or greater")

    cases: list[EvalCase] = []
    eligible_cases_skipped = 0
    seen_repos: dict[str, Path | None] = {}
    requirements_cache: dict[tuple[str, str, str], tuple[list[str], list[str]]] = {}
    snapshot_cache: dict[tuple[str, str], Path | None] = {}
    normalized_filter = case_filter.casefold() if case_filter else ""

    if case_offset:
        console.print(f"[dim]Skipping the first {case_offset} eligible case(s) due to --case-offset.[/dim]")

    for mig in migs:
        if not mig.files:
            continue
        for f in mig.files:
            if not f.code_changes:
                continue
            case_identity = " ".join((mig.id(), mig.repo, mig.source, mig.target, f.path)).casefold()
            if normalized_filter and normalized_filter not in case_identity:
                continue
            if mig.repo not in seen_repos:
                seen_repos[mig.repo] = ensure_client_repo(mig.repo, repo_cache)
            repo_dir = seen_repos[mig.repo]
            if repo_dir is None:
                if collection_skips is not None:
                    collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                             "reason": "client_repository_unavailable"})
                continue
            snapshot_key = (mig.repo, mig.commit)
            if snapshot_key not in snapshot_cache:
                snapshot_cache[snapshot_key] = _historical_project_snapshot(repo_dir, mig.commit)
            project_root = snapshot_cache[snapshot_key]
            if project_root is None:
                if collection_skips is not None:
                    collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                             "reason": "historical_snapshot_unavailable"})
                continue
            before = get_file_before_commit(repo_dir, mig.commit, f.path)
            if not before or not before.strip():
                if collection_skips is not None:
                    collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                             "reason": "pre_migration_source_unavailable"})
                continue
            if eligible_cases_skipped < case_offset:
                eligible_cases_skipped += 1
                continue
            req_key = (mig.repo, mig.commit, f.path)
            requirements_cache.setdefault(
                req_key, _requirements_at_pre_migration_commit(
                    repo_dir, mig.commit, f.path, include_diagnostics=True,
                ),
            )
            cases.append(EvalCase(
                migration_id=mig.id(), repo=mig.repo, domain=mig.domain,
                source=mig.source, target=mig.target, file_path=f.path,
                before_code=before, base_requirements=requirements_cache[req_key][0],
                base_requirement_recovery_errors=requirements_cache[req_key][1],
                project_root=str(project_root),
            ))
            if max_cases and len(cases) >= max_cases:
                console.print(f"[dim]Reached --max-cases={max_cases}, stopping collection.[/dim]")
                return cases

    return cases


def build_benchmark_cases(
    yaml_root: Path,
    repo_cache: Path,
    max_cases: int | None = None,
    case_offset: int = 0,
    case_filter: str | None = None,
    collection_skips: list[dict[str, str]] | None = None,
    *,
    code_only: bool = False,
) -> list[EvalCase]:
    """
    Like build_eval_cases, but ALSO recovers each file's developer-migrated
    content (File_dev, via `git show {commit}:{path}`) and PyMigBench's own
    recorded code_changes with their exact line ranges — everything
    run_benchmark_comparison_case() needs to replicate the evaluation
    methodology of "Using LLMs for Library Migration" (Islam et al.) /
    MigrateLib (same authors): migrate File_pre with AXIOM, then compare
    the result against File_dev the same way those papers compare their
    LLM's output against the developer's actual migration.

    ``code_only`` intentionally avoids materialising a historical worktree
    and recovering its full dependency graph.  A developer-diff benchmark
    needs only Git's File_pre/File_dev content plus PyMigBench's labeled
    changes.  Requiring a 2015 project's Python, database headers, or
    unrelated application services would turn a code-synthesis benchmark
    into an environment-reproduction benchmark and hide the metric it is
    meant to measure.  Strict project-runtime evaluation remains available
    through the full-pipeline path.
    """
    from pymigbench.database import Database

    db = Database.load_from_dir(yaml_root)
    migs = db.migs()
    console.print(f"[cyan]Loaded {len(migs)} migrations from PyMigBench.[/cyan]")

    if case_offset < 0:
        raise ValueError("case_offset must be zero or greater")

    cases: list[EvalCase] = []
    eligible_cases_skipped = 0
    seen_repos: dict[str, Path | None] = {}
    requirements_cache: dict[tuple[str, str, str], tuple[list[str], list[str]]] = {}
    snapshot_cache: dict[tuple[str, str], Path | None] = {}
    normalized_filter = case_filter.casefold() if case_filter else ""

    if case_offset:
        console.print(f"[dim]Skipping the first {case_offset} eligible case(s) due to --case-offset.[/dim]")

    for mig in migs:
        if not mig.files:
            continue
        for f in mig.files:
            if not f.code_changes:
                continue
            case_identity = " ".join((mig.id(), mig.repo, mig.source, mig.target, f.path)).casefold()
            if normalized_filter and normalized_filter not in case_identity:
                continue
            if mig.repo not in seen_repos:
                seen_repos[mig.repo] = ensure_client_repo(mig.repo, repo_cache)
            repo_dir = seen_repos[mig.repo]
            if repo_dir is None:
                if collection_skips is not None:
                    collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                             "reason": "client_repository_unavailable"})
                continue
            project_root: Path | None = None
            if not code_only:
                snapshot_key = (mig.repo, mig.commit)
                if snapshot_key not in snapshot_cache:
                    snapshot_cache[snapshot_key] = _historical_project_snapshot(repo_dir, mig.commit)
                project_root = snapshot_cache[snapshot_key]
                if project_root is None:
                    if collection_skips is not None:
                        collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                                 "reason": "historical_snapshot_unavailable"})
                    continue

            before = get_file_before_commit(repo_dir, mig.commit, f.path)
            dev = get_file_at_commit(repo_dir, mig.commit, f.path)
            if not before or not before.strip() or not dev or not dev.strip():
                if collection_skips is not None:
                    collection_skips.append({"migration_id": mig.id(), "repo": mig.repo, "file_path": f.path,
                                             "reason": "pre_or_post_migration_source_unavailable"})
                continue

            cc_records = []
            for cc in f.code_changes:
                cc_records.append(CCRecord(
                    line_expr=str(cc.line),
                    target_line_nums=sorted(cc.line.target.lines),
                    source_line_nums=sorted(cc.line.source.lines),
                    source_apis=list(cc.source_apis),
                    target_apis=list(cc.target_apis),
                    is_removal_only=cc.is_removal_only(),
                ))
            if not cc_records:
                continue

            if eligible_cases_skipped < case_offset:
                eligible_cases_skipped += 1
                continue

            base_requirements: list[str] = []
            base_requirement_recovery_errors: list[str] = []
            if not code_only:
                req_key = (mig.repo, mig.commit, f.path)
                requirements_cache.setdefault(
                    req_key, _requirements_at_pre_migration_commit(
                        repo_dir, mig.commit, f.path, include_diagnostics=True,
                    ),
                )
                base_requirements, base_requirement_recovery_errors = requirements_cache[req_key]
            cases.append(EvalCase(
                migration_id=mig.id(), repo=mig.repo, domain=mig.domain,
                source=mig.source, target=mig.target, file_path=f.path,
                before_code=before, base_requirements=base_requirements,
                base_requirement_recovery_errors=base_requirement_recovery_errors,
                project_root=str(project_root or ""),
                dev_code=dev, code_changes=cc_records,
            ))
            if max_cases and len(cases) >= max_cases:
                console.print(f"[dim]Reached --max-cases={max_cases}, stopping collection.[/dim]")
                return cases

    return cases


# ── Running AXIOM against a case ─────────────────────────────────────────────────

@dataclass
class EvalResult:
    case:                   EvalCase
    detected_group:         bool | None = None   # None = resolver step errored
    # Whether AXIOM's first source-aligned selection was PyMigBench's target.
    # Kept as ``recommended_target`` for report compatibility; the final
    # accepted/reverted state is reported separately below.
    recommended_target:     bool | None = None
    scoreable_full_pipeline: bool = False
    axiom_winner:           str | None = None     # final selection after verifier/retry handling
    attempted_axiom_winner: str | None = None     # first AXIOM selection before verifier mutation
    attempted_selection_status: str = "no_decision"
    attempted_selection_count: int = 0
    connector_ran:          bool = False
    # Monotonic evidence that Connector was reached at least once.  The final
    # graph state may clear ``connectors`` when AXIOM retries a decision, so a
    # snapshot of that final dict alone is not reliable evaluation telemetry.
    connector_ever_ran:     bool = False
    # The terminal verifier route.  It is preserved separately from the
    # per-cycle history below so consumers do not have to infer it from the
    # last list item (which may be absent when verification passed first try).
    verifier_route:         str | None = None    # "proceed" | "axiom" | "connector" | None (verifier didn't run)
    repair_cycle_count:     int = 0              # globally bounded verifier -> repair-agent cycles
    repair_history:         list[dict] = field(default_factory=list)
    final_verifier_feedback: dict = field(default_factory=dict)
    # Per-package profiler evidence, sanitized for reports.  This includes
    # actual runtime-probe provenance/statuses, never profiler source or a
    # synthetic timing.
    profiler_evidence:      dict[str, dict] = field(default_factory=dict)
    # Compact evidence from the side-effect-free historical-environment
    # planner.  This records the interpreter AXIOM actually selected (or why
    # no compatible local interpreter exists) without copying raw project
    # metadata or pretending that an arbitrary host Python is compatible.
    environment_plan:       dict[str, object] = field(default_factory=dict)
    verifier_passed_clean:  bool | None = None    # True = passed differential Stage 1/2; no new blocking findings
    # Explicit evidence that the ground-truth target was rejected as a
    # structural incompatibility.  This is not used for missing environments
    # or ordinary repair-budget exhaustion.
    target_infeasibility_evidence: list[dict] = field(default_factory=list)
    patched_code:           str | None = None     # AXIOM's final File_llm — only populated by
                                                    # run_pipeline_from_known_pair, used by
                                                    # run_benchmark_comparison_case
    patch_changed:          bool = False
    verification_status:    str = "not_run"       # verified | rejected | environment_incomplete | not_applicable | not_run
    verification_environment_status: str = "unknown"
    selection_status:       str = "no_decision"   # exact_target | alternative | incidental | no_decision
    outcome:                str = "not_run"       # terminal, mutually exclusive evaluation outcome
    verified_ground_truth:  bool = False
    verified_alternative:   bool = False
    # Four publication-facing groups.  The detailed ``outcome`` above remains
    # intact so a reader can distinguish, for example, a resolver miss from a
    # historical-environment block.
    result_category:        str = "not_verified_or_not_adjudicated"
    category_reason:        str = ""
    # A verified alternative is never called more efficient unless real,
    # comparable profiler evidence supports that claim.  Connector wrapper
    # timing is intentionally separate: it measures generated application
    # code, not the selected package's API.
    alternative_efficiency: dict[str, object] = field(default_factory=dict)
    adapter_runtime: list[dict] = field(default_factory=list)
    repair_budget_exhausted: bool = False
    error:                  str | None = None

    def __post_init__(self) -> None:
        """Give early pipeline-return results the same honest category fields.

        Most full-pipeline results are populated by ``_result_from_state``.
        Parser/provider early exits instantiate ``EvalResult`` directly, so
        normalize those too rather than leaving a blank category reason in the
        CSV/JSON report.
        """
        if self.category_reason:
            return
        exhausted = self.repair_budget_exhausted or _repair_budget_exhausted(self.repair_history)
        category, reason = _result_category(
            self.outcome,
            alternative_efficiency=self.alternative_efficiency,
            repair_budget_exhausted=exhausted,
        )
        self.result_category = category
        self.category_reason = reason
        self.repair_budget_exhausted = exhausted


def _build_case_environment_plan(case: EvalCase) -> dict:
    """Build a no-network, no-install environment plan for one PyMigBench case.

    ``project_root`` is a detached pre-migration worktree recovered during
    collection.  Its existing requirements are authoritative for the base
    layer; the migration target stays explicitly ``auto`` because planning
    itself must not fetch or invent release metadata.  When security/version
    resolution is enabled, Candidate Security later selects and records an
    exact compatible non-yanked target release.  Without that evidence, an
    isolated pipeline fails closed rather than letting pip choose an
    unpinned current release.
    """
    root = str(case.project_root or "").strip()
    if not root:
        # This should not occur for a collected PyMigBench case, but it must
        # fail closed rather than silently planning against AXIOM's checkout.
        return {
            "project_root": "",
            "requested_python": "auto",
            "detected_python": "",
            "python_constraint": "",
            "selected_python": "",
            "selected_python_version": "",
            "interpreter_status": "interpreter_unavailable",
            "environment_backend": "unavailable",
            "requirement_files": [],
            "requirements": list(case.base_requirements),
            "target_distribution": case.target,
            "target_version": "auto",
            "target_requirement": case.target,
            "python_evidence": [],
            "recovery_hints": [],
            "warnings": ["No historical project snapshot is available for this PyMigBench case."],
            "usable": False,
        }

    try:
        from smart_loader.core.environment_plan import plan_project_environment

        return plan_project_environment(
            root,
            # Use the dependency set recovered from the historical commit,
            # not whatever happens to be declared in the checkout today.
            base_requirements=case.base_requirements,
            target_distribution=case.target,
            # Do not look up or fabricate a target pin here.  Candidate
            # Security resolves this explicit ``auto`` policy later, against
            # the selected interpreter, and the isolated pipeline blocks if
            # it cannot obtain an exact release.
            target_version="auto",
            # PyMigBench cases routinely declare Python versions this host
            # never happened to have installed (e.g. 3.5, 3.6). Previously
            # that was a dead end reported only as "Docker is available for
            # an isolated fallback" with no code anywhere that acted on it —
            # the single largest contributor to environment_incomplete
            # outcomes. Opt in here (the eval harness, unlike a plain
            # library call to plan_project_environment, is expected to take
            # this kind of best-effort recovery action) so a missing
            # historical interpreter is installed via uv when possible
            # instead of immediately failing the case closed.
            auto_provision_interpreter=True,
        ).to_dict()
    except Exception as exc:
        # Planning itself must be evidence-bearing: an unexpected local
        # interpreter/metadata failure is an unavailable environment, never a
        # reason to fall back to the evaluator's Python silently.
        return {
            "project_root": root,
            "requested_python": "auto",
            "detected_python": "",
            "python_constraint": "",
            "selected_python": "",
            "selected_python_version": "",
            "interpreter_status": "interpreter_unavailable",
            "environment_backend": "unavailable",
            "requirement_files": [],
            "requirements": list(case.base_requirements),
            "target_distribution": case.target,
            "target_version": "auto",
            "target_requirement": case.target,
            "python_evidence": [],
            "recovery_hints": [],
            "warnings": [f"Environment planning failed: {type(exc).__name__}: {exc}"],
            "usable": False,
        }


def _environment_pipeline_inputs(plan: object, target: str) -> dict[str, object]:
    """Return the exact plan inputs accepted by normal and seeded pipelines."""
    data = dict(plan) if isinstance(plan, dict) else {}
    target_version = str(data.get("target_version", "auto") or "auto").strip() or "auto"
    selected_python = str(data.get("selected_python", "") or "").strip()
    return {
        "environment_plan": data,
        "environment_python": selected_python,
        "migration_target": target,
        "migration_target_version": target_version,
        "migration_target_versions": {target: target_version} if target else {},
    }


def _compact_environment_plan(raw_plan: object) -> dict[str, object]:
    """Serialize plan status and diagnostics without duplicating requirements."""
    plan = raw_plan if isinstance(raw_plan, dict) else {}

    def text(key: str, limit: int = 500) -> str | None:
        return _compact_text(plan.get(key), limit)

    def text_list(key: str, limit: int = 500) -> list[str]:
        raw = plan.get(key, [])
        return [item for value in raw[:20] if (item := _compact_text(value, limit))] if isinstance(raw, list) else []

    requirements = plan.get("requirements", [])
    requirement_files = plan.get("requirement_files", [])
    target_distribution = text("target_distribution", 160)
    target_key = re.sub(r"[-_.]+", "-", str(target_distribution or "").casefold())
    target_releases = plan.get("target_releases", {})
    resolved_target_version: str | None = None
    if isinstance(target_releases, dict) and target_key:
        for name, release in target_releases.items():
            if not isinstance(release, dict):
                continue
            distribution = str(release.get("distribution", name))
            if re.sub(r"[-_.]+", "-", distribution.casefold()) != target_key and \
               re.sub(r"[-_.]+", "-", str(name).casefold()) != target_key:
                continue
            version = _compact_text(release.get("version"), 120)
            if version:
                resolved_target_version = version
                break
    return {
        "interpreter_status": text("interpreter_status", 80) or "not_planned",
        "environment_backend": text("environment_backend", 80),
        "requested_python": text("requested_python", 240),
        "detected_python": text("detected_python", 40),
        "python_constraint": text("python_constraint", 240),
        "selected_python": text("selected_python", 500),
        "selected_python_version": text("selected_python_version", 40),
        "target_distribution": target_distribution,
        "target_version": text("target_version", 120),
        # Preserve ``target_version='auto'`` as the planning policy and
        # expose the exact later Security-selected release separately.  A
        # report must not make a resolved target look unpinned.
        "resolved_target_version": resolved_target_version,
        "target_requirement": text("target_requirement", 240),
        "requirement_count": len(requirements) if isinstance(requirements, list) else 0,
        "requirement_file_count": len(requirement_files) if isinstance(requirement_files, list) else 0,
        "python_evidence": text_list("python_evidence"),
        "warnings": text_list("warnings"),
        "recovery_hints": text_list("recovery_hints"),
        "target_releases": {
            str(name): {
                key: _compact_text(value, 240) if isinstance(value, str) else value
                for key, value in release.items()
                if key in {"distribution", "version", "install_spec", "selection", "python", "security_status", "risk_level", "error"}
            }
            for name, release in (plan.get("target_releases", {}) or {}).items()
            if isinstance(release, dict)
        } if isinstance(plan.get("target_releases", {}), dict) else {},
    }


def _same_distribution(actual: str, expected: str) -> bool:
    return actual.casefold() == expected.casefold() or _import_name_matches(actual, expected)


def _decision_value(decision: object, key: str, default: object = "") -> object:
    """Read a field from a live decision or an immutable evaluation snapshot."""
    return decision.get(key, default) if isinstance(decision, dict) else getattr(decision, key, default)


def _compact_text(value: object, limit: int = 500) -> str | None:
    """Keep report evidence readable without serializing a raw command/code blob."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _real_number(value: object) -> float | None:
    """Return a finite non-negative measurement, otherwise make absence explicit."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if numeric >= 0 and math.isfinite(numeric) else None


def _compact_runtime_probe(probe_key: str, raw_probe: object) -> dict:
    """Serialize only runtime result/provenance fields safe for a JSON report."""
    probe = raw_probe if isinstance(raw_probe, dict) else {}
    used_apis = probe.get("used_apis", [])
    return {
        "status": _compact_text(probe.get("status", "unknown"), 80) or "unknown",
        "probe_id": _compact_text(probe.get("probe_id") or probe_key, 120),
        "kind": _compact_text(probe.get("kind"), 120),
        "distribution": _compact_text(probe.get("distribution"), 160),
        "module": _compact_text(probe.get("module"), 160),
        "median_ms": _real_number(probe.get("median_ms")),
        "p95_ms": _real_number(probe.get("p95_ms")),
        "runs": _real_number(probe.get("runs")),
        "warmups": _real_number(probe.get("warmups")),
        # The probe fixture itself is intentionally not emitted.  Its hash
        # makes a measured result reproducible without leaking/duplicating
        # executable setup or input text into every benchmark report.
        "fixture_sha256": _compact_text(probe.get("fixture_sha256"), 128),
        "reason": _compact_text(probe.get("reason") or probe.get("error")),
        "used_apis": [str(api) for api in used_apis[:20]] if isinstance(used_apis, list) else [],
    }


def _compact_profiler_evidence(raw_benchmarks: object) -> dict[str, dict]:
    """Produce compact, provenance-rich profiler evidence for one eval case.

    The profiler's objects may contain implementation details in future
    releases.  Reports deliberately whitelist import/memory measurements and
    runtime result/provenance fields instead of serializing ``__dict__``.
    Therefore a missing field stays ``null`` rather than becoming a fallback
    score or fabricated runtime number.
    """
    if not isinstance(raw_benchmarks, dict):
        return {}
    compact: dict[str, dict] = {}
    for package, benchmark in sorted(raw_benchmarks.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(package, str):
            continue
        available = bool(_decision_value(benchmark, "available", False))
        raw_probes = _decision_value(benchmark, "runtime_probes", {})
        probes = {
            str(probe_key): _compact_runtime_probe(str(probe_key), raw_probe)
            for probe_key, raw_probe in sorted(raw_probes.items(), key=lambda item: str(item[0]))
        } if isinstance(raw_probes, dict) else {}
        measured_probe_ids = [
            probe_key for probe_key, probe in probes.items()
            if probe.get("status") == "measured" and probe.get("median_ms") is not None
        ]
        runtime_ms = _real_number(_decision_value(benchmark, "runtime_ms", None))
        # ``runtime_ms=0`` is the profiler's explicit unmeasured sentinel;
        # only surface a selected timing if a recorded probe substantiates it.
        selected_runtime_ms = runtime_ms if measured_probe_ids and runtime_ms and runtime_ms > 0 else None
        import_time = _real_number(_decision_value(benchmark, "import_time_ms", None))
        memory_kb = _real_number(_decision_value(benchmark, "memory_kb", None))
        compact[package] = {
            "available": available,
            "profile_status": _compact_text(_decision_value(benchmark, "profile_status", "unattempted"), 120),
            "import_time_ms": import_time if available else None,
            "memory_kb": memory_kb if available else None,
            "profile_detail": _compact_text(_decision_value(benchmark, "error", None)),
            "runtime": {
                "measured": bool(measured_probe_ids),
                "selected_probe": (
                    _compact_text(_decision_value(benchmark, "runtime_api", ""), 160)
                    if measured_probe_ids else None
                ),
                "probe_kind": (
                    _compact_text(_decision_value(benchmark, "runtime_probe_kind", ""), 120)
                    if measured_probe_ids else None
                ),
                "median_ms": selected_runtime_ms,
                "detail": _compact_text(_decision_value(benchmark, "runtime_probe_detail", "")),
                "measured_probe_ids": measured_probe_ids,
            },
            "runtime_probes": probes,
        }
    return compact


def _runtime_evidence_stats(profiler_evidence: dict[str, dict]) -> dict[str, object]:
    """Summarize recorded—not inferred—runtime coverage for CSV/aggregates."""
    package_count = len(profiler_evidence)
    measured_packages = 0
    measured_probes = 0
    status_counts: dict[str, int] = {}
    for evidence in profiler_evidence.values():
        runtime = evidence.get("runtime", {}) if isinstance(evidence, dict) else {}
        if isinstance(runtime, dict) and runtime.get("measured"):
            measured_packages += 1
        probes = evidence.get("runtime_probes", {}) if isinstance(evidence, dict) else {}
        if not isinstance(probes, dict):
            continue
        for probe in probes.values():
            status = probe.get("status", "unknown") if isinstance(probe, dict) else "unknown"
            status = str(status)
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "measured":
                measured_probes += 1
    return {
        "package_count": package_count,
        "measured_package_count": measured_packages,
        "measurement_coverage_pct": (
            round(100 * measured_packages / package_count, 2) if package_count else None
        ),
        "measured_probe_count": measured_probes,
        "probe_status_counts": status_counts,
    }


def _profile_for_distribution(profiler_evidence: dict[str, dict], distribution: str) -> dict | None:
    """Find one compact profile using distribution/import-name equivalence."""
    for package, evidence in profiler_evidence.items():
        if _same_distribution(str(package), distribution) and isinstance(evidence, dict):
            return evidence
    return None


def _measured_probe_values(profile: dict | None) -> dict[str, float]:
    """Return only recorded runtime medians with an explicit measured status."""
    probes = profile.get("runtime_probes", {}) if isinstance(profile, dict) else {}
    if not isinstance(probes, dict):
        return {}
    values: dict[str, float] = {}
    for probe_id, probe in probes.items():
        if not isinstance(probe, dict) or probe.get("status") != "measured":
            continue
        median = _real_number(probe.get("median_ms"))
        if median is not None and median > 0:
            values[str(probe_id)] = median
    return values


def _patched_target_api_invocation(patched_code: object, decision: object) -> dict[str, object]:
    """Prove that a final patch executes a selected target API, not only imports it."""
    winner = str(_decision_value(decision, "winner", "")).strip()
    target_import = str(_decision_value(decision, "winner_import", winner.replace("-", "_"))).strip()
    record: dict[str, object] = {
        "target_distribution": winner or None,
        "target_import": target_import or None,
        "invoked": False,
        "calls": [],
        "reason": "",
    }
    if not winner or not target_import or not isinstance(patched_code, str):
        record["reason"] = "The final selected target or patched source was unavailable."
        return record
    try:
        tree = ast.parse(patched_code)
    except SyntaxError as exc:
        record["reason"] = f"Patched source could not be parsed: {exc}"
        return record

    module_bindings: set[str] = set()
    symbol_bindings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_import or alias.name.startswith(f"{target_import}."):
                    module_bindings.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and (
            node.module == target_import or node.module.startswith(f"{target_import}.")
        ):
            for alias in node.names:
                if alias.name != "*":
                    symbol_bindings.add(alias.asname or alias.name)

    calls: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in module_bindings:
                calls.append({"line": node.lineno, "symbol": function.attr, "kind": "call"})
            elif isinstance(function, ast.Name) and function.id in symbol_bindings:
                calls.append({"line": node.lineno, "symbol": function.id, "kind": "call"})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id in symbol_bindings:
                    calls.append({"line": decorator.lineno, "symbol": decorator.id, "kind": "decorator"})
                elif isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name) and decorator.value.id in module_bindings:
                    calls.append({"line": decorator.lineno, "symbol": decorator.attr, "kind": "decorator"})

    record["calls"] = calls
    record["invoked"] = bool(calls)
    record["reason"] = (
        "Patched source invokes the selected target API."
        if calls else
        "Patched source imports the selected target but does not invoke one of its APIs."
    )
    return record


def _compact_adapter_runtime(raw_records: object) -> list[dict[str, object]]:
    """Keep adapter timing clearly separated from package profiler evidence."""
    if not isinstance(raw_records, list):
        return []
    compact: list[dict[str, object]] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        compact.append({
            "scope": "adapter_only",
            "status": _compact_text(raw.get("status", "unknown"), 80) or "unknown",
            "source_distribution": _compact_text(raw.get("source_distribution"), 160),
            "target_distribution": _compact_text(raw.get("target_distribution"), 160),
            "adapter_function": _compact_text(raw.get("adapter_function"), 160),
            "kind": _compact_text(raw.get("kind"), 160),
            "median_ms": _real_number(raw.get("median_ms")),
            "p95_ms": _real_number(raw.get("p95_ms")),
            "runs": _real_number(raw.get("runs")),
            "warmups": _real_number(raw.get("warmups")),
            "fixture_sha256": _compact_text(raw.get("fixture_sha256"), 128),
            "reason": _compact_text(raw.get("reason") or raw.get("error")),
        })
    return compact


def _alternative_efficiency_comparison(
    case: EvalCase,
    alternative: object,
    profiler_evidence: dict[str, dict],
    *,
    patched_code: object = None,
    selected_decision: object = None,
    adapter_runtime: list[dict] | None = None,
) -> dict[str, object]:
    """Compare a verified alternative to PyMigBench's target using real data.

    A passing patch alone proves functional acceptance, not that an alternative
    is *better*.  This records the exact comparable profiler measurements and
    only calls an alternative efficiency-superior when it has a material
    measured improvement and no material regression.  Missing or incompatible
    probes remain explicit rather than becoming a synthetic score.
    """
    winner = str(alternative or "").strip()
    record: dict[str, object] = {
        "reference_target": case.target,
        "alternative": winner or None,
        "measurement_source": "real_profiler",
        "claim_scope": "import_memory_only",
        "eligible": False,
        "verdict": "unavailable",
        "reason": "",
        "metrics": [],
        "target_api_usage": _patched_target_api_invocation(patched_code, selected_decision),
        "adapter_runtime": _compact_adapter_runtime(adapter_runtime or []),
        "runtime_evidence": {
            "source_alternative_shared_probe": None,
            "reference_alternative_shared_probe": None,
            "all_three_shared_probe": None,
        },
    }
    if not winner:
        record["reason"] = "No alternative winner was retained in the terminal state."
        return record

    target_profile = _profile_for_distribution(profiler_evidence, case.target)
    alternative_profile = _profile_for_distribution(profiler_evidence, winner)
    source_profile = _profile_for_distribution(profiler_evidence, case.source)
    if not target_profile or not alternative_profile:
        missing = []
        if not target_profile:
            missing.append(f"PyMigBench target {case.target!r}")
        if not alternative_profile:
            missing.append(f"selected alternative {winner!r}")
        record["reason"] = "No comparable real profile for " + " and ".join(missing) + "."
        return record
    if not target_profile.get("available") or not alternative_profile.get("available"):
        record["reason"] = "The target or selected alternative was unavailable in the verification environment."
        return record

    metrics: list[dict[str, object]] = []

    def add_metric(name: str, target_value: object, alternative_value: object) -> None:
        target_number = _real_number(target_value)
        alternative_number = _real_number(alternative_value)
        if target_number is None or alternative_number is None or target_number <= 0:
            return
        improvement = round(100 * (target_number - alternative_number) / target_number, 2)
        metrics.append({
            "metric": name,
            "reference_value": target_number,
            "alternative_value": alternative_number,
            "improvement_pct": improvement,
            "lower_is_better": True,
        })

    add_metric("import_time_ms", target_profile.get("import_time_ms"), alternative_profile.get("import_time_ms"))
    add_metric("memory_kb", target_profile.get("memory_kb"), alternative_profile.get("memory_kb"))

    source_runtime_values = _measured_probe_values(source_profile)
    target_runtime_values = _measured_probe_values(target_profile)
    alternative_runtime_values = _measured_probe_values(alternative_profile)
    source_alternative_shared = sorted(set(source_runtime_values).intersection(alternative_runtime_values))
    reference_alternative_shared = sorted(set(target_runtime_values).intersection(alternative_runtime_values))
    all_three_shared = sorted(
        set(source_runtime_values).intersection(target_runtime_values, alternative_runtime_values)
    )
    record["runtime_evidence"] = {
        "source_alternative_shared_probe": source_alternative_shared[0] if source_alternative_shared else None,
        "reference_alternative_shared_probe": reference_alternative_shared[0] if reference_alternative_shared else None,
        "all_three_shared_probe": all_three_shared[0] if all_three_shared else None,
    }
    if all_three_shared:
        probe_id = all_three_shared[0]
        add_metric(
            f"runtime:{probe_id}",
            target_runtime_values[probe_id],
            alternative_runtime_values[probe_id],
        )

    record["metrics"] = metrics
    api_usage = record["target_api_usage"]
    target_api_invoked = isinstance(api_usage, dict) and bool(api_usage.get("invoked"))
    if not target_api_invoked:
        record["verdict"] = "import_memory_only"
        record["reason"] = (
            "Only measured import/memory evidence is reported: the patched source does not invoke "
            "the selected alternative's API. Connector adapter timing, when present, is separate application-code evidence."
        )
        return record

    if not all_three_shared:
        record["verdict"] = "import_memory_only"
        record["reason"] = (
            "Only measured import/memory evidence is reported: source, PyMigBench reference target, and "
            "selected alternative do not share one measured semantic runtime probe."
        )
        return record

    runtime_metrics = [metric for metric in metrics if str(metric.get("metric", "")).startswith("runtime:")]
    if not runtime_metrics:
        record["verdict"] = "import_memory_only"
        record["reason"] = "The shared runtime probe did not yield valid comparable timing values."
        return record

    # A 10% *runtime* improvement is deliberately required before using the
    # phrase "runtime-efficient". Tiny timing noise is not a research result.
    # Conversely, a >5% regression in any comparable measurement prevents
    # that claim even if another metric improved dramatically.
    material_runtime_wins = [
        metric for metric in runtime_metrics if float(metric["improvement_pct"]) >= 10.0
    ]
    material_regressions = [metric for metric in metrics if float(metric["improvement_pct"]) <= -5.0]
    record["eligible"] = True
    record["claim_scope"] = "runtime"
    if material_runtime_wins and not material_regressions:
        record["verdict"] = "runtime_efficiency_superior"
        record["reason"] = (
            "The patched source invokes the selected target API; a shared semantic runtime probe improved by "
            "10% or more and no comparable metric regressed by more than 5%."
        )
    else:
        record["verdict"] = "runtime_not_superior"
        if material_regressions:
            record["reason"] = "A comparable real metric regressed by more than 5%."
        else:
            record["reason"] = "The shared runtime probe did not improve by the 10% materiality threshold."
    return record


def _repair_budget_exhausted(repair_history: list[dict]) -> bool:
    """Return true only when structured verifier evidence reached its cap."""
    for evidence in repair_history:
        if not isinstance(evidence, dict):
            continue
        try:
            cycle = int(evidence.get("cycle", 0))
            maximum = int(evidence.get("max_cycles", 0))
        except (TypeError, ValueError):
            continue
        if maximum > 0 and cycle >= maximum:
            return True
    return False


def _result_category(
    outcome: str,
    *,
    alternative_efficiency: dict[str, object],
    repair_budget_exhausted: bool,
) -> tuple[str, str]:
    """Map detailed terminal state into four publication-facing categories."""
    if outcome == "ground_truth_verified":
        return (
            "pymigbench_exact_verified",
            "AXIOM selected PyMigBench's target and the final patch passed verification.",
        )
    if outcome == "verified_alternative":
        verdict = alternative_efficiency.get("verdict")
        if verdict == "runtime_efficiency_superior":
            return (
                "verified_alternative",
                "A different target passed verification, invokes its selected API, and is runtime-efficient on a shared measured semantic probe.",
            )
        return (
            "verified_alternative",
            "A different target passed verification; the report records only measured import/memory or adapter evidence and makes no runtime-efficiency claim.",
        )
    if repair_budget_exhausted and outcome in {
        "verification_rejected", "target_infeasible", "unpatched_decision", "verification_incomplete",
    }:
        return (
            "rejected_after_repair_budget",
            "The migration was not accepted after the verifier's bounded repair budget was exhausted.",
        )
    if outcome == "no_migration_selected":
        return (
            "not_verified_or_not_adjudicated",
            "AXIOM kept the original library; this is a deliberate no-change decision, not a rejected patch.",
        )
    if outcome == "environment_incomplete":
        return (
            "not_verified_or_not_adjudicated",
            "The required historical base/target environment could not be reproduced, so no migration verdict is claimed.",
        )
    return (
        "not_verified_or_not_adjudicated",
        f"Terminal outcome: {outcome}.",
    )


def _selection_for_case(case: EvalCase, decisions: list[object]) -> dict[str, object]:
    """Describe one set of AXIOM selections without conflating it with verification."""
    source_decision = next(
        (
            decision for decision in decisions
            if _same_distribution(str(_decision_value(decision, "original", "")), case.source)
        ),
        None,
    )
    source_replacement = (
        source_decision
        if source_decision is not None
        and _decision_value(source_decision, "winner") != _decision_value(source_decision, "original")
        else None
    )
    incidental_decision = next(
        (
            decision for decision in decisions
            if _decision_value(decision, "winner") != _decision_value(decision, "original")
            and decision is not source_replacement
        ),
        None,
    )
    selected = source_replacement or incidental_decision
    exact_target = (
        _same_distribution(str(_decision_value(source_replacement, "winner", "")), case.target)
        if source_replacement is not None else None
    )
    if source_replacement is not None:
        status = "exact_target" if exact_target else "alternative"
    elif incidental_decision is not None:
        status = "incidental"
    elif source_decision is not None:
        status = "no_migration"
    else:
        status = "no_decision"
    return {
        "source_decision": source_decision,
        "source_replacement": source_replacement,
        "incidental_decision": incidental_decision,
        "selected": selected,
        "winner": _decision_value(selected, "winner", None) if selected is not None else None,
        "exact_target": exact_target,
        "status": status,
    }


def _target_infeasibility_evidence(case: EvalCase, state: dict) -> list[dict]:
    """Return only explicit verifier exclusions of this case's target.

    A dependency-installation conflict proves only that the historical
    environment could not be reproduced.  This evidence therefore comes
    solely from ``verification_failures``, which is populated when Connector
    or Stage 2 identifies a decision-level incompatibility for a candidate.
    """
    failures = state.get("verification_failures", {})
    if not isinstance(failures, dict):
        return []

    records: list[dict] = []
    for role, candidates in failures.items():
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, str) and _same_distribution(candidate, case.target):
                records.append({"role": str(role), "candidate": candidate})
    if not records:
        return []

    feedback = state.get("_last_verifier_feedback", {})
    errors = feedback.get("errors", []) if isinstance(feedback, dict) else []
    messages = [
        message for error in errors if isinstance(error, dict)
        if (message := _compact_text(error.get("message"), 500))
    ]
    for record in records:
        record["verifier_evidence"] = messages
    return records


def _result_from_state(case: EvalCase, state: dict, *, detected_group: bool | None,
                       seeded: bool = False) -> EvalResult:
    """Turn a terminal graph state into evidence, never an optimistic guess."""
    decisions = state.get("decisions", [])
    final_selection = _selection_for_case(case, decisions)
    attempt_history = state.get("attempted_decision_history", [])
    first_attempt = attempt_history[0] if attempt_history else decisions
    attempted_selection = _selection_for_case(case, first_attempt)
    source_decision = final_selection["source_decision"]
    source_replacement = final_selection["source_replacement"]
    incidental_decision = final_selection["incidental_decision"]
    selected = final_selection["selected"]
    exact_target_selected = final_selection["exact_target"]
    patched_code = state.get("patched_code")
    patch_changed = bool(patched_code and patched_code.strip() and patched_code != case.before_code)
    verification_status = state.get("verification_status", "not_run")
    environment_status = state.get("verification_environment_status", "unknown")
    # Retry traces may retain failed *intermediate* attempts.  The verifier
    # only sets ``verified`` after the final differential Stage 1 passes, so
    # that terminal status—not an all-history reduction—is the right signal.
    verifier_clean = verification_status == "verified"
    error = state.get("error")
    # Evaluator paths that predate the repair loop deliberately remain
    # compatible: absent or malformed evidence is represented as no repair,
    # never guessed from retry counters.
    raw_history = state.get("_repair_history", [])
    repair_history = list(raw_history) if isinstance(raw_history, list) else []
    try:
        repair_cycle_count = max(0, int(state.get("_repair_cycle_count", 0)))
    except (TypeError, ValueError):
        repair_cycle_count = 0
    raw_feedback = state.get("_last_verifier_feedback", {})
    final_verifier_feedback = dict(raw_feedback) if isinstance(raw_feedback, dict) else {}
    profiler_evidence = _compact_profiler_evidence(state.get("benchmarks", {}))
    adapter_runtime = _compact_adapter_runtime(state.get("adapter_runtime", []))
    environment_plan = _compact_environment_plan(state.get("environment_plan", {}))
    target_infeasibility_evidence = _target_infeasibility_evidence(case, state)
    connector_ever_ran = bool(state.get("connectors")) or any(
        (
            event.get("stage") == "connector" and event.get("event") == "started"
            if isinstance(event, dict)
            else getattr(event, "stage", None) == "connector" and getattr(event, "event", None) == "started"
        )
        for event in state.get("agent_trace", [])
    )

    selection_status = str(final_selection["status"])

    if verification_status == "environment_incomplete":
        outcome = "environment_incomplete"
    elif verification_status == "rejected":
        outcome = "verification_rejected"
    elif verification_status == "verified" and patch_changed and source_replacement:
        outcome = "ground_truth_verified" if exact_target_selected else "verified_alternative"
    elif verification_status == "verified" and patch_changed and incidental_decision:
        outcome = "verified_incidental_migration"
    elif error:
        lowered = error.casefold()
        outcome = "provider_failure" if any(
            term in lowered for term in (
                "api", "rate limit", "503", "timeout", "nvidia", "openai", "anthropic",
                "provider unavailable", "authentication", "unauthorized", "forbidden",
                "connection", "network", "dns",
            )
        ) else "pipeline_error"
    elif source_decision is None:
        outcome = "no_decision" if seeded else "resolver_miss"
    elif target_infeasibility_evidence and (
        _decision_value(source_decision, "winner") == _decision_value(source_decision, "original")
    ):
        outcome = "target_infeasible"
    elif _decision_value(source_decision, "winner") == _decision_value(source_decision, "original"):
        outcome = "no_migration_selected"
    elif not patch_changed:
        outcome = "unpatched_decision"
    else:
        outcome = "verification_incomplete"

    alternative_efficiency = (
        _alternative_efficiency_comparison(
            case,
            final_selection["winner"],
            profiler_evidence,
            patched_code=patched_code,
            selected_decision=final_selection["selected"],
            adapter_runtime=adapter_runtime,
        )
        if outcome == "verified_alternative" else {}
    )
    repair_budget_exhausted = _repair_budget_exhausted(repair_history)
    result_category, category_reason = _result_category(
        outcome,
        alternative_efficiency=alternative_efficiency,
        repair_budget_exhausted=repair_budget_exhausted,
    )

    return EvalResult(
        case=case,
        detected_group=detected_group,
        recommended_target=attempted_selection["exact_target"],
        # A source-aligned "keep the original" decision is a scored negative
        # for target selection, not something to omit from the denominator.
        scoreable_full_pipeline=attempted_selection["source_decision"] is not None,
        axiom_winner=final_selection["winner"],
        attempted_axiom_winner=attempted_selection["winner"],
        attempted_selection_status=str(attempted_selection["status"]),
        attempted_selection_count=len(attempt_history),
        connector_ran=connector_ever_ran,
        connector_ever_ran=connector_ever_ran,
        verifier_route=state.get("_verifier_route") or None,
        repair_cycle_count=repair_cycle_count,
        repair_history=repair_history,
        final_verifier_feedback=final_verifier_feedback,
        profiler_evidence=profiler_evidence,
        environment_plan=environment_plan,
        verifier_passed_clean=verifier_clean,
        target_infeasibility_evidence=target_infeasibility_evidence,
        patched_code=patched_code,
        patch_changed=patch_changed,
        verification_status=verification_status,
        verification_environment_status=environment_status,
        selection_status=selection_status,
        outcome=outcome,
        verified_ground_truth=outcome == "ground_truth_verified",
        verified_alternative=outcome == "verified_alternative",
        result_category=result_category,
        category_reason=category_reason,
        alternative_efficiency=alternative_efficiency,
        adapter_runtime=adapter_runtime,
        repair_budget_exhausted=repair_budget_exhausted,
        error=error,
    )


def run_detection_only(case: EvalCase, provider: str, model: str) -> EvalResult:
    """Fast path: parser_agent + resolver_agent only (no rules/profiler/axiom).
    Checks equivalence-group detection — does AXIOM's resolver recognize
    source and target as substitutable? Now meaningful even for files that
    only import the source library, since resolver_agent has its own
    decoupled library-discovery step (see agents/resolver_agent.py)."""
    import tempfile as _tempfile
    from pathlib import Path as _Path
    from smart_loader.agents.parser_agent import parser_agent
    from smart_loader.agents.resolver_agent import resolver_agent

    # Detection itself does not create a virtual environment, but recording
    # the side-effect-free plan exposes a historical interpreter mismatch
    # before the same case is later sent through the full pipeline.
    environment_plan = _build_case_environment_plan(case)

    with _tempfile.NamedTemporaryFile(
        suffix=f"_{Path(case.file_path).name}", mode="w", delete=False, dir="/tmp"
    ) as f:
        f.write(case.before_code)
        parse_tmp_path = f.name
    try:
        parser_state = parser_agent({"source_file": parse_tmp_path})
    finally:
        _Path(parse_tmp_path).unlink(missing_ok=True)

    if parser_state.get("error"):
        return EvalResult(
            case=case, outcome="pipeline_error", error=f"parser_agent: {parser_state['error']}",
            environment_plan=_compact_environment_plan(environment_plan),
        )

    imports = parser_state.get("imports", [])
    if not any(_import_name_matches(imp.module.split(".")[0], case.source) for imp in imports):
        return EvalResult(
            case=case, outcome="pipeline_error", error="source package not actually imported in before_code",
            environment_plan=_compact_environment_plan(environment_plan),
        )

    state = {
        "imports": imports, "rule_results": [], "source_code": case.before_code,
        "llm_provider": provider, "model": model, "_session_id": _safe_session_id(case.migration_id),
        "source_file": case.file_path, "agent_trace": [],
        "migration_source": case.source,
        "migration_source_import": _source_import_for_case(case),
        "verification_import_paths": _verification_import_paths(case),
        "verification_package": _verification_package(case),
        "verification_project_root": case.project_root,
        "verification_file_path": case.file_path,
    }
    try:
        out = resolver_agent(state)
    except Exception as e:
        return EvalResult(
            case=case, error=f"resolver_agent raised: {e}",
            environment_plan=_compact_environment_plan(environment_plan),
        )

    groups = out.get("equivalence_groups", [])
    def _in_group(name: str, candidates: list[str]) -> bool:
        return any(_import_name_matches(c, name) or c.lower() == name.lower() for c in candidates)

    detected = any(
        _in_group(case.source, g.candidates) and _in_group(case.target, g.candidates)
        for g in groups
    )
    return EvalResult(
        case=case, detected_group=detected,
        environment_plan=_compact_environment_plan(environment_plan),
    )


def run_pipeline_from_known_pair(case: EvalCase, provider: str, model: str, max_loop_iters: int = 20,
                                 auto_install: bool = False, enable_security: bool = True,
                                 isolated_profiling: bool = True) -> EvalResult:
    """
    Bypasses resolver_agent's blind discovery entirely and seeds the
    equivalence group directly from PyMigBench's ground-truth (source,
    target) pair, then runs the REST of the pipeline exactly as the real
    graph would: security (optional) -> profiler -> axiom -> (connector) ->
    (verifier),
    looping back to axiom/connector on failure using the exact same routing
    functions graph.py uses (_after_axiom, _after_verifier — imported
    directly, not reimplemented, so this can't silently drift from what a
    real `axiom run` does).

    WHY THIS EXISTS: resolver_agent now HAS a decoupled library-discovery
    step (see agents/resolver_agent.py's _discover_alternatives) that can
    propose packages never imported in the analyzed file — so its blind
    detection rate is no longer structurally capped near zero the way it
    used to be. This function still exists as a controlled ablation: it
    isolates AXIOM's EXECUTION quality (benchmark, score, generate a working
    wrapper, pass verification) from its DISCOVERY quality, by handing it
    the ground-truth pair directly rather than depending on resolver having
    found it. Use --full-pipeline (without --seed-target) to test discovery
    and execution together, end-to-end, the way a real unattended run would
    work.

    Sets detected_group=None (not applicable — the pair was seeded, not
    discovered) so seeded cases are correctly excluded from the detection
    rate metric in compute_metrics() rather than inflating it.

    ``max_loop_iters`` defaults to 20 so one initial AXIOM pass plus up to
    five globally capped repair cycles can reach a terminal verifier pass or
    rejection even when an AXIOM repair then travels through Connector and
    Verifier again. Earlier evaluators stopped after six graph transitions
    and could therefore report a false pipeline error before the repair
    budget was actually exercised.

    auto_install: best-effort `pip install` of case.source and case.target
    before profiler_agent runs (see ensure_pip_installed). Off by default —
    installing arbitrary PyPI packages from a benchmark dataset has real
    supply-chain and disk/time cost, so this should be an explicit opt-in,
    not silent default behavior.

    enable_security: run the same source- and candidate-package OSV/PyPI
    security scans used by the normal graph. It is on by default because
    isolated target installation requires an exact, interpreter-compatible
    target release. Disable it only when an explicit target version is
    supplied by a caller that can provide equivalent evidence.
    """
    import tempfile as _tempfile
    from pathlib import Path as _Path
    from smart_loader.agents.parser_agent import parser_agent
    from smart_loader.agents.profiler_agent import profiler_agent
    from smart_loader.agents.axiom_agent import axiom_agent
    from smart_loader.agents.connector_agent import connector_agent
    from smart_loader.agents.verifier_agent import verifier_agent
    from smart_loader.agents.security_agent import security_agent, candidate_security_agent
    from smart_loader.core.graph import (
        _after_axiom,
        _after_verifier,
        _cleanup_disposable_environments,
    )
    from smart_loader.core.state import EquivalenceGroup

    environment_plan = _build_case_environment_plan(case)
    environment_inputs = _environment_pipeline_inputs(environment_plan, case.target)

    # Route through the REAL parser_agent() — not a hand-rolled AST walk —
    # so this eval path exercises the same file-reading/parsing code a real
    # `axiom run` does (including its own console output, which is why you
    # should now see a "🔍 Parser Agent" section per case; it was silently
    # skipped before this fix).
    with _tempfile.NamedTemporaryFile(
        suffix=f"_{_Path(case.file_path).name}", mode="w", delete=False, dir="/tmp"
    ) as f:
        f.write(case.before_code)
        parse_tmp_path = f.name
    try:
        parser_state = parser_agent({"source_file": parse_tmp_path})
    finally:
        _Path(parse_tmp_path).unlink(missing_ok=True)

    if parser_state.get("error"):
        return EvalResult(
            case=case, outcome="pipeline_error", error=f"parser_agent: {parser_state['error']}",
            environment_plan=_compact_environment_plan(environment_plan),
        )

    imports = parser_state.get("imports", [])
    source_imports = [imp for imp in imports if _import_name_matches(imp.module.split(".")[0], case.source)]
    if not source_imports:
        return EvalResult(
            case=case, outcome="pipeline_error", error="source package not actually imported in before_code",
            environment_plan=_compact_environment_plan(environment_plan),
        )
    used_apis = sorted({a for imp in source_imports for a in imp.api_calls})

    session_id = _safe_session_id(case.migration_id)
    state: dict = {
        "imports": imports, "source_code": case.before_code, "patched_code": case.before_code,
        "llm_provider": provider, "model": model, "_session_id": session_id,
        "source_file": case.file_path, "agent_trace": [],
        "verifier_results": [], "verification_failures": {},
        "attempted_decision_history": [],
        "_axiom_retry_count": 0, "_connector_retry_count": 0,
        "_repair_cycle_count": 0, "_repair_history": [],
        "_last_verifier_feedback": {},
        "_verifier_route": "", "_last_connector_error": "", "connectors": {},
        "enable_connector": True,
        "isolated_profiling": isolated_profiling,
        "base_requirements": case.base_requirements,
        "base_requirement_recovery_errors": case.base_requirement_recovery_errors,
        "migration_source": case.source,
        "migration_source_import": _source_import_for_case(case),
        **environment_inputs,
        "verification_import_paths": _verification_import_paths(case),
        "verification_package": _verification_package(case),
        "verification_project_root": case.project_root,
        "verification_file_path": case.file_path,
        "verification_status": "not_run", "verification_environment_status": "unknown",
        "verification_tool_status": {}, "error": None,
        "verification_environment_cache": {},
    }

    try:
        # Isolated profiling already installs the historical base environment
        # and source/target candidates inside its disposable venv.  Never
        # duplicate that work (or pollute the user's interpreter) when the
        # sandbox mode is active.
        if auto_install and not isolated_profiling:
            ensure_pip_installed(case.source)
            ensure_pip_installed(case.target)
        elif auto_install:
            console.print("[dim]--auto-install is unnecessary with --isolated-profiling; using the disposable sandbox only.[/dim]")

        if enable_security:
            state.update(security_agent(state))

        seeded_group = EquivalenceGroup(
            role=f"{case.source} -> {case.target} (seeded from PyMigBench ground truth)",
            candidates=[case.source, case.target], used_apis=used_apis,
            reasoning="Seeded directly from a PyMigBench-confirmed real migration for pipeline-"
                      "execution evaluation, bypassing resolver_agent's own discovery step — see "
                      "run_pipeline_from_known_pair's docstring.",
            pitfalls=[],
        )
        # The source distribution name and Python import root often differ
        # (``pycrypto``/``Crypto``, ``slackclient``/``slack``).  Preserve the
        # parser-observed source binding so AST rewrite and verifier integrity
        # checks operate on the actual code instead of a guessed underscored
        # distribution name.
        source_import = _source_import_for_case(case)
        seeded_group.__dict__["original"] = case.source
        seeded_group.__dict__["original_import"] = source_import
        seeded_group.__dict__["candidate_imports"] = {case.source: source_import}
        state["equivalence_groups"] = [seeded_group]
        if enable_security:
            state.update(candidate_security_agent(state))

        state.update(profiler_agent(state))
        # AXIOM may produce a decision when only some evidence is available,
        # but it never invents neutral performance values: unavailable speed,
        # memory, or runtime dimensions are omitted from the weighted score.
        # ``scoreable`` therefore means "AXIOM made a source-aligned
        # selection"; the result's per-dimension profiler/verifier evidence
        # tells a report reader exactly what was actually measured and
        # validated.
        node = "axiom"
        for _ in range(max_loop_iters):
            if node == "axiom":
                state.update(axiom_agent(state))
                node = _after_axiom(state)
            elif node == "connector":
                state.update(connector_agent(state))
                node = "verifier"
            elif node == "verifier":
                state.update(verifier_agent(state))
                node = _after_verifier(state)
            else:  # "end"
                break
        if node != "end":
            state["error"] = (
                f"Seeded pipeline did not reach a terminal verifier decision after {max_loop_iters} steps "
                f"({state.get('_repair_cycle_count', 0)} repair cycle(s))."
            )
    except Exception as e:
        state["error"] = f"seeded pipeline raised: {e}"
    finally:
        _cleanup_disposable_environments(state)

    return _result_from_state(case, state, detected_group=None, seeded=True)


# ── Developer-diff comparison mode (replicates Islam et al.'s RQ1 methodology) ──
#
# "Using LLMs for Library Migration" / MigrateLib (same authors) evaluate an
# LLM migration by comparing File_llm against File_dev (the developer's own
# actual migration) at the level of PyMigBench's individually-recorded code
# changes, each with an exact line range. Their full process has TWO steps:
#   A. Auto change matching — exact AST-unparse-normalized match between the
#      Change_dev segment and some segment of File_llm. Fully automatable.
#   B. Manual review of anything auto-matching missed — a human checks
#      whether an unmatched Change_llm is actually a correct alternative
#      (different API, refactored, etc.) using library docs. NOT automatable.
#
# What's implemented here is step A only, honestly labeled as such — a text-
# normalized substring match (not full AST-unparse matching; see
# _normalize_snippet's docstring for why) standing in for their exact-match
# step. This means our "correct" rate is a LOWER BOUND relative to their
# published numbers: cases using a different-but-valid API, or the same API
# refactored differently, will show up here as "unmatched" even though a
# human reviewer might correctly call them correct. This is the same
# under-count their OWN auto-matching-only step would produce before manual
# review — see Figure 2/3 in "Using LLMs for Library Migration": only 1 of
# 10 Change_dev entries auto-matched in their worked example, with the rest
# resolved by manual review. Report numbers from this mode accordingly: as
# "AXIOM's auto-matched correctness," not as directly comparable to their
# final human-reviewed percentages, unless you also do the manual review
# pass on the unmatched cases (the CSV's dev_snippet/llm... columns are
# written specifically to make that manual pass easier to do by hand).

def _extract_lines(code: str, line_nums: list[int]) -> str:
    lines = code.splitlines()
    selected = [lines[i - 1] for i in line_nums if 0 < i <= len(lines)]
    return "\n".join(selected)


def _normalize_snippet(text: str) -> str:
    """
    Whitespace/blank-line-insensitive normalization, standing in for the
    papers' ast.unparse-based normalization. A full AST-based comparison
    would additionally be insensitive to e.g. quote style (single vs double)
    and trailing commas, and would require re-parsing line-extracted
    snippets in isolation (risky — a snippet may not be a syntactically
    complete statement on its own, e.g. one branch of an if/else split
    across a recorded line range). Text normalization is strictly weaker
    but doesn't have that failure mode, which matters more at the scale of
    an unattended batch eval than at the scale of the papers' manually-
    reviewed worked examples.
    """
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


@dataclass
class CCMatchResult:
    line_expr:    str
    source_apis:  list[str]
    target_apis:  list[str]
    matched:      bool
    dev_snippet:  str
    reason:       str   # "target_found_in_llm" | "source_correctly_removed" | "source_not_removed" | "target_not_found"


def match_code_changes(before_code: str, dev_code: str, llm_code: str, code_changes: list[CCRecord]) -> list[CCMatchResult]:
    """Step A only — see the module note above. Returns one CCMatchResult per
    PyMigBench-recorded code change for this file."""
    llm_norm = _normalize_snippet(llm_code)
    results = []
    for cc in code_changes:
        if cc.is_removal_only:
            # Nothing was added — success means the OLD (source) segment is
            # correctly gone from File_llm, not that something new appears.
            source_snippet = _extract_lines(before_code, cc.source_line_nums)
            source_norm = _normalize_snippet(source_snippet)
            removed = (not source_norm) or (source_norm not in llm_norm)
            results.append(CCMatchResult(
                line_expr=cc.line_expr, source_apis=cc.source_apis, target_apis=cc.target_apis,
                matched=removed, dev_snippet="(removal — no target content expected)",
                reason="source_correctly_removed" if removed else "source_not_removed",
            ))
            continue

        dev_snippet = _extract_lines(dev_code, cc.target_line_nums)
        dev_norm = _normalize_snippet(dev_snippet)
        found = bool(dev_norm) and dev_norm in llm_norm
        results.append(CCMatchResult(
            line_expr=cc.line_expr, source_apis=cc.source_apis, target_apis=cc.target_apis,
            matched=found, dev_snippet=dev_snippet,
            reason="target_found_in_llm" if found else "target_not_found",
        ))
    return results


@dataclass
class BenchmarkResult:
    case:                EvalCase
    status:              str   # "correct" | "partially_correct" | "incorrect" | "syntax_error" | "no_migration_attempted" | "error"
    matched_cc:          int
    total_cc:            int
    cc_matches:          list[CCMatchResult]
    llm_code:            str | None = None
    error:               str | None = None

    @property
    def cc_match_rate(self) -> float | None:
        return (self.matched_cc / self.total_cc) if self.total_cc else None


def run_benchmark_comparison_case(
    case: EvalCase,
    provider: str,
    model: str,
    auto_install: bool = False,
    enable_security: bool = True,
) -> BenchmarkResult:
    """
    Full replication of the papers' RQ1 comparison for one file: migrate
    File_pre with AXIOM (via the seeded pipeline — see
    run_pipeline_from_known_pair, since resolver's own blind discovery
    isn't the question this mode answers), then compare the result against
    File_dev using match_code_changes.

    Requires case.dev_code and case.code_changes to be populated — i.e. this
    must be called on EvalCases from build_benchmark_cases(), not
    build_eval_cases().
    """
    if case.dev_code is None or case.code_changes is None:
        return BenchmarkResult(
            case=case, status="error", matched_cc=0, total_cc=0, cc_matches=[],
            error="EvalCase missing dev_code/code_changes — use build_benchmark_cases(), not build_eval_cases()",
        )

    eval_result = run_pipeline_from_known_pair(
        case,
        provider,
        model,
        auto_install=auto_install,
        enable_security=enable_security,
    )
    if eval_result.error:
        return BenchmarkResult(
            case=case, status="error", matched_cc=0, total_cc=len(case.code_changes),
            cc_matches=[], error=eval_result.error,
        )

    llm_code = eval_result.patched_code
    if not llm_code or llm_code.strip() == case.before_code.strip():
        # Mirrors the papers' "Response failure" status — AXIOM produced no
        # (or no different) output for this file.
        return BenchmarkResult(
            case=case, status="no_migration_attempted", matched_cc=0,
            total_cc=len(case.code_changes), cc_matches=[], llm_code=llm_code,
        )

    import ast as _ast
    try:
        _ast.parse(llm_code)
    except SyntaxError as e:
        return BenchmarkResult(
            case=case, status="syntax_error", matched_cc=0,
            total_cc=len(case.code_changes), cc_matches=[], llm_code=llm_code,
            error=str(e),
        )

    matches = match_code_changes(case.before_code, case.dev_code, llm_code, case.code_changes)
    matched_count = sum(1 for m in matches if m.matched)
    total = len(matches)
    if total == 0:
        status = "error"
    elif matched_count == total:
        status = "correct"
    elif matched_count == 0:
        status = "incorrect"
    else:
        status = "partially_correct"

    return BenchmarkResult(
        case=case, status=status, matched_cc=matched_count, total_cc=total,
        cc_matches=matches, llm_code=llm_code,
    )


def compute_benchmark_metrics(results: list[BenchmarkResult]) -> dict:
    n = len(results)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    total_cc = sum(r.total_cc for r in results)
    matched_cc = sum(r.matched_cc for r in results)

    by_domain: dict[str, dict] = {}
    for r in results:
        dom = r.case.domain
        d = by_domain.setdefault(dom, {"n": 0, "matched_cc": 0, "total_cc": 0})
        d["n"] += 1
        d["matched_cc"] += r.matched_cc
        d["total_cc"] += r.total_cc

    return {
        "total_files": n,
        "by_status": by_status,
        "by_status_pct": {k: round(100 * v / n, 1) for k, v in by_status.items()} if n else {},
        "code_change_match_rate_pct": round(100 * matched_cc / total_cc, 2) if total_cc else None,
        "total_code_changes": total_cc,
        "matched_code_changes": matched_cc,
        "by_domain": {
            dom: {
                "n": d["n"],
                "cc_match_rate_pct": round(100 * d["matched_cc"] / d["total_cc"], 2) if d["total_cc"] else None,
            }
            for dom, d in by_domain.items()
        },
    }


def print_benchmark_summary(metrics: dict) -> None:
    console.print()
    console.print("[bold cyan]═══ AXIOM vs Developer Migration — Comparison Summary ═══[/bold cyan]")
    console.print(
        "[dim]Step-A auto-matching only — see run_benchmark_comparison_case's module note. "
        "These numbers are a LOWER BOUND vs. the papers' human-reviewed percentages.[/dim]\n"
    )
    console.print(f"Total files evaluated:      {metrics['total_files']}")
    table = Table(title="Migration Status (file level)", header_style="bold cyan")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_column("%", justify="right")
    status_order = ["correct", "partially_correct", "incorrect", "syntax_error", "no_migration_attempted", "error"]
    for status in status_order:
        count = metrics["by_status"].get(status, 0)
        if count:
            table.add_row(status, str(count), f"{metrics['by_status_pct'][status]}%")
    console.print(table)

    if metrics["code_change_match_rate_pct"] is not None:
        console.print(
            f"\n[bold]Code-change auto-match rate:[/bold] {metrics['code_change_match_rate_pct']}%  "
            f"({metrics['matched_code_changes']}/{metrics['total_code_changes']} code changes)"
        )

    dtable = Table(title="Code-Change Match Rate by Domain", header_style="bold cyan")
    dtable.add_column("Domain")
    dtable.add_column("Files", justify="right")
    dtable.add_column("CC Match Rate", justify="right")
    for dom, d in sorted(metrics["by_domain"].items(), key=lambda x: -x[1]["n"]):
        rate = f"{d['cc_match_rate_pct']}%" if d["cc_match_rate_pct"] is not None else "—"
        dtable.add_row(dom, str(d["n"]), rate)
    console.print(dtable)


def run_full_pipeline(case: EvalCase, provider: str, model: str, require_approval: bool = False,
                      enable_security: bool = True, isolated_profiling: bool = True) -> EvalResult:
    """
    Slow path: the FULL automated pipeline — resolver -> profiler -> axiom ->
    connector -> verifier. Security/version resolution is on by default: it
    selects an exact target release compatible with the historical Python
    interpreter before any candidate can be installed. Connector and verifier are ON,
    unlike an earlier version of this function which
    disabled connector entirely — that meant `--full-pipeline` silently
    never exercised connector_agent or verifier_agent regardless of the
    flag, which defeated the point of asking for the full pipeline. Fixed.

    require_approval: when True, runs with the SAME approval_gate/interrupt()
    code path `axiom run --feedback` uses — real gated routing, not a
    simulation of it — but auto-resumes with {"action": "accept_all"}
    instead of pausing for a literal human, the same substitution
    `axiom run --feedback --non-interactive` already makes for CI. A literal
    per-case human prompt across hundreds of migrations isn't something a
    batch eval can do, but the gated CODE PATH itself — which does the AST
    rewrite at a different point than the non-gated path — is worth being
    able to exercise and verify here rather than permanently skipping it.
    Defaults to False (matches the non-gated path AXIOM used before this
    flag existed).

    PyMigBench execution defaults to disposable historical environments.  A
    direct caller may opt out, but then missing verifier tools fail closed
    rather than being treated as a clean Stage 1 result.
    """
    from smart_loader.core.graph import _cleanup_disposable_environments, run_loader, resume_loader

    environment_plan = _build_case_environment_plan(case)
    environment_inputs = _environment_pipeline_inputs(environment_plan, case.target)

    with tempfile.NamedTemporaryFile(
        suffix=f"_{Path(case.file_path).name}", mode="w", delete=False, dir="/tmp"
    ) as f:
        f.write(case.before_code)
        tmp_path = f.name

    state: dict = {}
    try:
        state = run_loader(
            tmp_path, llm_provider=provider, model=model,
            enable_security=enable_security, enable_connector=True,
            require_approval=require_approval, isolated_profiling=isolated_profiling,
            base_requirements=case.base_requirements, migration_source=case.source,
            base_requirement_recovery_errors=case.base_requirement_recovery_errors,
            migration_source_import=_source_import_for_case(case),
            environment_plan=environment_inputs["environment_plan"],
            environment_python=str(environment_inputs["environment_python"]),
            migration_target=str(environment_inputs["migration_target"]),
            migration_target_version=str(environment_inputs["migration_target_version"]),
            verification_import_paths=_verification_import_paths(case),
            verification_package=_verification_package(case),
            verification_project_root=case.project_root,
            verification_file_path=case.file_path,
        )
        if state.get("__interrupted__"):
            # Same substitution --non-interactive makes for `axiom run
            # --feedback`: auto-accept so the gated path can run to
            # completion inside a batch eval instead of hanging forever
            # waiting for a human that isn't there.
            state = resume_loader(state["_graph"], state["_thread_id"], {"action": "accept_all"})
    except Exception as e:
        return EvalResult(
            case=case, outcome="pipeline_error", error=f"run_loader raised: {e}",
            environment_plan=_compact_environment_plan(environment_plan),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        _cleanup_disposable_environments(state)

    def _in_group(name: str, candidates: list[str]) -> bool:
        return any(_import_name_matches(c, name) or c.lower() == name.lower() for c in candidates)

    detected = any(
        _in_group(case.source, g.candidates) and _in_group(case.target, g.candidates)
        for g in state.get("equivalence_groups", [])
    )
    # A graph can end before Resolver (parser/provider error).  That is not a
    # resolver miss and must not enter the detection-rate denominator.
    resolver_completed = any(
        getattr(event, "stage", None) == "resolver"
        and getattr(event, "event", None) == "completed"
        for event in state.get("agent_trace", [])
    )
    return _result_from_state(
        case, state, detected_group=detected if resolver_completed else None,
    )


# ── Metrics ───────────────────────────────────────────────────────────────────────

def compute_metrics(results: list[EvalResult]) -> dict:
    n = len(results)

    detection_valid = [r for r in results if r.detected_group is not None]
    detection_rate = (
        sum(1 for r in detection_valid if r.detected_group) / len(detection_valid)
        if detection_valid else 0.0
    )

    scoreable = [r for r in results if r.scoreable_full_pipeline]
    selection_rate = (
        sum(1 for r in scoreable if r.recommended_target) / len(scoreable)
        if scoreable else None
    )

    connector_cases = [r for r in results if r.connector_ran]
    # ``environment_incomplete`` means no baseline/target pair reached a
    # deterministic patch verdict.  Counting it as a verifier rejection made
    # a 15-case environment outage look like 15 failed AXIOM patches.
    verifier_cases = [
        r for r in results if r.verification_status in {"verified", "rejected"}
    ]
    verifier_clean_rate = (
        sum(1 for r in verifier_cases if r.verifier_passed_clean) / len(verifier_cases)
        if verifier_cases else None
    )
    environment_incomplete_cases = [
        r for r in results if r.verification_status == "environment_incomplete"
    ]
    environment_incomplete_by_status: dict[str, int] = {}
    for result in environment_incomplete_cases:
        status = str(result.verification_environment_status or "unknown")
        environment_incomplete_by_status[status] = (
            environment_incomplete_by_status.get(status, 0) + 1
        )

    # Repair-loop evidence is deliberately counted from the verifier's
    # structured state, not inferred from how many times AXIOM happened to
    # run.  That distinction matters because an AXIOM retry can be caused by
    # a decision-level incompatibility while a Connector retry means a patch
    # or static-analysis problem.
    repair_cases = [r for r in results if r.repair_cycle_count > 0]
    repair_cycle_total = sum(r.repair_cycle_count for r in results)
    repair_routes: dict[str, int] = {}
    final_verifier_routes: dict[str, int] = {}
    repair_budget_exhausted_n = 0
    for r in results:
        if r.verifier_route:
            final_verifier_routes[r.verifier_route] = (
                final_verifier_routes.get(r.verifier_route, 0) + 1
            )
        cycle_reached_budget = False
        for evidence in r.repair_history:
            if not isinstance(evidence, dict):
                continue
            route = evidence.get("route")
            if isinstance(route, str) and route:
                repair_routes[route] = repair_routes.get(route, 0) + 1
            try:
                cycle = int(evidence.get("cycle", 0))
                max_cycles = int(evidence.get("max_cycles", 0))
                if max_cycles > 0 and cycle >= max_cycles:
                    cycle_reached_budget = True
            except (TypeError, ValueError):
                pass
        if r.repair_budget_exhausted or cycle_reached_budget:
            repair_budget_exhausted_n += 1
    verified_after_repair_n = sum(
        1 for r in repair_cases if r.verification_status == "verified"
    )

    # Runtime coverage is a package-case measurement statistic.  It only
    # counts probes whose recorded status is ``measured``; unsupported APIs,
    # timeouts, and missing environments remain visible in the status counts
    # rather than being folded into a default performance score.
    runtime_stats_by_result = [_runtime_evidence_stats(r.profiler_evidence) for r in results]
    profiler_package_case_n = sum(int(stats["package_count"]) for stats in runtime_stats_by_result)
    runtime_measured_package_case_n = sum(
        int(stats["measured_package_count"]) for stats in runtime_stats_by_result
    )
    runtime_measured_probe_n = sum(
        int(stats["measured_probe_count"]) for stats in runtime_stats_by_result
    )
    runtime_probe_status_counts: dict[str, int] = {}
    for stats in runtime_stats_by_result:
        statuses = stats["probe_status_counts"]
        if not isinstance(statuses, dict):
            continue
        for status, count in statuses.items():
            runtime_probe_status_counts[str(status)] = (
                runtime_probe_status_counts.get(str(status), 0) + int(count)
            )

    outcomes: dict[str, int] = {}
    result_categories: dict[str, int] = {}
    for r in results:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
        result_categories[r.result_category] = result_categories.get(r.result_category, 0) + 1
    verified_exact = outcomes.get("ground_truth_verified", 0)
    verified_alternatives = outcomes.get("verified_alternative", 0)
    verified_incidental = outcomes.get("verified_incidental_migration", 0)
    target_infeasible = outcomes.get("target_infeasible", 0)
    verified_alternative_runtime_efficiency_superior = sum(
        1
        for r in results
        if r.result_category == "verified_alternative"
        and r.alternative_efficiency.get("verdict") == "runtime_efficiency_superior"
    )
    verified_alternative_runtime_efficiency_unproven = sum(
        1
        for r in results
        if r.result_category == "verified_alternative"
        and r.alternative_efficiency.get("verdict") != "runtime_efficiency_superior"
    )
    # The only valid conditional denominator for a patch-verification rate is
    # cases that actually reached a deterministic verdict, not resolver misses
    # or cases that happened not to have an environment setup error.
    execution_ready = verifier_cases

    by_domain: dict[str, dict] = {}
    for r in results:
        dom = r.case.domain
        d = by_domain.setdefault(dom, {"n": 0, "detected": 0, "eval_detected": 0, "verified_exact": 0})
        d["n"] += 1
        if r.detected_group is not None:
            d["eval_detected"] += 1
            if r.detected_group:
                d["detected"] += 1
        if r.verified_ground_truth:
            d["verified_exact"] += 1

    return {
        "total_cases":        n,
        "errored_cases":      outcomes.get("pipeline_error", 0) + outcomes.get("provider_failure", 0),
        "detection_rate_pct": round(detection_rate * 100, 2),
        "detection_n":        len(detection_valid),
        # Selection is intentionally not called full-pipeline accuracy: it
        # answers whether AXIOM chose PyMigBench's name, not whether a working
        # patch survived verification.
        "target_selection_rate_pct": round(selection_rate * 100, 2) if selection_rate is not None else None,
        "target_selection_n": len(scoreable),
        "verified_ground_truth_n": verified_exact,
        "verified_ground_truth_rate_pct": round(100 * verified_exact / n, 2) if n else None,
        "execution_ready_n": len(execution_ready),
        "verified_ground_truth_rate_ready_pct": (
            round(100 * verified_exact / len(execution_ready), 2) if execution_ready else None
        ),
        "verified_alternative_n": verified_alternatives,
        "verified_incidental_migration_n": verified_incidental,
        "proven_target_infeasible_n": target_infeasible,
        "outcomes": outcomes,
        "result_categories": result_categories,
        "verified_alternative_runtime_efficiency_superior_n": verified_alternative_runtime_efficiency_superior,
        "verified_alternative_runtime_efficiency_unproven_n": verified_alternative_runtime_efficiency_unproven,
        "connector_invoked_n": len(connector_cases),
        "verification_environment_incomplete_n": len(environment_incomplete_cases),
        "verification_environment_incomplete_by_status": environment_incomplete_by_status,
        "verifier_clean_rate_pct": round(verifier_clean_rate * 100, 2) if verifier_clean_rate is not None else None,
        "verifier_n": len(verifier_cases),
        "repair_cases_n": len(repair_cases),
        "repair_cycle_total": repair_cycle_total,
        "repair_cycles_per_case": round(repair_cycle_total / n, 2) if n else 0.0,
        "repair_cycles_per_repaired_case": (
            round(repair_cycle_total / len(repair_cases), 2) if repair_cases else None
        ),
        "verified_after_repair_n": verified_after_repair_n,
        "repair_budget_exhausted_n": repair_budget_exhausted_n,
        "repair_routes": repair_routes,
        "final_verifier_routes": final_verifier_routes,
        "profiler_package_case_n": profiler_package_case_n,
        "runtime_measured_package_case_n": runtime_measured_package_case_n,
        "runtime_measurement_coverage_pct": (
            round(100 * runtime_measured_package_case_n / profiler_package_case_n, 2)
            if profiler_package_case_n else None
        ),
        "runtime_measured_probe_n": runtime_measured_probe_n,
        "runtime_probe_status_counts": runtime_probe_status_counts,
        "by_domain": {
            dom: {
                "n": d["n"],
                "detection_rate_pct": round(100 * d["detected"] / d["eval_detected"], 2) if d["eval_detected"] else None,
                "verified_ground_truth_rate_pct": round(100 * d["verified_exact"] / d["n"], 2) if d["n"] else None,
            }
            for dom, d in by_domain.items()
        },
    }


def print_summary(metrics: dict, full_pipeline_requested: bool = False) -> None:
    console.print()
    console.print(f"[bold cyan]═══ AXIOM vs PyMigBench — Evaluation Summary ═══[/bold cyan]")
    console.print(f"Total cases:                {metrics['total_cases']}")
    console.print(f"Terminal pipeline/provider failures: {metrics['errored_cases']}")
    console.print(
        f"[bold]Resolver detection rate:[/bold]     "
        f"{metrics['detection_rate_pct']}%  (n={metrics['detection_n']})"
    )
    if metrics["target_selection_rate_pct"] is not None:
        console.print(
            f"[bold]Target selection rate:[/bold]        "
            f"{metrics['target_selection_rate_pct']}%  (n={metrics['target_selection_n']}; selection only)"
        )
        console.print(
            f"[bold green]Verified exact PyMigBench migrations:[/bold green] "
            f"{metrics['verified_ground_truth_rate_pct']}%  "
            f"({metrics['verified_ground_truth_n']}/{metrics['total_cases']})"
        )
        console.print(
            f"[dim]Verification environments incomplete:[/dim] "
            f"{metrics['verification_environment_incomplete_n']}"
        )
        incomplete_by_status = metrics.get("verification_environment_incomplete_by_status", {})
        if incomplete_by_status:
            rendered = ", ".join(
                f"{status}={count}" for status, count in sorted(incomplete_by_status.items())
            )
            console.print(f"[dim]  Environment reasons:[/dim] {rendered}")
        console.print(
            f"[dim]Proven target-infeasible migrations:[/dim] "
            f"{metrics['proven_target_infeasible_n']}"
        )
        if metrics["verified_ground_truth_rate_ready_pct"] is not None:
            console.print(
                f"[dim]Verified exact among deterministic patch verdicts:[/dim] "
                f"{metrics['verified_ground_truth_rate_ready_pct']}%  "
                f"(n={metrics['execution_ready_n']})"
            )
        else:
            console.print(
                "[yellow]No selected patch reached a deterministic verification verdict; "
                "do not interpret environment-incomplete cases as failed migrations.[/yellow]"
            )
        console.print(
            f"[dim]Verified alternatives (not scored as exact target):[/dim] "
            f"{metrics['verified_alternative_n']}"
        )
        if metrics["verified_alternative_n"]:
            console.print(
                f"[dim]  Runtime-efficient with target-API evidence:[/dim] "
                f"{metrics['verified_alternative_runtime_efficiency_superior_n']}  "
                f"(import/memory or adapter-only: {metrics['verified_alternative_runtime_efficiency_unproven_n']})"
            )
        if metrics["verified_incidental_migration_n"]:
            console.print(
                f"[dim]Verified incidental migrations (different source role):[/dim] "
                f"{metrics['verified_incidental_migration_n']}"
            )
        console.print(f"[dim]Connector invoked on:[/dim]        {metrics['connector_invoked_n']} case(s)")
        console.print(
            f"[dim]Verifier repair cycles:[/dim]      {metrics['repair_cycle_total']} across "
            f"{metrics['repair_cases_n']} case(s)"
        )
        if metrics["repair_cases_n"]:
            console.print(
                f"[dim]Verified after repair:[/dim]       {metrics['verified_after_repair_n']}  "
                f"(budget exhausted: {metrics['repair_budget_exhausted_n']})"
            )
        if metrics["verifier_clean_rate_pct"] is not None:
            console.print(
                f"[dim]Verifier accepted (differential):[/dim] "
                f"{metrics['verifier_clean_rate_pct']}%  (n={metrics['verifier_n']})"
            )
    elif full_pipeline_requested:
        console.print(
            "[yellow]No source-aligned package decision was produced. Review the outcome table in the CSV; "
            "this is a resolver/pipeline result, not a successful migration.[/yellow]"
        )
    else:
        console.print("[dim]Full-pipeline accuracy:      not run (use --full-pipeline)[/dim]")

    if full_pipeline_requested and metrics["runtime_measurement_coverage_pct"] is not None:
        console.print(
            f"[dim]Real runtime-probe coverage:[/dim] {metrics['runtime_measurement_coverage_pct']}%  "
            f"({metrics['runtime_measured_package_case_n']}/{metrics['profiler_package_case_n']} "
            f"package-case profiles; {metrics['runtime_measured_probe_n']} measured probe(s))"
        )

    categories_table = Table(title="Publication Outcome Categories", header_style="bold green")
    categories_table.add_column("Category")
    categories_table.add_column("Cases", justify="right")
    category_labels = {
        "pymigbench_exact_verified": "Verified exact PyMigBench migration",
        "verified_alternative": "Verified alternative (see efficiency evidence)",
        "rejected_after_repair_budget": "Rejected after repair budget",
        "not_verified_or_not_adjudicated": "Not verified / not adjudicated",
    }
    for category in (
        "pymigbench_exact_verified",
        "verified_alternative",
        "rejected_after_repair_budget",
        "not_verified_or_not_adjudicated",
    ):
        count = metrics["result_categories"].get(category, 0)
        categories_table.add_row(category_labels[category], str(count))
    console.print(categories_table)

    outcomes_table = Table(title="Terminal Outcomes", header_style="bold cyan")
    outcomes_table.add_column("Outcome")
    outcomes_table.add_column("Cases", justify="right")
    for outcome, count in sorted(metrics["outcomes"].items()):
        outcomes_table.add_row(outcome, str(count))
    console.print(outcomes_table)

    table = Table(title="Detection / Verified Exact Rate by Domain", header_style="bold cyan")
    table.add_column("Domain")
    table.add_column("Cases", justify="right")
    table.add_column("Detection Rate", justify="right")
    table.add_column("Verified Exact", justify="right")
    for dom, d in sorted(metrics["by_domain"].items(), key=lambda x: -x[1]["n"]):
        rate = f"{d['detection_rate_pct']}%" if d["detection_rate_pct"] is not None else "—"
        verified = f"{d['verified_ground_truth_rate_pct']}%" if d["verified_ground_truth_rate_pct"] is not None else "—"
        table.add_row(dom, str(d["n"]), rate, verified)
    console.print(table)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate AXIOM against PyMigBench.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch-dataset", help="Clone the PyMigBench YAML dataset.")
    p_fetch.add_argument("dest")
    p_fetch.add_argument("--ref", default="main")

    p_run = sub.add_parser("run", help="Run the evaluation.")
    p_run.add_argument("dataset_dir", help="Path passed to fetch-dataset (or an existing PyMigBench checkout)")
    p_run.add_argument("--repo-cache", default="./pymigbench_repos", help="Where to cache cloned client repos")
    p_run.add_argument("--llm", default="ollama")
    p_run.add_argument("--model", default="qwen3-coder-next")
    p_run.add_argument("--max-cases", type=int, default=None)
    p_run.add_argument("--case-offset", type=int, default=0,
                       help="Skip this many eligible cases before applying --max-cases; use with batch runs to avoid overlap")
    p_run.add_argument("--case-filter", default="",
                       help="Run only cases whose migration ID, repo, source/target, or file path contains this text")
    p_run.add_argument("--full-pipeline", action="store_true",
                        help="Run the full resolver→verifier pipeline in disposable historical environments")
    p_run.add_argument("--seed-target", action="store_true",
                        help="With --full-pipeline: bypass resolver_agent's own discovery and seed the "
                             "known PyMigBench (source, target) pair directly, so the pipeline reaches "
                             "profiler/axiom/connector/verifier regardless of whether resolver would have "
                             "found the pair on its own. See run_pipeline_from_known_pair's docstring for "
                             "why this measures a different (and for most cases, more answerable) question "
                             "than plain --full-pipeline.")
    p_run.add_argument("--require-approval", action="store_true",
                        help="Exercise the same approval_gate/interrupt() code path `axiom run --feedback` "
                             "uses, auto-resumed with accept_all (same substitution --non-interactive makes "
                             "for CI). Only meaningful with --full-pipeline. Off by default.")
    p_run.add_argument("--compare-to-developer", action="store_true",
                        help="Replicate the 'Using LLMs for Library Migration' / MigrateLib papers' RQ1 "
                             "methodology exactly: migrate File_pre with AXIOM (seeded pipeline), then "
                             "compare the result against the developer's own File_dev at the level of "
                             "PyMigBench's recorded code changes. Overrides --seed-target/--full-pipeline "
                             "(always uses the seeded pipeline, since a developer-diff comparison needs "
                             "AXIOM to actually attempt the migration). See run_benchmark_comparison_case's "
                             "module note for what's automated vs. what the papers' manual-review step did "
                             "that this can't replicate.")
    p_run.add_argument("--auto-install", action="store_true",
                        help="Legacy host-environment installation for a non-isolated seeded run. "
                             "It is ignored when disposable historical environments are active.")
    security_flags = p_run.add_mutually_exclusive_group()
    security_flags.add_argument(
        "--enable-security", dest="enable_security", action="store_true",
        help="Resolve/scan exact compatible target releases (default).",
    )
    security_flags.add_argument(
        "--no-security", dest="enable_security", action="store_false",
        help="Disable target security/version resolution; isolated auto-target migrations will fail closed.",
    )
    p_run.set_defaults(enable_security=True)
    p_run.add_argument("--isolated-profiling", action="store_true",
                       help="Explicitly request disposable environments (automatic for full-pipeline evaluation).")
    p_run.add_argument("--out", default="./pymigbench_eval_report")

    args = parser.parse_args()

    if args.command == "fetch-dataset":
        fetch_dataset(Path(args.dest), args.ref)
        return

    # A PyMigBench result is only reproducible when historical dependencies,
    # candidate packages, and static tools are isolated from the developer's
    # own interpreter.  Do this by default for every execution-capable mode;
    # callers who bypass this module still fail closed in verifier_agent.
    if (args.full_pipeline or args.compare_to_developer) and not args.isolated_profiling:
        console.print("[dim]Enabling isolated profiling/verification for reproducible PyMigBench execution.[/dim]")
        args.isolated_profiling = True

    dataset_dir = Path(args.dataset_dir)
    yaml_root = dataset_dir / PYMIGBENCH_YAML_SUBDIR
    if not yaml_root.exists():
        yaml_root = fetch_dataset(dataset_dir)

    if args.compare_to_developer:
        collection_skips: list[dict[str, str]] = []
        cases = build_benchmark_cases(
            yaml_root, Path(args.repo_cache), max_cases=args.max_cases,
            case_offset=args.case_offset,
            case_filter=args.case_filter, collection_skips=collection_skips,
        )
        console.print(f"[green]Built {len(cases)} evaluable case(s) (with developer diff).[/green]")
        if collection_skips:
            console.print(f"[yellow]Collection skipped {len(collection_skips)} case(s); reasons will be in the report.[/yellow]")

        bresults: list[BenchmarkResult] = []
        t0 = time.time()
        for i, case in enumerate(cases, 1):
            console.print(f"[dim]→ [{i}/{len(cases)}] {case.repo}  {case.source}→{case.target}  ({case.file_path})[/dim]")
            r = run_benchmark_comparison_case(
                case,
                args.llm,
                args.model,
                auto_install=args.auto_install,
                enable_security=args.enable_security,
            )
            bresults.append(r)
            console.print(f"  [dim]{r.status}  ({r.matched_cc}/{r.total_cc} code changes matched)[/dim]"
                           + (f"  [yellow]{r.error}[/yellow]" if r.error else ""))

        elapsed = time.time() - t0
        console.print(f"\n[dim]Evaluation took {elapsed:.1f}s for {len(cases)} case(s).[/dim]")

        bmetrics = compute_benchmark_metrics(bresults)
        print_benchmark_summary(bmetrics)

        out_base = Path(args.out)
        out_base.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "config": {"llm": args.llm, "model": args.model, "max_cases": args.max_cases,
                       "case_offset": args.case_offset, "mode": "compare_to_developer",
                       "enable_security": args.enable_security,
                       "isolated_profiling": args.isolated_profiling},
            "metrics": bmetrics,
            "collection": {"built_cases": len(cases), "skipped_cases": collection_skips},
            "elapsed_seconds": elapsed,
        }
        Path(f"{out_base}.json").write_text(json.dumps(report, indent=2))

        with open(f"{out_base}.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "migration_id", "repo", "domain", "source", "target", "file_path",
                "status", "matched_cc", "total_cc", "cc_match_rate", "error",
            ])
            for r in bresults:
                writer.writerow([
                    r.case.migration_id, r.case.repo, r.case.domain, r.case.source, r.case.target,
                    r.case.file_path, r.status, r.matched_cc, r.total_cc,
                    r.cc_match_rate if r.cc_match_rate is not None else "", r.error or "",
                ])

        # Per-code-change detail file — this is what makes a manual review
        # pass (the papers' Step B) practical: one row per PyMigBench code
        # change, with the dev snippet right there to eyeball against the
        # unmatched result.
        with open(f"{out_base}_code_changes.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "migration_id", "file_path", "line_expr", "source_apis", "target_apis",
                "matched", "reason", "dev_snippet",
            ])
            for r in bresults:
                for m in r.cc_matches:
                    writer.writerow([
                        r.case.migration_id, r.case.file_path, m.line_expr,
                        ";".join(m.source_apis), ";".join(m.target_apis),
                        m.matched, m.reason, m.dev_snippet.replace("\n", " \\n "),
                    ])

        console.print(
            f"\n[bold green]✓ Report written:[/bold green] {out_base}.json, {out_base}.csv, "
            f"{out_base}_code_changes.csv"
        )
        return

    collection_skips: list[dict[str, str]] = []
    cases = build_eval_cases(
        yaml_root, Path(args.repo_cache), max_cases=args.max_cases,
        case_offset=args.case_offset,
        case_filter=args.case_filter, collection_skips=collection_skips,
    )
    console.print(f"[green]Built {len(cases)} evaluable case(s).[/green]")
    if collection_skips:
        console.print(f"[yellow]Collection skipped {len(collection_skips)} case(s); reasons will be in the report.[/yellow]")

    results: list[EvalResult] = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        console.print(f"[dim]→ [{i}/{len(cases)}] {case.repo}  {case.source}→{case.target}  ({case.file_path})[/dim]")
        if args.full_pipeline and args.seed_target:
            r = run_pipeline_from_known_pair(
                case, args.llm, args.model,
                auto_install=args.auto_install, enable_security=args.enable_security,
                isolated_profiling=args.isolated_profiling,
            )
        elif args.full_pipeline:
            r = run_full_pipeline(
                case, args.llm, args.model,
                require_approval=args.require_approval, enable_security=args.enable_security,
                isolated_profiling=args.isolated_profiling,
            )
        else:
            r = run_detection_only(case, args.llm, args.model)
        results.append(r)
        if r.error:
            console.print(f"  [yellow]{r.outcome}: {r.error}[/yellow]")

    elapsed = time.time() - t0
    console.print(f"\n[dim]Evaluation took {elapsed:.1f}s for {len(cases)} case(s).[/dim]")

    metrics = compute_metrics(results)
    print_summary(metrics, full_pipeline_requested=args.full_pipeline)

    out_base = Path(args.out)
    out_base.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "config": {
            "llm": args.llm, "model": args.model,
            "full_pipeline": args.full_pipeline, "seed_target": args.seed_target,
            "require_approval": args.require_approval,
            "auto_install": args.auto_install, "enable_security": args.enable_security,
            "isolated_profiling": args.isolated_profiling,
            "verification_scope": (
                "file-level component runtime slice: root metadata plus the migrated "
                "component's metadata; unrelated monorepo components are excluded"
            ),
            "max_cases": args.max_cases,
            "case_offset": args.case_offset,
            "case_filter": args.case_filter,
        },
        "metrics": metrics,
        "collection": {"built_cases": len(cases), "skipped_cases": collection_skips},
        "outcome_definitions": {
            "ground_truth_verified": "AXIOM selected PyMigBench's target and the final patch passed differential verification.",
            "verified_alternative": "AXIOM selected a different target for the same source and the final patch passed verification; this is not automatically a better migration.",
            "verified_incidental_migration": "A verified change affected a different source role in the same file and is not credited to this benchmark pair.",
            "environment_incomplete": "The historical component-level base/target environment or required verifier tooling could not be reproduced; not accepted as success.",
            "target_infeasible": "The verifier explicitly excluded PyMigBench's target as structurally incompatible with the source role; this is not inferred from an installation conflict.",
            "verification_rejected": "A selected migration failed a deterministic verifier guard or retry budget; not accepted as success.",
        },
        "result_category_definitions": {
            "pymigbench_exact_verified": "AXIOM produced and verified the same target migration recorded by PyMigBench.",
            "verified_alternative": "AXIOM verified a different target for the same source role. A runtime-efficiency claim additionally requires actual selected-target API use and one shared measured semantic runtime probe across source, reference target, and alternative.",
            "rejected_after_repair_budget": "A selected migration remained unaccepted after the globally bounded verifier repair budget was exhausted.",
            "not_verified_or_not_adjudicated": "No verified migration claim is made. category_reason and detailed outcome distinguish no-change, environment, resolver, provider, and other causes.",
        },
        "evidence_definitions": {
            "repair_history": "Structured verifier feedback for each real repair cycle; route identifies the repair agent asked to act.",
            "profiler_evidence": "Whitelisted import/memory values and runtime-probe result/provenance. Only runtime probes with status 'measured' count toward runtime coverage; unsupported, timeout, and failed probes are retained as explicit statuses.",
            "adapter_runtime": "Optional timing of a recognised, side-effect-free Connector wrapper. scope='adapter_only' measures generated application glue and is never used as selected-package runtime evidence.",
            "environment_plan": "Read-only historical-environment plan. It records the selected compatible local Python or explicit interpreter diagnostics; target_version='auto' means planning did not invent or fetch a pin. Candidate Security may later record an exact selected target under target_releases and resolved_target_version.",
        },
        "elapsed_seconds": elapsed,
        "results": [
            {
                "migration_id": r.case.migration_id,
                "repo": r.case.repo,
                "domain": r.case.domain,
                "source": r.case.source,
                "target": r.case.target,
                "file_path": r.case.file_path,
                "verification_scope": "component_runtime",
                "base_requirements": r.case.base_requirements,
                "base_requirement_count": len(r.case.base_requirements),
                "base_requirement_recovery_errors": r.case.base_requirement_recovery_errors,
                "detected_group": r.detected_group,
                "recommended_target": r.recommended_target,
                "scoreable_full_pipeline": r.scoreable_full_pipeline,
                "axiom_winner": r.axiom_winner,
                "attempted_axiom_winner": r.attempted_axiom_winner,
                "attempted_selection_status": r.attempted_selection_status,
                "attempted_selection_count": r.attempted_selection_count,
                "connector_ran": r.connector_ran,
                "connector_ever_ran": r.connector_ever_ran,
                "verifier_route": r.verifier_route,
                "repair_cycle_count": r.repair_cycle_count,
                "repair_history": r.repair_history,
                "final_verifier_feedback": r.final_verifier_feedback,
                "profiler_evidence": r.profiler_evidence,
                "environment_plan": r.environment_plan,
                "verifier_passed_clean": r.verifier_passed_clean,
                "target_infeasibility_evidence": r.target_infeasibility_evidence,
                "patch_changed": r.patch_changed,
                "verification_status": r.verification_status,
                "verification_environment_status": r.verification_environment_status,
                "selection_status": r.selection_status,
                "outcome": r.outcome,
                "verified_ground_truth": r.verified_ground_truth,
                "verified_alternative": r.verified_alternative,
                "result_category": r.result_category,
                "category_reason": r.category_reason,
                "alternative_efficiency": r.alternative_efficiency,
                "adapter_runtime": r.adapter_runtime,
                "repair_budget_exhausted": r.repair_budget_exhausted,
                "error": r.error,
            }
            for r in results
        ],
    }
    Path(f"{out_base}.json").write_text(json.dumps(report, indent=2))

    with open(f"{out_base}.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "migration_id", "repo", "domain", "source", "target", "file_path", "verification_scope", "base_requirement_count", "base_requirement_recovery_errors",
            "detected_group", "recommended_target", "axiom_winner", "attempted_axiom_winner",
            "attempted_selection_status", "attempted_selection_count",
            "connector_ran", "connector_ever_ran", "verifier_route", "repair_cycle_count", "repair_history",
            "final_verifier_feedback", "profiler_package_count", "runtime_measured_package_count",
            "runtime_measurement_coverage_pct", "runtime_measured_probe_count", "runtime_probe_status_counts",
            "environment_plan_status", "environment_backend", "environment_selected_python",
            "environment_selected_python_version", "environment_detected_python", "environment_target_version",
            "environment_resolved_target_version",
            "environment_plan_diagnostics",
            "verifier_passed_clean", "target_infeasibility_evidence", "patch_changed",
            "verification_status", "verification_environment_status", "selection_status", "outcome",
            "result_category", "category_reason", "alternative_efficiency", "adapter_runtime", "repair_budget_exhausted",
            "verified_ground_truth", "verified_alternative", "error",
        ])
        for r in results:
            runtime_stats = _runtime_evidence_stats(r.profiler_evidence)
            writer.writerow([
                r.case.migration_id, r.case.repo, r.case.domain, r.case.source,
                r.case.target, r.case.file_path, "component_runtime", len(r.case.base_requirements),
                " | ".join(r.case.base_requirement_recovery_errors), r.detected_group,
                r.recommended_target, r.axiom_winner, r.attempted_axiom_winner,
                r.attempted_selection_status, r.attempted_selection_count,
                r.connector_ran, r.connector_ever_ran, r.verifier_route, r.repair_cycle_count,
                json.dumps(r.repair_history, sort_keys=True),
                json.dumps(r.final_verifier_feedback, sort_keys=True),
                runtime_stats["package_count"], runtime_stats["measured_package_count"],
                runtime_stats["measurement_coverage_pct"], runtime_stats["measured_probe_count"],
                json.dumps(runtime_stats["probe_status_counts"], sort_keys=True),
                r.environment_plan.get("interpreter_status", "not_planned"),
                r.environment_plan.get("environment_backend") or "",
                r.environment_plan.get("selected_python") or "",
                r.environment_plan.get("selected_python_version") or "",
                r.environment_plan.get("detected_python") or "",
                r.environment_plan.get("target_version") or "",
                r.environment_plan.get("resolved_target_version") or "",
                json.dumps({
                    "python_evidence": r.environment_plan.get("python_evidence", []),
                    "warnings": r.environment_plan.get("warnings", []),
                    "recovery_hints": r.environment_plan.get("recovery_hints", []),
                    "target_releases": r.environment_plan.get("target_releases", {}),
                }, sort_keys=True),
                r.verifier_passed_clean, json.dumps(r.target_infeasibility_evidence, sort_keys=True), r.patch_changed,
                r.verification_status, r.verification_environment_status, r.selection_status, r.outcome,
                r.result_category, r.category_reason, json.dumps(r.alternative_efficiency, sort_keys=True),
                json.dumps(r.adapter_runtime, sort_keys=True), r.repair_budget_exhausted,
                r.verified_ground_truth, r.verified_alternative, r.error or "",
            ])

    console.print(f"\n[bold green]✓ Report written:[/bold green] {out_base}.json, {out_base}.csv")


if __name__ == "__main__":
    main()
