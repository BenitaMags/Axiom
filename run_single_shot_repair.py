#!/usr/bin/env python3
"""Baseline 1b — ``single_shot_repair``: one prompt, verified, then one repair.

The fourth arm. It sits between ``single_shot`` (one prompt, nothing checks
it) and the Proteus arms (agents plus a verification loop), and it isolates a
single variable: **does concrete runtime evidence improve generation, with no
agents and no loop at all?**

Architecture — two independent single-pass calls, NOT a conversation
────────────────────────────────────────────────────────────────────
    call 1   byte-identical to the ``single_shot`` arm. Literally the same
             function: :func:`run_single_shot.generate`, same PROMPT, same
             blinding, same parser. Not a copy that "should" match.
    verify   compile, the program's own pytest suite, and — on C/D, which
             have golden vectors — the four-layer equivalence oracle.
    call 2   only if verification failed. A fresh, stateless request with NO
             conversation history: the original program, the failed attempt,
             and the verifier's traces all go in as prompt content.

Never more than two calls. There is no chat loop and no message history
between them, which is what keeps this a *single-shot* arm rather than a
small agent.

Feedback is the VERIFIER, not a judge
─────────────────────────────────────
Compile diagnostics, pytest failure text, and the oracle's failing-layer
detail — the same evidence Proteus's VCA hands its own agents. That is what
makes the arms comparable: if the repair arm closes the gap to ``proteus_full``,
the gap was about evidence; if it does not, the gap was about the machinery.
``--feedback-source judge`` exists for a later experiment and is never the
default path.

Traces go in VERBATIM
─────────────────────
Assertion diffs, tracebacks, the oracle's recorded detail — unedited. The
hypothesis under test is that *concrete runtime evidence* helps; summarising
it would substitute the harness author's judgement for the evidence and test a
different hypothesis. The only editing is (a) middle-truncation of a single
trace over ``MAX_TRACE_CHARS``, keeping head and tail, and (b) the blinding
pass below.

Blinding
────────
``single_shot`` never names the replacement library, so neither may this arm.
The original sources are stripped by ``strip_target_hints`` exactly as in call
1, and the verifier evidence is stripped the same way in case a failure
message happens to quote the declared target. :func:`assert_blind` then fails
loudly if the declared target survives anywhere outside the model's own
previous attempt — see its docstring for why that one block is exempt.

Usage
─────
    python3 run_single_shot_repair.py --corpus ~/Downloads/evaluation --dry-run
    python3 run_single_shot_repair.py --corpus ... --program B1_scada_poller --runs 1
    python3 run_single_shot_repair.py --corpus ... --runs 3 --csv fresh.csv
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.eval import proteus_harness as H
import run_single_shot as S

CONFIG = "single_shot_repair"

#: The repair prompt. Fixed text, filled only from the four named holes. It
#: repeats the two conditions from call 1's prompt verbatim in substance,
#: because call 2 carries no conversation history — the model is not being
#: reminded of the rules, it is being told them for the first time.
REPAIR_PROMPT = """\
You previously rewrote a Python program to remove its dependency on `{from_lib}`
while keeping it FUNCTIONALLY EQUIVALENT to the original. Your rewrite was
executed and checked, and it did not pass. You are now going to correct it.

You are given three things: the ORIGINAL program, YOUR PREVIOUS ATTEMPT, and the
EVIDENCE from running it.

Work from the evidence. Each item names something concrete that went wrong. Fix
those things. Do not restart from scratch and do not redesign parts of your
attempt the evidence does not implicate — a rewrite that fixes the reported
problems and changes nothing else is the goal.

If the evidence shows that the library you chose cannot do what this program
needs — a fundamentally different execution model, a missing transport, an API
that cannot express the original's behaviour — then choosing a different library
IS the correct fix. Say so and switch. Do not build increasingly elaborate
adapters around a library that does not fit.

Switching means moving to ANOTHER library. Unless condition 2 below applies,
dropping the third-party dependency and writing the behaviour yourself is not a
repair — it abandons the task. Neither is restoring the original program, or
any part of it, to its pre-migration state: the original already fails the
requirement, so reverting cannot satisfy it. If the evidence says a package
could not be found or installed, name a real, existing replacement rather than
removing the dependency.

Exactly one of these two conditions applies, as before. Read the program —
including its module docstrings and comments — and decide which:

1. REPLACE WITH A LIBRARY (the normal case). Choose a suitable,
actively-maintained third-party library yourself and migrate to it. Write
whatever adapter or glue code the difference in API requires. Do not write your
own implementation of what a library already provides.

2. REIMPLEMENT AS FIRST-PARTY CODE (the exception). Choose this ONLY if the
program's own documentation states that the dependency is to be removed and its
behaviour written as first-party code. If the documentation does not say that,
condition 1 applies.

Requirements:
- Preserve the exact public API: same module names, same function and class
names, same signatures, same return types. The existing test suite must pass
against your rewrite unchanged.
- Preserve all numeric constants and thresholds exactly.
- Follow any additional requirement the program's own documentation states.
- On the first line of the first file, record which condition you applied and
your choice, formatted exactly as `# CHOSEN-TARGET: <pypi-name>` for condition 1,
or `# CHOSEN-TARGET: first-party` for condition 2. If you are switching to a
different library, this line must name the new one.

Return ONLY the corrected file(s), complete. For each file use exactly this
format, with no commentary before or after:

