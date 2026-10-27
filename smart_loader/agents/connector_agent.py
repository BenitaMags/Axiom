"""
agents/connector_agent.py
──────────────────────────
Connector Agent — Final node in the AXIOM LangGraph pipeline.

APPROACH (v3 — inline injection):
  Instead of generating a separate .py module and rewriting imports,
  this agent injects transformation helper functions DIRECTLY into the
  top of the target source file, right after the imports block.

  This means:
    - No separate connector file to manage or save
    - No import rewriting (the original package name stays)
    - The functions wrap incompatible API calls at the call site level
    - Every injected function has a comment block explaining what it does
      and WHY it's needed (the input/output type mismatch)

  Example — hashlib → xxhash:
    Original code:  hashlib.sha256(blob).hexdigest()
    Problem:        xxhash.sha256(blob) returns an xxhash object,
                    same .hexdigest() interface — but if the model
                    swaps to a package where return type differs,
                    we inject a wrapper.

DETECTION LOGIC (no hardcoding):
  The agent uses AST analysis to extract EVERY call site of the original
  package, including full method chains (e.g. .sha256().hexdigest()).
  It passes these to the LLM with a structured checklist so the LLM
  can reason about return type compatibility before generating adapters.

  The LLM is asked to:
    1. For each call site: does the new package return a compatible type?
    2. If yes → no wrapper needed, note it
    3. If no  → generate a named wrapper function with full docstring
    4. Then rewrite ONLY the affected call sites in the source

OUTPUT:
  - patched_code: original source with injected helpers + rewritten call sites
  - connectors: dict of {original: {"functions": [...], "call_rewrites": [...]}}
"""

from __future__ import annotations
import ast
import re
import time
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from smart_loader.core.ast_rewrite import (
    apply_call_site_rewrites,
    apply_import_replacements,
    names_bound_from_root,
    resolve_import_root,
    rewrite_simple_attrs_to_dataclass,
)
from smart_loader.core.llm_factory import _get_llm
# Module-level: connector_agent() uses coerce_llm_text at its JSON-parse site,
# but the import only existed inside two *other* functions in this file, so
# every connector call raised NameError. The verifier then correctly reported
# "no source change" and burned all five repair cycles retrying a bug that no
# amount of retrying could fix — 5 wasted LLM calls per program.
from smart_loader.core.llm_json import coerce_llm_text
from smart_loader.core.llm_timeout import invoke_with_timeout
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

console = Console()



# ── AST call site extractor ───────────────────────────────────────────────────

def _extract_call_sites(source_code: str, package: str) -> list[dict]:
    """
    Walk the AST and extract every call site that involves `package`.
    Returns a list of dicts with:
      - line       : line number (1-based)
      - source_line: the full source line text
      - call_expr  : the call expression as a string (e.g. "hashlib.sha256(blob)")
      - full_expr  : the outermost expression (e.g. "hashlib.sha256(blob).hexdigest()")
      - method_chain: list of chained method names after the call (e.g. ["hexdigest"])
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    lines = source_code.splitlines()
    results = []

    # First pass: find import alias for the package
    pkg_alias = package
    imported_names: dict[str, str] = {}  # local_name → original_name

    # A distribution name is not always spellable as its import path.
    # ``paho-mqtt`` arrives here as ``paho_mqtt`` (hyphens replaced), but the
    # module is imported as ``paho.mqtt.client``, and
    # "paho.mqtt.client".startswith("paho_mqtt.") is False — so every call site
    # was missed, the connector reported "no call sites found", produced no
    # change, and the verifier burned all five repair cycles. That silently
    # cost B3 and D3 every run.
    #
    # Accept any spelling that differs only in the separator, plus the leading
    # segment on its own, which is the real import root for namespace-style
    # distributions like paho-mqtt -> paho.mqtt.
    def _candidate_roots(name: str) -> list[str]:
        dotted = name.replace("-", ".").replace("_", ".")
        seen = [name, dotted, name.replace("-", "_"), dotted.split(".")[0]]
        out: list[str] = []
        for candidate in seen:
            if candidate and candidate not in out:
                out.append(candidate)
        return out

    roots = _candidate_roots(package)

    def _matches(module: str) -> bool:
        return any(module == r or module.startswith(f"{r}.") for r in roots)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _matches(alias.name):
                    pkg_alias = alias.asname or alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and _matches(node.module):
                for alias in node.names:
                    imported_names[alias.asname or alias.name] = alias.name

    def _get_base_name(node) -> str | None:
        """Walk an Attribute/Name chain and return the root Name id."""
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None

    def _involves_pkg(node) -> bool:
        base = _get_base_name(node)
        return base == pkg_alias or base in imported_names

    def _collect_chain(node) -> list[str]:
        """Collect attribute chain upward from a node."""
        chain = []
        current = node
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        return list(reversed(chain))

    # Second pass: collect call nodes that reference the package
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _involves_pkg(node.func):
            continue

        call_str = ast.unparse(node)
        line_num  = node.lineno
        src_line  = lines[line_num - 1].strip() if line_num <= len(lines) else ""

        # Detect method chain AFTER this call
        # e.g. hashlib.sha256(blob).hexdigest() — the Call node for sha256()
        # will be the .value of an Attribute node (.hexdigest) which is
        # itself the .func of another Call node (.hexdigest())
        method_chain: list[str] = []

        results.append({
            "line":         line_num,
            "source_line":  src_line,
            "call_expr":    call_str,
            "full_expr":    src_line,   # full line gives best context
            "method_chain": method_chain,
        })

    return results


def _find_import_block_end(source_code: str) -> int:
    """
    Return the line index (0-based) AFTER the last import statement
    in the file. This is where we inject helper functions.
    """
    lines = source_code.splitlines()
    last_import_line = 0
    try:
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import_line = max(last_import_line, node.lineno)
    except SyntaxError:
        pass
    return last_import_line   # 1-based line number of last import


def _top_level_definition_names(source: str) -> set[str]:
    """Names whose disappearance would indicate an unsafe whole-file edit."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _preserves_top_level_definitions(before: str, after: str) -> bool:
    """Reject an ACI response that silently deletes application code."""
    if not after.strip():
        return False
    try:
        ast.parse(after)
    except SyntaxError:
        return False
    return _top_level_definition_names(before).issubset(_top_level_definition_names(after))


# ── Prompt ────────────────────────────────────────────────────────────────────

ANALYSIS_SYSTEM = """You are an expert Python API compatibility engineer.

You will be given:
1. A package migration: FROM package A → TO package B
2. Every call site in the source code where package A is used (with full context)
3. Known pitfalls about the migration

Your job is to:

STEP 1 — ANALYZE each call site:
  For each call, determine:
  a) Does package B have an equivalent function/method with the same name?
  b) Does the return type match? (CRITICAL — e.g. bytes vs str, object vs primitive)
  c) Does the method chain still work? (e.g. .sha256(data).hexdigest())
  d) Are parameter names/types compatible?

STEP 2 — GENERATE wrapper functions ONLY for incompatible call sites.
  Rules:
  - If the call is fully compatible → write "COMPATIBLE: no wrapper needed"
  - If incompatible → note that a wrapper is needed and give it a
    descriptive name (e.g. `_axiom_sha256_wrapper`) and a one-line summary
    of what transformation it must perform. The actual wrapper CODE and its
    injection into the source happen in a separate editing pass — here you
    are only identifying WHERE wrappers are needed and WHAT they must do.

STEP 3 — ASSESS OVERALL MIGRATION FEASIBILITY:
  After analyzing every call site and drafting wrappers, step back and judge
  whether this migration is actually achievable end-to-end.
  A wrapper CAN fix: wrong return types, renamed parameters, missing
  convenience methods, minor signature mismatches — anything where writing
  glue code around the call closes the gap.
  A wrapper CANNOT fix: a missing decorator/registration mechanism (e.g. a
  route decorator that doesn't exist on the target), a missing dependency-
  injection system, a fundamentally different async/sync execution model
  the call sites depend on, or any other structural feature the code relies
  on that the target package has no path to support even with glue code.
  Set migration_feasible=false and list every such gap in
  remaining_incompatibilities if ANY call site has this kind of structural
  gap — do not contradict yourself by setting migration_feasible=true while
  a wrapper docstring admits the migration is incomplete.

Return a JSON object with this exact structure:
{
  "analysis": [
    {
      "line": <int>,
      "call": "<call expression>",
      "compatible": <true/false>,
      "reason": "<why compatible or not>",
      "wrapper_name": "<descriptive function name if incompatible, else null>",
      "wrapper_summary": "<one line: what the wrapper must do, if incompatible>"
    }
  ],
  "migration_feasible": <true/false>,
  "remaining_incompatibilities": ["<short description of each structural gap, if any>"]
}

CRITICAL RULES:
- TARGET IDENTITY LOCK: Package B is the *only* migration target.  Never
  claim that an API is compatible because a different package provides it.
  In particular, do not substitute, compare against, import, or cite another
  candidate as evidence for Package B.  The human message gives both B's
  distribution name and its Python import root; use those exact identities.
  If you cannot establish that B supports a call, mark that call incompatible
  and request a wrapper (or mark the migration infeasible).  Do not guess.
- migration_feasible and remaining_incompatibilities are REQUIRED fields, always include them.
- Do NOT write wrapper code here — this pass is analysis only. Wrapper code
  and source edits happen in a separate step.
"""

ANALYSIS_HUMAN = """
MIGRATION: {original} → {winner}
LOCKED TARGET DISTRIBUTION: {winner}
LOCKED TARGET IMPORT ROOT: {winner_import}
ROLE: {role}

OTHER CANDIDATES (NOT migration targets; do not use them as compatibility
evidence): {other_candidates}

KNOWN PITFALLS:
{pitfalls}
{verifier_feedback}
CALL SITES FOUND IN SOURCE (all usages of {original}):
{call_sites}

FULL SOURCE CODE:
```python
{source_code}
```

Analyze every call site and return the required JSON. Do not write wrapper code in this response — just identify which call sites need one and what each must do.
"""

VERIFIER_FEEDBACK_BLOCK = """
VERIFIER FEEDBACK FROM PREVIOUS ATTEMPT (this is a retry — fix this specific problem):
{error}
"""

# ── Phase B — the ACI edit loop (replaces the old one-shot `replaces`/`with`
# string-matching approach entirely). See core/aci_tools.run_aci_edit_session
# and ACISession for the tool implementations (open/goto/scroll/search/edit/
# submit — adapted from SWE-agent, Yang et al., NeurIPS 2024).

