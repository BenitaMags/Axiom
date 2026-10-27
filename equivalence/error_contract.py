"""Layer 7 — did the migration change what the program does when things fail?

The defect this exists for
──────────────────────────
D3's migrated ``sink.py`` wraps its publish call like this::

    def publish(self, topic, payload):
        try:
            self._client.publish(topic, payload)
            return _PublishResult(0)
        except Exception:
            return _PublishResult(1)

The original had no handler at all, so paho's ``ValueError: Publish topic
cannot contain wildcards`` propagated out of ``MqttSink.publish``. Measured
side by side:

    ORIGINAL (paho)  -> RAISED ValueError: Publish topic cannot contain wildcards.
    CLONE   (gmqtt)  -> returned rc=1

And ``gateway.py`` calls ``self.sink.publish(tag, record)`` while discarding the
return value, so a telemetry gateway that used to fail loudly now silently drops
data. That is live and reachable through the program's own public API.

Every existing layer scores this clone as a complete success:

    tests_passed   the injected fake never raises, so no error path is executed
    oracle_equiv   the golden vectors are all happy-path inputs
    layer 6        constructs the subject and makes one SUCCESSFUL call; it asks
                   whether the call is well-formed, never what failure looks like

A blind LLM judge found it and rated it MAJOR. This module is the deterministic
form of that finding.

Why this is static rather than executed
───────────────────────────────────────
The obvious design is to feed the program an invalid input and compare
raise-versus-return against the pristine original. It does not work here. The
swallowing lives in the ``_default_*`` factory's wrapper, not in
``MqttSink.publish``, so injecting a collaborator that raises bypasses the very
code under test -- and reaching the wrapper means constructing a real client,
which needs a live broker whose API differs for every library a clone might
adopt.

The AST shows the same fact without any of that: the clone introduced a handler
that catches and does not re-raise, where the original had none. That is
library-agnostic, deterministic, costs nothing, and cannot be defeated by a
collaborator that happens not to fail.

Differential, like every other layer here
─────────────────────────────────────────
Only handlers the MIGRATION introduced are reported. A program that already
swallowed its own errors is not made worse by a faithful copy, and failing a
clone for reproducing the original's behaviour is the mistake an early C1
invariant made by asserting a clamp the pristine program never performed.

Handlers are identified by (enclosing function, exception types caught, what
the body does) and never by variable names, because the Logic Substitution
Agent renames locals -- a clone's ``_spt_r2`` is the original's ``result``, and
a signature that included it would call every diversified file a divergence.

KNOWN LIMITATION -- read before acting on a DEFECT
──────────────────────────────────────────────────
This layer cannot tell a swallowed error from a correctly translated one, and
the difference is not visible in the code it reads.

    paho's ``publish`` RAISES ValueError on a bad topic.
        -> a clone that returns a status instead has swallowed it.   DEFECT.

    pymodbus's ``read_holding_registers`` RETURNS a response whose
    ``isError()`` reports the failure; it does not raise.
        -> a clone that catches modbus-tk's ModbusError and returns
           ``_Result(error=True)`` is REPRODUCING the original contract.
           Correct. This layer still reports it.

Both look identical here, because the comparison is against the original
FIRST-PARTY file -- and D1's original has no ``try``/``except`` at all, since it
relies on ``rr.isError()``. Telling them apart needs the source library's error
model per API, which is not in the sources being diffed.

This was not theoretical. The check briefly drove the Verifier's repair loop,
which told the Connector to re-raise in D1's adapter. ``_Result`` was then only
ever built with ``error=False``, ``isError()`` became unreachable, both
``raise IOError(...)`` branches became dead code, and a blind judge rated the
result MAJOR twice. B3 regressed the same way against paho's non-fatal
``info.rc``.

So a DEFECT here is a LEAD, not a verdict: report it, count it with this caveat
attached, and never let it steer a repair. On this corpus the modbus subjects
(B2, D1) report a status rather than raising, so most of their DEFECTs are
expected to be correct translations rather than real losses.
"""
from __future__ import annotations

import ast
import os

#: Handler bodies that let the error keep travelling. Anything else stops it.
_PROPAGATES = ("reraise",)

