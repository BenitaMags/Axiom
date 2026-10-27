"""Shared machinery for the Proteus clone-generation evaluation.

Implements everything in ``reporting/METRICS_GUIDE.md`` that is *not* the
generator itself: reading each program's substitution spec, running a
generated clone against the program's own pytest suite, measuring how far the
clone diverged from the original, and writing a fully-populated row into
``reporting/results.csv``.

Three configurations share this module and differ only in how the clone is
produced:

    single_shot        one LLM prompt, no agents, no verification  (baseline 1)
    proteus_no_verif   the agents, equivalence oracle disabled     (baseline 2)
    proteus_full       the agents with verification                (system)

Those exact strings are load-bearing: ``reporting/summarize.py`` hard-codes
``CONFIG_ORDER = ["single_shot", "proteus_no_verif", "proteus_full"]``, so
renaming them — however tempting, given this project is called AXIOM — stops
the professor's summariser from producing the paper's table.

Design notes that matter for the numbers
────────────────────────────────────────
*Never mutate the corpus.* Every run happens in a throwaway copy. A generator
that corrupts a program would otherwise poison every later run of it, and the
failure would look like a model failure rather than a harness bug.

*``gen_success`` is derived here, not reported by the generator.* The guide
defines it as "compiles AND all tests pass". A generator's own opinion of its
success is exactly the thing the experiment is measuring, so it is never
trusted as an input.

*The substitution spec comes from the ``# FROM:``/``# TO:`` comment headers*
in each ``requirements.txt``, not from the package lines. The package lines
are not a consistent direction indicator — A1/A2/A3/C3 list the target because
their source is stdlib (``zlib``, ``json``, ``datetime``, ``smtplib``) and so
has nothing to install, while the other eight list the incumbent. Reading the
package line as the target would ask the system to migrate eight programs to
the library they already use, which correctly changes nothing, still passes
every test, and records as a success — inflating the headline number with
no-ops.
"""

from __future__ import annotations

import ast
import csv
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import date

CONFIGS = ("single_shot", "single_shot_repair", "proteus_no_verif",
           "proteus_full")

#: Column order of ``reporting/results.csv``. Written exactly as the guide
#: defines it so the file stays loadable by the professor's summariser.
COLUMNS = [
    "program", "category", "config", "run_id", "model", "date",
    # `gen_success` was removed. It was documented as the guide's "compiles AND
    # all tests pass", but the A/B oracle branch overwrote it, so a row could
    # read compiles=Y, tests 3/3, gen_success=N and look self-contradictory.
    # Its components are all still here as their own columns, and
    # `migration_success` is now the single verdict.
    "compiles", "tests_passed", "tests_total",
    # Added beyond the guide's schema, and placed HERE rather than at the end.
    # summarize.py reads columns by name via DictReader, so position is free —
    # and appended after ``notes`` these two sat behind a wide free-text field,
    # far off the right edge of a spreadsheet. A column nobody scrolls to is a
    # column nobody reads, and these are the two that stop a no-op counting as
    # a win, so they belong beside the metric they qualify.
    #
    #   migrated           did the source actually change at all
    #   migration_success  gen_success AND changed AND the library really swapped
    #
    # On this corpus the originals already pass their own tests, so
    # gen_success alone cannot separate a real migration from an untouched
    # file. proteus_no_verif scored 32/36 on gen_success and 12/36 here.
    "migrated", "migration_success",
    # The oracle's own verdict, kept because it is part of the guide's
    # schema. NA on A and B, which have no probes: there is no oracle result
    # to report there, and copying gen_success into it would dress a test
    # pass up as an equivalence proof. On C and D it is the four-layer
    # verdict, which is also a component of gen_success — the two therefore
    # agree except where a row fails its tests for reasons the oracle never
    # sees.
    "oracle_equiv",
    "iters_total", "vca_reentries", "wall_time_s",
    # Why the Verifier gave up, verbatim from its last repair cycle.
    # Without it a reverted row records failure_mode=source_import_remains,
    # which merely describes the restored ORIGINAL file and says nothing
    # about the repair that failed -- five proteus_full reverts could not be
    # diagnosed from the CSV at all.
    "revert_reason",
    "llm_calls", "tokens_total", "cost_usd", "divergence_score",
    "failure_mode", "converged", "failing_stage",
    "iters_lsa", "iters_gsa", "iters_lma",
    "tokens_in", "tokens_out", "loc_changed", "pct_firstparty_changed",
    "lib_swapped_correctly", "chosen_target", "glue_needed", "logic_mutated",
    # Structural distance between the DELIVERED file and the pristine
    # original, rename-insensitive, measured for every arm and category.
    # Distinct from the iso_* columns, which compare the mutator's variant
    # against the patched file and so isolate the transformation search alone.
    # These capture migration and diversification together, which is the only
    # form of the question all three arms can answer identically.
    "struct_ast", "struct_cfg", "struct_composite",
    # logic_mutated is a yes/no, which answers "did the Logic Substitution
    # Agent change anything" but not "how different is the result" — and the
    # diversification claim is quantitative. These four carry the numbers the
    # agent already computes and was, until now, discarding at the CSV
    # boundary. Blank on A and B, whose spec does not ask for diversification.
    "iso_diversity", "iso_cfg_shape", "iso_rules", "iso_lines",
    # Layer 5. The corpus injects fakes into every test, so the factory that
    # actually performs the substitution is never executed by the suite OR by
    # the probes. This column runs it. MISUSE means the migrated call is
    # wrong in a way no absent server could explain.
    "factory_path",
    "oracle_divergence_loc", "speedup_pct", "security_rationale",
    "hallucination",
    # Content hash of the saved clone, written by `save_clone` at the moment
    # the row is produced. This is what makes a row and its clone provably
    # the same artefact; without it a later overwrite of the clone directory
    # is undetectable, which is exactly what happened to 7 of 144 rows.
    "clone_sha",
    "notes",
    # ── single_shot_repair only ───────────────────────────────────────────
    # The repair arm is a PAIRED, within-arm comparison: attempt 1 (which is
    # byte-identical to the single_shot arm) against attempt 2 (the same model
    # re-prompted with the verifier's own output). Both live in one row so the
    # pair can never be split across files or mis-joined on (program, run_id).
    # NA on every other config -- there is no second attempt to report there,
    # and blank would mean "could not measure", which is a different claim.
    "attempt1_compiles", "attempt1_tests_passed",
    "attempt1_migration_success",
    "repair_attempted",
    # Both derived from the CODE via chosen_target(), never from the model's
    # `# CHOSEN-TARGET:` comment -- a comment can claim one library while the
    # code imports another, and the whole question here is whether the repair
    # switched libraries.
    "chosen_target_before", "chosen_target_after", "repair_changed_target",
    # Whether the library the MODEL chose was importable when its attempt was
    # verified. Under free choice the model picks its own target, so the
    # corpus's declared-target install list does not cover it: six of the
    # first run's eight attempt-1 "failures" were ModuleNotFoundError on a
    # package we simply never installed. Y = importable (installed by us or
    # already present); N = pip could not supply it, which for a name that
    # does not exist on PyPI is a real model failure; NA = no third-party
    # import to install (first-party rewrite).
    "target_installed",
]