ACI_EDIT_SYSTEM = """You are AXIOM's connector agent, editing a Python file to
migrate it from one package to another. You have direct, checked editing tools
over the file — use them instead of describing what should change.

Your task for THIS session: inject any wrapper function(s) needed to bridge an
API incompatibility, and rewrite the specific call site(s) that need them to
use your wrapper instead of calling {winner} directly.

Workflow:
1. Use `open` (and `search_file` / `goto` / `scroll_down` / `scroll_up` if the
   file is long) to look at the actual current line numbers — they may have
   shifted from what any earlier analysis reported, especially if this is a
   retry after a previous edit.
2. Use `edit` to insert wrapper function definition(s) — a good place is right
   after the last import statement — and to rewrite each incompatible call
   site to use your wrapper. `edit` is REJECTED (file unchanged, you'll get
   the exact error) if your replacement_text would introduce a syntax error,
   so re-check indentation and line ranges from the tool's response if that
   happens, then try again.
3. Every wrapper function needs a docstring explaining what {original}
   returned, what {winner} returns instead, and what transformation the
   wrapper applies and why.
4. When the file is correct and complete, call `submit`.

IMPORT LOCK: Do NOT edit, remove, or replace any existing package import.
The Connector's deterministic post-processing performs the one allowed source
→ target import rewrite after this session. Do not introduce or reference an
unselected candidate package in a wrapper, its docstring, or a call rewrite.
Use only {winner}'s API or Python standard-library code for the adapter.

The lock covers IMPORT STATEMENTS ONLY. It is not permission to leave {original}
in use. When your edit is done, NO code path may still call {original} or
construct its objects:

* Rewrite EVERY call site, not just the one the analysis listed. That includes
  default-factory helpers like `_default_client(...)`, which the analysis often
  does not name because the class that calls them is where the object is used.
  Rewriting the class to call your wrapper while leaving the helper still doing
  `ModbusTcpClient(host, port=port)` is NOT a migration — it leaves a live
  dependency on {original} in the file.
* Never pass the {original} module, or anything imported from it, into your
  wrapper as an argument. A wrapper whose parameter is the old module has not
  replaced anything.

Do this correctly and every {original} import is left unreferenced, and the
post-processing deletes it. Leave one live reference and the migration is
rejected and sent back to you with the offending line.

ASYNC TO SYNC: if {winner} is async-only and the call site is synchronous,
bridge it with a PERSISTENT background event loop, not ``asyncio.run`` per call.

``asyncio.run`` creates a loop, runs one coroutine, and tears the loop down.
Any connection the library opened is bound to that dead loop, so the next call
operates on closed transport state. Measured on this corpus: a B3 adapter
connected with one ``asyncio.run`` and published with another, and the verifier
correctly rejected it as "structurally unreliable". A second attempt opened a
fresh connection per publish instead, which works but silently changes the
program from one long-lived session into one connection per message.

The pattern that does work::

    import asyncio, threading

    class _SyncFacade:
        def __init__(self, ...):
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
            self._client = <async client>
            self._run(self._client.__aenter__())     # or .connect(...)

        def _run(self, coro):
            # Blocks until the coroutine finishes ON THE PERSISTENT LOOP, and
            # re-raises whatever it raised, so failures reach the caller.
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

        def publish(self, topic, payload, qos=0):
            self._run(self._client.publish(topic, payload, qos=qos))
            return _Result(0)

        def close(self):
            self._run(self._client.__aexit__(None, None, None))
            self._loop.call_soon_threadsafe(self._loop.stop)

The loop outlives the calls, so connection state stays valid. ``.result()``
propagates exceptions rather than converting them to a status code.

Do NOT fabricate a success code you cannot justify. If the async publish is
fire-and-forget and returns nothing, awaiting it proves only that it was
queued -- say so in the docstring rather than claiming delivery. Where the
library confirms delivery (an ack at qos >= 1), await that and report it
honestly.

DEFERRED IMPORTS: if the original imported inside a function, you must too.

    def _default_smtp(host="localhost", port=25):
        import smtplib          # lazy import for testability
        return smtplib.SMTP(host, port)

The import sits inside the function so the MODULE can be imported with the
library absent -- which is the whole point of injecting the collaborator in
tests. Hoisting the replacement to module scope::

    import apprise             # WRONG -- now `import notifier` needs apprise

breaks every caller that relied on that, and the project's own tests still
pass because they inject a fake. Measured on C3: all three seeds hoisted it,
all three were rated MAJOR by review for exactly this. Keep the import where
the original had it. The same holds for anything else the original deferred.

EXCEPTION CLASSES: handing back {winner}'s own object is not an adapter.

There are two ways to break the error contract. The rules below are about
writing a bad handler. This one is about writing no adapter at all:

    def _axiom_default_session_wrapper():
        import httpx
        return httpx.Client(follow_redirects=True)        # WRONG

That renames the constructor and adapts nothing. The call sites still run
`response.raise_for_status()`, which now raises `httpx.HTTPStatusError`
where {original} raised `requests.exceptions.HTTPError`; connection failures
and timeouts change class the same way. A caller written
`except requests.exceptions.HTTPError` silently stops catching the failure.
Every rule below is written about handlers, so an adapter with no handler
obeys all of them and still breaks its callers -- review rated exactly this
MAJOR, and it is the most common defect measured on this corpus.

So where {original} handed callers an object of its OWN type, return a small
first-party class that holds the {winner} object and re-exposes the method
names the call sites already call, translating {winner}'s exceptions into the
ones {original} raised for the same failure. Returning the raw {winner}
object is right only when nothing ever calls a method on it.

ERROR CONTRACT: your wrapper must not swallow failures.

Adapting one library's error style to another's is where callers silently
lose errors, and it is the single most common defect in generated adapters
here: every swallowing handler measured across the evaluation archive was in
connector glue.

The concrete case. A migrated MQTT sink wrapped its publish like this:

    def publish(self, topic, payload):
        try:
            self._client.publish(topic, payload)
            return _Result(0)
        except Exception:
            return _Result(1)          # WRONG

{original}'s publish RAISED `ValueError: Publish topic cannot contain
wildcards` on a bad topic, so the caller saw the failure. The wrapper turned
it into a return code that the caller discarded, and a telemetry gateway that
used to fail loudly began silently dropping data. Its tests still passed,
because the tests inject a fake that never raises.

So:

* Do NOT add `try`/`except` around a {winner} call just to be safe. If
  {original} let an exception reach the caller, {winner}'s exception must
  reach the caller too.
* Catch ONLY to translate a specific exception, and then re-raise:
  `except {winner}Error as exc: raise OriginalStyleError(...) from exc`.
  A handler that ends in `return`, `pass`, or a bare log has swallowed it.
* Convert an exception into a return value ONLY where {original} itself
  returned a status for that same failure. Reproducing {original}'s status
  codes is correct; inventing new ones is not.
* Never write `except Exception:` or a bare `except:` in an adapter.
* Narrow is not the same as ONE class. Catch every exception {winner} raises
  for the failure modes {original} reported at that call site -- which usually
  means protocol errors AND transport errors (connection refused, timeouts,
  short or malformed responses). Read {winner}'s exception hierarchy before
  choosing: these are often siblings rather than subclasses, so catching the
  one you first thought of lets the rest escape as raw {winner} exceptions the
  caller has never heard of. Catch their common base class, or a tuple of the
  specific classes. A measured case: an adapter caught `ModbusError` alone,
  and `ModbusInvalidResponseError` and socket timeouts -- both of which the
  original surfaced through its status object -- propagated out unconverted.
  The rule is "as broad as the failure mode, no broader", not "as narrow as
  possible".

Do not call `submit` until you've actually made the necessary `edit` call(s).
"""

#: Injected into the edit task when the target library is importable. This is
#: the grounding half of the mitigation: the model is writing glue for a
#: package it has only seen in training data, and the installed version is
#: right there to be read. De-Hallucinator (arXiv:2401.01701) and MARIN both
#: report that supplying the real API names is what fixes the prediction --
#: reporting the miss afterwards only tells the model to try another spelling.
API_SURFACE_BLOCK = """THE TARGET LIBRARY'S ACTUAL API, read from the installed package.

Use these names. Anything not listed here does not exist in this version, and
inventing one produces an ImportError or AttributeError the moment the real
library is loaded -- the project's own tests will not catch it, because they
inject a fake shaped like the OLD library.

{surface}
"""


ACI_EDIT_TASK = """MIGRATION: {original} → {winner}
ROLE: {role}

{api_surface}CALL SITES NEEDING A WRAPPER (from a prior analysis pass — line numbers may
have shifted since; verify with `open`/`goto` before editing):
{wrapper_needs}
{verifier_feedback}
KNOWN PITFALLS:
{pitfalls}

Begin by opening the file to see its current state.
"""


# ── Strip fences ──────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()


def _parse_json_response(raw: str) -> dict | None:
    """
    Try to extract and parse a JSON object from an LLM response.
    Delegates to core.llm_json, which strips <think>/<reasoning> blocks
    before bracket matching — required for NVIDIA reasoning models
    (e.g. nemotron-3-ultra) whose chain-of-thought otherwise pollutes a
    naive first-brace-to-last-brace scan and causes silent parse failures.
    """
    from smart_loader.core.llm_json import extract_json, coerce_llm_text
    return extract_json(raw, expect=dict)


def _blanket_except_sites(source: str, wrapper_names: list) -> list[str]:
    """Handlers inside the injected wrappers that catch everything and swallow.

    ``except Exception:`` / bare ``except:`` in adapter glue is the defect the
    prompt has forbidden in two places since the first pilot, and the model
    still produces it: on one D1 run the ACI session wrote
    ``except mt_exc.ModbusError:`` (correct -- modbus-tk's protocol failure,
    translated into the status the call sites already check) and on the next it
    wrote ``except Exception:`` twice. A blind judge rated the second MAJOR:
    "every exception including socket timeouts and connection loss is
    swallowed", because pymodbus propagated those while reporting only
    protocol failures through ``.isError()``.

    Reported, never auto-repaired. Narrowing the handler needs the target
    library's own error class, which is not derivable from the patched file,
    and REVERTING is worse still: this is a fidelity defect in a working
    adapter, and discarding it turns a migration into a no-op. That
    overcorrection is exactly what an earlier round of this check produced.
    So the finding goes back to the edit session, which has the library in
    front of it and got it right the first time.

    A handler that re-raises is fine however broadly it catches -- the error
    still reaches the caller, which is the whole contract.
    """
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return []
    wanted = {n for n in wrapper_names if n}
    seen: dict = {}

    def _broad(handler) -> bool:
        if handler.type is None:
            return True
        names = (handler.type.elts if isinstance(handler.type, _ast.Tuple)
                 else [handler.type])
        return any(getattr(n, "id", getattr(n, "attr", "")) in ("Exception",
                                                                "BaseException")
                   for n in names)

    def _reraises(handler) -> bool:
        return any(isinstance(n, _ast.Raise) for n in _ast.walk(handler))

    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if wanted and node.name not in wanted:
            # Nested helpers inside a wrapper count too -- the D1 case put the
            # handlers in inner functions the wrapper returned.
            enclosing = {node.name}
            for outer in _ast.walk(tree):
                if (isinstance(outer, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                        and outer.name in wanted
                        and node in _ast.walk(outer) and node is not outer):
                    enclosing.add(outer.name)
            if not (enclosing & wanted):
                continue
        for inner in _ast.walk(node):
            if isinstance(inner, _ast.Try):
                for handler in inner.handlers:
                    if _broad(handler) and not _reraises(handler):
                        caught = ("bare except" if handler.type is None
                                  else "except Exception")
                        # Keyed by line: walking a wrapper reaches a handler in
                        # a nested helper twice, once under each enclosing
                        # function, and reporting one defect as two would have
                        # the edit session hunting for a second site.
                        seen[handler.lineno] = (f"{node.name}() line "
                                                f"{handler.lineno}: `{caught}` "
                                                f"that does not re-raise")
    return [seen[k] for k in sorted(seen)]


def _raw_client_returns(source: str, winner: str, wrapper_names: list) -> list[str]:
    """Wrappers that hand back the target library's OWN object, unwrapped.

    The counterpart to ``_blanket_except_sites``: that one catches an adapter
    that swallows errors, this one catches an adapter that never sees them.

    Measured on D2. The connector replaced ``requests.Session()`` with::

        def _axiom_default_session_wrapper():
            import httpx
            return httpx.Client(follow_redirects=True, timeout=...)

    and stopped there. Nothing is wrapped, so ``response.raise_for_status()``
    in the program's own ``get_demand``/``get_runtimes``/``command`` now raises
    ``httpx.HTTPStatusError`` where it raised ``requests.exceptions.HTTPError``,
    and a caller with ``except requests.exceptions.HTTPError`` stops catching
    it. Transport failures change class the same way. A blind judge rated it
    MAJOR, and the same defect appears in every arm's D2 clone -- it is
    systematic, not one unlucky seed.

    Every error-contract rule in the prompt is written about handlers, so an
    adapter that writes no handler satisfies all of them. Layer 7 compares
    handlers too, and D2 has none on either side, so it reports no divergence
    and is right by its own definition. The exception classes change at a call
    site nobody wrapped.

    The tell is mechanical: the wrapper RETURNS a call rooted in a name bound
    by importing {winner}. Contrast the two adapters that got it right --
    D1 returned ``_ModbusTkClientShim(master)`` and C3 returned
    ``_AppriseSMTPAdapter(host, port, sender)``, both first-party classes whose
    methods can translate. Returning ``httpx.Client(...)`` renames the
    constructor and adapts nothing.

    Reported, never auto-repaired, for the reason given in
    ``_blanket_except_sites``: writing the translating wrapper needs the target
    library's exception hierarchy, which is not derivable from the patched
    file, and reverting would turn a working migration into a no-op.
    """
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return []

    root = (winner or "").split("[")[0].strip().lower()
    roots = {root.replace("-", "_"), root.split("-")[0]} - {""}
    if not roots:
        return []

    # Names that refer to the target package here, including aliases and
    # submodule imports: `import modbus_tk.modbus_tcp as modbus_tcp` binds
    # `modbus_tcp`, and `from httpx import Client` binds `Client`.
    bound: set = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in roots:
                    bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, _ast.ImportFrom) and node.module and not node.level:
            if node.module.split(".")[0].lower() in roots:
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
    if not bound:
        return []

    def _call_root(node):
        """The leftmost name of a call's callee: httpx.Client(...) -> httpx."""
        if not isinstance(node, _ast.Call):
            return None
        func = node.func
        while isinstance(func, _ast.Attribute):
            func = func.value
        return func.id if isinstance(func, _ast.Name) else None

    wanted = {n for n in wrapper_names if n}
    seen: dict = {}
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if wanted and node.name not in wanted:
            continue
        # `c = httpx.Client(); return c` is the same defect as returning the
        # call directly, so a returned bare name is resolved to what the
        # function assigned it.
        assigned: dict = {}
        for inner in _ast.walk(node):
            if isinstance(inner, _ast.Assign) and len(inner.targets) == 1:
                target = inner.targets[0]
                if isinstance(target, _ast.Name):
                    assigned[target.id] = inner.value

        for inner in _ast.walk(node):
            if not isinstance(inner, _ast.Return) or inner.value is None:
                continue
            returned = inner.value
            if isinstance(returned, _ast.Name) and returned.id in assigned:
                returned = assigned[returned.id]
            name = _call_root(returned)
            if name in bound:
                seen[inner.lineno] = (
                    f"{node.name}() line {inner.lineno}: returns "
                    f"`{name}...` -- the {winner} object itself, not an adapter")
    return [seen[k] for k in sorted(seen)]


