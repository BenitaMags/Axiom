"""A repair that changes nothing the verifier sees must not be retried.

Measured on a D1 run: the Connector's ACI edit defined a wrapper nothing
called, the unused-wrapper guard reverted it to a byte-identical file, and the
next cycle produced the same six findings. Five cycles, four of them provably
pointless, at roughly two minutes each.

Keyed on the verifier's own findings rather than on the source: the question is
whether the repair changed the OUTCOME, and an edit that rewrites the file and
still trips the same checks has not made progress either.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import smart_loader.agents.verifier_agent as V  # noqa: E402


def _reserve(state, trace, errors, route="connector", failure_class="patch_or_static"):
    return V._reserve_repair_cycle(state, trace, route=route,
                                   failure_class=failure_class,
                                   roles=["role"], errors=errors)


def test_an_identical_second_cycle_is_refused():
    trace = []
    errors = [{"line": 1, "message": "Name 'cst' is not defined"}]
    fb1, hist1 = _reserve({}, trace, errors)
    assert fb1 is not None and fb1["cycle"] == 1

    state = {"_repair_cycle_count": 1, "_repair_history": hist1}
    fb2, hist2 = _reserve(state, trace, errors)
    assert fb2 is None, "the same findings twice cannot be repaired by retrying"
    assert len(hist2) == 1, "the stalled cycle must not be recorded as spent"
    assert any(e.event == "repair_stalled" for e in trace)


def test_different_findings_still_get_a_cycle():
    trace = []
    fb1, hist1 = _reserve({}, trace, [{"line": 1, "message": "first"}])
    state = {"_repair_cycle_count": 1, "_repair_history": hist1}
    fb2, _ = _reserve(state, trace, [{"line": 2, "message": "second"}])
    assert fb2 is not None and fb2["cycle"] == 2


def test_the_same_findings_on_a_different_route_still_get_a_cycle():
    """A different agent has not had its turn at this failure yet."""
    trace = []
    errors = [{"line": 1, "message": "same"}]
    fb1, hist1 = _reserve({}, trace, errors, route="connector")
    state = {"_repair_cycle_count": 1, "_repair_history": hist1}
    fb2, _ = _reserve(state, trace, errors, route="axiom")
    assert fb2 is not None


def test_the_budget_still_applies():
    trace = []
    state = {"_repair_cycle_count": V.MAX_REPAIR_CYCLES, "_repair_history": []}
    fb, _ = _reserve(state, trace, [{"line": 1, "message": "x"}])
    assert fb is None
