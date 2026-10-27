"""Layer 6 may excuse a clone's failure only when the original fails the SAME
operation with EXACTLY the same error.

Two defects this pins, both found by rescoring saved clones:

C3  A probe (`engine_process`) imported an `EngineConfig` the program does not
    define, so it failed on the ORIGINAL. The baseline was decided per program,
    so that one broken probe turned the real `notify` defect on every C3 clone
    into BASELINE.

B2  single_shot_repair run 2 called pyModbusTCP's ModbusClient with pymodbus's
    `unit=` keyword. The original also raised a TypeError about `unit`, but from
    ModbusClientMixin -- the INSTALLED pymodbus renamed the keyword. Same type,
    near-identical text, different library. The judge called it BROKEN; the
    per-program baseline excused it.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from equivalence import smoke_check as SC  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")


def op(label, verdict, detail=""):
    return (label, verdict, detail, "")


def test_misuse_on_one_operation_is_not_excused_by_another():
    """The C3 shape: original fails op B, clone fails op A."""
    clone = [op("notify", "MISUSE", "AttributeError: no attribute 'sendmail'"),
             op("engine_process", "UNREACHABLE", "timed out")]
    original = [op("notify", "UNREACHABLE", "Connection refused"),
                op("engine_process", "MISUSE", "ImportError: cannot import name 'X'")]
    verdict, detail = SC.compare(clone, original)
    assert verdict == "DEFECT", detail
    assert "notify" in detail


def test_same_type_different_message_is_a_defect():
    """The B2 shape: both TypeError about `unit`, different classes."""
    clone = [op("read", "MISUSE", "TypeError: ModbusClient.read_holding_registers() "
                                  "got an unexpected keyword argument 'unit'")]
    original = [op("read", "MISUSE", "TypeError: ModbusClientMixin.read_holding_registers() "
                                     "got an unexpected keyword argument 'unit'")]
    assert SC.compare(clone, original)[0] == "DEFECT"


def test_identical_failure_is_baseline():
    """An unchanged clone failing exactly like the original is the environment's fault."""
    msg = ("TypeError: ModbusClientMixin.read_holding_registers() "
           "got an unexpected keyword argument 'unit'")
    assert SC.compare([op("read", "MISUSE", msg)],
                      [op("read", "MISUSE", msg)])[0] == "BASELINE"


def test_paths_do_not_make_identical_failures_look_different():
    clone_dir, orig_dir = "/tmp/clone_x", "/tmp/orig_y"
    c = f"ImportError: cannot import name 'Q' from 'm' ({os.path.abspath(clone_dir)}/m.py)"
    o = f"ImportError: cannot import name 'Q' from 'm' ({os.path.abspath(orig_dir)}/m.py)"
    assert SC.compare([op("a", "MISUSE", c)], [op("a", "MISUSE", o)],
                      clone_dir, orig_dir)[0] == "BASELINE"


def test_no_misuse_is_ok():
    assert SC.compare([op("a", "UNREACHABLE", "refused")], [])[0] == "OK"


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="evaluation corpus not present")
def test_no_probe_is_broken_against_its_own_original():
    """A probe that cannot even import from the ORIGINAL is broken, not strict.

    ImportError / NameError on the pristine program means the probe names
    something the program does not define. The pymodbus TypeErrors on B2/D1 are
    the environment (installed pymodbus renamed `unit`) and are allowed.
    """
    import smart_loader.eval.proteus_harness as H

    progs = {p.name: p for p in H.discover_programs(CORPUS)}
    broken = []
    for name in SC.OPERATIONS:
        p = progs[name]
        for label, verdict, detail, _ in SC.check_each(p.path, name, p.from_lib, p.to_lib):
            if verdict == "MISUSE" and detail.split(":", 1)[0] in ("ImportError",
                                                                    "NameError",
                                                                    "ModuleNotFoundError"):
                broken.append(f"{name}[{label}]: {detail[:90]}")
    assert not broken, "probes broken against their own original:\n" + "\n".join(broken)