def _fabricated_status_returns(source: str, wrapper_names: list) -> list[str]:
    """Wrappers that throw away what the library said and return a constant.

    The third shape in this family, after ``_blanket_except_sites`` (catches
    everything and swallows it) and ``_raw_client_returns`` (never sees the
    error at all). Here the adapter DOES call the library, discards the result,
    and reports success it never checked.

    Measured on two of the six rows where verification made a blind judge's
    verdict worse than the same program and seed without it. Both are the same
    two lines::

        B3  self._run(self._client.publish(topic, payload, qos=qos))
            return _PublishResult(0)

        D3  client._run(client._client.publish(topic, payload))
            return 0

    The originals returned ``getattr(info, "rc", 0)`` -- paho's real result
    code, non-zero when the client is disconnected. After the rewrite the
    status is a literal, so ``rc`` can never be anything but 0 and a caller
    checking it is checking a constant. The judge rated both MAJOR; the same
    programs without the Verifier scored MINOR.

    The prompt has forbidden this since the first pilot ("Do NOT fabricate a
    success code you cannot justify") and it happened anyway, which is the
    argument this file keeps making: a check that runs is worth more than an
    instruction that is read.

    The tell is a discarded call plus a constant return on the same path. A
    constant return inside an ``except`` handler is exempt -- translating a
    caught failure into the status the original reported is the correct
    adapter, and ``_blanket_except_sites`` already governs that case.
    """
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return []

    def _constantish(node, bound: set) -> bool:
        """A value owing nothing to any call made in this function."""
        if isinstance(node, _ast.Constant):
            return True
        if isinstance(node, _ast.Call):
            parts = list(node.args) + [kw.value for kw in node.keywords]
            return all(isinstance(a, _ast.Constant) for a in parts) and bool(parts)
        return False

    wanted = {n for n in wrapper_names if n}
    seen: dict = {}
    for fn in _ast.walk(tree):
        if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if wanted and fn.name not in wanted and not any(
                isinstance(o, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                and o.name in wanted and fn in _ast.walk(o) and fn is not o
                for o in _ast.walk(tree)):
            continue

        # Names that carry a call's result, so a return using one is derived.
        bound: set = set()
        for node in _ast.walk(fn):
            if isinstance(node, _ast.Assign) and isinstance(node.value, _ast.Call):
                bound |= {t.id for t in node.targets if isinstance(t, _ast.Name)}

        discarded = [n for n in _ast.walk(fn)
                     if isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Call)]
        if not discarded:
            continue

        # EXCEPTION-AS-STATUS is the correct adapter, not a fabrication. When
        # the discarded call sits in a `try` whose handler returns a failure
        # status, "no exception raised" IS the success the constant reports --
        # the information came from the call, through control flow rather than
        # through a return value. D1's faithful wrapper is exactly this::
        #
        #     try:
        #         master.execute(unit, cst.WRITE_SINGLE_REGISTER, ...)
        #     except (ModbusError, OSError):
        #         return _WriteResult(error=True)
        #     return _WriteResult(error=False)
        #
        # pymodbus returned a status object and modbus-tk raises, so this is
        # the translation the migration is FOR. Flagging it would push the
        # Connector to break a correct adapter -- the error this project has
        # now made three times under other names.
        translates_via_exception = any(
            isinstance(node, _ast.Try)
            and any(isinstance(d, _ast.Expr) and isinstance(d.value, _ast.Call)
                    for d in _ast.walk(node) if d in discarded)
            and any(isinstance(r, _ast.Return)
                    for h in node.handlers for r in _ast.walk(h))
            for node in _ast.walk(fn))
        if translates_via_exception:
            continue

        handled = {h for node in _ast.walk(fn)
                   if isinstance(node, _ast.Try)
                   for h in node.handlers}
        in_handler = {n for h in handled for n in _ast.walk(h)}

        for node in _ast.walk(fn):
            if (isinstance(node, _ast.Return) and node.value is not None
                    and node not in in_handler
                    and _constantish(node.value, bound)):
                seen[node.lineno] = (
                    f"{fn.name}() line {node.lineno}: discards the library's "
                    f"result and returns the constant "
                    f"`{_ast.unparse(node.value)}`")
    return [seen[k] for k in sorted(seen)]


#: Probe body: import the named modules and report their real public surface.
#: Run in a subprocess because importing a third-party package executes its
#: module-level code, which can be slow or have side effects.
_API_SURFACE_PROBE = """
import importlib, json, pkgutil, sys

def surface(name):
    try:
        mod = importlib.import_module(name)
    except BaseException:
        return None, None
    try:
        names = sorted(n for n in dir(mod) if not n.startswith("_"))
    except BaseException:
        names = []
    return mod, names

out = {}
for root in json.loads(sys.argv[1]):
    mod, names = surface(root)
    if mod is None:
        continue
    out[root] = names
    # One level of submodules. `amqtt` is a namespace package with no public
    # names of its own and everything the Connector needs lives in
    # `amqtt.client`, so probing only the root learns nothing.
    for info in pkgutil.iter_modules(getattr(mod, "__path__", []) or []):
        if info.name.startswith("_"):
            continue
        child = root + "." + info.name
        _, child_names = surface(child)
        if child_names is not None:
            out[child] = child_names
print(json.dumps(out))
"""


def target_api_surface(modules: list, python_executable: str = "",
                       timeout: int = 20) -> dict:
    """The REAL public names of the installed target modules.

    The Connector is asked to write glue for a library it has only ever seen
    in training data, and is given no information about the installed version
    at all. It invents names. Measured on B3 and D3, both reverted::

        from amqtt.client import ClientException   # does not exist
        amqtt.Client(...)                          # does not exist

    The real names were `amqtt.client.ClientError` and
    `amqtt.client.MQTTClient` -- one is a single word away from what the model
    wrote. The package is installed, so this is answerable by import in
    milliseconds and needs no model at all.

    This is the "library introspection" half of the mitigation the literature
    converges on (De-Hallucinator, arXiv:2401.01701, grounds a prediction by
    retrieving real API names and re-querying; MARIN constrains decoding to the
    project's valid APIs). Here it serves two purposes: the surface goes INTO
    the prompt so the model has the names in front of it, and it comes back
    OUT as a check on what the model actually wrote.

    Returns ``{module: [names]}``; a module that cannot be imported is absent
    rather than empty, so "could not check" is never confused with "has no
    such name".
    """
    import json as _json
    import subprocess as _sp
    import sys as _sys          # this module does not import sys at top level

    wanted = [m for m in dict.fromkeys(modules) if m]
    if not wanted:
        return {}
    try:
        proc = _sp.run([python_executable or _sys.executable, "-c",
                        _API_SURFACE_PROBE, _json.dumps(wanted)],
                       capture_output=True, text=True, timeout=timeout)
        return _json.loads(proc.stdout or "{}")
    except Exception:                                          # noqa: BLE001
        return {}


def _referenced_target_symbols(source: str, target_root: str) -> list:
    """Every (module, symbol) the patch takes from the target library."""
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return []
    root = (target_root or "").split("[")[0].strip()
    if not root:
        return []

    found: list = []
    aliases: dict = {}          # local name -> the module it refers to
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for a in node.names:
                if a.name == root or a.name.startswith(f"{root}."):
                    aliases[(a.asname or a.name).split(".")[0]] = a.name
        elif isinstance(node, _ast.ImportFrom) and node.module and not node.level:
            if node.module == root or node.module.startswith(f"{root}."):
                for a in node.names:
                    if a.name != "*":
                        found.append((node.module, a.name))
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Attribute) and isinstance(node.value, _ast.Name)
                and node.value.id in aliases):
            found.append((aliases[node.value.id], node.attr))
    return list(dict.fromkeys(found))


def hallucinated_target_symbols(source: str, target_root: str,
                                surface: dict) -> list[str]:
    """Names the patch takes from the target library that do not exist.

    Each report carries the closest REAL names, because "X does not exist" sends
    the model looking for a different spelling of X while "X does not exist; the
    module has ClientError, ClientConfig, ClientContext" hands it the answer.
    That is the iterative-grounding result from De-Hallucinator: augmenting the
    prompt with actual similar names is what fixes the prediction, not merely
    reporting the miss.

    A module missing from ``surface`` could not be imported, so nothing is
    reported for it -- an unverifiable symbol must not be called invented.
    """
    import difflib

    out: list[str] = []
    for module, symbol in _referenced_target_symbols(source, target_root):
        names = surface.get(module)
        if names is None or symbol in names:
            continue

        close = difflib.get_close_matches(symbol, names or [], n=3, cutoff=0.6)
        if not close:
            close = [n for n in (names or [])
                     if len(symbol) > 4 and symbol.lower()[:5] in n.lower()][:3]
        suggestions = [f"{module}.{n}" for n in close]

        # Look in the SIBLING modules too. `amqtt` is a namespace package with
        # no public names of its own, so `amqtt.Client` gets no suggestion from
        # its own module while the answer -- `amqtt.client.MQTTClient` -- is one
        # level down. Reporting "available: " with nothing after it is what the
        # first version of this did, and it is useless to the model.
        if not suggestions:
            for other, other_names in surface.items():
                if other == module:
                    continue
                for n in difflib.get_close_matches(symbol, other_names, n=2,
                                                   cutoff=0.6):
                    suggestions.append(f"{other}.{n}")
            suggestions = suggestions[:3]

        if suggestions:
            hint = f"; did you mean {', '.join(suggestions)}?"
        elif names:
            hint = f"; {module} has {', '.join(names[:8])}"
        else:
            hint = (f"; {module} exposes no public names of its own -- the "
                    f"symbol is probably in a submodule")
        out.append(f"`{module}.{symbol}` does not exist{hint}")
    return out


def repair_hallucinated_symbols(source: str, target_root: str,
                                surface: dict) -> tuple:
    """Rewrite invented API names to the real ones, where that is unambiguous.

    Detection alone was not enough. Grounding the prompt and reporting the miss
    both depend on the model acting on the feedback, and on the runs measured
    here it did not: B3 seeds 1 and 2 and D3 seed 2 each burned their repair
    budget and ended `migration_success=N` with the source untouched.

    This is the "deterministic rules to find AND FIX API and identifier-level
    conflicts" half of the AST-analysis approach (arXiv:2601.19106). Where the
    correct name is not in doubt, no model is needed to write it.

    The measured case, from B3 seed 1::

        from amqtt.client import MQTTClient, ClientException
        ...
        except ClientException as exc:

    ``MQTTClient`` is real. ``ClientException`` is not, and the module exports
    exactly one name close to it: ``ClientError``. The import and every
    reference to the bound name are rewritten together.

    THE RULE IS UNAMBIGUITY, and it is what keeps this safe:

      * the symbol must be genuinely absent from a module that was imported
        successfully -- an unreadable module yields no repair at all;
      * there must be EXACTLY ONE close match. ``ClientException`` -> one
        (``ClientError``); ``ConnectException`` -> one (``ConnectError``); but
        a bare ``Client`` matches ``MQTTClient``, ``ClientError`` and
        ``ClientConfig``, so it is left alone and reported instead;
      * the local name must not be bound by anything else in the file, or a
        rename would capture an unrelated identifier.

    Returns ``(repaired_source, [descriptions])``; an empty list means nothing
    was unambiguous enough to touch and the caller should fall back to telling
    the model.
    """
    import ast as _ast
    import difflib
    import re as _re

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return source, []

    root = (target_root or "").split("[")[0].strip()
    if not root or not surface:
        return source, []

    # Names bound by anything other than the import we are about to rewrite.
    # Renaming into one of these would capture an unrelated identifier.
    otherwise_bound: set = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef)):
            otherwise_bound.add(node.name)
        elif isinstance(node, _ast.Assign):
            otherwise_bound |= {t.id for t in node.targets
                                if isinstance(t, _ast.Name)}

    renames: dict = {}
    notes: list = []
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.ImportFrom) and node.module
                and not node.level):
            continue
        if not (node.module == root or node.module.startswith(f"{root}.")):
            continue
        names = surface.get(node.module)
        if names is None:
            continue                     # module unreadable: no evidence
        for alias in node.names:
            symbol = alias.name
            if symbol == "*" or symbol in names:
                continue
            close = difflib.get_close_matches(symbol, names, n=3, cutoff=0.6)
            if len(close) != 1:
                continue                 # ambiguous: leave it to the model
            replacement = close[0]
            local = alias.asname or symbol
            if alias.asname is None and local in otherwise_bound:
                continue
            renames[symbol] = replacement
            notes.append(f"{node.module}.{symbol} -> {replacement}")

    if not renames:
        return source, []

    repaired = source
    for old, new in renames.items():
        repaired = _re.sub(rf"\b{_re.escape(old)}\b", new, repaired)
    try:
        _ast.parse(repaired)
    except SyntaxError:
        return source, []                # never hand back something broken
    return repaired, notes


def _winner_import_root(winner: str) -> str:
    """The module name for a distribution name: `modbus-tk` -> `modbus_tk`."""
    return (winner or "").split("[")[0].strip().replace("-", "_")


