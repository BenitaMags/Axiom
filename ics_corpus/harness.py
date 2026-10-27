"""Equivalence oracle for the diversification corpus.

Every module exposes a deterministic `run_scenario()` returning a list of output
samples. This harness runs each one and prints a behavioral fingerprint. Diversify
a module, re-run the harness, and compare fingerprints:

    * fingerprints identical  -> variant is behaviorally equivalent
    * fingerprints differ     -> the transformation changed observable behavior

Two fingerprints are reported per module:

    exact  : sha256 over repr() of the raw outputs. Bit-for-bit. Use this for
             transformations that must preserve results exactly (renaming,
             statement reordering of independent ops, branch inversion, ...).

    rounded: sha256 over outputs rounded to 6 decimals. Use this for
             transformations that touch float arithmetic (e.g. expression
             reassociation), where the last bits may legitimately drift because
             IEEE-754 addition/multiplication are NOT associative.

Run:  python harness.py
"""

import hashlib
import importlib

MODULES = [
    "pid_governor",
    "pump_controller",
    "protective_relay",
    "sensor_pipeline",
]


def _round_deep(obj, places=6):
    if isinstance(obj, float):
        return round(obj, places)
    if isinstance(obj, (list, tuple)):
        return type(obj)(_round_deep(x, places) for x in obj)
    return obj


def _digest(obj) -> str:
    return hashlib.sha256(repr(obj).encode("utf-8")).hexdigest()[:16]


def fingerprint(module_name: str) -> dict:
    mod = importlib.import_module(module_name)
    samples = mod.run_scenario()
    return {
        "module": module_name,
        "steps": len(samples),
        "exact": _digest(samples),
        "rounded": _digest(_round_deep(samples)),
        "head": samples[0],
        "tail": samples[-1],
    }


def main() -> None:
    print(f"{'module':<20}{'steps':>7}  {'exact':<18}{'rounded':<18}")
    print("-" * 65)
    for name in MODULES:
        fp = fingerprint(name)
        print(f"{fp['module']:<20}{fp['steps']:>7}  "
              f"{fp['exact']:<18}{fp['rounded']:<18}")
    print()
    print("Diversify a module, re-run this harness, and diff the columns above.")


if __name__ == "__main__":
    main()
