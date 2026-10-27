"""Shared import / usage rewriting for AXIOM agents.

Rewritten to use LibCST (a format-preserving concrete syntax tree) instead
of the stdlib ``ast`` module. The previous implementation parsed with
``ast.parse`` and re-emitted the whole file with ``ast.unparse`` -- correct
code, but ``ast.unparse`` regenerates every line's formatting from the tree
alone, discarding the original file's comments and exact whitespace/style in
the process. A migration patch that silently reformats an entire file is not
a minimal diff, which matters here for two concrete reasons: PyMigBench's
own ground-truth patches *are* minimal diffs (so a byte-for-byte-noisier
patch is a worse comparison target even when semantically identical), and a
human reviewing what AXIOM actually changed shouldn't have to read a full
reformat to find the one changed import. This is the same design choice
MigrateLib (Islam, Jha, Mahmoud, Nadi -- arXiv 2510.08810, 2026) makes for
its own source rewriter, for the stated reason: insert the new syntax
"without changing the rest of the file." LibCST is Meta's format-preserving
CST library, used for exactly this kind of large-scale mechanical Python
rewrite.
"""

from __future__ import annotations

import ast

import libcst as cst


def _dotted_name_to_string(node: cst.BaseExpression) -> str:
    """Flatten a CST ``Name``/``Attribute`` chain (e.g. ``kafka.errors``) to a string.

    LibCST represents a dotted module path as nested ``Attribute`` nodes
    rather than a single string, unlike ``ast.ImportFrom.module`` /
    ``ast.alias.name``, which are already plain strings. This and
    ``_string_to_dotted_name`` are the two conversions that let the actual
    rewrite logic below stay a simple string comparison, exactly like the
    previous ``ast``-based version.
    """
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_name_to_string(node.value)}.{node.attr.value}"
    raise TypeError(f"Unsupported dotted-name node: {type(node).__name__}")


def _string_to_dotted_name(path: str) -> cst.BaseExpression:
    """Build a CST ``Name``/``Attribute`` chain from a dotted string."""
    parts = path.split(".")
    node: cst.BaseExpression = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


def _bound_name(alias: cst.ImportAlias) -> str:
    """The identifier a ``from X import Y [as Z]`` alias actually binds."""
    if alias.asname is not None and isinstance(alias.asname.name, cst.Name):
        return alias.asname.name.value
    return _dotted_name_to_string(alias.name)


