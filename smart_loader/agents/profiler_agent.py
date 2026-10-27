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
import re
import tempfile
import hashlib
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from langchain_core.messages import AIMessage

from smart_loader.core.state import EquivalenceGroup, BenchmarkResult, AgentEvent, LoaderState
from smart_loader.core.environment_plan import provision_interpreter, provision_docker_interpreter

console = Console()

BENCHMARK_RUNS = 3
TIMEOUT_SEC    = 15
INSTALL_TIMEOUT_SEC = 90
# One historical base is resolved in a single transaction.  This bound keeps a
# pathological legacy dependency set from making a 20-case evaluation appear
# stuck for hours (formerly 90 seconds * every individual requirement).
BASE_ENVIRONMENT_TIMEOUT_SEC = 180

# Stage 1 is only meaningful when its tools are actually present in the same
# interpreter that imports the candidate package.  The verifier provisions a
# small, current toolchain in each of its separate baseline/target venvs;
# never in the profiler's mixed-candidate venv.  These tools are deliberately
# separate from the project's requirements: they inspect the migration, they
# are not part of the application being migrated.
STATIC_ANALYSIS_TOOL_SPECS = {
    "pylint": "pylint>=3.0,<4",
    # Pinned below 1.8: every verifier run in the Proteus evaluation reported
    # "Bandit TOOL_ERROR — TypeError: FileType.__init__() got an unexpected
    # keyword argument 'encoding'", raised inside bandit's own CLI parser on
    # Python 3.12. One of AXIOM's three static-analysis tools was therefore
    # contributing nothing to any result, while the table still showed a
    # Bandit row — the worst combination, because the check looks present.
    # 1.7.x is the last series without that entry point.
    "bandit": "bandit>=1.7,<1.8",
    "mypy": "mypy>=1.8,<2",
}

# These are command-line tools, not Python import libraries.  They can be
# useful tools in their own right, but are not valid candidates for a Python
# import migration and must not consume a profiling environment.
NON_PYTHON_CANDIDATES = {"k6", "vegeta", "hey"}
# Python import names do not always match distribution names in historical
# project requirements (notably PyMigBench's kafka-python / locustio cases).
IMPORT_TO_DISTRIBUTION = {
    "kafka": "kafka-python",
    "locust": "locustio",
    "slack": "slackclient",
    "ruamel": "ruamel.yaml",
    "pkg_resources": "setuptools",
    "cheetah": "cheetah3",
    "crypto": "pycrypto",
    "pil": "pillow",
    "netfields": "django-postgresql-netfields",
    "bs4": "beautifulsoup4",
    # Extended from the ten entries above (originally added one at a time,
    # each the direct fix for one specific PyMigBench case). A real 30-case
    # re-run then hit three more real cases — bs4, colorama, websocket — that
    # this table's small size wasn't actually the cause of (see
    # _declared_support_packages's docstring: the deeper bug was the
    # local-vs-external gate itself), but expanding known import/distribution
    # mismatches from a well-established, widely-referenced set (the same
    # mismatches tools like pipreqs curate) is a cheap, deterministic,
    # zero-network, zero-new-dependency improvement in its own right —
    # exactly the kind of static lookup MigrateLib's own johnnydep step is
    # used for, minus the per-import network round-trip and the
    # dependency-confusion risk of guessing an unknown import's distribution
    # name from PyPI at runtime. Deliberately conservative: only added
    # mismatches with an unambiguous, long-established single answer; several
    # plausible-but-ambiguous ones (psycopg2, attr, telegram-adjacent import
    # names) were deliberately left out rather than guessed.
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "websocket": "websocket-client",
    "serial": "pyserial",
    "usb": "pyusb",
    "dateutil": "python-dateutil",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "git": "gitpython",
    "jwt": "pyjwt",
    "openssl": "pyopenssl",
    "mysqldb": "mysqlclient",
    "nmap": "python-nmap",
    "xlib": "python-xlib",
    "gi": "pygobject",
    "cairo": "pycairo",
    "opengl": "pyopengl",
    "wx": "wxpython",
    "levenshtein": "python-levenshtein",
    "jose": "python-jose",
}
HISTORICAL_DISTRIBUTION_RENAMES = {"locustio": "locust"}

# Runtime data must come from a fixed, side-effect-free semantic probe—not a
# model-generated snippet or a made-up neutral number.  A probe has an
# explicit fixture, validates one result before timing, imports/setup once,
# warms up, and only then measures the in-memory operation.  Network brokers,
# SaaS APIs, global monkey-patching, and real CLI commands deliberately have
# no probe until a genuine integration fixture is supplied by the project.
@dataclass(frozen=True)
class RuntimeProbeSpec:
    probe_id: str
    api_tokens: frozenset[str]
    kind: str
    candidates: dict[str, tuple[str, str]]  # normalized dist -> (setup, validate)
    fixture: str


def _runtime_candidate_key(package: str) -> str:
    """PEP 503-style key used only to select a fixed local probe."""
    return re.sub(r"[-_.]+", "-", package.casefold())


