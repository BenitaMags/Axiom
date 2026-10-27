#!/usr/bin/env python3
"""Baseline 1 — ``single_shot``: one LLM prompt, no agents, no verification.

The control arm the paper compares AXIOM against. One prompt goes out, whatever
comes back is the clone, and it is measured exactly as every other arm is
measured. There is deliberately no repair loop, no oracle, and no retry: the
point of a baseline is to show what you get *without* the machinery, so adding
any of it back would quietly make the comparison flattering.

Uses the same model AXIOM runs on, so the only variable between arms is the
system rather than the model.

Usage
─────
    python3 run_single_shot.py --corpus ~/Downloads/evaluation           # all 12
    python3 run_single_shot.py --corpus ... --program A1_crc_telemetry   # just one
    python3 run_single_shot.py --corpus ... --runs 3                     # 3 seeds
    python3 run_single_shot.py --corpus ... --dry-run                    # no API calls

Rows are appended to ``<corpus>/reporting/results.csv``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smart_loader.eval import proteus_harness as H

#: ONE prompt, for every program in the corpus. No per-category variants, no
#: template holes filled from a variable somewhere else — what you read here is
#: what the model receives.
#:
#: The replacement library is never named. The protocol asks what a single
#: prompt achieves when it must decide *what to migrate to*.

PROMPT = """\
You are given a Python program that depends on `{from_lib}`. Rewrite it so the \
dependency on `{from_lib}` is gone and the program remains FUNCTIONALLY \
EQUIVALENT.

### THE TASK FOR THIS PROGRAM

{task}

Exactly one of these two conditions applies. Read the program — including its \
module docstrings and comments — and decide which:

1. REPLACE WITH A LIBRARY (the normal case). Choose a suitable, \
actively-maintained third-party library yourself and migrate to it. Write \
whatever adapter or glue code the difference in API requires. Do not write \
your own implementation of what a library already provides.

2. REIMPLEMENT AS FIRST-PARTY CODE (the exception). Choose this ONLY if the \
program's own documentation states that the dependency is to be removed and \
its behaviour written as first-party code. If the documentation does not say \
that, condition 1 applies.

Requirements:
- Preserve the exact public API: same module names, same function and class \
names, same signatures, same return types. The existing test suite must pass \
against your rewrite unchanged.
- Preserve all numeric constants and thresholds exactly.
- Follow any additional requirement the program's own documentation states.
- On the first line of the first file, record which condition you applied and \
your choice, formatted exactly as `# CHOSEN-TARGET: <pypi-name>` for \
condition 1, or `# CHOSEN-TARGET: first-party` for condition 2.

Return ONLY the rewritten file(s). For each file use exactly this format, with \
no commentary before or after:

### FILENAME: <name.py>
```python
<complete file contents>
```

The program's files are below.

