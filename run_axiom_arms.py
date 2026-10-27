#!/usr/bin/env python3
"""The two AXIOM configurations of the Proteus evaluation.

    proteus_no_verif   the agents run, equivalence oracle disabled  (baseline 2)
    proteus_full       the agents run with verification              (system)

Both drive the real ``run_loader`` pipeline over each corpus program, then hand
the result to ``proteus_harness`` for exactly the same measurement the
single-shot baseline received: the program's own pytest suite, divergence
against the original, and one fully-populated row in ``results_<provider>.csv``.

Measuring all three arms through one module is the point. If the baseline were
scored by different code than the system, any difference between them could be
the scoring rather than the system, and the paper's central comparison would
not survive a careful reader.

Usage
─────
    python3 run_axiom_arms.py --corpus ~/Downloads/evaluation --config proteus_full
    python3 run_axiom_arms.py --corpus ... --config proteus_no_verif --runs 3
    python3 run_axiom_arms.py --corpus ... --program A1_crc_telemetry --dry-run
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
from run_single_shot import DEFAULT_MODELS, PRICING, REQUEST_TIMEOUT_S

#: AXIOM's agents map onto the protocol's agent names as follows.
#:
#: The earlier version of this table claimed LSA and GSA "are not separable"
#: and folded both into ``iters_lsa`` with ``iters_gsa`` marked NA. That was
#: wrong, and it mislabelled every agent-arm row. The two agents are distinct
#: nodes with distinct retry counters:
#:
#:   LSA  the AXIOM agent decides WHICH library wins and performs the
#:        substitution itself -- ``axiom_agent.py`` calls
#:        ``apply_import_replacements`` directly.   counter: _axiom_retry_count
#:   GSA  the Connector synthesises the GLUE: it injects wrapper functions that
#:        bridge the API delta.                 counter: _connector_retry_count
#:
#: ``iters_total`` was always ``axiom + connector + lma``, so the split is
#: recoverable for rows written under the old labels.
AGENT_MAP = {
    "LSA": "axiom",
    "GSA": "connector",
    "LMA": "logic_substitution",
    "VCA": "verifier",
}


def _telemetry_since(before: dict) -> dict:
    """Token/cost delta for one run, from AXIOM's own process accumulator."""
    from smart_loader.core import token_tracker

    after = token_tracker.get_all_summary()
    # Store the summary once and reuse it to avoid redundant calls.
    tin = int(after.get("total_input", 0)) - int(before.get("total_input", 0))
    tout = int(after.get("total_output", 0)) - int(before.get("total_output", 0))
    calls = int(after.get("total_calls", 0)) - int(before.get("total_calls", 0))
    return {"tokens_in": max(0, tin), "tokens_out": max(0, tout),
            "tokens_total": max(0, tin + tout), "llm_calls": max(0, calls)}


def _iteration_counts(state: dict) -> dict:
    """Repair-loop counts, read from the state AXIOM already maintains."""
    trace = state.get("agent_trace") or []
    stages = [getattr(e, "stage", "") for e in trace]
    axiom_retries = int(state.get("_axiom_retry_count", 0) or 0)
    conn_retries = int(state.get("_connector_retry_count", 0) or 0)
    cycles = int(state.get("_repair_cycle_count", 0) or 0)

    # VCA re-entries: how many times the verifier sent a stage back for repair.
    # _repair_cycle_count is exactly that count, since every cycle originates
    # at the verifier.
    # A genuine total: every repair iteration any agent performed. It used to
    # be ``axiom_retries + conn_retries``, which silently omitted the LSA's
    # own iterations -- so 129 of 144 rows reported iters_total=0 while
    # iters_lma was 1, 3 or 7. A column named "total" that is smaller than one
    # of its own parts is worse than no column.
    lma_iters = sum(1 for s in stages if s == "logic_substitution")
    return {
        "iters_total": axiom_retries + conn_retries + lma_iters,
        "vca_reentries": cycles,
        # LSA is the AXIOM agent (it picks the library and rewrites the
        # imports); GSA is the Connector (it synthesises the glue). These used
        # to be reported as one number under iters_lsa with iters_gsa="NA",
        # which credited the Connector's retries to the wrong agent and hid
        # the AXIOM agent's own inside iters_total. See AGENT_MAP.
        "iters_lsa": axiom_retries,
        "iters_gsa": conn_retries,
        "iters_lma": lma_iters,
    }


def _metric(report: dict, name: str) -> str:
    """One diversity metric out of the isomorph report, CSV-safe.

    Returns "" rather than 0 when the agent did not run: a blank says "not
    measured", whereas 0.0 would claim the variant WAS measured and found
    identical to the original. On A and B, where diversification is not part
    of the spec, that distinction is the whole point.
    """
    value = (report.get("diversity") or {}).get(name)
    return f"{value:.4f}" if isinstance(value, (int, float)) else ""


