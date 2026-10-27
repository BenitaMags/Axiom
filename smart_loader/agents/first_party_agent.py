"""First-Party Reimplementation Agent (FRA).

Why this agent exists
─────────────────────
The Library-Substitution Agent selects *among libraries*. Two evaluation
subjects do not ask for that:

    C1  simple-pid  -> in-house controller
    C2  transitions -> hand-rolled FSM

Their requirement is to remove the dependency and write its behaviour as
first-party code. No agent in the pipeline was asked to do that, so every run
declined and returned the file untouched -- 6 of 6 runs, both configurations,
every seed, across the whole study. This is a capability gap, not a tuning
failure.

Why it bypasses the Glue-Synthesis Agent
────────────────────────────────────────
The GSA's editing session exists to bridge an interface delta. The FRA is
constrained to produce none: it implements the outgoing library's *used* API
surface exactly, so ``PID(kp, ki, kd, setpoint=, output_limits=)`` and
``pid(level)`` keep their signatures and every existing call site is already
correct. Running an LLM editing pass over a file that needs no edits cannot
improve it and can damage it -- we observed that session rewrite a call site
other than the one its own analysis had identified. Skipping it also removes a
repair target and the loop that goes with it.

The four mechanical steps the Connector performs *after* its editing session
are NOT skipped; they run here too, through the shared
``migration_finalize`` module. Step 1 alone is what turns working code into a
completed migration.

Inline, not a new module
────────────────────────
The replacement is written into the same file rather than emitted as a sibling
module. Three reasons: the pipeline is built around one source file at a time
and carries a single ``patched_code`` back to the runner; the GSA already
injects its wrappers inline, so this is the established shape; and
``run_axiom_arms``'s diversification pass iterates the program's declared
source files, so a new module would silently escape the Logic-Mutation Agent --
which on these two subjects is precisely the code the LMA exists to diversify.
"""

from __future__ import annotations

import os

from rich.console import Console

from smart_loader.core.ast_rewrite import resolve_import_root
from smart_loader.core.migration_finalize import (
    FinalizeRequest, finalize_migration)

console = Console()

#: Attempts at the local execute-and-repair loop. Each agent in the pipeline
#: carries one; this is the FRA's. A failure here is repaired in place rather
#: than escalated, so the VCA sees only what survived this loop.
# 3 was not enough: a C1 run exhausted all three and the pipeline shipped
# nothing. The repair loop is also genuinely incremental now (it shows the
# model its own failed attempt), so later attempts are worth more than they
# were when every one of them restarted from the original.
MAX_ATTEMPTS = 5

SYSTEM = """You are replacing a third-party dependency with first-party code.

The program below depends on `{original}`. Its own documentation states that
this dependency is to be REMOVED and its behaviour written as first-party code
in this program. You are not choosing a different library. You are implementing
the behaviour yourself.

PRESERVE THE API SURFACE EXACTLY. This is the hard requirement, and everything
else depends on it. The program already calls `{original}` at specific sites,
and those call sites must keep working with no change:

{surface}

Your implementation must accept the same constructor arguments in the same
order with the same defaults, expose the same attributes, support the same
call and operator forms, and return the same types. If the original returns a
float, yours returns a float. If callers assign to an attribute, yours is
assignable.

PRESERVE THE BEHAVIOUR. Reproduce what `{original}` actually computes, not an
approximation of it. Numeric constants, clamping, saturation, sign conventions
and update order are observable and are tested. Where the original library has
a documented behaviour the program relies on -- integral windup limits, output
clamping, state-transition guards -- implement that behaviour.

Pay particular attention to behaviour that is easy to overlook because the
program's own tests cannot see it:
- caching or rate limiting. If the library only recomputes after some interval
  and returns its previous result in between, that IS its behaviour.
- what the very first call does, and what internal state the constructor
  initialises before that call.
- which quantity a rate-of-change term is computed from: the measurement or the
  error. Those are identical until a setpoint or reference moves, and different
  afterwards.
- initialise every internal attribute BEFORE assigning to any property whose
  setter reads it, or the constructor raises AttributeError.
{reference}
Requirements:
- Define the replacement INLINE in this file. Do not import it from a new
  module, and do not create one.
- REMOVE the `{original}` import. You own this whole file; deleting that import
  is your job and the migration is rejected if it survives.
- Change nothing else. Leave every other function, class, constant, docstring
  and import exactly as it is.
- Do not import any third-party package. Only the standard library, and only if
  the behaviour genuinely needs it.

Return ONLY the complete rewritten file, in a single fenced block:

```python
<complete file contents>
```
"""