{sources}
"""

#: The corpus files name their own intended target in module docstrings
#: ("substitution target: `pymodbus` -> `minimalmodbus`"). Left in place, a
#: free-choice prompt is not free at all — the answer sits in the input. These
#: are stripped from the COPY sent to the model; the corpus on disk is never
#: modified.
_HINT_RE = re.compile(
    r"^.*?(?:substitution target|migration target)\b.*$"
    r"|^.*?`[\w.-]+`\s*(?:\(stdlib\)\s*)?->\s*`?[\w.-]+`?.*$",
    re.I | re.M)

_FILE_RE = re.compile(
    r"###\s*FILENAME:\s*(?P<name>[\w./-]+\.py)\s*\n+```(?:python)?\s*\n"
    r"(?P<body>.*?)```", re.S | re.I)

#: Sensible default model per provider, so ``--llm openrouter`` alone works.
#: The OpenRouter and NVIDIA entries are free tiers — useful when Anthropic
#: credit has run out, with the caveat that a free tier's rate limits and
#: quantisation are not guaranteed to match the paid model's behaviour, so
#: results are only comparable *within* one provider's CSV.
DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "openrouter": "deepseek/deepseek-chat-v3.1:free",
    "openai": "gpt-4o-mini",
    "ollama": "qwen3-coder-next",
}

#: Per-1M-token pricing used for ``cost_usd``. Free tiers are genuinely 0.0 —
#: recorded as such rather than left blank, because blank means "could not
#: measure" in the guide's convention and 0.0 is a real measurement here.
#: Per-request ceiling. Long enough for a large multi-file prompt on a slow
#: free tier, short enough that one stalled call cannot eat an overnight batch.
REQUEST_TIMEOUT_S = 180

PRICING = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (15.0, 75.0),
}


def list_free_models(limit: int = 25) -> int:
    """Print OpenRouter models that currently cost nothing.

    OpenRouter rotates its free roster — a slug that was free last month can
    return 404 with "the paid version is available now", which is what
    ``deepseek-chat-v3.1:free`` did. Hard-coding a default is therefore a
    guess with a shelf life, so this asks the API what is actually free right
    now and sorts by context length, since the corpus prompts are large.
    """
    import json
    import ssl
    import urllib.request

    # macOS python.org builds ship without a CA bundle wired into urllib, so a
    # plain urlopen raises CERTIFICATE_VERIFY_FAILED even though the LLM calls
    # in this same script work fine — httpx and openai bundle certifi. Use that
    # bundle here too rather than asking the user to run
    # Install Certificates.command, and rather than disabling verification.
    context = None
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models",
                                    timeout=30, context=context) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"could not reach OpenRouter: {type(exc).__name__}: {exc}\n\n"
              f"Browse the current free models here instead:\n"
              f"  https://openrouter.ai/models?max_price=0\n"
              f"then pass one with --model <id>", file=sys.stderr)
        return 1

    free = []
    for m in data.get("data", []):
        pricing = m.get("pricing") or {}
        try:
            cost = float(pricing.get("prompt", 1)) + float(pricing.get("completion", 1))
        except (TypeError, ValueError):
            continue
        if cost == 0.0:
            free.append((int(m.get("context_length") or 0), m.get("id", "")))

    if not free:
        print("no free models reported by the API", file=sys.stderr)
        return 1
    print(f"{len(free)} free models (sorted by context length):\n")
    for ctx, mid in sorted(free, reverse=True)[:limit]:
        print(f"  {ctx:>9,}  {mid}")
    print(f"\nuse one with:  --llm openrouter --model <id>")
    return 0


def strip_target_hints(text: str, target: str = "") -> str:
    """Remove lines that name the intended migration target.

    Two passes. The first drops the explicit "substitution target: X -> Y"
    docstring lines. The second drops any remaining line that mentions the
    target package by name, which catches the prose forms the pattern misses
    ("the `requests`/`httpx` package", "ciso8601 does").

    Stripping every mention is safe precisely because the ORIGINAL programs
    never use their target: they import the library being migrated away from.
    So in an unmodified corpus file, a mention of the target can only be a
    hint about the intended answer.
    """
    text = _HINT_RE.sub("", text)
    root = (target or "").split()[0].split("[")[0].strip("`").lower()
    # Multi-word targets ("in-house controller") are not package names and
    # have no import form to leak, so there is nothing to strip for those.
    if root and root.isidentifier() or (root and "-" in root):
        pattern = re.compile(rf"^.*\b{re.escape(root)}\b.*$", re.I | re.M)
        text = pattern.sub("", text)
    return text


def build_prompt(program: H.Program) -> str:
    """The exact text sent to the model. One shape, every program.

    Target hints are always stripped from the copy of the sources. The corpus
    docstrings name their own intended target ("substitution target:
    `pymodbus` -> `minimalmodbus`"), and leaving those in place would make the
    instruction to choose a replacement meaningless -- the answer would be
    sitting in the input. The corpus on disk is never modified.
    """
    sources = "\n\n".join(
        f"### FILENAME: {name}\n```python\n"
        f"{strip_target_hints(open(os.path.join(program.path, name), encoding='utf-8').read(), program.to_lib)}```"
        for name in program.source_files
    )
    return PROMPT.format(from_lib=program.from_lib, sources=sources,
                         task=task_block(program))


def task_block(program: H.Program) -> str:
    """What this specific program is asked to do, without naming the answer.

    Until this existed the baselines were told only "exactly one of these two
    conditions applies -- read the program and decide which", and had to infer
    the task from docstrings that ``strip_target_hints`` had partly removed.
    ``proteus_full`` and ``proteus_no_verif`` were never asked to infer it:
    ``run_axiom_arms`` hands the graph ``reimplement_mode=program.reimplement``
    and ``require_substitution=not program.reimplement`` directly. Measured on
    C2 -- whose only statement of its task is the ``TO: hand-rolled FSM`` line
    the blinder must strip, because that line also names the answer -- the
    baselines were guessing at a requirement the system was told.

    Built from ``from_lib``, ``reimplement`` and ``category``: the same three
    facts the graph receives, and no more. The declared target is never named,
    which is what keeps "choose a replacement" a real question. The category
    is NOT sufficient on its own -- C1 and C2 are reimplementations and C3 is
    not, all three Category C -- so ``reimplement`` is read directly.
    """
    lines = [f"This program currently depends on: {program.from_lib}"]
    if program.reimplement:
        lines.append(
            "Required outcome: REMOVE that dependency and reimplement its "
            "behaviour as first-party code inside this program. Do not adopt "
            "another third-party library in its place.")
    else:
        lines.append(
            "Required outcome: REPLACE that dependency with a different "
            "third-party library, which you choose. Do not write your own "
            "implementation of what a library already provides.")
    if program.category in ("B", "D"):
        lines.append("The replacement's API differs from the original's, so "
                     "adapter or glue code will be required.")
    if program.category in ("C", "D"):
        lines.append("The program's own first-party logic must also be "
                     "restructured into an equivalent but structurally "
                     "different form.")
    block = "\n".join(lines)
    root = (program.to_lib or "").split()[0].strip("`").lower()
    if root and root in block.lower():
        raise AssertionError(
            f"{program.name}: task block names the declared target {root!r}")
    return block


def parse_files(reply: str) -> dict[str, str]:
    """Pull ``### FILENAME:`` blocks out of the reply.

    A single-shot baseline gets no repair loop, so a reply we cannot parse is a
    real failure of the configuration and is recorded as one — not retried and
    not hand-fixed. Falling back to "grab the first code fence" would quietly
    turn a malformed multi-file answer into a plausible-looking single file and
    inflate the baseline.
    """
    found = {m.group("name").strip(): m.group("body")
             for m in _FILE_RE.finditer(reply)}
    return {os.path.basename(k): v for k, v in found.items()}


_FENCE_RE = re.compile(r"```(?:python)?\s*\n(?P<body>.*?)```", re.S)
_INLINE_NAME_RE = re.compile(r"^#\s*(?:FILENAME:\s*)?(?P<name>[\w./-]+\.py)\s*$",
                             re.M)


def parse_files_lenient(reply: str, program: H.Program) -> dict[str, str]:
    """``parse_files`` plus two unambiguous fallbacks.

    The strict format is what the prompt asks for, and six of the first 22 runs
    ignored it — all on multi-file programs. Recording those as failures is
    defensible, but only if the failure is really "the model did not produce
    the code", not "the model produced the code and labelled it differently".
    Those are different claims, and conflating them overstates the baseline's
    weakness in a way that flatters AXIOM.

    So two fallbacks are allowed, both chosen because they involve no guessing:

    1. A ``# name.py`` comment on the first line inside a fence. The model told
       us the filename, just not where we asked for it.
    2. Exactly one fence for a program with exactly one source file. There is
       only one possible mapping, so no inference is involved.

    Deliberately NOT allowed: matching N fences to N files by position on a
    multi-file program. That *is* a guess — get the order wrong and you write
    valid code into the wrong module, producing a failure the model did not
    commit. Those stay recorded as unparseable.
    """
    strict = parse_files(reply)
    if strict:
        return strict

    fences = [m.group("body") for m in _FENCE_RE.finditer(reply)]
    if not fences:
        return {}

    named: dict[str, str] = {}
    for body in fences:
        head = _INLINE_NAME_RE.search(body[:200])
        if head:
            name = os.path.basename(head.group("name"))
            if name in program.source_files:
                named[name] = body
    if named:
        return named

    if len(fences) == 1 and len(program.source_files) == 1:
        return {program.source_files[0]: fences[0]}
    return {}


#: Failures that are properties of the *infrastructure*, not of the model's
#: answer. Retrying these is honest. Retrying a bad answer would quietly turn
#: the single-shot baseline into a multi-shot one and flatter the comparison,
#: so anything not on this list propagates and is recorded as a real failure.
_TRANSIENT_SIGNS = (
    "rate limit", "429", "too many requests", "quota",
    "temporarily rate-limited",
    # A dropped or refused connection says nothing about the model. Free tiers
    # sever idle connections and OpenRouter routes through upstream providers
    # that go down independently; four of these killed runs in the first batch.
    "connection error", "apiconnectionerror", "connection reset",
    "connection aborted", "timed out", "timeout", "502", "503", "504",
    "bad gateway", "service unavailable", "remote end closed",
    "wall-clock guard",
)


class HardTimeout(Exception):
    """The wall-clock guard fired. Distinct so it is never mistaken for a
    model failure when classifying the row."""


def _invoke_guarded(llm, prompt: str, seconds: float):
    """Invoke with a wall-clock ceiling that does not depend on the client.

    Two previous attempts to bound a call — ``with_config`` and post-hoc
    attribute assignment — both silently did nothing, and a batch sat for eight
    hours on a single request. The client-level timeout is now set correctly at
    construction, but this guard exists so that a hang is impossible even if a
    future provider ignores it: the call runs on a daemon thread and the caller
    gives up after ``seconds`` regardless.

    The abandoned thread cannot be killed — Python has no safe way to do that —
    but it is a daemon, so it never blocks interpreter exit, and the batch
    continues to the next run instead of stopping overnight.
    """
    import threading

    box: dict = {}

    def _work() -> None:
        try:
            box["value"] = llm.invoke(prompt)
        except BaseException as exc:            # noqa: BLE001 — re-raised below
            box["error"] = exc

    # A raw daemon thread, NOT ThreadPoolExecutor. The executor's workers are
    # non-daemon and Python joins them via an atexit hook, so a stuck call would
    # still hang the interpreter at shutdown — the exact failure this guard is
    # meant to prevent. A daemon thread is abandoned cleanly at exit.
    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout=seconds)
    if worker.is_alive():
        raise HardTimeout(f"no response after {seconds:.0f}s (wall-clock guard)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _invoke_with_backoff(llm, prompt: str, attempts: int = 3):
    """Call the model, retrying only on transient infrastructure failures."""
    delay = 10.0
    for attempt in range(1, attempts + 1):
        try:
            return _invoke_guarded(llm, prompt, REQUEST_TIMEOUT_S + 30)
        except Exception as exc:
            text = f"{type(exc).__name__} {exc}".lower()
            if attempt == attempts or not any(s in text for s in _TRANSIENT_SIGNS):
                raise
            print(f"    transient ({type(exc).__name__}); retrying in "
                  f"{delay:.0f}s (attempt {attempt}/{attempts})", flush=True)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _save_reply(reply_dir: str, program: H.Program, run_id: int,
                prompt: str, text: str, parsed: dict) -> None:
    """Persist the raw exchange so a parse failure can be diagnosed, not guessed.

    Every multi-file program in the corpus failed with "no parseable files",
    and without the reply there is no way to tell whether the model produced
    the code under different labels, produced prose, or produced nothing. Those
    demand completely different fixes, and choosing between them by intuition
    is how an afternoon gets spent on the wrong one.
    """
    if not reply_dir:
        return
    os.makedirs(reply_dir, exist_ok=True)
    stem = os.path.join(reply_dir, f"{program.name}_run{run_id}")
    with open(f"{stem}.prompt.txt", "w", encoding="utf-8") as fh:
        fh.write(prompt)
    with open(f"{stem}.reply.txt", "w", encoding="utf-8") as fh:
        fh.write(f"# parsed {len(parsed)} file(s): {sorted(parsed)}\n"
                 f"# expected: {program.source_files}\n"
                 f"# reply length: {len(text)} chars\n"
                 f"{'=' * 70}\n{text}")


def generate(program: H.Program, model: str, provider: str, seed: int,
             reply_dir: str = "",
             ) -> tuple[dict[str, str], dict]:
    """One prompt. Returns (files, telemetry)."""
    from smart_loader.core.llm_factory import _get_llm

    # temperature 0.7 rather than 0.0: the guide asks for three runs per
    # (program, config) "with different random seeds, so we can report
    # variance". At temperature 0 the three runs would be near-identical and
    # the variance column would be meaningless.
    llm = _get_llm(provider=provider, model=model, temperature=0.7,
                   disable_thinking=True)
    # The client-level timeout is set at construction in llm_factory;
    # _invoke_guarded adds a wall-clock ceiling on top of it.
    prompt = build_prompt(program)
    started = time.time()
    reply = _invoke_with_backoff(llm, prompt)
    elapsed = time.time() - started

    from smart_loader.core.llm_json import coerce_llm_text
    text = coerce_llm_text(getattr(reply, "content", reply))

    usage = getattr(reply, "usage_metadata", None) or {}
    tin = int(usage.get("input_tokens", 0) or 0)
    tout = int(usage.get("output_tokens", 0) or 0)
    parsed = parse_files_lenient(text, program)
    _save_reply(reply_dir, program, seed, prompt, text, parsed)
    rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
    return parsed, {
        "wall_time_s": round(elapsed, 1), "llm_calls": 1,
        "tokens_in": tin, "tokens_out": tout, "tokens_total": tin + tout,
        "cost_usd": round(tin / 1e6 * rate_in + tout / 1e6 * rate_out, 4),
        "raw_chars": len(text),
    }


def run_one(program: H.Program, run_id: int, model: str, provider: str,
            csv_path: str, clone_root: str, dry_run: bool,
            reply_dir: str = "") -> dict:
    row = H.blank_row(program, "single_shot", run_id, model)
    work = H.stage(program)
    try:
        if dry_run:
            files, tel = {}, {"wall_time_s": 0.0, "llm_calls": 0,
                              "tokens_in": 0, "tokens_out": 0,
                              "tokens_total": 0, "cost_usd": 0.0}
            note = "dry run — no LLM call"
        else:
            attempt_started = time.time()
            try:
                files, tel = generate(program, model, provider, run_id,
                                      reply_dir=reply_dir)
                note = ""
            except Exception as exc:
                # Record the real elapsed time. Writing 0.0 here made four
                # rate-limited runs look instantaneous when they had actually
                # spent minutes in backoff, which hid where the batch time went.
                files, tel = {}, {
                    "wall_time_s": round(time.time() - attempt_started, 1),
                    "llm_calls": 1, "tokens_in": 0, "tokens_out": 0,
                    "tokens_total": 0, "cost_usd": 0.0}
                note = f"generation error: {type(exc).__name__}: {exc}"[:200]
                # Leave a marker when no reply ever arrived. Otherwise the only
                # evidence is an ABSENT file in --save-replies, and "the model
                # never answered" then looks identical to "reply logging was
                # off" — which are very different diagnoses.
                if reply_dir:
                    os.makedirs(reply_dir, exist_ok=True)
                    stem = os.path.join(reply_dir,
                                        f"{program.name}_run{run_id}")
                    with open(f"{stem}.NO_REPLY.txt", "w",
                              encoding="utf-8") as fh:
                        fh.write(f"no response from the model\n"
                                 f"program : {program.name} "
                                 f"({len(program.source_files)} source files)\n"
                                 f"elapsed : {tel['wall_time_s']}s\n"
                                 f"error   : {type(exc).__name__}: {exc}\n")

        row.update(tel)
        row["iters_total"] = 0          # no repair loop by definition

        wrote = 0
        for name, body in files.items():
            if name in program.source_files:
                with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
                    fh.write(body)
                wrote += 1

        if wrote == 0:
            row.update(compiles="N", tests_passed=0,
                       tests_total=_expected_tests(program),
                       failure_mode="compile", converged="N",
                       divergence_score=1.0, loc_changed=0,
                       lib_swapped_correctly="N",
                       notes=note or "model returned no parseable files")
            return row

        compiled = H.compiles(work, program)
        passed, total, detail, raw = H.run_tests(work, program)
        success = compiled and total > 0 and passed == total

        # Same computation the AXIOM arms use. It was added there first and not
        # here, which would have put a column in the results table that only
        # one arm populated — a comparison artefact rather than a measurement.
        total_lines = sum(
            len(open(os.path.join(program.path, n), encoding="utf-8")
                .read().splitlines())
            for n in program.source_files
            if os.path.isfile(os.path.join(program.path, n))
        ) or 1
        changed = H.loc_changed(program.path, work, program)
        divergence = H.divergence_score(program.path, work, program)
        # See H.migrated: `divergence < 0.999` is blind to identifier changes,
        # so a correct `requests` -> `httpx` swap scored as a no-op here while
        # any agent that injected adapter code registered as changed.
        migrated = H.migrated(program.path, work, program)

        # Measured here too, not only in the Proteus arms. The baseline's own
        # design note says it has "no oracle" -- meaning no oracle in the LOOP,
        # no repair, no retry -- and that stays true: this is an after-the-fact
        # measurement by the harness, made identically for every arm so the A
        # results are comparable rather than only reportable inside Proteus.
        # Without it, A carried oracle_equiv=NA here while the other arms
        # recorded a verdict, which would have made the comparison meaningless.
        oracle_equiv, oracle_note = H.oracle_verdict(program, work)

        row.update(
            compiles="Y" if compiled else "N",
            tests_passed=passed, tests_total=total,
            oracle_equiv=oracle_equiv,
            converged="Y",
            failure_mode=H.classify_failure(
                compiled, passed, total, output=raw,
                oracle=oracle_equiv,
                swapped=H.library_swapped(work, program),
                api_used=("NA" if program.reimplement
                          else ("Y" if H.target_api_used(work, program) else "N")),
                changed=changed),
            hallucination="NA",
            target_installed=H.target_importable(work, program),
            divergence_score=divergence,
            loc_changed=changed,
            pct_firstparty_changed=round(100.0 * changed / total_lines, 1),
            migrated="Y" if migrated else "N",
            migration_success=("Y" if H.migration_ok(program.path, work, program)
                               else "N"),
            lib_swapped_correctly=H.library_swapped(work, program),
            chosen_target=H.chosen_target(work, program),
            glue_needed="Y" if program.category in ("B", "D") else "N",
            logic_mutated="Y" if program.category in ("C", "D") else "NA",
            notes=(note or detail or oracle_note or "")[:200],
        )

        if clone_root:
            row["clone_sha"] = H.save_clone(work, clone_root, "single_shot",
                                            program, run_id)
        return row
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _expected_tests(program: H.Program) -> int:
    """Test count from the pristine program, for rows where the clone died.

    Without this a failed generation records ``tests_total=0``, and a later
    ``passed/total`` average silently treats the row as having nothing to pass.
    """
    return H.baseline_tests(program)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--list-free-models", action="store_true",
                    help="print OpenRouter models that currently cost nothing, "
                         "then exit (the free roster changes, so a hard-coded "
                         "default has a shelf life)")
    ap.add_argument("--program", action="append", default=None,
                    help="limit to these programs (repeatable)")
    ap.add_argument("--runs", type=int, default=1, help="seeds per program")
    ap.add_argument("--start-run", type=int, default=1)
    ap.add_argument("--llm", default="claude",
                    help="claude | nvidia | openrouter | openai | ollama")
    ap.add_argument("--model", default=None,
                    help="model id; defaults to the provider's usual model")
    ap.add_argument("--csv", default=None,
                    help="default: reporting/results_<provider>.csv — one file "
                         "per provider, because a single CSV must not mix "
                         "models across configs or the comparison is confounded")
    ap.add_argument("--clone-dir", default=None,
                    help="keep the generated clones here")
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise the plumbing without calling the API")
    ap.add_argument("--save-replies", default=None,
                    help="write the raw prompt and model reply for every run "
                         "here — the only way to tell an unparseable reply "
                         "from an empty one")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds to pause between runs; use ~5-10 on a free "
                         "tier to stay under its per-minute limit")
    ap.add_argument("--skip-install", action="store_true",
                    help="do not pip-install the target libraries first "
                         "(they must already be importable, or every clone "
                         "that adopts one will fail at collection)")
    args = ap.parse_args(argv)

    if args.list_free_models:
        return list_free_models()
    if not args.corpus:
        ap.error("--corpus is required (unless --list-free-models)")

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    model = args.model or DEFAULT_MODELS.get(args.llm, "")
    if not model:
        print(f"no default model for provider {args.llm!r}; pass --model",
              file=sys.stderr)
        return 2
    # One CSV per provider. Every arm inside a file must share a model, or the
    # single_shot-vs-AXIOM comparison is measuring the model as well as the
    # system. Separate files keep each comparison internally valid, and the
    # paper can report whichever provider it has complete data for.
    csv_path = args.csv or os.path.join(
        corpus, "reporting", f"results_{args.llm}.csv")
    programs = H.discover_programs(corpus)
    if args.program:
        wanted = set(args.program)
        programs = [p for p in programs if p.name in wanted]
    if not programs:
        print("no programs matched", file=sys.stderr)
        return 2

    print(f"config   : single_shot ({args.llm}/{model})")
    print(f"programs : {len(programs)}   runs each: {args.runs}")
    print(f"csv      : {csv_path}")

    # Install the target libraries before generating anything. A clone that
    # correctly adopts `fastcrc` cannot even be imported if fastcrc is absent,
    # and the row would record api_misuse — blaming the model for our missing
    # dependency, on precisely the programs where the swap worked.
    missing: list[str] = []
    if not args.skip_install:
        status = H.ensure_targets(programs)
        missing = [p for p, ok in status.items() if not ok]
        have = sum(1 for ok in status.values() if ok)
        print(f"targets  : {have}/{len(status)} importable"
              + (f"   MISSING: {', '.join(missing)}" if missing else ""))
    print()
    print(f"{'program':30s} {'run':>3} {'tests':>9} {'succ':>5} "
          f"{'diverg':>7} {'swap':>5} {'time':>6}")
    print("-" * 76)

    ok = 0
    for program in programs:
        for run_id in range(args.start_run, args.start_run + args.runs):
            row = run_one(program, run_id, model, args.llm, csv_path,
                          args.clone_dir, args.dry_run,
                          reply_dir=args.save_replies or "")
            if (not program.reimplement) and program.to_lib in missing:
                # The row is still recorded, but flagged: a clone that adopted
                # an uninstallable target cannot be fairly scored, and a
                # reader must not read its failure as a model failure.
                row["notes"] = (f"TARGET {program.to_lib} NOT INSTALLABLE — "
                                f"failure not attributable to the model. "
                                + str(row.get("notes", "")))[:200]
            H.append_row(csv_path, row)
            ok += row["migration_success"] == "Y"
            if args.sleep:
                time.sleep(args.sleep)
            print(f"{program.name:30s} {run_id:3d} "
                  f"{str(row['tests_passed'])+'/'+str(row['tests_total']):>9} "
                  f"{row['migration_success']:>5} {str(row['divergence_score']):>7} "
                  f"{row['lib_swapped_correctly']:>5} "
                  f"{row['wall_time_s']:>5}s", flush=True)

    total = len(programs) * args.runs
    print(f"\nmigration_success {ok}/{total} ({ok/total:.0%})")
    print(f"rows appended to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