### FILENAME: <name.py>
```python
<complete file contents>
```

=== ORIGINAL PROGRAM ===
{sources}

=== YOUR PREVIOUS ATTEMPT ===
{attempt}

=== EVIDENCE FROM RUNNING YOUR ATTEMPT ===
{feedback}
"""

#: Ceiling on ONE trace, not on the evidence block. Roughly 4k tokens. A
#: pytest run that fails 40 tests with full tracebacks can run to hundreds of
#: kilobytes and would crowd out the program itself; a single assertion diff
#: never comes close, so in practice this fires rarely.
MAX_TRACE_CHARS = 16000

#: Marker inserted where the middle of an over-long trace was removed. Head
#: and tail are kept: the head carries the first failure (usually the causal
#: one) and the tail carries pytest's summary line.
TRUNCATION_MARKER = "\n\n... [{n:,} characters elided from the middle of this trace] ...\n\n"


# ── evidence ─────────────────────────────────────────────────────────────────

def middle_truncate(text: str, limit: int = MAX_TRACE_CHARS) -> str:
    """Keep the head and the tail, drop the middle, say how much was dropped.

    Truncating from the end would throw away pytest's summary and the oracle's
    verdict line, which are the parts that name what failed. Truncating from
    the front would throw away the first traceback, which is usually the cause
    of everything after it. So both ends stay.
    """
    if len(text) <= limit:
        return text
    keep = limit // 2
    dropped = len(text) - 2 * keep
    return text[:keep] + TRUNCATION_MARKER.format(n=dropped) + text[-keep:]


def compile_diagnostics(clone_dir: str, program: H.Program) -> str:
    """The interpreter's own compile error, as emitted. Empty if all files parse.

    Uses ``py_compile`` in a subprocess rather than ``ast.parse`` in-process so
    the text handed to the model is the diagnostic a developer would actually
    see — caret position, offending source line and all — not a re-rendering of
    a SyntaxError object.
    """
    parts: list[str] = []
    for name in program.source_files:
        path = os.path.join(clone_dir, name)
        if not os.path.isfile(path):
            parts.append(f"{name}: MISSING — your reply did not contain this "
                         f"file, and the program cannot run without it.")
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", name],
            cwd=clone_dir, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            parts.append((proc.stderr or proc.stdout or "").strip())
    shutil.rmtree(os.path.join(clone_dir, "__pycache__"), ignore_errors=True)
    return "\n\n".join(p for p in parts if p)


def oracle_detail(clone_dir: str, program: H.Program) -> tuple[str, str]:
    """``(equivalent, detail)`` from the four-layer oracle, or ``("NA", "")``.

    Only C and D have recorded golden vectors; A and B have no probes, so
    there is no oracle verdict to report and none is invented. Any failure to
    even run the oracle is returned as evidence too — "the checker could not
    execute your code" is a real, actionable finding, not a harness problem to
    hide from the model.
    """
    equiv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "equivalence")
    if equiv_dir not in sys.path:
        sys.path.insert(0, equiv_dir)
    from oracle import ProgramOracle  # type: ignore

    oracle = ProgramOracle(program.path, program.name)
    # "This program has no probes" (A and B) and "the oracle is broken" must
    # not look alike. Only the first is NA. Previously a bare except turned a
    # missing golden.json into NA on every C/D row, which would silently
    # remove the equivalence gate from the arm without a word in the output.
    if program.name not in oracle.golden:
        return "NA", ""

    # Every .py the clone carries, including modules the model ADDED — the
    # same rule backfill_from_clones.py uses. Restricting to source_files
    # would stage a copy importing a file that is not there and score N for a
    # reason that never existed.
    sources = {}
    for name in sorted(os.listdir(clone_dir)):
        path = os.path.join(clone_dir, name)
        if name.endswith(".py") and not name.startswith("test_") \
                and os.path.isfile(path):
            sources[name[:-3]] = open(path, encoding="utf-8").read()
    if not sources:
        return "NA", ""
    try:
        result = oracle.check_many(sources)
    except Exception as exc:                              # noqa: BLE001
        return "N", f"oracle could not run: {type(exc).__name__}: {exc}"
    return ("Y" if result.equivalent else "N"), result.detail


def build_evidence(compile_out: str, pytest_out: str, oracle_out: str,
                   parse_failure: str = "", install_failure: str = "") -> str:
    """The evidence block. Verbatim traces, labelled, in causal order.

    Ordered compile → tests → oracle because that is the order in which one
    failure explains the next: code that does not compile cannot be tested,
    and code that fails its tests tells you nothing new about equivalence.
    Sections with nothing to report are omitted entirely rather than written
    as "PASSED" — a clean section is not evidence of a defect, and listing it
    invites the model to go looking for one.
    """
    items: list[str] = []
    if parse_failure:
        items.append("[REPLY FORMAT]\n" + parse_failure)
    if install_failure:
        # First, and stated plainly, because every trace below it is a
        # downstream consequence: nothing can import, so nothing can run. It
        # is also the one item that is NOT a verbatim trace — a raw pip log
        # would say "No matching distribution found", from which "you invented
        # this library" is not obvious.
        items.append("[LIBRARY AVAILABILITY]\n" + install_failure)
    if compile_out.strip():
        items.append("[COMPILE DIAGNOSTIC]\n" + middle_truncate(compile_out.strip()))
    if pytest_out.strip():
        items.append("[PYTEST OUTPUT]\n" + middle_truncate(pytest_out.strip()))
    if oracle_out.strip():
        items.append("[EQUIVALENCE CHECK — failing layer]\n"
                     + middle_truncate(oracle_out.strip()))
    if not items:
        # Reached only if verification failed for a reason none of the three
        # channels recorded. Say so plainly rather than fabricating a cause.
        items.append("[NO TRACE RECORDED]\nThe checks did not pass, but no "
                     "diagnostic output was captured. Re-read your attempt "
                     "against the original program's public API and constants.")
    return "\n\n".join(items)


# ── the model's own choice, installed ────────────────────────────────────────

#: How the model is asked to name its choice. The prompt requires a PyPI
#: DISTRIBUTION name here, which is exactly what pip needs and is not always
#: the import root: C2 chose `python-statemachine`, imported as `statemachine`.
#: So the comment is read for INSTALLATION only. Scoring still derives the
#: target from the code via ``chosen_target()``, as it must — a comment can
#: claim one library while the code imports another, and C3 did precisely that.
_CHOSEN_RE = re.compile(r"^\s*#\s*CHOSEN-TARGET:\s*(?P<name>[\w.\-\[\]]+)",
                        re.M | re.I)

#: Names that mean "no package": condition 2, or a model that left the marker
#: unfilled. Nothing to install, and not a failure.
_NOT_A_PACKAGE = {"first-party", "first_party", "firstparty", "none", "stdlib",
                  "n/a", "na", "<pypi-name>"}


def declared_choice(clone_dir: str, program: H.Program) -> str:
    """The distribution name from the ``# CHOSEN-TARGET:`` comment, or "".

    Read from the clone's files in the program's own source order so a
    multi-file reply gives the marker on "the first file" the prompt asked for.
    """
    for name in program.source_files:
        path = os.path.join(clone_dir, name)
        if not os.path.isfile(path):
            continue
        match = _CHOSEN_RE.search(open(path, encoding="utf-8").read()[:4000])
        if match:
            found = match.group("name").strip().strip("`")
            if found.lower() not in _NOT_A_PACKAGE:
                return found
    return ""


def ensure_chosen_target(clone_dir: str, program: H.Program,
                         timeout: int = 300) -> tuple[str, str]:
    """Install the library the MODEL picked. Returns ``(status, detail)``.

    ``ensure_targets`` installs the corpus's seven DECLARED targets, but this
    arm never tells the model what to migrate to — across the first full run
    it chose eleven different libraries, only three of which overlap. Six of
    the eight attempt-1 failures were therefore ``ModuleNotFoundError`` on a
    package we simply never installed, and since the evidence block quotes the
    verifier faithfully, the model was told "No module named 'gmqtt'" and
    correctly concluded it should stop using gmqtt. Five of eight repairs
    abandoned the library. The repair loop worked perfectly on false evidence.

    Status values, and why the distinction is load-bearing:

      ``Y``   importable, whether it already was or we installed it
      ``NA``  no third-party import to install (a first-party rewrite)
      ``N``   pip could not supply it. If the name does not exist on PyPI the
              model invented a library, which is a REAL failure and should
              reach the repair prompt as one. That is a completely different
              claim from "we did not install it", and before this they were
              indistinguishable in the row and in the evidence.

    NOTE: this installs a package name chosen by the model into the running
    interpreter. That is a genuine supply-chain surface — a hallucinated name
    is a typosquatting target — which is why ``--no-install-chosen`` exists and
    why every install is printed.
    """
    root = H.chosen_target(clone_dir, program)
    if not root:
        return "NA", ""                    # first-party rewrite: nothing to get
    try:
        __import__(root)
        return "Y", ""
    except ImportError:
        pass

    # Candidates in order of authority: what the model DECLARED (a
    # distribution name), then the import root itself, then its dash form.
    # ``python-statemachine`` is only reachable from the declaration.
    candidates: list[str] = []
    for name in (declared_choice(clone_dir, program), root,
                 root.replace("_", "-")):
        if name and name not in candidates:
            candidates.append(name)

    last = ""
    for name in candidates:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", name],
            capture_output=True, text=True, timeout=timeout)
        last = (proc.stderr or proc.stdout or "").strip()
        try:
            __import__(root)
            print(f"    installed {name} (imports as {root})", flush=True)
            return "Y", ""
        except ImportError:
            continue

    tail = last.splitlines()[-1][:150] if last else ""
    if "No matching distribution" in last or "Could not find a version" in last:
        # The model named a package that does not exist. Real failure, and the
        # single most actionable thing we can tell it.
        return "N", (f"The package `{candidates[0]}` you chose does not exist "
                     f"on PyPI — it could not be found or installed, so "
                     f"`import {root}` cannot work. You invented this library. "
                     f"Choose one that actually exists.")
    return "N", (f"`{root}` could not be installed in this environment "
                 f"(tried: {', '.join(candidates)}). {tail}")


# ── blinding ─────────────────────────────────────────────────────────────────

class BlindingError(AssertionError):
    """The declared migration target survived into the repair prompt."""


def _target_root(program: H.Program) -> str:
    """The declared target as a searchable token, or "" when there is none.

    C1/C2 declare "in-house controller" / "hand-rolled FSM" — not package
    names, with no import form to leak, so there is nothing to blind.
    """
    root = (program.to_lib or "").split()[0].split("[")[0].strip("`").lower()
    # Same admissibility test strip_target_hints uses, so the assertion checks
    # exactly what the stripper was asked to remove -- no more, no less.
    return root if root and (root.isidentifier() or "-" in root) else ""


def strip_hint_prose(text: str, target: str) -> str:
    """Drop comment/docstring lines that name the target; leave CODE untouched.

    The model routinely carries the original's module docstring forward into
    its rewrite, and that docstring is where the corpus names its own intended
    answer ("substitution target: `requests` -> `httpx`", "the
    `requests`/`httpx` package"). Echoing the attempt back verbatim would
    therefore re-inject the hint that call 1 was blinded against — an actual
    leak, observed on B1, not a hypothetical one.

    What it must NOT do is what ``strip_target_hints`` does to the sources:
    delete every line mentioning the target. If the model genuinely migrated
    to that library, that would remove its imports and calls, and the attempt
    shown to the model would be broken in ways it did not write — the evidence
    would then contradict the code beside it, which is worse than the leak.

    So the line is dropped only when it carries no executable tokens. An
    unparseable attempt falls back to returning the text unchanged; the
    assertion below is what catches anything this misses.
    """
    import io
    import tokenize

    root = (target or "").split()[0].split("[")[0].strip("`").lower()
    if not root or not (root.isidentifier() or "-" in root):
        return text
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    code_lines: set[int] = set()
    for tok in toks:
        if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                        tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.ENDMARKER):
            continue
        code_lines.update(range(tok.start[0], tok.end[0] + 1))
    kept = [line for n, line in enumerate(text.splitlines(keepends=True), 1)
            if n in code_lines or root not in line.lower()]
    return "".join(kept)


def assert_blind(prompt: str, program: H.Program, attempt_block: str) -> None:
    """Fail loudly if the declared target leaked into the repair prompt.

    The attempt block is exempt, and only the attempt block. If the model's
    own previous rewrite imported the declared target, that was *its* choice
    in a blinded call-1 prompt — echoing its own code back to it reveals
    nothing it did not already decide. Everything else in the prompt (the
    original sources and the verifier evidence) is under our control and must
    be clean, so a hit there raises.
    """
    root = _target_root(program)
    if not root:
        return
    outside = prompt.replace(attempt_block, "") if attempt_block else prompt
    if root in outside.lower():
        lines = [ln.strip() for ln in outside.splitlines()
                 if root in ln.lower()][:5]
        raise BlindingError(
            f"{program.name}: declared target {program.to_lib!r} leaked into "
            f"the repair prompt outside the model's own attempt:\n  "
            + "\n  ".join(lines))
    # Inside the attempt, only executable lines can still name the target —
    # strip_hint_prose removed the rest. Those are the model's own imports and
    # calls from a blinded call 1, so they are its choice, not our hint. Not
    # fatal, but announced every time so it stays a counted, visible event
    # rather than a silent exception to the blinding rule.
    if root in (attempt_block or "").lower():
        print(f"    note: {program.name} — the attempt's own CODE references "
              f"{root!r}; the model chose it in call 1 under blinding, so it "
              f"is echoed back unchanged", flush=True)


# ── prompt assembly ──────────────────────────────────────────────────────────

def read_sources_blinded(program: H.Program) -> str:
    """The original program, stripped and formatted exactly as in call 1.

    Deliberately reuses ``strip_target_hints`` and the same ``### FILENAME:``
    shape as ``run_single_shot.build_prompt`` so the model sees one consistent
    representation of the program across both calls.
    """
    return "\n\n".join(
        f"### FILENAME: {name}\n```python\n"
        f"{S.strip_target_hints(open(os.path.join(program.path, name), encoding='utf-8').read(), program.to_lib)}```"
        for name in program.source_files
    )


def render_attempt(files: dict[str, str], raw_reply: str = "",
                   target: str = "") -> str:
    """The failed attempt, in the same ``### FILENAME:`` shape it arrived in.

    When nothing parsed there are no files to show, so the raw reply goes in
    instead — the defect being repaired IS the reply's shape, and hiding the
    reply would make the evidence unactionable.
    """
    if files:
        return "\n\n".join(f"### FILENAME: {name}\n```python\n"
                           f"{strip_hint_prose(body, target)}```"
                           for name, body in sorted(files.items()))
    # An unparseable reply is mostly prose, not code, so the tokenizer-based
    # stripper cannot classify it. Use the sources' own stripper instead: this
    # text is being shown as an example of a badly-shaped reply, not as code
    # to preserve, so losing a line to over-stripping costs nothing.
    return (middle_truncate(S.strip_target_hints(raw_reply, target).strip())
            or "(your reply was empty)")


def build_repair_prompt(program: H.Program, attempt: str,
                        feedback: str) -> str:
    """Assemble call 2's prompt and verify its blinding before returning it."""
    sources = read_sources_blinded(program)
    # The evidence is machine-generated from the clone's execution and can
    # quote the declared target (a ModuleNotFoundError names it verbatim), so
    # it gets the same stripping the sources get.
    feedback = S.strip_target_hints(feedback, program.to_lib)
    prompt = REPAIR_PROMPT.format(from_lib=program.from_lib, sources=sources,
                                  attempt=attempt, feedback=feedback)
    assert_blind(prompt, program, attempt)
    return prompt


# ── the second call ──────────────────────────────────────────────────────────

def generate_repair(program: H.Program, prompt: str, model: str,
                    provider: str, seed: int, reply_dir: str = "",
                    ) -> tuple[dict[str, str], dict]:
    """One stateless call with the assembled repair prompt.

    A fresh client and a bare ``invoke(prompt)`` — no message list, no history
    from call 1. Everything the model knows about the first attempt is text in
    this prompt, which is what makes the arm reproducible from the saved
    prompt alone.
    """
    from smart_loader.core.llm_factory import _get_llm
    from smart_loader.core.llm_json import coerce_llm_text

    llm = _get_llm(provider=provider, model=model, temperature=0.7,
                   disable_thinking=True)
    started = time.time()
    reply = S._invoke_with_backoff(llm, prompt)
    elapsed = time.time() - started

    text = coerce_llm_text(getattr(reply, "content", reply))
    usage = getattr(reply, "usage_metadata", None) or {}
    tin = int(usage.get("input_tokens", 0) or 0)
    tout = int(usage.get("output_tokens", 0) or 0)
    parsed = S.parse_files_lenient(text, program)
    if reply_dir:
        os.makedirs(reply_dir, exist_ok=True)
        stem = os.path.join(reply_dir, f"{program.name}_run{seed}.repair")
        with open(f"{stem}.prompt.txt", "w", encoding="utf-8") as fh:
            fh.write(prompt)
        with open(f"{stem}.reply.txt", "w", encoding="utf-8") as fh:
            fh.write(f"# parsed {len(parsed)} file(s): {sorted(parsed)}\n"
                     f"# expected: {program.source_files}\n"
                     f"{'=' * 70}\n{text}")
    rate_in, rate_out = S.PRICING.get(model, (0.0, 0.0))
    return parsed, {
        "wall_time_s": round(elapsed, 1),
        "tokens_in": tin, "tokens_out": tout,
        "cost_usd": round(tin / 1e6 * rate_in + tout / 1e6 * rate_out, 4),
    }


def judge_feedback(program: H.Program, clone_dir: str, model: str,
                   provider: str) -> str:
    """``--feedback-source judge``. Not the default path; not used by it.

    Kept for a later experiment that asks whether an LLM reviewer's prose
    findings repair as well as the verifier's traces. It is a strictly
    different treatment — the judge sees no execution — so mixing the two in
    one CSV would confound the arm, which is why ``feedback_source`` is
    recorded per row.
    """
    import judge_clones as J

    prompt, leaked = J.build_prompt(program, clone_dir)
    if leaked:
        raise BlindingError(f"judge prompt leaks {leaked}")
    verdict, text, _tel = J.judge_one(prompt, provider, model)
    if not verdict:
        return "[JUDGE]\n" + middle_truncate(text.strip())
    return "[JUDGE FINDINGS]\n" + middle_truncate(J.detail_of(verdict) or text)


# ── one run ──────────────────────────────────────────────────────────────────

def _write_files(work: str, program: H.Program, files: dict) -> int:
    wrote = 0
    for name, body in files.items():
        if name in program.source_files:
            with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
                fh.write(body)
            wrote += 1
    return wrote


def _save_clone(clone_root: str, subdir: str, work: str) -> str:
    """Returns the clone fingerprint, or "" when no archive is being kept.

    Delegates to `H.save_clone` so all three runners share one implementation:
    an existing clone directory is renamed aside rather than written into, which
    is what stops a re-run from producing a directory that is a mixture of two
    runs and no longer matches the CSV row describing it.

    This arm saves TWO clones per run -- attempt 1 under
    `<program>_run<n>_attempt1` and the final one under `<program>_run<n>` -- so
    the directory name is passed explicitly.
    """
    if not clone_root:
        return ""
    return H.save_clone(work, clone_root, CONFIG, dest_name=subdir)


def _measure(work: str, program: H.Program) -> dict:
    """Everything the row needs about one attempt, computed identically to
    ``single_shot``. Shared by attempt 1 and the final attempt so the paired
    comparison cannot be an artefact of two different measurement paths."""
    compiled = H.compiles(work, program)
    passed, total, detail, raw = H.run_tests(work, program)
    success = compiled and total > 0 and passed == total
    divergence = H.divergence_score(program.path, work, program)
    # See H.migrated: `divergence < 0.999` is blind to identifier changes, so a
    # correct library swap scored as a no-op while adapter-injecting agents
    # always registered as changed.
    migrated = H.migrated(program.path, work, program)
    swapped = H.library_swapped(work, program)
    return {
        "compiled": compiled, "passed": passed, "total": total,
        "detail": detail, "raw": raw, "success": success,
        "divergence": divergence, "migrated": migrated, "swapped": swapped,
        "chosen": H.chosen_target(work, program),
        # The same four-part verdict every arm uses, so the paired attempt-1 /
        # final comparison in this row is computed identically to the rows in
        # the other three CSVs.
        "migration_success": H.migration_ok(program.path, work, program),
    }


def run_one(program: H.Program, run_id: int, model: str, provider: str,
            clone_root: str, dry_run: bool, feedback_source: str = "verifier",
            reply_dir: str = "", prompt_dir: str = "",
            install_chosen: bool = True) -> dict:
    """One (program, seed): call 1, verify, and at most one repair call."""
    row = H.blank_row(program, CONFIG, run_id, model)
    work = H.stage(program)
    scratch_replies = "" if reply_dir else tempfile.mkdtemp(prefix="ssr-reply-")
    capture_dir = reply_dir or scratch_replies
    attempt1_dir = ""
    try:
        # ── call 1: byte-identical to the single_shot arm ─────────────────
        if dry_run:
            return _dry_run_row(row, program, run_id, prompt_dir)

        started1 = time.time()
        gen_error = ""
        raw_reply = ""
        try:
            files1, tel1 = S.generate(program, model, provider, run_id,
                                      reply_dir=capture_dir)
        except Exception as exc:                          # noqa: BLE001
            files1, tel1 = {}, {
                "wall_time_s": round(time.time() - started1, 1),
                "llm_calls": 1, "tokens_in": 0, "tokens_out": 0,
                "tokens_total": 0, "cost_usd": 0.0}
            gen_error = f"generation error: {type(exc).__name__}: {exc}"[:200]
        # Read back the raw reply, which only ``generate`` sees. This matters
        # exactly when nothing parsed: the repair prompt then shows the reply
        # itself, and without it the prompt would claim "(your reply was
        # empty)" when the model had in fact answered at length. So the reply
        # is always captured -- into a scratch directory when the user did not
        # ask for one -- rather than silently degrading to false evidence.
        path = os.path.join(capture_dir, f"{program.name}_run{run_id}.reply.txt")
        if os.path.isfile(path):
            raw_reply = open(path, encoding="utf-8").read().split("=" * 70, 1)[-1]

        wrote1 = _write_files(work, program, files1)
        parse_failure = ""
        if wrote1 == 0:
            parse_failure = (
                "Your reply contained no file we could extract. The required "
                "format is a `### FILENAME: <name.py>` line followed by a "
                "```python fenced block, one per file. Expected file(s): "
                + ", ".join(program.source_files))
            m1 = {"compiled": False, "passed": 0,
                  "total": S._expected_tests(program), "detail": "", "raw": "",
                  "success": False, "divergence": 1.0, "migrated": False,
                  "swapped": "N", "chosen": "", "migration_success": False}
            oracle_equiv, oracle_out = "NA", ""
            install_status, install_detail = "NA", ""
            row["target_installed"] = "NA"
        else:
            # Install the model's OWN choice before measuring anything. Under
            # free choice the corpus's declared-target install list does not
            # cover what the model picked, and verifying without this scores
            # "we did not install it" as a model failure — then tells the model
            # so, in the evidence. See ensure_chosen_target.
            #
            # Call 1 itself is untouched: same prompt, same call, byte for
            # byte. Only the VERIFICATION of its output is corrected, which is
            # why attempt1_* here and the single_shot arm's columns can differ
            # on rows whose chosen library was simply absent.
            install_status, install_detail = (
                ensure_chosen_target(work, program) if install_chosen
                else ("NA", ""))
            row["target_installed"] = install_status
            m1 = _measure(work, program)
            # Always asked, pass or fail: on C/D the oracle is part of what
            # "verified" MEANS, so a clone that passes its tests can still
            # need repair. Skipping it on a test-pass would let an
            # equivalence violation end the run at one call.
            oracle_equiv, oracle_out = oracle_detail(work, program)

        verified = bool(m1["success"] and oracle_equiv in ("Y", "NA"))

        # Attempt 1 is kept whatever happens, beside the final clone, so a
        # repair can be diffed against what it repaired.
        attempt1_dir = f"{program.name}_run{run_id}_attempt1"
        _save_clone(clone_root, attempt1_dir, work)

        row.update(
            attempt1_compiles="Y" if m1["compiled"] else "N",
            attempt1_tests_passed=m1["passed"],
            attempt1_migration_success="Y" if m1["migration_success"] else "N",
            chosen_target_before=m1["chosen"],
        )

        final = m1
        tel2: dict = {}
        repair_note = ""
        # A call that never returned leaves nothing to repair — the evidence
        # would be about our network, not the model's code. Recorded as an
        # infrastructure failure, exactly as single_shot records it.
        if verified or gen_error:
            row["repair_attempted"] = "N"
        else:
            row["repair_attempted"] = "Y"
            if feedback_source == "judge":
                feedback = judge_feedback(program, work, model, provider)
            else:
                # A fully-passing suite is not evidence of a defect, so it
                # is withheld: the D-category case where every test passes and
                # only the oracle objects must point the model at the oracle,
                # not hand it a "5 passed" line to reconcile.
                pytest_out = "" if (m1["compiled"] and m1["total"]
                                    and m1["passed"] == m1["total"]) else m1["raw"]
                feedback = build_evidence(
                    compile_diagnostics(work, program), pytest_out, oracle_out,
                    parse_failure=parse_failure,
                    install_failure=install_detail)
            prompt2 = build_repair_prompt(
                program, render_attempt(files1, raw_reply, program.to_lib),
                feedback)
            if prompt_dir:
                os.makedirs(prompt_dir, exist_ok=True)
                with open(os.path.join(
                        prompt_dir, f"{program.name}_run{run_id}.repair.prompt.txt"),
                        "w", encoding="utf-8") as fh:
                    fh.write(prompt2)
            try:
                files2, tel2 = generate_repair(program, prompt2, model,
                                               provider, run_id,
                                               reply_dir=reply_dir)
            except Exception as exc:                      # noqa: BLE001
                files2, tel2 = {}, {"wall_time_s": 0.0, "tokens_in": 0,
                                    "tokens_out": 0, "cost_usd": 0.0}
                repair_note = f"repair error: {type(exc).__name__}: {exc}"[:150]
            if _write_files(work, program, files2):
                final = _measure(work, program)
            elif not repair_note:
                # The repair produced nothing usable. Attempt 1 stands as the
                # delivered clone; overwriting it with nothing would score a
                # failed repair as a regression it did not cause.
                repair_note = "repair returned no parseable files"

        # The fingerprint of the DELIVERED clone -- the one this row describes.
        # attempt 1 is archived beside it but is not what the row measures, so
        # its hash is not the row's identity. Discarding this return value is
        # how all 36 rows of the first final run shipped without a clone_sha.
        row["clone_sha"] = _save_clone(clone_root, f"{program.name}_run{run_id}", work)

        # ── telemetry: both calls summed, llm_calls counts calls made ─────
        calls = 1 + (1 if tel2 else 0)
        row.update(
            wall_time_s=round(tel1.get("wall_time_s", 0.0)
                              + tel2.get("wall_time_s", 0.0), 1),
            llm_calls=calls,
            tokens_in=tel1.get("tokens_in", 0) + tel2.get("tokens_in", 0),
            tokens_out=tel1.get("tokens_out", 0) + tel2.get("tokens_out", 0),
            tokens_total=(tel1.get("tokens_in", 0) + tel2.get("tokens_in", 0)
                          + tel1.get("tokens_out", 0) + tel2.get("tokens_out", 0)),
            cost_usd=round(tel1.get("cost_usd", 0.0)
                           + tel2.get("cost_usd", 0.0), 4),
            iters_total=calls - 1,
        )

        total_lines = sum(
            len(open(os.path.join(program.path, n), encoding="utf-8")
                .read().splitlines())
            for n in program.source_files
            if os.path.isfile(os.path.join(program.path, n))
        ) or 1
        changed = H.loc_changed(program.path, work, program)
        final_oracle = oracle_equiv
        if row["repair_attempted"] == "Y" and final is not m1:
            final_oracle = oracle_detail(work, program)[0]

        row.update(
            compiles="Y" if final["compiled"] else "N",
            tests_passed=final["passed"], tests_total=final["total"],
            converged="Y",
            oracle_equiv=final_oracle,
            failure_mode=H.classify_failure(final["compiled"], final["passed"],
                                            final["total"],
                                            output=final["raw"]),
            # See the note in run_axiom_arms: filled by the judge, NA until then.
            hallucination="NA",
            divergence_score=final["divergence"],
            loc_changed=changed,
            pct_firstparty_changed=round(100.0 * changed / total_lines, 1),
            migrated="Y" if final["migrated"] else "N",
            migration_success="Y" if final["migration_success"] else "N",
            lib_swapped_correctly=final["swapped"],
            chosen_target=final["chosen"],
            chosen_target_after=final["chosen"],
            repair_changed_target=("Y" if (row["repair_attempted"] == "Y"
                                           and final["chosen"] != m1["chosen"])
                                   else "N"),
            glue_needed="Y" if program.category in ("B", "D") else "N",
            logic_mutated="Y" if program.category in ("C", "D") else "NA",
            notes=("; ".join(x for x in (gen_error, repair_note,
                                         final["detail"]) if x))[:200],
        )
        return row
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if scratch_replies:
            shutil.rmtree(scratch_replies, ignore_errors=True)


#: Stand-in evidence for ``--dry-run``. It deliberately quotes the declared
#: target in all three channels — a ModuleNotFoundError really does name the
#: package it could not import — so the dry run exercises the blinding
#: stripper and ``assert_blind`` on real prompts rather than on clean ones
#: that could never have leaked in the first place.
_DRY_EVIDENCE = """\
  File "{first}", line 41
    return client.read(  # noqa
                     ^
SyntaxError: '(' was never closed"""

_DRY_PYTEST = """\
=================================== FAILURES ===================================
______________________________ test_reads_register _____________________________
E   ModuleNotFoundError: No module named '{target}'
E       assert 12.5 == 12.75
E        +  where 12.5 = read_scaled(0x40)
=========================== 1 failed, 3 passed in 0.14s ========================"""

_DRY_ORACLE = "golden mismatch in: poll_cycle_2, poll_cycle_5 ({target})"


def _dry_run_row(row: dict, program: H.Program, run_id: int,
                 prompt_dir: str) -> dict:
    """Assemble and print both prompts, call nothing.

    The point is to prove the two things that cost money to discover: that the
    repair prompt assembles for every program, and that it is blind. So the
    real ``strip_target_hints`` / ``assert_blind`` path runs here, on evidence
    engineered to contain the target.
    """
    first = program.source_files[0] if program.source_files else "main.py"
    target = program.to_lib or "TARGET"
    feedback = build_evidence(
        _DRY_EVIDENCE.format(first=first),
        _DRY_PYTEST.format(target=target),
        _DRY_ORACLE.format(target=target))
    attempt = render_attempt(
        {first: "# CHOSEN-TARGET: <the model's choice>\n"
                "<the model's previous rewrite would be here>\n"},
        target=program.to_lib)
    prompt2 = build_repair_prompt(program, attempt, feedback)
    prompt1 = S.build_prompt(program)
    if prompt_dir:
        os.makedirs(prompt_dir, exist_ok=True)
        stem = os.path.join(prompt_dir, f"{program.name}_run{run_id}")
        with open(f"{stem}.call1.prompt.txt", "w", encoding="utf-8") as fh:
            fh.write(prompt1)
        with open(f"{stem}.repair.prompt.txt", "w", encoding="utf-8") as fh:
            fh.write(prompt2)
    else:
        print(f"\n{'=' * 78}\n=== {program.name} run{run_id} — CALL 1 PROMPT "
              f"({len(prompt1):,} chars)\n{'=' * 78}\n{prompt1}")
        print(f"\n{'=' * 78}\n=== {program.name} run{run_id} — REPAIR PROMPT "
              f"({len(prompt2):,} chars)\n{'=' * 78}\n{prompt2}")
    row.update(wall_time_s=0.0, llm_calls=0, tokens_in=0, tokens_out=0,
               tokens_total=0, cost_usd=0.0, iters_total=0,
               repair_attempted="N", notes="dry run — no LLM call")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--program", action="append", default=None)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--start-run", type=int, default=1)
    ap.add_argument("--llm", default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--clone-dir", default=None)
    ap.add_argument("--feedback-source", default="verifier",
                    choices=("verifier", "judge"),
                    help="what call 2 is shown. 'verifier' (default) is the "
                         "compile diagnostic, pytest output and oracle detail "
                         "— the same evidence Proteus's VCA gives its agents, "
                         "which is what makes the arms comparable. 'judge' "
                         "substitutes judge_clones.py's LLM review and is a "
                         "DIFFERENT treatment reserved for a later "
                         "experiment; it is never the default.")
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble and print both prompts, call no API")
    ap.add_argument("--prompt-dir", default=None,
                    help="write assembled prompts here instead of stdout")
    ap.add_argument("--save-replies", default=None)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--skip-install", action="store_true",
                    help="do not pre-install the corpus's DECLARED targets")
    ap.add_argument("--no-install-chosen", action="store_true",
                    help="do not install the library the MODEL chose before "
                         "verifying its attempt. Reproduces the original "
                         "behaviour, in which a free-choice migration to any "
                         "package outside the corpus's declared seven was "
                         "scored a failure for being absent. Off by default "
                         "because that is a harness artefact, not a model "
                         "failure; on, if you would rather not pip-install a "
                         "package name an LLM chose.")
    args = ap.parse_args(argv)

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    model = args.model or S.DEFAULT_MODELS.get(args.llm, "")
    if not model:
        print(f"no default model for provider {args.llm!r}; pass --model",
              file=sys.stderr)
        return 2
    csv_path = args.csv or os.path.join(corpus, "reporting",
                                        f"results_{args.llm}.csv")
    programs = H.discover_programs(corpus)
    if args.program:
        wanted = set(args.program)
        programs = [p for p in programs if p.name in wanted]
    if not programs:
        print("no programs matched", file=sys.stderr)
        return 2

    print(f"config   : {CONFIG} ({args.llm}/{model})")
    print(f"feedback : {args.feedback_source}")
    print(f"programs : {len(programs)}   runs each: {args.runs}")
    print(f"csv      : {csv_path}")

    missing: list[str] = []
    if not args.skip_install and not args.dry_run:
        status = H.ensure_targets(programs)
        missing = [p for p, ok in status.items() if not ok]
        have = sum(1 for ok in status.values() if ok)
        print(f"targets  : {have}/{len(status)} importable"
              + (f"   MISSING: {', '.join(missing)}" if missing else ""))
    print()
    if not args.dry_run:
        print(f"{'program':30s} {'run':>3} {'a1':>3} {'fin':>4} {'rep':>4} "
              f"{'calls':>5} {'tests':>9} {'time':>6}")
        print("-" * 74)

    ok = ok1 = 0
    for program in programs:
        for run_id in range(args.start_run, args.start_run + args.runs):
            row = run_one(program, run_id, model, args.llm, args.clone_dir,
                          args.dry_run,
                          feedback_source=args.feedback_source,
                          reply_dir=args.save_replies or "",
                          prompt_dir=args.prompt_dir or "",
                          install_chosen=not args.no_install_chosen)
            if (not program.reimplement) and program.to_lib in missing:
                row["notes"] = (f"TARGET {program.to_lib} NOT INSTALLABLE — "
                                f"failure not attributable to the model. "
                                + str(row.get("notes", "")))[:200]
            if args.dry_run:
                print(f"{program.name:30s} run{run_id}  prompts assembled, "
                      f"blinding verified")
                continue
            H.append_row(csv_path, row)
            ok += row["migration_success"] == "Y"
            ok1 += row["attempt1_migration_success"] == "Y"
            print(f"{program.name:30s} {run_id:3d} "
                  f"{row['attempt1_migration_success']:>3} {row['migration_success']:>4} "
                  f"{row['repair_attempted']:>4} {row['llm_calls']:>5} "
                  f"{str(row['tests_passed'])+'/'+str(row['tests_total']):>9} "
                  f"{row['wall_time_s']:>5}s", flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    if args.dry_run:
        print(f"\n{len(programs)} program(s) x {args.runs} run(s): "
              f"prompts assemble and contain no declared target")
        return 0
    total = len(programs) * args.runs
    print(f"\nattempt 1 migration_success {ok1}/{total} ({ok1/total:.0%})")
    print(f"final     migration_success {ok}/{total} ({ok/total:.0%})")
    print(f"rows appended to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
