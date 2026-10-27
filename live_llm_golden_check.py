"""Narrow LIVE ablation: does the LMA's actual LLM-generation step
(llm_variant.generate, called from logic_substitution_agent.py) ever produce a
candidate that the real oracle rejects specifically on a golden-vector mismatch
but the golden-vector-ablated oracle would have accepted?

This makes REAL Claude API calls (real cost). Scope, as approved: one program
per category (A/B/C/D), proteus_full's module target, 3 LLM attempts per
oracle condition (real vs. ablated) = up to 24 real LLM calls total.

Methodology note / honest limitation: the LMA normally receives the
Connector's already-patched module (library substituted, glue adapted) as its
seed, not the pristine original -- that intermediate state isn't persisted
anywhere in this repo. This script seeds from the PRISTINE ORIGINAL instead,
which is a disclosed simplification: it still exercises the exact same
generate() -> contract_check -> oracle.check() -> (accept/reject) loop the
live pipeline uses, just starting one step earlier than a historical replay
would.
"""
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
CORPUS = "/Users/benz.mags/Downloads/evaluation-2"
OUT = os.path.join(REPO, "live_llm_golden_check_results.json")

os.chdir(REPO)
sys.path.insert(0, REPO)

from equivalence.oracle import ProgramOracle, OracleResult  # noqa: E402
from smart_loader.isomorph import llm_variant  # noqa: E402
from smart_loader.core.llm_factory import _get_llm  # noqa: E402
from smart_loader.core.llm_json import coerce_llm_text  # noqa: E402


class ProgramOracleNoGolden(ProgramOracle):
    """Identical to ProgramOracle.check_many, Layer 1 (golden) skipped.

    Probes still run (so an execution failure still fails); only the
    `actual != expected` comparison against golden.json is removed.
    """

    def check_many(self, sources: dict) -> OracleResult:
        import shutil
        import subprocess

        started = time.time()
        work = self._stage()
        try:
            for module_name, variant_source in sources.items():
                with open(os.path.join(work, f"{module_name}.py"), "w",
                          encoding="utf-8") as handle:
                    handle.write(variant_source)

            for name in self.modules:
                proc = subprocess.run(
                    [sys.executable, "-c", f"import {name}"], cwd=work,
                    capture_output=True, text=True, timeout=self.timeout,
                )
                if proc.returncode != 0:
                    tail = (proc.stderr or "").strip().splitlines()
                    return OracleResult(
                        False, f"import {name} failed: "
                               f"{tail[-1][:150] if tail else '?'}",
                        time.time() - started)

            # Layer 1 (golden) ABLATED. Run the probes anyway, so a variant
            # that cannot even execute still fails -- that is an execution
            # error, not a golden-vector comparison.
            actual, err = self._run(work, "probes")
            if actual is None:
                return OracleResult(
                    False, f"(golden ablated) probes failed to execute: {err}",
                    time.time() - started)

            violations, err = self._run(work, "invariants")
            if violations is None:
                return OracleResult(False, err, time.time() - started)
            if violations:
                return OracleResult(
                    False, f"invariant violated: {violations[0][:160]}",
                    time.time() - started)

            ok, detail = self._tests(work)
            if not ok:
                return OracleResult(False, f"tests failed: {detail}",
                                    time.time() - started)

            return OracleResult(
                True, "golden ablated; invariants+tests+imports passed",
                time.time() - started, script_checked=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)


PROGRAMS = [
    ("A", "A1_crc_telemetry", "crc_telemetry"),
    ("B", "B2_modbus_meter_reader", "modbus_meter_reader"),
    ("C", "C2_tank_level_fsm", "fsm"),
    ("D", "D1_boiler_control_loop", "supervisor"),
]


def make_ask():
    client = _get_llm("claude", "claude-sonnet-5", disable_thinking=True)

    def _ask(prompt: str) -> str:
        reply = client.invoke(prompt)
        return coerce_llm_text(getattr(reply, "content", reply))
    return _ask


def main():
    results = []
    for category, program_name, module in PROGRAMS:
        program_dir = os.path.join(CORPUS, program_name)
        with open(os.path.join(program_dir, f"{module}.py"), encoding="utf-8") as f:
            source = f.read()
        siblings = {n[:-3] for n in os.listdir(program_dir)
                    if n.endswith(".py") and not n.startswith("test_")}

        for condition, oracle_cls in [("real_with_golden", ProgramOracle),
                                       ("ablated_no_golden", ProgramOracleNoGolden)]:
            print(f"\n=== {program_name} ({category}) / {module} / {condition} ===")
            oracle = oracle_cls(program_dir, program_name)
            events = []

            def on_event(attempt, kind, detail, _events=events):
                _events.append({"attempt": attempt, "kind": kind, "detail": str(detail)})
                print(f"  attempt {attempt}: {kind} - {str(detail)[:160]}")

            _ask = make_ask()
            t0 = time.time()
            result = llm_variant.generate(
                oracle, module, source, _ask, attempts=3,
                siblings=siblings, on_event=on_event,
            )
            dt = time.time() - t0
            print(f"  -> oracle_passed={result.oracle_passed}  "
                  f"candidates_generated={result.candidates_generated}  "
                  f"candidates_equivalent={result.candidates_equivalent}  "
                  f"({dt:.1f}s)")
            print(f"  -> final detail: {result.oracle_detail[:200]}")

            results.append({
                "category": category, "program": program_name, "module": module,
                "condition": condition,
                "oracle_passed": result.oracle_passed,
                "oracle_detail": result.oracle_detail,
                "candidates_generated": result.candidates_generated,
                "candidates_equivalent": result.candidates_equivalent,
                "duration_s": round(dt, 1),
                "events": events,
            })
            # Write incrementally so partial progress survives any failure.
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)

    print(f"\n\nWrote {len(results)} records to {OUT}")


if __name__ == "__main__":
    main()
