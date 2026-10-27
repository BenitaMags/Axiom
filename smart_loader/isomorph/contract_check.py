"""Public-contract checks the equivalence oracle structurally cannot make.

Why this exists
───────────────
A restructured module can reproduce every recorded value, satisfy every
invariant, pass the project's suite and still break its callers. The oracle
exercises the normal call path; it never assigns to an attribute, never calls
``dataclasses.replace``, never imports the module with the target library
absent. So a rewrite is free to change the SHAPE of the public surface while
keeping every number identical, and four layers will wave it through.

Measured, on three separate programs, all oracle-PASS and all caught by a blind
judge instead:

    D1  ``self.kp = kp`` became a getter-only ``@property``. ``ctrl.kp = 3.0``
        worked on the original and raises AttributeError on the rewrite. No
        golden vector assigns to a gain.

    C2  ``@dataclass class Process`` became a hand-written class.
        ``dataclasses.fields(Process)`` and ``dataclasses.replace(p, level=1)``
        raise TypeError where they previously worked. No probe introspects it.

    D3  ``import paho`` inside ``_default_client`` -- lazy, and the docstring
        says "lazy import for testability" -- became ``import aiomqtt`` at
        module top level. ``import sink`` now fails when the library is absent,
        even though the whole point of injecting a client is to run without it.

Three programs, three shapes, one cause. Prompt text was tried first and is not
enough: the connector prompt has forbidden ``except Exception:`` in two places
since the first pilot and still produced it, and the attribute rule added after
D1 did not prevent C2's dataclass or D3's import. A check that runs is worth
more than an instruction that is read.

What this is NOT
────────────────
It is not a general equivalence check and does not replace the oracle. It looks
only for these three regressions, each of which was observed in a variant the
oracle accepted. Anything it reports is a difference in the public surface that
callers can reach and the recorded vectors cannot.
"""
from __future__ import annotations

import ast
import os
import sys


def _module(source: str):
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _classes(tree) -> dict:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _is_dataclass(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = getattr(target, "attr", getattr(target, "id", ""))
        if name == "dataclass":
            return True
    return False


def _self_assigned(node: ast.ClassDef) -> set:
    """Attributes the class assigns on ``self``, which callers may assign too."""
    found = set()
    for fn in node.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in ast.walk(fn):
            targets = (stmt.targets if isinstance(stmt, ast.Assign)
                       else [stmt.target] if isinstance(stmt, ast.AnnAssign)
                       else [])
            for t in targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                        and not t.attr.startswith("_")):
                    found.add(t.attr)
    return found


def _readonly_properties(node: ast.ClassDef) -> set:
    """Names exposed as a getter with no matching setter."""
    getters, setters = set(), set()
    for fn in node.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "property":
                getters.add(fn.name)
            elif (isinstance(dec, ast.Attribute) and dec.attr == "setter"
                  and isinstance(dec.value, ast.Name)):
                setters.add(dec.value.id)
    return getters - setters


def _module_level_imports(tree) -> set:
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def _deferred_imports(tree) -> set:
    """Imports that run only when a function is CALLED, not when it is loaded."""
    out = set()
    top = set(tree.body)
    for node in ast.walk(tree):
        if node in top or not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out - _module_level_imports(tree)


def divergences(original: str, variant: str, extra_files: dict | None = None,
                siblings: set | None = None) -> list[str]:
    """Public-surface regressions the rewrite introduced. Empty means clean.

    ``extra_files`` are the modules the variant extracted; an import moved into
    one of them still runs at load time, so they count as part of the variant's
    module-level surface. D3 moved ``import aiomqtt`` into ``mqtt_bridge.py``
    and imported that at the top of ``sink.py``, which breaks ``import sink``
    exactly as an inline top-level import would.
    """
    before, after = _module(original), _module(variant)
    if before is None or after is None:
        return []
    found: list[str] = []

    old_classes, new_classes = _classes(before), _classes(after)
    for name, old in old_classes.items():
        new = new_classes.get(name)
        if new is None:
            continue
        if _is_dataclass(old) and not _is_dataclass(new):
            found.append(
                f"class {name} was a @dataclass and is not any more; "
                f"dataclasses.fields({name}), .replace() and .astuple() raise "
                f"TypeError on the rewrite and worked on the original")
        frozen = _readonly_properties(new) & _self_assigned(old)
        for attr in sorted(frozen):
            found.append(
                f"{name}.{attr} was a plain attribute and is now a read-only "
                f"property; `obj.{attr} = ...` worked on the original and "
                f"raises AttributeError on the rewrite")

    # An import the original deferred must stay deferred -- but NOT matched by
    # name, because a migration changes the name on purpose. D3 deferred
    # ``paho`` and the rewrite hoisted ``aiomqtt``; intersecting the two sets
    # finds nothing while the regression is total. What carries over is the
    # PROPERTY: the original could be imported with its collaborator absent,
    # and the rewrite cannot.
    #
    # An import pulled in through an extracted module counts the same as one
    # written inline -- D3 put ``import aiomqtt`` in mqtt_bridge.py and
    # imported that at the top of sink.py, which fails identically.
    if _deferred_imports(before):
        # The program's OWN modules are first-party too, not just the ones the
        # variant extracted. Importing a sibling at module level is how these
        # programs are already written and has nothing to do with whether a
        # third-party collaborator can be absent. Without ``siblings`` a
        # rewrite of D3's sink.py that imports ``estimator`` reads as hoisting
        # a library.
        first_party = {os.path.basename(k)[:-3] for k in (extra_files or {})
                       if k.endswith(".py")} | set(siblings or ())
        surface = _module_level_imports(after)
        for body in (extra_files or {}).values():
            tree = _module(body)
            if tree is not None:
                surface |= _module_level_imports(tree)
        stdlib = getattr(sys, "stdlib_module_names", frozenset())
        for name in sorted(surface - _module_level_imports(before)):
            if name in first_party or name in stdlib or name.startswith("_"):
                continue
            found.append(
                f"`{name}` is imported at module load, and the original "
                f"deferred its collaborator import into a function "
                f"(\"lazy import for testability\"). Importing this module now "
                f"fails when {name} is absent, which the original supported so "
                f"an injected collaborator could be used without it")
    return found