class _ReferencedNameCollector(cst.CSTVisitor):
    """Collect every identifier used anywhere outside import statements.

    Mirrors what the previous ``ast``-based version got for free: ``ast.walk``
    collecting every ``ast.Name`` node never included an import statement's
    own bound names, because ``ast.alias`` (the AST node for one imported
    symbol) is not an ``ast.Name`` in the first place. LibCST's
    ``ImportAlias``/``AsName`` *do* wrap real ``Name`` nodes internally, so
    this visitor explicitly refuses to recurse into ``Import``/``ImportFrom``
    to reproduce the same behavior -- without it, every import would count as
    a "reference" to its own imported name and the dead-import check below
    would never drop anything.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def visit_Name(self, node: cst.Name) -> None:
        self.names.add(node.value)


_EXPORTS_CACHE: dict[str, set[str] | None] = {}


def module_exports(module_name: str) -> set[str] | None:
    """Public names a module actually exports, or None if it cannot be read.

    Imported in a subprocess rather than here: the target of a migration is
    arbitrary third-party code, and importing it into the agent's own
    interpreter would run its module-level side effects and leave them in
    place for everything that follows.

    None means "could not determine" (not installed, import error, timeout)
    and every caller must treat that as "do not rewrite" rather than as an
    empty export list — a missing package would otherwise look exactly like
    a package that exports nothing.
    """
    if module_name in _EXPORTS_CACHE:
        return _EXPORTS_CACHE[module_name]
    import json as _json
    import subprocess as _sp
    import sys as _sys

    code = ("import importlib,json,sys\n"
            f"m=importlib.import_module({module_name!r})\n"
            "print(json.dumps([n for n in dir(m) if not n.startswith('_')]))")
    try:
        proc = _sp.run([_sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=20)
        names = (set(_json.loads(proc.stdout.strip().splitlines()[-1]))
                 if proc.returncode == 0 and proc.stdout.strip() else None)
    except Exception:
        names = None
    _EXPORTS_CACHE[module_name] = names
    return names


class ImportRewriter(cst.CSTTransformer):
    """Rewrite top-level import statements and module.attribute usages.

    ``referenced_names``, when supplied, is the set of identifiers actually
    used anywhere else in the source (see ``_ReferencedNameCollector`` --
    the same technique ``agents/connector_agent.py``'s
    ``_unused_wrapper_names`` uses to catch a wrapper that was defined but
    never wired up). This module only ever rewrites the *module path* of a
    ``from X import Y`` statement, never the imported name ``Y`` itself -- a
    real, connector-generated migration (kafka -> confluent-kafka) exposed
    exactly why that matters: the original code did
    ``from kafka import KafkaProducer``, the connector's own wrapper
    functions correctly do their own local
    ``from confluent_kafka import Producer`` (the real class name under the
    new library), and the call sites were rewritten to go through those
    wrappers -- leaving the top-level ``KafkaProducer`` import completely
    unused. Without this check, the rewrite still fired, producing
    ``from confluent_kafka import KafkaProducer`` -- a class that does not
    exist under the new module -- and the verifier's import smoke test
    failed on a name the patched file never even called anymore. When
    ``referenced_names`` is provided, an import whose bound name has gone
    unused is dropped instead of rewritten; when it's still referenced (the
    ordinary case where the old and new library share the same class name,
    e.g. ``WebClient`` for ``slack`` -> ``slack_sdk``), the module path is
    rewritten exactly as before.

    Every rewrite below is deliberately careful to touch only the specific
    CST nodes that actually changed and leave everything else -- including a
    statement's own original comma placement and whitespace -- as the
    original parsed nodes, rather than always rebuilding a node once any
    part of the statement changes. That's what makes the format-preservation
    guarantee real rather than nominal: a multi-name ``from X import A, B``
    where only the module is renamed keeps its exact original comma/spacing
    style; only a statement whose *name list itself* actually shrank (an
    unused name was dropped) gets its comma placement normalized, because at
    that point there is no longer an "original" spacing to preserve for a
    name that no longer exists.
    """

    def __init__(
        self, replacements: dict[str, str], referenced_names: set[str] | None = None,
    ):
        self.replacements = replacements
        self.referenced_names = referenced_names
        self._seen_imports: set[tuple[str, str | None]] = set()
        #: Targets whose from-import could not be rewritten because the module
        #: does not export the required names. Each needs a plain
        #: ``import <target>`` adding, since wrapper code refers to it by
        #: module path (``ciso8601.parse_datetime(...)``).
        self.deferred_module_imports: set[str] = set()
        self._module_level_imports: set[str] = set()

    @staticmethod
    def _module_exists(dotted: str) -> bool:
        """Whether ``dotted`` is importable here. False on any doubt.

        The migration target is installed before the run, so this is a real
        check rather than a guess. Anything that raises -- a parent package
        that executes code on import, a partially installed distribution --
        counts as "does not exist", because the caller's fallback is the safe
        one.
        """
        import importlib.util
        try:
            return importlib.util.find_spec(dotted) is not None
        except Exception:                                  # noqa: BLE001
            return False

    def _replacement_for_module(self, module: str) -> str:
        """Rewrite an exact module or its submodule prefix.

        Replacing ``slack`` with ``slack_sdk`` must also rewrite
        ``slack.errors`` to ``slack_sdk.errors``; exact-key lookup alone
        leaves a migrated file importing two incompatible SDK namespaces.

        But carrying the submodule path across is only correct when the target
        actually HAS that submodule, and libraries that replace one another
        rarely share an internal layout. ``paho.mqtt.client`` under a migration
        to aiomqtt became ``aiomqtt.mqtt.client``, which does not exist -- a
        guaranteed ModuleNotFoundError produced by the rewrite itself, on a
        file that was otherwise correctly migrated.

        So the submodule path is preserved only if the resulting module is
        importable. Otherwise the whole dotted path collapses to the target
        package, which is what a flat replacement library expects: with an
        alias, ``import paho.mqtt.client as mqtt`` becomes
        ``import aiomqtt as mqtt``, and the adapter's ``mqtt.Client(...)``
        then resolves against aiomqtt exactly as the connector intended.
        """
        for original, winner in self.replacements.items():
            if module == original:
                return winner
            if module.startswith(f"{original}."):
                preserved = f"{winner}{module[len(original):]}"
                return preserved if self._module_exists(preserved) else winner
        return module

    def visit_Import(self, node: cst.Import) -> bool:
        # Remember what is already imported plainly, so leave_Module never
        # adds a duplicate ``import X`` for a module the file already has.
        for alias in node.names:
            try:
                self._module_level_imports.add(
                    _dotted_name_to_string(alias.name))
            except TypeError:
                pass
        # A dotted import path (``kafka.errors``) is built from the exact
        # same Name/Attribute node types LibCST uses for an ordinary
        # attribute-access *expression* like ``kafka.errors`` appearing in
        # code -- unlike ``ast``, where ``ast.Import``'s dotted name is a
        # plain string, entirely separate from ``ast.Attribute``. Without
        # this, ``leave_Attribute`` below fires on the import's own module
        # path during LibCST's normal bottom-up child traversal and rewrites
        # it *before* ``leave_Import`` ever runs -- by the time this
        # statement's own dedup/rename logic sees it, the module has already
        # changed and its own comparison against the original silently
        # no-ops. ``leave_Import``/``leave_ImportFrom`` already rewrite the
        # dotted path directly via string manipulation, so nothing is lost
        # by skipping the generic per-node attribute walk here -- it would
        # only ever produce the same answer twice, and (as found by a real
        # failing test) sometimes race ahead of the statement-level logic
        # that's supposed to gate it.
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_Import(
        self, original_node: cst.Import, updated_node: cst.Import,
    ) -> cst.Import | cst.RemovalSentinel:
        original_aliases = list(updated_node.names)
        new_aliases: list[cst.ImportAlias] = []
        changed = False
        for alias in original_aliases:
            dotted = _dotted_name_to_string(alias.name)
            new_dotted = self._replacement_for_module(dotted)
            asname_str = (
                alias.asname.name.value
                if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                else None
            )
            import_key = (new_dotted, asname_str)
            if import_key in self._seen_imports:
                changed = True
                continue
            self._seen_imports.add(import_key)
            if new_dotted != dotted:
                changed = True
                new_aliases.append(alias.with_changes(name=_string_to_dotted_name(new_dotted)))
            else:
                new_aliases.append(alias)
        if not new_aliases:
            return cst.RemoveFromParent()
        if not changed:
            return updated_node
        if len(new_aliases) != len(original_aliases):
            # A name was actually dropped (deduplicated away) -- its
            # neighbors' original comma placement no longer describes a
            # valid list, so normalize commas across what remains.
            new_aliases = [a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in new_aliases]
        return updated_node.with_changes(names=new_aliases)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom,
    ) -> cst.ImportFrom | cst.RemovalSentinel:
        if updated_node.module is None:
            return updated_node
        module_str = _dotted_name_to_string(updated_node.module)
        new_module_str = self._replacement_for_module(module_str)
        if new_module_str == module_str:
            return updated_node
        if self.referenced_names is None or isinstance(updated_node.names, cst.ImportStar):
            # A wildcard import binds names we cannot enumerate from this
            # file alone (they come from the target module's own
            # namespace) -- never treat "not found in referenced_names" as
            # "unused" for one; that would drop it on every rewrite.
            return updated_node.with_changes(module=_string_to_dotted_name(new_module_str))
        original_names = list(updated_node.names)
        kept_names = [alias for alias in original_names if _bound_name(alias) in self.referenced_names]
        if not kept_names:
            return cst.RemoveFromParent()

        # Does the target actually export these names? Renaming the module of
        # a from-import silently asserts that it does, and when the source is
        # a stdlib module whose *symbol* is being replaced rather than the
        # module itself, it does not. A3 is exactly that shape: only
        # ``datetime.fromisoformat`` is being migrated to ciso8601, but
        #     from datetime import datetime, timezone
        # was rewritten to
        #     from ciso8601 import datetime, timezone
        # and ciso8601 exports neither — the file failed at import, before a
        # single test ran, on two stdlib types the migration never touched.
        #
        # When the target cannot supply every name, the original import is
        # left exactly as it was: the code still needs those names from their
        # original home. The target is recorded so a plain ``import <target>``
        # can be added instead, which is what the connector's wrapper calls.
        exports = module_exports(new_module_str)
        if exports is not None and any(_bound_name(a) not in exports
                                       for a in kept_names):
            self.deferred_module_imports.add(new_module_str)
            return updated_node
        if len(kept_names) == len(original_names):
            # Nothing was dropped -- keep the original alias nodes exactly
            # as parsed, comma placement included, and only the module path
            # itself changes.
            new_names = kept_names
        else:
            new_names = [a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in kept_names]
        return updated_node.with_changes(module=_string_to_dotted_name(new_module_str), names=new_names)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module,
    ) -> cst.Module:
        """Add ``import <target>`` for any from-import we declined to rewrite.

        Without this the file keeps its original stdlib import (correct) but
        has no binding for the target at all, so connector-generated wrapper
        bodies calling ``ciso8601.parse_datetime`` raise NameError — a second
        failure hiding behind the first.
        """
        needed = sorted(self.deferred_module_imports - self._module_level_imports)
        if not needed:
            return updated_node
        new_lines = [
            cst.SimpleStatementLine(body=[
                cst.Import(names=[cst.ImportAlias(
                    name=_string_to_dotted_name(mod))])
            ])
            for mod in needed
        ]
        # Placed after the existing import block so module docstrings and
        # __future__ imports keep their required position at the top.
        body = list(updated_node.body)
        insert_at = 0
        for index, stmt in enumerate(body):
            if isinstance(stmt, cst.SimpleStatementLine) and stmt.body and isinstance(
                    stmt.body[0], (cst.Import, cst.ImportFrom)):
                insert_at = index + 1
        return updated_node.with_changes(
            body=body[:insert_at] + new_lines + body[insert_at:])

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute,
    ) -> cst.Attribute:
        if isinstance(updated_node.value, cst.Name):
            new_module = self.replacements.get(updated_node.value.value)
            if new_module:
                # Only if the target really has an attribute of that name.
                # Renaming the module half of ``datetime.fromisoformat`` gave
                # ``ciso8601.fromisoformat``, which does not exist — the
                # function is ``parse_datetime``. A mechanical rename is only
                # valid where the two APIs agree; where they do not, the call
                # site must be left for the connector, whose whole purpose is
                # writing the wrapper that translates it. Silently renaming
                # produces an AttributeError at runtime instead.
                exports = module_exports(new_module)
                if exports is not None and updated_node.attr.value not in exports:
                    return updated_node
                return updated_node.with_changes(
                    value=updated_node.value.with_changes(value=new_module),
                )
        return updated_node


def apply_import_replacements(source_code: str, replacements: dict[str, str]) -> str:
    """Return source with import lines and module usages rewritten.

    A name imported by a rewritten ``from X import Y`` that isn't referenced
    anywhere else in the file is dropped rather than rewritten -- see
    ``ImportRewriter``'s docstring for the real migration this was found
    against (a wrapper-based case where the original imported class name
    doesn't exist under the new library and, once wrappers took over every
    call site, was never used anymore either). Every part of the file this
    migration didn't touch -- comments, blank lines, quote style, unrelated
    statements -- comes back byte-identical, because the rewrite operates on
    a concrete syntax tree (LibCST) instead of regenerating the file from an
    abstract one.
    """
    if not replacements:
        return source_code
    tree = cst.parse_module(source_code)
    collector = _ReferencedNameCollector()
    tree.visit(collector)
    new_tree = tree.visit(ImportRewriter(replacements, collector.names))
    return new_tree.code


def rewrite_simple_attrs_to_dataclass(source_code: str) -> tuple[str, list[str]] | None:
    """Rewrite the small, semantics-preserving ``attr`` → ``dataclass`` subset.

    ``attr.ib()`` is not a general-purpose function substitute for
    ``dataclasses.field()``.  In particular, wrapping ``field()`` in an
    ordinary helper function makes static analysis (correctly) reject it and
    loses the dataclass class-body context.  For the common no-argument form
    below, the native transformation is deterministic instead::

        import attr                         from dataclasses import dataclass
        @attr.s                    ->       @dataclass
        class Record:                        class Record:
            value: str = attr.ib()               value: str

    This function intentionally declines every more complex attrs feature.
    Defaults, factories, validators, converters, aliases, and decorator
    options need a fuller semantic migration and remain Connector/LLM work.
    Returning ``None`` is therefore a safety decision, not a failed rewrite.

    Kept on stdlib ``ast`` rather than LibCST: this transformation reshapes
    class bodies and decorators wholesale (a full ``@attr.s`` class becomes
    a full ``@dataclass`` class), not a handful of import lines, so there is
    little of the original formatting left to preserve in the rewritten
    class either way, and ``ast``'s simpler node model is a better fit for
    the validation-heavy walk this function does before ever touching the
    tree.
    """
    import ast

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None

    # Be deliberately narrow.  This avoids changing aliases or mixed imports
    # whose binding/usage would need a semantic lookup rather than this recipe.
    attr_imports = [
        node for node in tree.body
        if isinstance(node, ast.Import)
        and len(node.names) == 1
        and node.names[0].name == "attr"
        and node.names[0].asname is None
    ]
    if len(attr_imports) != 1:
        return None
    if any(
        isinstance(node, ast.ImportFrom) and node.module == "attr"
        for node in ast.walk(tree)
    ):
        return None
    if any(
        isinstance(node, ast.ImportFrom) and node.module == "dataclasses"
        for node in tree.body
    ):
        return None

    def is_attr_member(node: ast.AST, member: str) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == member
            and isinstance(node.value, ast.Name)
            and node.value.id == "attr"
        )

    decorator_nodes: set[int] = set()
    allowed_attr_members: set[int] = set()
    allowed_attr_names: set[int] = set()
    ib_calls: set[int] = set()
    changed_classes = 0
    changed_fields = 0

    # First validate the entire recipe before mutating the AST.  A partial
    # attrs rewrite would be worse than leaving the case to the Connector.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        attrs_decorators: list[ast.AST] = []
        for decorator in node.decorator_list:
            if is_attr_member(decorator, "s"):
                attrs_decorators.append(decorator)
            elif isinstance(decorator, ast.Call) and is_attr_member(decorator.func, "s"):
                if decorator.args or decorator.keywords:
                    return None
                attrs_decorators.append(decorator)
        if not attrs_decorators:
            continue
        # Multiple ``@attr.s`` decorators would be nonsensical, but decline
        # rather than silently choosing one.
        if len(attrs_decorators) != 1:
            return None
        decorator_nodes.add(id(attrs_decorators[0]))
        decorator = attrs_decorators[0]
        decorator_member = decorator.func if isinstance(decorator, ast.Call) else decorator
        assert isinstance(decorator_member, ast.Attribute)
        allowed_attr_members.add(id(decorator_member))
        allowed_attr_names.add(id(decorator_member.value))
        changed_classes += 1

        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            value = statement.value
            if not isinstance(value, ast.Call) or not is_attr_member(value.func, "ib"):
                continue
            if value.args or value.keywords or statement.target is None:
                return None
            ib_calls.add(id(value))
            allowed_attr_members.add(id(value.func))
            allowed_attr_names.add(id(value.func.value))
            changed_fields += 1

    if not changed_classes or not changed_fields:
        return None

    # Reject any ``attr`` use not accounted for by the safe recipe.  The
    # import itself is allowed; decorators and field calls are the only other
    # legal references in this narrow transformation.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "attr":
            if id(node) not in allowed_attr_members:
                return None
        if isinstance(node, ast.Name) and node.id == "attr" and id(node) not in allowed_attr_names:
            return None

    class _SimpleAttrsTransformer(ast.NodeTransformer):
        def visit_Import(self, node: ast.Import) -> ast.AST:
            if node in attr_imports:
                return ast.ImportFrom(
                    module="dataclasses",
                    names=[ast.alias(name="dataclass")],
                    level=0,
                )
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
            for index, decorator in enumerate(node.decorator_list):
                if id(decorator) in decorator_nodes:
                    node.decorator_list[index] = ast.Name(id="dataclass", ctx=ast.Load())
            self.generic_visit(node)
            return node

        def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
            self.generic_visit(node)
            if isinstance(node.value, ast.Call) and id(node.value) in ib_calls:
                node.value = None
            return node

    rewritten = _SimpleAttrsTransformer().visit(tree)
    ast.fix_missing_locations(rewritten)
    return ast.unparse(rewritten), [
        "import attr → from dataclasses import dataclass",
        "@attr.s → @dataclass",
        "annotated attr.ib() field → native required dataclass field",
    ]


# ── distribution name -> import root ─────────────────────────────────────────

def import_roots(source_code: str) -> set[str]:
    """Top-level import roots in a file, including imports inside functions.

    ``ast.walk`` rather than ``tree.body``: nine of the twelve evaluation
    programs import their migrated-away library inside a function ("lazy
    import for testability"), so a module-level-only scan misses almost all of
    them.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


def _distribution_import_roots(distribution: str) -> list:
    """Import roots a distribution actually installs, from package metadata.

    `importlib.metadata.packages_distributions()` is the authoritative answer to
    "what does this distribution import as", and it is the only one that gets
    `python-statemachine -> statemachine` right: that name shares no spelling
    with its import root, so every string transformation of the distribution
    name fails on it. The naive `replace("-", "_")` fallback produced
    `python_statemachine`, which matches no import in any file -- so
    `remaining_source_imports` looked for the wrong module, found nothing, and
    the post-condition that is supposed to reject an unfinished migration passed
    an unmigrated clone instead.

    Returns [] when the distribution is not installed, which is the normal case
    for a candidate target that was never adopted.
    """
    try:
        from importlib.metadata import packages_distributions
    except ImportError:                                       # pragma: no cover
        return []
    wanted = (distribution or "").strip().lower().replace("_", "-")
    if not wanted:
        return []
    try:
        mapping = packages_distributions()
    except Exception:                                         # noqa: BLE001
        return []
    roots = []
    for module, dists in mapping.items():
        for dist in dists:
            if dist.lower().replace("_", "-") == wanted and module not in roots:
                roots.append(module)
    return roots


def resolve_import_root(distribution: str, source_code: str,
                        declared: str = "") -> str:
    """The module root a distribution is actually imported as, IN THIS FILE.

    A PyPI distribution name is not its import path, and the two differ in
    opposite directions:

        paho-mqtt   -> paho          (first segment wins)
        simple-pid  -> simple_pid    (underscore form wins)

    Guessing one form broke two separate call sites in the pipeline, which is
    why the resolution lives here rather than being open-coded a third time:

    * ``connector_agent`` keyed ``import_replacements`` on ``paho_mqtt``, so
      the libCST rewrite matched nothing and the dead-import sweep beneath it
      -- keyed on the same value -- matched nothing either. The old import
      survived every run of B3 and D3, and the model, forbidden by the IMPORT
      LOCK from touching imports itself, wrote an adapter that TAKES the old
      module as a parameter.
    * ``verifier_agent``'s patch-integrity check compared the same guess
      against the file's real roots, reported "Selected source import
      'paho_mqtt' is not present in the original file" against a CORRECT
      migration, and -- because that branch fired -- never reached the ``elif``
      that verifies the source import actually changed. A false positive that
      also disabled the real invariant.

    Every plausible spelling is tried against the roots the file actually
    imports, longest first so ``simple_pid`` is preferred over the bare
    ``simple`` prefix. With no match the declared value stands: this narrows a
    guess to a fact where the fact is available, and never widens it.
    """
    roots = import_roots(source_code)
    raw = (distribution or "").strip()
    installed = _distribution_import_roots(raw)
    spellings: list[str] = []
    # Metadata first: it is fact, not transformation, and it is the only source
    # that resolves a name sharing no spelling with its import root
    # (python-statemachine -> statemachine).
    for spelling in (installed
                     + [raw, raw.replace("-", "_"), raw.replace("-", "."),
                        raw.split("-")[0], raw.split(".")[0]]
                     # Trailing segments too: the root is as often the suffix
                     # as the prefix, and only the prefix was tried before.
                     + raw.replace(".", "-").split("-")):
        if spelling and spelling not in spellings:
            spellings.append(spelling)
    for spelling in sorted(spellings, key=len, reverse=True):
        if spelling in roots:
            return spelling
    # No import in this file matches. Metadata still beats a string guess.
    if installed:
        return installed[0]
    return declared or raw.replace("-", "_")


# ── deterministic call-site rewriting ────────────────────────────────────────

def names_bound_from_root(source_code: str, root: str) -> set[str]:
    """Local names that an import of ``root`` brings into scope.

    ``import paho.mqtt.client as mqtt``     -> {"mqtt"}
    ``from pymodbus.client import ModbusTcpClient`` -> {"ModbusTcpClient"}
    ``import pymodbus``                     -> {"pymodbus"}

    These are the names a call site uses to reach the old library, so they are
    what identifies a call as belonging to it. Function-scope imports count:
    nine of the twelve corpus programs put theirs inside a function.
    """
    root = (root or "").split(".")[0].casefold()
    if not root:
        return set()
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].casefold() == root:
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.split(".")[0].casefold() == root:
                names.update(a.asname or a.name for a in node.names)
    return names