#: Columns that exist only for ``single_shot_repair``. See the block above.
REPAIR_COLUMNS = [
    "attempt1_compiles", "attempt1_tests_passed",
    "attempt1_migration_success", "repair_attempted",
    "chosen_target_before", "chosen_target_after", "repair_changed_target",
    "target_installed",
]

FAILURE_MODES = ("none", "compile", "api_misuse", "logic_error",
                 "equiv_violation", "nonconvergence")


@dataclass
class Program:
    """One corpus program and its substitution requirement."""

    name: str
    path: str
    category: str
    from_lib: str
    to_lib: str
    source_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)

    @property
    def reimplement(self) -> bool:
        """True when the 'target' is not a package but 'write it yourself'.

        C1 and C2 name their targets 'in-house controller' and 'hand-rolled
        FSM'. Those are not installable, and treating them as package names
        would send the generator looking for a distribution that does not
        exist. They mean: remove the dependency and reimplement its behaviour
        as first-party code.
        """
        t = self.to_lib.lower()
        return any(k in t for k in ("in-house", "hand-rolled", "hand rolled",
                                    "in house", "first-party"))


_FROM_RE = re.compile(r"^#\s*FROM[^:]*:\s*(.+?)\s*$", re.I | re.M)
_TO_RE = re.compile(r"^#\s*TO[^:]*:\s*(.+?)\s*$", re.I | re.M)
_STDLIB_RE = re.compile(r"standard library\s*`?([A-Za-z_][\w.]*)`?", re.I)


def _clean_lib(raw: str) -> str:
    """Pull the library name out of a prose comment line.

    The headers read like ``requests -- synchronous HTTP client used by the
    original.`` or ``Python standard library `zlib` (zlib.crc32).`` Both forms
    appear in the corpus, so both are handled rather than assuming one.
    """
    text = raw.strip()
    stdlib = _STDLIB_RE.search(text)
    if stdlib:
        return stdlib.group(1).split(".")[0]
    text = re.split(r"\s+--\s+|\s+—\s+", text)[0]
    text = text.split("(")[0].strip().rstrip(".")
    return text.strip("`").strip()


def discover_programs(corpus_dir: str) -> list[Program]:
    """Every program in the corpus, with its substitution spec resolved."""
    programs: list[Program] = []
    for entry in sorted(os.listdir(corpus_dir)):
        path = os.path.join(corpus_dir, entry)
        req = os.path.join(path, "requirements.txt")
        if not os.path.isdir(path) or not os.path.isfile(req):
            continue
        text = open(req, encoding="utf-8").read()
        f_match, t_match = _FROM_RE.search(text), _TO_RE.search(text)
        if not (f_match and t_match):
            # Loud, not silent: a program whose direction we cannot read must
            # not quietly become a row with a guessed requirement.
            print(f"warning: {entry} has no readable FROM/TO header; skipping",
                  file=sys.stderr)
            continue
        py = sorted(f for f in os.listdir(path) if f.endswith(".py"))
        programs.append(Program(
            name=entry, path=path, category=entry[0].upper(),
            from_lib=_clean_lib(f_match.group(1)),
            to_lib=_clean_lib(t_match.group(1)),
            source_files=[f for f in py if not f.startswith("test_")],
            test_files=[f for f in py if f.startswith("test_")],
        ))
    return programs


# ── measurement ───────────────────────────────────────────────────────────────

