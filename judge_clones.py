#!/usr/bin/env python3
"""LLM-as-judge: read every saved clone and report defects the harness cannot see.

Why
───
Every automated measure in this evaluation can be satisfied by code that does
not work.

  * ``tests_passed`` — the corpus injects fakes into every test, so the factory
    that performs the substitution is frequently never executed by the suite.
  * ``oracle_equiv`` — golden vectors and invariants exist only for C and D.
    On A and B there are no probes at all.
  * ``factory_path`` — layer 5 executes the factory, but only where the adopted
    package is installed and importable.
  * ``struct_*`` / ``iso_*`` — these measure how much the code MOVED. A clone
    can be maximally diverse and completely wrong.

What it is NOT
──────────────
Not a correctness proof and not a replacement for the oracle. A model reading
code produces opinions, and opinions are wrong sometimes. It is used here as a
*comparator*: the same prompt, the same rubric, and the same blinding applied
to all three arms, so "does AXIOM produce better clones than one prompt?" gets
an answer that does not depend on which measures happen to cover which
category. Treat an individual verdict as a lead to check, not a fact.

Blinding
────────
The judge is never told which system produced a clone. That matters more than
it sounds: told the arm, a model will reason backwards from the label and
manufacture a justification for the verdict it expects. Two leaks are removed
before sending (see ``blind``), and ``leaks`` re-checks the final text and
refuses to send if anything survived — a silent blinding failure would
invalidate the comparison while leaving the output looking perfectly normal.

Cost and resumability
─────────────────────
One call per row, cached by clone content hash in ``--cache``. Re-running costs
nothing for rows already judged, so an interrupted batch resumes where it
stopped and a re-run after editing only some clones re-judges only those.

    # see the exact prompt for one row, no API calls, no spending
    python3 judge_clones.py --csv ~/Downloads/evaluation/reporting/results_FINAL.csv \\
        --corpus ~/Downloads/evaluation --dry-run --limit 1 --show-prompt

    # the real pass
    python3 judge_clones.py --csv ~/Downloads/evaluation/reporting/results_FINAL.csv \\
        --corpus ~/Downloads/evaluation --clones clones

Idempotent: re-running writes the same values from the same clones.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.eval import proteus_harness as H

#: Judge model. Deliberately NOT the model under test: asked to grade its own
#: output a model tends to agree with itself, and every clone in this
#: evaluation was written by claude-sonnet-5. A stronger, different model
#: reading the code is the cheapest available guard against that bias. Override
#: with --model if the comparison should instead hold the model fixed.
DEFAULT_JUDGE_MODEL = "claude-opus-5"

#: Temperature 0. Unlike generation — where three seeds exist precisely to
#: produce variance — a judge that changes its mind between runs cannot be
#: audited, and the cache would be keyed to a verdict that no longer reproduces.
JUDGE_TEMPERATURE = 0.0

#: Severity ladder, worst first. The row's verdict is the severity of its worst
#: finding, so the ordering here IS the aggregation rule.
SEVERITIES = ("BROKEN", "MAJOR", "MINOR")

#: NO-OP is not a judgement the model makes — it is imposed from the measured
#: row (see ``is_noop``). A clone that never changed the code is trivially
#: "behaviourally faithful" and the judge correctly finds nothing wrong with
#: it, so without this the laziest possible output scores the best possible
#: verdict. The first trial run hit exactly that: three proteus_no_verif clones
#: came back byte-identical to their originals and swept 100% CLEAN.
VERDICTS = SEVERITIES + ("CLEAN", "NO-OP", "VENDORED", "NO-VERDICT")

#: Columns this script owns. Three, not eight: a judgement nobody reads is
#: worse than no judgement, and the raw findings live in the cache file for
#: anyone who wants the detail.
JUDGE_COLUMNS = ("judge_verdict", "judge_findings", "judge_detail",
                 # Not decidable from the code alone -- see
                 # `hallucination_from_findings`. The runners write NA.
                 "hallucination")


PROMPT = """\
You are reviewing a Python program that was rewritten to remove its dependency \
on `{from_lib}`. The rewrite was supposed to keep the program FUNCTIONALLY \
EQUIVALENT to the original.

You are shown the original program and the rewritten program. Review the \
rewrite the way you would review a colleague's pull request before it ships to \
production hardware.

Report only CONCRETE defects — a specific place where the rewritten program \
would behave differently from the original, or would fail outright. For each \
one, name the file, quote the offending line, and state what actually goes \
wrong at runtime.

Things that are worth reporting:
- an exception type, message, or class that callers can observe changing
- an error that is now swallowed, or one that is now raised where it was not
- a timeout, retry, backoff, or reconnect that was dropped or altered
- a parameter that is accepted and then ignored
- a numeric result that differs: rounding, truncation, integer vs float, \
endianness, signedness, units
- a boundary case handled differently: empty input, None, zero, negative, \
overflow, malformed input
- an API of the new library called incorrectly, or with arguments that do not \
mean what the original's arguments meant
- state, ordering, or concurrency behaviour that changed
- a resource that is no longer closed or released

Things that are NOT defects — do not report these:
- style, naming, formatting, type hints, comments, or docstrings
- the code being longer, shorter, or structured differently
- a different library or a different approach being chosen, when the \
observable behaviour is the same
- anything you are speculating about rather than reading in the code