def _base_name(node: cst.BaseExpression) -> str:
    """Leftmost identifier of a (possibly dotted) callee expression."""
    while isinstance(node, cst.Attribute):
        node = node.value
    return node.value if isinstance(node, cst.Name) else ""


class _CallSiteRewriter(cst.CSTTransformer):
    """Repoint calls that reach the old library at their generated wrapper.

    Two conditions must BOTH hold, and they are deliberately strict:

    1. the callee's leftmost name is one the old library's import bound, and
    2. the rendered call matches, character for character once whitespace is
       normalised, the call text the analysis pass recorded.

    Arguments are passed through verbatim -- never remapped. The wrapper was
    generated to be a drop-in for this exact call, so reproducing its argument
    list is the wrapper's own contract; inventing a mapping would be a guess,
    and on this corpus a wrong guess is INVISIBLE. The tests dependency-inject
    their clients, so a default-factory helper like ``_default_client`` is
    never executed by the suite or by the oracle's probes -- broken code there
    passes every gate. Anything that does not match exactly is left alone for
    the post-condition to report.
    """

    def __init__(self, wanted: dict[str, str], names: set[str]) -> None:
        self.wanted = wanted            # normalised call text -> wrapper name
        self.names = names
        self.applied: list[str] = []

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        if _base_name(updated.func) not in self.names:
            return updated
        rendered = cst.Module(body=[]).code_for_node(updated)
        key = "".join(rendered.split())
        wrapper = self.wanted.get(key)
        if not wrapper:
            return updated
        self.applied.append(f"{rendered.strip()} -> {wrapper}(...)")
        return updated.with_changes(func=cst.Name(wrapper))