#: Methods where swallowing is the documented, correct thing to do, so a new
#: handler in one is not a fidelity finding.
#:
#: ``__del__`` is the only entry so far, and it is not a judgement call: Python
#: ignores exceptions raised in a destructor and prints them to stderr, so
#: ``except Exception: pass`` there is idiomatic rather than a swallowed error.
#: Measured before this exclusion, four clones were reported solely for a
#: ``__del__`` guard -- correct code, flagged. Note that ``close`` is NOT here:
#: a close() that hides a flush failure loses data, which is exactly the class
#: of defect this layer exists to find.
_SWALLOWING_IS_CORRECT = ("__del__",)


def _enclosing(tree: ast.AST) -> dict[ast.AST, str]:
    """Map each node to the qualified name of the function containing it."""
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                owner[child] = name
                walk(child, name)
            else:
                owner[child] = prefix
                walk(child, prefix)

    walk(tree, "")
    return owner


def _caught(handler: ast.ExceptHandler) -> tuple[str, ...]:
    """The exception types this handler catches; ``("",)`` for a bare except."""
    node = handler.type
    if node is None:
        return ("",)
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    names = []
    for part in parts:
        if isinstance(part, ast.Name):
            names.append(part.id)
        elif isinstance(part, ast.Attribute):
            names.append(part.attr)
        else:
            names.append("?")
    return tuple(sorted(names))


def _action(handler: ast.ExceptHandler) -> str:
    """What the handler does with the error it caught.

    ``reraise`` is the only outcome that preserves the caller's contract. A
    handler that returns, passes or only logs converts a raised error into a
    normal-looking result, which is the divergence this layer reports.
    """
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return "reraise"
    for node in ast.walk(handler):
        if isinstance(node, ast.Return):
            return "return"
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        return "pass"
    return "swallow"


def _guards_a_call(node: ast.Try) -> bool:
    """Only handlers wrapped around a call are interesting.

    Any call can fail, so this is not limited to collaborator methods: a clone
    that turns ``int(text)`` raising ValueError into a silent ``return 0`` has
    changed the contract exactly as much as one that swallows a publish error.
    A guarded body with no call at all -- an index, an attribute read, an
    arithmetic expression -- is left alone, because reporting it would be noise
    against the corpus.
    """
    return any(isinstance(child, ast.Call) for stmt in node.body
               for child in ast.walk(stmt))