Severity:
- BROKEN — will not run, or fails on ordinary input
- MAJOR  — runs, but produces wrong results or wrong behaviour in a case the \
original handled
- MINOR  — a real observable difference, but in an edge case or on a path that \
rarely matters

If the rewrite is behaviourally faithful, say so and return an empty findings \
list. An empty list is a perfectly good answer and is expected to be common; \
do not invent a defect to appear thorough.

State each finding DIRECTLY in the `problem` field: what differs, and what goes \
wrong because of it. Do not reason, weigh alternatives, or correct yourself \
there. If you work through a possibility and conclude the behaviour actually \
matches, that is not a finding — leave it out entirely rather than writing it up \
and appending "so no divergence". Several verdicts have been wasted on findings \
whose own text ended by withdrawing the claim.

Reply with JSON only, in exactly this shape:

{{"findings": [{{"file": "<filename>", "line": "<the offending line, quoted>", \
"severity": "BROKEN|MAJOR|MINOR", "problem": "<what goes wrong at runtime, one \
sentence>"}}], "summary": "<one sentence overall>"}}

=== ORIGINAL ===
{original}

=== REWRITTEN ===
{rewritten}
"""

#: Appended to the prompt for reimplement subjects (C1, C2) only.
#:
#: Without it the rubric is unsatisfiable for them. The prompt lists "an
#: exception type, message, or class that callers can observe changing" as a
#: reportable defect -- correct when one library replaces another, and impossible
#: to avoid when the library is removed outright, because first-party code cannot
#: raise `transitions.core.MachineError` without importing the package it was
#: told to delete.
#:
#: Measured, not assumed. A hand-rolled FSM verified three ways -- it passes the
#: corrected oracle, handles both halves of the refusal behaviour, and matches
#: the real library across 300 differential fuzz scripts -- was judged MAJOR on
#: exactly one finding: RuntimeError instead of MachineError. Its own summary
#: said "behaviour is otherwise faithful". Meanwhile three clones that VENDORED
#: the library verbatim scored CLEAN, because copied code raises the copied
#: exception.
#:
#: So the rubric scored the correct answer below the one the provenance guard
#: exists to forbid, and the CLEAN rate for these two subjects was structurally
#: capped at zero. Every C1 and C2 verdict recorded before this note was added
#: carries that bias.
REIMPLEMENT_NOTE = """

IMPORTANT — this rewrite REMOVES `{from_lib}` entirely and reimplements its \
behaviour as first-party code inside this same program. No replacement library \
is adopted. That changes what counts as a defect:

- The rewrite CANNOT raise `{from_lib}`'s own exception classes, because it must \
not import `{from_lib}`. A different exception type raised at the same point, \
with the same effect on control flow, is CORRECT. Do not report it.
- What matters about errors is WHETHER and WHEN they are raised, not their \
class: an error the original raised and the rewrite swallows IS a defect, and so \
is an error the rewrite raises where the original returned a value instead.
- The rewrite no longer importing `{from_lib}` is the point of the exercise, not \
a finding.
- Reproducing `{from_lib}`'s source verbatim is NOT a way to be faithful. If the \
rewrite appears to be a copy of the library rather than an implementation of the \
behaviour the program uses, say so as a finding.
"""



# ── blinding ────────────────────────────────────────────────────────────────

#: AXIOM leaves its name in two places: compatibility-shim identifiers
#: (``_axiom_read_registers``, ``_AxiomModbusResult``) and banner comments the
#: connector writes (``# AXIOM CONNECTOR: smtplib -> apprise``). An earlier
#: version matched only the identifier form and the batch aborted on the
#: comment form 59 rows in.
#:
#: So: match the bare word anywhere, in any case, and rename it consistently.
#: Renaming rather than deleting still matters — the identifiers are called
#: elsewhere in the file, and a deleted token would hand the judge a NameError
#: to report, making the defect count measure our redaction instead of the
#: clone. Case is preserved so ``AXIOM``/``Axiom``/``axiom`` stay distinct
#: tokens after the rewrite and the code remains valid Python.
_ARM_WORD = re.compile(r"[Aa][Xx][Ii][Oo][Mm]")


def _mask(match: re.Match) -> str:
    word = match.group(0)
    if word.isupper():
        return "COMPAT"
    return "Compat" if word[0].isupper() else "compat"

#: single_shot's prompt asks for this marker on the first line. Only that arm
#: emits it, and it also states the library choice, so it goes.
_CHOSEN_TARGET = re.compile(r"^[ \t]*#[ \t]*CHOSEN-TARGET:.*$\n?", re.M)

#: Checked against the FINAL text, after every rewrite above. If any of these
#: survive, the blinding failed and the run is aborted rather than silently
#: producing a compromised comparison.
_LEAK_SIGNS = ("axiom", "proteus", "chosen-target", "single_shot",
               "single-shot", "smart_loader", "logic_substitution")


#: Every other arm-identifying word, masked to something neutral that carries
#: the same meaning. These were previously CHECKED but never MASKED, so a clone
#: containing one aborted the batch instead of being blinded.
#:
#: Measured: a D1 variant's docstring read "Breaks the single-shot supervisory
#: cycle into discrete stages" -- ordinary English for one pass of a control
#: loop, describing the PROGRAM and revealing nothing about which arm wrote it.
#: The run aborted anyway, because the guard tested for the substring.
#:
#: Masking rather than relaxing the guard: the check stays exactly as strict,
#: and the word simply is not there by the time it runs. Weakening the test to
#: let prose through would also let a real leak through, and the whole point of
#: the abort is that a judge able to identify the arm invalidates the batch.
_ARM_PHRASES = (
    (re.compile(r"single[_-]shot", re.I), lambda m: m.group(0)
     .replace("shot", "pass").replace("Shot", "Pass").replace("SHOT", "PASS")),
    (re.compile(r"proteus", re.I), lambda m: "Compat" if m.group(0)[0].isupper()
     else ("COMPAT" if m.group(0).isupper() else "compat")),
    (re.compile(r"smart_loader", re.I), lambda m: "toolkit"),
    (re.compile(r"logic_substitution", re.I), lambda m: "restructure"),
)


def blind(text: str) -> str:
    """Remove every trace of which system wrote this code.

    Renaming rather than stripping is the point: ``_axiom_session_wrapper`` is
    called elsewhere in the file, so deleting the token would hand the judge a
    NameError to report and the defect count would measure our redaction
    instead of the clone.
    """
    text = _CHOSEN_TARGET.sub("", text)
    text = _ARM_WORD.sub(_mask, text)
    for pattern, repl in _ARM_PHRASES:
        text = pattern.sub(repl, text)
    return text


def leaks(text: str) -> list[str]:
    """Arm-identifying tokens still present. Empty means the text is safe."""
    low = text.lower()
    return [s for s in _LEAK_SIGNS if s in low]


# ── assembling one review ───────────────────────────────────────────────────

def read_program(directory: str, names: list[str]) -> str:
    """Concatenate a program's sources into one labelled blob.

    Files are read from ``names`` in sorted order so the original and the clone
    are presented in the same sequence; a judge shown them in different orders
    can mistake a reordering for a change.
    """
    parts = []
    for name in sorted(names):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            source = open(path, encoding="utf-8").read()
        except OSError:
            continue
        parts.append(f"--- {name} ---\n{source.rstrip()}")
    return "\n\n".join(parts)


def clone_files(clone_dir: str, program: H.Program) -> list[str]:
    """Every .py in the clone, not just the ones the original had.

    C-category runs legitimately CREATE modules (``pid_engine.py``,
    ``statekit.py``). Restricting to ``program.source_files`` would hide exactly
    the first-party code the category exists to test, and the judge would be
    reviewing a program with its new logic removed.
    """
    if not os.path.isdir(clone_dir):
        return []
    return [n for n in os.listdir(clone_dir)
            if n.endswith(".py") and not n.startswith("test_")]


def build_prompt(program: H.Program, clone_dir: str) -> tuple[str, list[str]]:
    """Returns (prompt, leaks). A non-empty leak list means do not send."""
    original = blind(read_program(program.path, program.source_files))
    rewritten = blind(read_program(clone_dir, clone_files(clone_dir, program)))
    prompt = PROMPT.format(from_lib=program.from_lib,
                           original=original, rewritten=rewritten)
    if getattr(program, "reimplement", False):
        # Inserted BEFORE the code blocks would be better stylistically, but the
        # rubric has to come after the general one to override it, and the model
        # reads the whole prompt either way.
        prompt += REIMPLEMENT_NOTE.format(from_lib=program.from_lib)
    return prompt, leaks(prompt)


def clone_hash(clone_dir: str, program: H.Program) -> str:
    """Content hash of the clone, so the cache invalidates when code changes.

    Keying on (config, program, run) alone would serve a stale verdict after a
    re-run overwrote the clone — the row would carry a judgement of code that
    no longer exists.
    """
    blob = read_program(clone_dir, clone_files(clone_dir, program))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── verdict extraction ──────────────────────────────────────────────────────

#: Phrases that mark a finding as withdrawn by its own text. Drawn from the
#: actual replies in this session, not imagined: the model uses the `problem`
#: field as a scratchpad, works through a possibility, concludes the behaviour
#: matches after all, and still returns the entry with a severity attached --
#: which then sets the whole row's verdict.
#:
#: Measured: 3 of 20 verdicts had EVERY finding withdrawn this way, including
#: one on a clone independently confirmed faithful by the oracle, a 300-script
#: differential fuzz and a direct surface comparison. Its summary read "no
#: concrete divergence was found" and its verdict was MAJOR.
WITHDRAWAL_PHRASES = (
    "so no divergence", "no divergence", "this is not the issue",
    "so behaviour is equivalent", "behaviour is equivalent",
    "no defect here", "so no defect", "so this matches", "this matches",
    "identical control flow", "so behaviour matches", "matches upstream",
    "no other concrete",
    # The model sometimes marks the retraction explicitly. "(withdrawn)" is
    # unambiguous and was missing, which let a finding ending "the real
    # divergence is none... (withdrawn)" set a MAJOR verdict.
    "withdrawn", "divergence is none", "behaviour is identical",
    "no divergence actually occurs", "identical here",
)


#: Phrases in the SUMMARY that state the reviewer found nothing. Used only
#: alongside fully-retracted findings, so the clean reading needs two
#: independent signals rather than one heuristic over prose.
CLEAN_SUMMARY_PHRASES = (
    "no concrete behavioural divergence", "no genuine behavioural divergence",
    "no concrete divergence", "no genuine divergence",
    "no behavioural divergence", "no concrete defect",
    "found no concrete", "no divergence found", "no divergences found",
    "i found no", "found no behavioural",
)


def summary_affirms_clean(summary: str) -> bool:
    """True when the judge's own summary says it found nothing."""
    text = (summary or "").lower()
    return any(phrase in text for phrase in CLEAN_SUMMARY_PHRASES)


