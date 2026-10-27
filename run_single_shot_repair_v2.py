#!/usr/bin/env python3
"""Baseline 1b, redefined — ``single_shot_repair`` as a REVIEW of single_shot.

What changed and why
────────────────────
The previous implementation made its own call 1 with the single_shot prompt and
then repaired its own output. Two consequences, both measured:

  * It was not paired. Call 1 runs at temperature 0.7, so attempt 1 differed
    from the single_shot row for 26 of 36 programs. Any difference between the
    two arms mixed "repair helped" with "a different sample was drawn".
  * Repair fired on 13 of 36 rows, because the trigger was
    ``compiles and tests pass and oracle``. Seven runs that fail the evaluation
    gate were waved through: every one compiles with every test green, because
    the corpus tests inject fakes matching the OLD library's signature.

This runner instead takes the variant single_shot ALREADY produced, hands it to
one LLM call together with the pristine original, and asks the model to decide
for itself whether the migration is correct — returning it unchanged if it is,
or corrected if it is not.

No harness verdict reaches the model
────────────────────────────────────
Deliberately absent from the prompt: the equivalence oracle's verdict, the
real-library smoke verdict, the error-contract verdict, and
``migration_success``. Those are the SCORER. An arm that repairs against its
own scorer measures nothing, and ``proteus_full``'s verifier does not get them
either — it computes its own findings. What the model gets is what a reviewer
has: the original, the proposed migration, and whatever the code does when run.

A passing test suite is withheld for the same reason it is withheld in the
older runner: "4 passed" is not evidence of a defect, and showing it invites
the model to conclude the variant is fine when the tests never exercised the
migrated path.

Every row is reviewed
─────────────────────
All 36, with no trigger at all. Whether the model leaves a good variant alone,
and whether it damages one, are the results this arm exists to measure — not
decisions the harness should make on its behalf.

Outputs are written to their OWN clone root and CSV. single_shot's variants are
read-only inputs here and are never overwritten.

    python3 run_single_shot_repair_v2.py \\
        --corpus ~/Downloads/evaluation \\
        --source-clones final_v4/single_shot_v2 \\
        --clones final_v4/single_shot_repair_v2 \\
        --out results_single_shot_repair_v2.csv --runs 3

The arm's name is unchanged -- ``CONFIG`` is still ``single_shot_repair``, so
the CSV's ``config`` column reads the same as before and the rows drop straight
into the four-arm comparison. Only the FILE is ``_v2``, because
``run_single_shot_repair.py`` still holds the previous implementation and
``final_v4/single_shot_repair/`` still holds the clones it produced.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import smart_loader.eval.proteus_harness as H  # noqa: E402
import run_single_shot as S  # noqa: E402
import run_single_shot_repair as R  # noqa: E402

CONFIG = "single_shot_repair"

#: The review call. The task block and the two conditions are the SAME ones
#: call 1 receives, so the reviewer knows exactly what the first model knew
#: about the task and no more — the only new thing in this arm is the
#: opportunity to look again. Both come from ``run_single_shot`` rather than
#: being restated here, so the two calls cannot drift apart.
#:
#: The older REPAIR_PROMPT carried a paragraph this one deliberately drops:
#: "dropping the third-party dependency and writing the behaviour yourself is
#: not a repair — it abandons the task". On C2, whose own docstring asks for a
#: hand-rolled FSM, that instruction moved the model off a correct first-party
#: reimplementation and into a `statemachine` dependency on all three seeds,
#: and the blind judge called all three BROKEN. Conditions 1 and 2 already say
#: which case applies; restating it as a prohibition inverted the answer.
REVIEW_PROMPT = """\
You are reviewing a proposed migration of a Python program.

The program originally depended on `{from_lib}`. Another engineer rewrote it to
remove that dependency while keeping the program FUNCTIONALLY EQUIVALENT to the
original. You are given the ORIGINAL program and THEIR REWRITE.

Your job is to decide whether the rewrite is correct, and to return a rewrite
that is.

Read both carefully and compare them. Look for anything that would go wrong
when this code actually runs against the real replacement library:

- a call into the new library whose arguments, keywords or return type do not
  match that library's actual API;
- a name, module or attribute that does not exist;
- behaviour that differs from the original on some input — different numbers,
  different types, different rounding, different treatment of empty or invalid
  values;
