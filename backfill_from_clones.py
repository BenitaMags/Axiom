#!/usr/bin/env python3
"""Fill the arm-asymmetric columns in results.csv from the saved clones.

Why this exists
───────────────
Two columns were being produced by the AXIOM arms only, because they were
computed inside ``run_axiom_arms.py``:

    pct_firstparty_changed   how much of the first-party source moved
    gen_success              did it work — which on C/D includes equivalence

A metric present for one arm and blank for the others cannot be compared
across arms — and "AXIOM is equivalence-checked" is not a finding if the
baseline was never checked at all. Both are properties of the *output*, not of
the system that produced it, so both can be measured after the fact from the
clone each arm already saved. That makes the comparison symmetric at no
further LLM cost.

``gen_success`` means different things by category, because the categories ask
for different things:

    A, B   compiles and the program's own test suite passes
    C, D   the above AND the equivalence oracle passes — golden vectors
           reproduced exactly, invariants held, every module imports

``oracle_equiv`` is kept alongside it, since it is part of the guide's schema,
but it reports only what the oracle itself said:

    A, B   NA — no probes exist, so there is no oracle verdict to give.
           Copying gen_success into it would dress a passing test suite up as
           an equivalence proof.
    C, D   Y/N from the four layers. This verdict is also a component of
           gen_success, so the two agree except where a row fails its tests
           for a reason the oracle never sees.

When the oracle is what rejected a C/D row, the reason is written to
oracle_divergence_loc, so an equivalence failure stays distinguishable from an
ordinary test failure.

    python3 backfill_from_clones.py \
        --csv ~/Downloads/evaluation/reporting/results_FINAL.csv \
        --corpus ~/Downloads/evaluation --clones clones

Idempotent: re-running recomputes the same values from the same clones.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "equivalence"))

from smart_loader.eval import proteus_harness as H  # noqa: E402
from smart_loader.eval.proteus_harness import (  # noqa: E402
    discover_programs, library_swapped, chosen_target)
from factory_check import check as factory_check  # noqa: E402


def _structural_change(original_dir: str, clone_dir: str, program) -> dict:
    """Rename-insensitive structural distance between original and clone.

    Measured for EVERY arm and every category, because all three arms answer
    the same question here: how structurally different is the file we shipped
    from the file we were given? That is comparable in a way ``logic_mutated``
    was not --- ``single_shot`` hard-codes it by category rather than measuring
    anything, so its Y/N carried no information.

    A library migration restructures code too. Swapping ``requests`` for
    ``httpx`` moves call sites, changes exception handling and often reshapes
    control flow, and there is no principled reason to call that "not a
    structural change" simply because a mutator did not perform it. So the
    distance is reported for A and B as well.

    This is NOT the same measurement as the ``iso_*`` columns. Those compare
    the mutator's variant against the *patched* file, isolating what the
    transformation search contributed. These compare the delivered file
    against the *pristine* original, capturing migration and diversification
    together.
    """
    from smart_loader.isomorph import metrics as metrics_mod

    def joined(directory: str) -> str:
        parts = []
        for name in sorted(program.source_files):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                parts.append(open(path, encoding="utf-8").read())
        return "\n".join(parts)

    before, after = joined(original_dir), joined(clone_dir)
    if not before or not after:
        return {}
    try:
        return metrics_mod.measure_all(before, after)
    except SyntaxError:
        # A clone that does not parse has no AST to compare. Returning empty
        # leaves the columns blank ("not measured") rather than writing 0,
        # which would claim the structure was measured and found identical.
        return {}


def _pct_changed(original_dir: str, clone_dir: str, program) -> tuple:
    """Share of first-party lines that differ, plus how much we could see.

    Files absent from the clone are skipped rather than counted as wholly
    deleted. A clone that never wrote a file is evidence of nothing, whereas
    scoring it as a total rewrite would report a spectacular fake number.
    """
    changed = total = 0
    seen = missing = 0
    for name in program.source_files:
        o = os.path.join(original_dir, name)
        c = os.path.join(clone_dir, name)
        if not os.path.isfile(o):
            continue
        ol = open(o, encoding="utf-8").read().splitlines()
        total += len(ol)
        if not os.path.isfile(c):
            missing += 1
            continue
        seen += 1
        cl = open(c, encoding="utf-8").read().splitlines()
        changed += sum(1 for line in difflib.unified_diff(ol, cl, n=0)
                       if line.startswith(("+", "-"))
                       and not line.startswith(("+++", "---")))
    if not seen:
        return None, missing
    return round(100.0 * changed / max(total, 1), 1), missing


def _reverify(clone_dir: str, program, install_chosen: bool = True) -> dict:
    """Re-run compile and the test suite against the clone, with the library
    the clone actually adopted installed first.

    Why this is needed for the arms to be comparable
    ────────────────────────────────────────────────
    Every arm here is free-choice: the model picks its own replacement, and
    across the corpus it picked eleven different libraries while the harness
    pre-installed only the seven the corpus DECLARES. A clone that migrated
    correctly to ``gmqtt`` or ``pyModbusTCP`` therefore died at import with
    ModuleNotFoundError and was recorded as a model failure, when the only
    thing missing was a pip install on our side. On the repair arm that
    accounted for six of eight recorded failures.

    Those stale verdicts are what the recorded ``tests_passed`` carries, so
    re-deriving the other columns while trusting that number would leave the
    artefact in place under a tidier surface. The suite is therefore re-run
    here, against a pristine staging of the program overlaid with the clone's
    own files — the same construction the runners use, so a re-verified row
    means exactly what a freshly-generated one means.

    Returns the measured columns; the caller decides what to write.
    """
    import run_single_shot_repair as R

    work = H.stage(program)
    try:
        for name in sorted(os.listdir(clone_dir)):
            src = os.path.join(clone_dir, name)
            if name.endswith(".py") and os.path.isfile(src):
                shutil.copy(src, work)
        installed = (R.ensure_chosen_target(work, program)[0]
                     if install_chosen else "NA")
        compiled = H.compiles(work, program)
        passed, total, _detail, raw = H.run_tests(work, program)
        return {
            "compiles": "Y" if compiled else "N",
            "tests_passed": passed,
            "tests_total": total,
            "target_installed": installed,
            "failure_mode": H.classify_failure(compiled, passed, total,
                                               output=raw),
            "hallucination": H.detect_hallucination(raw, program),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--clones", default="clones")
    ap.add_argument("--no-oracle", action="store_true",
                    help="skip the equivalence layer (much faster)")
    ap.add_argument("--no-factory", action="store_true",
                    help="skip the un-injected factory-path layer")
    ap.add_argument("--reverify", action="store_true",
                    help="re-run compile and the test suite against each "
                         "clone, after installing the library that clone "
                         "actually adopted. Without this the recorded "
                         "tests_passed is trusted, and on a free-choice arm "
                         "that number can record a ModuleNotFoundError for a "
                         "package the harness simply never installed. Needed "
                         "for arms to be compared to one another.")
    ap.add_argument("--config", action="append", default=None,
                    help="restrict to these configs (repeatable)")
    args = ap.parse_args(argv)

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    clones = os.path.abspath(os.path.expanduser(args.clones))
    programs = {p.name: p for p in discover_programs(corpus)}

    oracle_cache: dict = {}
    if not args.no_oracle:
        from oracle import ProgramOracle  # noqa: E402

    with open(args.csv, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys())
    # The file was written under an older schema, so a column produced below
    # would otherwise be dropped silently by DictWriter on the way out.
    for column in H.COLUMNS:
        if column not in fields:
            fields.append(column)
    for row in rows:
        for column in fields:
            row.setdefault(column, "")

    filled_pct = filled_equiv = filled_factory = filled_struct = 0
    reverified = 0
    for row in rows:
        program = programs.get(row["program"])
        if program is None:
            continue
        if args.config and row["config"] not in args.config:
            continue
        clone_dir = os.path.join(clones, row["config"],
                                 f"{row['program']}_run{row['run_id']}")
        if not os.path.isdir(clone_dir):
            continue

        if args.reverify:
            measured = _reverify(clone_dir, program)
            row.update(measured)
            reverified += 1
            print(f"  {row['config']:18s} {row['program']:28s} "
                  f"run{row['run_id']} -> {measured['tests_passed']}/"
                  f"{measured['tests_total']} "
                  f"installed={measured['target_installed']}", flush=True)

        # Structural distance from the pristine original, for every arm and
        # category. This is what makes logic_mutated a measurement instead of
        # a per-category assertion.
        struct = _structural_change(program.path, clone_dir, program)
        if struct:
            row["struct_ast"] = f"{struct['ast_structural']:.4f}"
            row["struct_cfg"] = f"{struct['cfg_shape']:.4f}"
            row["struct_composite"] = f"{struct['composite']:.4f}"
            # Measured, not asserted: did the structure actually move at all?
            row["logic_mutated"] = "Y" if struct["ast_structural"] > 0 else "N"
            filled_struct += 1

        # Recomputed here, from the saved clone, for every arm identically.
        # These were written during the run by a ``library_swapped`` whose
        # distribution-name-to-module-name mapping was wrong for hyphenated
        # packages: ``simple-pid`` was searched for as ``simple``, which cannot
        # match ``simple_pid``, so every C1 run was recorded as having removed
        # a dependency that is still imported in the clone. Re-deriving it here
        # means the correction reaches rows already in the CSV without paying
        # for another pipeline run.
        row["lib_swapped_correctly"] = library_swapped(clone_dir, program)
        row["chosen_target"] = chosen_target(clone_dir, program)

        pct, _missing = _pct_changed(program.path, clone_dir, program)
        if pct is not None:
            row["pct_firstparty_changed"] = pct
            filled_pct += 1
            # A clone identical to the original was not migrated, whatever the
            # runner recorded. C1 and C2 came back byte-identical and still
            # claimed migrated=Y, because the old rule treated "reimplement
            # case" as migration by definition. migration_success depends on
            # this flag, so leaving it would let a no-op count as a win.
            if pct == 0 and row.get("migrated") == "Y":
                row["migrated"] = "N"
                row["migration_success"] = "N"

            # ...and the converse, which is the more damaging error.
            #
            # ``migrated`` was originally derived from ``divergence_score <
            # 0.999``. That score is a similarity over the AST *node-type*
            # sequence, and a clean library swap changes names, not node types:
            # ``requests.Session()`` -> ``httpx.Client()`` is Call/Attribute/Name
            # before and after. So a correct migration that did not also
            # restructure control flow scored 1.0 and was recorded as "not
            # migrated" -- eight single_shot rows, including three the blind
            # reviewer rated CLEAN with the dependency verifiably swapped.
            #
            # The direction matters: single_shot is the BASELINE, so this
            # understated the system we are comparing against and flattered
            # ours. Recomputed here from loc_changed, which is a direct textual
            # measure rather than a structural proxy, and applied to every arm
            # identically.
            elif pct > 0 and row.get("migrated") == "N":
                row["migrated"] = "Y"
                row["migration_success"] = (
                    "Y" if (row.get("gen_success") == "Y"
                            and row.get("lib_swapped_correctly") == "Y")
                    else "N")

        # Layer 5, for EVERY category and every arm. Applying it only where it
        # is convenient would rebuild the asymmetry this script exists to
        # remove: the baseline must be checked exactly as hard as AXIOM.
        if not args.no_factory:
            # Check the library the clone ACTUALLY adopted, not the one the
            # corpus declared. Under --free-choice they differ: a clone that
            # migrated to umodbus was being probed for a minimalmodbus factory,
            # found none, and reported SKIPPED — a silent non-result dressed as
            # a clean one. Fall back to the declared target when the clone
            # adopted nothing new (reimplement cases, and no-ops).
            adopted = row.get("chosen_target") or ""
            verdict, detail = factory_check(clone_dir,
                                            adopted or program.to_lib,
                                            program.from_lib)
            row["factory_path"] = verdict
            filled_factory += 1
            if verdict == "MISUSE":
                # The migrated call cannot work. Tests passing means they
                # never reached it, so this outranks them.
                row["gen_success"] = "N"
                row["migration_success"] = "N"
                row["oracle_divergence_loc"] = f"factory path: {detail}"[:200]

        if program.category in ("A", "B"):
            # No probes exist for these, so there is no oracle verdict to
            # give. NA says that; it is not the same claim as "equivalent".
            row["oracle_equiv"] = "NA"
            if args.reverify:
                # A and B never reach the oracle block below, where gen_success
                # is recomputed for C/D. Without this their gen_success would
                # keep the stale pre-install verdict while their tests_passed
                # showed the corrected one -- the two disagreeing in the same
                # row, which is worse than either alone.
                try:
                    passed = int(row.get("tests_passed") or 0)
                    total = int(row.get("tests_total") or 0)
                except ValueError:
                    passed = total = 0
                row["gen_success"] = (
                    "Y" if (row.get("compiles") == "Y" and total > 0
                            and passed == total
                            and row.get("factory_path") != "MISUSE") else "N")
            continue

        if args.no_oracle:
            continue
        # Overwrite every module the clone carries, then run all four layers
        # against the values recorded from the pristine program.
        # Every .py in the clone, including modules the run ADDED. Restricting
        # this to program.source_files dropped newly written first-party
        # modules, so the staged copy imported a file that was not there and
        # scored N for a reason that never existed during the run.
        sources = {}
        for name in sorted(os.listdir(clone_dir)):
            path = os.path.join(clone_dir, name)
            if name.endswith(".py") and os.path.isfile(path):
                sources[name[:-3]] = open(path, encoding="utf-8").read()
        if not sources:
            continue
        oracle = oracle_cache.get(program.name)
        if oracle is None:
            oracle = oracle_cache[program.name] = ProgramOracle(program.path,
                                                                program.name)
        try:
            result = oracle.check_many(sources)
            # An import failure naming a module that is neither installed nor
            # present in the clone means the SAVED ARTEFACT is incomplete —
            # the run wrote a module that clone-saving dropped. The oracle
            # cannot execute that, so there is no verdict to give. Scoring it
            # N would blame the migration for a harness defect (fixed now, but
            # these clones predate the fix).
            missing = ""
            if not result.equivalent and "No module named" in result.detail:
                missing = result.detail.split("No module named", 1)[1]
                missing = missing.strip().strip("'\"").split("'")[0]
            if missing and missing not in sources:
                # Two very different causes look identical here, and calling
                # both "incomplete clone" was wrong. If the missing module is
                # the program's own declared source or target library, it is a
                # third-party package absent from THIS machine — the corpus
                # never installs a source library whose target is not
                # installable, which is exactly C2 (transitions -> hand-rolled).
                # Otherwise it is a first-party module the run wrote and
                # clone-saving dropped, e.g. pid_engine.py or statekit.py.
                def _root(lib):
                    return (lib or "").split()[0].lower().replace("-", "_")

                if _root(missing) in (_root(program.from_lib),
                                      _root(program.to_lib)):
                    reason = (f"not measurable: '{missing}' is this program's "
                              f"declared library and is not installed on this "
                              f"machine; install it and re-run to score")
                    label = f"NOT MEASURABLE ({missing} not installed)"
                else:
                    # NOTE: "imported by the clone" does NOT separate an adopted
                    # third-party package from a first-party module the run
                    # wrote and clone-saving dropped — both are imported and
                    # neither is present. An earlier attempt to use that signal
                    # labelled single_shot's own ``pid_engine`` and ``statekit``
                    # as adopted packages. The declared-library check above is
                    # the only reliable discriminator we have, so anything that
                    # falls through is reported as a saving gap.
                    reason = (f"not measurable: clone is missing {missing}.py, "
                              f"which the run created but clone-saving dropped")
                    label = "NOT MEASURABLE (incomplete clone)"
                row["oracle_equiv"] = "NA"
                # gen_success is deliberately LEFT ALONE. It was measured
                # during the run, against the complete working directory,
                # before clone-saving dropped a module. Overwriting it with N
                # here scores the generator for a defect in our own harness --
                # and both affected rows belong to single_shot, the baseline,
                # so the error ran against the system we are comparing
                # ourselves to. "We could not re-check this" and "this failed"
                # are different facts; only the first one is true.
                row["oracle_divergence_loc"] = reason[:200]
                filled_equiv += 1
                print(f"  {row['config']:18s} {row['program']:28s} "
                      f"run{row['run_id']} -> {label}", flush=True)
                continue
            row["oracle_equiv"] = "Y" if result.equivalent else "N"
            # Recompute gen_success from its components rather than only
            # downgrading it. An earlier pass that downgraded a row for a
            # failure since resolved — C2 failed the oracle only because the
            # source library was absent from that machine — would otherwise
            # leave gen_success=N sitting next to oracle_equiv=Y and 5/5
            # tests, which is not a state the definition can produce.
            try:
                passed = int(row.get("tests_passed") or 0)
                total = int(row.get("tests_total") or 0)
            except ValueError:
                passed = total = 0
            row["gen_success"] = (
                "Y" if (row.get("compiles") == "Y" and total > 0
                        and passed == total and result.equivalent
                        and row.get("factory_path") != "MISUSE") else "N")
            row["oracle_divergence_loc"] = ("" if result.equivalent
                                            else result.detail[:120])
        except Exception as exc:
            row["oracle_equiv"] = "NA"
            row["oracle_divergence_loc"] = f"oracle error: {exc}"[:120]
        filled_equiv += 1
        print(f"  {row['config']:18s} {row['program']:28s} run{row['run_id']} "
              f"-> oracle {'pass' if result.equivalent else 'FAIL'}, "
              f"gen_success={row['gen_success']}", flush=True)

    # Enforce the definition rather than trusting every write path to have
    # respected it. migration_success is strictly stronger than gen_success,
    # so a Y here with an N there is not a judgement call, it is a stale cell —
    # and one such cell survived several passes before an audit caught it.
    # Recomputed from its three components rather than only invalidated, so a
    # correction to any component propagates in BOTH directions. Repairing only
    # the Y-that-should-be-N case was itself asymmetric: when the swap
    # detection was fixed, rows that should have flipped N->Y would have stayed
    # N and quietly understated whichever arm they belonged to.
    repaired = 0
    for row in rows:
        want = ("Y" if (row.get("gen_success") == "Y"
                        and row.get("migrated") == "Y"
                        and row.get("lib_swapped_correctly") == "Y")
                else "N")
        if row.get("migration_success") != want:
            row["migration_success"] = want
            repaired += 1

    with open(args.csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if repaired:
        print(f"migration_success repaired on {repaired} row(s) "
              f"(was Y without gen_success+migrated)")

    if reverified:
        print(f"\nre-verified (tests re-run, chosen library installed) on "
              f"{reverified} row(s)")
    print(f"\npct_firstparty_changed filled on {filled_pct} row(s)")
    print(f"equivalence checked on  {filled_equiv} C/D row(s)")
    print(f"factory path checked on {filled_factory} row(s)")
    print(f"structural distance on   {filled_struct} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
