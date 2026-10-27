"""Remove imports of the migrated-away library once nothing references them.

Why this exists
───────────────
The Connector does the hard part of a migration correctly: it writes the
adapter functions and rewrites the call sites. What it does not do is delete
the now-dead import of the library it just replaced. D3's ``sink.py`` is the
clearest case — 70 lines of correct ``amqtt`` glue, the call site rewritten to
``client = _axiom_mqtt_client_factory_wrapper()``, and then, four lines below:

    def _default_client(host="localhost", port=1883):
        import paho.mqtt.client as mqtt   # <- nothing references `mqtt` any more
        client = _axiom_mqtt_client_factory_wrapper()

The name ``mqtt`` is never used again in the file. The migration happened; the
import statement is litter.

It is not harmless litter, which is why this prunes rather than ignores. The
dependency is still declared, so ``library_swapped`` correctly reports the
library as not removed, and on a machine without ``paho-mqtt`` the first call
to ``_default_client`` still raises ImportError. A migration that leaves the
old library required has not finished. But the distance between "did not
migrate" and "migrated and left one dead line" is enormous, and the scoring
cannot tell them apart — across the Proteus arms this single omission is what
put ``migration_success`` at 11-12/36.

Nine of the twelve corpus programs import the old library INSIDE a function
("lazy import for testability"), so a module-level-only pruner would miss
almost all of them. This walks every scope.

Deliberately conservative. An import is removed only when every name it binds
is unreferenced in the rest of the file, and the result is re-parsed before it
is returned — a pruner that produced invalid code, or that removed the sole
statement in a body, would turn a working migration into a compile failure,
which is far worse than the litter it cleans up.
"""

from __future__ import annotations

import libcst as cst


def _root(name: str) -> str:
    """First dotted segment, lowercased. ``paho.mqtt.client`` -> ``paho``."""
    return (name or "").split(".")[0].strip().lower()


def source_roots(library: str) -> set[str]:
    """Every plausible import root for a distribution name.

    A distribution name is not its import path, and the two differ in opposite
    directions across this corpus: ``paho-mqtt`` imports as ``paho`` (first
    segment wins) while ``simple-pid`` imports as ``simple_pid`` (underscore
    form wins). Both forms are returned and either may match, which errs
    toward leaving an import in place rather than removing one we misread.
    """
    raw = (library or "").strip().lower()
    if not raw:
        return set()
    return {r for r in (raw, raw.replace("-", "_"), raw.split("-")[0],
                        raw.split(".")[0]) if r}


class _NameUsageCollector(cst.CSTVisitor):
    """Every bare name and attribute base referenced OUTSIDE import statements.

    Import statements are skipped because an import always mentions the name it
    binds; counting that as a use would mean no import is ever dead.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()

    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def visit_Name(self, node: cst.Name) -> None:
        self.used.add(node.value)


class _DeadImportRemover(cst.CSTTransformer):
    """Drop imports of ``roots`` whose bound names are all unused."""

    def __init__(self, roots: set[str], used: set[str]) -> None:
        self.roots = roots
        self.used = used
        self.removed: list[str] = []

    def _bound_names(self, alias: cst.ImportAlias) -> str:
        if alias.asname is not None:
            target = alias.asname.name
            return target.value if isinstance(target, cst.Name) else ""
        # ``import paho.mqtt.client`` with no alias binds the ROOT name
        # ``paho``, not the dotted path — removing it on the strength of
        # ``paho.mqtt.client`` being unreferenced would be wrong.
        name = alias.name
        parts = []
        while isinstance(name, cst.Attribute):
            parts.append(name.attr.value)
            name = name.value
        if isinstance(name, cst.Name):
            parts.append(name.value)
        return parts[-1] if parts else ""

    def _module_of(self, alias: cst.ImportAlias) -> str:
        name, parts = alias.name, []
        while isinstance(name, cst.Attribute):
            parts.append(name.attr.value)
            name = name.value
        if isinstance(name, cst.Name):
            parts.append(name.value)
        return ".".join(reversed(parts))

    def leave_Import(self, original: cst.Import, updated: cst.Import):
        keep = []
        for alias in updated.names:
            module = self._module_of(alias)
            bound = self._bound_names(alias)
            if _root(module) in self.roots and bound and bound not in self.used:
                self.removed.append(module)
                continue
            keep.append(alias)
        if not keep:
            return cst.RemoveFromParent()
        # A trailing comma on the last alias is a syntax error.
        keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated.with_changes(names=keep)

    def leave_ImportFrom(self, original: cst.ImportFrom,
                         updated: cst.ImportFrom):
        if isinstance(updated.names, cst.ImportStar):
            return updated                      # cannot tell what it binds
        module = ""
        name = updated.module
        parts = []
        while isinstance(name, cst.Attribute):
            parts.append(name.attr.value)
            name = name.value
        if isinstance(name, cst.Name):
            parts.append(name.value)
        module = ".".join(reversed(parts))
        if _root(module) not in self.roots:
            return updated
        keep = []
        for alias in updated.names:
            bound = self._bound_names(alias)
            if bound and bound not in self.used:
                self.removed.append(f"{module}.{bound}")
                continue
            keep.append(alias)
        if not keep:
            return cst.RemoveFromParent()
        keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated.with_changes(names=keep)


def prune_dead_source_imports(source_code: str,
                              library: str) -> tuple[str, list[str]]:
    """Return ``(code, removed)``. Unchanged code and ``[]`` when nothing is dead.

    Never raises: a file that cannot be parsed, or a prune that would produce
    code that cannot be parsed, returns the input untouched. Leaving one dead
    import is a scoring problem; emitting a syntax error is a broken program.
    """
    roots = source_roots(library)
    if not roots or not source_code.strip():
        return source_code, []
    try:
        tree = cst.parse_module(source_code)
    except Exception:                                     # noqa: BLE001
        return source_code, []

    usage = _NameUsageCollector()
    tree.visit(usage)
    remover = _DeadImportRemover(roots, usage.used)
    try:
        new_tree = tree.visit(remover)
    except Exception:                                     # noqa: BLE001
        return source_code, []
    if not remover.removed:
        return source_code, []

    result = new_tree.code
    try:
        # An import can be the only statement in a function body ("lazy import
        # for testability" often is not, but it can be). Removing it would
        # leave an empty block, which does not parse.
        compile(result, "<pruned>", "exec")
    except SyntaxError:
        return source_code, []
    return result, remover.removed