- an error the original raised that the rewrite now swallows or converts into a
  normal-looking return value, or vice versa;
- setup, connection or authentication that the original performed and the
  rewrite does not, or performs at a different time;
- the dependency not actually removed, or the new one imported but never used.

Note that the test suite is not sufficient evidence on its own. It injects a
fake collaborator built to the ORIGINAL library's interface, so a rewrite can
pass every test and still call the real replacement incorrectly.

{evidence}### THE TASK FOR THIS PROGRAM

{task}

Exactly one of these two conditions applies. Read the program — including its
module docstrings and comments — and decide which:

1. REPLACE WITH A LIBRARY (the normal case). A suitable, actively-maintained
third-party library should be chosen and migrated to, with whatever adapter or
glue code the difference in API requires. Do not write your own implementation
of what a library already provides.

2. REIMPLEMENT AS FIRST-PARTY CODE (the exception). This applies ONLY if the
program's own documentation states that the dependency is to be removed and its
behaviour written as first-party code. If the documentation does not say that,
condition 1 applies.

Requirements:
- Preserve the exact public API: same module names, same function and class
names, same signatures, same return types. The existing test suite must pass
against your answer unchanged.
- Preserve all numeric constants and thresholds exactly.
- Follow any additional requirement the program's own documentation states.

If the rewrite is already correct, return it unchanged. If it is not, return a
corrected version. Either way, return EVERY file in full.