TASK = """FILE: {filename}

{feedback}```python
{source}
```
"""

REFERENCE = """
THE OUTGOING LIBRARY'S OWN SOURCE. This is the real implementation of
`{original}`, taken from the installed package. Translate the behaviour the
program depends on from this code rather than recalling it. You are not copying
the whole library -- only the part this file uses -- but where you do implement
something, match what this source actually does.

```python
{source}
```
"""

RETRY = """Your previous attempt was REJECTED: {error}

Below is the attempt that was rejected, followed by the original file. Fix the
specific problem in your previous attempt -- do not start over from scratch, and
do not reintroduce anything it already got right.

YOUR REJECTED ATTEMPT:
```python
{attempt}
```

THE ORIGINAL FILE, for reference:

"""


def _reference_implementation(original: str, source_code: str,
                              budget: int = 24000) -> str:
    """The outgoing library's own source for the surface being replaced.

    The FRA was asked to "reproduce what `{original}` actually computes" from
    recall alone, and three rounds of review showed what that costs on
    `simple-pid`: the 0.01 s `sample_time` hold, the constructor-initialised
    `_last_time`, and `differential_on_measurement=True` were all missed, none
    of them visible in the program's own tests.

    The library is installed -- the evaluation requires it -- so handing over its
    real source turns "remember this library's semantics" into "translate this
    code", which is both easier and the thing a human actually does when removing
    a dependency. Nothing is leaked by it: in reimplement mode there is no
    candidate target to blind, only the outgoing package the program already
    imports in plain sight.

    The budget is deliberately generous (~6k tokens). At 9000 chars the
    truncation cut `simple_pid.PID.reset()`, which is where `_last_time` is
    initialised -- one of the three behaviours this helper exists to convey.
    Paying a few thousand tokens once per attempt is cheaper than a wasted
    attempt.

    Returns "" when the source cannot be recovered, in which case the prompt
    falls back to its previous wording.
    """
    import importlib
    import inspect

    root = resolve_import_root(original, source_code)
    try:
        module = importlib.import_module(root)
    except Exception:                                         # noqa: BLE001
        return ""

    chunks: list[str] = []
    seen: set[str] = set()
    for name in getattr(module, "__all__", None) or dir(module):
        if name.startswith("_") or name in seen:
            continue
        obj = getattr(module, name, None)
        if not (inspect.isclass(obj) or inspect.isfunction(obj)):
            continue
        # Only what this module defines, not what it re-exports from elsewhere.
        if getattr(obj, "__module__", "").split(".")[0] != root:
            continue
        try:
            chunks.append(inspect.getsource(obj))
        except (OSError, TypeError):
            continue
        seen.add(name)

    if not chunks:
        return ""
    text = "\n\n".join(chunks)
    if len(text) > budget:
        # Truncate from the END rather than the middle: definitions are ordered,
        # and a half-class is worse than a missing one.
        text = text[:budget].rsplit("\n", 1)[0] + "\n# ... (truncated)\n"
    return text


def _verbatim_overlap(candidate: str, original: str, source_code: str) -> float:
    """Fraction of the outgoing library's own code lines copied into the rewrite.

    Independent of how the text got there -- a prompt, or recall. The task is to
    reimplement the dependency's behaviour, not to paste it in: a vendored copy
    keeps the library's licence, defeats the stated purpose of removing it, and
    is byte-identical across seeds, which the diversity stage downstream depends
    on not being.

    Comments and docstrings are stripped before comparing, so reformatting does
    not hide a copy and rewriting a docstring does not look like original work.
    Returns 0.0 when the library's source cannot be read, since an unmeasurable
    overlap must not be reported as a clean one -- the caller treats 0.0 as
    "no evidence of copying", which is the correct reading of "could not check".
    """
    import difflib
    import re

    library = _reference_implementation(original, source_code, budget=200_000)
    if not library:
        return 0.0

    def code_lines(text: str) -> list:
        text = re.sub(r"#.*", "", text)
        text = re.sub(r'"' + '""' + r'.*?' + '"' + '""', "", text, flags=re.S)
        text = re.sub(r"'" + "''" + r".*?" + "'" + "''", "", text, flags=re.S)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    lib = code_lines(library)
    if not lib:
        return 0.0
    # Only RUNS of consecutive lines count. Counting every coincident line
    # inflated the score badly: a PID's attribute assignments and its update
    # step have a canonical form, so `self.Kp = Kp` matching proves nothing. On
    # the same five files, any-line scoring put an honest rewrite at 63% against
    # a vendored copy's 82% -- too close to threshold safely. Requiring runs of
    # four separates them cleanly: 6%, 15% and 45% for the honest rewrites
    # against 73% for the copy, whose longest single run was 31 lines.
    matcher = difflib.SequenceMatcher(None, code_lines(candidate), lib)
    blocks = matcher.get_matching_blocks()
    shared = sum(b.size for b in blocks if b.size >= MIN_VERBATIM_RUN)
    return shared / len(lib)


