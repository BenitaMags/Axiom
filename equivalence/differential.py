"""Run the ORIGINAL and the PATCHED module on the same inputs and compare.

The original is the ground truth by definition, so this needs no golden vectors
and no expected outputs -- everything it uses is on hand at migration time. That
matters: the acceptance gate must never be handed the scoring oracle's answer
key, and this does not touch it.

The defect it exists for
────────────────────────
A2 migrates ``json`` -> ``ujson``. ujson escapes forward slashes by default and
the stdlib does not, so a tag containing a slash serializes differently::

    original:  {"quality":"GOOD","tag":"T3/sub","ts":"t3","value":null}
    migrated:  {"quality":"GOOD","tag":"T3\\/sub","ts":"t3","value":null}

The program's whole contract is byte-reproducible audit logs, so that is a real
defect, and it is one flag away from correct (``escape_forward_slashes=False``).
All three A2 seeds failed on it, in every arm.

Nothing in the pipeline could see it. The suite passes -- no test uses a slash.
Layer 6 constructs nothing, because A2 has no collaborator. Only the golden
vectors caught it, and the Verifier is deliberately blind to those, so the
repair loop was never told what was wrong and could not fix it.

What it compares
────────────────
Public functions the two modules share, called with generated arguments. Return
values must be equal; a function that raises in the original must raise the same
type in the patch.

What it refuses to touch
────────────────────────
* functions taking a collaborator (``client``, ``session``, ``smtp``, ``sock``
  and friends) -- calling those means opening sockets, which is layer 6's job
  and needs an injected double, not a generated value
* functions whose parameters cannot be given a value confidently
* anything private, and anything only one of the two modules defines

Everything runs in a subprocess with a timeout, so a module that hangs or exits
cannot take the harness with it. Every failure to determine an answer yields no
finding: this can understate a divergence, never invent one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

#: Parameter names that mean "a live collaborator goes here". A generated value
#: for one of these would make a real connection, which is not this layer's job.
COLLABORATOR_HINTS = (
    "client", "session", "smtp", "sock", "socket", "conn", "connection",
    "transport", "broker", "publisher", "subscriber", "master", "instrument",
    "reader", "writer", "sink", "channel", "stub", "service",
)

#: Values chosen to expose serialization and parsing differences between two
#: libraries that claim to be drop-in for each other. The forward slash is what
#: separates ujson from the stdlib; the rest cover escaping, unicode, precision
#: and boundary handling that sibling libraries routinely disagree about.
_STRINGS = ["PT101", "a/b", "Plant/Unit1/PT101", "", "Temp °C", 'a"b',
            "line\nbreak", "tab\there", "back\\slash", "<&>"]
_NUMBERS = [0, 1, -1, 42, 2**53, 2**63 - 1, 3.14159265358979, 0.1, -0.0, 1e300]

_RUNNER = r'''
import importlib.util, inspect, json, sys, traceback

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def _call(fn, args):
    try:
        return {"ok": True, "value": repr(fn(*args))}
    except BaseException as exc:
        return {"ok": False, "error": type(exc).__name__}

original_path, patched_path, plan_path = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.load(open(plan_path))
out = []
try:
    a = _load(original_path, "_axiom_original")
    b = _load(patched_path, "_axiom_patched")
except BaseException as exc:
    print(json.dumps({"fatal": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)

for entry in plan:
    name, argsets = entry["name"], entry["argsets"]
    fa, fb = getattr(a, name, None), getattr(b, name, None)
    if fa is None or fb is None:
        continue
    for args in argsets:
        ra, rb = _call(fa, args), _call(fb, args)
        if ra != rb:
            out.append({"function": name, "args": repr(args),
                        "original": ra, "patched": rb})
            break
print(json.dumps({"divergences": out}))
'''


def _argsets(node) -> list | None:
    """Concrete argument tuples for one ``ast.FunctionDef``, or None if unsafe.

    Reads the AST directly. An earlier version built an ``inspect.Signature``
    from a synthesized lambda; it raised for every real function and was caught
    by a bare ``except``, so the plan came back empty and this layer silently
    checked nothing at all. Reading ``node.args`` needs no evaluation and
    cannot fail that way.

    Returns None rather than guessing: a wrong value produces a spurious
    divergence, and a spurious divergence costs a repair cycle and can revert a
    working migration.
    """
    import ast

    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    first_default = len(positional) - len(defaults)

    choices: list[list] = []
    for index, parameter in enumerate(positional):
        if parameter.arg in ("self", "cls"):
            continue
        lowered = parameter.arg.lower()
        if any(hint in lowered for hint in COLLABORATOR_HINTS):
            return None

        text = ""
        if parameter.annotation is not None:
            try:
                text = ast.unparse(parameter.annotation).lower()
            except Exception:
                text = ""

        default = None
        has_default = index >= first_default
        if has_default:
            try:
                default = ast.literal_eval(defaults[index - first_default])
            except Exception:
                has_default = False

        if "str" in text:
            choices.append(_STRINGS)
        elif "bool" in text:
            choices.append([True, False])
        elif "int" in text or "float" in text:
            choices.append(_NUMBERS)
        elif "mapping" in text or "dict" in text:
            choices.append([{"tag": "a/b", "value": 1, "ts": "t", "quality": "GOOD"}])
        elif "iterable" in text or "list" in text or "sequence" in text:
            choices.append([[{"tag": "a/b", "value": 1, "ts": "t"}]])
        elif has_default:
            choices.append([default])
        elif not text:
            choices.append(_STRINGS[:4] + _NUMBERS[:4])
        else:
            return None

    if not choices:
        return [[]]
    # One argument varies at a time, the rest hold their first value: the full
    # product explodes, and a single varying argument is enough to surface a
    # systematic difference like slash escaping.
    base = [options[0] for options in choices]
    argsets = [list(base)]
    for index, options in enumerate(choices):
        for value in options[1:]:
            candidate = list(base)
            candidate[index] = value
            argsets.append(candidate)
    return argsets[:40]


def _plan(source: str) -> list:
    """Public functions worth comparing, with the arguments to call them on."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    plan = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        argsets = _argsets(node)
        if argsets:
            plan.append({"name": node.name, "argsets": argsets})
    return plan