RUNTIME_PROBES: tuple[RuntimeProbeSpec, ...] = (
    RuntimeProbeSpec(
        probe_id="json_serialization",
        api_tokens=frozenset({"dumps", "loads", "dump", "load"}),
        kind="pure_local",
        fixture='{"alpha": 1, "items": [1, 2, 3], "nested": {"ok": true}}',
        candidates={
            "json": (
                "import json\nfixture = {'alpha': 1, 'items': [1, 2, 3], 'nested': {'ok': True}}\ndef run():\n    return json.dumps(fixture, sort_keys=True)",
                '''assert '"alpha"' in run()''',
            ),
            "orjson": (
                "import orjson\nfixture = {'alpha': 1, 'items': [1, 2, 3], 'nested': {'ok': True}}\ndef run():\n    return orjson.dumps(fixture, option=orjson.OPT_SORT_KEYS)",
                '''assert b'"alpha"' in run()''',
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="yaml_serialization",
        api_tokens=frozenset({"dump", "safe_dump", "dumps", "load", "safe_load", "yaml"}),
        kind="pure_local",
        fixture='{"alpha": 1, "items": [1, 2, 3]}',
        candidates={
            "pyyaml": (
                "import yaml\nfixture = {'alpha': 1, 'items': [1, 2, 3]}\ndef run():\n    return yaml.safe_dump(fixture, sort_keys=True)",
                "assert 'alpha' in run()",
            ),
            "oyaml": (
                "import oyaml\nfixture = {'alpha': 1, 'items': [1, 2, 3]}\ndef run():\n    return oyaml.safe_dump(fixture, default_flow_style=False)",
                "assert 'alpha' in run()",
            ),
            "ruamel-yaml": (
                "import io\nfrom ruamel.yaml import YAML\nyaml = YAML()\nfixture = {'alpha': 1, 'items': [1, 2, 3]}\ndef run():\n    stream = io.StringIO()\n    yaml.dump(fixture, stream)\n    return stream.getvalue()",
                "assert 'alpha' in run()",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="ip_network_parse",
        api_tokens=frozenset({"ipnetwork", "ipaddress", "ip_network", "network"}),
        kind="pure_local",
        fixture="192.0.2.0/24",
        candidates={
            "netaddr": (
                "from netaddr import IPNetwork\ndef run():\n    return str(IPNetwork('192.0.2.0/24'))",
                "assert run() == '192.0.2.0/24'",
            ),
            "py2-ipaddress": (
                "import ipaddress\ndef run():\n    return str(ipaddress.ip_network('192.0.2.0/24'))",
                "assert run() == '192.0.2.0/24'",
            ),
            "ipaddress": (
                "import ipaddress\ndef run():\n    return str(ipaddress.ip_network('192.0.2.0/24'))",
                "assert run() == '192.0.2.0/24'",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="template_render",
        api_tokens=frozenset({"template", "render", "respond"}),
        kind="pure_local",
        fixture="Hello Ada",
        candidates={
            "jinja2": (
                "from jinja2 import Template\ntemplate = Template('Hello {{ name }}')\ndef run():\n    return template.render(name='Ada')",
                "assert run() == 'Hello Ada'",
            ),
            "mako": (
                "from mako.template import Template\ntemplate = Template('Hello ${name}')\ndef run():\n    return template.render(name='Ada')",
                "assert run() == 'Hello Ada'",
            ),
            "cheetah3": (
                "from Cheetah.Template import Template\ndef run():\n    return str(Template('Hello $name', searchList=[{'name': 'Ada'}]))",
                "assert run() == 'Hello Ada'",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="cli_option_parse",
        api_tokens=frozenset({"argumentparser", "add_argument", "parse_args", "command", "option", "click"}),
        kind="pure_local",
        fixture="--count 2",
        candidates={
            "argparse": (
                "import argparse\ndef run():\n    parser = argparse.ArgumentParser(add_help=False)\n    parser.add_argument('--count', type=int)\n    return parser.parse_args(['--count', '2']).count",
                "assert run() == 2",
            ),
            "click": (
                "import click\nfrom click.testing import CliRunner\n@click.command()\n@click.option('--count', type=int)\ndef command(count):\n    click.echo(count)\ndef run():\n    result = CliRunner().invoke(command, ['--count', '2'])\n    assert result.exit_code == 0, result.output\n    return result.output.strip()",
                "assert run() == '2'",
            ),
            "docopt": (
                "from docopt import docopt\nDOC = 'Usage: app --count=<count>'\ndef run():\n    return int(docopt(DOC, argv=['--count=2'])['--count'])",
                "assert run() == 2",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="record_construction",
        api_tokens=frozenset({"s", "ib", "define", "field", "dataclass", "attr", "attrs"}),
        kind="pure_local",
        fixture="Record(count=2)",
        candidates={
            "attrs": (
                "import attr\n@attr.s\nclass Record:\n    count = attr.ib()\ndef run():\n    return Record(2).count",
                "assert run() == 2",
            ),
            "dataclasses": (
                "from dataclasses import dataclass\n@dataclass\nclass Record:\n    count: int\ndef run():\n    return Record(2).count",
                "assert run() == 2",
            ),
            "characteristic": (
                "from characteristic import attributes\n@attributes(['count'])\nclass Record:\n    pass\ndef run():\n    return Record(2).count",
                "assert run() == 2",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="noqa_detection",
        api_tokens=frozenset({"noqa"}),
        kind="pure_local",
        fixture="'x = 1  # noqa' -> true; 'x = 1' -> false",
        candidates={
            "pep8": (
                "import pep8\ndef run():\n    return (bool(pep8.noqa('x = 1  # noqa')), bool(pep8.noqa('x = 1')))",
                "assert run() == (True, False)",
            ),
            "pycodestyle": (
                "import pycodestyle\ndef run():\n    return (bool(pycodestyle.noqa('x = 1  # noqa')), bool(pycodestyle.noqa('x = 1')))",
                "assert run() == (True, False)",
            ),
        },
    ),
    RuntimeProbeSpec(
        probe_id="bounded_retry",
        api_tokens=frozenset({"retry", "retrying", "retry_call", "retrying"}),
        kind="pure_local",
        fixture="fail twice, return 3 on third attempt",
        candidates={
            "retrying": (
                "from retrying import retry\ndef run():\n    attempts = []\n    @retry(stop_max_attempt_number=3, wait_fixed=0)\n    def eventually():\n        attempts.append(1)\n        if len(attempts) < 3:\n            raise ValueError('retry')\n        return len(attempts)\n    return eventually()",
                "assert run() == 3",
            ),
            "tenacity": (
                "from tenacity import retry, stop_after_attempt, wait_none\ndef run():\n    attempts = []\n    @retry(stop=stop_after_attempt(3), wait=wait_none(), reraise=True)\n    def eventually():\n        attempts.append(1)\n        if len(attempts) < 3:\n            raise ValueError('retry')\n        return len(attempts)\n    return eventually()",
                "assert run() == 3",
            ),
        },
    ),
)

RUNTIME_WARMUPS = 10
RUNTIME_SAMPLES = 50


def _api_token(used_api: str) -> str:
    return used_api.split(".")[-1].lower()


DOCKER_SENTINEL_PREFIX = "docker://"


def _get_python(preferred: str = "") -> str:
    """Return a validated Python executable, honoring a planned interpreter.

    ``venv.EnvBuilder`` always uses the interpreter running AXIOM.  Historical
    projects need the inverse relationship: select the interpreter first, then
    invoke ``selected_python -m venv``.  This helper keeps the old current-
    interpreter fallback only when no explicit plan was supplied.

    A ``docker://<image>`` value is not a filesystem path — it is
    ``core/environment_plan.py``'s ``provision_docker_interpreter`` sentinel
    for a historical minor with no local or uv-provisionable build (chiefly
    Python 2.x). Pass it through unresolved; ``_create_disposable_venv``
    below is what actually turns it into something runnable.
    """
    if preferred:
        if preferred.startswith(DOCKER_SENTINEL_PREFIX):
            return preferred
        resolved = preferred if os.path.isfile(preferred) else shutil.which(preferred)
        if resolved and os.path.isfile(resolved):
            return resolved
        raise RuntimeError(f"Planned Python interpreter is unavailable: {preferred}")

    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    for name in ("python3", "python3.12", "python3.11", "python3.10", "python"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found
    raise RuntimeError("No valid Python executable found.")


def _create_docker_venv(env_dir: str, image: str) -> str:
    """Materialize a venv inside ``image`` and return a host-executable wrapper.

    Everything downstream (``_pip_install``, the runtime probes, verifier's
    static-analysis and execution calls) just runs ``[python, ...]`` via
    ``subprocess.run`` — it has no idea a container is involved. The wrapper
    script written here is a real, executable file at a real host path, so
    it satisfies every ``os.path.isfile`` check those callers already do;
    internally it re-invokes ``docker run`` against the *same* image every
    time, which is what makes it safe to hand back a path like
    ``/axiom-env/venv/bin/python`` that only resolves inside that container.

    Python 2's official image has no ``venv`` module (that's Python 3.3+),
    so venv creation is attempted first and falls back to installing and
    using ``virtualenv`` — both produce a ``bin/python`` this wrapper can
    invoke identically.
    """
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("docker is no longer on PATH; cannot materialize a container-backed environment.")

    venv_cmd = (
        "python -m venv --clear /axiom-env/venv "
        "|| (pip install --quiet virtualenv && python -m virtualenv --clear /axiom-env/venv)"
    )
    completed = subprocess.run(
        [docker, "run", "--rm", "-v", f"{env_dir}:/axiom-env", image, "sh", "-c", venv_cmd],
        capture_output=True, text=True, timeout=INSTALL_TIMEOUT_SEC,
        env=_verification_subprocess_environment(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-1000:] or "container venv creation failed"
        raise RuntimeError(f"docker[{image}] venv creation failed: {detail}")

    tmp_root = tempfile.gettempdir()
    wrapper_path = os.path.join(env_dir, "docker-python.sh")
    wrapper = f"""#!/bin/sh
# AXIOM-generated: routes this "interpreter" through a Docker container
# reproducing a historical Python minor local/uv provisioning could not
# reach. See core/environment_plan.py:provision_docker_interpreter and
# agents/profiler_agent.py:_create_docker_venv.
cid_file=$(mktemp -u)
cleanup() {{
    cid=$(cat "$cid_file" 2>/dev/null)
    [ -n "$cid" ] && docker kill "$cid" >/dev/null 2>&1
    rm -f "$cid_file"
}}
trap cleanup TERM INT EXIT
exec docker run --rm --cidfile "$cid_file" \\
    -v "$(pwd)":"$(pwd)" \\
    -v "{tmp_root}":"{tmp_root}" \\
    -v "{env_dir}":/axiom-env \\
    -w "$(pwd)" \\
    {image} /axiom-env/venv/bin/python "$@"
"""
    with open(wrapper_path, "w", encoding="utf-8") as handle:
        handle.write(wrapper)
    os.chmod(wrapper_path, 0o755)
    return wrapper_path


def _create_disposable_venv(env_dir: str, python_executable: str = "") -> str:
    """Create a venv using the plan-selected Python, returning its executable.

    Explicitly wipes ``env_dir`` before creation rather than relying on
    ``venv --clear`` alone. This function is called repeatedly against the
    *same* directory when tier 4 of ``_install_base_layer`` cycles through
    several candidate interpreters in one case (3.11, then 3.10, then 3.9,
    then 3.8, then a final restore to the original) — five rebuilds of one
    path in quick succession. A real re-run surfaced this exact sequence
    producing ``[Errno 62] Too many levels of symbolic links`` on
    ``env_dir/bin/python`` by the last attempt: every candidate in that case
    then failed identically, not because the older interpreter genuinely
    couldn't build the package, but because the venv itself was corrupted by
    the time any of them got a real chance. ``--clear`` documents that it
    "deletes the contents... before environment creation," but that's
    ``venv``'s own internal cleanup — an explicit ``shutil.rmtree`` first
    guarantees a genuinely empty starting point every time, independent of
    whatever edge case repeated in-place clearing was hitting.
    """
    base_python = _get_python(python_executable)
    if base_python.startswith(DOCKER_SENTINEL_PREFIX):
        return _create_docker_venv(env_dir, base_python[len(DOCKER_SENTINEL_PREFIX):])
    # The base interpreter must never live inside the directory this function
    # is about to wipe. Tier 4's restore step passes the venv's *own*
    # ``env_dir/bin/python`` back in as the base ("rebuild this venv under
    # the interpreter it was built with") — before the explicit wipe below
    # existed that accidentally worked, but wipe-then-create deletes the
    # base executable before ``subprocess.run([base_python, "-m", "venv"])``
    # ever launches it, raising FileNotFoundError ([Errno 2]) — found live:
    # the first run on a machine where tier 4 actually exhausted real local
    # candidates hit this on every affected case, and the escaping OSError
    # got misreported as a per-package "hard_environment_failure". A venv's
    # ``bin/python`` is a symlink to its base interpreter on POSIX, so
    # resolving the real path first yields the true, outside-env_dir base;
    # pyvenv.cfg's ``home`` key is the documented fallback when the resolved
    # path still lives inside (e.g. a copies-based venv).
    try:
        env_real = os.path.realpath(env_dir)
        base_real = os.path.realpath(base_python)
        if base_real.startswith(env_real + os.sep):
            resolved_outside = ""
            pyvenv_cfg = os.path.join(env_dir, "pyvenv.cfg")
            if os.path.isfile(pyvenv_cfg):
                with open(pyvenv_cfg, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        key, _, value = line.partition("=")
                        if key.strip() == "home" and value.strip():
                            for name in ("python3", "python"):
                                candidate = os.path.join(value.strip(), name)
                                if os.path.isfile(candidate):
                                    resolved_outside = candidate
                                    break
                        if resolved_outside:
                            break
            base_python = resolved_outside or _get_python("")
        elif base_real != base_python and os.path.isfile(base_real):
            # Not inside env_dir, but still a symlink (the normal venv
            # case when tier 4 passes a *different* env's python): use the
            # real base so the new venv chains to an actual interpreter
            # rather than a soon-to-be-deleted link.
            base_python = base_real
    except OSError:
        # realpath on a corrupted/circular link: fall back to the default
        # interpreter rather than crashing before the wipe can clean it up.
        base_python = _get_python("")
    if os.path.lexists(env_dir):
        # ``isdir``/``exists`` follow symlinks and can themselves raise
        # OSError(ELOOP) if a prior rebuild already left a broken/circular
        # link at this exact path — lexists + a plain unlink attempt first
        # is the only check here that's safe against that.
        try:
            if os.path.isdir(env_dir) and not os.path.islink(env_dir):
                shutil.rmtree(env_dir, ignore_errors=True)
            else:
                os.unlink(env_dir)
        except OSError:
            shutil.rmtree(env_dir, ignore_errors=True)
    os.makedirs(env_dir, exist_ok=True)
    completed = subprocess.run(
        [base_python, "-m", "venv", "--clear", env_dir],
        capture_output=True, text=True, timeout=INSTALL_TIMEOUT_SEC,
        env=_verification_subprocess_environment(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-1000:] or "python -m venv failed"
        raise RuntimeError(detail)
    python = os.path.join(env_dir, "bin", "python")
    if not os.path.exists(python):
        python = os.path.join(env_dir, "Scripts", "python.exe")
    if not os.path.isfile(python):
        raise RuntimeError(f"Virtual environment did not expose a Python executable: {env_dir}")
    return python


def _canonical_distribution_key(name: str) -> str:
    """PEP 503 comparison key for state passed between pipeline agents."""
    return re.sub(r"[-_.]+", "-", str(name).casefold())


def _target_version_for_package(
    target_versions: dict[str, str] | None, package: str,
) -> str | None:
    """Find a target pin across equivalent import/distribution spellings.

    Resolver, CLI, and package metadata do not consistently spell a target
    the same way (``confluent_kafka`` versus ``confluent-kafka``).  Prefer a
    concrete resolved pin over an earlier ``auto`` request so the profiler
    never falls back to pip's latest release after Security resolved one.
    """
    if not isinstance(target_versions, dict):
        return None
    keys = {
        _canonical_distribution_key(package),
        _canonical_distribution_key(_distribution_name(package)),
    }
    fallback: str | None = None
    for raw_name, raw_version in target_versions.items():
        if _canonical_distribution_key(raw_name) not in keys:
            continue
        version = str(raw_version or "").strip()
        if version.casefold() not in {"", "auto", "latest", "latest-compatible"}:
            return version
        fallback = version
    return fallback


def _target_resolution_failure_for_package(
    failures: dict[str, str] | None, package: str,
) -> str | None:
    """Look up a fail-closed target diagnostic using PEP 503 name matching."""
    if not isinstance(failures, dict):
        return None
    keys = {
        _canonical_distribution_key(package),
        _canonical_distribution_key(_distribution_name(package)),
    }
    for raw_name, detail in failures.items():
        if _canonical_distribution_key(raw_name) in keys:
            return str(detail)
    return None


def _target_install_spec(package: str, target_versions: dict[str, str] | None = None) -> str:
    """Return an exact target install spec when the environment plan chose one."""
    requested = _target_version_for_package(target_versions, package)
    if not requested or str(requested).casefold() in {"auto", "latest", "latest-compatible"}:
        return _distribution_name(package)
    text = str(requested).strip()
    if text.startswith(("==", ">=", "<=", "~=", ">", "<")):
        return f"{_distribution_name(package)}{text}"
    return f"{_distribution_name(package)}=={text}"


_TIMER_SCRIPT = """
import sys, time, importlib, importlib.util, tracemalloc

requested = sys.argv[1]
runs = int(sys.argv[2])


def _canon(text):
    return text.lower().replace("-", "_").replace(".", "_")


def _resolve(name):
    # A PyPI distribution name is not its import path, and a hyphenated one
    # can NEVER be a module name: `import modbus-tk` is a SyntaxError-shaped
    # lookup that always raises ModuleNotFoundError. Importing the raw name
    # therefore marked every hyphenated distribution permanently unavailable
    # -- paho-mqtt, modbus-tk, simple-pid -- even with the package installed
    # and importable as `paho`, `modbus_tk`, `simple_pid`. Their speed and
    # memory evidence was then omitted from scoring on every run.
    #
    # find_spec is used rather than a trial import so resolution costs
    # nothing and cannot warm the cache ahead of the timed loop below.
    if importlib.util.find_spec(name) is not None:
        return name
    try:
        import importlib.metadata as md
        for module, dists in md.packages_distributions().items():
            if any(_canon(d) == _canon(name) for d in dists):
                return module
    except Exception:
        pass
    # A segment guess must not be accepted merely because SOME module of that
    # name exists. ``asyncio-mqtt`` is not installed here at all, but its first
    # segment resolves to the STDLIB ``asyncio``, so this loop reported the
    # distribution as available and AXIOM selected it. The Connector then had to
    # emit ``import asyncio_mqtt``, which does not exist; it discovered that
    # ``aiomqtt`` (the renamed successor) works, and patch-integrity forced it
    # back to the non-existent name. B3 burned its whole repair budget and was
    # reverted.
    #
    # So a guess is accepted only when nothing contradicts it: never a stdlib
    # module, and never one another installed distribution owns.
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    try:
        import importlib.metadata as _md
        owners = _md.packages_distributions()
    except Exception:
        owners = {}
    for candidate in (name.replace("-", "_"), name.split("-")[0],
                      name.split(".")[0]):
        if not candidate or candidate == name:
            continue
        if candidate in stdlib:
            continue
        holders = owners.get(candidate)
        if holders and not any(_canon(d) == _canon(name) for d in holders):
            continue
        try:
            if importlib.util.find_spec(candidate) is not None:
                return candidate
        except Exception:
            continue
    return name


try:
    pkg = _resolve(requested)
except Exception:
    pkg = requested

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

_RUNTIME_PROBE_SCRIPT = r'''
import hashlib
import json
import statistics
import sys
import time

descriptor = json.loads(sys.argv[1])
namespace = {"__name__": "__axiom_runtime_probe__"}
try:
    # ``setup`` and ``validate`` originate only from the static registry in
    # profiler_agent.py; no model output or project source is executed here.
    exec(descriptor["setup"], namespace)
    exec(descriptor["validate"], namespace)
    run = namespace["run"]
    for _ in range(int(descriptor["warmups"])):
        run()
    samples = []
    for _ in range(int(descriptor["samples"])):
        started = time.perf_counter_ns()
        run()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    print(json.dumps({
        "status": "measured",
        "median_ms": statistics.median(samples),
        "p95_ms": ordered[p95_index],
        "runs": len(samples),
        "warmups": int(descriptor["warmups"]),
        "fixture_sha256": hashlib.sha256(descriptor["fixture"].encode()).hexdigest()[:16],
    }))
except AssertionError as exc:
    print(json.dumps({"status": "validation_failed", "error": str(exc) or "probe result did not match its fixed fixture"}))
except Exception as exc:
    print(json.dumps({"status": "probe_failed", "error": f"{type(exc).__name__}: {exc}"}))
'''


def _probe_package_in_interpreter(
    python: str, package: str, *, failure_status: str = "hard_probe_failure",
) -> BenchmarkResult:
    """Measure a direct import in the supplied interpreter.

    Used for stdlib modules inside an isolated sandbox as well as the legacy
    host-environment profiling path.  The caller decides whether a failed
    measurement is a hard sandbox failure or merely an unattempted host probe.
    """
    try:
        result = subprocess.run(
            [python, "-c", _TIMER_SCRIPT, package, str(BENCHMARK_RUNS)],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
        stdout = result.stdout.strip()
        if stdout.startswith("OK:"):
            parts = stdout[3:].split(":")
            return BenchmarkResult(package=package, import_time_ms=round(float(parts[0]), 3),
                                   memory_kb=round(float(parts[1]) if len(parts) > 1 else 0.0, 1),
                                   available=True, profile_status="measured")
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error=stdout.replace("ERROR:", "") or result.stderr[:80],
                               profile_status=failure_status)
    except subprocess.TimeoutExpired:
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error=f"Timed out after {TIMEOUT_SEC}s",
                               profile_status=failure_status)
    except Exception as exc:
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error=str(exc), profile_status=failure_status)


def _probe_package(package: str) -> BenchmarkResult:
    """Host-environment probe retained for non-isolated interactive runs."""
    return _probe_package_in_interpreter(
        _get_python(), package, failure_status="neutral_unattempted",
    )


_ISOLATED_PROBE_SCRIPT = r'''
import importlib, importlib.metadata as md, sys, time, tracemalloc

dist_name, runs = sys.argv[1], int(sys.argv[2])
canonical = lambda s: s.lower().replace("-", "_").replace(".", "_")
try:
    dist = md.distribution(dist_name)
    modules = [line.strip() for line in (dist.read_text("top_level.txt") or "").splitlines() if line.strip()]
    if not modules:
        mapping = md.packages_distributions()
        modules = [name for name, dists in mapping.items()
                   if any(canonical(d) == canonical(dist.metadata["Name"]) for d in dists)]
    if not modules:
        print("ERROR:no Python import module declared by this distribution")
        raise SystemExit(1)
    module = sorted(modules, key=lambda m: (m != canonical(dist_name), len(m)))[0]
    times, peak = [], 0
    for _ in range(runs):
        for name in [m for m in sys.modules if m == module or m.startswith(module + ".")]:
            del sys.modules[name]
        tracemalloc.start(); t0 = time.perf_counter()
        importlib.import_module(module)
        times.append((time.perf_counter() - t0) * 1000)
        peak = max(peak, tracemalloc.get_traced_memory()[1]); tracemalloc.stop()
    print(f"OK:{module}:{sum(times)/len(times):.3f}:{peak/1024:.1f}")
except Exception as exc:
    try: tracemalloc.stop()
    except Exception: pass
    print(f"ERROR:{exc}")
    raise SystemExit(1)
'''


def _probe_isolated_package(python: str, package: str) -> BenchmarkResult:
    if package.lower() in NON_PYTHON_CANDIDATES:
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error="Not a Python import-library candidate",
                               profile_status="hard_unsupported_candidate")
    try:
        result = subprocess.run(
            [python, "-c", _ISOLATED_PROBE_SCRIPT, package, str(BENCHMARK_RUNS)],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
        stdout = result.stdout.strip()
        if stdout.startswith("OK:"):
            _, module, import_ms, memory_kb = stdout.split(":", 3)
            return BenchmarkResult(package=package, import_time_ms=round(float(import_ms), 3),
                                   memory_kb=round(float(memory_kb), 1), available=True,
                                   error=f"import module: {module}", profile_status="measured")
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error=stdout.replace("ERROR:", "") or result.stderr[:160],
                               profile_status="hard_probe_failure")
    except Exception as exc:
        return BenchmarkResult(package=package, import_time_ms=0.0, memory_kb=0.0,
                               available=False, error=str(exc), profile_status="hard_probe_failure")


def _requirement_name(spec: str) -> str:
    """Return a normalized distribution name, or ``""`` for a pip directive.

    This deliberately accepts the name from a PEP 508 direct reference
    (``name @ https://...``), but rejects bare VCS/URL/path entries that
    cannot be safely replayed as a project dependency.
    """
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", spec)
    # PEP 503 normalizes runs of ``-``, ``_`` and ``.`` equivalently.  Keeping
    # the same key everywhere matters for names such as ``ruamel.yaml``.
    return re.sub(r"[-_.]+", "-", match.group(1).casefold()) if match else ""


_LEGACY_POETRY_RANGE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^\]]+\])?)\s*"
    r"(?P<operator>\^|~)(?P<version>[0-9][A-Za-z0-9.+!_-]*)(?P<marker>\s*;.*)?$"
)

# ``locustio`` was the pre-1.0 distribution name; releases from 1.0 onward
# are published as ``locust``.  A historical requirement such as
# ``locustio>=1.0`` is therefore unsatisfiable on PyPI even though its intent
# is unambiguous.  Do not rewrite unpinned/pre-1.0 requirements: those can
# still genuinely refer to the old distribution.
_LOCUSTIO_POST_RENAME_VERSION_RE = re.compile(
    r"(?:>=|==|~=|>|===)\s*[1-9][0-9]*(?:\.[0-9]+)*"
)


def _normalize_historical_distribution_requirement(spec: str) -> str:
    """Apply only proven historical PyPI distribution renames.

    This is intentionally narrower than import-name resolution.  It repairs
    an impossible package *requirement* while preserving extras, version
    bounds, markers, and every unrelated dependency unchanged.
    """
    text = spec.strip()
    match = re.match(
        r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?P<extras>\[[^\]]+\])?(?P<tail>.*)$",
        text,
    )
    if not match or _requirement_name(match.group("name")) != "locustio":
        return text
    tail = match.group("tail")
    if not _LOCUSTIO_POST_RENAME_VERSION_RE.search(tail):
        return text
    return f"locust{match.group('extras') or ''}{tail}"


def _normalize_legacy_requirement(spec: str) -> str:
    """Translate simple Poetry ``^``/``~`` ranges into PEP 440.

    Historical PyMigBench projects often use a Poetry ``pyproject.toml``.
    Pip rightly rejects entries such as ``boto3^1.12.31`` in a constraints
    file, but that syntax has a precise, mechanically convertible meaning.
    Leave complex or unfamiliar syntax untouched so it remains visible as an
    environment problem instead of being silently guessed.
    """
    text = spec.strip()
    match = _LEGACY_POETRY_RANGE_RE.match(text)
    if not match:
        return text

    version = match.group("version")
    # Bounds can be calculated only for a plain numeric release version.  A
    # prerelease/local version needs Poetry's own resolver and is left raw.
    numeric = version.split("+", 1)[0]
    if "-" in numeric or not all(piece.isdigit() for piece in numeric.split(".")):
        return text
    components = [int(piece) for piece in numeric.split(".")]
    if not components:
        return text

    if match.group("operator") == "^":
        bound_index = next((i for i, value in enumerate(components) if value != 0), len(components) - 1)
    else:  # Poetry's ~1.2.3 is >=1.2.3,<1.3.0 (and ~1 is <2)
        bound_index = 1 if len(components) > 1 else 0
    upper = list(components)
    upper[bound_index] += 1
    for index in range(bound_index + 1, len(upper)):
        upper[index] = 0
    upper_version = ".".join(str(value) for value in upper)
    marker = match.group("marker") or ""
    return f"{match.group('name')}>={version},<{upper_version}{marker}"


def _distribution_name(package: str) -> str:
    """Return a resolver candidate's declared PyPI distribution name.

    Resolver candidates are package distributions, not import modules.  Do
    not apply historical import aliases here: doing so turns the real
    ``locustio -> locust`` migration into a false no-op.
    """
    return _requirement_name(package)


def _historical_distribution_name(package: str, requirements: list[str]) -> str:
    """Resolve an import label to its distribution in the historical project.

    Import aliases are valid only when recovering the source application's
    pin.  Prefer an exact declared distribution, then use a known import alias
    if that alias is actually declared by the project.
    """
    raw = _requirement_name(package)
    declared = {_requirement_name(spec) for spec in requirements}
    if raw in declared:
        return raw
    aliased = _requirement_name(IMPORT_TO_DISTRIBUTION.get(raw, raw))
    return aliased if aliased in declared or aliased != raw else raw


_STDLIB_MIN_MINOR: dict[str, tuple[int, int]] = {
    # These modules are visible in the operator's Python 3.12 stdlib, but
    # historical projects can legitimately require their PyPI backports.
    "contextvars": (3, 7),
    "dataclasses": (3, 7),
    "importlib_metadata": (3, 8),
    "zoneinfo": (3, 9),
    "graphlib": (3, 9),
    "tomllib": (3, 11),
    "exceptiongroup": (3, 11),
}
_INTERPRETER_MINOR_CACHE: dict[str, tuple[int, int] | None] = {}


def _interpreter_minor(python_executable: str = "") -> tuple[int, int] | None:
    """Return an interpreter's minor version without importing project code."""
    executable = _get_python(python_executable)
    if executable in _INTERPRETER_MINOR_CACHE:
        return _INTERPRETER_MINOR_CACHE[executable]
    try:
        completed = subprocess.run(
            [executable, "-c", "import sys; print('%s.%s' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=5,
            env=_verification_subprocess_environment(),
        )
        major, minor = (int(part) for part in completed.stdout.strip().split(".", 1))
        value: tuple[int, int] | None = (major, minor) if completed.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        value = None
    _INTERPRETER_MINOR_CACHE[executable] = value
    return value


def _is_stdlib_candidate(package: str, python_executable: str = "") -> bool:
    """Avoid only the backports already supplied by the selected Python."""
    root = package.split(".")[0].casefold()
    if root not in getattr(sys, "stdlib_module_names", frozenset()):
        return False
    minimum = _STDLIB_MIN_MINOR.get(root)
    if minimum is None:
        return True
    if python_executable:
        version = _interpreter_minor(python_executable)
    else:
        version = sys.version_info[:2]
    return bool(version and version >= minimum)


def _distribution_aliases(package: str) -> set[str]:
    """Names that must be excluded when ``package`` is an explicit target.

    The historical ``locustio`` -> ``locust`` rename is the important edge
    case here: a constraint for either name must not prevent the selected
    package from being installed at the intended version.
    """
    raw = _requirement_name(package)
    canonical = _distribution_name(package)
    historical_alias = _requirement_name(IMPORT_TO_DISTRIBUTION.get(raw, raw))
    aliases = {name for name in (raw, canonical, historical_alias) if name}
    for historical, current in HISTORICAL_DISTRIBUTION_RENAMES.items():
        if historical in aliases or current in aliases:
            aliases.update({historical, current})
    return aliases


def _base_specs_and_constraints(
    base_requirements: list[str], explicit_packages: list[str], env_dir: str,
) -> tuple[list[str], str | None]:
    """Return historical base specs and a constraints file for a sandbox.

    The migration source and candidate targets are installed separately.  They
    therefore must be removed from the base layer *and* its constraints; an
    old target pin otherwise wins over ``pip install target``.  All remaining
    declared base dependencies are constraints so installing a current target
    cannot silently modernize unrelated project dependencies.
    """
    excluded = {
        alias
        for package in explicit_packages
        for alias in _distribution_aliases(package)
    }
    base_specs = [
        _normalize_historical_distribution_requirement(
            _normalize_legacy_requirement(spec)
        ) for spec in base_requirements
        if _installable_requirement(spec) and _requirement_name(spec) not in excluded
    ]

    # Pip constraints cannot contain editable/local requirements or extras.
    # Keep a normal named requirement and strip extras while preserving its
    # version/marker.  A named direct URL remains installable as a base spec
    # but is omitted here: older pip versions reject it in constraints files.
    constraint_specs: list[str] = []
    for spec in base_specs:
        text = spec.strip()
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?(.*)$", text)
        if not match or match.group(2).lstrip().startswith("@"):
            continue
        constraint_specs.append(f"{match.group(1)}{match.group(2)}")
    if not constraint_specs:
        return base_specs, None

    constraints_path = Path(env_dir) / "axiom-base-constraints.txt"
    constraints_path.write_text(
        "\n".join(dict.fromkeys(constraint_specs)) + "\n", encoding="utf-8",
    )
    return base_specs, str(constraints_path)


def _pinned_spec(package: str, requirements: list[str]) -> str:
    key = _historical_distribution_name(package, requirements)
    spec = _normalize_legacy_requirement(
        next((spec for spec in requirements if _requirement_name(spec) == key), key)
    )
    return _normalize_historical_distribution_requirement(spec)


# Directories that can legitimately contain a same-named file/package but are
# never where a project's own real sibling module lives -- walking into them
# only wastes time and can produce a false "found it locally" match against a
# vendored copy of an unrelated third-party package.
_PROJECT_SCAN_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".tox",
    ".eggs", "build", "dist", "node_modules", ".mypy_cache", ".pytest_cache",
}


def _module_exists_in_project(module: str, project_root: str) -> bool:
    """Return whether ``module`` resolves to a real file inside the checkout.

    A conservative, bounded walk of ``project_root`` (the same historical
    snapshot ``_project_overlay`` copies wholesale for verification, so
    ``sys.path``/``cwd`` end up rooted here at runtime too) looking for
    either ``module.py`` or a ``module/__init__.py`` package directory at any
    level. This is deliberately a real filesystem check, not a name-based
    guess -- see ``_declared_support_packages``'s docstring for the case that
    exposed why a text-only "is it declared in requirements.txt" check isn't
    enough on its own.
    """
    if not module or not project_root or not os.path.isdir(project_root):
        return False
    target_file = f"{module}.py"
    for root, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _PROJECT_SCAN_SKIP_DIRS and not d.startswith(".")]
        if target_file in filenames:
            return True
        if module in dirnames and os.path.isfile(os.path.join(root, module, "__init__.py")):
            return True
    return False


def _declared_support_packages(
    import_modules: set[str], requirements: list[str], project_root: str = "",
) -> tuple[list[str], list[str]]:
    """Split imported modules into declared PyPI dependencies and local code.

    A historical project often imports sibling modules such as ``engine`` or
    ``tau``. They must be supplied by the cloned project source during
    verification, not sent to PyPI where they produce false environment
    failures. An import that matches a declared requirement (including known
    distribution/import aliases) is installed as a support package exactly
    as before.

    The gap this now closes: an import that matches *neither* a declared
    requirement *nor* a real file anywhere in the project checkout used to
    fall through to "local" by default -- treated as a sibling module that
    the checkout would supply at verification time, when it never actually
    would. Real cases this was found against, in a single 30-case re-run:
    ``bs4``, ``colorama``, and ``websocket`` are all genuine third-party
    packages a project imports without ever declaring in a requirements.txt
    (common for informal/tutorial repos in PyMigBench, which is most of
    where this shows up), so each got silently reclassified as "local,"
    never installed, and the case failed downstream with a plain
    ``ModuleNotFoundError`` that looked like an unrelated environment
    failure. ``project_root``, when supplied, lets this function tell that
    case apart from a genuine sibling module: if the import isn't declared
    but a real file for it exists in the checkout, it's still local (no
    behavior change from before); if it isn't declared *and* no such file
    exists, it's an undeclared external dependency and now gets attempted as
    one -- best-effort, via the same alias table and unpinned-latest
    fallback (``_pinned_spec`` degrades to a bare distribution name when
    nothing in ``requirements`` matches) every other support package already
    uses, so a genuinely wrong guess fails visibly at install time rather
    than silently at import time three stages later.
    """
    declared = {_requirement_name(spec) for spec in requirements}
    support: list[str] = []
    local: list[str] = []
    for module in sorted(import_modules):
        distribution = IMPORT_TO_DISTRIBUTION.get(module.lower(), module).replace("_", "-")
        if _requirement_name(distribution) in declared or _requirement_name(module) in declared:
            support.append(module)
        elif _module_exists_in_project(module, project_root):
            local.append(module)
        else:
            support.append(module)
    return support, local


_LEGACY_BUILD_FAILURE_SIGNATURES = (
    "distutils", "ccompiler", "cython_sources",
    # Old Cython-generated C sources failing to (re)compile under a modern
    # interpreter/toolchain — found live: gevent 1.x fails with
    # "Cython.Compiler.Errors.CompileError: src/gevent/libev/corecext.pyx",
    # which matched none of the three signatures above, so the tier-4
    # older-interpreter retry that exists for precisely this failure class
    # never fired for it. ".pyx" is the precise tell (the file extension
    # only Cython builds emit in errors); "cython.compiler" catches the
    # exception's module path when the .pyx filename is truncated away.
    ".pyx", "cython.compiler",
)


def _looks_like_legacy_build_incompatibility(detail: str) -> bool:
    """Heuristic: does this failure look like a build system 3.12+ broke?

    Python 3.12 removed the stdlib ``distutils`` module entirely (PEP 632).
    A package whose build system still depends on it — directly, via
    ``numpy.distutils``, or via an old Cython/setuptools integration written
    against it — fails to build on 3.12+ *regardless of which release is
    selected*. That makes it a fundamentally different problem from
    ``_same_major_version_fallback``'s "this one exact pin has no
    installable release" case: no amount of picking a different patch
    within the same major version fixes an interpreter incompatibility.
    Deliberately broad (substring, case-insensitive) rather than an exact
    signature list — the goal is "worth trying an older interpreter for",
    not a precise classifier.
    """
    lowered = detail.lower()
    return any(signature in lowered for signature in _LEGACY_BUILD_FAILURE_SIGNATURES)


_OLDER_INTERPRETER_FALLBACK_COMMANDS = ("python3.11", "python3.10", "python3.9", "python3.8")


def _older_local_interpreters(exclude: str) -> list[str]:
    """Already-installed local Pythons older than ``exclude``, newest first.

    Only ever consulted by the legacy-build-incompatibility retry below,
    and only among interpreters already on this machine — this never
    downloads or provisions anything (that's ``provision_interpreter``/
    ``provision_docker_interpreter`` in ``core/environment_plan.py``, for
    when a project's own *declared* version is genuinely unavailable). This
    is the opposite situation: nothing pinned the case to 3.12+, AXIOM's
    planner just defaulted to it, and a slightly older local interpreter
    would very likely satisfy an old, undeclared, or loosely-declared
    project just as well while still supporting the build systems these
    old packages were actually written against.
    """
    found: list[str] = []
    exclude_real = os.path.realpath(exclude) if exclude and os.path.isfile(exclude) else ""
    for name in _OLDER_INTERPRETER_FALLBACK_COMMANDS:
        path = shutil.which(name)
        if path and os.path.isfile(path) and os.path.realpath(path) != exclude_real:
            found.append(path)
    return found


_OLDER_INTERPRETER_PROVISION_VERSIONS = ("3.11", "3.10", "3.9", "3.8")


def _older_interpreter_candidates(exclude: str):
    """Yield executables to retry a legacy-build failure under, cheapest first.

    Already-installed local interpreters come first — free, instant,
    exactly what ``_older_local_interpreters`` alone used to offer. On an
    eval machine that only has the newest Python installed (the exact
    situation that made a real re-run of this case fail even though it's
    precisely what this tier exists for — nothing local to fall back to,
    so the loop below never even started), that first pass yields nothing.

    This is a generator specifically so the second and third passes are
    lazy: downloading an older CPython minor via ``provision_interpreter``
    (``core/environment_plan.py``, the same ``uv python install`` mechanism
    already used for a project's *declared* historical version) only
    happens if the caller keeps asking after every local candidate has
    already failed, and pulling a Docker image only happens if ``uv`` also
    failed for that specific version. A case that succeeds on a local
    interpreter never pays for a download it didn't need, and a case that
    succeeds via ``uv`` never pays for a container pull. Best-effort and
    silent per candidate on failure, same contract as ``provision_
    interpreter``/``provision_docker_interpreter`` themselves: not every
    minor is published as a standalone build ``uv`` can fetch, and not
    every Docker Hub tag still exists for every old minor either — trying
    both, in cheapest-first order, is what actually running one of these
    projects on a real machine looks like rather than assuming either one
    alone is enough.
    """
    local = _older_local_interpreters(exclude)
    yield from local
    local_versions = {mv for mv in (_interpreter_minor(c) for c in local) if mv}
    exclude_real = os.path.realpath(exclude) if exclude and os.path.isfile(exclude) else ""
    for version in _OLDER_INTERPRETER_PROVISION_VERSIONS:
        major_str, minor_str = version.split(".")
        if (int(major_str), int(minor_str)) in local_versions:
            continue
        console.print(
            f"[dim]  No older local interpreter left to try; attempting to "
            f"download Python {version} via uv...[/dim]"
        )
        path = provision_interpreter(version)
        if path and os.path.isfile(path) and os.path.realpath(path) != exclude_real:
            yield path
            continue
        console.print(
            f"[dim]  Could not provision Python {version} via uv "
            f"(unavailable, no standalone build for this platform, or "
            f"uv itself is not installed); trying a Docker container "
            f"instead...[/dim]"
        )
        docker_image = provision_docker_interpreter(version)
        if docker_image:
            # A ``docker://<image>`` sentinel, not a filesystem path —
            # _create_disposable_venv/_get_python already know how to route
            # this through docker run instead of a local executable, so the
            # tier-4 loop's body needs no changes to accept it.
            yield docker_image
        else:
            console.print(
                f"[dim]  Could not provision Python {version} via Docker "
                f"either (daemon not running, no network, or no matching "
                f"image tag); trying the next version.[/dim]"
            )


def _install_base_layer(
    python: str,
    base_requirements: list[str],
    explicit_packages: list[str],
    env_dir: str,
    *,
    needed_modules: list[str] | None = None,
) -> tuple[str | None, dict[str, str], str]:
    """Install a historical project's declared base dependencies into ``python``.

    Shared by both disposable-environment builders in this module — the
    profiler's mixed measurement venv (``_profile_in_temporary_venv``) and
    the verifier's authoritative baseline/target sandboxes
    (``create_verification_environment``). Previously each had its own copy
    of this install-then-narrow-on-failure logic, and only the profiler's
    copy got the narrowing retry added — the verifier's baseline/target
    venvs are what actually decide ``environment_incomplete``, so that gap
    meant the fix never reached the outcome it was meant to fix. Fixed by
    making this the one place either caller calls.

    Returns ``(constraints_path, failures, python)``. ``failures`` is empty
    on success. ``python`` is normally the same executable passed in, but
    the fourth (interpreter-swap) tier below can rebuild ``env_dir``'s venv
    under a different, older local interpreter — callers must use the
    returned ``python``, not their original one, for every subsequent
    install/execution in this environment, or they will silently be back on
    the interpreter that just failed.
    """
    failures: dict[str, str] = {}
    # Capture the TRUE base interpreter now, before tier 4 below rebuilds
    # env_dir under any candidate. ``python`` is env_dir's own bin/python —
    # a symlink whose target CHANGES with every tier-4 rebuild. The restore
    # step used to resolve it at restore time, by which point it pointed at
    # the *last candidate tried* (3.8), not the interpreter this function
    # started with — found live: a "restored" venv running Python 3.8
    # silently made pip hide every candidate release requiring >=3.9
    # (PyQt6 6.11.0 invisible, only 6.7.1 offered) and broke the probe on
    # importlib.metadata.packages_distributions (3.10+), marking every
    # candidate hard-failed for a version-skew reason that had nothing to
    # do with the candidates.
    try:
        original_base_python = (
            os.path.realpath(python)
            if python and os.path.isfile(python) else python
        )
    except OSError:
        original_base_python = python
    base_specs, constraints_path = _base_specs_and_constraints(
        base_requirements, explicit_packages, env_dir,
    )
    ok, detail = _pip_install_many(
        python, list(dict.fromkeys(base_specs)), constraints_path=constraints_path,
    )
    if ok:
        return constraints_path, failures, python

    # Retry once with only the packages this file's own imports (plus
    # migration-relevant support modules) actually need — see
    # _narrow_base_specs_to_needed_imports. Unrelated packages in the
    # project's full declared dependency set (e.g. a Postgres driver in a
    # Kafka migration's project) should not block verifying a migration that
    # never touches them, especially when the block is an old pin failing to
    # build a native extension for today's platform rather than anything
    # about the migration itself.
    working_specs = list(dict.fromkeys(base_specs))
    narrowed_specs = _narrow_base_specs_to_needed_imports(base_specs, needed_modules or [])
    if len(narrowed_specs) < len(base_specs):
        # An EMPTY narrowed set is a legitimate — and common — answer, not
        # a reason to skip narrowing: a file that imports only the
        # migration source and project-local modules needs *nothing* from
        # the project's declared base requirements. The old ``if
        # narrowed_specs and ...`` guard treated empty as "narrowing
        # unavailable" and fell through to installing the ENTIRE historical
        # requirements — found live: PyQt5 GUI form files burning the full
        # four-interpreter tier-4 ladder trying to build numpy and
        # pyalsaaudio (Linux-only ALSA headers, unbuildable on macOS under
        # any interpreter), packages those files never import.
        dropped = sorted(set(_requirement_name(s) for s in base_specs)
                          - set(_requirement_name(s) for s in narrowed_specs))
        console.print(
            f"[yellow]  ⚠ Full base-requirement install failed ({detail[:200]}); "
            f"retrying with only the {len(narrowed_specs)} package(s) this file's "
            f"imports actually need (dropped: {', '.join(dropped)})[/yellow]"
        )
        working_specs = list(dict.fromkeys(narrowed_specs))
        if not working_specs:
            console.print(
                "[dim]  ✓ This file's imports need none of the declared base "
                "requirements; skipping the base layer entirely (the failing "
                "packages are unrelated to the migration under test).[/dim]"
            )
            return constraints_path, failures, python
        narrowed_ok, narrowed_detail = _pip_install_many(python, working_specs)
        if narrowed_ok:
            console.print("[dim]  ✓ Narrowed base install succeeded.[/dim]")
            return constraints_path, failures, python
        detail = f"{detail}\n[narrowed retry also failed: {narrowed_detail}]"

    # Narrowing only helps when the failing package is unrelated to this
    # file's own imports.  A genuinely needed old native package (e.g. an
    # ancient numpy that fails "NameError: name 'CCompiler' is not defined"
    # building under today's setuptools/distutils) survives narrowing and
    # still blocks the case for a reason that has nothing to do with the
    # migration itself.  Same fix as the migration source's own pin
    # (_pip_install_source_pin): widen each exact pin to its same major
    # version and retry once more, so pip can pick any patch/minor release
    # it can actually install instead of one specific broken one.
    widened_specs = [_same_major_version_fallback(spec) or spec for spec in working_specs]
    if widened_specs != working_specs:
        console.print(
            "[yellow]  ⚠ Base install still failed with exact historical pins; "
            "retrying with pins widened to their same major version[/yellow]"
        )
        widened_ok, widened_detail = _pip_install_many(python, widened_specs)
        if widened_ok:
            console.print("[dim]  ✓ Same-major-version base install succeeded.[/dim]")
            return constraints_path, failures, python
        detail = f"{detail}\n[same-major-version retry also failed: {widened_detail}]"

    # Fourth tier: some failures are not a wrong-version-of-the-package
    # problem at all — they're the *interpreter* being newer than this old
    # package's build system supports (see
    # _looks_like_legacy_build_incompatibility). If nothing declared this
    # project needs 3.12+ specifically — AXIOM's planner just defaulted to
    # the newest available interpreter — retry the whole base layer under
    # an older interpreter instead of failing the case for a reason that has
    # nothing to do with the migration itself. Already-installed local
    # interpreters are tried first; if the eval machine doesn't have one
    # (confirmed by a real re-run: this tier never fired at all, silently,
    # because the local-only search came back empty), _older_interpreter_
    # candidates falls back to downloading one via uv rather than giving up
    # — the same provisioning mechanism already used for a project's
    # explicitly declared historical Python version, applied here to the
    # undeclared/defaulted-to-newest case instead.
    if _looks_like_legacy_build_incompatibility(detail):
        current_minor = _interpreter_minor(python)
        if current_minor and current_minor >= (3, 12):
            for candidate in _older_interpreter_candidates(python):
                console.print(
                    f"[yellow]  ⚠ Base install failure looks like a build system "
                    f"Python 3.{current_minor[1]} broke (stdlib distutils removal), not "
                    f"a wrong package version; retrying the whole base layer under "
                    f"{candidate}[/yellow]"
                )
                try:
                    fallback_python = _create_disposable_venv(env_dir, candidate)
                except RuntimeError as exc:
                    detail = f"{detail}\n[venv rebuild under {candidate} failed: {exc}]"
                    continue
                # ``_create_disposable_venv`` runs ``python -m venv --clear
                # env_dir`` — the venv lives directly in env_dir (not a
                # subdirectory), so ``--clear`` deletes everything already
                # there, including the constraints file
                # ``_base_specs_and_constraints`` wrote before tier 1 ran.
                # Without regenerating it here, this call and the restore
                # below both silently corrupt ``constraints_path`` into a
                # dangling reference: a later ``--constraint <path>`` pip
                # call fails with "Could not open requirements file" for a
                # file that used to exist, and the profiler misreports that
                # as a package install failure (hard_install_failure) rather
                # than the venv-rebuild side effect it actually is. Found via
                # a real re-run: 3 cases (petisco x2, throat) that previously
                # reached ``environment_incomplete`` after tier 3 flipped to
                # AXIOM declining to migrate at all, once this branch started
                # firing on their (non-3.12-specific) build failures.
                _, constraints_path = _base_specs_and_constraints(
                    base_requirements, explicit_packages, env_dir,
                )
                fallback_ok, fallback_detail = _pip_install_many(
                    fallback_python, widened_specs, constraints_path=constraints_path,
                )
                if fallback_ok:
                    console.print(
                        f"[dim]  ✓ Base install succeeded under {candidate} — this "
                        f"case's baseline now runs there instead of the original "
                        f"3.{current_minor[1]} venv.[/dim]"
                    )
                    return constraints_path, failures, fallback_python
                detail = f"{detail}\n[retry under {candidate} also failed: {fallback_detail}]"
            # However this loop ended, env_dir's venv may now be rebuilt
            # under whichever candidate was tried last, not the interpreter
            # the caller started with. Restore the original so a failed
            # fallback attempt never leaves the caller holding a stale
            # ``python`` path that no longer matches env_dir's actual venv.
            #
            # A real re-run surfaced why this needs to be more careful than
            # a single attempt: five rebuilds of the same env_dir in quick
            # succession (one per candidate, plus this restore) occasionally
            # left this final ``_create_disposable_venv`` call itself
            # failing (a lingering file handle from the just-failed pip
            # install, a filesystem timing hiccup — the exact conditions
            # that motivated wiping env_dir explicitly in the first place).
            # The old code caught that failure, logged it, and returned the
            # *original* ``python`` string anyway — a path that no longer
            # pointed to a real file, since the directory it lived in had
            # just been wiped for the restore attempt that failed. Every
            # candidate/support-package install downstream then failed with
            # a generic "[Errno 2] No such file or directory" that looked
            # like a probe problem instead of what it actually was: this
            # case's environment no longer existed. Retrying the restore a
            # few times and verifying the result is a real file before
            # trusting it catches the transient case instead of silently
            # handing back a dangling path.
            restored_python = ""
            restore_error: Exception | None = None
            for attempt in range(3):
                try:
                    # Restore under the interpreter this function STARTED
                    # with (resolved to its real path at entry, before any
                    # tier-4 rebuild re-targeted env_dir/bin/python's
                    # symlink at a candidate) — never the stale env_dir
                    # path, which by now points at the last candidate tried.
                    candidate_python = _create_disposable_venv(env_dir, original_base_python)
                    if os.path.isfile(candidate_python):
                        restored_python = candidate_python
                        break
                    restore_error = RuntimeError(
                        f"venv reported success but {candidate_python} does not exist"
                    )
                except (RuntimeError, OSError) as exc:
                    # OSError included deliberately: the live failure this
                    # retry exists for was a FileNotFoundError (the venv's
                    # own base interpreter deleted out from under it — see
                    # _create_disposable_venv's inside-env_dir guard), which
                    # a RuntimeError-only catch let escape all the way to
                    # the profiler's blanket handler as a fake per-package
                    # environment failure.
                    restore_error = exc
                if attempt < 2:
                    import time
                    time.sleep(0.5)
            if restored_python:
                if restore_error is not None:
                    console.print(
                        "[dim]  Restoring the original interpreter needed a retry "
                        "after a transient failure, but succeeded.[/dim]"
                    )
                python = restored_python
                # Same --clear side effect as above: regenerate the
                # constraints file so callers that reuse the returned
                # ``constraints_path`` after a fully-failed fallback attempt
                # (e.g. _pip_install_source_pin for the migration source)
                # still find a real file there instead of a deleted one.
                _, constraints_path = _base_specs_and_constraints(
                    base_requirements, explicit_packages, env_dir,
                )
            else:
                # Genuinely unrecoverable: env_dir has no working interpreter
                # at all after three attempts. Returning the stale ``python``
                # here would just move this exact failure downstream in a
                # more confusing shape (a bare "[Errno 2]" on every later
                # install/probe call instead of one clear message here) —
                # an empty string is an explicit, checkable "this case's
                # environment is unusable" signal callers must check for
                # before doing anything else with it.
                detail = (
                    f"{detail}\n[could not restore a working interpreter after 3 "
                    f"attempts, environment is unusable: {restore_error}]"
                )
                python = ""

    failures["base-requirements"] = detail
    return constraints_path, failures, python


def _narrow_base_specs_to_needed_imports(
    base_specs: list[str], needed_modules: list[str],
) -> list[str]:
    """Return only the base specs this file's own imports actually need.

    A historical project's full ``install_requires``/``requirements.txt``
    routinely includes packages the specific file under migration never
    imports (e.g. a project that bundles Postgres, MongoDB, and Kafka
    integrations in one ``install_requires`` list, when the file being
    migrated only uses Kafka). Requiring the *entire* declared set to
    install successfully — including unrelated native/compiled packages
    whose old historical pin has no prebuilt wheel for today's platform —
    blocks verification for a reason that has nothing to do with the
    migration actually being tested. This is used only as a fallback retry
    after the full, unnarrowed install already failed once (see
    ``_profile_in_temporary_venv``); the full set remains the default and
    preferred install.
    """
    needed_keys: set[str] = set()
    for module in needed_modules:
        distribution = IMPORT_TO_DISTRIBUTION.get(module.lower(), module).replace("_", "-")
        needed_keys.add(_requirement_name(distribution))
        needed_keys.add(_requirement_name(module))
    return [spec for spec in base_specs if _requirement_name(spec) in needed_keys]


def _pip_install(
    python: str, spec: str, *, constraints_path: str | None = None, upgrade: bool = False,
) -> tuple[bool, str]:
    """Install one requirement without allowing unrelated dependency drift."""
    try:
        command = [python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
        if upgrade:
            command.append("--upgrade")
        if constraints_path:
            command.extend(["--constraint", constraints_path])
        command.append(spec)
        install = subprocess.run(
            command,
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT_SEC,
            env=_verification_subprocess_environment(),
        )
    except Exception as exc:
        return False, str(exc)
    if install.returncode == 0:
        return True, ""
    return False, (install.stderr or install.stdout)[-400:] or "pip install failed"


def _pip_install_many(
    python: str,
    specs: list[str],
    *,
    constraints_path: str | None = None,
    timeout: int = BASE_ENVIRONMENT_TIMEOUT_SEC,
) -> tuple[bool, str]:
    """Resolve a historical base in one bounded pip transaction."""
    if not specs:
        return True, ""
    try:
        command = [python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet"]
        if constraints_path:
            command.extend(["--constraint", constraints_path])
        command.extend(specs)
        install = subprocess.run(
            command,
            capture_output=True, text=True, timeout=timeout,
            env=_verification_subprocess_environment(),
        )
    except subprocess.TimeoutExpired:
        return False, f"base environment install timed out after {timeout}s"
    except Exception as exc:
        return False, str(exc)
    if install.returncode == 0:
        return True, ""
    return False, (install.stderr or install.stdout)[-1000:] or "base environment install failed"


def _pip_check(python: str) -> tuple[bool, str]:
    """Detect resolver conflicts that individual pip installs can otherwise hide."""
    try:
        check = subprocess.run(
            [python, "-m", "pip", "check"],
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT_SEC,
            env=_verification_subprocess_environment(),
        )
    except Exception as exc:
        return False, str(exc)
    if check.returncode == 0:
        return True, ""
    return False, (check.stdout or check.stderr)[-800:] or "pip check failed"


def _verification_subprocess_environment() -> dict[str, str]:
    """Run installers without exposing the caller's model/API credentials.

    This is containment of process environment, not a VM/container sandbox.
    A genuinely hostile package still needs an OS-level sandbox (for example
    Kadabra, a container, or a VM) to restrict filesystem/network access.
    """
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
    env = {key: value for key in keep if (value := os.environ.get(key))}
    env.update({
        "HOME": tempfile.gettempdir(),
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONNOUSERSITE": "1",
    })
    return env


def _installable_requirement(spec: str) -> bool:
    """Reject pip directives/local paths that are not standalone dependencies."""
    text = spec.strip()
    lowered = text.casefold()
    return bool(
        text
        and not text.startswith(("-", ".", "/"))
        and not lowered.startswith(("git+", "http://", "https://", "file:"))
        and _requirement_name(text)
    )


def _same_distribution(package: str, source_package: str) -> bool:
    return _distribution_name(package) == _distribution_name(source_package)


def _same_major_version_fallback(spec: str) -> str | None:
    """Widen an exact ``name==X.Y.Z`` pin to ``name>=X,<X+1`` for a retry.

    Returns ``None`` when ``spec`` is not a simple exact pin (already a
    range, a VCS/path directive, or unparsable) — those cases have nothing
    more specific to fall back to.
    """
    if "==" not in spec:
        return None
    name, _, version_text = spec.partition("==")
    name = name.strip()
    version_text = version_text.strip()
    if not name or not version_text:
        return None
    try:
        from packaging.version import InvalidVersion, Version
        major = Version(version_text).release[0]
    except (InvalidVersion, IndexError, ImportError):
        return None
    return f"{name}>={major},<{major + 1}"


# pip names the distribution whose build actually failed in two stable
# shapes; both appear verbatim in real logs from this eval machine
# ("╰─> ruamel.yaml.clib", "Failed building wheel for gevent").
_FAILING_DISTRIBUTION_PATTERNS = (
    re.compile(r"Failed building wheel for\s+([A-Za-z0-9][A-Za-z0-9._-]*)"),
    re.compile(r"╰─>\s+([A-Za-z0-9][A-Za-z0-9._-]*)"),
)


def _widen_failing_transitive_constraint(
    constraints_path: str | None, detail: str, requested_package: str,
) -> list[str]:
    """Widen the constraint pin of a *transitive* dependency whose build failed.

    Found live (cloud-custodian, ruamel.yaml→pyyaml): installing the source
    at its historical pin failed — but not because of the source itself.
    The project's 2020 requirements pin ``ruamel.yaml.clib`` (a compiled
    helper the source depends on) to a version with no wheel for this
    platform, the constraints file faithfully carries that pin into every
    install, and pip's forced source-build of that one transitive dep dies
    with ``error: command 'cc' failed``. Every existing retry widens the
    package *being installed* and re-runs against the same constraints —
    so the strangling pin survives every tier and the case dies for a
    reason that has nothing to do with the migration under test.

    This parses which distribution actually failed to build out of pip's
    own error text, and — only when that distribution is *not* the package
    the caller asked for — widens its ``==`` pin in the constraints file to
    the same-major range (``_same_major_version_fallback``, the exact
    policy every other fallback already uses; for the real clib case that
    turns ``==0.2.0`` into ``>=0,<1``, admitting the modern 0.2.x releases
    that ship prebuilt wheels). Returns the list of widened distribution
    names (empty = nothing applicable, caller shouldn't bother retrying).
    Deliberately NOT applied when the failing distribution is the requested
    package itself — its own exact-pin/same-major fallbacks already cover
    that, and widening a constraint can never fix a package whose every
    same-major release fails to build.
    """
    if not constraints_path or not os.path.isfile(constraints_path):
        return []
    requested_key = _requirement_name(requested_package)
    failing_keys = {
        _requirement_name(match.group(1))
        for pattern in _FAILING_DISTRIBUTION_PATTERNS
        for match in pattern.finditer(detail or "")
    }
    failing_keys.discard("")
    failing_keys.discard(requested_key)
    if not failing_keys:
        return []
    try:
        lines = Path(constraints_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    widened: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        name = _requirement_name(line)
        if name in failing_keys and "==" in line:
            widened_spec = _same_major_version_fallback(line.strip())
            if widened_spec:
                new_lines.append(widened_spec)
                widened.append(name)
                continue
        new_lines.append(line)
    if not widened:
        return []
    try:
        Path(constraints_path).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError:
        return []
    console.print(
        f"[yellow]  ⚠ The failing build belongs to transitive dependenc"
        f"{'ies' if len(widened) > 1 else 'y'} {', '.join(widened)} pinned by "
        f"the project's historical constraints, not to {requested_package} "
        f"itself; widening that constraint to its same major version and "
        f"retrying[/yellow]"
    )
    return widened


def _pip_install_source_pin(
    python: str, source_package: str, base_requirements: list[str], *,
    constraints_path: str | None = None, upgrade: bool = False,
) -> tuple[str, bool, str]:
    """Install the migration source at its historical pin, degrading gracefully.

    ``_pinned_spec`` recovers the exact historical version (for example
    ``PyQt5==5.13.1``) so baseline behavior reflects what the project
    actually ran against. Old native/binary packages, though, routinely
    have no installable wheel for today's platform at that *exact* patch
    version even when neighboring versions do — observed directly:
    ``PyQt5==5.13.1`` has no matching distribution for the verifier's
    platform while 5.14.0+ do. Failing the whole case over one unavailable
    patch release blocks verification for a reason that has nothing to do
    with the migration under test. When the exact pin fails to install,
    retry once within the same major version (``name>=X,<X+1``) so pip can
    pick any patch/minor release it can actually install, without jumping
    to a different major line that could behave very differently from the
    historical baseline. The degradation is always logged, never silent,
    and the caller's ``failures`` entry records it when the fallback is
    what saved the install.
    """
    spec = _pinned_spec(source_package, base_requirements)
    ok, detail = _pip_install(python, spec, constraints_path=constraints_path, upgrade=upgrade)
    if ok:
        return spec, True, ""

    fallback_spec = _same_major_version_fallback(spec)
    if not fallback_spec:
        return spec, False, detail

    console.print(
        f"[yellow]  ⚠ Exact historical pin {spec!r} has no installable release "
        f"for this platform ({detail[:200]}); retrying within the same major "
        f"version ({fallback_spec!r}) so baseline still reflects a "
        f"compatible historical release[/yellow]"
    )
    fallback_ok, fallback_detail = _pip_install(
        python, fallback_spec, constraints_path=constraints_path, upgrade=upgrade,
    )
    if fallback_ok:
        console.print(
            f"[dim]  ✓ Installed {fallback_spec} (exact pin {spec!r} had no "
            f"installable release here; baseline uses the nearest compatible "
            f"release within the same major version instead).[/dim]"
        )
        return fallback_spec, True, (
            f"exact historical pin unavailable, used {fallback_spec} instead "
            f"(was: {detail[:200]})"
        )

    # Last tier: the build that failed may belong to a *transitive*
    # dependency whose historical pin the constraints file is forcing —
    # not to the source package at all. Widen that one constraint and give
    # the EXACT pin one more chance first (best baseline fidelity: the real
    # ruamel.yaml case installs at its exact historical version once the
    # clib constraint admits a release with wheels), then the same-major
    # fallback.
    widened = _widen_failing_transitive_constraint(
        constraints_path, f"{detail}\n{fallback_detail}", source_package,
    )
    if widened:
        retry_ok, retry_detail = _pip_install(
            python, spec, constraints_path=constraints_path, upgrade=upgrade,
        )
        if retry_ok:
            console.print(
                f"[dim]  ✓ Installed {spec} after widening the transitive "
                f"constraint(s) {', '.join(widened)}.[/dim]"
            )
            return spec, True, (
                f"transitive constraint(s) {', '.join(widened)} widened to "
                f"same-major (historical pin had no installable build here)"
            )
        retry_fallback_ok, retry_fallback_detail = _pip_install(
            python, fallback_spec, constraints_path=constraints_path, upgrade=upgrade,
        )
        if retry_fallback_ok:
            console.print(
                f"[dim]  ✓ Installed {fallback_spec} after widening the "
                f"transitive constraint(s) {', '.join(widened)}.[/dim]"
            )
            return fallback_spec, True, (
                f"exact pin unavailable; used {fallback_spec} with transitive "
                f"constraint(s) {', '.join(widened)} widened to same-major"
            )
        fallback_detail = (
            f"{fallback_detail}\n[retry after widening transitive constraint(s) "
            f"{', '.join(widened)} also failed: {retry_detail[:200]} / "
            f"{retry_fallback_detail[:200]}]"
        )
    return spec, False, f"{detail}\n[same-major-version fallback also failed: {fallback_detail}]"


def _benchmark_import_module(result: BenchmarkResult) -> str | None:
    """Extract the module selected by the isolated probe, even if neutralized."""
    prefix = "import module: "
    if not result.error or not result.error.startswith(prefix):
        return None
    return result.error[len(prefix):].split(";", 1)[0].strip() or None


def _benchmark_import_root(result: BenchmarkResult) -> str | None:
    module = _benchmark_import_module(result)
    return module.split(".")[0].casefold() if module else None


def _neutralize_shared_import_root_collisions(benchmarks: dict[str, BenchmarkResult]) -> None:
    """Do not attribute a mixed-venv measurement to competing distributions.

    A single virtual environment cannot reliably benchmark ``pycrypto`` and
    ``pycryptodome`` together: both write the ``Crypto`` import root, and the
    last pip install wins.  The profiler deliberately degrades those results
    to neutral/unavailable rather than feeding fabricated timings to AXIOM.
    Verification subsequently runs the source and target in separate venvs.
    """
    by_root: dict[str, list[tuple[str, str]]] = {}
    for package, result in benchmarks.items():
        root = _benchmark_import_root(result)
        if root:
            by_root.setdefault(root, []).append((package, _distribution_name(package)))

    for root, entries in by_root.items():
        distributions = {distribution for _, distribution in entries}
        if len(distributions) < 2:
            continue
        packages = ", ".join(sorted(package for package, _ in entries))
        detail = (
            f"Shared import root '{root}' across {packages}; mixed-environment "
            "benchmark neutralized. Verification uses separate source/target venvs."
        )
        for package, _ in entries:
            result = benchmarks[package]
            module = _benchmark_import_module(result) or root
            result.available = False
            result.import_time_ms = 0.0
            result.memory_kb = 0.0
            result.runtime_ms = 0.0
            result.runtime_api = ""
            result.api_runtimes = {}
            result.error = f"import module: {module}; {detail}"
            result.profile_status = "neutral_shared_import_root"


def _neutralize_mixed_environment_conflict(
    benchmarks: dict[str, BenchmarkResult], detail: str,
) -> None:
    """Keep a mixed profile conflict out of the verifier's base-env result.

    Each source/target pair is subsequently installed in its own verifier
    environment, where a genuine dependency conflict is tested correctly.
    The profiler's all-candidates environment is only a measurement cache, so
    it must not make an otherwise reproducible pair fail before verification.
    """
    for result in benchmarks.values():
        if not result.available:
            continue
        module = _benchmark_import_module(result)
        result.available = False
        result.import_time_ms = 0.0
        result.memory_kb = 0.0
        result.runtime_ms = 0.0
        result.runtime_api = ""
        result.api_runtimes = {}
        prefix = f"import module: {module}; " if module else ""
        result.error = (
            f"{prefix}Mixed-candidate profiling dependency conflict; performance "
            f"measurement neutralized ({detail[:300]}). Separate verifier environments remain authoritative."
        )
        result.profile_status = "neutral_mixed_conflict"


def create_verification_environment(
    base_requirements: list[str], migration_source: str, packages: list[str], *, include_source: bool,
    python_executable: str = "", target_versions: dict[str, str] | None = None,
    needed_modules: list[str] | None = None,
) -> tuple[str, str, dict[str, str], dict[str, str]]:
    """Build a clean baseline or target-only verifier sandbox.

    Profiling may co-locate only non-colliding candidates for cheap import
    measurements; shared import roots are neutralized instead of trusted.
    Verification cannot mix source and target at all: packages such as
    ``pycrypto`` and ``pycryptodome`` share an import root, so a mixed venv
    can make the baseline accidentally import the target.  This helper creates
    separate disposable environments after AXIOM has selected its winner.

    ``needed_modules`` (the migrated file's own imports, from the caller's
    ``state["imports"]``) is passed through to ``_install_base_layer`` so an
    unrelated package in the project's full declared dependency set — one
    this file never imports — can't block baseline/target provisioning on
    its own. This is the authoritative environment (unlike the profiler's
    measurement-only venv), so this is the copy that actually determines an
    ``environment_incomplete`` outcome.
    """
    failures: dict[str, str] = {}
    # Deliberately empty: static analysis runs in a separate tool-only venv.
    # Installing Pylint/Bandit/Mypy into this app environment could upgrade
    # pinned historical dependencies and invalidate the baseline we intend to
    # reproduce.
    tool_status: dict[str, str] = {}
    env_dir = tempfile.mkdtemp(prefix="axiom-verify-env-")
    python = _get_python(python_executable)
    try:
        python = _create_disposable_venv(env_dir, python_executable)

        # The source and selected target(s) are explicit installation targets,
        # never ordinary base requirements.  Excluding them from the base
        # constraints prevents an historical target pin from winning over the
        # current target requested by AXIOM.
        explicit_packages = [migration_source, *packages]
        constraints_path, base_layer_failures, python = _install_base_layer(
            python, base_requirements, explicit_packages, env_dir,
            needed_modules=needed_modules,
        )
        failures.update(base_layer_failures)
        if not python:
            # _install_base_layer returns "" only when tier 4's restore
            # attempt itself failed after retries and env_dir has no working
            # interpreter left at all -- not a per-package install failure.
            # Attempting the installs below against an empty/nonexistent
            # python would just produce a confusing "[Errno 2] No such file
            # or directory" per package instead of the one clear reason
            # already recorded in ``failures``.
            return env_dir, python, failures, tool_status

        install_packages = list(packages)
        if include_source and migration_source and not _is_stdlib_candidate(migration_source, python) and not any(
            _same_distribution(package, migration_source) for package in install_packages
        ):
            install_packages.append(migration_source)
        for package in dict.fromkeys(install_packages):
            if _is_stdlib_candidate(package, python):
                continue
            # Source uses its recovered historical spec, with a same-major-
            # version fallback if that exact pin has no installable release
            # for this platform (see _pip_install_source_pin).  Candidates
            # are current package targets.  --upgrade makes either intent
            # win over a pre-existing installation while constraints protect
            # the rest of the project environment.
            if _same_distribution(package, migration_source):
                _, ok, detail = _pip_install_source_pin(
                    python, migration_source, base_requirements,
                    constraints_path=constraints_path, upgrade=True,
                )
            else:
                spec = _target_install_spec(package, target_versions)
                ok, detail = _pip_install(
                    python, spec, constraints_path=constraints_path, upgrade=True,
                )
            if not ok:
                failures[package] = detail

        # Support packages: this file's own imports that are neither the
        # source nor a target — including UNDECLARED external ones (Fix 17's
        # ``_declared_support_packages`` classifies e.g. ``bs4`` in a
        # requirements-less tutorial repo as external). The profiler's
        # measurement venv has always installed these in its Layer 2, but
        # this authoritative baseline/target sandbox never did — found live
        # (talkpython, requests→httpx): the case survived profiling, AXIOM
        # selected the correct target, the drop-in patch was produced, and
        # the baseline import smoke then failed with a bare
        # ``ModuleNotFoundError: No module named 'bs4'`` because nothing
        # ever installed it *here*, in the one environment that decides
        # ``environment_incomplete``. Same install policy as the profiler's
        # support path: historical pin when declared, bare latest when not,
        # with the same-major fallback (Fix 16's pattern) on failure.
        for module in dict.fromkeys(needed_modules or []):
            if _is_stdlib_candidate(module, python):
                continue
            if any(
                _same_distribution(module, existing)
                for existing in [migration_source, *packages] if existing
            ):
                continue
            support_spec = _pinned_spec(module, base_requirements)
            ok, detail = _pip_install(
                python, support_spec, constraints_path=constraints_path,
            )
            if not ok:
                support_fallback = _same_major_version_fallback(support_spec)
                if support_fallback:
                    ok, fallback_detail = _pip_install(
                        python, support_fallback, constraints_path=constraints_path,
                    )
                    if not ok:
                        detail = (
                            f"{detail}\n[same-major-version fallback also "
                            f"failed: {fallback_detail}]"
                        )
            if not ok:
                failures[module] = detail

        healthy, detail = _pip_check(python)
        if not healthy:
            failures["pip-check"] = detail
    except Exception as exc:
        failures["sandbox"] = str(exc)
    return env_dir, python, failures, tool_status


def create_static_analysis_environment(python_executable: str = "") -> tuple[str, str, dict[str, str]]:
    """Provision verifier instrumentation without mutating an app sandbox.

    Pylint, Bandit, and Mypy are AXIOM's tooling, not dependencies of the
    historical project under test.  A dedicated venv keeps their current
    transitive dependencies out of both baseline and target environments.
    """
    tool_status: dict[str, str] = {}
    env_dir = tempfile.mkdtemp(prefix="axiom-static-tools-")
    python = _get_python(python_executable)
    try:
        python = _create_disposable_venv(env_dir, python_executable)
        for tool, spec in STATIC_ANALYSIS_TOOL_SPECS.items():
            ok, detail = _pip_install(python, spec, upgrade=True)
            tool_status[tool] = "available" if ok else f"install failed: {detail}"
        healthy, detail = _pip_check(python)
        if not healthy:
            for tool, status in list(tool_status.items()):
                if status == "available":
                    tool_status[tool] = f"tool environment pip check failed: {detail}"
    except Exception as exc:
        for tool in STATIC_ANALYSIS_TOOL_SPECS:
            tool_status.setdefault(tool, f"tool environment setup failed: {exc}")
    return env_dir, python, tool_status


def _profile_in_temporary_venv(packages: list[str], support_packages: list[str],
                               source_package: str, base_requirements: list[str], *,
                               python_executable: str = "",
                               target_versions: dict[str, str] | None = None,
                               target_resolution_failures: dict[str, str] | None = None) -> tuple[
                                   dict[str, BenchmarkResult], str, str, dict[str, str], dict[str, str]
                               ]:
    """Create a disposable venv; its caller removes it after verification."""
    benchmarks: dict[str, BenchmarkResult] = {}
    base_failures: dict[str, str] = {}
    tool_status: dict[str, str] = {}
    target_resolution_failures = target_resolution_failures or {}
    env_dir = tempfile.mkdtemp(prefix="axiom-profile-")
    python = _get_python(python_executable)
    try:
        python = _create_disposable_venv(env_dir, python_executable)

        # Layer 1: reproduce the pre-migration project's declared runtime
        # environment.  Source/candidate distributions are explicitly
        # excluded here and added in Layer 2.  The remaining requirements are
        # also passed as constraints to every target install so a candidate
        # cannot silently modernize unrelated historical dependencies.
        explicit_packages = [source_package, *packages]
        constraints_path, base_layer_failures, python = _install_base_layer(
            python, base_requirements, explicit_packages, env_dir,
            needed_modules=support_packages,
        )
        base_failures.update(base_layer_failures)
        if not python:
            # _install_base_layer returns "" only when tier 4's restore
            # attempt failed after retries and env_dir has no working
            # interpreter left -- a real re-run hit exactly this after five
            # rapid rebuilds of the same directory. Attempting every
            # candidate/support-package install below against an empty
            # python would just relabel this one clear cause as a generic
            # "[Errno 2] No such file or directory" on each package instead.
            detail = base_failures.get("base-requirements", "environment restore failed")
            for package in packages:
                benchmarks[package] = BenchmarkResult(
                    package=package, import_time_ms=0.0, memory_kb=0.0, available=False,
                    error=detail, profile_status="hard_environment_restore_failure",
                )
            for tool in STATIC_ANALYSIS_TOOL_SPECS:
                tool_status.setdefault(tool, "deferred to verifier's separate sandbox")
            return benchmarks, env_dir, python, base_failures, tool_status

        # Layer 2: install the source at its historical pin (for baseline
        # comparison/profiling) and candidates at current versions.  A target
        # that also appears in the historical dependency list is deliberately
        # installed last, upgrading it to the migration target.
        install_items = [
            (
                package,
                _pinned_spec(source_package, base_requirements)
                if _same_distribution(package, source_package)
                else _target_install_spec(package, target_versions),
                True,
            )
            for package in packages
        ]
        # Keep this argument for compatibility with older callers.  Declared
        # base requirements are already installed above, so this only fills a
        # legacy caller that explicitly supplied support modules.
        install_items.extend(
            (package, _pinned_spec(package, base_requirements), False)
            for package in support_packages
        )
        for package, install_spec, upgrade in dict.fromkeys(install_items):
            target_failure = (
                None if _same_distribution(package, source_package)
                else _target_resolution_failure_for_package(target_resolution_failures, package)
            )
            if target_failure:
                benchmarks[package] = BenchmarkResult(
                    package=package, import_time_ms=0.0, memory_kb=0.0, available=False,
                    error=target_failure,
                    profile_status="hard_target_resolution_failure",
                )
                continue
            if _is_stdlib_candidate(package, python):
                if package in packages:
                    # The module is supplied by this venv's interpreter.  It
                    # needs no PyPI distribution, but it is still a real
                    # baseline candidate and should receive a measurement.
                    benchmarks[package] = _probe_package_in_interpreter(python, package)
                continue
            if package.lower() in NON_PYTHON_CANDIDATES:
                benchmarks[package] = _probe_isolated_package(python, package)
                continue
            if _same_distribution(package, source_package):
                # Same-major-version fallback if the exact historical pin
                # has no installable release for this platform — see
                # _pip_install_source_pin.
                _, ok, detail = _pip_install_source_pin(
                    python, source_package, base_requirements,
                    constraints_path=constraints_path, upgrade=upgrade,
                )
            else:
                ok, detail = _pip_install(
                    python, install_spec, constraints_path=constraints_path, upgrade=upgrade,
                )
                if not ok and package in support_packages:
                    # A declared runtime dependency's own exact historical pin
                    # can hit the identical "no installable release/build for
                    # this platform" problem the migration source's pin
                    # already gets a same-major-version fallback for (see
                    # _pip_install_source_pin) — but this loop's support-
                    # package branch called plain _pip_install with no
                    # fallback at all. Real case this was found against: a
                    # PyQt5→PyQt6 case declaring numpy==1.17.3, which fails
                    # here with the exact stdlib-distutils-removal signature
                    # tier 4 above exists for (NameError: 'CCompiler' is not
                    # defined) — except Layer 1's _install_base_layer, called
                    # moments earlier in this same venv, had *already* proven
                    # a same-major-version numpy release installs and would
                    # have satisfied this exact import (that's what its own
                    # narrowing+widening retry is for). This loop then
                    # re-pinned numpy to the one exact version known not to
                    # build here, on a plain install with no retry, and
                    # recorded that as a hard failure — discarding a working
                    # install the venv already had. Widening here the same
                    # way closes that gap instead of failing the whole case
                    # over a re-install this loop didn't need to attempt at
                    # the exact pin in the first place.
                    fallback_spec = _same_major_version_fallback(install_spec)
                    if fallback_spec:
                        console.print(
                            f"[yellow]  ⚠ Declared dependency exact pin "
                            f"{install_spec!r} failed ({detail[:200]}); "
                            f"retrying within the same major version "
                            f"({fallback_spec!r})[/yellow]"
                        )
                        fallback_ok, fallback_detail = _pip_install(
                            python, fallback_spec,
                            constraints_path=constraints_path, upgrade=upgrade,
                        )
                        if fallback_ok:
                            console.print(
                                f"[dim]  ✓ Installed {fallback_spec} instead "
                                f"(exact pin {install_spec!r} had no "
                                f"installable release here).[/dim]"
                            )
                            ok, detail = True, ""
                        else:
                            # Same last tier as _pip_install_source_pin: the
                            # failing build may belong to a transitive dep
                            # whose historical pin the constraints file is
                            # forcing (ruamel.yaml.clib blocking ruamel.yaml,
                            # found live). Widen that one constraint, retry
                            # exact-then-widened.
                            widened = _widen_failing_transitive_constraint(
                                constraints_path,
                                f"{detail}\n{fallback_detail}",
                                _requirement_name(install_spec),
                            )
                            retried_ok = False
                            if widened:
                                for retry_spec in (install_spec, fallback_spec):
                                    retried_ok, retry_detail = _pip_install(
                                        python, retry_spec,
                                        constraints_path=constraints_path,
                                        upgrade=upgrade,
                                    )
                                    if retried_ok:
                                        console.print(
                                            f"[dim]  ✓ Installed {retry_spec} "
                                            f"after widening the transitive "
                                            f"constraint(s) "
                                            f"{', '.join(widened)}.[/dim]"
                                        )
                                        ok, detail = True, ""
                                        break
                            if not retried_ok:
                                detail = (
                                    f"{detail}\n[same-major-version fallback "
                                    f"also failed: {fallback_detail}]"
                                )
            if not ok:
                if package in support_packages:
                    base_failures[package] = detail
                benchmarks[package] = BenchmarkResult(
                    package=package, import_time_ms=0.0, memory_kb=0.0, available=False,
                    error=detail, profile_status="hard_install_failure",
                )
                continue
            if package in packages:
                # ``importlib.metadata.distribution`` accepts a distribution
                # name, not a pip requirement spec such as slackclient==2.5.0.
                result = _probe_isolated_package(python, _requirement_name(install_spec))
                result.package = package
                benchmarks[package] = result

        # This venv intentionally mixes candidates only to collect cheap
        # import measurements.  Static analyzers belong in verifier's
        # separate baseline/target venvs, where their dependencies cannot
        # contaminate profile results or add three extra installs per case.
        tool_status = {
            tool: "deferred to verifier's separate sandbox"
            for tool in STATIC_ANALYSIS_TOOL_SPECS
        }

        _neutralize_shared_import_root_collisions(benchmarks)

        healthy, detail = _pip_check(python)
        if not healthy:
            # Candidate packages are intentionally co-installed only for
            # profiling.  A conflict here must neutralize performance data,
            # not preempt the separate source/target verifier environments.
            _neutralize_mixed_environment_conflict(benchmarks, detail)
    except Exception as exc:
        for package in packages:
            benchmarks.setdefault(package, BenchmarkResult(package=package, import_time_ms=0.0,
                                                          memory_kb=0.0, available=False, error=str(exc),
                                                          profile_status="hard_environment_failure"))
        for tool in STATIC_ANALYSIS_TOOL_SPECS:
            tool_status.setdefault(tool, f"environment setup failed: {exc}")
    return benchmarks, env_dir, python, base_failures, tool_status


def _runtime_specs_for_group(group: EquivalenceGroup) -> list[RuntimeProbeSpec]:
    """Find comparable fixed probes using *all* parsed APIs, never just one."""
    api_tokens = {_api_token(api) for api in group.used_apis}
    candidate_keys = {_runtime_candidate_key(package) for package in group.candidates}
    matched: list[RuntimeProbeSpec] = []
    for spec in RUNTIME_PROBES:
        comparable_candidates = candidate_keys.intersection(spec.candidates)
        if len(comparable_candidates) < 2:
            continue
        if api_tokens.intersection(spec.api_tokens):
            matched.append(spec)
    return matched


def _record_runtime_probe(result: BenchmarkResult, probe_id: str, record: dict) -> None:
    """Attach transparent probe evidence without inventing a numeric result."""
    result.runtime_probes[probe_id] = record
    status = record.get("status", "unknown")
    if status != "measured" and not result.runtime_probe_detail:
        result.runtime_probe_detail = str(record.get("reason", record.get("error", status)))


def _run_runtime_probe(
    package: str,
    spec: RuntimeProbeSpec,
    python: str | None = None,
    module: str | None = None,
) -> dict:
    """Run one registered local probe and return provenance-rich JSON data."""
    config = spec.candidates.get(_runtime_candidate_key(package))
    if not config:
        return {
            "status": "unsupported", "probe_id": spec.probe_id, "kind": spec.kind,
            "reason": f"{package} has no implementation for the fixed {spec.probe_id} probe",
            "module": module or "",
        }
    setup, validate = config
    descriptor = {
        "setup": setup,
        "validate": validate,
        "fixture": spec.fixture,
        "warmups": RUNTIME_WARMUPS,
        "samples": RUNTIME_SAMPLES,
    }
    try:
        completed = subprocess.run(
            [python or _get_python(), "-c", _RUNTIME_PROBE_SCRIPT, json.dumps(descriptor)],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout", "probe_id": spec.probe_id, "kind": spec.kind,
            "reason": f"Timed out after {TIMEOUT_SEC}s", "module": module or "",
        }
    except Exception as exc:
        return {
            "status": "probe_failed", "probe_id": spec.probe_id, "kind": spec.kind,
            "reason": str(exc), "module": module or "",
        }

    try:
        payload = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError:
        return {
            "status": "probe_failed", "probe_id": spec.probe_id, "kind": spec.kind,
            "reason": (completed.stderr or completed.stdout or "runtime probe emitted no JSON")[:500],
            "module": module or "",
        }
    if not isinstance(payload, dict):
        return {
            "status": "probe_failed", "probe_id": spec.probe_id, "kind": spec.kind,
            "reason": "runtime probe emitted an invalid result", "module": module or "",
        }
    payload.update({
        "probe_id": spec.probe_id,
        "kind": spec.kind,
        "module": module or "",
        "distribution": package,
    })
    if completed.returncode and payload.get("status") == "measured":
        payload.update({"status": "probe_failed", "reason": (completed.stderr or "probe process failed")[:500]})
    return payload


def _attach_runtime_benchmarks(
    benchmarks: dict[str, BenchmarkResult],
    groups: list[EquivalenceGroup],
    python: str | None = None,
) -> None:
    """Measure fixed safe APIs, or record why an API cannot be benchmarked.

    A blank used to mean any of: unsupported, install failure, timeout, or a
    registry miss.  Every candidate now gets a structured reason.  External
    services (Kafka, Slack, HTTP against the public Internet) are explicitly
    *unmeasured*, not mocked and mislabelled as runtime performance.
    """
    jobs: dict[tuple[str, str], RuntimeProbeSpec] = {}
    for group in groups:
        specs = _runtime_specs_for_group(group)
        if not specs:
            used = ", ".join(group.used_apis) or "no parsed API"
            reason = (
                f"No registered side-effect-free runtime probe for {used}; "
                "an integration fixture is required for network, broker, CLI, or global-state APIs."
            )
            for package in group.candidates:
                benchmark = benchmarks.get(package)
                if benchmark:
                    _record_runtime_probe(benchmark, f"unsupported:{_api_token(group.used_apis[0]) if group.used_apis else 'api'}", {
                        "status": "unsupported", "reason": reason, "used_apis": list(group.used_apis),
                    })
            continue
        for spec in specs:
            for package in group.candidates:
                benchmark = benchmarks.get(package)
                if not benchmark:
                    continue
                if not benchmark.available:
                    _record_runtime_probe(benchmark, spec.probe_id, {
                        "status": "not_comparable",
                        "reason": f"package was not import-measured ({benchmark.profile_status})",
                        "probe_id": spec.probe_id, "kind": spec.kind,
                    })
                    continue
                if _runtime_candidate_key(package) not in spec.candidates:
                    _record_runtime_probe(benchmark, spec.probe_id, {
                        "status": "unsupported",
                        "reason": f"no {spec.probe_id} implementation is registered for {package}",
                        "probe_id": spec.probe_id, "kind": spec.kind,
                    })
                    continue
                jobs[(package, spec.probe_id)] = spec

    if not jobs:
        return

    console.print(
        f"[dim]Runtime API benchmarks ({RUNTIME_WARMUPS} warmups + {RUNTIME_SAMPLES} samples; fixed local fixtures)...[/dim]"
    )
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(
                _run_runtime_probe, package, spec, python,
                _benchmark_import_module(benchmarks[package]),
            ): (package, spec)
            for (package, _probe_id), spec in jobs.items()
        }
        for future in as_completed(futures):
            package, spec = futures[future]
            benchmark = benchmarks[package]
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "status": "probe_failed", "probe_id": spec.probe_id, "kind": spec.kind,
                    "reason": str(exc), "module": _benchmark_import_module(benchmark) or "",
                }
            _record_runtime_probe(benchmark, spec.probe_id, record)
            if record.get("status") != "measured":
                continue
            median_ms = record.get("median_ms")
            if not isinstance(median_ms, (int, float)) or median_ms <= 0:
                continue
            benchmark.api_runtimes[spec.probe_id] = round(float(median_ms), 6)
            if not benchmark.runtime_ms or float(median_ms) < benchmark.runtime_ms:
                benchmark.runtime_ms = round(float(median_ms), 6)
                benchmark.runtime_api = f"call:{spec.probe_id}"
                benchmark.runtime_probe_kind = spec.kind
                benchmark.runtime_probe_detail = (
                    f"median/p95 over {record.get('runs', RUNTIME_SAMPLES)} fixed-fixture samples"
                )


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

    # A set's iteration order changes between processes.  Stable installation
    # order makes non-colliding probe results reproducible, while shared-root
    # collisions are neutralized below rather than attributed to the last
    # package pip happened to install.
    all_packages = sorted({pkg for g in groups for pkg in g.candidates}, key=str.casefold)
    console.print(f"[dim]Probing {len(all_packages)} package(s) across {len(groups)} group(s)...[/dim]\n")

    benchmarks: dict[str, BenchmarkResult] = {}
    profile_state: dict = {}
    isolated = bool(state.get("isolated_profiling", False))
    if isolated:
        console.print("[dim]Using disposable profiling/verification environments; installed packages will be removed after the case finishes.[/dim]")
        environment_plan = state.get("environment_plan", {})
        planned_python = str(
            state.get("environment_python", "")
            or (environment_plan.get("selected_python", "") if isinstance(environment_plan, dict) else "")
        )
        # Resolve stdlib status against the planned historical interpreter,
        # not AXIOM's own Python.  ``dataclasses`` for example is a stdlib
        # module on 3.12 but a PyPI backport required by Python 3.6 projects.
        imported_supports = {
            imp.module.split(".")[0] for imp in state.get("imports", [])
            if not _is_stdlib_candidate(imp.module.split(".")[0], planned_python)
            and imp.module.split(".")[0] not in all_packages
        }
        support_packages, local_modules = _declared_support_packages(
            imported_supports, state.get("base_requirements", []),
            state.get("verification_project_root", ""),
        )
        if local_modules:
            console.print(
                "[dim]Project-local or undeclared imports supplied from the project checkout: "
                f"{', '.join(local_modules)}[/dim]"
            )
        target_versions = state.get("migration_target_versions", {})
        target_resolution_failures = dict(state.get("target_resolution_failures", {}) or {})
        # Every non-source candidate in a disposable environment must have an
        # exact resolved release.  Otherwise pip is free to substitute the
        # registry's latest version, which makes an ordinary resolver run
        # silently less reproducible than a seeded ``--target`` run.  This
        # also keeps security-disabled isolated runs honestly blocked instead
        # of producing unverifiable current-version measurements.
        base_requirements = state.get("base_requirements", [])
        source_labels = {
            str(getattr(group, "original", "") or group.candidates[0])
            for group in groups if group.candidates
        }
        explicit_source = str(state.get("migration_source", "") or "").strip()
        if explicit_source:
            source_labels.add(explicit_source)
        source_keys = {
            _canonical_distribution_key(label)
            for source in source_labels
            for label in (source, _historical_distribution_name(source, base_requirements))
            if label
        }
        for package in all_packages:
            package_keys = {
                _canonical_distribution_key(package),
                _canonical_distribution_key(_distribution_name(package)),
            }
            if package_keys.intersection(source_keys):
                continue
            if package.casefold() in NON_PYTHON_CANDIDATES:
                # This has its own explicit hard-unsupported verdict below;
                # it is not a version-resolution problem.
                continue
            if _is_stdlib_candidate(package, planned_python):
                continue
            if _target_resolution_failure_for_package(target_resolution_failures, package):
                continue
            resolved_target_version = _target_version_for_package(
                target_versions if isinstance(target_versions, dict) else {}, package,
            )
            if str(resolved_target_version or "").casefold() in {"", "auto", "latest", "latest-compatible"}:
                target_resolution_failures[package] = (
                    "Candidate release was not resolved. Enable security/version resolution "
                    "or pass an exact --target-version before isolated profiling."
                )
        plan_status = environment_plan.get("interpreter_status", "ready") if isinstance(environment_plan, dict) else "ready"
        if plan_status != "ready":
            detail = "; ".join(environment_plan.get("warnings", [])) if isinstance(environment_plan, dict) else ""
            detail = detail or f"Environment plan is not runnable: {plan_status}"
            console.print(f"[red]Profiler cannot create the planned historical environment: {detail}[/red]")
            benchmarks = {
                package: BenchmarkResult(
                    package=package, import_time_ms=0.0, memory_kb=0.0, available=False,
                    error=detail, profile_status="hard_environment_plan_failure",
                )
                for package in all_packages
            }
            env_dir, env_python = "", ""
            base_failures = {"environment-plan": detail}
        else:
            benchmarks, env_dir, env_python, base_failures, _ = _profile_in_temporary_venv(
                all_packages, support_packages,
                state.get("migration_source", ""), state.get("base_requirements", []),
                python_executable=planned_python,
                target_versions=target_versions if isinstance(target_versions, dict) else {},
                target_resolution_failures=(
                    target_resolution_failures if isinstance(target_resolution_failures, dict) else {}
                ),
            )
        profile_state = {
            "profiling_env_dir": env_dir, "profiling_env_python": env_python,
            "base_environment_failures": base_failures,
            "target_resolution_failures": target_resolution_failures,
            "verification_environment_status": "blocked" if base_failures else "ready",
        }
    else:
        console.print("[dim]Using the current Python environment (pass --isolated-profiling in eval mode for disposable probes).[/dim]")
    if not isolated:
      target_resolution_failures = state.get("target_resolution_failures", {})
      blocked_targets = target_resolution_failures if isinstance(target_resolution_failures, dict) else {}
      for pkg in all_packages:
          detail = _target_resolution_failure_for_package(blocked_targets, pkg)
          if detail:
              benchmarks[pkg] = BenchmarkResult(
                  package=pkg, import_time_ms=0.0, memory_kb=0.0, available=False,
                  error=str(detail), profile_status="hard_target_resolution_failure",
              )
      with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
          probe_packages = [pkg for pkg in all_packages if pkg not in benchmarks]
          tasks = {pkg: progress.add_task(f"Probing [cyan]{pkg}[/cyan]...") for pkg in probe_packages}
          with ThreadPoolExecutor(max_workers=6) as ex:
              futures = {ex.submit(_probe_package, pkg): pkg for pkg in probe_packages}
              for future in as_completed(futures):
                  pkg = futures[future]
                  benchmarks[pkg] = future.result()
                  progress.update(tasks[pkg], description=f"[green]✓[/green] {pkg}")

    _attach_runtime_benchmarks(benchmarks, groups, profile_state.get("profiling_env_python"))

    table = Table(title="Benchmark Results", header_style="bold magenta")
    table.add_column("Package")
    table.add_column("Available", justify="center")
    table.add_column(f"Import ({BENCHMARK_RUNS} runs)", justify="right")
    table.add_column("Runtime API (real probe)", justify="right")
    table.add_column("Memory (KB)", justify="right")
    table.add_column("Notes")

    for pkg, res in sorted(benchmarks.items(), key=lambda x: x[1].import_time_ms if x[1].available else 9999):
        avail = "[green]✓[/green]" if res.available else "[red]✗[/red]"
        imp_t = f"{res.import_time_ms:.1f} ms" if res.available else "—"
        if res.runtime_ms:
            probe_id = res.runtime_api.removeprefix("call:")
            probe = res.runtime_probes.get(probe_id, {})
            p95 = probe.get("p95_ms")
            spread = f" / {float(p95):.3f} ms p95" if isinstance(p95, (int, float)) else ""
            rt_t = f"{res.runtime_ms:.3f} ms median{spread} ({res.runtime_api})"
        else:
            reason = res.runtime_probe_detail
            if not reason and res.runtime_probes:
                first = next(iter(res.runtime_probes.values()))
                reason = str(first.get("reason", first.get("error", first.get("status", "no safe probe"))))
            rt_t = f"UNMEASURED — {(reason or 'no safe, comparable probe registered')[:110]}"
        mem = f"{res.memory_kb:.0f}" if res.available else "—"
        table.add_row(pkg, avail, imp_t, rt_t, mem, res.error or "")

    console.print(table)

    available_count = sum(1 for r in benchmarks.values() if r.available)
    runtime_count = sum(1 for r in benchmarks.values() if r.runtime_ms)
    unmeasured_runtime_count = sum(1 for r in benchmarks.values() if not r.runtime_ms)
    console.print(
        f"\n[green]✓ Profiled {len(benchmarks)} package(s), "
        f"{available_count} available, {runtime_count} with measured runtime probes, "
        f"{unmeasured_runtime_count} explicitly unmeasured[/green]\n"
    )

    trace.append(AgentEvent(
        stage="profiler", event="completed",
        detail={
            "packages": len(benchmarks),
            "available": available_count,
            "runtime_benchmarks": runtime_count,
            "runtime_unmeasured": unmeasured_runtime_count,
            "timings": {k: v.import_time_ms for k, v in benchmarks.items() if v.available},
            # Retain the exact measured/unmeasured provenance in state and
            # telemetry.  A consumer can distinguish a timeout, unsupported
            # external API, validation failure, and a real median/p95 value.
            "runtime_probes": {k: v.runtime_probes for k, v in benchmarks.items()},
        },
    ))

    return {
        "benchmarks":  benchmarks,
        **profile_state,
        "agent_trace": trace,
        "messages": [AIMessage(
            content=f"Profiler: {available_count}/{len(benchmarks)} available, "
                    f"{runtime_count} measured runtime probes; {unmeasured_runtime_count} explicitly unmeasured."
        )],
    }