def apply_call_site_rewrites(source_code: str, rewrites: list[dict],
                             root: str) -> tuple[str, list[str]]:
    """Point recorded call sites at their wrappers. Returns ``(code, applied)``.

    Why this is deterministic rather than left to the editing model: the model
    demonstrably edits the wrong line. On B2 the analysis reported the call
    site correctly --

        Found 1 call site(s):  L14: client = ModbusTcpClient(host, port=port)
        1 call site(s) need a wrapper: _axiom_modbus_client_wrapper  L14

    -- and the ACI session then rewrote line 69 (``MeterReader.__init__``)
    instead, leaving line 14 inside ``_default_client`` untouched. The result
    was an orphaned factory still constructing the old library, so the import
    stayed live and could not be swept, and the row scored lib_swapped=N.

    The connector already knows the line, the call text and the wrapper name.
    Applying that mapping here takes the model out of a step it fails at,
    while leaving it the genuinely semantic job of writing the wrapper.

    Never raises, and never returns code that will not parse: the rewrite is
    re-parsed before it is returned, and the input is returned unchanged if
    anything is off.
    """
    # ``call_rewrites`` is not uniformly shaped across the connector's paths:
    # the deterministic attrs->dataclasses recipe records plain strings there,
    # and the legacy wrapper path uses {"replaces", "with"} instead of
    # {"call", "wrapper_name"}. Anything this cannot read is skipped rather
    # than assumed — a rewrite driven by a misread entry would edit the wrong
    # call, which is the exact failure this function exists to prevent.
    wanted: dict[str, str] = {}
    for item in rewrites or []:
        if not isinstance(item, dict):
            continue
        call = str(item.get("call") or item.get("replaces") or "")
        wrapper = str(item.get("wrapper_name") or item.get("wrapper")
                      or item.get("with") or "")
        if not call or not wrapper:
            continue
        wanted["".join(call.split())] = wrapper
    if not wanted:
        return source_code, []
    names = names_bound_from_root(source_code, root)
    if not names:
        return source_code, []
    try:
        tree = cst.parse_module(source_code)
    except Exception:                                     # noqa: BLE001
        return source_code, []
    rewriter = _CallSiteRewriter(wanted, names)
    try:
        new_tree = tree.visit(rewriter)
    except Exception:                                     # noqa: BLE001
        return source_code, []
    if not rewriter.applied:
        return source_code, []
    result = new_tree.code
    try:
        ast.parse(result)
    except SyntaxError:
        return source_code, []
    return result, rewriter.applied