def _format_api_surface(surface: dict, relevant_to: str = "",
                        per_module: int = 40, max_modules: int = 6) -> str:
    """Render the probed surface for a prompt, most relevant module first.

    SELECTIVE augmentation, not a dump. De-Hallucinator's mechanism is to use
    "the initial prediction(s) by the model to iteratively retrieve
    increasingly suitable API references" -- the relevance signal is what the
    model already said it intends to do, not the whole package.

    The first version of this function ignored that and listed every module
    alphabetically: for `amqtt` that is 14 modules and 283 names, led by
    `amqtt.adapters`, with `amqtt.client` -- the only one that matters, and the
    one holding both names the model got wrong -- buried in the middle. Burying
    the answer in noise is the failure mode the grounding exists to prevent.

    ``relevant_to`` is the Connector's own analysis output: the call sites it
    found and the wrappers it says it will write. Modules are ranked by how
    many of their names that text actually mentions, so the module the model is
    already reaching for comes first and the rest are trimmed.
    """
    import re as _re

    if not surface:
        return ""
    def _parts(identifier: str) -> set:
        """`MQTTClient` -> {mqttclient, mqtt, client}; `client_id` -> {client, id}.

        Without this the signal is lost exactly where it matters: the
        prediction says `MQTTClient` and `client_id`, neither of which yields
        the bare token `client`, so `amqtt.client` scored nothing and
        `amqtt.mqtt` won on generic protocol constants named `connect` and
        `publish`.
        """
        out = {identifier.lower()}
        for chunk in identifier.split("_"):
            for word in _re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+", chunk):
                if len(word) > 2:
                    out.add(word.lower())
        return out

    tokens: set = set()
    for raw in _re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", relevant_to or ""):
        tokens |= _parts(raw)

    # A token that merely echoes the PACKAGE name says nothing about which
    # submodule is wanted. `amqtt` yields the token `mqtt`, which then matched
    # the module `amqtt.mqtt` -- a packet-encoding module the prediction never
    # mentions -- and outranked `amqtt.client`, where both names the model got
    # wrong actually live. The package name is the one thing every candidate
    # shares, so it cannot discriminate between them.
    # Substring, not equality: the package is `amqtt` and the token is `mqtt`,
    # which `_parts` never separates because `amqtt` is a single lowercase run.
    # Equality filtering left `mqtt` in play, it matched the module
    # `amqtt.mqtt`, and that packet-encoding module outranked `amqtt.client`
    # on a tie-break by raw symbol count.
    root = (min(surface, key=len) if surface else "").lower()
    discriminating = {t for t in tokens
                      if root and t not in root and root not in t}

    def score(module: str, names: list) -> tuple:
        # A module the prediction NAMES outranks one that merely shares a
        # symbol: `amqtt.client` beats `amqtt.mqtt` even though the latter
        # exports `connect` and `publish`, because the prediction says
        # MQTTClient and client_id and never mentions the mqtt packet module.
        named = sum(1 for part in module.lower().split(".")
                    if part in discriminating)
        hits = sum(1 for n in names if _parts(n) & tokens)
        return (named, hits, -len(module))

    ranked = sorted(((m, n) for m, n in surface.items() if n),
                    key=lambda kv: score(*kv), reverse=True)
    lines = []
    for module, names in ranked[:max_modules]:
        ordered = (sorted(names, key=lambda n: (not (_parts(n) & tokens), n))
                   if tokens else names)
        shown = ", ".join(ordered[:per_module])
        if len(ordered) > per_module:
            shown += f", ... (+{len(ordered) - per_module} more)"
        lines.append(f"  {module}: {shown}")
    if len(ranked) > max_modules:
        lines.append(f"  (+{len(ranked) - max_modules} more submodule(s); "
                     f"open them if you need something not listed)")
    return "\n".join(lines)


def _validate_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def _foreign_candidate_mentions(payload: object, candidates: list[str]) -> list[str]:
    """Return candidate names improperly cited in Connector's LLM output.

    A Connector decision is only sound when its compatibility claim is about
    the package selected by AXIOM.  A frequent hallucination is to recognize
    that *another* candidate offers the source API, then mark the selected
    target as compatible.  The prompt tells the model not to do this; this
    guard makes the identity boundary enforceable before imports are changed.
    """
    fragments: list[str] = []
    evidence_fields = {"reason", "wrapper_summary", "remaining_incompatibilities"}

    def collect(value: object, *, is_evidence: bool = False) -> None:
        if isinstance(value, str):
            if is_evidence:
                fragments.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(item, is_evidence=is_evidence or key in evidence_fields)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, is_evidence=is_evidence)

    # Deliberately inspect compatibility explanations, not the literal source
    # call expression: application strings can legitimately mention another
    # library without being evidence for the migration decision.
    collect(payload)
    text = "\n".join(fragments).lower()
    mentioned: list[str] = []
    for candidate in candidates:
        name = str(candidate).strip()
        if not name:
            continue
        # A distribution name and its normal import-root spelling are both
        # meaningful evidence (e.g. ``some-package`` / ``some_package``).
        variants = {name.lower(), name.replace("-", "_").lower()}
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(variant)}(?![a-z0-9_])", text)
            for variant in variants
        ):
            mentioned.append(name)
    return mentioned