def compiles(clone_dir: str, program: Program, timeout: int = 60) -> bool:
    """Every first-party file runs under ``python3 <file>`` without error.

    It used to be ``ast.parse`` alone -- a syntax check. That is a far weaker
    claim than the column name makes, and it misses the whole class of failure
    that only appears when the file is actually executed: an import of a package
    that is not installed, a name used at module scope that no longer exists, a
    decorator or class body that raises. Two single_shot clones scored
    ``compiles=Y`` while importing ``pid`` and ``envelopes`` -- packages that are
    neither the mandated target nor present in the environment -- because the
    text was valid Python.

    Each file is run the way you would run it by hand, as its own script, in a
    subprocess so that a module which crashes, hangs or calls ``sys.exit`` at
    import time cannot take the harness with it. ``cwd`` is the clone directory
    and Python puts the script's own directory on ``sys.path``, so sibling
    imports resolve exactly as they do in the tests.

    This corpus has no ``if __name__ == "__main__"`` block in any first-party
    file, so running a file executes its top level and nothing more: imports
    plus class and function definitions. No factory is constructed and no socket
    is opened -- exercising the factories is layer 5's job and calling the real
    API is layer 6's.

    The environment is the precondition, not part of the measurement: every
    target the corpus mandates (fastcrc, ujson, ciso8601, httpx, minimalmodbus,
    aiomqtt, apprise) is installed before any arm runs, and C1 and C2 mandate a
    first-party reimplementation that needs no package at all. So a clone that
    cannot be run because its import is missing chose a library it was not asked
    to choose, which is a migration failure and not an environment gap.

    A timeout counts as NOT compiling: a file that blocks forever is not usable,
    whatever the reason.
    """
    for name in program.source_files:
        target = os.path.join(clone_dir, name)
        if not os.path.isfile(target):
            return False
        try:
            ast.parse(open(target, encoding="utf-8").read())
        except SyntaxError:
            return False

    for name in program.source_files:
        try:
            proc = subprocess.run([sys.executable, name], cwd=clone_dir,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        if proc.returncode != 0:
            return False
    return True


#: Target distributions that must be importable before a clone can be tested.
#: The corpus dependency-injects all external I/O, so the *originals* pass with
#: none of these installed — 46/46 on a bare interpreter. A clone that adopts
#: the target library, however, imports it at module scope, so without these a
#: correct migration dies at collection and records as ``api_misuse``. That
#: would blame the model for the harness's missing dependency, and it would do
#: so on exactly the programs where the swap actually succeeded.
def target_packages(programs: list[Program]) -> list[str]:
    """Installable target distributions across the corpus.

    Reimplement cases (C1, C2) contribute nothing: their 'target' is
    first-party code, not a package.
    """
    seen: list[str] = []
    for p in programs:
        if p.reimplement:
            continue
        name = p.to_lib.strip()
        if name and name not in seen and " " not in name:
            seen.append(name)
    return seen


def ensure_targets(programs: list[Program], quiet: bool = False) -> dict:
    """Install the target libraries once, into the running interpreter.

    Returns ``{package: True/False}``. A package that will not install is
    reported rather than raised: ``ciso8601`` needs a C toolchain and may be
    unavailable, and one unbuildable wheel should not stop the other eleven
    programs from being measured. The runner records the shortfall in the row's
    notes so the limitation reaches the paper instead of silently depressing
    the success rate.
    """
    status: dict[str, bool] = {}
    for pkg in target_packages(programs):
        mod = pkg.replace("-", "_")
        try:
            __import__(mod)
            status[pkg] = True
            continue
        except ImportError:
            pass
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", pkg],
            capture_output=True, text=True, timeout=300,
        )
        try:
            __import__(mod)
            status[pkg] = True
        except ImportError:
            status[pkg] = False
            if not quiet:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                print(f"  could not install {pkg}: {tail[-1][:100] if tail else '?'}",
                      file=sys.stderr)
    return status


_BASELINE_CACHE: dict[str, int] = {}


def baseline_tests(program: Program) -> int:
    """How many tests the pristine program has. Measured once, then cached.

    The guide's own instruction: "the number of tests (tests_total) for a
    program is fixed — run the original's suite once to get it, then
    tests_passed on the clone tells you the pass rate." Deriving the total from
    the *clone's* run instead makes a collection error look like a two-test
    suite that failed both, which understates the denominator and flatters the
    pass rate.
    """
    if program.name not in _BASELINE_CACHE:
        work = stage(program)
        try:
            passed, _, _out = _run_suite(work, program)
            _BASELINE_CACHE[program.name] = passed
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return _BASELINE_CACHE[program.name]


def clone_fingerprint(directory: str) -> str:
    """Content hash of every .py file in *directory*, names included.

    The identity of a saved clone. Recorded in the CSV row that describes it, so
    any later re-scoring can prove it is looking at the code that was measured
    rather than at whatever later occupied the same path.
    """
    import hashlib

    digest = hashlib.sha256()
    if not os.path.isdir(directory):
        return ""
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not (name.endswith(".py") and os.path.isfile(path)):
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(open(path, "rb").read())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def save_clone(work: str, clone_root: str, config: str, program=None,
               run_id=None, dest_name: str = "") -> str:
    """Save the run's source into the clone archive. Returns its fingerprint.

    Shared by every runner so the three cannot drift apart, and so the two rules
    below hold everywhere.

    **An existing clone is never written into.** The previous code did
    ``makedirs(exist_ok=True)`` and copied files in one by one, which overwrote
    matching names, left non-matching ones in place, and produced a directory
    that was a MIXTURE of two runs. Seven of 144 rows in
    ``results_FOUR_ARM_FINAL.csv`` ended up describing clones that no longer
    matched what was on disk -- one row dated 2026-09-02 sitting beside files
    dated 2026-08-29 -- and the disagreement was invisible until the clones were
    re-scored weeks later. A superseded directory is renamed aside rather than
    deleted, because it is the only remaining evidence for whatever CSV row
    already described it.

    **Every .py the run produced is copied**, not just ``program.source_files``:
    a reimplementation often ADDS a module, and copying only the pristine file
    list leaves the clone importing a file that is not there.
    """
    import shutil
    import time

    # `dest_name` for callers that save more than one clone per run -- the repair
    # arm keeps attempt 1 alongside the final attempt, so its directory name
    # cannot be derived from the program and run id alone.
    name = dest_name or f"{program.name}_run{run_id}"
    destination = os.path.join(clone_root, config, name)
    if os.path.isdir(destination) and os.listdir(destination):
        superseded = f"{destination}.superseded-{time.strftime('%Y%m%dT%H%M%S')}"
        suffix = 1
        while os.path.exists(superseded):
            suffix += 1
            superseded = (f"{destination}.superseded-"
                          f"{time.strftime('%Y%m%dT%H%M%S')}-{suffix}")
        shutil.move(destination, superseded)
    # `exist_ok=True` because an EMPTY destination is left in place rather than
    # renamed aside -- there is nothing in it to preserve, and an interrupted
    # earlier run leaves exactly that. Raising here would abort a run after the
    # API spend for a directory that holds nothing.
    os.makedirs(destination, exist_ok=True)

    for name in sorted(os.listdir(work)):
        source = os.path.join(work, name)
        if name.endswith(".py") and os.path.isfile(source):
            shutil.copy(source, destination)
    return clone_fingerprint(destination)