#: Words that mean a defect is still being asserted. If any of these follows the
#: withdrawal phrase, the phrase was not a withdrawal: "this matches the original
#: on the first call, but every later call raises ZeroDivisionError" opens with a
#: match and ends with a real defect.
DEFECT_INDICATORS = (
    "rais", "crash", "wrong", "differ", "diverg", "fail", "error", "instead",
    "swallow", "never", "incorrect", "breaks", "exception",
)


def finding_withdrawn(problem: str) -> bool:
    """True when a finding's own text retracts it.

    A withdrawal is a CONCLUSION, so it must be the last thing said: the phrase
    has to appear in the text, and nothing after it may assert a defect. A
    trailing-window check alone was too loose -- it flagged "this matches the
    original on the first call, but every later call ... raises
    ZeroDivisionError", which is a real finding that happens to open with a
    match.
    """
    text = (problem or "").lower()
    for phrase in WITHDRAWAL_PHRASES:
        position = text.rfind(phrase)
        if position == -1:
            continue
        after = text[position + len(phrase):]
        if not any(word in after for word in DEFECT_INDICATORS):
            return True
    return False


def all_findings_withdrawn(findings: list) -> bool:
    """True when there is at least one finding and every one retracts itself."""
    if not findings:
        return False
    return all(finding_withdrawn(str(f.get("problem", ""))) for f in findings)


#: Phrases in a finding that mean the code named something that does not exist,
#: as opposed to using something real incorrectly. Drawn from findings this
#: judge actually produced: "there is no module named `_Result`", "redmail's
#: EmailSender has no `sendmail` method", "aiomqtt has no `Session` class".
_INVENTED_SIGNS = (
    r"no module named",
    r"does not exist",
    r"non-?existent",
    # "has no attribute", "has no sendmail method", "has no Session class" --
    # the identifier sits between the verb and the noun, so a literal list of
    # phrases misses most real findings.
    r"has no \w*\s?(attribute|method|class|function|member)",
    r"no such (method|attribute|class|function)",
    r"does not have (a |an )?\w*\s?(attribute|method|class|function)",
    r"is not a valid",
    r"\binvented\b",
    r"no longer exists",
    r"is not defined",
    r"modulenotfounderror",
)

#: Phrases that mean the opposite: the API is real, the call is simply wrong.
#: Checked first, so "passes an unsupported keyword to a real method" is not
#: counted as a hallucination.
_REAL_BUT_MISUSED = (
    "unexpected keyword argument", "signature", "wrong order", "different "
    "default", "returns a different", "semantics differ", "timeout",
)


def hallucination_from_findings(findings: list) -> str:
    """``Y``/``N`` -- did the model name an API or module that does not exist?

    The guide asks whether the model "invented a nonexistent API/library". That
    is not decidable from the source alone: a call to something that does not
    exist and a call to something real with the wrong arguments are the same
    text until one of them runs. The old column tried anyway, by grepping the
    test output for "has no attribute" / "ModuleNotFoundError", and answered N
    on 140 of 144 rows -- including rows where the judge had written, in plain
    words, that the clone imported a module that does not exist.

    So it is answered from the judge's findings instead, which are produced by
    something that read the whole clone. It is an opinion rather than a
    measurement, and it is recorded as one: absent a judge verdict the column
    is NA, never N.
    """
    import re

    for finding in findings or []:
        text = f"{finding.get('problem', '')} {finding.get('line', '')}".lower()
        if any(sign in text for sign in _REAL_BUT_MISUSED):
            continue
        if any(re.search(pattern, text) for pattern in _INVENTED_SIGNS):
            return "Y"
    return "N"


def verdict_of(findings: list) -> str:
    """Worst finding wins. No findings means CLEAN."""
    present = {str(f.get("severity", "")).upper() for f in findings}
    for level in SEVERITIES:
        if level in present:
            return level
    # Findings that all carry an unrecognised severity are still findings.
    return "MINOR" if findings else "CLEAN"


