"""Equivalence oracle A(x, x') — the gate every variant must pass.

Auto-SPT formalises the oracle as ``A: X × X -> {0,1}``, "a binary function
that evaluates if two programs are semantically equivalent", commonly
implemented by checking identical outputs over a set of test inputs. We use
the corpus's own three-layer checker as that function:

  1. golden regression — every scenario and probe must reproduce golden.json;
  2. invariants        — properties that must hold for ANY input, checked
                         over hundreds of seeded pseudo-random values;
  3. coverage          — proof that the probes actually reach every branch
                         and state, so layer 1 is known to be meaningful.

Layer 3 is what makes this stronger than "the tests pass": a golden vector
set that never exercises the FAULT path would give false confidence, and the
coverage layer rules that out explicitly.

The oracle runs in a scratch copy of the corpus so the pristine sources are
never mutated, and in a subprocess so a variant that hangs or crashes the
interpreter cannot take the search down with it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class OracleResult:
    equivalent: bool
    detail: str
    duration_s: float
    script_checked: bool = False


def _run_as_script(work_dir: str, module_name: str, timeout: int = 60):
    """Execute a module as ``python module.py`` and capture its output."""
    try:
        proc = subprocess.run(
            [sys.executable, f"{module_name}.py"], cwd=work_dir,
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "", f"script timed out after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


class CorpusOracle:
    """Runs the corpus test suite against a candidate variant of one module.

    ``corpus_dir`` must contain the pristine modules plus ``oracle.py``,
    ``test_corpus.py``, ``make_golden.py`` and ``golden.json``.
    """

    REQUIRED = ("oracle.py", "test_corpus.py", "golden.json")

    def __init__(self, corpus_dir: str, timeout: int = 300,
                 check_script_output: bool = True) -> None:
        self.corpus_dir = os.path.abspath(corpus_dir)
        self.timeout = timeout
        # Extend coverage rather than restrict transformation. ``test_corpus``
        # imports each module and calls ``run_scenario()``; it NEVER executes
        # ``if __name__ == "__main__":``. Proven directly — replacing a print
        # in pid_governor's __main__ with ``raise RuntimeError`` still gave
        # "5 passed, 0 failed". Any transformation landing there was therefore
        # counted toward the line target and the diversity score while being
        # entirely unverified.
        #
        # The tempting fix is to forbid transforming __main__, but that throws
        # away real (and correct) work to satisfy a limitation of the checker.
        # Running each module as a script and comparing its output against the
        # pristine baseline closes the gap the other way: those lines become
        # genuinely verified, so they can keep counting honestly.
        self.check_script_output = check_script_output
        self._script_baseline: dict[str, tuple] = {}
        missing = [f for f in self.REQUIRED
                   if not os.path.isfile(os.path.join(self.corpus_dir, f))]
        if missing:
            raise FileNotFoundError(
                f"corpus at {self.corpus_dir} is missing: {', '.join(missing)}"
            )

    def script_baseline(self, module_name: str) -> tuple:
        """Pristine ``python module.py`` output, computed once and cached."""
        if module_name not in self._script_baseline:
            self._script_baseline[module_name] = _run_as_script(
                self.corpus_dir, module_name,
            )
        return self._script_baseline[module_name]

    def check(self, module_name: str, variant_source: str,
              extra_files: dict | None = None) -> OracleResult:
        """A(original, variant) for one module, as a 0/1 verdict plus detail.

        ``extra_files`` carries modules the variant ADDED -- ``{"saturation.py":
        source}`` -- which are written into the scratch copy alongside the
        rewritten module.

        Without it a restructuring that extracts a helper module is rejected
        with ``ModuleNotFoundError`` no matter how faithful it is, because the
        scratch copy holds only the pristine corpus plus the one module being
        checked. Measured on a hand-written reference variant of D1 that splits
        a saturation element into its own file: the blind judge rated it CLEAN
        with zero findings, and this oracle failed it on
        ``No module named 'saturation'``. Extracting a module is exactly the
        kind of restructuring the agent is supposed to be able to perform, so
        the gate must be able to see one.
        """
        import time

        work = tempfile.mkdtemp(prefix="axiom-isomorph-oracle-")
        try:
            for entry in os.listdir(self.corpus_dir):
                src = os.path.join(self.corpus_dir, entry)
                if os.path.isfile(src):
                    shutil.copy(src, work)
            with open(os.path.join(work, f"{module_name}.py"), "w") as handle:
                handle.write(variant_source)
            for name, body in (extra_files or {}).items():
                name = os.path.basename(name)
                if not name.endswith(".py") or name == f"{module_name}.py":
                    continue
                with open(os.path.join(work, name), "w") as handle:
                    handle.write(body)

            started = time.time()
            try:
                proc = subprocess.run(
                    [sys.executable, "test_corpus.py"], cwd=work,
                    capture_output=True, text=True, timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                return OracleResult(
                    False, f"oracle timed out after {self.timeout}s",
                    float(self.timeout),
                )
            elapsed = time.time() - started
            output = (proc.stdout or "") + (proc.stderr or "")
            failures = [ln for ln in output.splitlines()
                        if ln.startswith(("FAIL", "ERROR"))]
            if proc.returncode != 0:
                return OracleResult(
                    False, "; ".join(failures)[:400] or output[-400:], elapsed,
                )

            # Layer 4: script-level equivalence. Only reached once the three
            # corpus layers already passed, so it costs one extra run on
            # candidates that were going to be accepted anyway.
            if self.check_script_output:
                base_rc, base_out, _ = self.script_baseline(module_name)
                var_rc, var_out, var_err = _run_as_script(work, module_name)
                if base_rc == 0:          # only meaningful if the original runs
                    if var_rc != base_rc:
                        return OracleResult(
                            False,
                            f"__main__ exited {var_rc} (original {base_rc}): "
                            f"{var_err.strip()[-200:]}",
                            time.time() - started, script_checked=True,
                        )
                    if var_out != base_out:
                        return OracleResult(
                            False,
                            "__main__ produced different output than the original",
                            time.time() - started, script_checked=True,
                        )
                    return OracleResult(
                        True, "all layers passed (incl. __main__ output)",
                        time.time() - started, script_checked=True,
                    )
            return OracleResult(True, "all layers passed", elapsed)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def module_source(self, module_name: str) -> str:
        with open(os.path.join(self.corpus_dir, f"{module_name}.py")) as handle:
            return handle.read()


class NullOracle:
    """An oracle that accepts everything. For the no-verification ablation ONLY.

    ``proteus_no_verif`` in the evaluation protocol runs the agents with the
    equivalence oracle disabled, so the paper can report what verification is
    actually worth. That configuration needs an object with ``CorpusOracle``'s
    interface that never rejects.

    Deliberately a distinct class rather than a flag inside ``CorpusOracle``.
    A boolean threaded through the real oracle is one wrong default away from
    silently disabling verification in a normal run; a separate type has to be
    constructed on purpose, and shows up as itself in a traceback or a repr.

    ``detail`` says so on every verdict, and ``script_checked`` stays False, so
    a row built from this oracle can never be mistaken for a verified one.
    """

    def __init__(self, source: str, module_name: str = "") -> None:
        self._source = source
        self._module = module_name

    def module_source(self, module_name: str) -> str:
        return self._source

    def check(self, module_name: str, variant_source: str,
              extra_files: dict | None = None) -> OracleResult:
        return OracleResult(
            True, "NOT VERIFIED — equivalence oracle disabled (ablation)",
            0.0, script_checked=False,
        )

    def script_baseline(self, module_name: str):
        return (0, "", "")


def is_valid_python(source: str) -> bool:
    """Auto-SPT's ``IsValid`` filter: syntactic validity, checked before the
    (much more expensive) equivalence run."""
    import ast

    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False
