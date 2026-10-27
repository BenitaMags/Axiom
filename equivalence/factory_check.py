"""Layer 5 — exercise the code path the injected tests never reach.

Why this exists
───────────────
Every program in this corpus is written for dependency injection: the object
that talks to the outside world is built by a small factory (``_default_client``,
``_default_session``) which runs **only when the caller injects nothing**. Both
the corpus's own tests and this repository's probes inject a fake, so the
factory — which is exactly where the library substitution lives — is never
executed by either.

That is not a hypothetical gap. On D1 a single prompt produced::

    instrument = minimalmodbus.Instrument(host, 1)

passing a TCP hostname to a constructor whose first parameter is a serial
device path. It scored 5/5 on the suite and passed all four oracle layers,
because nothing ever called it. Run it and you get::

    SerialException: could not open port 192.168.0.10:
        No such file or directory: '192.168.0.10'

The failure names the host being opened as a file. That is the evidence this
module collects.

What it can and cannot conclude
───────────────────────────────
Executing a factory without hardware or network will usually fail even when
the migration is perfect — a correct TCP client still cannot reach a host that
is not there. So a bare exception proves nothing, and the classification below
is deliberately conservative:

    MISUSE        TypeError / AttributeError / NameError / ValueError; an
                  import of a module that is neither the declared source nor
                  target (an invented dependency); or an OS-level "no such
                  file" naming the address we passed as a host. None of these
                  can be caused by absent hardware — the call itself is wrong.
    UNREACHABLE   connection refused, timeout, DNS failure. Consistent with a
                  correct migration and no server. Reported, never failed.
    OK            constructed without raising.
    SKIPPED       no factory found, or the declared source/target library is
                  not installed here — which the pristine program suffers too,
                  so it is an environment fact rather than a defect.

Only MISUSE is treated as a failure. UNREACHABLE is recorded verbatim so a
reader can audit the judgement rather than trust it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

#: A documentation address (RFC 5737). Routable-looking but guaranteed not to
#: be a real host, so a correct network client fails as UNREACHABLE rather
#: than accidentally connecting to something.
PROBE_HOST = "192.0.2.1"

#: Exception types that cannot be produced by absent hardware or a missing
#: package — the call itself is wrong. ImportError/ModuleNotFoundError are
#: deliberately NOT here: a library that is simply not installed on this
#: machine raises exactly that, and flagging it would condemn the pristine
#: program too (D1's original raises ModuleNotFoundError for pymodbus). They
#: are handled separately, where the missing module's NAME decides it.
_MISUSE_TYPES = ("TypeError", "AttributeError", "NameError", "ValueError")

_UNREACHABLE_SIGNS = ("connection refused", "timed out", "timeout",
                      "name or service not known", "nodename nor servname",
                      "network is unreachable", "no route to host",
                      "failed to resolve", "connection error")


def find_factories(clone_dir: str, target_lib: str) -> list:
    """Module-level functions whose body imports the migration target.

    These are the factories: the substitution lives inside them, and the
    injected tests route around them.
    """
    root = target_lib.split()[0].replace("-", "_").lower()
    found = []
    for name in sorted(os.listdir(clone_dir)):
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        try:
            tree = ast.parse(open(os.path.join(clone_dir, name),
                                  encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                mods = []
                if isinstance(sub, ast.Import):
                    mods = [a.name for a in sub.names]
                elif isinstance(sub, ast.ImportFrom):
                    mods = [sub.module or ""]
                if any(root in (m or "").lower().replace("-", "_")
                       for m in mods):
                    args = [a.arg for a in node.args.args]
                    found.append((name[:-3], node.name, args))
                    break
    return found


_RUNNER = r'''
import importlib, json, sys, traceback
sys.path.insert(0, {clone!r})
out = []
for module_name, func_name, argnames in {factories!r}:
    rec = {{"module": module_name, "func": func_name}}
    try:
        mod = importlib.import_module(module_name)
        fn = getattr(mod, func_name)
    except Exception as exc:
        rec.update(status="SKIPPED", detail=f"{{type(exc).__name__}}: {{exc}}"[:200])
        out.append(rec); continue
    # Positional args are guessed from the parameter NAME, which is the only
    # signal available without types: host-ish names get the probe address,
    # everything else a small int. Wrong guesses surface as MISUSE, so the
    # caller must read the detail rather than trust the verdict blindly.
    args = []
    for a in argnames:
        low = a.lower()
        if any(k in low for k in ("host", "url", "addr", "port_name",
                                  "device", "path", "broker", "endpoint")):
            args.append({host!r})
        elif "port" in low:
            args.append(502)
        else:
            args.append(1)
    try:
        fn(*args)
        rec.update(status="OK", detail="constructed without raising")
    except Exception as exc:
        rec.update(status="RAISED", etype=type(exc).__name__,
                   detail=f"{{type(exc).__name__}}: {{exc}}"[:220])
    out.append(rec)
print(json.dumps(out))
'''


def run_factories(clone_dir: str, factories: list, timeout: int = 45,
                  source_lib: str = "", target_lib: str = "") -> list:
    """Execute each factory in a subprocess and classify what happened."""
    if not factories:
        return []
    script = _RUNNER.format(clone=os.path.abspath(clone_dir),
                            factories=factories, host=PROBE_HOST)
    try:
        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [{"module": "?", "func": "?", "status": "UNREACHABLE",
                 "detail": f"factory blocked for {timeout}s (likely a real "
                           f"connection attempt)"}]
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return [{"module": "?", "func": "?", "status": "SKIPPED",
                 "detail": (proc.stderr or "no output")[:200]}]
    try:
        results = json.loads(line[-1])
    except json.JSONDecodeError:
        return [{"module": "?", "func": "?", "status": "SKIPPED",
                 "detail": line[-1][:200]}]

    for rec in results:
        if rec.get("status") != "RAISED":
            continue
        etype = rec.get("etype", "")
        detail = rec.get("detail", "")
        low = detail.lower()
        if etype in ("ModuleNotFoundError", "ImportError"):
            # Which module is missing decides this. The declared source or
            # target simply not being installed here is an environment fact,
            # not a defect — the pristine program raises it too. Any OTHER
            # module name means the code imports something that does not
            # exist, which is the invented-dependency failure.
            missing = ""
            if "No module named" in detail:
                missing = detail.split("No module named", 1)[1]
                missing = missing.strip().strip("'\"")
            rec["status"] = ("SKIPPED"
                             if _known(missing, source_lib, target_lib)
                             else "MISUSE")
        elif etype in _MISUSE_TYPES:
            rec["status"] = "MISUSE"
        elif any(sign in low for sign in _UNREACHABLE_SIGNS):
            rec["status"] = "UNREACHABLE"
        elif PROBE_HOST in detail and ("no such file" in low
                                       or "could not open" in low):
            # The address we passed as a network host is being opened as a
            # path. This is D1's failure exactly, and no absent server can
            # produce it.
            rec["status"] = "MISUSE"
        else:
            rec["status"] = "UNREACHABLE"
    return results


def _known(name: str, *libs: str) -> bool:
    """Is this module one of the declared libraries for the migration?"""
    root = (name or "").split(".")[0].lower().replace("-", "_")
    return any(root == (l or "").split()[0].lower().replace("-", "_")
               for l in libs if l)


def check(clone_dir: str, target_lib: str, source_lib: str = "") -> tuple:
    """(verdict, detail) for one clone. Verdict is OK / MISUSE / SKIPPED."""
    factories = find_factories(clone_dir, target_lib)
    if not factories:
        return "SKIPPED", "no factory imports the target library"
    results = run_factories(clone_dir, factories, source_lib=source_lib,
                            target_lib=target_lib)
    misuse = [r for r in results if r.get("status") == "MISUSE"]
    if misuse:
        r = misuse[0]
        return "MISUSE", f"{r['module']}.{r['func']}: {r['detail']}"[:200]
    statuses = ",".join(sorted({r.get("status", "?") for r in results}))
    detail = "; ".join(f"{r['module']}.{r['func']}={r.get('status')}"
                       for r in results)
    return ("OK" if "OK" in statuses else "SKIPPED"), detail[:200]
