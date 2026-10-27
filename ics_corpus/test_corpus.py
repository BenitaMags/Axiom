"""Test suite / equivalence oracle for the diversification corpus.

Three layers:
  1. golden regression -- every scenario and probe must reproduce golden.json
  2. invariants        -- properties that must hold for ANY input
  3. coverage          -- proves the probes actually reach every branch/state,
                          so layer 1 is known to cover the whole control graph

Usage as a diversification oracle:
    python make_golden.py         # once, on the pristine corpus
    # ... diversify a module in place ...
    python test_corpus.py         # PASS => variant is equivalent

Runs standalone (no dependencies). Also discoverable by pytest if installed:
    pytest test_corpus.py

TOL controls float comparison for the golden layer. 0.0 = bit-exact; raise to
e.g. 1e-9 when testing transforms that reassociate floating-point expressions
(see README on IEEE-754 non-associativity).
"""

import json
import os

import oracle

TOL = 0.0
_GOLD = None


def _gold():
    global _GOLD
    if _GOLD is None:
        if not os.path.exists("golden.json"):
            import make_golden
            with open("golden.json", "w") as f:
                json.dump(make_golden.build(), f, indent=1)
        with open("golden.json") as f:
            _GOLD = json.load(f)
    return _GOLD


def _all_strings(obj):
    out = []

    def rec(o):
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, (list, tuple)):
            for x in o:
                rec(x)

    rec(obj)
    return out


# --- Layer 1: golden regression ---------------------------------------------
def test_scenarios_match_golden():
    for name in oracle.MODULES:
        got = list(oracle.load(name).run_scenario())
        assert oracle.approx_equal(got, _gold()[name]["scenario"], TOL), \
            f"scenario mismatch in {name}"


def test_probes_match_golden():
    for name in oracle.MODULES:
        m = oracle.load(name)
        for pn, fn in oracle.PROBES[name].items():
            assert oracle.approx_equal(fn(m), _gold()[name]["probes"][pn], TOL), \
                f"probe mismatch: {name}.{pn}"


# --- Layer 2: invariants ----------------------------------------------------
def test_invariants_hold():
    for name in oracle.MODULES:
        m = oracle.load(name)
        for desc, ok in oracle.INVARIANTS[name](m):
            assert ok, f"invariant failed in {name}: {desc}"


# --- Layer 3: coverage ------------------------------------------------------
def test_probes_reach_every_state():
    def strings_of(module_name):
        m = oracle.load(module_name)
        seen = set()
        for fn in oracle.PROBES[module_name].values():
            seen |= set(_all_strings(fn(m)))
        return seen

    assert {"OFF", "ON", "FAULT"} <= strings_of("pump_controller")
    assert {"CLOSED", "TRIPPED", "RECLOSING", "LOCKOUT"} <= strings_of("protective_relay")
    assert {"NORMAL", "WARN", "ALARM"} <= strings_of("sensor_pipeline")


def test_probes_reach_command_saturation():
    p = oracle.load("pid_governor")
    hi = any(cmd == 100.0 for _, cmd in oracle.PROBES["pid_governor"]["sat_high"](p))
    lo = any(cmd == 0.0 for _, cmd in oracle.PROBES["pid_governor"]["sat_low"](p))
    assert hi, "sat_high probe never reached the upper command clamp"
    assert lo, "sat_low probe never reached the lower command clamp"


# --- standalone runner ------------------------------------------------------
def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS  {n}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {n}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