def _repair_json_shape(text: str):
    """Recover a verdict whose JSON is well formed except for missing braces.

    Observed on a D1 review. The reply carried two ``{``, one ``}`` and no
    ``]`` at all: the model wrote ``summary`` as a field INSIDE the finding
    object and then closed a single brace, leaving the finding, the findings
    array and the outer object all open::

        {"findings": [{"file": ..., "problem": "...NaN.", "summary": "No
        behavioural divergence found..."}

    Two repairs, both purely structural:

    1. Close whatever containers are still open at the end of the text. This is
       what every JSON-repair library does with a truncated reply.
    2. If the result then has no top-level ``summary`` and the findings carry
       one, lift it. The schema in the prompt puts ``summary`` at top level, so
       a ``summary`` nested in a finding is a misplaced brace, not data.

    Neither step can manufacture a pass. The repair is refused unless at least
    one real finding was recovered, so it can never produce the EMPTY findings
    list that reads as CLEAN -- the one outcome ``parse_verdict`` exists to
    prevent. Lifting the summary is verdict-neutral in the same way: it hands
    the text to ``summary_affirms_clean``, which is free to reject it, and a
    lifted summary describing a defect leaves the verdict exactly where it was.

    Returns the repaired ``dict``, or None.
    """
    import json

    opening = text.find("{")
    if opening < 0:
        return None
    stack: list = []
    in_str = esc = False
    for ch in text[opening:]:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if not stack:
                return None
            stack.pop()
    if in_str or not stack:
        return None                      # nothing missing, or a broken string
    closed = text[opening:] + "".join("]" if c == "[" else "}"
                                      for c in reversed(stack))
    try:
        data = json.loads(closed)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    findings = data.get("findings")
    if not isinstance(findings, list) or not any(isinstance(f, dict)
                                                 for f in findings):
        # No finding survived, so there is nothing to recover and an empty
        # list here would be indistinguishable from a clean review.
        return None

    if not str(data.get("summary", "")).strip():
        for f in findings:
            if isinstance(f, dict) and str(f.get("summary", "")).strip():
                data["summary"] = f.pop("summary")
                break
    return data


def parse_verdict(text: str) -> dict | None:
    """Pull the JSON verdict out of a reply. None if it cannot be read.

    A reply we cannot parse is recorded as such rather than being coerced into
    CLEAN — "the judge said nothing was wrong" and "we could not read the
    judge's answer" are opposite facts and must not share a value.
    """
    from smart_loader.core.llm_json import extract_json

    data = extract_json(text, expect=dict)
    findings = data.get("findings") if isinstance(data, dict) else None

    if not isinstance(findings, list):
        # A bare top-level array is the one alternative shape worth accepting:
        # it is unambiguously the findings list, so reading it loses nothing.
        # Anything else stays an error — inventing an empty findings list from
        # an unreadable reply would manufacture a CLEAN verdict, which is the
        # single most damaging thing this script could get wrong.
        # A reply that is well formed except for braces the model dropped is
        # repaired FIRST, because the bare-array fallback below keeps only the
        # array and silently discards the summary. That loss is not cosmetic:
        # when every finding retracts itself, the summary is the second signal
        # deciding CLEAN vs NO-VERDICT, so one dropped brace was enough to turn
        # a clean review into no verdict at all. `_repair_json_shape` can only
        # put a finding back, never produce the empty list that reads CLEAN.
        repaired = _repair_json_shape(text)
        if isinstance(repaired, dict) and isinstance(repaired.get("findings"), list):
            data, findings = repaired, repaired["findings"]
        else:
            listed = extract_json(text, expect=list)
            if not isinstance(listed, list):
                return None
            data, findings = {}, listed
    clean = [f for f in findings if isinstance(f, dict)]
    return {"findings": clean,
            "summary": str(data.get("summary", "")).strip(),
            "verdict": verdict_of(clean)}


def is_vendored(program, clone_dir: str) -> tuple:
    """True when the clone reproduces the outgoing library's own source.

    The judge cannot see this, and the failure is not its fault: it is asked
    "does this behave differently from the original?", and a verbatim copy of
    the dependency behaves IDENTICALLY. CLEAN is the correct answer to that
    question and a completely wrong verdict. A C1 round returned 3/3 CLEAN on
    three byte-identical clones that contained 73% of `simple_pid.PID`'s code
    lines in runs of four or more.

    So this is the exact counterpart of ``is_noop``: both catch a clone that
    earns a good verdict without doing the work, and both are measured from the
    files rather than asked of a model. A no-op changed nothing; a vendored copy
    changed plenty and wrote someone else's code while doing it.

    Returns ``(vendored, overlap)``. ``overlap`` is 0.0 when the library's source
    cannot be read, which is reported as no evidence rather than as a pass.
    """
    from smart_loader.agents.first_party_agent import (
        MAX_VERBATIM_OVERLAP, _verbatim_overlap)

    outgoing = getattr(program, "from_lib", "") or ""
    if not outgoing or not getattr(program, "reimplement", False):
        # Only the reimplement subjects are asked to write the behaviour
        # themselves. On a library-substitution subject, resembling the OUTGOING
        # package is neither expected nor meaningful.
        return False, 0.0

    worst = 0.0
    for name in getattr(program, "source_files", ()) or ():
        if not name.endswith(".py") or os.path.basename(name).startswith("test_"):
            continue
        path = os.path.join(clone_dir, name)
        if not os.path.isfile(path):
            continue
        code = open(path, encoding="utf-8", errors="replace").read()
        worst = max(worst, _verbatim_overlap(code, outgoing, code))
    return worst > MAX_VERBATIM_OVERLAP, worst