def _diversify_remaining(work: str, program: H.Program, already: list,
                         use_generated: bool, state: dict) -> list:
    """Diversify the first-party modules the migration loop never reached.

    Returns one isomorph report per module that produced a variant. Modules
    whose variant fails the oracle are skipped and their file left untouched:
    a rejected variant is not written, so a failure here can never degrade the
    program relative to not having run at all.
    """
    from smart_loader.agents.logic_substitution_agent import logic_substitution_agent

    done = {os.path.basename(n) for n in already}
    reports = []
    for name in program.source_files:
        if name in done or not name.endswith(".py"):
            continue
        path = os.path.join(work, name)
        if not os.path.isfile(path):
            continue
        source = open(path, encoding="utf-8").read()
        result = logic_substitution_agent({
            "enable_isomorph": True,
            "patched_code": source,
            "source_file": path,
            "isomorph_program_dir": program.path,
            "isomorph_program_name": program.name,
            "isomorph_use_generated": use_generated,
            # Carried from the caller's state. Without these the agent's LLM
            # generator falls back to the ollama default and every attempt
            # dies on "Connection refused", so these modules silently got the
            # transformation search while the migrated module got the LLM.
            "llm_provider": state.get("llm_provider", ""),
            "model": state.get("model", ""),
            "isomorph_generator": state.get("isomorph_generator", "llm"),
            "agent_trace": [],
        }) or {}
        variant = result.get("isomorph_variant_code") or ""
        report = result.get("isomorph_report") or {}
        if variant.strip() and report.get("applied"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(variant)
            # Modules this variant extracted, beside the file that imports them.
            for extra_name, body in (result.get("isomorph_extra_files") or {}).items():
                extra_name = os.path.basename(str(extra_name))
                if extra_name.endswith(".py") and extra_name != name:
                    with open(os.path.join(work, extra_name), "w",
                              encoding="utf-8") as fh:
                        fh.write(body)
            reports.append(report)
        # Carry the trace through so failing_stage and iters_lma still see
        # every invocation, not only the one the graph made.
        state.setdefault("agent_trace", []).extend(result.get("agent_trace", []))
    return reports


def _merge_iso(reports: list) -> dict:
    """Collapse several per-module isomorph reports into one row's worth.

    Diversity is averaged over the modules that were actually transformed;
    line counts are summed. Reporting the maximum instead would let one
    heavily-restructured helper stand in for a program that was otherwise
    untouched.
    """
    if not reports:
        return {}
    div: dict = {}
    for key in ("composite", "cfg_shape", "ast_structural", "ncd", "du_chain"):
        vals = [(r.get("diversity") or {}).get(key) for r in reports]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            div[key] = sum(vals) / len(vals)
    rules: list = []
    for r in reports:
        for rule in r.get("applied") or []:
            if rule not in rules:
                rules.append(rule)
    before = sum(r.get("original_lines") or 0 for r in reports)
    after = sum(r.get("achieved_lines") or 0 for r in reports)
    return {
        "diversity": div,
        "applied": rules,
        "original_lines": before,
        "achieved_lines": after,
        "modules": len(reports),
        "verification": reports[0].get("verification"),
        "oracle_detail": reports[0].get("oracle_detail"),
        # Carried, not dropped. Omitting this key made
        # ``report.get("oracle_passed")`` return None for every C/D row, which
        # is falsy, so the writer below recorded oracle_equiv=N and forced
        # gen_success=N on rows whose oracle had actually PASSED — the row's
        # own oracle_divergence_loc read "all four layers passed (golden,
        # invariants, tests, imports)" beside the N. It cost 16 of 18 C/D rows
        # in one run, dropping gen_success from 30/36 to 20/36 for a reason
        # that never happened.
        #
        # ``all`` rather than ``reports[0]``: a program is equivalent only if
        # EVERY module it transformed is. Reading the first module's verdict
        # would let an untouched helper vouch for a broken one.
        "oracle_passed": all(bool(r.get("oracle_passed")) for r in reports),
    }


def _failing_stage(state: dict) -> str:
    """Which agent's output first failed, in the protocol's vocabulary."""
    for event in state.get("agent_trace") or []:
        stage = getattr(event, "stage", "")
        name = getattr(event, "event", "")
        if name in ("rejected", "failed", "blocked", "error"):
            if stage == "axiom":
                return "LSA"
            if stage == "connector":
                return "GSA"
            if stage == "logic_substitution":
                return "LMA"
            if stage == "verifier":
                return "VCA"
    return "none"


def _files_using(work: str, program: H.Program) -> list[str]:
    """Source files that actually import the library being migrated.

    Comments and docstrings are stripped first: every corpus module documents
    its own substitution in the module docstring ("`pymodbus` ->
    `minimalmodbus`"), so a raw text match flags files that merely *describe*
    the migration. B2's registers.py says exactly that and imports only
    ``struct``.
    """
    # A distribution name is not its import path. Taking the first
    # hyphen-separated segment gave "simple" for ``simple-pid``, but the module
    # is imported as ``simple_pid`` — so C1 matched nothing, was skipped
    # entirely, and recorded 0 LLM calls and divergence 1.0 on every run while
    # claiming lib_swapped=Y. Try every plausible spelling instead.
    raw = program.from_lib.strip()
    roots = []
    for candidate in (raw, raw.replace("-", "_"), raw.replace("-", "."),
                      raw.replace("_", "."), raw.split("-")[0].split(".")[0]):
        if candidate and candidate not in roots:
            roots.append(candidate)

    hits = []
    for name in program.source_files:
        path = os.path.join(work, name)
        if not os.path.isfile(path):
            continue
        code = H._code_only(open(path, encoding="utf-8").read())
        if any(re.search(rf"\b(import|from)\s+{re.escape(r)}\b", code, re.I)
               for r in roots):
            hits.append(name)
    return hits


def _speedup_and_security(state: dict, program: H.Program) -> dict:
    """``speedup_pct`` and ``security_rationale`` from AXIOM's own agents.

    Both columns sat blank on all 109 rows, and both are measurable from data
    AXIOM already produces — the Profiler benchmarks every candidate, and the
    Security Agent performs OSV/PyPI lookups with **zero LLM calls**, so
    enabling it costs time but no tokens.

    speedup_pct is (source - target) / source as a percentage, positive
    meaning the target is faster. Runtime is used when a real side-effect-free
    probe exists; on this corpus none did (every probe reported UNMEASURED for
    network/broker APIs), so it falls back to import time and says so in the
    basis string. That distinction matters: a 0.2ms -> 3.6ms import change is
    a real measurement but it is startup cost, not throughput, and reporting
    it unlabelled as "speedup" would misrepresent it.
    """
    out = {"speedup_pct": "", "security_rationale": ""}
    benches = state.get("benchmarks") or {}
    src, tgt = program.from_lib, program.to_lib

    def _pick(name: str):
        for key, value in benches.items():
            if str(key).lower().replace("-", "_") == name.lower().replace("-", "_"):
                return value
        return None

    b_src, b_tgt = _pick(src), _pick(tgt)
    if b_src and b_tgt:
        for attr, basis in (("runtime_ms", "runtime"),
                            ("import_time_ms", "import-time")):
            a = float(getattr(b_src, attr, 0) or 0)
            b = float(getattr(b_tgt, attr, 0) or 0)
            if a > 0 and b > 0:
                out["speedup_pct"] = round((a - b) / a * 100.0, 1)
                out["_speedup_basis"] = basis
                break

    sec = state.get("security_results") or {}
    s_src, s_tgt = sec.get(src), sec.get(tgt)
    if s_tgt is not None:
        status = str(getattr(s_tgt, "data_status", "") or "")
        if status == "available":
            n_tgt = len(getattr(s_tgt, "vulnerabilities", []) or [])
            n_src = len(getattr(s_src, "vulnerabilities", []) or []) if s_src else None
            risk = getattr(s_tgt, "risk_level", "?")
            if n_src is None:
                out["security_rationale"] = (
                    f"{tgt}: {n_tgt} open advisories, risk {risk}")
            else:
                verdict = ("fewer" if n_tgt < n_src
                           else "more" if n_tgt > n_src else "same")
                out["security_rationale"] = (
                    f"{tgt} {n_tgt} advisories vs {src} {n_src} "
                    f"({verdict}); risk {risk}")
        else:
            # Say why rather than leaving it blank: "unavailable" is a
            # different claim from "not measured".
            out["security_rationale"] = f"scan {status} for {tgt}"
    return out


def run_one(program: H.Program, config: str, run_id: int, model: str,
            provider: str, clone_root: str, dry_run: bool,
            isomorph: bool, use_generated: bool = False,
            no_aci: bool = False) -> dict:
    """One (program, config, seed) cell, measured exactly as the baseline is."""
    from smart_loader.core import token_tracker
    from smart_loader.core.graph import run_loader

    row = H.blank_row(program, config, run_id, model)
    work = H.stage(program)
    started = time.time()
    try:
        if dry_run:
            row.update(compiles="N", tests_passed=0,
                       tests_total=H.baseline_tests(program),
                       wall_time_s=0.0, llm_calls=0,
                       tokens_total=0, cost_usd=0.0, iters_total=0,
                       divergence_score=1.0, notes="dry run — no LLM call")
            return row

        before = token_tracker.get_all_summary()
        state: dict = {}
        note = ""
        try:
            # AXIOM migrates one file at a time, so a multi-file program is a
            # sequence of runs over its own sources sharing one scratch dir.
            #
            # Only files that actually reference the source library are worth
            # running. B2's registers.py imports nothing but struct, yet the
            # pipeline still selected pymodbus -> minimalmodbus for it, found
            # no call sites, produced no change, and the verifier then spent
            # all five repair cycles asking the connector to fix a file that
            # was never wrong — five LLM calls and ~70s per innocent file.
            iso_reports: list = []
            targets = _files_using(work, program)
            if not targets:
                note = (f"no file references {program.from_lib}; "
                        f"nothing to migrate")
            for name in targets:
                # What is on disk BEFORE the graph runs. If patched_code comes
                # back equal to this, the Verifier reverted and no variant of
                # the rejected patch may be shipped.
                try:
                    with open(os.path.join(work, name), encoding="utf-8") as fh:
                        before_code = fh.read()
                except OSError:
                    before_code = ""
                state = run_loader(
                    source_file=os.path.join(work, name),
                    llm_provider=provider, model=model,
                    # ON: security_agent makes ZERO LLM calls (OSV/PyPI
                    # lookups only), so it costs a little wall time and no
                    # tokens — and it is the only source of the guide's
                    # security_rationale column, which his own example row
                    # fills in.
                    enable_security=True,
                    enable_connector=True,
                    migration_source=program.from_lib,
                    migration_target=("" if program.reimplement
                                      else program.to_lib),
                    # The corpus states the substitution as the requirement.
                    # Without seeding AND mandating it, AXIOM evaluates the
                    # swap on its own merits and correctly declines most of
                    # them: the pilot returned byte-identical files that
                    # passed their tests as no-ops and scored 100% success.
                    # The resolver chooses its own replacement. It used to be
                    # handed the corpus's declared target as a binding
                    # instruction, which meant the stage whose entire purpose
                    # is selecting a library never selected one -- and made
                    # the comparison against the single-shot baseline
                    # asymmetric, since the baseline picks freely. It also
                    # produced the D1 result directly: AXIOM was locked to
                    # minimalmodbus, which is serial-only, while a free
                    # baseline found pyModbusTCP and passed.
                    #
                    # seed_target is off for the same reason: seeding the
                    # candidate list with the declared answer is a softer form
                    # of the same instruction.
                    seed_target=False,
                    mandate_target="",
                    # But "free" must mean free to choose WHICH library, not
                    # free to choose whether to migrate at all. Without this
                    # flag, dropping mandate_target reverted AXIOM to plain
                    # optimisation semantics, where keeping the incumbent is a
                    # legitimate answer -- and on this corpus every candidate
                    # scores 1.0, so the incumbent won every tie. Measured over
                    # 36 proteus_no_verif runs: gen_success 36/36 while
                    # lib_swapped fell 19/36 -> 5/36, a perfect score made of
                    # byte-identical no-ops. The corpus states the substitution
                    # as the requirement; only the destination is open.
                    # ...except where the requirement is NOT "swap libraries".
                    # C1 and C2 declare targets of "in-house controller" and
                    # "hand-rolled FSM": remove the dependency and write the
                    # behaviour as first-party code. Forcing a non-incumbent
                    # library to win makes that requirement unsatisfiable, and
                    # it did -- C2 migrated transitions -> pysm under
                    # proteus_full and transitions -> statemachine under
                    # proteus_no_verif, trading one FSM library for another on
                    # the two subjects that exist to test first-party
                    # reimplementation. On those programs the resolver goes
                    # back to being free to decline, which is what "write it
                    # yourself" requires.
                    require_substitution=not program.reimplement,
                    # C1 and C2 declare a target that is not a package
                    # ("in-house controller", "hand-rolled FSM"): the
                    # requirement is to remove the dependency and write its
                    # behaviour as first-party code. Before the FRA existed
                    # the pipeline had no way to express that, so the LSA
                    # declined and the file came back untouched on all six
                    # runs of both configurations. reimplement_mode routes
                    # parser -> FRA -> LMA -> VCA, skipping the resolver,
                    # profiler and AXIOM, which exist to choose among
                    # libraries and have nothing to choose here.
                    reimplement_mode=program.reimplement,
                    verification_project_root=work,
                    # The path RELATIVE to that root. Without it the verifier's
                    # overlay takes its fallback branch: instead of copying the
                    # project and replacing one file in place, it writes the
                    # patched code alone into an empty temp dir as
                    # ``migration.py``. Static analysis, the import smoke test
                    # and `pytest -q` then all run against a single file with
                    # no siblings and no test suite, so the baseline reports
                    # "no tests ran in 0.00s" -- a blocker -- and the verifier
                    # returns environment_incomplete BEFORE reaching its
                    # acceptance gate. Measured: vca_reentries was 0 on 34 of
                    # 36 proteus_full runs and converged=Y on all 36, including
                    # all 10 failures. The VCA was not being lenient; it was
                    # never getting to judge anything.
                    verification_file_path=name,
                    test_command="python -m pytest -q",
                    enable_isomorph=isomorph,
                    isomorph_skip_verification=(config == "proteus_no_verif"),
                    # C and D are the categories whose spec asks for
                    # "diversify all first-party logic". Without an oracle the
                    # agent refused on every run, so that half of their
                    # requirement went unanswered. equivalence/ now holds
                    # recorded golden vectors for all six.
                    isomorph_program_dir=(program.path
                                          if program.category in ("C", "D")
                                          else ""),
                    isomorph_program_name=program.name,
                    # The four LLM-designed rules are opt-in because the
                    # module that produced them executes generated code. They
                    # are validated by the same oracle as every hand-written
                    # rule and cost nothing at run time, so the only reason to
                    # withhold them is caution — but withholding them here
                    # while the standalone sweeps used them made the two sets
                    # of results incomparable, which is worse.
                    isomorph_use_generated=use_generated,
                    # The real ablation: proteus_no_verif runs the agents with
                    # the VCA switched off, so the Connector's patch is taken
                    # as-is with no acceptance gate and no repair cycles.
                    enable_verifier=(config != "proteus_no_verif"),
                    # The ACI editor is the most expensive component in the
                    # pipeline: 13k-138k input tokens per attempt against ~2k
                    # for one-shot wrapper generation, and it often fails its
                    # own integrity check and falls back anyway. single_shot_repair
                    # matches proteus_full's 25/36 for $1.25 against $13.57
                    # without any ACI at all, so whether the multi-turn editor
                    # earns its cost is an open question this flag exists to ask.
                    enable_aci=not no_aci,
                ) or {}
                # The Logic Substitution Agent runs INSIDE the graph for this
                # file, but it returns its result in ``isomorph_variant_code``
                # and never touches ``patched_code``. Writing only
                # ``patched_code`` therefore shipped the undiversified module
                # while ``isomorph_report`` (read at the bottom of this loop)
                # still credited the variant in the CSV -- the row claimed a
                # transformation the clone on disk did not contain. The variant
                # is derived from patched_code and has passed the same oracle,
                # so it supersedes it for this file; an empty or rejected
                # variant falls back to the patch, exactly as before.
                iso_applied = bool((state.get("isomorph_report") or {})
                                   .get("applied"))
                variant = state.get("isomorph_variant_code") or ""
                patched = state.get("patched_code") or ""

                # A REVERT MUST WIN OVER THE VARIANT.
                #
                # The Logic Substitution Agent runs before the Verifier, so
                # ``isomorph_variant_code`` is a diversified copy of the patch
                # as it stood BEFORE verification. When the Verifier then
                # exhausts its repair budget it reverts by setting
                # ``patched_code`` back to the original source -- and this line
                # used to prefer the variant unconditionally, so the rejected
                # patch shipped anyway, wearing the LSA's renames.
                #
                # D1 r1 is the case that exposed it. The Verifier detected
                # `Name "defines" is not defined`, spent all five cycles on it,
                # gave up and reverted. The runner shipped the variant of that
                # rejected patch, the row scored migration_success=Y, and every
                # valve write in it raises NameError -- which a blind LLM judge
                # found and all seven scoring layers missed.
                #
                # The revert is recognised by ``patched_code`` coming back equal
                # to what was on disk before the graph ran. That is the
                # Verifier's own signal and needs no status string to be
                # interpreted.
                reverted = patched.strip() and patched.strip() == before_code.strip()
                if reverted and variant.strip():
                    print(f"  verifier reverted {name}; discarding the "
                          f"isomorph variant of the rejected patch", flush=True)
                    variant = ""

                shipped = variant if (iso_applied and variant.strip()) else patched
                if shipped.strip():
                    with open(os.path.join(work, name), "w",
                              encoding="utf-8") as fh:
                        fh.write(shipped)
                    # Modules the variant EXTRACTED, written only when that
                    # variant is the thing being shipped. A restructuring that
                    # moves a helper into its own file imports a module that
                    # does not exist until it is written, so omitting these
                    # ships a clone that cannot import -- and writing them
                    # beside a REVERTED patch would leave an orphan file in a
                    # clone that never referenced it.
                    if shipped is variant:
                        for extra_name, body in (
                                state.get("isomorph_extra_files") or {}).items():
                            extra_name = os.path.basename(str(extra_name))
                            if not extra_name.endswith(".py") or extra_name == name:
                                continue
                            with open(os.path.join(work, extra_name), "w",
                                      encoding="utf-8") as fh:
                                fh.write(body)
                            print(f"  wrote extracted module {extra_name}",
                                  flush=True)

            # ── the rest of the chain, for the categories that ask for it ──
            #
            # Exactly one file per program imports the library being migrated,
            # so the loop above visits one file and stops. That satisfies the
            # substitution, but categories C and D also require "diversify all
            # first-party logic" -- and C1 has three first-party modules, of
            # which only controller.py was ever reached. D, being "all of the
            # above", inherited the same shortfall, which is why its output was
            # indistinguishable from C's: both diversified a single module.
            #
            # Running the substitution agent over every remaining first-party
            # module is what makes the chain complete. Each module is checked
            # by the same four-layer oracle, so a variant that changes
            # behaviour is rejected exactly as it is in the migrated file.
            if isomorph and program.category in ("C", "D"):
                extra = _diversify_remaining(
                    work, program, targets, use_generated, state,
                )
                iso_reports.extend(extra)
        except Exception as exc:
            note = f"pipeline error: {type(exc).__name__}: {exc}"[:200]

        row.update(_telemetry_since(before))
        row.update(_iteration_counts(state))
        # The Verifier's own last word on why it refused. ``_repair_history`` is
        # the structured record it already keeps; the final entry is the cycle
        # that exhausted the budget. Recording it turns a revert from an opaque
        # no-op into a diagnosable one.
        # The FRA records why it gave up in ``fra_report`` and that never
        # reached the CSV either, so C1 r2 -- a reimplement subject that came
        # back byte-identical with vca_reentries=0 -- was undiagnosable: the
        # row said source_import_remains, which merely describes the untouched
        # original. The reimplementation agent's own last error is the answer.
        fra = state.get("fra_report") or {}
        if fra.get("error"):
            row["revert_reason"] = (f"FRA exhausted {fra.get('attempts')} attempts: "
                                    f"{fra['error']}")[:400]

        history = state.get("_repair_history") or []
        if history:
            last = history[-1]
            parts = []
            for err in (last.get("errors") or [])[:3]:
                msg = err.get("message", err) if isinstance(err, dict) else err
                parts.append(str(msg))
            row["revert_reason"] = (f"cycle {last.get('cycle')}/"
                                    f"{last.get('max_cycles')} "
                                    f"{last.get('failure_class', '')}: "
                                    + "; ".join(parts))[:400]
        row["wall_time_s"] = round(time.time() - started, 1)
        rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
        row["cost_usd"] = round(
            row["tokens_in"] / 1e6 * rate_in + row["tokens_out"] / 1e6 * rate_out, 4)

        compiled = H.compiles(work, program)
        passed, total, detail, raw = H.run_tests(work, program)
        divergence = H.divergence_score(program.path, work, program)

        # A clone that was never modified passes the original's own tests by
        # construction. The guide defines gen_success as "compiles AND all
        # tests pass", so a no-op satisfies it — 18 of 46 rows in the first
        # full arm were byte-identical files scoring as successes, inflating
        # the headline from 71% to 83%. `migrated` records whether anything
        # actually changed, so the CSV can distinguish the two. gen_success
        # keeps the guide's definition (his summarizer depends on it); the
        # notes column carries the caveat.
        # "The source actually changed", and nothing weaker. The old form was
        #     divergence < 0.999 or program.reimplement
        # which marked every reimplement case (C1, C2) as migrated whether or
        # not a byte moved — those two came back byte-identical to the
        # pristine program and still recorded migrated=Y. The `or` was there
        # because a reimplementation can score low textual divergence while
        # being a real rewrite; but low divergence is not zero change, so the
        # line-count test covers that case without excusing a no-op.
        # See H.migrated: the old test was , which is blind
        # to identifier changes and therefore scored a correct library swap as a
        # no-op while crediting any agent that injected adapter code.
        migrated = H.migrated(program.path, work, program)
        success = compiled and total > 0 and passed == total

        # Keyed off the isomorph report's `verification` field, never off
        # oracle_passed alone: the NullOracle used by the no-verification
        # ablation returns True for everything, so oracle_passed by itself
        # cannot distinguish "checked and equivalent" from "never checked".
        # The graph's report covers the migrated module only. Merge in the
        # per-module reports from the rest of the first-party source so the
        # iso_* columns describe the whole program rather than one file.
        report = state.get("isomorph_report") or {}
        _all_iso = ([report] if report.get("applied") else []) + list(
            locals().get("iso_reports") or [])
        if len(_all_iso) > 1:
            report = _merge_iso(_all_iso)
        # `oracle_equiv` is reported in its own column and is one of the four
        # conditions `migration_success` requires. What it MEANS is still
        # category-dependent, and the difference has to be stated wherever the
        # number is quoted:
        #
        #   A, B  compiles AND the program's own suite passes. These are
        #         library migrations with no logic mutation, so the suite is
        #         the behavioural evidence that exists.
        #   C, D  the above AND the equivalence oracle passes — recorded
        #         golden vectors reproduced exactly, invariants held over
        #         swept inputs, and every module still imports.
        #
        # The stricter bar on C/D is deliberate: those are the categories that
        # rewrite logic, so "the tests still pass" is too weak a claim. A row
        # that passes its tests but fails the oracle is now a FAILURE, and the
        # reason is written to oracle_divergence_loc so that case stays
        # distinguishable from an ordinary test failure.
        # Measured by the HARNESS for every arm and every category, directly
        # against the clone on disk.
        #
        # It used to be read from the Logic Substitution Agent's report for C/D
        # and skipped entirely for proteus_no_verif. That was wrong twice over.
        # The no_verif ablation switches the VERIFIER AGENT off during the run
        # -- it is not a licence to stop measuring equivalence afterwards, and
        # skipping it meant the ablation's C/D rows were scored on a weaker bar
        # than the arm it is compared against. Reading it from the agent's own
        # report also made the measurement depend on which agents happened to
        # run, so the same clone could be scored differently by two arms.
        #
        # NA now means one thing only: this program has no recorded probes.
        oracle_equiv, equiv_detail = H.oracle_verdict(program, work)

        # Lines changed as a share of the program's first-party source. The
        # guide lists this as EXT and it was left blank on all 109 rows; it is
        # cheap to compute and is the only column that says HOW MUCH changed
        # rather than merely whether anything did.
        total_lines = sum(
            len(open(os.path.join(program.path, n), encoding="utf-8")
                .read().splitlines())
            for n in program.source_files
            if os.path.isfile(os.path.join(program.path, n))
        ) or 1
        changed = H.loc_changed(program.path, work, program)
        extra = _speedup_and_security(state, program)

        row.update(
            compiles="Y" if compiled else "N",
            tests_passed=passed, tests_total=total,
            # `migration_success` is the single verdict now -- see
            # H.migration_ok. Its components stay in their own columns
            # (compiles, tests_passed/total, oracle_equiv, migrated,
            # lib_swapped_correctly) so a reader can see which one failed.
            # One verdict: compiles AND all tests pass AND the oracle did not
            # reject it AND the old dependency is gone, the new one imported,
            # and the new one actually called. `gen_success` used to sit beside
            # this as a weaker signal and was removed -- see COLUMNS.
            migration_success=("Y" if H.migration_ok(program.path, work, program)
                               else "N"),
            pct_firstparty_changed=round(100.0 * changed / total_lines, 1),
            migrated="Y" if migrated else "N",
            oracle_equiv=oracle_equiv,
            converged="Y" if state else "N",
            failing_stage=_failing_stage(state),
            failure_mode=H.classify_failure(
                compiled, passed, total, converged=bool(state), output=raw,
                oracle=oracle_equiv,
                swapped=H.library_swapped(work, program),
                api_used=("NA" if program.reimplement
                          else ("Y" if H.target_api_used(work, program) else "N")),
                changed=H.loc_changed(program.path, work, program)),
            # Not decidable from the code alone: a call that does not exist and
            # a call that exists but is wrong look identical until something
            # runs. Filled by the judge, which reads the clone; NA until then.
            hallucination="NA",
            target_installed=H.target_importable(work, program),
            divergence_score=divergence,
            loc_changed=H.loc_changed(program.path, work, program),
            lib_swapped_correctly=H.library_swapped(work, program),
            chosen_target=H.chosen_target(work, program),
            glue_needed="Y" if program.category in ("B", "D") else "N",
            logic_mutated=("Y" if (isomorph and report.get("applied"))
                           else ("N" if program.category in ("C", "D") else "NA")),
            # The diversity numbers the agent already computed. Recording only
            # logic_mutated threw them away at the CSV boundary, leaving the
            # diversification claim with no magnitude behind it — "we changed
            # the structure" with nothing saying how much. cfg_shape is broken
            # out because control-flow difference is the metric that actually
            # distinguishes a restructured program from a renamed one.
            iso_diversity=_metric(report, "composite"),
            iso_cfg_shape=_metric(report, "cfg_shape"),
            iso_rules=",".join(report.get("applied") or []),
            iso_lines=(f"{report.get('original_lines')}->"
                       f"{report.get('achieved_lines')}"
                       if report.get("achieved_lines") is not None else ""),
            **{k: v for k, v in extra.items() if not k.startswith("_")},
            # The guide asks "where the oracle said the clone diverged". For
            # this experiment that is the verifier's rejection detail; blank
            # would imply we failed to record it rather than that it accepted.
            # Why the row failed, when it failed. equiv_detail is set only
            # when the equivalence oracle was the thing that rejected it,
            # which is what keeps "passed its tests but changed behaviour"
            # distinguishable from "failed its tests" now that gen_success
            # collapses both into N.
            oracle_divergence_loc=(equiv_detail or (
                next((str(getattr(e, "detail", "") or "")[:120]
                      for e in reversed(state.get("agent_trace") or [])
                      if getattr(e, "stage", "") == "verifier"
                      and getattr(e, "event", "") in ("rejected", "failed",
                                                      "exhausted")), "")
                if not success else "")),
            notes=(("NO-OP: source unchanged; tests pass vacuously. "
                    if not migrated else "")
                   + (f"speedup basis={extra['_speedup_basis']}. "
                      if extra.get("_speedup_basis") else "")
                   + (note or detail or ""))[:200],
        )

        if clone_root:
            # Shared implementation: refuses to write into an existing clone
            # directory (it renames the old one aside), copies every .py the run
            # produced, and returns the fingerprint recorded on this row.
            row["clone_sha"] = H.save_clone(work, clone_root, config,
                                            program, run_id)
        return row
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--config", choices=("proteus_no_verif", "proteus_full"),
                    required=True)
    ap.add_argument("--program", action="append", default=None)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--start-run", type=int, default=1)
    ap.add_argument("--llm", default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--clone-dir", default=None)
    ap.add_argument("--no-aci", action="store_true",
                    help="skip the multi-turn ACI edit session and use one-shot "
                         "wrapper generation only; far cheaper, and measures "
                         "whether the editor earns its tokens")
    ap.add_argument("--no-isomorph", action="store_true",
                    help="skip the logic-substitution agent (it only applies "
                         "to categories asked to diversify)")
    ap.add_argument("--use-generated", action="store_true",
                    help="also load the four LLM-designed transformations from "
                         "generated_spts.json. They are already validated by "
                         "the same oracle as the hand-written rules and cost "
                         "no tokens at run time; the standalone sweeps use "
                         "them, so omitting them here makes the two sets of "
                         "results incomparable.")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    os.environ.setdefault("AXIOM_LLM_TIMEOUT_S", str(REQUEST_TIMEOUT_S))
    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    model = args.model or DEFAULT_MODELS.get(args.llm, "")
    csv_path = args.csv or os.path.join(corpus, "reporting",
                                        f"results_{args.llm}.csv")
    programs = H.discover_programs(corpus)
    if args.program:
        wanted = set(args.program)
        programs = [p for p in programs if p.name in wanted]
    if not programs:
        print("no programs matched", file=sys.stderr)
        return 2

    print(f"config   : {args.config} ({args.llm}/{model})")
    print(f"programs : {len(programs)}   runs each: {args.runs}")
    print(f"csv      : {csv_path}")
    if not args.skip_install:
        status = H.ensure_targets(programs)
        have = sum(1 for ok in status.values() if ok)
        print(f"targets  : {have}/{len(status)} importable")
    print()
    print(f"{'program':30s} {'run':>3} {'tests':>9} {'succ':>5} {'orc':>4} "
          f"{'iters':>6} {'calls':>6} {'diverg':>7} {'time':>7}")
    print("-" * 88)

    ok = 0
    for program in programs:
        for run_id in range(args.start_run, args.start_run + args.runs):
            row = run_one(program, args.config, run_id, model, args.llm,
                          args.clone_dir, args.dry_run,
                          isomorph=not args.no_isomorph,
                          use_generated=args.use_generated,
                          no_aci=args.no_aci)
            H.append_row(csv_path, row)
            ok += row["migration_success"] == "Y"
            print(f"{program.name:30s} {run_id:3d} "
                  f"{str(row['tests_passed'])+'/'+str(row['tests_total']):>9} "
                  f"{row['migration_success']:>5} {str(row['oracle_equiv']):>4} "
                  f"{str(row['iters_total']):>6} {str(row['llm_calls']):>6} "
                  f"{str(row['divergence_score']):>7} "
                  f"{row['wall_time_s']:>6}s", flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    total = len(programs) * args.runs
    print(f"\nmigration_success {ok}/{total} ({ok/total:.0%})")
    print(f"rows appended to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