#: Consecutive matching code lines needed before a match counts as copying.
MIN_VERBATIM_RUN = 4

#: Above this share of the outgoing library's code lines, the rewrite is a
#: vendored copy rather than a reimplementation. Measured on five real files:
#: a verbatim vendoring scored 73% (longest run 31 lines) while three honest
#: reimplementations of the same class scored 6%, 15% and 45%. The threshold
#: sits in that gap with margin on both sides.
MAX_VERBATIM_OVERLAP = 0.60


def _behaviour_check(state: dict, filename: str, code: str):
    """Run the program's equivalence oracle against a candidate rewrite.

    Returns ``(ok, detail)``; ``(True, "")`` when no oracle corpus is
    configured, so categories without recorded golden vectors behave as before.

    This gate exists because the cheaper checks cannot see a behavioural
    regression. Compile, surface-preservation and the program's own pytest
    suite all passed on a C1 rewrite whose ``PID.__init__`` assigned
    ``self.output_limits`` -- invoking a property setter that reads
    ``self._integral`` -- seven lines BEFORE ``self._integral`` was defined, so
    constructing a PID with any output limit raised ``AttributeError``. The five
    C1 tests never caught it because every one of them injects a ``FakePID``
    and never constructs the real class. The golden vectors do construct it.
    """
    program_dir = str(state.get("isomorph_program_dir", "") or "")
    if not program_dir:
        return True, ""
    try:
        from equivalence.oracle import ProgramOracle
        oracle = ProgramOracle(
            program_dir, str(state.get("isomorph_program_name", "") or ""))
        verdict = oracle.check(os.path.splitext(os.path.basename(filename))[0],
                              code)
    except Exception as exc:                                  # noqa: BLE001
        # An oracle that cannot run must not silently pass the candidate, but
        # it must not fail an otherwise good rewrite either. Report and abstain.
        console.print(f"[yellow]  oracle unavailable "
                      f"({type(exc).__name__}: {exc}) — behaviour unchecked[/yellow]")
        return True, ""
    return bool(verdict.equivalent), str(verdict.detail)


def _used_surface(state: dict, original: str) -> str:
    """The API of the outgoing library this file actually uses.

    Taken from the parser's ``api_calls``, which records both ``module.attr()``
    call sites and bare calls to from-imported names. Scoping the requirement
    to what is USED matters: ``simple-pid`` has a broad surface and the FRA
    only has to reproduce the part this program depends on, which is what makes
    "preserve it exactly" a satisfiable instruction rather than a request to
    reimplement a library.
    """
    root = (original or "").split("-")[0].split(".")[0].casefold()
    seen: list[str] = []
    for imp in state.get("imports", []) or []:
        module = getattr(imp, "module", "") or ""
        if root and root not in module.casefold():
            continue
        for name in getattr(imp, "names", []) or []:
            entry = f"  - {name}  (imported by name)"
            if entry not in seen:
                seen.append(entry)
        for call in getattr(imp, "api_calls", []) or []:
            entry = f"  - {call}"
            if entry not in seen:
                seen.append(entry)
    if not seen:
        return ("  (the parser recorded no specific call sites; read the file "
                "and preserve every use of the dependency you find)")
    return "\n".join(seen)


def _extract_code(text: str) -> str:
    """The single fenced block the prompt asks for."""
    import re

    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if not blocks:
        return ""
    return max(blocks, key=len).strip() + "\n"