def _import_roots(source_code: str) -> set[str]:
    """Return top-level import roots without executing generated source."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _resolve_import_root(distribution: str, source_code: str,
                         declared: str = "") -> str:
    """Thin delegate to the shared resolver — see ast_rewrite.resolve_import_root.

    Kept as a name in this module so the call sites read locally, but the
    logic lives in one place: the identical guess was open-coded here and in
    verifier_agent, and fixing one without the other left a correct migration
    failing the patch-integrity check.
    """
    return resolve_import_root(distribution, source_code, declared)


def _remaining_source_imports(source_code: str, root: str) -> list[str]:
    """Import statements of ``root`` still present, as ``"line N: <stmt>"``.

    Runs AFTER the deterministic rewrite and the dead-import sweep, so anything
    it finds is an import the migration genuinely left LIVE — some code path
    still references the name. That is not a cosmetic leftover: the clone
    cannot run without the old distribution installed, which is precisely what
    the migration was asked to remove.

    ``ast.walk`` rather than ``tree.body`` because nine of the twelve
    evaluation programs import the migrated-away library inside a function.
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
        module = ""
        if isinstance(node, ast.Import):
            module = ",".join(a.name for a in node.names)
            hit = any(a.name.split(".")[0].casefold() == root for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            module = node.module
            hit = node.module.split(".")[0].casefold() == root
        else:
            continue
        if hit:
            lineno = getattr(node, "lineno", 0)
            text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else module
            found.append(f"line {lineno}: {text}")
    return found


def _unused_wrapper_names(source: str, wrapper_names: list[str]) -> list[str]:
    """Return wrapper function names that were defined but never called.

    ACI_EDIT_SYSTEM's step 2 instructs the model to do two things in one
    session: inject the wrapper AND rewrite the incompatible call site to use
    it. Only the first half is guaranteed by ``_run_aci_wrapper_injection``'s
    existing checks (syntax validity, top-level structure preserved) — a
    model can define a wrapper, forget to redirect the original call site to
    it, and still pass both of those. The result is a patch that looks
    complete but still calls the old API under the new import (e.g. a
    left-behind ``KafkaProducer(...)`` call site once the import becomes
    ``from confluent_kafka import KafkaProducer``, which doesn't exist).

    The model does not always return one identifier. Measured on a live D1 run,
    the analysis pass named its wrapper::

        "_axiom_modbus_tcp_client_wrapper / _AxiomModbusTkClientAdapter"

    -- a factory and the adapter class it builds, joined with " / ". That
    string is never an ``ast.Name``, so the guard reported it unused on every
    attempt and the Connector reverted its own correct edit five times in a
    row: same input, same output, five repair cycles and ~190k tokens spent
    reaching the identical result. A false positive here is far more damaging
    than a missed detection, because it discards a working adapter AND
    consumes the entire repair budget.

    So a name is resolved to the identifiers inside it. If any of those is
    actually defined in the file, the wrapper counts as wired in when at least
    one of them is referenced. A name with no defined identifier falls back to
    the original literal check.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    unused: list[str] = []
    for name in wrapper_names:
        if not name or name in referenced:
            continue
        parts = [p for p in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", name) if p in defined]
        # EVERY defined part must be referenced, not merely one of them. With
        # "any", a compound name passes whenever the adapter class is
        # instantiated inside its own factory -- while the factory itself is
        # never called from the call site, which is precisely the failure this
        # guard exists to catch.
        if parts and all(p in referenced for p in parts):
            continue
        unused.append(name)
    return unused



def _connector_edit_candidate_violation(
    before: str,
    after: str,
    *,
    original_import: str,
    foreign_candidates: list[str],
) -> tuple[str, str] | None:
    """Reject an edit that swaps in a candidate AXIOM did not select.

    Prompts are advisory.  This AST guard protects the ACI and legacy editing
    paths from inserting a different candidate (for example ``pycodestyle``)
    while AXIOM's decision and verification environment are for ``ruff``.

    Returns ``(message, failure_kind)``. ``failure_kind`` distinguishes two
    situations that used to be conflated under one label:

    - ``"generation"`` — a self-inflicted, repairable Connector mistake (it
      edited the original import line, which it does not own — that
      replacement is reserved for the deterministic post-processing rewrite
      a few dozen lines below, applied only once ``migration_feasible`` is
      True). The candidate itself was never disproven, so Verifier should
      retry the SAME candidate with feedback telling Connector to leave the
      import statement alone.
    - ``"candidate_identity"`` — Connector's edit actually imports or
      references a DIFFERENT, unselected candidate. That is real evidence
      the selected candidate cannot satisfy this call site, so Verifier
      should exclude it and let AXIOM re-score with it removed.

    Prior to this split both cases returned a bare string and the caller
    always treated it as ``candidate_identity`` — permanently vetoing a
    candidate (e.g. kafka -> confluent-kafka) whose only fault was that the
    ACI edit session touched the import while writing the wrapper, which the
    LLM will happily do unless told not to. See connector_agent()'s handling
    of this return value for where the two kinds now diverge.
    """
    try:
        before_tree = ast.parse(before)
        after_tree = ast.parse(after)
    except SyntaxError:
        return "connector edit could not be parsed for target-identity validation", "generation"

    before_roots = _import_roots(before)
    after_roots = _import_roots(after)
    before_names = {node.id for node in ast.walk(before_tree) if isinstance(node, ast.Name)}
    after_names = {node.id for node in ast.walk(after_tree) if isinstance(node, ast.Name)}
    original_root = original_import.split(".")[0]
    if original_root in before_roots and original_root not in after_roots:
        # Removing the source import is only an overreach while the file still
        # USES it. If the edit also rewrote every call site, so that nothing
        # the import bound is referenced any more, the migration is finished —
        # and the deterministic post-processing a few dozen lines below would
        # have deleted that import itself, as dead.
        #
        # Rejecting it anyway is expensive and self-defeating. On B2 the model
        # rewrote `_default_client` to call the wrapper and dropped the dead
        # `from pymodbus.client import ModbusTcpClient`; this guard reverted
        # all five injected wrappers, and the repair cycle that followed
        # rebuilt the same work — 12 LLM calls and $0.39 for a run that needed
        # about half of that. The edit being reverted was correct.
        #
        # So the veto now applies only to a LIVE removal. A removal that
        # leaves dangling references is still caught (the code would raise
        # NameError), and the checks below still run, so removing the source
        # import while smuggling in an unselected candidate is still rejected
        # as candidate_identity.
        orphaned = names_bound_from_root(before, original_root) & after_names
        if orphaned:
            return (
                f"connector edit removed source import {original_root!r} while injecting the wrapper, "
                f"but {', '.join(sorted(orphaned))} is still referenced; rewrite those call sites to use "
                "the wrapper, or leave the import statement unchanged",
                "generation",
            )

    foreign_roots = {
        str(candidate).replace("-", "_").split(".")[0]
        for candidate in foreign_candidates if str(candidate).strip()
    }
    newly_imported = sorted((after_roots - before_roots).intersection(foreign_roots))
    if newly_imported:
        return (
            f"connector edit introduced unselected candidate import(s): {', '.join(newly_imported)}",
            "candidate_identity",
        )

    newly_referenced = sorted((after_names - before_names).intersection(foreign_roots))
    if newly_referenced:
        return (
            f"connector edit referenced unselected candidate(s): {', '.join(newly_referenced)}",
            "candidate_identity",
        )
    return None


def _connector_failure_kind(error: object) -> str:
    """Separate retryable bad generation from an unavailable LLM service.

    A malformed adapter can benefit from concrete Verifier feedback.  A bad
    API key, rate limit, DNS outage, or provider timeout cannot; retrying it
    five times merely hides the real cause and makes an evaluation look stuck.
    """
    text = str(error).casefold()
    provider_markers = (
        "api key", "authentication", "unauthorized", "forbidden", "permission",
        "rate limit", "too many requests", "429", "503", "service unavailable",
        "connection", "dns", "network", "timed out", "timeout",
        "nvidia", "openai", "anthropic",
    )
    return "provider" if any(marker in text for marker in provider_markers) else "generation"


# ── Main agent ────────────────────────────────────────────────────────────────

def connector_agent(state: dict) -> dict:
    """
    LangGraph node: for each replacement decision, analyze call sites,
    generate wrapper functions, and inject them inline into the source.
    """
    from smart_loader.core.state import AgentEvent, LoadDecision, EquivalenceGroup

    console.rule("[bold blue]🔌 Connector Agent — Inline I/O Transformer")

    decisions:    list[LoadDecision]    = state.get("decisions", [])
    groups:       list[EquivalenceGroup] = state.get("equivalence_groups", [])
    source_code:  str                   = state.get("source_code", "")
    patched_code: str                   = state.get("patched_code", source_code)
    provider:     str                   = state.get("llm_provider", "ollama")
    model:        str                   = state.get("model", "qwen3-coder-next")
    session_id:   str                   = state.get("_session_id", "unknown")
    source_file:  str                   = state.get("source_file", "")
    verifier_error: str                 = state.get("_last_connector_error", "")
    verifier_feedback: dict             = state.get("_last_verifier_feedback", {})
    last_valid_connector_code: str      = state.get("connector_last_valid_code", "")
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="connector", event="started"))
    if verifier_error:
        cycle = verifier_feedback.get("cycle")
        prefix = f"repair cycle {cycle}: " if cycle else ""
        console.print(f"[yellow]↩ Retrying with verifier feedback ({prefix}{verifier_error[:300]})[/yellow]")

    replacements = [d for d in decisions if d.winner != d.original]

    if not replacements:
        console.print("[green]No package replacements — no connectors needed.[/green]")
        return {
            "connectors":   {},
            "patched_code": patched_code,
            "agent_trace":  trace,
            "messages":     [AIMessage(content="Connector: no replacements.")],
        }

    # On a verifier retry, preserve the last syntax-valid patch.  Starting
    # from source_code here used to discard corrections from earlier retries
    # (for example, a repaired import), causing the connector to regress.
    working_source = last_valid_connector_code if verifier_error and last_valid_connector_code else source_code
    analysis_source = source_code

    # Apply only deterministic, semantics-preserving recipes before asking an
    # LLM to invent a wrapper.  ``attr.ib()`` is one such special case: a
    # wrapper around ``dataclasses.field()`` loses the class-body context that
    # dataclasses requires.  The narrow helper declines any non-trivial attrs
    # construct, which then continues through the ordinary agentic path.
    connectors: dict[str, dict] = {}
    pending_replacements = []
    for decision in replacements:
        original_import = getattr(decision, "original_import", decision.original.replace("-", "_"))
        winner_import = getattr(decision, "winner_import", decision.winner.replace("-", "_"))
        native_rewrite = (
            rewrite_simple_attrs_to_dataclass(working_source)
            if original_import == "attr" and winner_import == "dataclasses"
            else None
        )
        if native_rewrite is None:
            pending_replacements.append(decision)
            continue

        working_source, edits = native_rewrite
        console.print(
            f"\n[bold]Applying deterministic native migration:[/bold] "
            f"[red]{decision.original}[/red] → [green]{decision.winner}[/green]"
        )
        for edit in edits:
            console.print(f"  [green]✓[/green] {edit}")
        connectors[decision.original] = {
            "functions": [],
            "call_rewrites": edits,
            "compatible": True,
            "migration_feasible": True,
            "deterministic_rewrite": "simple_attrs_dataclass",
            "remaining_incompatibilities": [],
        }

    llm = None
    if pending_replacements:
        try:
            llm = _get_llm(provider, model)
        except Exception as exc:
            failure_kind = _connector_failure_kind(exc)
            detail = f"Connector could not initialize {provider}/{model}: {exc}"
            console.print(f"[red]{detail}[/red]")
            for decision in pending_replacements:
                connectors[decision.original] = {
                    "functions": [], "call_rewrites": [], "compatible": False,
                    "migration_feasible": None, "failure_kind": failure_kind,
                    "remaining_incompatibilities": [detail],
                }
            trace.append(AgentEvent(
                stage="connector", event="provider_unavailable" if failure_kind == "provider" else "generation_failed",
                detail={"error": detail},
            ))
            return {
                "connectors": connectors, "patched_code": working_source,
                "agent_trace": trace,
                "messages": [AIMessage(content=detail)],
            }

    for decision in pending_replacements:
        original = decision.original
        winner   = decision.winner
        role     = decision.role
        # Decisions are scored using PyPI distribution names, but call-site
        # extraction must use the Python import module (e.g. slackclient
        # provides ``slack``).  AXIOM attaches these names for eval cases
        # where the benchmark provides the distribution/import mapping.
        original_import = getattr(decision, "original_import", original.replace("-", "_"))
        winner_import = getattr(decision, "winner_import", winner.replace("-", "_"))

        # Get pitfalls from the equivalence group
        group = next((g for g in groups if g.role == decision.role), None)
        pitfalls_text = "\n".join(group.pitfalls) if group and group.pitfalls else decision.rationale[:300]
        foreign_candidates = [
            candidate for candidate in (group.candidates if group else [])
            if candidate not in {original, winner}
        ]

        console.print(
            f"\n[bold]Analyzing:[/bold] [red]{original}[/red] → [green]{winner}[/green]"
        )

        # ── Extract every call site of the original package ────────────────────
        # Original source remains the reliable way to identify the migration's
        # call sites after imports/wrappers have already been transformed.
        call_sites = _extract_call_sites(analysis_source, original_import)

        if not call_sites:
            console.print(f"  [dim]No call sites found for {original_import} (distribution: {original}) — skipping.[/dim]")
            # An import-only migration can still be valid.  Always attempt the
            # deterministic import rewrite below; if the expected import is
            # not present, Verifier will reject it as an unpatched decision
            # instead of allowing a false success.
            connectors[original] = {
                "functions": [], "call_rewrites": [], "compatible": True,
                "migration_feasible": True, "no_call_sites": True,
                "remaining_incompatibilities": [],
            }
            continue

        call_sites_str = "\n".join(
            f"  Line {cs['line']}: {cs['source_line']}"
            for cs in call_sites
        )
        console.print(f"  Found [cyan]{len(call_sites)}[/cyan] call site(s):")
        for cs in call_sites:
            console.print(f"    [dim]L{cs['line']}:[/dim] {cs['source_line']}")

        # ── LLM: analyze + generate inline wrappers + rewrite source ──────────
        t0 = time.time()
        try:
            resp = invoke_with_timeout(llm, [
                SystemMessage(content=ANALYSIS_SYSTEM),
                HumanMessage(content=ANALYSIS_HUMAN.format(
                    original=original,
                    winner=winner,
                    winner_import=winner_import,
                    role=role,
                    other_candidates=", ".join(foreign_candidates) or "(none)",
                    pitfalls=pitfalls_text,
                    verifier_feedback=(
                        VERIFIER_FEEDBACK_BLOCK.format(error=verifier_error)
                        if verifier_error else ""
                    ),
                    call_sites=call_sites_str,
                    source_code=working_source,
                )),
            ])
            latency = (time.time() - t0) * 1000

            # Token tracking
            try:
                from smart_loader.core.token_tracker import record_usage
                usage = getattr(resp, "usage_metadata", None) or {}
                record_usage(
                    session_id=session_id, agent="connector",
                    model=model, provider=provider,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    latency_ms=latency,
                )
                console.print(
                    f"  [dim]Tokens — in:{usage.get('input_tokens',0):,} "
                    f"out:{usage.get('output_tokens',0):,} "
                    f"latency:{latency:.0f}ms[/dim]"
                )
            except Exception:
                pass

            # ── Parse JSON response ────────────────────────────────────────────
            result = _parse_json_response(coerce_llm_text(resp.content))

            if not result:
                console.print(f"[yellow]  ⚠ Could not parse JSON response — skipping {original}[/yellow]")
                console.print(f"  [dim]Raw response (first 300 chars): {coerce_llm_text(resp.content)[:300]}[/dim]")
                # A malformed model response is a Connector generation
                # failure, not evidence that the selected target package is
                # structurally impossible.  Verifier routes this back here
                # with a bounded concrete-repair cycle.
                connectors[original] = {
                    "functions": [], "call_rewrites": [], "compatible": False,
                    "migration_feasible": None, "failure_kind": "generation",
                    "remaining_incompatibilities": [
                        "connector analysis response was not parseable JSON"
                    ],
                }
                continue

            analysis    = result.get("analysis", [])

            # Do not let a well-formed but cross-candidate answer turn into a
            # patch.  For example, “pycodestyle has noqa” is irrelevant when
            # AXIOM selected ``ruff``.  Leave the source unchanged so Verifier
            # can route the concrete generation error back to Connector.
            foreign_mentions = _foreign_candidate_mentions(result, foreign_candidates)
            if foreign_mentions:
                detail = (
                    "connector analysis cited unselected candidate(s) "
                    f"{', '.join(foreign_mentions)} while the locked target is "
                    f"{winner} (import {winner_import})"
                )
                console.print(f"[yellow]  ⚠ {detail}; rejecting this analysis.[/yellow]")
                connectors[original] = {
                    "functions": [], "call_rewrites": [], "compatible": False,
                    "migration_feasible": False, "failure_kind": "candidate_identity",
                    "remaining_incompatibilities": [detail],
                }
                continue

            # Show analysis summary
            console.print(f"\n  [bold]Compatibility analysis:[/bold]")
            for item in analysis:
                icon = "[green]✓[/green]" if item.get("compatible") else "[red]✗[/red]"
                console.print(
                    f"    {icon} L{item.get('line','?')}: {item.get('call','?')[:60]}"
                    f"  — {item.get('reason','')[:80]}"
                )

            migration_feasible = result.get("migration_feasible", True)
            remaining_incompat = result.get("remaining_incompatibilities", [])
            if remaining_incompat:
                console.print(f"  [yellow]⚠ Remaining incompatibilities noted by connector LLM:[/yellow]")
                for note in remaining_incompat:
                    console.print(f"    [yellow]- {note}[/yellow]")

            wrapper_needs = [
                {
                    "line": item.get("line"), "call": item.get("call"),
                    "wrapper_name": item.get("wrapper_name"),
                    "wrapper_summary": item.get("wrapper_summary", ""),
                }
                for item in analysis
                if not item.get("compatible") and item.get("wrapper_name")
            ]

            if not wrapper_needs:
                console.print(f"  [green]✓ All {original} call sites are fully compatible — no wrappers needed[/green]")
                connectors[original] = {
                    "functions": [], "call_rewrites": [], "compatible": True,
                    "migration_feasible": migration_feasible,
                    "remaining_incompatibilities": remaining_incompat,
                }
                continue

            console.print(f"\n  [bold]{len(wrapper_needs)} call site(s) need a wrapper:[/bold]")
            for w in wrapper_needs:
                console.print(f"    [cyan]{w['wrapper_name']}[/cyan]  L{w['line']}: {w.get('wrapper_summary','')[:70]}")

            # ── PRIMARY: multi-turn ACI edit loop (open/goto/search/edit/submit) ──
            #
            # ``enable_aci=False`` skips straight to the one-shot path. The ACI
            # session is by far the most expensive thing in the pipeline -- 13k
            # to 138k input tokens per attempt against roughly 2k for one-shot
            # generation -- and it frequently fails its own integrity check and
            # falls back anyway. Whether the multi-turn editor earns that is an
            # empirical question, and it could not be asked while the only way
            # to reach the one-shot path was for ACI to fail first.
            edit_input_source = working_source
            if not state.get("enable_aci", True):
                console.print(f"  [dim]ACI disabled; using one-shot wrapper "
                              f"generation for {original}.[/dim]")
                wrapper_names, aci_ok = [], False
            else:
                console.print(f"  [yellow]Opening ACI edit session for {original} → {winner}...[/yellow]")
                working_source, wrapper_names, aci_ok = _run_aci_wrapper_injection(
                    llm, working_source, original, winner, role, pitfalls_text,
                    wrapper_needs, verifier_error, source_file, session_id, provider, model,
                )

            if aci_ok:
                console.print(f"  [green]✓ ACI edit session complete — {len(wrapper_needs)} wrapper(s) injected.[/green]")
                connectors[original] = {
                    "functions":     [w["wrapper_name"] for w in wrapper_needs],
                    "call_rewrites": [{"line": w["line"], "call": w["call"], "wrapper": w["wrapper_name"]} for w in wrapper_needs],
                    "compatible":    False,
                    "migration_feasible": migration_feasible,
                    "remaining_incompatibilities": remaining_incompat,
                }
            else:
                # ── FALLBACK: legacy one-shot JSON wrapper generation + AST/string match ──
                if state.get("enable_aci", True):
                    console.print(f"  [yellow]Falling back to one-shot wrapper generation for {original}...[/yellow]")
                wrappers = _legacy_generate_wrappers(llm, working_source, original, winner, wrapper_needs)
                if not wrappers:
                    console.print(f"  [red]  Legacy fallback also produced no wrappers — {original} left unpatched.[/red]")
                    connectors[original] = {
                        "functions": [], "call_rewrites": [], "compatible": False,
                        "migration_feasible": None, "failure_kind": "generation",
                        "remaining_incompatibilities": remaining_incompat + ["wrapper generation failed (both ACI and fallback paths)"],
                    }
                    continue

                for w in wrappers:
                    console.print(f"    [cyan]{w['name']}[/cyan]  replaces: {w.get('replaces','?')[:60]}")
                    console.print(
                        Panel(
                            Syntax(w["code"], "python", theme="monokai", line_numbers=True),
                            title=f"[cyan]{w['name']}[/cyan]", border_style="blue",
                        )
                    )

                working_source = _inject_wrappers(working_source, original, winner, wrappers)
                connectors[original] = {
                    "functions":     [w["name"] for w in wrappers],
                    "call_rewrites": [{"replaces": w.get("replaces"), "with": w.get("with")} for w in wrappers],
                    "compatible":    False,
                    "migration_feasible": migration_feasible,
                    "remaining_incompatibilities": remaining_incompat,
                }

            # A wrapper that got defined but never wired into the call site
            # it was supposed to replace is worse than no wrapper at all: the
            # patch parses, passes the top-level-structure check, and still
            # calls the old (now nonexistent-under-the-new-import) API. Catch
            # it deterministically rather than trusting the edit session
            # followed both halves of its instructions.
            # Repair scope before judging. A wrapper whose import landed in the
            # wrong function is a fixable edit, not a failed one, and the guards
            # below cannot tell the two apart -- they would revert a correct
            # adapter and hand back a byte-identical file.
            working_source, import_repairs = _bind_wrapper_imports(
                working_source, connectors[original]["functions"])
            # ...and the same defect in a method the wrapper list does not
            # cover, using the import statement the file already contains.
            # D1 seeds 1 and 2 both died on `cst` bound in one method of a
            # generated adapter class and used in the next.
            working_source, scope_repairs = replicate_lazy_imports(working_source)
            for repair in import_repairs + scope_repairs:
                console.print(f"[dim]  · connector scope repair — {repair}[/dim]")

            unused = _unused_wrapper_names(working_source, connectors[original]["functions"])
            if unused:
                detail = (
                    f"wrapper(s) {', '.join(unused)} were defined but never called — the "
                    "call site(s) they were meant to replace are still calling the old API "
                    "directly instead of going through the wrapper"
                )
                console.print(f"[yellow]  ⚠ {detail}; reverting connector edit.[/yellow]")
                working_source = edit_input_source
                connectors[original] = {
                    "functions": [], "call_rewrites": [], "compatible": False,
                    "migration_feasible": None, "failure_kind": "generation",
                    "remaining_incompatibilities": [detail],
                }
                continue

            violation = _connector_edit_candidate_violation(
                edit_input_source,
                working_source,
                original_import=original_import,
                foreign_candidates=foreign_candidates,
            )
            if violation:
                identity_error, violation_kind = violation
                console.print(f"[yellow]  ⚠ {identity_error}; reverting connector edit.[/yellow]")
                working_source = edit_input_source
                # "generation" (e.g. the edit touched the import it doesn't
                # own) is repairable — migration_feasible=None routes Verifier
                # back to retry this SAME candidate with feedback, same as the
                # legacy-fallback and top-level-definitions failure paths
                # below. "candidate_identity" is a proven decision-level
                # incompatibility — keep vetoing that candidate outright.
                connectors[original] = {
                    "functions": [], "call_rewrites": [], "compatible": False,
                    "migration_feasible": None if violation_kind == "generation" else False,
                    "failure_kind": violation_kind,
                    "remaining_incompatibilities": [identity_error],
                }
                continue

        except Exception as e:
            import traceback
            console.print(f"[red]  Connector failed for {original}: {e}[/red]")
            console.print(f"[dim]{traceback.format_exc()[:400]}[/dim]")
            failure_kind = _connector_failure_kind(e)
            connectors[original] = {
                "functions": [], "call_rewrites": [], "compatible": False,
                "migration_feasible": None, "failure_kind": failure_kind,
                "remaining_incompatibilities": [f"connector exception: {e}"],
            }

    # Every accepted migration must remove the source import, including
    # wrapper migrations.  Previously only ``compatible`` paths rewrote the
    # import, allowing a generated wrapper to leave the legacy distribution in
    # place.  The wrapper is supposed to bridge the *target* API, so changing
    # the import is a required part of a completed migration.
    import_replacements = {
        # Resolved against the file's real imports rather than guessed from
        # the distribution name -- see _resolve_import_root. The declared
        # value is kept as the fallback, so this can only ever narrow a guess
        # to a fact, never invent one.
        _resolve_import_root(
            d.original, source_code,
            declared=getattr(d, "original_import", "")):
        getattr(d, "winner_import", d.winner.replace("-", "_"))
        for d in replacements
        if d.original in connectors
        # ``None`` means Connector failed to generate an adapter (malformed
        # response, failed ACI/legacy edit, or provider error).  Rewriting
        # only the import in that state can make a wrapper migration look
        # superficially clean to import/static checks, even though the
        # required call-site transformation was never produced.  Only an
        # explicit True is permission to modify source; every other state
        # leaves a no-op that Verifier routes back to Connector with concrete
        # evidence (or stops clearly for a provider failure).
        and connectors[d.original].get("migration_feasible") is True
    }
    if import_replacements:
        console.print(f"\n[bold]Applying migration import rewrites:[/bold] {import_replacements}")
        try:
            working_source = apply_import_replacements(working_source, import_replacements)
        except Exception as e:
            console.print(f"[yellow]Drop-in import rewrite failed: {e}[/yellow]")

        # ...and then sweep up anything the rewrite did not reach. Removing the
        # dependency is the migration; leaving a dead ``import paho.mqtt.client
        # as mqtt`` behind means the old package must still be installed for
        # the clone to run, and the run scores lib_swapped=N despite the
        # adapter being correct.
        # The sweep is keyed on the SAME resolved roots as the rewrite above.
        # Keying it on the guessed distribution name is what let a dead
        # ``import paho.mqtt.client as mqtt`` survive a completed migration.
        source_roots = {
            root.split(".")[0].casefold()
            for root in import_replacements
            if root
        }
        if source_roots:
            working_source, dropped = _drop_unused_source_imports(
                working_source, source_roots)
            for entry in dropped:
                console.print(f"[dim]  · removed now-unused source import — {entry}[/dim]")

    # Point every RECORDED call site at its wrapper, deterministically.
    #
    # This runs before the sweep below, and it is what makes the sweep able to
    # do anything: an import can only be removed once nothing references it,
    # and what still references it is a call site the editing model did not
    # rewrite. On B2 the analysis named the site exactly (L14, inside
    # ``_default_client``) and the ACI session edited line 69 instead, leaving
    # an orphaned factory still constructing pymodbus.
    #
    # The connector already holds the line, the call text and the wrapper
    # name, so this needs no model at all. Matching is exact and arguments are
    # passed through verbatim -- see apply_call_site_rewrites for why a looser
    # rule would be dangerous on a corpus whose tests inject their clients.
    for decision in replacements:
        entry = connectors.get(decision.original) or {}
        needs = entry.get("call_rewrites") or []
        if not needs:
            continue
        root = _resolve_import_root(
            decision.original, source_code,
            declared=getattr(decision, "original_import", ""))
        working_source, applied = apply_call_site_rewrites(
            working_source, needs, root)
        for entry_text in applied:
            console.print(f"[dim]  · rewrote call site — {entry_text}[/dim]")

    # Sweep dead source imports even when the import REWRITE above did not
    # run. That block is gated on ``migration_feasible is True``, and the gate
    # is right for rewriting an import — a half-generated adapter must not be
    # made to look clean. But it is wrong for REMOVING an import that nothing
    # references any more, and the two were bundled together.
    #
    # The observed consequence: B3 and D3 report remaining incompatibilities
    # (aiomqtt/amqtt are async, the call chain is sync), so the block is
    # skipped — yet the ACI edit had already rewritten the call site, leaving
    #
    #     def _default_client(cfg):
    #         import paho.mqtt.client as mqtt    # nothing uses `mqtt` now
    #         client = _axiom_mqtt_client_factory_wrapper(cfg)
    #
    # The migration is done and the dependency still declared, so Verifier
    # correctly reports "No import binding for selected source 'paho'
    # changed" and the row scores lib_swapped=N.
    #
    # Removing an import whose bound names are unreferenced cannot change
    # behaviour — that is the whole precondition — so it is safe on every
    # path, including the ones where the adapter is incomplete.
    for decision in replacements:
        root = _resolve_import_root(
            decision.original, source_code,
            declared=getattr(decision, "original_import", ""))
        if not root:
            continue
        working_source, dropped = _drop_unused_source_imports(
            working_source, {root.split(".")[0].casefold()})
        for entry in dropped:
            console.print(f"[dim]  · removed dead source import — {entry}[/dim]")

    # Scope repair, LAST, on the finished patch.
    #
    # Placed here after two wrong guesses, both of which produced a fix that
    # provably worked on the failing source and never ran. It was first called
    # on `working_source` in the main flow, before the ACI session; then on
    # `final_source` inside the session. Both are too early for the same
    # reason: `import modbus_tk.defines as cst` is not in the file yet. It
    # arrives with `apply_import_replacements` above, so a repair that runs
    # before that finds nothing to replicate and reports nothing -- which is
    # exactly what four D1 runs showed, all failing on `Name "cst" is not
    # defined` with zero "scope repair" lines in their logs.
    #
    # Everything that can introduce or move an import has now run: the ACI
    # edit, the import rewrite, the dead-import sweep and the call-site
    # rewrites. This is the last point at which the patch changes.
    working_source, scope_fixes = replicate_lazy_imports(working_source)
    for fix in scope_fixes:
        console.print(f"[dim]  · connector scope repair — {fix}[/dim]")

    # POST-CONDITION: the old distribution must no longer be imported anywhere.
    #
    # The prompt asks for this and the sweep above clears the easy case, but a
    # prompt is a request and a sweep only removes imports nothing references.
    # Neither catches the failure actually observed: the model rewrites the
    # class's call site to use the wrapper and leaves the default-factory
    # helper still constructing the old library, so the import stays LIVE --
    #
    #     def _default_client(host, port=502):
    #         from pymodbus.client import ModbusTcpClient   # still used below
    #         client = ModbusTcpClient(host, port=port)
    #
    # -- an orphaned function that keeps the dependency alive. Silently
    # accepting that is how six Proteus rows shipped a "migration" whose clone
    # still needs the package it was told to remove.
    #
    # So it is enforced rather than requested. A survivor marks the adapter
    # incomplete with the offending line attached, and Verifier routes it back
    # to Connector as a repair cycle with that evidence -- the same mechanism
    # any other incomplete patch goes through. The patch is NOT reverted: it is
    # closer to correct than the original, and the retry edits from there.
    for decision in replacements:
        if decision.original not in connectors:
            continue
        # Never overwrite a diagnosis the connector already made. A rejected
        # analysis is recorded as ``candidate_identity``, and a surviving
        # source import is an inevitable CONSEQUENCE of that rejection, not a
        # separate defect — relabelling it ``generation`` would send the
        # repair cycle after the wrong problem and lose the real reason.
        if connectors[decision.original].get("failure_kind"):
            continue
        root = _resolve_import_root(
            decision.original, source_code,
            declared=getattr(decision, "original_import", ""))
        survivors = _remaining_source_imports(working_source, root)
        if not survivors:
            continue
        console.print(
            f"[red]  Migration incomplete: '{decision.original}' is still "
            f"imported and in use after the edit — {'; '.join(survivors)}[/red]")
        connectors[decision.original]["migration_feasible"] = None
        connectors[decision.original]["failure_kind"] = "generation"
        connectors[decision.original].setdefault(
            "remaining_incompatibilities", []).append(
            f"'{decision.original}' is still imported and used after the "
            f"migration ({'; '.join(survivors)}). Every call site must be "
            f"rewritten to use the adapter -- including default-factory "
            f"helpers the analysis did not list -- so that no code path "
            f"references {decision.original} and its import becomes dead.")

    # A tool-calling model must never be allowed to turn a complete source
    # file into an empty/minimal one.  Keep the original so Verifier can
    # honestly reject the unpatched decision rather than smoke-testing a
    # deleted program.
    if not _preserves_top_level_definitions(source_code, working_source):
        console.print("[red]Connector edit removed source structure — reverting this connector output.[/red]")
        working_source = source_code
        for connector in connectors.values():
            # The selected package was not disproven; Connector produced an
            # invalid edit.  Keep this repairable rather than asking AXIOM to
            # blacklist the candidate prematurely.
            connector["migration_feasible"] = None
            connector["failure_kind"] = "generation"
            connector.setdefault("remaining_incompatibilities", []).append(
                "connector edit removed required top-level source definitions"
            )

    # NOTE: telemetry save is deferred to verifier_agent now — connector's
    # output can still be retried (wrapper_fixable loop) or reverted
    # (decision_level loop / exhausted retries) after this node returns, so
    # saving here would persist a possibly-stale patch. verifier_agent saves
    # once on its "proceed" route, whether that's a clean pass or a revert.

    # ── Show final patched source ──────────────────────────────────────────────
    if working_source != source_code:
        console.print(
            Panel(
                Syntax(working_source, "python", theme="monokai", line_numbers=True),
                title="[bold cyan]Patched Source (with inline adapters)[/bold cyan]",
                border_style="cyan",
            )
        )

    trace.append(AgentEvent(
        stage="connector", event="completed",
        detail={"connectors_generated": list(connectors.keys())},
    ))

    console.print(
        f"\n[bold blue]✓ Connector Agent complete — "
        f"{sum(1 for c in connectors.values() if not c.get('compatible'))} "
        f"package(s) needed adapters[/bold blue]\n"
    )

    result = {
        "connectors":   connectors,
        "patched_code": working_source,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Connector: {len(connectors)} package(s) analyzed, "
                    + ", ".join(
                        f"{o}({'compatible' if c.get('compatible') else str(len(c.get('functions',[]))) + ' wrappers'})"
                        for o, c in connectors.items()
                    )
        )],
    }
    if _validate_syntax(working_source)[0]:
        result["connector_last_valid_code"] = working_source
    return result


# ── ACI-based wrapper injection (primary path) ───────────────────────────────────

def _run_aci_wrapper_injection(
    llm,
    working_source: str,
    original: str,
    winner: str,
    role: str,
    pitfalls_text: str,
    wrapper_needs: list[dict],       # from Phase A's analysis: [{line, call, wrapper_name, wrapper_summary}]
    verifier_error: str,
    source_file: str,
    session_id: str,
    provider: str,
    model: str,
) -> tuple[str, list[str], bool]:
    """
    Runs the multi-turn ACI edit loop (see core/aci_tools.py) to inject
    wrapper(s) and rewrite the affected call sites. Returns
    (patched_source, wrapper_names_seen_in_edit_log, success).

    success=False on ANY exception (including the tool-calling bind failing
    outright for a provider that doesn't support it) — the caller falls back
    to the legacy one-shot JSON approach in that case. This function never
    raises.
    """
    from smart_loader.core.aci_tools import ACISession, run_aci_edit_session

    session = ACISession(working_source, filename=source_file or "<source>")
    needs_str = "\n".join(
        f"  L{w.get('line','?')}: {w.get('call','?')[:70]}  "
        f"→ wrapper `{w.get('wrapper_name','?')}`: {w.get('wrapper_summary','')}"
        for w in wrapper_needs
    ) or "  (none listed — inspect the file yourself to find the incompatible call sites)"

    # One retry, and only for a blanket `except` that swallows. The prompt
    # forbids it in two places and the model still writes it on some runs --
    # measured on consecutive D1 runs, one wrote `except mt_exc.ModbusError`
    # and the next wrote `except Exception` twice. Handing the offending lines
    # back is what turns that coin flip into a fix: the session has the target
    # library in front of it and got the narrow handler right unaided once.
    feedback = verifier_error
    attempt = 0
    surface_text = None          # probed once, reused across repair attempts
    _surface_cache: dict = {}
    try:
        while True:
            attempt += 1
            # Grounding: read the installed target's real surface once and put
            # it in front of the model. Measured on B3 and D3, which invented
            # `amqtt.client.ClientException` and `amqtt.Client`; the real names
            # are `ClientError` and `MQTTClient`, both present in the package.
            if surface_text is None:
                _surface_cache = target_api_surface([_winner_import_root(winner)])
                # The relevance signal is the Connector's own analysis: the
                # call sites it found and the wrappers it intends to write.
                surface_text = _format_api_surface(_surface_cache,
                                                   relevant_to=needs_str)
            session = ACISession(working_source,
                                 filename=source_file or "<source>")
            final_source, transcript, tokens = run_aci_edit_session(
                llm, session,
                system_prompt=ACI_EDIT_SYSTEM.format(original=original, winner=winner),
                task_prompt=ACI_EDIT_TASK.format(
                    original=original, winner=winner, role=role,
                    api_surface=(API_SURFACE_BLOCK.format(surface=surface_text)
                                 + "\n" if surface_text else ""),
                    wrapper_needs=needs_str,
                    verifier_feedback=(VERIFIER_FEEDBACK_BLOCK.format(error=feedback)
                                       if feedback else ""),
                    pitfalls=pitfalls_text,
                ),
                max_turns=8, session_id=session_id, agent_name="connector",
                model=model, provider=provider, source_file=source_file,
            )
            # Repair invented API names in place, BEFORE anything else looks
            # at the patch. Where the correct name is not in doubt this needs
            # no model and no retry: the alternative measured on B3 seeds 1
            # and 2 and D3 seed 2 was the repair budget draining and the whole
            # migration reverting, three runs ending migration_success=N over
            # one wrong identifier.
            if session.edit_count:
                if surface_text is None:
                    _surface_cache = target_api_surface(
                        [_winner_import_root(winner)])
                    surface_text = _format_api_surface(_surface_cache,
                                                       relevant_to=needs_str)
                # Scope repair FIRST, on the ACI session's own output. It was
                # previously called only on `working_source` in the main flow
                # -- before the edit session produces the patch that actually
                # ships -- so on three D1 runs it repaired an earlier draft and
                # never saw the `cst` the final wrappers used. All three failed
                # on `Name "cst" is not defined` with the fix sitting unused.
                final_source, scope_fixes = replicate_lazy_imports(final_source)
                for fix in scope_fixes:
                    console.print(f"    [dim]· scope repair — {fix}[/dim]")

                repaired, fixes = repair_hallucinated_symbols(
                    final_source, _winner_import_root(winner), _surface_cache)
                if fixes:
                    console.print("  [green]  corrected invented API name(s) "
                                  "against the installed package[/green]")
                    for fix in fixes:
                        console.print(f"    [dim]· {fix}[/dim]")
                    final_source = repaired

            if attempt >= 2 or session.edit_count == 0:
                break
            names = [w.get("wrapper_name") for w in wrapper_needs
                     if w.get("wrapper_name")]
            # Two opposite ways to get the error contract wrong: a handler that
            # catches everything and swallows it, and no handler at all because
            # the library's own object was handed straight back. Both are
            # reported in one pass so a second defect does not cost a third
            # edit session, which the attempt cap would refuse anyway.
            swallows = _blanket_except_sites(final_source, names)
            raw_clients = _raw_client_returns(final_source, winner, names)
            fabricated = _fabricated_status_returns(final_source, names)
            # Checked against the INSTALLED package, not the model's memory of
            # it. This is the cheapest of the four and the most damaging when
            # it is missed: an invented name survives the project's own tests
            # (they inject a fake) and only surfaces as ImportError when the
            # real library loads, by which point the Verifier reverts the whole
            # migration. B3 and D3 both died this way.
            invented = hallucinated_target_symbols(
                final_source, _winner_import_root(winner),
                _surface_cache)
            if not swallows and not raw_clients and not fabricated and not invented:
                break
            notes = []
            if swallows:
                console.print("  [yellow]  wrapper swallows every exception; "
                              "asking for a narrower handler[/yellow]")
                for site in swallows:
                    console.print(f"    [dim]· {site}[/dim]")
                notes.append(
                    "Your previous edit caught every exception and returned "
                    "instead of letting it reach the caller:\n  "
                    + "\n  ".join(swallows)
                    + f"\n\nCatch ONLY the specific exception {winner} raises for "
                      f"the failure {original} reported at that call site, and "
                      f"import that class by name. Everything else -- connection "
                      f"errors, timeouts, programming errors -- must propagate, "
                      f"because {original} propagated them. If {original} reported "
                      f"that failure through a RETURN VALUE rather than by "
                      f"raising, translating the narrow exception into that same "
                      f"return value is correct; catching `Exception` to do it is "
                      f"not."
                )
            if raw_clients:
                console.print("  [yellow]  wrapper returns the target library's "
                              "own object; asking for a translating adapter"
                              "[/yellow]")
                for site in raw_clients:
                    console.print(f"    [dim]· {site}[/dim]")
                notes.append(
                    f"Your previous edit returned {winner}'s own object rather "
                    f"than an adapter:\n  "
                    + "\n  ".join(raw_clients)
                    + f"\n\nThat renames the constructor and adapts nothing. "
                      f"Every method the call sites invoke on that object now "
                      f"raises {winner}'s exception classes where {original} "
                      f"raised its own, so a caller written `except "
                      f"{original}...` stops catching the failure entirely. "
                      f"Return a small first-party class that holds the "
                      f"{winner} object and exposes the SAME method names the "
                      f"call sites already call, translating {winner}'s "
                      f"exceptions into the ones {original} raised for the same "
                      f"failure: `except {winner}Error as exc: raise "
                      f"OriginalError(...) from exc`. Translate only the "
                      f"failure modes {original} actually surfaced, and "
                      f"re-raise -- do not swallow, and do not catch "
                      f"`Exception` to do it."
                )
            if fabricated:
                console.print("  [yellow]  wrapper reports a status it never "
                              "checked; asking it to use the real result"
                              "[/yellow]")
                for site in fabricated:
                    console.print(f"    [dim]· {site}[/dim]")
                notes.append(
                    f"Your previous edit called {winner} and then threw the "
                    f"result away, returning a constant:\n  "
                    + "\n  ".join(fabricated)
                    + f"\n\nThe caller reads that value. {original} returned "
                      f"the library's own status there -- non-zero when the "
                      f"operation actually failed -- so a literal makes the "
                      f"failure unreachable and every check of it a check of a "
                      f"constant. Use what {winner} returns. If {winner} "
                      f"reports failure by RAISING instead of returning, that "
                      f"is fine and is the one accepted form: catch the "
                      f"specific exception and return the failure status "
                      f"{original} used, letting the success path fall "
                      f"through. If {winner} genuinely reports nothing at all, "
                      f"say so in the docstring and return the value that "
                      f"means 'queued, not confirmed' -- do not claim a "
                      f"success you did not observe."
                )
            if invented:
                console.print("  [yellow]  wrapper references API that does not "
                              "exist in the installed package[/yellow]")
                for site in invented:
                    console.print(f"    [dim]· {site}[/dim]")
                notes.append(
                    f"Your previous edit referenced names that do not exist in "
                    f"the installed {winner}:\n  "
                    + "\n  ".join(invented)
                    + f"\n\nThese were read from the package itself, not "
                      f"guessed. Use the suggested names, or open the module "
                      f"and read what it exports. An invented name is not "
                      f"caught by the project's tests -- they inject a fake "
                      f"shaped like {original} -- and fails with ImportError or "
                      f"AttributeError the moment the real library loads."
                )
            feedback = "\n\n".join(notes)
    except Exception as e:
        console.print(f"  [yellow]ACI edit session unavailable ({e}) — falling back to one-shot generation.[/yellow]")
        return working_source, [], False

    console.print(
        f"  [dim]ACI session: {session.edit_count} edit(s) applied over "
        f"{len(transcript)} tool call(s)  |  tokens in:{tokens['input_tokens']:,} out:{tokens['output_tokens']:,}[/dim]"
    )
    for t in transcript:
        if "tool" in t:
            console.print(f"    [dim]· {t['tool']}({', '.join(f'{k}=...' for k in t.get('args', {}))})[/dim]")

    if session.edit_count == 0:
        console.print("  [yellow]ACI session made no edits — falling back to one-shot generation.[/yellow]")
        return working_source, [], False

    valid, err = _validate_syntax(final_source)
    if not valid or not _preserves_top_level_definitions(working_source, final_source):
        # Shouldn't happen — ACISession.edit() already syntax-checks every
        # individual edit — but guard anyway since this becomes patched_code.
        detail = err if not valid else "removed source structure or returned an empty file"
        console.print(f"  [red]ACI session result failed integrity check ({detail}) — falling back.[/red]")
        return working_source, [], False

    wrapper_names = [w.get("wrapper_name") for w in wrapper_needs if w.get("wrapper_name")]
    return final_source, wrapper_names, True


# ── Legacy one-shot injection (fallback for providers without tool calling) ─────

LEGACY_WRAPPER_SYSTEM = """You are an expert Python API compatibility engineer.
The ACI-based editing path is unavailable for this model/provider, so you're
generating wrapper code as a structured JSON blob instead of editing the file
directly. For each call site that needs a wrapper, write a complete Python
wrapper function (exact same call signature, silent input/output conversion,
descriptive name, docstring explaining the original vs new return type and
the transformation applied), and give the EXACT original call expression it
replaces plus the exact replacement expression.

DEFERRED IMPORTS: if the original imported the library INSIDE a function, the
replacement must be imported inside that function too. A lazy import means the
module can be loaded with the library absent, which is what makes an injected
collaborator work in tests; hoisting it to module scope breaks that while the
tests still pass against their fake.

EXCEPTION CLASSES: returning the new package's own object is not an adapter.
If the original package handed callers an object of its own type, return a
small first-party class that holds the new package's object and re-exposes the
same method names, translating the new package's exceptions into the ones the
original raised for the same failure. `return httpx.Client()` in place of
`requests.Session()` renames the constructor and leaves every call site raising
`httpx.HTTPStatusError` where it raised `requests.exceptions.HTTPError`, so
callers catching the original class stop catching the failure.

ERROR CONTRACT: the wrapper must not swallow failures. Do not add try/except
around a call to the new package defensively. If the ORIGINAL package raised
for a condition, the wrapper must raise too -- catch only to translate a
specific exception and then `raise ... from exc`. A handler ending in
`return`, `pass` or a bare log converts a loud failure into a silent wrong
answer, and the project's tests cannot catch it because they inject fakes that
never raise. Never write `except Exception:` or a bare `except:`. Catch every
exception the new package raises for the failure modes the original reported --
protocol AND transport errors, which are often sibling classes rather than
subclasses, so one class name is usually not enough; catch their common base or
a tuple. Convert an
exception into a return value only where the ORIGINAL package itself returned
a status for that same failure.

Return ONLY a JSON object:
{
  "wrappers": [
    {
      "name": "<function name>",
      "code": "<complete Python function definition with docstring>",
      "replaces": "<EXACT original call expression from the source>",
      "with": "<replacement call expression using the wrapper>"
    }
  ]
}
"""

LEGACY_WRAPPER_HUMAN = """MIGRATION: {original} → {winner}

CALL SITES NEEDING A WRAPPER:
{wrapper_needs}

FULL SOURCE CODE:
```python
{source_code}
```
"""


def _legacy_generate_wrappers(llm, working_source, original, winner, wrapper_needs) -> list[dict]:
    """True fallback: one-shot JSON generation of wrapper code + replaces/with
    strings, applied via the AST-offset/string-matching _inject_wrappers()
    below. Only used when the ACI edit loop itself is unavailable (e.g. the
    provider doesn't support tool calling) — see _run_aci_wrapper_injection."""
    needs_str = "\n".join(
        f"  L{w.get('line','?')}: {w.get('call','?')}  needs: {w.get('wrapper_summary','')}"
        for w in wrapper_needs
    )
    resp = invoke_with_timeout(llm, [
        SystemMessage(content=LEGACY_WRAPPER_SYSTEM),
        HumanMessage(content=LEGACY_WRAPPER_HUMAN.format(
            original=original, winner=winner, wrapper_needs=needs_str, source_code=working_source,
        )),
    ])
    from smart_loader.core.llm_json import extract_json, coerce_llm_text
    result = extract_json(resp.content, expect=dict) or {}
    return result.get("wrappers", [])

def _drop_unused_source_imports(source: str, source_roots: set[str]) -> tuple[str, list[str]]:
    """Remove imports of the migrated-away library that nothing uses any more.

    Removing the dependency IS the migration. A clone that imports the adapter
    and still carries ``import paho.mqtt.client as mqtt`` has not migrated: the
    old package must still be installed for it to run, which is precisely the
    coupling the exercise exists to break. It also scores ``lib_swapped=N`` and
    is therefore not counted as a successful migration, so the run is wasted.

    ``apply_import_replacements`` rewrites simple ``import X`` forms but did not
    match ``import paho.mqtt.client as mqtt`` -- a dotted submodule import with
    an alias -- so it survived a migration to aiomqtt untouched and unused.

    SAFETY: an import is removed only when every name it binds is unreferenced
    in the rest of the file. If any reference remains the statement stays, even
    though that means the dependency stays too, because deleting a live import
    turns a scoring problem into a NameError. Never guess here.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []

    def binds(node) -> set[str]:
        out = set()
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.add(a.asname or a.name)
        return out

    def targets_source(node) -> bool:
        if isinstance(node, ast.Import):
            return any(a.name.split(".")[0].casefold() in source_roots
                       for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            return node.module.split(".")[0].casefold() in source_roots
        return False

    candidates = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.Import, ast.ImportFrom)) and targets_source(n)]
    if not candidates:
        return source, []

    candidate_lines = {n.lineno for n in candidates}
    # Every name loaded anywhere OUTSIDE the import statements themselves.
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.lineno not in candidate_lines:
                used.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.lineno not in candidate_lines:
                used.add(node.value.id)

    drop, removed = set(), []
    for node in candidates:
        names = binds(node)
        if names and not (names & used):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            drop.update(range(node.lineno, end + 1))
            removed.append(f"line {node.lineno}: {', '.join(sorted(names))}")

    if not drop:
        return source, []
    kept = [line for i, line in enumerate(source.splitlines(keepends=True), start=1)
            if i not in drop]
    return "".join(kept), removed


def _bind_wrapper_imports(source: str, wrapper_names: list[str]) -> tuple[str, list[str]]:
    """Give each injected wrapper the imports it actually uses.

    The ACI edit session repeatedly put the target import in the WRONG scope:
    it rewrote the ``import`` line inside the function it was replacing rather
    than giving the new wrapper one of its own. Observed on
    B1_scada_poller run 3, where the two ended up exactly swapped::

        def _default_session():            # replaced, now dead
            import httpx                   # <- the import landed here
            return requests.Session()      # <- and 'requests' is now unbound

        def _default_session_wrapper():    # live, called from __init__
            return httpx.Client(...)       # <- 'httpx' unbound in THIS scope

    Both halves raise NameError. Neither is caught by the test suite, because
    the corpus injects a fake session and the factory never executes; and
    neither is caught by the "wrapper defined but never called" guard, because
    the wrapper IS called. It is a scoping failure, not a wiring failure.

    Repairing beats rejecting here. The wrapper body is correct and the fix is
    unambiguous -- a module the wrapper references, that is bound nowhere in
    its scope, needs importing. Reverting the whole edit over it throws away a
    working adapter and yields a byte-identical clone, which is the outcome
    with the least information in it.

    Only names used as ``X.attr`` are considered, and only when ``X`` is bound
    nowhere the wrapper can see it. Returns (source, [repairs made]).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []

    module_bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_bound.add(node.name)
        elif isinstance(node, ast.Assign):
            module_bound.update(t.id for t in node.targets if isinstance(t, ast.Name))

    repairs: list[str] = []
    lines = source.splitlines(keepends=True)
    # Bottom-up so earlier insertions do not shift later line numbers.
    targets = [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name in set(wrapper_names)]
    for fn in sorted(targets, key=lambda n: n.lineno, reverse=True):
        local: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Import):
                local.update(a.asname or a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                local.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.add(node.id)
            elif isinstance(node, ast.arg):
                local.add(node.arg)
        used = {n.value.id for n in ast.walk(fn)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
        missing = sorted(used - local - module_bound - {"self", "cls"})
        if not missing:
            continue
        # Insert inside the wrapper, matching the corpus convention of lazy
        # imports in factory functions, and keeping the repair local to the
        # function that needs it.
        #
        # After the docstring, not before it: the first statement of these
        # wrappers is almost always a docstring, and anything inserted above it
        # demotes it to an ordinary string expression, silently costing the
        # function its ``__doc__``.
        anchor = fn.body[0]
        if (len(fn.body) > 1 and isinstance(anchor, ast.Expr)
                and isinstance(anchor.value, ast.Constant)
                and isinstance(anchor.value.value, str)):
            anchor = fn.body[1]
        body_line = anchor.lineno - 1
        indent = " " * (len(lines[body_line]) - len(lines[body_line].lstrip()))
        for name in reversed(missing):
            lines.insert(body_line, f"{indent}import {name}  # connector: bound to wrapper scope\n")
            repairs.append(f"{fn.name}: added 'import {name}'")
    return "".join(lines), repairs


def replicate_lazy_imports(source: str) -> tuple:
    """Copy a lazy import into every other scope that uses the name it binds.

    ``_bind_wrapper_imports`` above repairs the same class of defect but only
    inside the functions named as wrappers, and it synthesises ``import X``,
    which is right only when the missing name IS a module name.

    Neither holds for the measured case. D1 seeds 1 and 2 both died on::

        def read_holding_registers(self, address, count, unit=None):
            import modbus_tk.defines as cst      # bound HERE
            return self._execute(unit, cst.READ_HOLDING_REGISTERS, ...)

        def write_register(self, address, value, unit=None):
            return self._execute(unit, cst.WRITE_SINGLE_REGISTER, ...)
                                       # ^ NameError: cst is not defined

    These are methods on a Connector-generated adapter class, so they are not
    in ``wrapper_names`` and were skipped; and the needed statement is
    ``import modbus_tk.defines as cst``, which no amount of guessing turns into
    ``import cst``.

    Both runs ended migration_success=N with the source untouched, over a name
    the file itself already defines two methods higher up.

    The repair invents nothing: it reuses an import statement VERBATIM from
    elsewhere in the same file, so the binding it adds is one the author
    already wrote. A name bound nowhere in the file is left alone -- that is a
    real unknown and belongs to the model, not here.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, []

    lines = source.splitlines(keepends=True)

    def statement_text(node) -> str:
        return "".join(lines[node.lineno - 1:node.end_lineno]).strip()

    # name -> the exact import statement that binds it, from anywhere in the file
    known: dict = {}
    module_bound: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or (alias.name.split(".")[0]
                                         if isinstance(node, ast.Import)
                                         else alias.name)
                known.setdefault(bound, statement_text(node))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_bound.update(
                a.asname or (a.name.split(".")[0] if isinstance(node, ast.Import)
                             else a.name) for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_bound.add(node.name)
        elif isinstance(node, ast.Assign):
            module_bound.update(t.id for t in node.targets
                                if isinstance(t, ast.Name))

    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    repairs: list = []
    insertions: list = []
    for fn in functions:
        local: set = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Import):
                local.update(a.asname or a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                local.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.add(node.id)
            elif isinstance(node, ast.arg):
                local.add(node.arg)
        used = {n.value.id for n in ast.walk(fn)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
        missing = sorted((used - local - module_bound - {"self", "cls"}) & set(known))
        if not missing:
            continue
        anchor_node = fn.body[0]
        if (len(fn.body) > 1 and isinstance(anchor_node, ast.Expr)
                and isinstance(anchor_node.value, ast.Constant)
                and isinstance(anchor_node.value.value, str)):
            anchor_node = fn.body[1]
        insertions.append((anchor_node.lineno - 1, fn.name, missing))

    if not insertions:
        return source, []

    # Bottom-up, so an earlier insertion does not shift a later line number.
    for body_line, fn_name, missing in sorted(insertions, reverse=True):
        indent = " " * (len(lines[body_line]) - len(lines[body_line].lstrip()))
        for name in reversed(missing):
            lines.insert(body_line,
                         f"{indent}{known[name]}  # connector: bound to this scope\n")
            repairs.append(f"{fn_name}: added `{known[name]}` for `{name}`")

    repaired = "".join(lines)
    try:
        ast.parse(repaired)
    except SyntaxError:
        return source, []
    return repaired, repairs


#: Markers delimiting a Connector-injected block, so a later repair cycle can
#: remove the previous one instead of stacking a second copy beside it.
_BLOCK_START = "AXIOM CONNECTOR"
_BLOCK_END = "AXIOM CONNECTOR END"


def _strip_previous_injection(source: str) -> str:
    """Remove blocks a previous Connector cycle injected into ``source``.

    A block runs from its ``AXIOM CONNECTOR`` banner to the matching
    ``AXIOM CONNECTOR END`` marker, and both are removed together.

    A block written before the end marker existed has no terminator, and there
    is no safe way to find where it stops. Consuming the run of top-level
    definitions after the banner looked right and was not: with only blank
    lines between the injected wrapper and the file's own next function, the
    sweep ran straight through and deleted the ORIGINAL code as well. Losing a
    user's function to tidy up a duplicate is far worse than the duplicate, so
    an untagged block gives up only its banner comment.

    That is not a gap in practice. Every injection from here on carries a
    terminator, so the accumulation this exists to stop cannot recur; only
    files patched by an older build can still hold an untagged block.

    Returns ``source`` unchanged when it holds no banner, and discards any
    removal that would stop the file parsing.
    """
    if _BLOCK_START not in source:
        return source
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    top_defs = [(n.lineno, n.end_lineno) for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]

    drop: set[int] = set()
    for i, line in enumerate(lines):
        if _BLOCK_START not in line or not line.lstrip().startswith("#"):
            continue
        start = i
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        end = None
        for j in range(i, len(lines)):
            if _BLOCK_END in lines[j]:
                end = j
                break
        if end is None:
            # Untagged: banner only. See the docstring -- guessing where the
            # block ends deleted the file's own code.
            end = i
            while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("#"):
                end += 1
        drop.update(range(start, end + 1))

    if not drop:
        return source
    kept = "\n".join(ln for k, ln in enumerate(lines) if k not in drop)
    ok, _ = _validate_syntax(kept)
    return kept if ok else source


def _inject_wrappers(
    source: str,
    original: str,
    winner: str,
    wrappers: list[dict],
) -> str:
    """
    Inject wrapper function definitions directly after the last
    import statement in the source file, and use AST to find and
    replace call sites precisely.
    """
    # 1. Build a map of AST-normalized original expressions to their new replacement strings
    ast_replacements = {}
    string_replacements = {}
    for w in wrappers:
        replaces_str = w.get("replaces", "")
        with_str = w.get("with", "")
        if replaces_str and with_str:
            string_replacements[replaces_str] = with_str
            try:
                orig_ast = ast.parse(replaces_str.strip(), mode="eval").body
                ast_replacements[ast.unparse(orig_ast)] = with_str.strip()
            except Exception:
                pass

    # 2. Replace the call sites using AST offsets
    patched = source
    try:
        tree = ast.parse(patched)
        matches = []
        for node in ast.walk(tree):
            if isinstance(node, ast.expr):
                try:
                    node_str = ast.unparse(node)
                    if node_str in ast_replacements:
                        matches.append((
                            node.lineno, node.col_offset,
                            node.end_lineno, node.end_col_offset,
                            ast_replacements[node_str]
                        ))
                except Exception:
                    pass

        # Sort matches in reverse order so replacements don't shift offsets of subsequent matches
        matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
        
        if matches:
            lines = patched.splitlines(keepends=True)
            for start_line, start_col, end_line, end_col, new_text in matches:
                start_idx = start_line - 1
                end_idx = end_line - 1
                if start_idx == end_idx:
                    line = lines[start_idx]
                    lines[start_idx] = line[:start_col] + new_text + line[end_col:]
                else:
                    first_line = lines[start_idx]
                    last_line = lines[end_idx]
                    new_first = first_line[:start_col] + new_text + last_line[end_col:]
                    lines[start_idx] = new_first
                    for i in range(start_idx + 1, end_idx + 1):
                        lines[i] = ""
            patched = "".join(lines)
            console.print(f"  [green]✓ AST-based replacement applied ({len(matches)} matches)[/green]")
        else:
            # Fallback to string replacement
            for rep, new_val in string_replacements.items():
                if rep in patched:
                    patched = patched.replace(rep, new_val)
                    console.print(f"  [dim]String Replaced: {rep} → {new_val}[/dim]")
    except SyntaxError:
        # Fallback to string replacement
        for rep, new_val in string_replacements.items():
            if rep in patched:
                patched = patched.replace(rep, new_val)
                console.print(f"  [dim]String Replaced: {rep} → {new_val}[/dim]")

    # 3. Inject wrapper definitions after the last *module-level* import.
    # ``ast.walk`` also sees imports nested in functions; inserting a
    # top-level wrapper after one of those places it inside that function's
    # indentation block and produces a syntax error.
    try:
        tree = ast.parse(patched)
        last_import_idx = 0
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import_idx = max(last_import_idx, node.lineno)
    except SyntaxError:
        last_import_idx = 0

    header = (
        f"\n\n# {'─' * 70}\n"
        f"# {_BLOCK_START}: {original} → {winner}\n"
        f"# The following functions bridge API incompatibilities between\n"
        f"# {original} and {winner}. They are injected by the AXIOM\n"
        f"# connector agent and called in place of the original API.\n"
        f"# {'─' * 70}\n"
    )

    if wrappers:
        # Drop anything a PREVIOUS repair cycle injected before adding this
        # round's wrappers. Without this the block is appended every cycle and
        # the stale copies stay behind as dead code.
        #
        # Measured on D3 with ACI disabled, so every cycle took this path: the
        # file ended up holding both ``publish_via_aiomqtt_client`` and
        # ``publish_via_aiomqtt_client_v2``. The stale one referenced ``asyncio``
        # and ``aiomqtt``, which the replacement imports only inside its own
        # body, so it produced `undefined-variable` findings -- which the
        # verifier routed back as a repair, which appended yet another wrapper.
        # Five cycles, five wrappers, budget exhausted, file reverted. A3 showed
        # the same shape earlier with five copies of ``parse_iso_datetime``.
        #
        # Matching on the name does not work: the model often renames the
        # replacement (``..._v2``), so the stale definition survives a
        # name-based sweep. The block markers are what make removal reliable.
        patched = _strip_previous_injection(patched)
        try:
            tree = ast.parse(patched)
            last_import_idx = 0
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    last_import_idx = max(last_import_idx, node.lineno)
        except SyntaxError:
            last_import_idx = 0

        wrapper_code = "\n\n".join(w["code"] for w in wrappers)
        injection = header + wrapper_code + f"\n# {_BLOCK_END}\n"

        lines = patched.splitlines()
        # insert_at is 0-indexed, lineno is 1-indexed, so lineno is the index of the line AFTER the import
        insert_at = last_import_idx
        lines.insert(insert_at, injection)
        patched = "\n".join(lines)

    valid, err = _validate_syntax(patched)
    if not valid:
        console.print(f"  [red]Injection resulted in syntax error: {err} — reverting[/red]")
        return source

    return patched
