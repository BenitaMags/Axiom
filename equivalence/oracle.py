"""Equivalence oracle for the C/D evaluation programs.

Implements the same interface as ``smart_loader.isomorph.oracle.CorpusOracle``
so the Logic Substitution Agent can use it unchanged, but sources its evidence
from this corpus rather than from the ICS golden-vector layout.

Four layers, all of which must pass:

  1. golden regression — every probe reproduces its recorded value exactly
  2. invariants        — properties that hold for ANY input, over swept ranges
  3. coverage          — the program's own pytest suite still passes
  4. import integrity  — every first-party module still imports cleanly

Layer 3 is deliberately the corpus's own tests rather than a separate coverage
proof: these programs ship pytest suites, and re-deriving branch coverage would
duplicate what those suites already exercise. That makes this oracle *weaker*
than the ICS one, which additionally proves its probes reach every branch. The
difference is worth stating rather than glossing: a rewrite that breaks a path
neither the probes nor the tests visit would pass here.

Each check runs in a scratch copy of the program, in a subprocess, so a variant
that hangs or corrupts state cannot affect the next candidate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(HERE, "golden.json")


@dataclass(frozen=True)
class OracleResult:
    equivalent: bool
    detail: str
    duration_s: float
    script_checked: bool = False


# Runs inside the scratch copy: import the program's modules, execute the
# probes, print JSON. Kept as a string because it must run in a subprocess with
# the scratch directory on sys.path, not in this interpreter.
_RUNNER = r'''
import importlib, json, os, sys
sys.path.insert(0, os.getcwd())
sys.path.insert(0, {probes_dir!r})
import probes as P

program = {program!r}
mods = {{}}
for name in {modules!r}:
    mods[name] = importlib.import_module(name)

what = sys.argv[1]
if what == "probes":
    print(json.dumps(P.PROBES[program](mods), sort_keys=True, default=str))
else:
    print(json.dumps(P.INVARIANTS[program](mods), sort_keys=True, default=str))
'''


class ProgramOracle:
    """Equivalence gate for one C/D program."""

    def __init__(self, program_dir: str, program_name: str = "",
                 timeout: int = 120) -> None:
        self.program_dir = os.path.abspath(program_dir)
        self.program = program_name or os.path.basename(self.program_dir)
        self.timeout = timeout
        self.modules = sorted(
            f[:-3] for f in os.listdir(self.program_dir)
            if f.endswith(".py") and not f.startswith("test_")
        )
        self._golden = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _stage(self) -> str:
        work = tempfile.mkdtemp(prefix=f"equiv-{self.program}-")
        for entry in os.listdir(self.program_dir):
            src = os.path.join(self.program_dir, entry)
            if os.path.isfile(src):
                shutil.copy(src, work)
        return work

    def _run(self, work: str, mode: str):
        script = _RUNNER.format(probes_dir=HERE, program=self.program,
                                modules=self.modules)
        path = os.path.join(work, "_equiv_runner.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        try:
            proc = subprocess.run(
                [sys.executable, "_equiv_runner.py", mode], cwd=work,
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"{mode} timed out after {self.timeout}s"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return None, f"{mode} failed: {tail[-1][:180] if tail else '?'}"
        try:
            return json.loads(proc.stdout or "null"), ""
        except json.JSONDecodeError as exc:
            return None, f"{mode} produced unparseable output: {exc}"

    def _tests(self, work: str) -> tuple[bool, str]:
        tests = [f for f in os.listdir(work) if f.startswith("test_")]
        if not tests:
            return True, "no test file"
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", *tests], cwd=work,
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"tests timed out after {self.timeout}s"
        out = (proc.stdout or "") + (proc.stderr or "")
        last = out.strip().splitlines()[-1][:160] if out.strip() else ""
        return proc.returncode == 0, last

    # ── golden vectors ───────────────────────────────────────────────────────

    def record_golden(self) -> dict:
        """Run the pristine program and return its probe values."""
        work = self._stage()
        try:
            values, err = self._run(work, "probes")
            if values is None:
                raise RuntimeError(f"{self.program}: {err}")
            return values
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @property
    def golden(self) -> dict:
        if self._golden is None:
            if not os.path.isfile(GOLDEN_PATH):
                raise FileNotFoundError(
                    f"{GOLDEN_PATH} missing — run make_golden.py first")
            with open(GOLDEN_PATH, encoding="utf-8") as handle:
                self._golden = json.load(handle)
        return self._golden

    # ── the CorpusOracle interface ───────────────────────────────────────────

    def module_source(self, module_name: str) -> str:
        path = os.path.join(self.program_dir, f"{module_name}.py")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def check(self, module_name: str, variant_source: str,
              extra_files: dict | None = None) -> OracleResult:
        """A(original, variant) — all four layers, in cheapest-first order.

        The single-module form the Logic Substitution Agent calls. It is a
        special case of :meth:`check_many`, which is where the four layers
        actually live — one implementation, so the agent's gate and the
        offline scoring of a whole migrated program cannot drift apart.

        ``extra_files`` carries modules the variant ADDED, keyed by filename:
        ``{"saturation.py": source}``. ``check_many`` already writes whatever
        module names it is handed, so the only thing missing was a way to hand
        them over. Without it a restructuring that extracts a helper into its
        own module is rejected on ``ModuleNotFoundError`` however faithful it
        is -- measured on a hand-written reference variant of D1 that a blind
        judge rated CLEAN with zero findings. Extracting a module is one of
        the restructurings this gate exists to permit.
        """
        sources = {module_name: variant_source}
        for name, body in (extra_files or {}).items():
            stem = os.path.basename(name)
            if not stem.endswith(".py"):
                continue
            stem = stem[:-3]
            if stem != module_name:
                sources[stem] = body
        return self.check_many(sources)

    def check_many(self, sources: dict) -> OracleResult:
        """A(original, variant) where the variant spans several modules.

        A migration usually rewrites more than one file, so scoring a saved
        clone means overwriting every module it changed, not just one. The
        unlisted modules stay pristine, which is the point: the recorded
        golden values describe the ORIGINAL program's behaviour, so whatever
        replaces a module has to reproduce it in the original's company.
        """
        started = time.time()
        expected = self.golden.get(self.program)
        if expected is None:
            return OracleResult(False, f"no golden vectors for {self.program}",
                                0.0)

        work = self._stage()
        try:
            for module_name, variant_source in sources.items():
                with open(os.path.join(work, f"{module_name}.py"), "w",
                          encoding="utf-8") as handle:
                    handle.write(variant_source)

            # Layer 4 first: an unimportable variant makes the rest moot and
            # this is the cheapest check.
            for name in self.modules:
                proc = subprocess.run(
                    [sys.executable, "-c", f"import {name}"], cwd=work,
                    capture_output=True, text=True, timeout=self.timeout,
                )
                if proc.returncode != 0:
                    tail = (proc.stderr or "").strip().splitlines()
                    return OracleResult(
                        False, f"import {name} failed: "
                               f"{tail[-1][:150] if tail else '?'}",
                        time.time() - started)

            # Layer 1 — recorded values, exactly.
            actual, err = self._run(work, "probes")
            if actual is None:
                return OracleResult(False, err, time.time() - started)
            if actual != expected:
                differing = sorted(
                    k for k in set(actual) | set(expected)
                    if actual.get(k) != expected.get(k)
                )
                return OracleResult(
                    False, f"golden mismatch in: {', '.join(differing)[:160]}",
                    time.time() - started)

            # Layer 2 — properties over swept inputs.
            violations, err = self._run(work, "invariants")
            if violations is None:
                return OracleResult(False, err, time.time() - started)
            if violations:
                return OracleResult(
                    False, f"invariant violated: {violations[0][:160]}",
                    time.time() - started)

            # Layer 3 — the program's own suite.
            ok, detail = self._tests(work)
            if not ok:
                return OracleResult(False, f"tests failed: {detail}",
                                    time.time() - started)

            return OracleResult(
                True, "all four layers passed (golden, invariants, tests, imports)",
                time.time() - started, script_checked=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def script_baseline(self, module_name: str):
        return (0, "", "")