def _top_level_names(source: str) -> set:
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return out


def first_party_agent(state: dict) -> dict:
    """LangGraph node. Rewrites the source so the dependency is gone."""
    import os
    import time

    from smart_loader.core.llm_factory import _get_llm
    from smart_loader.core.llm_json import coerce_llm_text
    from smart_loader.core.state import AgentEvent
    from smart_loader.core.token_tracker import record_usage

    console.rule("[bold magenta]  First-Party Reimplementation Agent")

    source_code: str = state.get("source_code", "") or ""
    original: str = str(state.get("migration_source", "") or "").strip()
    filename = str(state.get("source_file", "") or "source.py").split("/")[-1]
    trace = list(state.get("agent_trace", []))

    if not source_code or not original:
        console.print("[yellow]  nothing to reimplement (no source or no "
                      "declared dependency)[/yellow]")
        return {}

    surface = _used_surface(state, original)
    console.print(f"  Reimplementing [bold]{original}[/bold] as first-party "
                  f"code in {filename}")
    console.print(f"[dim]  API surface to preserve:\n{surface}[/dim]")

    provider = state.get("llm_provider", "claude")
    model = state.get("model", "")
    session_id = state.get("_session_id", "unknown")
    llm = _get_llm(provider=provider, model=model, temperature=0.7,
                   disable_thinking=True)

    baseline_names = _top_level_names(source_code)
    feedback = ""
    last_error = ""
    last_attempt = ""

    # Built once: it is the same text on every attempt, and recovering it costs
    # an import plus inspect.getsource.
    # OFF by default, and it must stay that way for any reported result.
    #
    # Supplying the library's own source made the model VENDOR the library
    # instead of reimplementing it: three C1 runs at temperature 0.7 came back
    # byte-identical (same MD5, 334 lines), and 107 of the 130 code lines of
    # `simple_pid.PID` appeared verbatim in the output -- 82% of the class. It
    # passed every oracle layer for the worst possible reason: it WAS the
    # library. That is not a migration, it carries the original's licence into
    # the file, and identical output across seeds destroys the diversity premise
    # the Logic Substitution stage rests on.
    #
    # Kept behind a flag because it is still a legitimate thing to MEASURE --
    # "how much does an oracle-verified rewrite improve when the agent may read
    # the dependency?" is a real question, and the answer turned out to be
    # "it stops being a rewrite". Never enable it for a headline number.
    reference_source = (_reference_implementation(original, source_code)
                        if state.get("fra_supply_library_source") else "")
    reference = (REFERENCE.format(original=original, source=reference_source)
                 if reference_source else "")
    if reference_source:
        console.print(f"[yellow]  · {len(reference_source)} chars of {original} "
                      f"source supplied as reference — EXPERIMENTAL, expect "
                      f"verbatim copying[/yellow]")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        system = SYSTEM.format(original=original, surface=surface,
                               reference=reference)
        task = TASK.format(filename=filename, source=source_code,
                           feedback=feedback)
        started = time.time()
        try:
            reply = llm.invoke(f"{system}\n\n{task}")
        except Exception as exc:                          # noqa: BLE001
            console.print(f"[red]  provider error: {type(exc).__name__}: {exc}[/red]")
            trace.append(AgentEvent(stage="fra", event="provider_error",
                                    detail={"error": str(exc)[:200]}))
            return {"agent_trace": trace}

        # Record the call. Without this the run reports llm_calls=0,
        # tokens_total=0 and cost_usd=0.0 for a subject that plainly used the
        # provider -- which would understate Proteus's cost on exactly the two
        # subjects this agent exists to handle. Every other agent records here;
        # the FRA was simply missing it on its first live run.
        try:
            usage = getattr(reply, "usage_metadata", None) or {}
            record_usage(
                session_id=session_id, agent="fra", model=model,
                provider=provider,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                source_file=str(state.get("source_file", "") or ""),
                latency_ms=(time.time() - started) * 1000,
            )
        except Exception:                                 # noqa: BLE001
            pass

        candidate = _extract_code(coerce_llm_text(getattr(reply, "content", reply)))
        last_attempt = candidate or last_attempt
        if not candidate:
            last_error = "the reply contained no fenced python block"
        else:
            # Local execute-and-repair gate, cheapest checks first.
            import ast as _ast
            try:
                _ast.parse(candidate)
            except SyntaxError as exc:
                last_error = f"the rewrite does not parse: {exc}"
            else:
                missing = baseline_names - _top_level_names(candidate)
                if missing:
                    # Deleting a public definition is how a "rewrite" silently
                    # becomes a different program; the suite may not notice.
                    last_error = (f"top-level definitions were removed: "
                                  f"{', '.join(sorted(missing))}. Change nothing "
                                  f"except the dependency.")
                else:
                    result = finalize_migration(
                        source_code, candidate,
                        [FinalizeRequest(original=original)])
                    overlap = (_verbatim_overlap(result.code, original,
                                                 source_code)
                               if result.complete else 0.0)
                    ok, detail = (_behaviour_check(state, filename, result.code)
                                  if result.complete else (True, ""))
                    if result.complete and overlap > MAX_VERBATIM_OVERLAP:
                        # Checked BEFORE the behavioural gate, because a vendored
                        # copy passes that gate by construction -- it is the
                        # library. Rejecting it on behaviour would be impossible;
                        # rejecting it on provenance is the whole point.
                        last_error = (
                            f"{overlap:.0%} of `{original}`'s own code lines "
                            f"appear verbatim in your rewrite. You are copying "
                            f"the library, not reimplementing it. Write the "
                            f"behaviour yourself: same observable semantics, "
                            f"your own structure, names and control flow. Do not "
                            f"reproduce the library's source.")
                    elif result.complete and not ok:
                        # The rewrite is structurally correct and the library is
                        # gone, but it does not behave like the original. This
                        # is repairable feedback, so spend another attempt on it
                        # rather than shipping a module the oracle rejects.
                        last_error = (f"the rewrite is not behaviourally "
                                      f"equivalent to the original: {detail}. "
                                      f"Note that the program's own tests inject "
                                      f"a fake, so passing them does not mean "
                                      f"your implementation is correct — it is "
                                      f"exercised directly by recorded vectors.")
                    elif result.complete:
                        for entry in result.imports_dropped:
                            console.print(f"[dim]  · removed dead import — {entry}[/dim]")
                        console.print(f"[green]  ✓ reimplemented in {attempt} "
                                      f"attempt(s); {original} is gone[/green]")
                        trace.append(AgentEvent(
                            stage="fra", event="completed",
                            detail={"original": original, "attempts": attempt}))
                        return {
                            "patched_code": result.code,
                            "agent_trace": trace,
                            "fra_report": {"original": original,
                                           "attempts": attempt,
                                           "surface": surface},
                        }
                    else:
                        # Explicitly `else`, not a fall-through. When the
                        # behavioural gate above rejected a COMPLETE rewrite,
                        # falling through here overwrote the oracle's diagnosis
                        # with "still imported and in use ()" -- an empty
                        # survivor list, because there were none -- and sent the
                        # model that instead of the real reason.
                        survivors = result.survivors.get(original, [])
                        last_error = (
                            f"`{original}` is still imported and in use "
                            f"({'; '.join(survivors)}). Remove that import "
                            f"and make sure no code path needs it.")

        console.print(f"[yellow]  attempt {attempt}/{MAX_ATTEMPTS}: "
                      f"{last_error}[/yellow]")
        # Incremental, not a restart. The loop used to resend only the
        # original file with a one-line complaint, so the model never saw the
        # code it had just been told was wrong -- and a C1 run made the
        # identical `_integral` ordering mistake on attempts 2 AND 3 because of
        # it. Carrying the rejected attempt turns this into a repair loop.
        feedback = (RETRY.format(error=last_error, attempt=last_attempt)
                    if last_attempt else
                    f"Your previous attempt was rejected: {last_error}\n"
                    f"Correct it. Here is the ORIGINAL file again.\n\n")

    console.print(f"[red]  could not reimplement {original} in "
                  f"{MAX_ATTEMPTS} attempts — leaving the source unchanged[/red]")
    trace.append(AgentEvent(stage="fra", event="exhausted",
                            detail={"original": original, "error": last_error[:200]}))
    return {"agent_trace": trace, "fra_report": {"original": original,
                                                 "attempts": MAX_ATTEMPTS,
                                                 "error": last_error[:200]}}