def is_noop(row: dict) -> bool:
    """True when the harness measured no first-party change at all.

    Read from the row rather than asked of the judge, for two reasons. It is
    already measured exactly — ``pct_firstparty_changed`` and ``loc_changed``
    come from a diff, not an opinion — and asking the model to also police
    whether work happened would blur the one question it is good at
    ("does this behave differently?") with one arithmetic answers better.
    """
    def zero(name: str) -> bool:
        raw = (row.get(name) or "").strip()
        try:
            return float(raw) == 0.0
        except ValueError:
            return False

    if zero("pct_firstparty_changed") or zero("loc_changed"):
        return True

    # A clone can differ from the original by a few lines and still have
    # migrated nothing. C3 kept three of its four files byte-identical and
    # changed seven lines of docstring and one comment in the fourth, then
    # relabelled the file `# CHOSEN-TARGET: secure-smtplib` while still
    # importing stdlib smtplib -- loc_changed was 7, so the line-count test
    # above passed it through, and the reviewer correctly returned CLEAN
    # because the behaviour genuinely is unchanged.
    #
    # That CLEAN is right and meaningless at the same time. The reviewer is
    # asked "is this faithful to the original?", and a clone that did not
    # migrate is trivially faithful -- it IS the original. Counting it beside
    # clones that actually replaced a library rewards not doing the work, and
    # ranks the arms in reverse order of how much migrating they did.
    #
    # So the real test of "nothing was migrated" is the dependency itself:
    # lib_swapped_correctly is measured from the clone's imports, in the same
    # harness, for every arm. Blank means the column was never computed for
    # this row, which is not evidence of a no-op, so only an explicit N counts.
    if (row.get("lib_swapped_correctly") or "").strip().upper() == "N":
        return True
    return False


def detail_of(result: dict) -> str:
    """One-line CSV cell: the worst finding, or the summary when clean."""
    findings = result["findings"]
    if not findings:
        return result["summary"][:200]
    worst = result["verdict"]
    for f in findings:
        if str(f.get("severity", "")).upper() == worst:
            where = str(f.get("file", "")).strip()
            problem = str(f.get("problem", "")).strip()
            return f"{where}: {problem}"[:200] if where else problem[:200]
    return result["summary"][:200]


# ── cache ───────────────────────────────────────────────────────────────────