Reply with one `### FILENAME: <name.py>` line per file, each followed by a
```python fenced block containing that file's complete contents. No commentary
outside the blocks.

### ORIGINAL PROGRAM

{sources}

### THE REWRITE UNDER REVIEW

{attempt}
"""



def variant_dir(source_clones: str, program: H.Program, run_id: int):
    """Where single_shot's clone for this (program, seed) actually lives.

    ``H.save_clone`` nests under ``<clone_root>/<config>/``, so a run started
    with ``--clone-dir final_v4/single_shot_v2`` writes to
    ``final_v4/single_shot_v2/single_shot/<program>_run<n>``. Pointing
    ``--source-clones`` at either level works: the first invocation of this
    arm was given the outer one, found nothing for all 36 rows, and wrote 36
    empty rows before anyone noticed. Both layouts are accepted, and
    ``main`` now counts the variants before doing any work.
    """
    leaf = f"{program.name}_run{run_id}"
    for candidate in (os.path.join(source_clones, leaf),
                      os.path.join(source_clones, "single_shot", leaf)):
        if os.path.isdir(candidate):
            return candidate
    return None


def load_variant(source_clones: str, program: H.Program, run_id: int) -> dict:
    """single_shot's delivered files for this (program, seed).

    Includes any ``.py`` the rewrite ADDED -- a reimplementation often splits
    out a new module, and reading only ``program.source_files`` would hand the
    reviewer a program that cannot import.

    Excludes the program's test files. Call 1 never sees them
    (``read_sources_blinded`` walks ``source_files``, which has no test in it),
    so showing them here would give the reviewer context the first model did
    not have, and the tests in a saved clone are byte-identical to the pristine
    ones anyway: ``_write_files`` only ever writes ``source_files``, so no arm
    can modify its own tests. Showing one would also invite the model to return
    a file that is then silently discarded.
    """
    d = variant_dir(source_clones, program, run_id)
    if d is None:
        raise FileNotFoundError(
            os.path.join(source_clones, f"{program.name}_run{run_id}"))
    pristine = {n for n in os.listdir(program.path) if n.endswith(".py")}
    keep = lambda n: n in program.source_files or n not in pristine
    return {name: open(os.path.join(d, name), encoding="utf-8").read()
            for name in sorted(os.listdir(d))
            if name.endswith(".py") and os.path.isfile(os.path.join(d, name))
            and keep(name)}


def build_review_prompt(program: H.Program, attempt: str, evidence: str) -> str:
    """Assemble the review prompt and verify its blinding before returning it."""
    sources = R.read_sources_blinded(program)
    block = f"### EVIDENCE FROM RUNNING THE REWRITE\n\n{evidence}\n\n" if evidence else ""
    prompt = REVIEW_PROMPT.format(from_lib=program.from_lib, sources=sources,
                                  attempt=attempt, evidence=block,
                                  task=S.task_block(program))
    R.assert_blind(prompt, program, attempt)
    return prompt


def run_one(program: H.Program, run_id: int, model: str, provider: str,
            source_clones: str, clone_root: str, dry_run: bool,
            reply_dir: str = "", prompt_dir: str = "") -> dict:
    """One (program, seed): load single_shot's variant, review it once."""
    row = H.blank_row(program, CONFIG, run_id, model)
    work = H.stage(program)
    try:
        try:
            files1 = load_variant(source_clones, program, run_id)
        except FileNotFoundError as exc:
            row["notes"] = f"no single_shot variant at {exc}"[:200]
            return row

        # Overwrite the pristine staging copy with what single_shot delivered.
        for name, body in files1.items():
            with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
                fh.write(body)

        # The reviewed variant imports whatever single_shot chose, so that
        # choice has to be installed before anything is measured -- otherwise
        # the evidence would report OUR missing environment as the model's bug.
        install_status, install_detail = R.ensure_chosen_target(work, program)
        row["target_installed"] = install_status

        before = R._measure(work, program)
        row.update(
            attempt1_compiles="Y" if before["compiled"] else "N",
            attempt1_tests_passed=before["passed"],
            attempt1_migration_success="Y" if before["migration_success"] else "N",
            chosen_target_before=before["chosen"],
        )

        # Evidence: what the code DOES, never what the scorer thinks of it.
        # A fully-passing suite is withheld -- see the module docstring.
        pytest_out = "" if (before["compiled"] and before["total"]
                            and before["passed"] == before["total"]) else before["raw"]
        evidence = R.build_evidence(R.compile_diagnostics(work, program),
                                    pytest_out, "",
                                    install_failure=install_detail)
        if evidence.startswith("[NO TRACE RECORDED]"):
            evidence = ""          # nothing ran badly; say nothing rather than imply a defect

        attempt = R.render_attempt(files1, target=program.to_lib)
        prompt = build_review_prompt(program, attempt, evidence)
        if prompt_dir:
            os.makedirs(prompt_dir, exist_ok=True)
            with open(os.path.join(prompt_dir,
                                   f"{program.name}_run{run_id}.review.prompt.txt"),
                      "w", encoding="utf-8") as fh:
                fh.write(prompt)

        if dry_run:
            row["notes"] = (f"dry run — would review {len(files1)} file(s), "
                            f"prompt {len(prompt)} chars, "
                            f"evidence {'yes' if evidence else 'none'}")[:200]
            row["repair_attempted"] = "Y"
            return row

        started = time.time()
        note = ""
        try:
            files2, tel = R.generate_repair(program, prompt, model, provider,
                                            run_id, reply_dir=reply_dir)
        except Exception as exc:                            # noqa: BLE001
            files2, tel = {}, {"wall_time_s": round(time.time() - started, 1),
                               "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
            note = f"review error: {type(exc).__name__}: {exc}"[:150]

        row["repair_attempted"] = "Y"
        if R._write_files(work, program, files2):
            R.ensure_chosen_target(work, program)
        elif not note:
            # Nothing parseable came back. single_shot's variant stands as the
            # delivered clone: overwriting it with nothing would score a failed
            # review as a regression it did not cause.
            note = "review returned no parseable files"

        final = R._measure(work, program)
        row["clone_sha"] = R._save_clone(clone_root, f"{program.name}_run{run_id}",
                                         work) if clone_root else ""

        rate_in, rate_out = S.PRICING.get(model, (0.0, 0.0))
        passed, total, detail, raw = final["passed"], final["total"], "", ""
        oracle_equiv, oracle_out = R.oracle_detail(work, program)
        smoke_verdict, smoke_detail = H.smoke_regression(program, work)
        changed = H.loc_changed(program.path, work, program)
        row.update(
            compiles="Y" if final["compiled"] else "N",
            tests_passed=passed, tests_total=total,
            migrated="Y" if final["migrated"] else "N",
            migration_success="Y" if final["migration_success"] else "N",
            oracle_equiv=oracle_equiv,
            lib_swapped_correctly=final["swapped"],
            chosen_target=final["chosen"],
            chosen_target_after=final["chosen"],
            repair_changed_target=("Y" if final["chosen"] != before["chosen"]
                                   else "N"),
            divergence_score=final["divergence"],
            loc_changed=changed,
            smoke_path=smoke_verdict, smoke_detail=smoke_detail[:200],
            wall_time_s=round(tel.get("wall_time_s", 0.0), 1),
            llm_calls=1,
            tokens_in=tel.get("tokens_in", 0),
            tokens_out=tel.get("tokens_out", 0),
            tokens_total=tel.get("tokens_in", 0) + tel.get("tokens_out", 0),
            cost_usd=round(tel.get("tokens_in", 0) / 1e6 * rate_in
                           + tel.get("tokens_out", 0) / 1e6 * rate_out, 4),
            glue_needed="Y" if program.category in ("B", "D") else "N",
            notes=note[:200],
        )
        return row
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--source-clones", required=True,
                    help="clone root holding single_shot's delivered variants")
    ap.add_argument("--clones", default="",
                    help="where THIS arm's reviewed variants are written")
    ap.add_argument("--out", default="results_single_shot_repair_v2.csv")
    ap.add_argument("--runs", type=int, default=3)
    # Same convention as the other runners: --llm picks the provider, --model
    # overrides its default. Hard-coding a provider here would let this arm
    # drift onto a different model from the one the other three rows describe.
    ap.add_argument("--llm", default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--programs", default="",
                    help="comma-separated program names; default is all")
    ap.add_argument("--reply-dir", default="")
    ap.add_argument("--prompt-dir", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and blind-check every prompt, make no LLM call")
    args = ap.parse_args(argv)

    if os.path.abspath(args.clones or ".") == os.path.abspath(args.source_clones):
        ap.error("--clones must differ from --source-clones; single_shot's "
                 "variants are inputs here and must not be overwritten")

    model = args.model or S.DEFAULT_MODELS.get(args.llm, "")
    if not model:
        print(f"no default model for provider {args.llm!r}; pass --model",
              file=sys.stderr)
        return 2

    programs = H.discover_programs(os.path.abspath(os.path.expanduser(args.corpus)))
    if args.programs:
        want = {p.strip() for p in args.programs.split(",") if p.strip()}
        programs = [p for p in programs if p.name in want]
    if not programs:
        print("no programs matched", file=sys.stderr)
        return 1

    # Count the inputs BEFORE calling anything. A source root that resolves to
    # nothing is a typo, not a result, and must not be recorded as 36 failures.
    expected = [(p, r) for p in programs for r in range(1, args.runs + 1)]
    missing = [f"{p.name}_run{r}" for p, r in expected
               if variant_dir(args.source_clones, p, r) is None]
    if missing:
        print(f"{len(missing)} of {len(expected)} variants not found under "
              f"{args.source_clones!r}", file=sys.stderr)
        print(f"  first missing: {missing[0]}", file=sys.stderr)
        for probe in (args.source_clones,
                      os.path.join(args.source_clones, "single_shot")):
            if os.path.isdir(probe):
                seen = sorted(n for n in os.listdir(probe) if "_run" in n)[:3]
                if seen:
                    print(f"  {probe} contains: {', '.join(seen)} ...",
                          file=sys.stderr)
        if len(missing) == len(expected):
            print("  nothing to review; not writing any rows", file=sys.stderr)
            return 2

    rows = []
    for program in programs:
        for run_id in range(1, args.runs + 1):
            print(f"  {program.name:30} run{run_id} ...", flush=True)
            row = run_one(program, run_id, model, args.llm,
                          args.source_clones, args.clones, args.dry_run,
                          reply_dir=args.reply_dir, prompt_dir=args.prompt_dir)
            rows.append(row)
            if not args.dry_run:
                H.append_row(args.out, row)
            print(f"    compiles={row['compiles'] or '-'} "
                  f"tests={row['tests_passed']}/{row['tests_total']} "
                  f"success={row['migration_success'] or '-'} "
                  f"changed_target={row['repair_changed_target'] or '-'} "
                  f"{row['notes'][:60]}", flush=True)

    if args.dry_run:
        print(f"\ndry run: {len(rows)} prompt(s) built and blind-checked, "
              f"no LLM call made, nothing written")
        return 0
    ok = sum(1 for r in rows if r["migration_success"] == "Y")
    print(f"\nmigration_success {ok}/{len(rows)} ({ok/max(1,len(rows)):.0%})")
    print(f"rows written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