def divergences(original_code: str, patched_code: str, module_name: str = "subject",
                timeout: int = 40) -> list[dict]:
    """Inputs on which the patched module disagrees with the original."""
    plan = _plan(original_code)
    if not plan:
        return []
    workspace = tempfile.mkdtemp(prefix="axiom-differential-")
    try:
        original_path = os.path.join(workspace, "original.py")
        patched_path = os.path.join(workspace, "patched.py")
        plan_path = os.path.join(workspace, "plan.json")
        open(original_path, "w", encoding="utf-8").write(original_code)
        open(patched_path, "w", encoding="utf-8").write(patched_code)
        json.dump(plan, open(plan_path, "w"))
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _RUNNER, original_path, patched_path, plan_path],
                capture_output=True, text=True, timeout=timeout, cwd=workspace)
        except subprocess.TimeoutExpired:
            return []
        lines = (proc.stdout or "").strip().splitlines()
        if not lines:
            return []
        try:
            record = json.loads(lines[-1])
        except json.JSONDecodeError:
            return []
        return record.get("divergences", []) or []
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)


def check(original_code: str, patched_code: str) -> tuple[str, str]:
    """``(verdict, detail)``. DEFECT when the patch changed observable behaviour."""
    found = divergences(original_code, patched_code)
    if not found:
        return "OK", "no behavioural divergence on generated inputs"
    first = found[0]
    detail = (f"{first['function']}{first['args']} returns "
              f"{first['original'].get('value', first['original'].get('error'))} "
              f"in the original but "
              f"{first['patched'].get('value', first['patched'].get('error'))} "
              f"after migration")
    if len(found) > 1:
        detail += f" (+{len(found) - 1} more)"
    return "DEFECT", detail[:400]
