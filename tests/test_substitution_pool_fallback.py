"""The profiler must not be able to veto a required substitution into a no-op.

Regression test for the seven proteus rows that spent 6-14 LLM calls and left
the target file byte-identical. The chain was:

    resolver proposes pymodbus -> {minimalmodbus, pyModbusTCP}
    profiler cannot install either in its sandbox  -> hard_install_failure
    both alternatives vetoed                       -> pool empty
    incumbent wins by default                      -> patched_code == source
    unmodified program passes its own tests        -> gen_success = Y

The last step is what makes it dangerous: the refusal is recorded as a
success. ``hard_install_failure`` is a fact about our sandbox, not evidence the
library is unfit, so under ``require_substitution`` the vetoed alternatives are
reinstated and the Verifier stays the real gate.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.axiom_agent import _compute_scores
from smart_loader.core.state import BenchmarkResult, EquivalenceGroup


def _group():
    return EquivalenceGroup(
        role="Modbus protocol client",
        candidates=["pymodbus", "minimalmodbus", "pyModbusTCP"],
        used_apis=["ModbusTcpClient", "ModbusTcpClient.read_holding_registers"],
        reasoning="test fixture", pitfalls=[],
    )


def _benchmarks(status: str):
    """Incumbent measurable, both alternatives failing to install."""
    return {
        "pymodbus": BenchmarkResult(package="pymodbus", import_time_ms=12.0,
                                    available=True, profile_status="ok"),
        "minimalmodbus": BenchmarkResult(package="minimalmodbus",
                                         import_time_ms=0.0, available=False,
                                         profile_status=status),
        "pyModbusTCP": BenchmarkResult(package="pyModbusTCP",
                                       import_time_ms=0.0, available=False,
                                       profile_status=status),
    }


def _run(require_substitution: bool, status: str = "hard_install_failure"):
    winner, _confidence, scores, _crypto, _not_installed = _compute_scores(
        _group(), _benchmarks(status), {}, "claude", "claude-sonnet-5",
        "test-session", "modbus_meter_reader.py",
        require_substitution=require_substitution,
    )
    return winner, (scores.get(winner) or {}).get("selection_basis")


def test_vetoed_alternatives_are_reinstated_when_substitution_required():
    """The bug: this returned the incumbent, and the run became a silent no-op."""
    winner, basis = _run(require_substitution=True)
    assert winner != "pymodbus", (
        f"substitution was required but AXIOM kept the incumbent "
        f"(basis={basis!r}) — this is the no-op that scores as success")
    assert winner in {"minimalmodbus", "pyModbusTCP"}


def test_optimisation_semantics_are_unchanged():
    """Without require_substitution, keeping the incumbent is still allowed.

    The fix must not leak into ordinary optimisation runs, where declining to
    migrate is a legitimate answer.
    """
    winner, _basis = _run(require_substitution=False)
    assert winner == "pymodbus"


def test_a_real_alternative_still_beats_a_vetoed_one():
    """The fallback is last-resort only: an installable alternative wins."""
    benchmarks = _benchmarks("hard_install_failure")
    benchmarks["pyModbusTCP"] = BenchmarkResult(
        package="pyModbusTCP", import_time_ms=8.0, available=True,
        profile_status="ok")
    winner, _c, _s, _cv, _ni = _compute_scores(
        _group(), benchmarks, {}, "claude", "claude-sonnet-5",
        "test-session", "modbus_meter_reader.py", require_substitution=True)
    assert winner == "pyModbusTCP"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
