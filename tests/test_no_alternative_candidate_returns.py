"""Every return path of ``_compute_scores`` must reference only bound names.

The ``no_alternative_candidate`` refusal returned ``not_installed``, a name that
does not exist in that scope. Reaching it raised NameError instead of returning
a refusal; ``run_axiom_arms`` catches the exception into a note and records the
row with a falsy state, which surfaces as ``converged=N``, zero repair cycles,
and a byte-identical no-op clone.

Three proteus_full runs were lost that way -- C3 r3, D3 r1, D3 r2 -- all
reported as "nonconvergence" with ``vca_reentries=0``, which is precisely what a
crash on this line looks like from the outside. The branch is rare (it needs the
resolver to offer nothing but the incumbent), so no test covered it.

A static check is used rather than a call, because the branch depends on
resolver output that is expensive to reproduce, and the defect class -- a name
that is simply not in scope -- is fully visible in the AST.
"""
import ast
import builtins
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "smart_loader", "agents", "axiom_agent.py")


def _bound_names(fn: ast.FunctionDef) -> set:
    names = {a.arg for a in fn.args.args}
    names |= {a.arg for a in fn.args.kwonlyargs}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, (ast.For, ast.AugAssign, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            for sub in ast.walk(node.optional_vars):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(open(AGENT, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in axiom_agent.py")


def test_compute_scores_returns_only_bound_names():
    fn = _function("_compute_scores")
    bound = _bound_names(fn) | set(dir(builtins))
    unbound = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id not in bound:
                        unbound.append((node.lineno, sub.id))
    assert not unbound, f"returns reference unbound names: {unbound}"


def test_every_return_has_the_same_arity():
    """Callers unpack five values; a short tuple is the same failure by another route."""
    fn = _function("_compute_scores")
    arities = {
        len(node.value.elts)
        for node in ast.walk(fn)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)
    }
    assert arities == {5}, f"inconsistent return arity: {arities}"


def test_the_refusal_path_is_still_a_refusal():
    """The fix must not turn the refusal into a migration."""
    source = open(AGENT, encoding="utf-8").read()
    idx = source.index('"selection_basis": "no_alternative_candidate"')
    window = source[idx - 400:idx + 200]
    assert "return (original" in window, \
        "no_alternative_candidate must still return the incumbent, not a winner"
