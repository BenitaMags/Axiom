"""Evidence-backed planning for reproducible Python migration environments.

The planner is deliberately side-effect free: it reads project metadata and
discovers locally available interpreters, but never downloads a Python runtime,
installs a package, or mutates the project.  Installation happens later in the
Profiler/Verifier using the explicit plan it produced.

This separation matters for historical projects.  Falling back silently from a
declared Python 3.7 project to the operator's Python 3.12 makes a failed
baseline look like a failed migration.  A plan therefore either identifies a
compatible local interpreter or says exactly why it cannot do so.
"""

from __future__ import annotations

import ast
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


_VERSION_RE = re.compile(r"(?<!\d)([23])\.(\d{1,2})(?!\d)")
_PYTHON_COMMANDS = tuple(
    f"python{major}.{minor}" for major in (3, 2) for minor in range(13, 4, -1)
) + ("python3", "python")


@dataclass
class EnvironmentPlan:
    """A serializable description of how AXIOM should reproduce one project."""

    project_root: str
    requested_python: str = "auto"
    detected_python: str = ""
    python_constraint: str = ""
    python_evidence: list[str] = field(default_factory=list)
    selected_python: str = ""
    selected_python_version: str = ""
    interpreter_status: str = "ready"  # ready | interpreter_unavailable | interpreter_incompatible
    environment_backend: str = "local"  # local | docker_recommended | unavailable
    requirement_files: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    target_distribution: str = ""
    target_version: str = "auto"
    target_requirement: str = ""
    test_command: str = ""
    recovery_hints: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.interpreter_status == "ready" and bool(self.selected_python)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["usable"] = self.usable
        return data


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _first_version(text: str) -> str:
    match = _VERSION_RE.search(text)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _normalise_python_constraint(raw: str, *, exact_minor: bool = False) -> str:
    """Convert common project metadata spellings into PEP 440 specifiers.

    Poetry accepts convenient ``^3.8``/``~3.8`` forms whereas
    :class:`packaging.specifiers.SpecifierSet` does not.  Metadata such as a
    ``.python-version`` file describes one interpreter minor, not an open
    requirement, so it is represented by a minor-wide range.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    version = _first_version(text)
    if text.startswith("^") and version:
        major = int(version.split(".")[0])
        return f">={version},<{major + 1}.0"
    # ``~=`` is already valid PEP 440 syntax.  It is *not* Poetry's
    # shorthand ``~``: for example, ``~=3.8`` permits Python <4.0, whereas
    # Poetry's ``~3.8`` means <3.9.  Leaving the PEP 440 form intact avoids
    # falsely declaring a compatible modern local interpreter unavailable.
    if text.startswith("~="):
        return text
    if text.startswith("~") and version:
        major, minor = version.split(".")
        return f">={version},<{major}.{int(minor) + 1}"
    if re.fullmatch(r"[23]\.\d{1,2}(?:\.\d+)?", text) and version:
        major, minor = version.split(".")
        return f">={major}.{minor},<{major}.{int(minor) + 1}"
    if exact_minor and version:
        major, minor = version.split(".")
        return f">={major}.{minor},<{major}.{int(minor) + 1}"
    return text


def _append_version(
    candidates: list[tuple[str, str, str]], version: str, constraint: str, evidence: str,
) -> None:
    if version:
        candidates.append((version, constraint, evidence))


def _python_metadata(root: Path) -> tuple[str, str, list[str], list[str]]:
    """Return preferred minor, constraint, evidence, and warnings from metadata.

    The first usable declaration is the project contract.  Lower-priority
    declarations are retained as provenance and flagged if they conflict;
    AXIOM does not quietly guess its way through contradictory history.
    """
    candidates: list[tuple[str, str, str]] = []
    warnings: list[str] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read_text(pyproject))
            project_spec = str(data.get("project", {}).get("requires-python", ""))
            poetry_spec = str(
                data.get("tool", {}).get("poetry", {}).get("dependencies", {}).get("python", "")
            )
            for spec, label in ((project_spec, "pyproject project.requires-python"),
                                (poetry_spec, "pyproject Poetry python")):
                version = _first_version(spec)
                if version:
                    _append_version(
                        candidates, version, _normalise_python_constraint(spec), f"{label}: {spec}",
                    )
        except (tomllib.TOMLDecodeError, TypeError) as exc:
            warnings.append(f"Cannot parse pyproject.toml Python requirement: {exc}")

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read_string(_read_text(setup_cfg))
            spec = parser.get("options", "python_requires", fallback="")
            version = _first_version(spec)
            if version:
                _append_version(
                    candidates, version, _normalise_python_constraint(spec),
                    f"setup.cfg python_requires: {spec}",
                )
        except configparser.Error as exc:
            warnings.append(f"Cannot parse setup.cfg Python requirement: {exc}")

    setup_py = root / "setup.py"
    if setup_py.is_file():
        text = _read_text(setup_py)
        match = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", text)
        if match:
            version = _first_version(match.group(1))
            if version:
                _append_version(
                    candidates, version, _normalise_python_constraint(match.group(1)),
                    f"setup.py python_requires: {match.group(1)}",
                )

    for name in (".python-version", "runtime.txt"):
        path = root / name
        if path.is_file():
            text = _read_text(path).strip().splitlines()
            version = _first_version(text[0] if text else "")
            if version:
                _append_version(
                    candidates, version, _normalise_python_constraint(text[0], exact_minor=True),
                    f"{name}: {text[0]}",
                )

    tox = root / "tox.ini"
    if tox.is_file():
        text = _read_text(tox)
        # tox environments commonly spell this py37 / py310.
        for token in re.findall(r"\bpy(2|3)(\d{1,2})\b", text):
            major, minor = token
            version = f"{major}.{minor}"
            _append_version(
                candidates, version, _normalise_python_constraint(version, exact_minor=True),
                f"tox.ini envlist: py{major}{minor}",
            )
            break

    dockerfile = root / "Dockerfile"
    if dockerfile.is_file():
        match = re.search(r"^\s*FROM\s+python:([0-9]+\.[0-9]+)", _read_text(dockerfile), re.MULTILINE | re.IGNORECASE)
        if match:
            version = match.group(1)
            _append_version(
                candidates, version, _normalise_python_constraint(version, exact_minor=True),
                f"Dockerfile Python image: {version}",
            )

    workflow_dir = root / ".github" / "workflows"
    if workflow_dir.is_dir():
        for workflow in sorted(workflow_dir.glob("*.y*ml")):
            match = re.search(r"python-version\s*:\s*['\"]?([23]\.\d+)", _read_text(workflow))
            if match:
                version = match.group(1)
                _append_version(
                    candidates, version, _normalise_python_constraint(version, exact_minor=True),
                    f"{workflow.relative_to(root)} python-version: {version}",
                )
                break

    # An old source file may not parse under the host interpreter.  This is
    # only a recovery hint, not an inferred exact version.
    inspected_sources = 0
    for source in root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "site-packages"} for part in source.parts):
            continue
        inspected_sources += 1
        if inspected_sources > 250:
            break
        text = _read_text(source)
        if re.search(r"^\s*except\s+[^:]+,\s*\w+\s*:", text, re.MULTILINE) or re.search(r"^\s*print\s+[^ (]", text, re.MULTILINE):
            warnings.append(f"Python-2-style syntax detected in {source.relative_to(root)}; use a Python 2 container/interpreter.")
            _append_version(candidates, "2.7", ">=2.7,<3", "legacy Python-2 syntax evidence")
            break

    if not candidates:
        return "", "", [], warnings
    # Metadata nearest the project contract has priority.  Preserve all
    # evidence so a user can see conflicting declarations rather than hiding it.
    preferred, constraint, _ = candidates[0]
    try:
        primary = SpecifierSet(constraint) if constraint else SpecifierSet()
    except InvalidSpecifier:
        warnings.append(f"Cannot interpret Python constraint '{constraint}'; use an explicit --python interpreter.")
        primary = SpecifierSet()
    for _version, other_constraint, evidence in candidates[1:]:
        try:
            if other_constraint and not primary.contains(Version(_version), prereleases=True):
                warnings.append(f"Conflicting Python evidence: {evidence}")
        except (InvalidSpecifier, InvalidVersion):
            warnings.append(f"Cannot interpret Python evidence: {evidence}")
    return preferred, constraint, [evidence for _version, _constraint, evidence in candidates], warnings


def _interpreter_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "-c", "import sys; print('%s.%s' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _uv_managed_interpreter_paths() -> list[str]:
    """Discover Python executables ``uv`` has already installed.

    Read-only: lists uv's managed Python directory, it does not install
    anything. ``uv python install`` places standalone CPython builds under
    its own data dir, outside ``_PYTHON_COMMANDS``'s PATH-based search, so a
    version provisioned by ``provision_interpreter`` (below) on a previous
    run would otherwise never be found again by this discovery step.
    """
    uv = shutil.which("uv")
    if not uv:
        return []
    try:
        completed = subprocess.run(
            [uv, "python", "list", "--only-installed"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and os.path.isfile(parts[-1]):
            paths.append(parts[-1])
    return paths


def available_interpreters() -> dict[str, str]:
    """Return {major.minor: executable} for local interpreters only."""
    candidates = [sys.executable]
    candidates.extend(shutil.which(name) or "" for name in _PYTHON_COMMANDS)
    candidates.extend(_uv_managed_interpreter_paths())
    found: dict[str, str] = {}
    for executable in candidates:
        if not executable or not os.path.isfile(executable):
            continue
        version = _interpreter_version(executable)
        if version:
            found.setdefault(version, executable)
    return found


def provision_interpreter(version: str, *, timeout: int = 300) -> str | None:
    """Install a missing historical CPython minor via ``uv``, on request.

    This is the one deliberate exception to this module's side-effect-free
    contract described in the module docstring: planning itself never
    installs anything by default. A caller that has already concluded
    verification cannot proceed without a specific interpreter (i.e.
    ``interpreter_status == "interpreter_unavailable"``) may call this
    explicitly — or pass ``auto_provision_interpreter=True`` to
    ``plan_project_environment`` — to try to recover, the same way
    ``eval/pymigbench_eval.py``'s ``ensure_pip_installed`` does a best-effort
    install for packages rather than leaving the profiler to report every
    uninstalled candidate as unavailable.

    Best-effort and silent on failure: some historical minors (Python 2.x,
    and 3.x releases that predate python-build-standalone's coverage) are
    not published as standalone builds uv can fetch, and must instead fall
    back to the Docker recovery path (``environment_backend ==
    "docker_recommended"``) or a manually installed interpreter passed via
    ``--python``.

    Returns the resolved interpreter path, or None if ``uv`` is unavailable
    or the install did not succeed.
    """
    uv = shutil.which("uv")
    if not uv:
        return None
    try:
        install = subprocess.run(
            [uv, "python", "install", version],
            capture_output=True, text=True, timeout=timeout,
        )
        if install.returncode != 0:
            return None
        found = subprocess.run(
            [uv, "python", "find", version],
            capture_output=True, text=True, timeout=10,
        )
        if found.returncode != 0:
            return None
        path = found.stdout.strip()
        return path if path and os.path.isfile(path) else None
    except (OSError, subprocess.SubprocessError):
        return None


def _docker_image_candidates(version: str) -> list[str]:
    """Official Docker Hub ``python`` image tags to try for a historical minor.

    ``-slim`` first (smaller, faster to pull); the full tag as a fallback in
    case a given minor never published a slim variant. Both are attempted by
    ``provision_docker_interpreter`` — old, EOL minors (2.x, early 3.x) are
    not guaranteed to still be pullable, so this is a list of things to try,
    not a claim that they exist.
    """
    version = version.strip()
    return [f"python:{version}-slim", f"python:{version}"]


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        completed = subprocess.run(
            [docker, "info"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def provision_docker_interpreter(version: str, *, timeout: int = 600) -> str | None:
    """Confirm a historical CPython minor is obtainable as a container image.

    The second, heavier exception to this module's side-effect-free
    contract (the first is ``provision_interpreter``, uv-based). This is
    specifically for minors ``provision_interpreter`` cannot reach at all —
    principally Python 2.x, which predates ``python-build-standalone``'s
    coverage by construction, so no standalone-build fetch will ever find
    it. Docker Hub's official ``python`` image still publishes (or keeps
    old tags of) 2.7 and most early 3.x minors, which is the only route
    left to actually *executing* — not merely acknowledging — one of these
    projects.

    Best-effort and silent on failure, same convention as
    ``provision_interpreter``: no local ``docker`` binary, a daemon that
    isn't running, or no matching image tag all fall back to
    ``interpreter_status="interpreter_unavailable"`` rather than raising.

    Returns a ``docker://<image>`` sentinel (not a filesystem path) on
    success — ``profiler_agent.py``'s ``_get_python``/
    ``_create_disposable_venv`` recognize this prefix and route every
    subprocess call for that case through ``docker run`` instead of a local
    executable. Returns ``None`` if no candidate image could be pulled.
    """
    docker = shutil.which("docker")
    if not docker or not _docker_available():
        return None
    for image in _docker_image_candidates(version):
        try:
            pulled = subprocess.run(
                [docker, "pull", "--quiet", image],
                capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if pulled.returncode == 0:
            return f"docker://{image}"
    return None


def _resolve_requested_interpreter(requested: str, available: dict[str, str]) -> tuple[str, str]:
    requested = (requested or "auto").strip()
    if requested.casefold() == "auto":
        return "", ""
    if os.path.isfile(requested):
        return requested, _interpreter_version(requested)
    if requested in available:
        return available[requested], requested
    command = shutil.which(requested)
    if command:
        return command, _interpreter_version(command)
    return "", ""


def _compatible_local_interpreter(
    constraint: str, available: dict[str, str], preferred_version: str,
) -> tuple[str, str]:
    """Choose the closest locally available historical interpreter.

    For a broad historical lower bound (``>=3.7``), the oldest compatible
    local interpreter is safer than silently jumping to the host's newest
    version: old pins frequently stop installing on newer Python releases.
    A narrow range still resolves to the highest compatible minor in that
    range when the exact preferred minor is unavailable.
    """
    try:
        specifier = SpecifierSet(constraint) if constraint else SpecifierSet()
    except InvalidSpecifier:
        return "", ""
    compatible: list[tuple[Version, str, str]] = []
    for version, executable in available.items():
        try:
            parsed = Version(version)
        except InvalidVersion:
            continue
        if specifier.contains(parsed, prereleases=True):
            compatible.append((parsed, version, executable))
    if not compatible:
        return "", ""
    for _parsed, version, executable in compatible:
        if version == preferred_version:
            return executable, version
    compatible.sort(key=lambda item: item[0])
    _parsed, version, executable = compatible[0]
    return executable, version


def _target_requirement(distribution: str, version: str) -> str:
    if not distribution:
        return ""
    normalized = (version or "auto").strip()
    if normalized.casefold() in {"", "auto", "latest", "latest-compatible"}:
        return distribution
    if normalized.startswith(("==", ">=", "<=", "~=", ">", "<")):
        return f"{distribution}{normalized}"
    return f"{distribution}=={normalized}"


def _requirements_from_files(paths: Iterable[Path], *, root: Path) -> tuple[list[str], list[str]]:
    """Read runtime requirements and in-tree ``-r``/``-c`` includes safely.

    A requirements file is often merely an entrypoint to a pinned constraints
    file.  Ignoring those includes causes AXIOM to verify against an
    accidental modern dependency set.  Includes are permitted only inside
    the project root; editable, local, and remote entries are intentionally
    left to an explicit user-provided environment instead of being replayed
    implicitly.
    """
    specs: list[str] = []
    warnings: list[str] = []
    seen: set[Path] = set()

    def _include_path(parent: Path, raw: str) -> Path | None:
        text = raw.strip()
        if not text or "://" in text or text.startswith(("/", "~")):
            return None
        candidate = (parent / text).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _read(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        for raw in _read_text(resolved).splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            include = ""
            if line.startswith("-r ") or line.startswith("--requirement "):
                include = line.split(maxsplit=1)[1]
            elif line.startswith("--requirement="):
                include = line.split("=", 1)[1]
            elif line.startswith("-c ") or line.startswith("--constraint "):
                include = line.split(maxsplit=1)[1]
            elif line.startswith("--constraint="):
                include = line.split("=", 1)[1]
            if include:
                child = _include_path(resolved.parent, include)
                if child is None:
                    warnings.append(f"Cannot safely read requirement include '{include}' from {resolved.name}.")
                else:
                    _read(child)
                continue
            if line.startswith(("-e ", "--editable", "--", "-f ", ".", "/")):
                continue
            specs.append(line)

    for path in paths:
        _read(path)
    return list(dict.fromkeys(specs)), list(dict.fromkeys(warnings))


def _poetry_requirement(package: str, constraint: object, origin: str) -> tuple[str | None, str | None]:
    """Convert the common Poetry dependency forms to a pip requirement."""
    extras: list[str] = []
    marker = ""
    if isinstance(constraint, str):
        version = constraint.strip()
    elif isinstance(constraint, dict):
        if any(key in constraint for key in ("path", "git", "url")):
            return None, f"Non-registry dependency {package} in {origin} needs an explicit environment."
        version = str(constraint.get("version", "")).strip()
        raw_extras = constraint.get("extras", [])
        if raw_extras and not isinstance(raw_extras, list):
            return None, f"Unsupported extras for {package} in {origin}."
        extras = [str(extra) for extra in raw_extras]
        marker = str(constraint.get("markers", constraint.get("marker", ""))).strip()
    else:
        return None, f"Unsupported dependency declaration for {package} in {origin}."

    if "||" in version:
        return None, f"Alternative Poetry constraint for {package} in {origin} needs an explicit requirements file."
    if version in {"", "*"}:
        specifier = ""
    elif version.startswith("^"):
        try:
            parsed = Version(version[1:].strip())
        except InvalidVersion:
            return None, f"Cannot interpret Poetry constraint {version!r} for {package} in {origin}."
        release = parsed.release + (0, 0, 0)
        major, minor, patch = release[:3]
        upper = (
            f"<{major + 1}.0.0" if major else
            (f"<0.{minor + 1}.0" if minor else f"<0.0.{patch + 1}")
        )
        specifier = f">={parsed},{upper}"
    elif version.startswith("~") and not version.startswith("~="):
        try:
            parsed = Version(version[1:].strip())
        except InvalidVersion:
            return None, f"Cannot interpret Poetry constraint {version!r} for {package} in {origin}."
        release = parsed.release
        upper = f"<{release[0] + 1}.0" if len(release) == 1 else f"<{release[0]}.{release[1] + 1}"
        specifier = f">={parsed},{upper}"
    elif re.fullmatch(r"\d+(?:\.\d+){0,2}", version):
        specifier = f"=={version}"
    else:
        specifier = version

    name = package + ("[" + ",".join(extras) + "]" if extras else "")
    candidate = f"{name}{specifier}" + (f"; {marker}" if marker else "")
    try:
        Requirement(candidate)
    except InvalidRequirement:
        return None, f"Cannot turn {package}'s dependency declaration in {origin} into a pip requirement."
    return candidate, None


def _project_metadata_requirements(root: Path) -> tuple[list[str], list[str]]:
    """Recover static runtime dependencies from standard packaging metadata."""
    specs: list[str] = []
    warnings: list[str] = []

    def _append(spec: str, origin: str) -> None:
        try:
            Requirement(spec)
        except InvalidRequirement:
            warnings.append(f"Cannot replay dependency {spec!r} from {origin}.")
            return
        specs.append(spec)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read_text(pyproject))
            for spec in data.get("project", {}).get("dependencies", []) or []:
                if isinstance(spec, str):
                    _append(spec, "pyproject.toml [project]")
            for package, constraint in (data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}).items():
                if str(package).casefold() == "python":
                    continue
                spec, warning = _poetry_requirement(str(package), constraint, "pyproject.toml [tool.poetry]")
                if spec:
                    _append(spec, "pyproject.toml [tool.poetry]")
                if warning:
                    warnings.append(warning)
        except (tomllib.TOMLDecodeError, TypeError) as exc:
            warnings.append(f"Cannot parse project dependency metadata in pyproject.toml: {exc}")

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read_string(_read_text(setup_cfg))
            for spec in parser.get("options", "install_requires", fallback="").splitlines():
                if spec.strip():
                    _append(spec.strip(), "setup.cfg [options].install_requires")
        except configparser.Error as exc:
            warnings.append(f"Cannot parse setup.cfg dependency metadata: {exc}")

    setup_py = root / "setup.py"
    if setup_py.is_file():
        try:
            tree = ast.parse(_read_text(setup_py))
            named_lists: dict[str, list[str]] = {}
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                    continue
                values = [elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and values:
                        named_lists[target.id] = values
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or getattr(node.func, "id", getattr(node.func, "attr", "")) != "setup":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "install_requires":
                        continue
                    values = named_lists.get(keyword.value.id, []) if isinstance(keyword.value, ast.Name) else [
                        elt.value for elt in getattr(keyword.value, "elts", [])
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
                    for spec in values:
                        _append(spec, "setup.py install_requires")
        except SyntaxError as exc:
            warnings.append(f"Cannot parse setup.py dependency metadata: {exc}")

    return list(dict.fromkeys(specs)), list(dict.fromkeys(warnings))


def discover_requirement_files(root: Path, explicit: Iterable[str] | None = None) -> list[Path]:
    """Find root-level runtime requirement files without sweeping a monorepo."""
    root = root.resolve()
    selected: list[Path] = []
    if explicit:
        for item in explicit:
            path = Path(item)
            if not path.is_absolute():
                path = root / path
            try:
                path.resolve().relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                selected.append(path.resolve())
        return list(dict.fromkeys(selected))

    for name in ("requirements.txt", "requirements-prod.txt", "requirements-production.txt", "requirements-base.txt"):
        path = root / name
        if path.is_file():
            selected.append(path.resolve())
    requirements_dir = root / "requirements"
    if requirements_dir.is_dir():
        for path in sorted(requirements_dir.glob("*.txt")):
            if not any(token in path.name.casefold() for token in ("dev", "test", "docs", "lint")):
                selected.append(path.resolve())
    return list(dict.fromkeys(selected))


def plan_project_environment(
    project_root: str | Path,
    *,
    requested_python: str = "auto",
    requirement_files: Iterable[str] | None = None,
    base_requirements: Iterable[str] | None = None,
    target_distribution: str = "",
    target_version: str = "auto",
    test_command: str = "",
    auto_provision_interpreter: bool = False,
) -> EnvironmentPlan:
    """Build an evidence-rich project environment plan.

    Side-effect-free by default. Pass ``auto_provision_interpreter=True`` to
    allow the one documented exception: when no local interpreter satisfies
    the project's declared Python version, attempt ``provision_interpreter``
    (installs a standalone build via ``uv``) before giving up and returning
    ``interpreter_status="interpreter_unavailable"``. Off by default so a
    plain call to this function still never touches the network or disk
    beyond reading the project.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return EnvironmentPlan(
            project_root=str(root), requested_python=requested_python,
            interpreter_status="interpreter_unavailable", environment_backend="unavailable",
            warnings=[f"Project directory does not exist: {root}"],
        )

    detected, python_constraint, evidence, warnings = _python_metadata(root)
    available = available_interpreters()
    requested_label = str(requested_python or "auto").strip() or "auto"
    explicit_requested = requested_label.casefold() != "auto"
    explicit_executable, explicit_version = _resolve_requested_interpreter(requested_label, available)
    selected = explicit_executable
    selected_version = explicit_version
    explicit_incompatible = False
    selected_via_docker = False

    # An explicit ``--python`` is a reproducibility contract.  It must never
    # degrade into an implicit host-Python selection merely because its path
    # is misspelled, non-executable, or cannot report a version.
    if explicit_requested and (not selected or not selected_version):
        selected = ""
        selected_version = ""
        warnings.append(
            f"Requested Python interpreter could not be resolved or executed: {requested_label}."
        )
    elif not selected:
        if detected:
            selected, selected_version = _compatible_local_interpreter(
                python_constraint, available, detected,
            )
            # No locally installed interpreter satisfies this historical
            # project. Previously this was a dead end reported only as
            # "Docker is available for an isolated fallback" — a hint
            # nothing in the codebase ever acted on. Python 2 is excluded
            # from the uv attempt: it has no standalone-build coverage, so
            # only the container path below can ever reach it.
            if not selected and auto_provision_interpreter and not detected.startswith("2."):
                provisioned = provision_interpreter(detected)
                if provisioned:
                    provisioned_version = _interpreter_version(provisioned)
                    if provisioned_version:
                        selected, selected_version = provisioned, provisioned_version
                        warnings.append(
                            f"No local interpreter satisfied Python {python_constraint or detected}; "
                            f"auto-provisioned {provisioned_version} via uv for this historical project."
                        )
            # Second, heavier fallback: uv either isn't applicable (Python 2)
            # or didn't have this minor's build available at all. Docker is
            # the only remaining route to actually running one of these
            # projects rather than just labeling it unreachable.
            if not selected and auto_provision_interpreter:
                docker_image = provision_docker_interpreter(detected)
                if docker_image:
                    selected, selected_version = docker_image, detected
                    selected_via_docker = True
                    warnings.append(
                        f"No local interpreter satisfied Python {python_constraint or detected}; "
                        f"auto-provisioned a {docker_image.removeprefix('docker://')} container for this historical project."
                    )
        elif not detected:
            # No historical metadata is not an error; the current interpreter
            # is the only honest default for an ordinary user project.
            selected_version = _interpreter_version(sys.executable)
            selected = sys.executable if selected_version else ""
    elif python_constraint:
        try:
            explicit_incompatible = not SpecifierSet(python_constraint).contains(
                Version(selected_version), prereleases=True,
            )
        except (InvalidSpecifier, InvalidVersion):
            # The diagnostic was already retained above; an explicitly
            # requested interpreter is still the least surprising choice.
            explicit_incompatible = False

    status = "interpreter_incompatible" if explicit_incompatible else ("ready" if selected else "interpreter_unavailable")
    # AXIOM's local venv path provisions sandboxes with ``python -m venv``,
    # which a local Python 2 interpreter cannot run (venv is Python 3.3+
    # only) — treating a locally-detected Python 2 as ready would only
    # create a late, misleading installation error. This guard does not
    # apply when the interpreter came from provision_docker_interpreter
    # above: that path uses the container's own pip/virtualenv instead of
    # a local ``venv`` invocation, so Python 2 genuinely is usable there.
    if selected_version.startswith("2.") and not selected_via_docker:
        status = "interpreter_unavailable"
        warnings.append("Python 2 requires AXIOM's explicit container backend; local venv provisioning is unsupported.")
    docker_available = bool(shutil.which("docker"))
    backend = (
        "docker" if selected_via_docker
        else "local" if status == "ready"
        else "docker_recommended" if docker_available
        else "unavailable"
    )
    if detected and not selected:
        warnings.append(
            f"Project declares Python {python_constraint or detected}, but no matching local interpreter was found. "
            + ("Docker is available for an isolated fallback." if docker_available else "Install that interpreter or enable Docker.")
        )
    if explicit_incompatible:
        warnings.append(
            f"Requested interpreter Python {selected_version} does not satisfy the project constraint {python_constraint}."
        )

    requirement_paths = discover_requirement_files(root, requirement_files)
    explicit_requirements = list(base_requirements or [])
    if explicit_requirements:
        # Evaluator callers supply a commit-recovered dependency slice.  It
        # is more authoritative than metadata from a possibly different
        # checkout, so do not introduce unrelated parser warnings or specs.
        file_requirements: list[str] = []
        metadata_requirements: list[str] = []
    else:
        file_requirements, requirement_warnings = _requirements_from_files(requirement_paths, root=root)
        metadata_requirements, metadata_warnings = _project_metadata_requirements(root)
        warnings.extend(requirement_warnings)
        warnings.extend(metadata_warnings)
    # Explicit CLI specs are intentional operator input and therefore take
    # precedence.  Otherwise, keep historical requirements-file pins ahead
    # of generic package metadata: the profiler uses the first matching spec
    # as the reconstructed source pin, while metadata fills dependencies that
    # were not listed in a requirements file.
    requirements = explicit_requirements or list(dict.fromkeys([
        *file_requirements,
        *metadata_requirements,
    ]))
    if not requirements and not requirement_paths:
        warnings.append("No runtime requirements file was found; AXIOM will rely on package metadata and smoke-test recovery.")

    hints: list[str] = []
    if any("Python-2-style" in warning for warning in warnings):
        hints.append("Use a Python 2 container; a Python 3 virtual environment cannot reproduce this baseline.")
    if not selected and docker_available:
        hints.append("Run AXIOM inside a matching container image, or install the declared local interpreter before retrying.")
    if target_distribution and target_version.casefold() in {"auto", "latest", "latest-compatible", ""}:
        hints.append("Target version is unpinned; resolve and record the newest compatible non-yanked release, then retain its OSV/PyPI security evidence before publishing results.")

    return EnvironmentPlan(
        project_root=str(root), requested_python=requested_python, detected_python=detected,
        python_constraint=python_constraint,
        python_evidence=evidence, selected_python=selected, selected_python_version=selected_version,
        interpreter_status=status, environment_backend=backend,
        requirement_files=[str(path) for path in requirement_paths], requirements=requirements,
        target_distribution=target_distribution, target_version=target_version,
        target_requirement=_target_requirement(target_distribution, target_version),
        test_command=test_command.strip(), recovery_hints=hints, warnings=warnings,
    )
