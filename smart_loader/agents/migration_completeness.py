"""Acceptance checks the VCA can run that do NOT go through the project tests.

Why this module exists
──────────────────────
The Verification Agent's Stage 1 gate was::

    stage1_clean = (baseline_environment_ready and patched_analysis_ready
                    and smoke_ok and patched_tests_ok
                    and not deterministic_errors)

Every one of those is satisfied by a migration that did not happen. Measured
over 36 ``proteus_full`` runs, the verifier re-entered on 2 and reported
``converged=Y`` on all 36 -- including all 10 failures. It signed off on a D3
clone whose ``sink.py`` still contained eight references to paho, and on a D2
clone whose ``client.py`` still referenced ``requests``.

The cause is ``patched_tests_ok``. The corpus suites inject fakes whose
signatures match the ORIGINAL library, so a clone that calls the fake correctly
and the real library incorrectly passes every test. The verifier was downstream
of the one oracle that cannot see the injection seam, so it was re-measuring
``tests_passed`` -- already 34/36 -- and therefore almost never fired.

The three checks here are deliberately chosen to use only information available
at migration time:

    1. the source library is still imported            (static)
    2. the target library is imported but never called (static)
    3. the program's own API misuses the real library  (executed, no expected
                                                        values compared)

None of them reads a golden vector, and none of them consults the corpus's
expected outputs. That separation is the point: the VCA is an acceptance gate,
and if it were handed the same golden vectors the scoring oracle uses, the
system would be graded on a test it had been given the answers to.

Check 3 overlaps in mechanism with scoring layer 6 -- it is the same operation
run at a different time -- and that overlap should be stated plainly in any
write-up rather than left for a reader to discover.

Scope
─────
The verifier runs once per source file, so checks 1 and 2 examine only the file
this invocation is responsible for. Complaining about a sibling nobody has
touched yet would spend a repair cycle on the wrong file. Check 3 executes the
whole program, so it is attributed by traceback: only a failure whose deepest
in-project frame is the current file counts here.

Every check errs toward silence. A check that cannot determine an answer
returns no error, so this module can understate a problem but can never invent
one.
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
import tokenize

#: For stdlib sources, the call the migration is actually meant to replace.
#: The module itself often has to stay imported after a correct swap -- A3
#: migrates ``datetime.fromisoformat`` to ciso8601 but still handles datetime
#: objects -- so the presence of the import is not evidence of failure. The
#: presence of the replaced CALL is. Kept in step with the same table in
#: ``smart_loader/eval/proteus_harness.py``.
STDLIB_SOURCES: dict[str, str] = {
    "datetime": r"\bdatetime\s*\.\s*(datetime\s*\.\s*)?fromisoformat\b",
    "json": r"\bjson\s*\.\s*(dumps|loads|dump|load)\b",
    "zlib": r"\bzlib\s*\.\s*crc32\b",
    "smtplib": r"\bsmtplib\s*\.\s*SMTP\b",
}


def _code_only(source: str) -> str:
    """Strip comments and string literals, leaving executable code.

    Matching a library name against raw text gives false positives from prose:
    A3's clone correctly calls ``ciso8601.parse_datetime`` while its docstring
    still reads "datetime.fromisoformat (stdlib) -> ciso8601". Regexing the
    file as text finds that sentence and reports a correct migration as
    unmigrated.
    """
    kept: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source
    return " ".join(kept)


def _roots(spelling: str) -> set[str]:
    """Module roots a distribution name might import as.

    A PyPI distribution name is not its module name, and the two differ in
    opposite directions across this corpus: ``paho-mqtt`` imports as ``paho``
    (first segment wins) while ``simple-pid`` imports as ``simple_pid``
    (underscore form wins). Both spellings are checked.
    """
    lib = (spelling or "").split(".")[0].split("[")[0].strip().lower()
    if not lib:
        return set()
    return {r for r in (lib.replace("-", "_"), lib.split("-")[0]) if r}


def source_import_errors(patched_code: str, migration_source: str) -> list[dict]:
    """Check 1 — the library the migration was told to remove is still here."""
    roots = _roots(migration_source)
    if not roots:
        return []
    text = _code_only(patched_code)
    present = [
        root for root in roots
        if re.search(rf"\b(import|from)\s+{re.escape(root)}\b", text, re.I)
    ]
    if not present:
        return []

    # A stdlib source may legitimately remain imported; only the replaced call
    # is evidence the migration did not happen.
    root = sorted(roots)[0]
    if root in STDLIB_SOURCES:
        if not re.search(STDLIB_SOURCES[root], text, re.I):
            return []
        return [{
            "check": "migration_completeness", "line": 1,
            "message": (
                f"The call this migration replaces is still present: "
                f"{migration_source} is used via a call matching "
                f"{STDLIB_SOURCES[root]!r}. Rewrite that call site to the "
                f"replacement; keeping `import {root}` is fine, keeping the "
                f"call is not."
            ),
        }]

    return [{
        "check": "migration_completeness", "line": 1,
        "message": (
            f"The source library is still imported: found `import {present[0]}` "
            f"after migrating away from {migration_source}. Remove every import "
            f"of it and route the code through the replacement."
        ),
    }]


def target_api_errors(patched_code: str, migration_target: str) -> list[dict]:
    """Check 2 — the target is imported but nothing ever calls it.

    The exact shape this exists for, produced by the connector on D2::

        def _default_session():
            import httpx                  # the new library, never touched
            _spt_r1 = requests.Session()  # the old one, no longer imported
            return _spt_r1

    That file imports the target, has no import of the source, compiles, and
    passes 3/3 tests -- and raises ``NameError`` the moment anything calls it.
    An import on its own proves nothing; some name BOUND by importing the
    target must be referenced somewhere other than the import statement.

    Silent when the file does not import the target at all: a file that never
    needed the new library is not evidence of an incomplete migration, and this
    check has no business guessing which files should use it.
    """
    roots = _roots(migration_target)
    if not roots:
        return []
    try:
        tree = ast.parse(patched_code)
    except SyntaxError:
        return []          # the syntax check already owns this failure

    bound: set[str] = set()
    import_nodes: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in roots:
                    import_nodes.add(node)
                    bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.split(".")[0].lower() in roots:
                import_nodes.add(node)
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
    if not bound:
        return []

    inside = {child for imp in import_nodes for child in ast.walk(imp)}
    used: set[str] = set()
    for node in ast.walk(tree):
        if node in inside:
            continue
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                used.add(base.id)
    if bound & used:
        return []

    return [{
        "check": "migration_completeness", "line": 1,
        "message": (
            f"{migration_target} is imported as {sorted(bound)!r} but never "
            f"referenced outside the import statement, so the migrated code "
            f"path cannot be executing it. Call the new library where the old "
            f"one used to be called."
        ),
    }]


def real_library_errors(project_root: str, program_name: str, current_file: str,
                        migration_source: str = "",
                        migration_target: str = "") -> list[dict]:
    """Check 3 — construct the program with no fake injected and call it once.

    This is the deterministic form of a defect class the corpus tests cannot
    reach. C3's ``_default_smtp`` returns a ``redmail.EmailSender`` quite
    happily and ``notify`` then calls ``.sendmail(...)`` on it, which redmail
    does not have; B2 builds its client fine and then passes ``unit=`` to a
    library whose parameter is ``slave=``. Both pass their suites, because the
    suites inject a fake matching the original library's signature.

    Only a MISUSE attributed to ``current_file`` is reported. The verifier runs
    per file, so a project that is half migrated would otherwise raise a defect
    caused by a sibling that has not been touched yet and spend this file's
    repair budget on someone else's problem.

    Silent -- deliberately -- when no operation is registered for the program,
    when the target library is simply absent, or when the collaborator is
    merely unreachable. Nothing here compares an expected value; it only asks
    whether calling the real library raises the kind of error that means the
    call is malformed.
    """
    equivalence_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "equivalence",
    )
    if not os.path.isdir(equivalence_dir) or not os.path.isdir(project_root):
        return []
    if equivalence_dir not in sys.path:
        sys.path.insert(0, equivalence_dir)
    try:
        import smoke_check  # noqa: PLC0415  (optional, path-dependent import)
    except ImportError:
        return []
    if program_name not in getattr(smoke_check, "OPERATIONS", {}):
        return []

    try:
        verdict, detail, origin = smoke_check.check(
            project_root, program_name, migration_source, migration_target,
            with_origin=True,
        )
    except Exception:
        # An acceptance check must never be able to fail the run it is judging.
        return []
    if verdict != "MISUSE":
        return []
    if origin and current_file and origin != os.path.basename(current_file):
        return []

    return [{
        "check": "real_library_call", "line": 1,
        "message": (
            f"Calling this program's own API with the real library -- nothing "
            f"injected -- fails in {origin or current_file}: {detail}. The "
            f"project tests pass because they inject a fake matching the OLD "
            f"library's signature; fix the call to match the new library's "
            f"actual signature."
        ),
    }]


def error_contract_errors(source_code: str, patched_code: str) -> list[dict]:
    """Check 4 -- the patch added a handler that stops an error the original raised.

    D3's migrated sink wrapped its publish call in ``except Exception: return
    _Result(1)``. The original had no handler, so paho's ``ValueError: Publish
    topic cannot contain wildcards`` propagated out of ``MqttSink.publish``.
    Measured side by side:

        ORIGINAL (paho)  -> RAISED ValueError: Publish topic cannot contain wildcards.
        CLONE   (gmqtt)  -> returned rc=1

    ``gateway.py`` discards that return value, so a telemetry gateway that used
    to fail loudly began silently dropping data -- while scoring a clean sweep
    on every other layer, because the injected test fakes never raise, the
    golden vectors are all happy-path, and layer 6 makes one SUCCESSFUL call.

    Measured over the archive, every such handler in both proteus arms (14/14
    and 11/11) sits in Connector-generated glue: wrapping a call to normalise
    one library's error style into another's is precisely how the caller's
    contract gets broken. This is the check that makes it repairable instead of
    shipped.

    Differential, and in memory: the verifier already holds the original and
    the patch for the file it is judging, so no directory walk is needed and a
    handler the original already had is never reported.
    """
    equivalence_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "equivalence",
    )
    if not os.path.isdir(equivalence_dir):
        return []
    if equivalence_dir not in sys.path:
        sys.path.insert(0, equivalence_dir)
    try:
        import error_contract  # noqa: PLC0415  (optional, path-dependent import)
    except ImportError:
        return []

    try:
        before = error_contract.handler_signatures(source_code)
        after = error_contract.handler_signatures(patched_code)
    except Exception:
        # An acceptance check must never fail the run it is judging.
        return []

    errors: list[dict] = []
    for where, caught, action in sorted(after - before):
        if action in error_contract._PROPAGATES:
            continue
        if where.rsplit(".", 1)[-1] in error_contract._SWALLOWING_IS_CORRECT:
            continue
        shown = ", ".join(c or "<bare except>" for c in caught)
        errors.append({
            "check": "error_contract", "line": 1,
            "message": (
                f"{where or '<module>'} catches ({shown}) and {action}s instead "
                f"of re-raising. The original propagated this failure to the "
                f"caller. Re-raise it -- add `raise` at the end of the handler, "
                f"or `raise NewError(...) from exc` -- so a caller that relied "
                f"on the error still sees one."
            ),
        })
    return errors


def behaviour_errors(source_code: str, patched_code: str) -> list[dict]:
    """Check 5 -- the patch changed what the module returns.

    Runs the ORIGINAL and the PATCHED module on generated inputs and compares.
    The original is the ground truth by definition, so this needs no golden
    vector and no expected output; everything it uses is available at migration
    time, and the scoring oracle's answer key stays out of the gate.

    The defect it exists for: A2 migrates json -> ujson, which escapes forward
    slashes by default while the stdlib does not, so a tag containing a slash
    serializes as ``"a\\/b"`` instead of ``"a/b"``. The program's contract is
    byte-reproducible audit logs, so that is real, and it is one flag away from
    correct (``escape_forward_slashes=False``). All three A2 seeds failed on it
    in every arm.

    Nothing could see it. The suite passes -- no test uses a slash. Layer 6
    constructs nothing, because A2 has no collaborator. Only the golden vectors
    caught it, and the Verifier is blind to those by design, so the repair loop
    was never told what was wrong and could not fix it.

    Measured before wiring: zero false positives across all twelve originals
    compared with themselves, and it fires on two of thirty-six clones -- both
    already failing. It never fires on a passing row, which is the property
    check 4 lacked.
    """
    equivalence_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "equivalence",
    )
    if not os.path.isdir(equivalence_dir):
        return []
    if equivalence_dir not in sys.path:
        sys.path.insert(0, equivalence_dir)
    try:
        import differential  # noqa: PLC0415  (optional, path-dependent import)
    except ImportError:
        return []
    try:
        verdict, detail = differential.check(source_code, patched_code)
    except Exception:
        # An acceptance check must never fail the run it is judging.
        return []
    if verdict != "DEFECT":
        return []
    return [{
        "check": "behaviour_divergence", "line": 1,
        "message": (
            f"The migrated module returns something different from the "
            f"original: {detail}. Same inputs, same function -- the "
            f"replacement library's defaults differ from the source's. Pass "
            f"whatever option restores the original behaviour (for a "
            f"serializer this is usually an escaping or separator flag)."
        ),
    }]


def completeness_errors(patched_code: str, migration_source: str,
                        migration_target: str, *, project_root: str = "",
                        program_name: str = "", current_file: str = "",
                        include_runtime: bool = True,
                        source_code: str | None = None) -> list[dict]:
    """All three checks, in increasing order of cost.

    The static pair runs always. The executed check runs last and only when a
    caller supplies a project root and a registered program name, so a verifier
    with nothing to execute against degrades to the static checks rather than
    erroring.
    """
    errors = source_import_errors(patched_code, migration_source)
    errors += target_api_errors(patched_code, migration_target)
    if source_code is not None:
        errors += error_contract_errors(source_code, patched_code)
        errors += behaviour_errors(source_code, patched_code)
    if include_runtime and project_root and program_name:
        errors += real_library_errors(project_root, program_name, current_file,
                                      migration_source, migration_target)
    return errors