def load_cache(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue          # a half-written line from an interrupted run
            if "key" in rec:
                out[rec["key"]] = rec
    return out


def append_cache(path: str, record: dict) -> None:
    """Append immediately after each call, not in a batch at the end.

    A run interrupted at row 90 of 108 must not throw away 90 paid API calls.
    """
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ── trial sampling ──────────────────────────────────────────────────────────

def sample_keys(rows: list, size: int) -> set:
    """Pick ~``size`` rows as whole (program, run) groups, spread over categories.

    A bare head-of-file limit is the wrong trial here. The CSV is ordered by
    arm and then by category, so ``--limit 9`` buys nine single_shot
    category-A verdicts: one arm, the three easiest programs, and no way to see
    whether the judge distinguishes the arms at all — which is the only thing a
    trial needs to establish before committing to the full spend.

    So: choose a (program, run_id) and take EVERY arm's clone of it, because
    the same task judged three ways is what makes the verdicts comparable. And
    rotate D, C, B, A so the hard categories are represented first — a judge
    that looks discerning on category A has proved very little.
    """
    groups: dict = {}
    for row in rows:
        groups.setdefault((row["program"], row["run_id"]), []).append(row)

    by_category: dict = {}
    for key in groups:
        by_category.setdefault(key[0][0].upper(), []).append(key)
    for keys in by_category.values():
        keys.sort()

    chosen: set = set()
    taken = 0
    # Hardest first: D exercises the whole chain, A the least of it.
    order = [c for c in "DCBA" if c in by_category]
    cursor = {c: 0 for c in order}
    while taken < size and order:
        for category in list(order):
            keys = by_category[category]
            if cursor[category] >= len(keys):
                order.remove(category)
                continue
            key = keys[cursor[category]]
            cursor[category] += 1
            chosen.add(key)
            taken += len(groups[key])
            if taken >= size:
                break
    return chosen


# ── main ────────────────────────────────────────────────────────────────────

#: Failures that are properties of the API, not of the clone under review.
#: Retrying these is honest — the verdict does not change because the service
#: was busy. Four rows were lost to 529s in the first full pass, and a missing
#: verdict is not neutral: it silently shrinks one arm's denominator.
_TRANSIENT = ("overloaded", "529", "rate limit", "429", "too many requests",
              "timeout", "timed out", "connection", "502", "503", "504",
              "bad gateway", "service unavailable")


def judge_one(prompt: str, provider: str, model: str,
              attempts: int = 4) -> tuple[dict | None, str, dict]:
    """One call, retried only on transient API failures."""
    from smart_loader.core.llm_factory import _get_llm
    from smart_loader.core.llm_json import coerce_llm_text

    llm = _get_llm(provider=provider, model=model,
                   temperature=JUDGE_TEMPERATURE, disable_thinking=True)
    started = time.time()
    delay = 8.0
    for attempt in range(1, attempts + 1):
        try:
            reply = llm.invoke(prompt)
            # An EMPTY completion is a transient failure that does not raise:
            # the call succeeded and returned nothing. One row came back at
            # 0 chars on an otherwise ordinary 9,390-character prompt with the
            # clone fully present. Retrying only on exceptions missed it, and
            # a silently dropped verdict shrinks one arm's denominator without
            # announcing itself.
            if coerce_llm_text(getattr(reply, "content", reply)).strip():
                break
            if attempt == attempts:
                break
            print(f"    empty reply; retrying in {delay:.0f}s "
                  f"({attempt}/{attempts})", flush=True)
        except Exception as exc:                          # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if attempt == attempts or not any(s in text for s in _TRANSIENT):
                raise
            print(f"    transient ({type(exc).__name__}); retrying in "
                  f"{delay:.0f}s ({attempt}/{attempts})", flush=True)
        time.sleep(delay)
        delay *= 2
    elapsed = time.time() - started
    text = coerce_llm_text(getattr(reply, "content", reply))
    usage = getattr(reply, "usage_metadata", None) or {}
    tin = int(usage.get("input_tokens", 0) or 0)
    tout = int(usage.get("output_tokens", 0) or 0)
    return parse_verdict(text), text, {
        "wall_time_s": round(elapsed, 1), "tokens_in": tin, "tokens_out": tout,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--clones", default="clones")
    ap.add_argument("--cache", default="judge_cache.jsonl",
                    help="verdicts keyed by clone content hash; re-runs are free")
    ap.add_argument("--llm", default="claude")
    ap.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--config", action="append",
                    help="limit to one arm (repeatable); default is all")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N calls; NOTE the CSV is ordered by arm "
                         "then category, so a bare --limit takes the first arm's "
                         "easiest programs. Prefer --sample for a trial.")
    ap.add_argument("--sample", type=int, default=0,
                    help="judge ~N rows chosen as whole (program, run) groups "
                         "across ALL arms and spread over categories, so a "
                         "costed trial can actually compare arms")
    ap.add_argument("--dry-run", action="store_true",
                    help="build prompts and check blinding; no API calls")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the first prompt in full, then continue")
    args = ap.parse_args(argv)

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    clones = os.path.abspath(os.path.expanduser(args.clones))
    programs = {p.name: p for p in H.discover_programs(corpus)}
    if not programs:
        print(f"no programs discovered under {corpus}", file=sys.stderr)
        return 2

    with open(args.csv, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys())
    for column in JUDGE_COLUMNS:
        if column not in fields:
            fields.append(column)

    cache = load_cache(args.cache)
    if cache:
        print(f"cache: {len(cache)} verdicts already recorded\n")

    wanted = None
    if args.sample:
        eligible = [r for r in rows
                    if r["program"] in programs
                    and not (args.config and r["config"] not in args.config)]
        wanted = sample_keys(eligible, args.sample)
        print(f"trial sample: {len(wanted)} (program, run) group(s) across all "
              f"arms — {', '.join(sorted(p for p, _ in wanted))}\n")

    calls = cached = skipped = failed = 0
    shown = False
    counts: dict = {}

    for row in rows:
        program = programs.get(row["program"])
        if program is None:
            continue
        if args.config and row["config"] not in args.config:
            continue
        if wanted is not None and (row["program"], row["run_id"]) not in wanted:
            continue
        clone_dir = os.path.join(clones, row["config"],
                                 f"{row['program']}_run{row['run_id']}")
        if not os.path.isdir(clone_dir):
            skipped += 1
            continue

        prompt, found = build_prompt(program, clone_dir)
        if found:
            # Hard stop, not a warning. A leak means every verdict from here on
            # is comparing arms the judge can identify, which is precisely the
            # bias this design exists to exclude.
            print(f"BLINDING LEAK in {row['config']}/{row['program']}"
                  f"_run{row['run_id']}: {found}", file=sys.stderr)
            print("aborting; fix blind() before spending on this batch",
                  file=sys.stderr)
            return 3

        if args.show_prompt and not shown:
            print("=" * 70)
            print(prompt)
            print("=" * 70 + "\n")
            shown = True

        key = f"{row['config']}|{row['program']}|{row['run_id']}|" \
              f"{clone_hash(clone_dir, program)}"
        record = cache.get(key)

        if record is None:
            if args.dry_run:
                continue
            if args.limit and calls >= args.limit:
                continue
            label = (f"{row['config']:<18} {row['program']:<28} "
                     f"run{row['run_id']}")
            try:
                parsed, raw, tel = judge_one(prompt, args.llm, args.model)
            except Exception as exc:                      # noqa: BLE001
                print(f"  {label}  ERROR {type(exc).__name__}: {exc}",
                      flush=True)
                failed += 1
                continue
            calls += 1
            if parsed is None:
                # Recorded, and recorded as unreadable. Caching the failure
                # would freeze it; leaving it uncached lets a re-run retry.
                #
                # The raw reply is written to disk because the alternative is
                # guessing. "The model produced findings in a shape we did not
                # expect", "the model refused", and "the reply was truncated"
                # demand different fixes and are indistinguishable from a
                # character count. run_single_shot.py learned this the same way.
                where = os.path.join("judge_failures",
                                     f"{row['config']}_{row['program']}"
                                     f"_run{row['run_id']}.txt")
                os.makedirs("judge_failures", exist_ok=True)
                with open(where, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                print(f"  {label}  UNPARSEABLE ({len(raw)} chars) -> {where}",
                      flush=True)
                row["judge_verdict"] = "ERROR"
                row["judge_findings"] = ""
                row["judge_detail"] = "judge reply could not be parsed"
                failed += 1
                continue
            record = {"key": key, **parsed, **tel}
            append_cache(args.cache, record)
            cache[key] = record
            # Print the RECLASSIFIED verdict, not the model's raw one. The
            # raw line said "CLEAN  0 finding(s)" for a clone that had migrated
            # nothing, while the CSV and the summary table both recorded NO-OP
            # -- correctly, but the log is what a person actually reads, and it
            # invited exactly the wrong conclusion.
            vendored_now, overlap_now = is_vendored(program, clone_dir)
            shown_verdict = ("VENDORED" if vendored_now
                             else "NO-OP" if is_noop(row)
                             else parsed["verdict"])
            suffix = ""
            if vendored_now:
                suffix = (f"  (judge said {parsed['verdict']}; {overlap_now:.0%} "
                          f"of the library copied verbatim, which behaves "
                          f"identically by construction)")
            elif shown_verdict == "NO-OP" and parsed["verdict"] != "NO-OP":
                suffix = (f"  (judge said {parsed['verdict']}; nothing was "
                          f"migrated, so that verdict carries no information)")
            print(f"  {label}  {shown_verdict:<7} "
                  f"{len(parsed['findings'])} finding(s){suffix}", flush=True)
        else:
            cached += 1

        verdict, detail = record["verdict"], detail_of(record)
        if all_findings_withdrawn(record.get("findings") or []):
            # Every finding retracts itself. What that means depends on whether
            # the summary agrees:
            #
            #   summary also says it found nothing  -> CLEAN. Two independent
            #       signals point the same way, and calling it NO-VERDICT would
            #       throw away a real clean verdict. Observed on all three C2 v3
            #       clones, each independently confirmed by a 300-script fuzz.
            #   summary ambiguous or claims a defect -> NO-VERDICT. The judge
            #       contradicted itself, which is a different fact from "the code
            #       is faithful"; the two must not share a value. Scoring it
            #       MAJOR was worse still, asserting a defect the judge's own
            #       text had already taken back.
            #
            # The CLEAN branch requires agreement between two signals precisely
            # because a single keyword heuristic over model prose must never be
            # able to manufacture a pass on its own.
            if summary_affirms_clean(record.get("summary", "")):
                verdict = "CLEAN"
                detail = ("judge found no divergence; its findings were "
                          "retracted by their own text")
            else:
                verdict = "NO-VERDICT"
                detail = ("judge returned findings that its own text retracts, "
                          "and its summary does not say it found nothing; "
                          "no usable verdict")
        vendored, overlap = is_vendored(program, clone_dir)
        if vendored:
            # Checked before NO-OP: a vendored clone is not unchanged, it is
            # changed into someone else's code, and the two must not be conflated.
            verdict = "VENDORED"
            detail = (f"{overlap:.0%} of {program.from_lib}'s own code lines "
                      f"reproduced verbatim; the dependency was copied, not "
                      f"reimplemented")
        elif is_noop(row):
            if verdict == "CLEAN":
                # Faithful because nothing was attempted. Recorded as its own
                # outcome so it can never be read as a quality result.
                verdict = "NO-OP"
                detail = "clone is unchanged from the original; nothing migrated"
            else:
                # The judge reported defects in code it was told is identical
                # to the original. That is a judge error, and hiding it would
                # remove the only signal that the judge is unreliable here.
                detail = f"[unchanged clone, judge still reported defects] {detail}"

        row["judge_verdict"] = verdict
        row["judge_findings"] = str(len(record["findings"]))
        row["hallucination"] = hallucination_from_findings(
            record.get("findings") or [])
        row["judge_detail"] = detail
        bucket = counts.setdefault(row["config"], {})
        bucket[verdict] = bucket.get(verdict, 0) + 1

    # ── report ──────────────────────────────────────────────────────────────
    print(f"\n{calls} call(s), {cached} from cache, {skipped} without clones, "
          f"{failed} failed")

    if counts:
        # "clean of attempted", NOT "clean of all". Dividing by every row would
        # reward an arm for the clones it never changed, which is how the first
        # trial made the weakest arm look like the strongest.
        head = (f"\n{'configuration':<20}" +
                "".join(f"{v:>12}" for v in VERDICTS) + f"{'clean/attempted':>17}")
        print(head)
        print("-" * (len(head) - 1))
        for cfg in sorted(counts):
            bucket = counts[cfg]
            clean = bucket.get("CLEAN", 0)
            attempted = sum(n for v, n in bucket.items()
                            if v not in ("NO-OP", "VENDORED", "NO-VERDICT"))
            rate = f"{clean}/{attempted}" if attempted else "0/0"
            print(f"{cfg:<20}" +
                  "".join(f"{bucket.get(v, 0):>12}" for v in VERDICTS) +
                  f"{rate:>17}")
        print("\nNO-OP = nothing was migrated (source unchanged, or the old "
              "dependency is still\n        imported). Not a pass: a clone that "
              "did not migrate is trivially faithful\n        to the original, so "
              "a CLEAN verdict on it carries no information.")
        print("NO-VERDICT = the judge's findings were retracted by their own text "
              "(\"...so\n        behaviour is equivalent\"). Not a pass and not a "
              "failure: excluded from\n        the rate, because no usable "
              "judgement was produced.")
        print("VENDORED = the dependency's own source was copied in rather than "
              "reimplemented.\n        Also not a pass, and invisible to the "
              "judge by construction: copied code\n        behaves identically, "
              "so it earns CLEAN while doing none of the work.")

    if args.dry_run:
        print("\ndry run — no calls made, no CSV written")
        return 0

    with open(args.csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            for column in JUDGE_COLUMNS:
                row.setdefault(column, "")
            writer.writerow(row)
    print(f"\nwrote {len(rows)} rows with {len(JUDGE_COLUMNS)} added columns "
          f"-> {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