def handler_signatures(source: str) -> set[tuple[str, tuple[str, ...], str]]:
    """Every exception handler in ``source``, as comparable signatures.

    Deliberately excludes variable names so that a diversified clone and its
    original produce identical signatures for identical error handling.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    owner = _enclosing(tree)
    found: set[tuple[str, tuple[str, ...], str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _guards_a_call(node):
            continue
        for handler in node.handlers:
            found.add((owner.get(handler, ""), _caught(handler), _action(handler)))
    return found


def _program_handlers(directory: str, source_files) -> set:
    """Every handler signature in a program, across all its non-test modules."""
    found: set = set()
    names = set(source_files)
    try:
        names |= {n for n in os.listdir(directory)
                  if n.endswith(".py") and not n.startswith("test_")}
    except OSError:
        pass
    for name in sorted(names):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            found |= handler_signatures(open(path, encoding="utf-8").read())
    return found


def divergences(original_dir: str, clone_dir: str, source_files) -> list[dict]:
    """Handlers the migration ADDED that stop an error the original propagated.

    Two passes, because a migration can add a handler in two different places.

    The first pass matches files by NAME and diffs each pair, which is precise
    about where a handler appeared. It cannot see a module the clone ADDED --
    there is no same-named original to diff against, so the loop skips it.

    That gap stopped being hypothetical once variants began extracting modules.
    Measured on a D1 clone whose Connector adapter was restructured into
    ``modbus_bridge.py``: both of its ``except Exception: return`` handlers
    were invisible here and the layer reported OK, while a blind judge rated
    the clone MAJOR for exactly them -- "every exception including socket
    timeouts and connection loss is swallowed". So the second pass compares
    handlers in ADDED modules against the whole original program.

    A handler in an added module is reported only when its (caught, action)
    pair appears NOWHERE in the original. Matching on the pair rather than on
    the qualified name is what keeps a handler that merely MOVED into the new
    module from reading as a new swallow: relocating code is a restructuring,
    not a fidelity defect, and flagging it would penalise exactly the variants
    this project wants.
    """
    results: list[dict] = []
    for name in source_files:
        original = os.path.join(original_dir, name)
        clone = os.path.join(clone_dir, name)
        if not (os.path.isfile(original) and os.path.isfile(clone)):
            continue
        before = handler_signatures(open(original, encoding="utf-8").read())
        after = handler_signatures(open(clone, encoding="utf-8").read())
        for where, caught, action in sorted(after - before):
            if action in _PROPAGATES:
                continue
            if where.rsplit(".", 1)[-1] in _SWALLOWING_IS_CORRECT:
                continue
            shown = ", ".join(c or "<bare except>" for c in caught)
            results.append({
                "file": name,
                "function": where or "<module>",
                "caught": shown,
                "action": action,
                "detail": (
                    f"{name}:{where or '<module>'} catches ({shown}) and "
                    f"{action}s instead of re-raising. The original propagated "
                    f"this failure to the caller; the migrated code does not, "
                    f"so a caller that relied on the error now sees a normal "
                    f"return."
                ),
            })

    # ── second pass: modules the clone ADDED ──────────────────────────────
    try:
        present = sorted(n for n in os.listdir(clone_dir)
                         if n.endswith(".py") and not n.startswith("test_"))
    except OSError:
        present = []
    added = [n for n in present
             if n not in set(source_files)
             and not os.path.isfile(os.path.join(original_dir, n))]
    if added:
        original_pairs = {(caught, action) for _, caught, action
                          in _program_handlers(original_dir, source_files)}
        for name in added:
            clone_path = os.path.join(clone_dir, name)
            for where, caught, action in sorted(
                    handler_signatures(open(clone_path, encoding="utf-8").read())):
                if action in _PROPAGATES:
                    continue
                if where.rsplit(".", 1)[-1] in _SWALLOWING_IS_CORRECT:
                    continue
                if (caught, action) in original_pairs:
                    continue          # the handler moved here; it is not new
                # Only a BROAD catch counts here. Narrowly catching the target
                # library's own error and returning a status is how a faithful
                # adapter is written when the ORIGINAL library reported that
                # failure by return value rather than by raising -- D1's
                # pymodbus reports through ``rr.isError()`` on the object it
                # returns, so ``except ModbusError: return _Result(error=exc)``
                # reproduces the original contract exactly.
                #
                # A blind judge drew this same line on two D1 adapters: it
                # rated ``except Exception`` MAJOR -- "every exception
                # including socket timeouts and connection loss is swallowed"
                # -- and had nothing to say about ``except ModbusError``. The
                # first pass over matched files keeps its own looser rule,
                # which is measured and record-only; applying that rule to
                # added modules turned the faithful adapter into a DEFECT the
                # moment this pass could see it.
                if not any(c in ("Exception", "BaseException", "") for c in caught):
                    continue
                shown = ", ".join(c or "<bare except>" for c in caught)
                results.append({
                    "file": name,
                    "function": where or "<module>",
                    "caught": shown,
                    "action": action,
                    "detail": (
                        f"{name}:{where or '<module>'} catches ({shown}) and "
                        f"{action}s instead of re-raising. This module was "
                        f"ADDED by the migration and the original program has "
                        f"no handler like it anywhere, so a failure the "
                        f"original propagated to the caller now returns "
                        f"normally."
                    ),
                })
    return results


def check(original_dir: str, clone_dir: str, source_files) -> tuple[str, str]:
    """``(verdict, detail)``. DEFECT / OK / SKIPPED.

    DEFECT means the migration introduced error swallowing that the original
    did not have. It is a fidelity finding, not a crash: the code runs, and
    that is precisely why no other layer sees it.
    """
    if not (os.path.isdir(original_dir) and os.path.isdir(clone_dir)):
        return "SKIPPED", "directory missing"
    found = divergences(original_dir, clone_dir, source_files)
    if not found:
        return "OK", "error contract preserved"
    head = found[0]["detail"]
    extra = f" (+{len(found) - 1} more)" if len(found) > 1 else ""
    return "DEFECT", (head + extra)[:400]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Compare a clone's error contract against the pristine original.")
    ap.add_argument("original_dir")
    ap.add_argument("clone_dir")
    ap.add_argument("files", nargs="+", help="first-party source files to compare")
    args = ap.parse_args()
    verdict, detail = check(args.original_dir, args.clone_dir, args.files)
    print(f"{verdict}\t{detail}")
    raise SystemExit(1 if verdict == "DEFECT" else 0)