#: Baseline smoke verdicts, computed once per program. The pristine original is
#: the same for every clone of a program, so re-running it 12 times per arm
#: would triple the cost of scoring for no new information.
_SMOKE_BASELINE: dict = {}


def smoke_regression(program, clone_dir: str) -> tuple:
    """``(verdict, detail)`` from layer 6 — the program's API on the REAL library.

    Layers 1-4 never execute the ``_default_*`` factory, because every test and
    every probe injects a fake. Layer 5 constructs the factory but stops there.
    This runs one real operation through the program's own public API with
    nothing injected, which is the only way to reach a defect that lives in how
    the migrated code CALLS the new library rather than in how it builds it.

    Verdicts: DEFECT (clone misuses the library and the original does not),
    BASELINE (both fail identically — the subject is not checkable in this
    environment), OK, or SKIPPED where no operation is registered.

    Only DEFECT is a failure.
    """
    # Imported through the package path so editors and type checkers can
    # resolve it. The previous form put `equivalence/` on sys.path at runtime
    # and then did `from smoke_check import ...`, which worked when run but
    # showed as "could not be resolved" to any static analyser.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from equivalence.smoke_check import OPERATIONS, check_each, compare
    except Exception as exc:                                  # noqa: BLE001
        return "SKIPPED", f"layer 6 unavailable: {type(exc).__name__}: {exc}"[:120]
    if program.name not in OPERATIONS:
        return "SKIPPED", "no operation registered"

    # Compared operation by operation, and a clone's failure is excused only
    # when the original fails THAT operation with exactly the same error. The
    # previous version asked only whether the original failed anywhere, so one
    # unrunnable probe (C3's engine_process) excused a real defect in another
    # operation (C3's notify), and an unrelated environment failure in the
    # original (B2, installed pymodbus renamed `unit`) excused a clone that
    # called pyModbusTCP with that same keyword. See smoke_check.compare.
    clone_ops = check_each(clone_dir, program.name,
                           program.from_lib, program.to_lib)
    if not any(v == "MISUSE" for _, v, _, _ in clone_ops):
        return compare(clone_ops, [], clone_dir, program.path)
    if program.name not in _SMOKE_BASELINE:
        _SMOKE_BASELINE[program.name] = check_each(program.path, program.name,
                                                   program.from_lib, program.to_lib)
    return compare(clone_ops, _SMOKE_BASELINE[program.name], clone_dir, program.path)


def oracle_verdict(program, clone_dir: str) -> tuple:
    """Run the equivalence oracle against a clone. Returns ``(verdict, detail)``.

    ``verdict`` is "Y", "N", or "NA" when the program has no recorded probes.

    Needed because the only path to an oracle verdict used to run through the
    Logic Substitution Agent's report, and that agent only executes for C and D.
    Category A therefore recorded ``oracle_equiv=NA`` on every row of every arm,
    leaving ``gen_success`` -- compiles plus tests pass -- as the sole signal for
    a third of the corpus. A is a pure library swap with no logic rewriting, so
    there is no variant to report on; the clone on disk has to be checked
    directly instead.

    Used by every arm, including the single-shot baselines, so the A results are
    comparable across arms rather than only within Proteus.
    """
    # Imported through the package path, like `smoke_regression` above, so
    # editors and type checkers can resolve the names. The previous form added
    # `equivalence/` to sys.path at runtime and then imported the modules flat,
    # which Python follows and a static analyser cannot.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from equivalence.oracle import ProgramOracle
        from equivalence.probes import PROBES
    except Exception as exc:                                  # noqa: BLE001
        # Name the reason rather than returning a bare NA: "this program has no
        # probes" and "the oracle could not be imported" are different facts,
        # and an empty detail made them look identical.
        return "NA", f"oracle unavailable: {type(exc).__name__}: {exc}"[:120]
    if program.name not in PROBES:
        return "NA", ""

    # Every .py in the clone, not just the pristine file list. A variant that
    # EXTRACTS a module imports a file that is not in ``program.source_files``,
    # and scoring only the listed names drops it -- so the clone fails on
    # ``ModuleNotFoundError`` for a module sitting right there in its own
    # directory. Measured on a D1 run whose transport.py was restructured into
    # transport.py + modbus_tk_adapter.py: the Logic Substitution Agent's own
    # oracle passed it, and this one scored oracle_equiv=N on
    # ``No module named 'modbus_tk_adapter'``.
    #
    # Test files are excluded: the suite is run separately by ``run_tests``,
    # and handing a test module to the oracle as program source would have it
    # overwrite the pristine test with the clone's copy.
    sources = {}
    listed = {n for n in program.source_files if n.endswith(".py")}
    try:
        present = sorted(n for n in os.listdir(clone_dir) if n.endswith(".py"))
    except OSError:
        present = sorted(listed)
    for name in present:
        if name.startswith("test_") and name not in listed:
            continue
        path = os.path.join(clone_dir, name)
        if os.path.isfile(path):
            sources[name[:-3]] = open(path, encoding="utf-8",
                                      errors="replace").read()
    if not sources:
        return "NA", ""
    try:
        result = ProgramOracle(program.path, program.name).check_many(sources)
    except Exception as exc:                                  # noqa: BLE001
        # An oracle that cannot run must say so rather than pass the clone.
        return "NA", f"oracle unavailable: {type(exc).__name__}: {exc}"[:120]
    return ("Y" if result.equivalent else "N"), str(result.detail)[:120]


