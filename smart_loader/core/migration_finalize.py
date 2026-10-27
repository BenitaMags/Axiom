"""The deterministic tail of a migration, shared by every path that performs one.

What this is, and why it is not part of the Connector
────────────────────────────────────────────────────
Producing a migration has two halves. One is generative and needs a model: read
the call sites, decide what an adapter must do, write it. The other is
mechanical and must not involve a model at all:

    1. rewrite the outgoing import to the incoming one
    2. repoint recorded call sites at their wrapper
    3. delete imports the migration has left unreferenced
    4. refuse a patch that still imports the library it was told to remove

Those four lived inside ``connector_agent`` next to the ACI editing session,
which made them look like part of the Glue-Synthesis Agent. They are not. Step
1 alone is what turns a working adapter into a completed migration: without it
the file keeps ``from simple_pid import PID``, the dependency is not removed,
and the run scores ``lib_swapped=N`` however good the new code is.

That distinction matters now that a second path exists. The First-Party
Reimplementation Agent writes an implementation with the *same* API surface, so
there is no interface delta and no reason to run an editing session over a file
that needs no edits — but it needs all four of these steps exactly as the
Connector does. Keeping one implementation is also the fix for a defect that
already cost this project several days: the distribution-name-versus-import-root
bug survived in four separate copies of this logic, each silently matching
nothing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from smart_loader.core.ast_rewrite import (
    apply_call_site_rewrites,
    apply_import_replacements,
    resolve_import_root,
)


@dataclass
class FinalizeRequest:
    """One outgoing -> incoming pair to complete.

    ``call_rewrites`` is whatever the generative half recorded: for the
    Connector, the analysis pass's ``{line, call, wrapper_name}`` entries; for
    the FRA, usually empty, because an implementation that preserves the API
    surface leaves every call site already correct.
    """

    original: str                       # outgoing distribution, e.g. "simple-pid"
    #: Incoming module root, e.g. ``httpx``. EMPTY means there is no incoming
    #: module: the First-Party Reimplementation Agent defines the replacement
    #: inline in the same file, so the outgoing import must simply be GONE, not
    #: redirected. Rewriting it to a module that does not exist would trade a
    #: missing dependency for an ImportError.
    winner_import: str = ""
    call_rewrites: list = field(default_factory=list)
    original_import: str = ""           # declared root, used only as a fallback


@dataclass
class FinalizeResult:
    code: str
    import_rewrites: dict = field(default_factory=dict)
    calls_rewritten: list = field(default_factory=list)
    imports_dropped: list = field(default_factory=list)
    #: ``{original: ["line 12: from simple_pid import PID", ...]}`` — imports of
    #: the outgoing library still LIVE after all four steps. Non-empty means the
    #: migration is incomplete and the caller must reject it.
    survivors: dict = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.survivors


def remaining_source_imports(source_code: str, root: str) -> list[str]:
    """Import statements of ``root`` still present, as ``"line N: <stmt>"``.

    Run after the rewrite and the sweep, so anything found is an import the
    migration genuinely left live -- some code path still references the name.
    That is not cosmetic: the clone cannot run without the old distribution
    installed, which is precisely what it was asked to remove.

    ``ast.walk`` rather than ``tree.body``: nine of the twelve evaluation
    programs import their outgoing library inside a function ("lazy import for
    testability"), so a module-level-only scan misses almost all of them.
    """
    if not root:
        return []
    root = root.split(".")[0].casefold()
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    lines = source_code.splitlines()
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hit = any(a.name.split(".")[0].casefold() == root for a in node.names)
            label = ",".join(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            hit = node.module.split(".")[0].casefold() == root
            label = node.module
        else:
            continue
        if hit:
            lineno = getattr(node, "lineno", 0)
            text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else label
            found.append(f"line {lineno}: {text}")
    return found


def drop_unused_source_imports(source_code: str,
                               roots: set[str]) -> tuple[str, list[str]]:
    """Remove imports of ``roots`` whose bound names are unreferenced.

    Delegates to the libCST pass in ``dead_import_prune``; kept as a name here
    so both callers reach it through this module rather than importing the
    pruner directly and drifting apart again.
    """
    from smart_loader.core.dead_import_prune import prune_dead_source_imports

    dropped: list[str] = []
    for root in sorted(roots):
        source_code, removed = prune_dead_source_imports(source_code, root)
        dropped.extend(removed)
    return source_code, dropped


def finalize_migration(source_code: str, working_source: str,
                       requests: list[FinalizeRequest]) -> FinalizeResult:
    """Apply the four mechanical steps. Never calls a model.

    ``source_code`` is the PRISTINE original and is used only to resolve the
    outgoing library's import root -- a property of the library, read from a
    file we know still contains the import. ``working_source`` is what the
    generative half produced and is what gets rewritten.

    Failure of any individual step is survivable and recorded; the caller
    decides what to do. Only ``survivors`` is a verdict, and it is the one the
    caller must honour: a non-empty entry means the outgoing library is still
    imported and in use, so the migration is not finished.
    """
    result = FinalizeResult(code=working_source)

    # 1. imports: outgoing root -> incoming root. Resolved from the file's own
    #    imports, never guessed from the distribution name -- `paho-mqtt` is
    #    imported as `paho`, `simple-pid` as `simple_pid`, and guessing either
    #    way is wrong for the other.
    replacements: dict[str, str] = {}
    roots: dict[str, str] = {}
    for req in requests:
        root = resolve_import_root(req.original, source_code,
                                   declared=req.original_import)
        roots[req.original] = root
        if root and req.winner_import:
            replacements[root] = req.winner_import
    if replacements:
        try:
            result.code = apply_import_replacements(result.code, replacements)
            result.import_rewrites = replacements
        except Exception:                                 # noqa: BLE001
            pass                                          # recorded as unapplied

    # 2. call sites the generative half recorded. A no-op when the new code
    #    preserves the API surface, which is the FRA's contract -- kept wired
    #    in as the safety net for when it does not.
    for req in requests:
        if not req.call_rewrites:
            continue
        result.code, applied = apply_call_site_rewrites(
            result.code, req.call_rewrites, roots.get(req.original, ""))
        result.calls_rewritten.extend(applied)

    # 3. sweep imports nothing references any more.
    result.code, dropped = drop_unused_source_imports(
        result.code, {r.split(".")[0].casefold() for r in roots.values() if r})
    result.imports_dropped = dropped

    # 4. post-condition. Anything left is LIVE, and the migration is unfinished.
    for req in requests:
        survivors = remaining_source_imports(result.code, roots.get(req.original, ""))
        if survivors:
            result.survivors[req.original] = survivors
    return result