def run_tests(clone_dir: str, program: Program, timeout: int = 120) -> tuple:
    """Tests passing on the clone, against the pristine suite's fixed size.

    Returns ``(passed, total, detail, raw_output)`` where ``total`` comes from the
    original program, so a clone that fails to import scores 0/5 rather than
    0/0 or 0/1.
    """
    passed, _, (detail, raw) = _run_suite(clone_dir, program, timeout)
    return passed, baseline_tests(program), detail, raw


def _run_suite(clone_dir: str, program: Program, timeout: int = 120) -> tuple:
    """Raw suite execution. The suites are dual-mode, so pytest is tried first
    and a direct ``python3 test_x.py`` used as the fallback — that keeps the
    harness working where pytest is missing rather than recording a false zero.
    """
    total_expected = 0
    passed = total = 0
    detail = ""
    raw: list[str] = []
    for test_name in program.test_files:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", test_name],
                cwd=clone_dir, capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            p = sum(int(m) for m in re.findall(r"(\d+) passed", out))
            f = sum(int(m) for m in re.findall(r"(\d+) failed", out))
            e = sum(int(m) for m in re.findall(r"(\d+) error", out))
            if p or f or e:
                passed += p
                total += p + f + e
                detail += out.strip().splitlines()[-1][:120] if out.strip() else ""
                raw.append(out)
                continue
            # pytest produced no recognisable summary (collection error, or
            # pytest absent) — fall through to the dual-mode entry point.
            proc = subprocess.run(
                [sys.executable, test_name], cwd=clone_dir,
                capture_output=True, text=True, timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            m = re.search(r"(\d+)\s+passed[,\s]+(\d+)\s+failed", out)
            if m:
                passed += int(m.group(1))
                total += int(m.group(1)) + int(m.group(2))
            else:
                total += 1
                passed += 1 if proc.returncode == 0 else 0
            detail += out.strip().splitlines()[-1][:120] if out.strip() else ""
            raw.append(out)
        except subprocess.TimeoutExpired:
            detail += f"timeout after {timeout}s; "
            total += 1
    return passed, (total or total_expected), (detail.strip(), "\n".join(raw))


def divergence_score(original_dir: str, clone_dir: str,
                     program: Program) -> float:
    """How similar the clone still is to the original. **Lower = more divergent.**

    The guide allows either a clone detector or ``1 - normalized_edit_distance``
    and asks only that one method be used for every row. We use the second, on
    the token stream rather than raw text, so that reformatting and comment
    changes do not read as diversification. Computed over the concatenation of
    all first-party files so multi-file programs get one number.

    Note the direction: this is a *similarity*, so an identical clone scores
    1.0 and a total rewrite approaches 0.0. That is the opposite of the
    diversity metrics used elsewhere in AXIOM, and matching the guide's
    convention matters more here than matching our own.
    """
    def tokens(directory: str) -> list[str]:
        out: list[str] = []
        for name in program.source_files:
            p = os.path.join(directory, name)
            if not os.path.isfile(p):
                continue
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError:
                out.extend(open(p, encoding="utf-8").read().split())
                continue
            out.extend(type(n).__name__ for n in ast.walk(tree))
        return out

    a, b = tokens(original_dir), tokens(clone_dir)
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def loc_changed(original_dir: str, clone_dir: str, program: Program) -> int:
    """Lines differing between original and clone across first-party files."""
    n = 0
    for name in program.source_files:
        o = os.path.join(original_dir, name)
        c = os.path.join(clone_dir, name)
        ol = open(o, encoding="utf-8").read().splitlines() if os.path.isfile(o) else []
        cl = open(c, encoding="utf-8").read().splitlines() if os.path.isfile(c) else []
        n += sum(1 for line in difflib.unified_diff(ol, cl, n=0)
                 if line.startswith(("+", "-"))
                 and not line.startswith(("+++", "---")))
    return n


def migrated(original_dir: str, clone_dir: str, program: Program) -> bool:
    """Did a MIGRATION happen? Not "did bytes change".

    All of these must hold:

        the source changed at all            loc_changed > 0
        the old dependency is gone           no live import of it remains
        the new one is imported              ... and for a library swap,
        the new one is actually CALLED       some name it binds is referenced

    Two earlier definitions were both wrong in ways that showed up in the CSV.
    ``divergence_score < 0.999`` compared AST node TYPE names, so renaming
    ``requests.Session()`` to ``httpx.Client()`` left the shape identical and a
    correct swap scored as a no-op -- while an agent that injected adapter
    functions always registered as changed, which biased the comparison toward
    verbose rewrites. Replacing it with ``loc_changed > 0`` fixed that and
    introduced a different lie: A3 proteus_full run1 has one changed line, no
    library swapped, and was recorded ``migrated=Y``. One edited line is not a
    migration.

    ``lib_swapped_correctly`` and the API-usage check stay in their own columns
    so a reader can still see WHICH part failed.
    """
    if loc_changed(original_dir, clone_dir, program) <= 0:
        return False
    if library_swapped(clone_dir, program) != "Y":
        return False
    # A first-party rewrite has no library to call; "the dependency is gone and
    # the source changed" is the whole requirement there.
    if program.reimplement:
        return True
    return target_api_used(clone_dir, program)


#: For stdlib sources, the call the migration is actually meant to replace.
#: The module itself may still be needed for other reasons after a correct
#: swap, so presence of the import is not evidence the migration failed —
#: presence of the *replaced call* is.
_STDLIB_SOURCES = {
    "datetime": r"\bdatetime\s*\.\s*(datetime\s*\.\s*)?fromisoformat\b",
    "json": r"\bjson\s*\.\s*(dumps|loads|dump|load)\b",
    "zlib": r"\bzlib\s*\.\s*crc32\b",
    "smtplib": r"\bsmtplib\s*\.\s*SMTP\b",
}


def _code_only(source: str) -> str:
    """Strip comments and string literals, leaving executable code.

    Matching library usage against raw text gives false negatives from prose:
    A3's clone correctly calls ``ciso8601.parse_datetime``, but its module
    docstring still says "datetime.fromisoformat (stdlib) -> ciso8601" because
    the model preserved the original's documentation. Regexing the file as text
    found that sentence and scored a correct migration as unswapped, on all
    three seeds.
    """
    import io
    import tokenize

    kept: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source          # unparseable: fall back rather than claim nothing
    return " ".join(kept)


def _clone_modules(clone_dir: str, program: Program) -> list:
    """Every first-party .py in the clone, not just the pristine file list.

    A variant that EXTRACTS a module puts real first-party code in a file that
    is not in ``program.source_files``, and every scorer that iterates the
    pristine list stops seeing it. Measured on a D1 clone whose transport.py
    was restructured into transport.py + modbus_shim.py, moving the lone
    ``import modbus_tk`` into the extracted file: ``chosen_target`` answered
    ``clamp_utils`` -- another extracted helper, first in sort order --
    ``target_importable`` then reported N for a package that is installed, and
    ``target_api_used`` returned True by matching that helper's ``clamp``. So
    ``migrated`` certified "the adopted library is actually called" on the
    strength of a first-party function call, and ``migration_success`` was
    granted to a row whose library usage had never been checked.

    ``oracle_verdict`` already collects files this way; these three scorers
    were simply not updated with it. Test files are excluded for the reason
    given there: the suite is run separately and its imports are not the
    program's own.
    """
    listed = {n for n in program.source_files if n.endswith(".py")}
    try:
        present = sorted(n for n in os.listdir(clone_dir) if n.endswith(".py"))
    except OSError:
        return sorted(listed)
    return [n for n in present
            if not (n.startswith("test_") and n not in listed)]


def library_swapped(clone_dir: str, program: Program) -> str:
    """Y/N: the target library is imported and the source one is gone.

    For the reimplement cases (C1, C2) there is no target package, so the test
    is only that the dependency was removed.
    """
    text = "\n".join(
        _code_only(open(os.path.join(clone_dir, n), encoding="utf-8").read())
        for n in _clone_modules(clone_dir, program)
        if os.path.isfile(os.path.join(clone_dir, n))
    )
    # A PyPI distribution name is not its module name, and the two differ in
    # opposite directions across this corpus:
    #
    #   paho-mqtt   -> module ``paho``        (first segment wins)
    #   simple-pid  -> module ``simple_pid``  (underscore form wins)
    #
    # Taking only ``split("-")[0]`` searched for ``\bsimple\b``, which cannot
    # match inside ``simple_pid`` because ``_`` is a word character. Every C1
    # run was therefore scored as having removed a dependency that is still
    # imported on line 13 of the clone -- a migration credited to all three
    # arms that none of them performed.
    #
    # Both candidate forms are checked and the dependency counts as present if
    # EITHER appears. That errs toward "not removed", which is the safe
    # direction: it can understate success but can never invent it.
    lib = program.from_lib.split(".")[0].strip().lower()
    src_roots = {lib.replace("-", "_"), lib.split("-")[0]}
    src_root = sorted(src_roots)[0]           # for the stdlib lookup below
    source_gone = not any(
        re.search(rf"\b(import|from)\s+{re.escape(root)}\b", text, re.I)
        for root in src_roots if root
    )

    # A stdlib source module often has to stay imported after a correct swap,
    # because the replacement covers only one of its uses. A3 migrates
    # datetime.fromisoformat -> ciso8601, but the parsed result is still a
    # datetime object, so `import datetime` legitimately remains — and all
    # three A3 runs passed 4/4 while being scored swap=N. What matters for
    # these is that the specific replaced CALL is gone, not the whole module.
    if src_root in _STDLIB_SOURCES and not source_gone:
        replaced_call = _STDLIB_SOURCES[src_root]
        source_gone = not re.search(replaced_call, text, re.I)

    if program.reimplement:
        return "Y" if source_gone else "N"
    tgt_root = program.to_lib.split("-")[0].split("[")[0].strip().lower()
    target_used = bool(re.search(rf"\b(import|from)\s+{re.escape(tgt_root)}\b",
                                 text, re.I))
    if target_used and source_gone:
        return "Y"
    # Free-choice runs are not told which library to adopt, so demanding the
    # corpus's declared target would score a perfectly good migration to a
    # different package as a failure. What the task actually requires is that
    # the old dependency is gone and something real took its place; which
    # package that is gets recorded separately in ``chosen_target``.
    if source_gone and chosen_target(clone_dir, program):
        return "Y"
    return "N"


def chosen_target(clone_dir: str, program: Program) -> str:
    """The third-party module the clone adopted, or "" if none.

    Any top-level import present in the clone but absent from the original,
    excluding the standard library and the program's own modules. Under
    ``--free-choice`` this is the model's answer to "what should replace this?"
    and is the only record of it, so it is derived from the code rather than
    trusted from the ``# CHOSEN-TARGET:`` comment the prompt asks for --- a
    comment can claim one library while the code imports another.
    """
    own = ({n[:-3] for n in program.source_files if n.endswith(".py")}
           | {n[:-3] for n in _clone_modules(clone_dir, program)})

    def roots(directory: str, names) -> set:
        found = set()
        for name in names:
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    found.add(node.module.split(".")[0])
        return found

    added = (roots(clone_dir, _clone_modules(clone_dir, program))
             - roots(program.path, program.source_files)
             - own - set(sys.stdlib_module_names))
    return sorted(added)[0] if added else ""


#: Exceptions that mean "the code called the library wrongly", as opposed to
#: "the code computed the wrong answer". Distinguishing them is the whole point
#: of the ``api_misuse`` vs ``logic_error`` split, and a pass-count heuristic
#: cannot tell them apart: a clone that misuses its new library still passes any
#: test that never touches it, which would misfile the run as a logic error.
_API_MISUSE_SIGNS = (
    "TypeError", "AttributeError", "ImportError", "ModuleNotFoundError",
    "NameError", "is not callable", "has no attribute",
)


def classify_failure(compiled: bool, passed: int, total: int,
                     converged: bool = True, output: str = "",
                     oracle: str = "", swapped: str = "",
                     api_used: str = "", changed: int | None = None) -> str:
    """The guide's ``failure_mode`` taxonomy, applied consistently.

    Order matters: a clone that does not compile is a ``compile`` failure even
    though its tests also fail, so the more fundamental cause wins.

    When test output is available it decides between ``api_misuse`` and
    ``logic_error`` by what actually blew up, rather than by how many tests
    survived. An ``AssertionError`` means the code ran and produced the wrong
    number; a ``TypeError`` on the target library means it was called wrongly.
    """
    if not compiled:
        return "compile"
    if not converged:
        return "nonconvergence"
    if total and passed == total:
        # The tests passing is not the end of it. A row can pass every test and
        # still fail `migration_success`, and recording "none" there said the
        # row failed for no reason at all -- which is how 19 rows came to read
        # failure_mode=none beside failing_stage=LMA.
        if oracle == "N":
            return "oracle_divergence"
        if swapped == "N":
            return "source_import_remains"
        if api_used == "N":
            return "target_never_called"
        if changed is not None and changed <= 0:
            return "no_op"
        return "none"
    if output:
        if any(sign in output for sign in _API_MISUSE_SIGNS):
            return "api_misuse"
        if "AssertionError" in output:
            return "logic_error"
    return "api_misuse" if passed == 0 else "logic_error"


def detect_hallucination(output: str, program: Program) -> str:
    """Y when the failure names an attribute the target library does not have.

    The guide asks whether the model "invented a nonexistent API/library".
    A clone that calls ``fastcrc.crc32(payload)`` when ``fastcrc.crc32`` is a
    module rather than a function produces exactly this signature, and it is a
    different and more interesting failure than getting the arithmetic wrong.
    """
    if not output:
        return "N"
    root = (program.to_lib.split("-")[0].split("[")[0].strip().lower()
            if not program.reimplement else "")
    invented = ("has no attribute" in output
                or "is not callable" in output
                or "ModuleNotFoundError" in output)
    if invented and (not root or root in output.lower()
                     or "AttributeError" in output or "TypeError" in output):
        return "Y"
    return "N"


# ── scratch dirs & CSV ────────────────────────────────────────────────────────

def stage(program: Program) -> str:
    """A throwaway copy of the program. Caller is responsible for removing it."""
    work = tempfile.mkdtemp(prefix=f"proteus-{program.name}-")
    for name in os.listdir(program.path):
        src = os.path.join(program.path, name)
        if os.path.isfile(src):
            shutil.copy(src, work)
    return work


def target_api_used(clone_dir: str, program: Program) -> bool:
    """Is the adopted library actually CALLED, not merely imported?

    An import on its own proves nothing. A D2 clone produced by the connector
    reads::

        def _default_session():
            import httpx                  # the new library, never touched
            _spt_r1 = requests.Session()  # the old one, no longer imported
            return _spt_r1

    That file imports the target, has no import of the source, compiles, and
    passes 3/3 tests -- and raises ``NameError`` the moment anything calls it.
    Checking imports alone scores it as a completed migration.

    So the rule is: some name bound by importing the adopted library must be
    referenced somewhere other than the import statement itself. Reimplement
    subjects (C1, C2) have no target package and are excluded by the caller.

    Returns False when no adopted library can be identified, which is the safe
    direction: it can understate success, never invent it.
    """
    roots: set[str] = set()
    for spelling in (chosen_target(clone_dir, program), program.to_lib):
        root = (spelling or "").split("-")[0].split("[")[0].strip().lower()
        if root:
            roots.add(root)
    if not roots:
        return False

    bound: set[str] = set()
    used: set[str] = set()
    for name in _clone_modules(clone_dir, program):
        path = os.path.join(clone_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:
            continue
        import_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_nodes.add(node)
                for alias in node.names:
                    if alias.name.split(".")[0].lower() in roots:
                        bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                import_nodes.add(node)
                if node.module.split(".")[0].lower() in roots:
                    for alias in node.names:
                        bound.add(alias.asname or alias.name)
        # Every Name/Attribute reference that is NOT part of an import.
        inside = {child for imp in import_nodes for child in ast.walk(imp)}
        for node in ast.walk(tree):
            if node in inside:
                continue
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                used.add(node.value.id)
    return bool(bound & used)


def target_importable(clone_dir: str, program: Program) -> str:
    """``Y``/``N``/``NA`` -- is the library this clone adopted importable here?

    Measured identically for every arm. It used to exist only on
    ``single_shot_repair``, which is the one arm that also INSTALLS the model's
    choice mid-run; the other three adopt libraries too, and a clone that
    imports a package nobody can install is a different failure from one that
    computes the wrong answer.

    ``NA`` means there is no third-party import to check -- a first-party
    rewrite, or a clone that adopted nothing. ``N`` means the name does not
    resolve, which for a name absent from PyPI is the invented-library failure.

    Nothing is installed here. On the repair arm ``Y`` may mean "importable
    after that arm installed it during the run"; everywhere else it means
    "importable as the environment stands".
    """
    import importlib.util

    root = chosen_target(clone_dir, program)
    if not root:
        return "NA"
    try:
        return "Y" if importlib.util.find_spec(root) is not None else "N"
    except (ImportError, ValueError, ModuleNotFoundError):
        return "N"


def migration_ok(original_dir: str, clone_dir: str, program: Program) -> bool:
    """The single verdict behind ``migration_success``.

        compiles      the clone imports without raising
        tests         every test in the program's own suite passes
        oracle        the equivalence oracle did not reject it
        migrated      see `migrated` -- old dependency gone, new one imported
                      AND actually called, and the source really changed
        runs          layer 6: the program's public API, exercised against the
                      real library with nothing injected, does not misuse it

    ``gen_success`` used to sit between these as "compiles and tests pass", but
    the oracle branch overwrote it, so a row could read compiles=Y, tests 3/3,
    gen_success=N and look self-contradictory. It was removed rather than
    patched: one verdict with its components in their own columns is clearer
    than two overlapping ones.
    """
    passed, total, _, _ = run_tests(clone_dir, program)
    if not (compiles(clone_dir, program) and total > 0 and passed == total):
        return False
    if oracle_verdict(program, clone_dir)[0] == "N":
        return False
    if not migrated(original_dir, clone_dir, program):
        return False
    # Layer 6: the program's own API, called with the real library and nothing
    # injected. A clone can satisfy everything above and still raise NameError
    # the first time anything constructs it -- D2 proteus_full run2 does
    # exactly that, and passed all five earlier layers. DEFECT is the only
    # failing verdict; BASELINE (the original fails the same way here) and
    # SKIPPED (no injection seam to exercise) are not held against the clone.
    return smoke_regression(program, clone_dir)[0] != "DEFECT"


def blank_row(program: Program, config: str, run_id: int, model: str) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update(program=program.name, category=program.category, config=config,
               run_id=run_id, model=model, date=date.today().isoformat())
    # Columns that genuinely do not apply to a configuration are "NA" rather
    # than blank; the guide distinguishes the two, and blank means "could not
    # measure", which is a different claim.
    if config in ("single_shot", "single_shot_repair"):
        # Neither arm runs agents, so the per-agent iteration counters and the
        # VCA columns have no value to report.
        for col in ("vca_reentries", "failing_stage",
                    "iters_lsa", "iters_gsa", "iters_lma",
                    "oracle_divergence_loc", "provenance_complete"):
            row[col] = "NA"
    if config != "single_shot_repair":
        for col in REPAIR_COLUMNS:
            row[col] = "NA"
    return row


def append_row(csv_path: str, row: dict) -> None:
    """Append one row, creating the file with headers if needed.

    Appends rather than rewrites so a long run that dies partway keeps every
    row it already earned.
    """
    exists = os.path.isfile(csv_path)

    # Guard against a schema that grew mid-experiment. The header is written
    # only on creation, so adding a column to COLUMNS later meant new rows
    # carried 39 fields into a file whose header still declared 37 — the two
    # extra values spilled past the last named column and became unreadable by
    # name. It happened: single_shot rows were written under the old schema,
    # then the AXIOM arms under the new one, and the file needed repairing.
    #
    # If the existing header no longer matches, migrate the file in place:
    # re-read every row under its own header, pad the new columns, and rewrite
    # with the current schema. Losing rows to a schema change would be worse
    # than the mismatch it fixes.
    if exists:
        try:
            with open(csv_path, newline="", encoding="utf-8") as handle:
                existing = list(csv.reader(handle))
        except OSError:
            existing = []
        if existing and existing[0] != COLUMNS:
            old_header = existing[0]
            migrated = []
            for values in existing[1:]:
                if len(values) == len(COLUMNS):
                    prior = dict(zip(COLUMNS, values))
                else:
                    prior = dict(zip(old_header, values))
                migrated.append({c: prior.get(c, "") for c in COLUMNS})
            with open(csv_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=COLUMNS,
                                        extrasaction="ignore")
                writer.writeheader()
                writer.writerows(migrated)
            print(f"note: {os.path.basename(csv_path)} header was out of date; "
                  f"migrated {len(migrated)} row(s) to the current schema",
                  file=sys.stderr)

    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS,
                                extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in COLUMNS})
