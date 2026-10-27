"""A source package can be replaced once, not twice.

The resolver can raise two equivalence groups rooted at the same import, and
AXIOM then picks a winner for each. Both are coherent as ADVICE and incoherent
as an EDIT: a file imports `pymodbus` once, and it cannot become two different
libraries.

Measured on D1 seed 1 -- 68 LLM calls, $3.27, nothing shipped. AXIOM emitted::

    Modbus TCP client for reading/writing holding registers
        pymodbus -> modbus-tk        api 0.1   total 0.702
    Modbus client (serial-focused, weak fit for TCP use case)
        pymodbus -> minimalmodbus    api 0.0   total 0.606

The Connector applied both to the same file in sequence. The first rewired
``ModbusTcpClient(...)`` to ``_axiom_modbustk_client_wrapper``; the second
could no longer find the call site it was written for, so it defined
``_axiom_minimalmodbus_client_wrapper`` with nothing calling it. The
uncalled-wrapper guard fired -- correctly -- and reverted the edit. Every
repair cycle re-ran both decisions and reproduced it: the run alternated
targets four times and ended migration_success=N with the source untouched.

It was the only one of twelve proteus_full runs with two distinct targets, and
the only one to burn its entire repair budget this way.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AXIOM = os.path.join(ROOT, "smart_loader/agents/axiom_agent.py")


def _dedupe(decisions):
    """The rule as implemented, exercised on plain objects."""
    best = {}
    for d in decisions:
        if d.winner == d.original:
            continue
        total = (d.score or {}).get("total", 0.0) if isinstance(d.score, dict) else 0.0
        kept = best.get(d.original)
        if kept is None or total > kept[0]:
            best[d.original] = (total, d)
    dropped = {id(d) for d in decisions
               if d.winner != d.original
               and best.get(d.original, (0, None))[1] is not d}
    return [d for d in decisions if id(d) not in dropped]


class D:
    def __init__(self, original, winner, total):
        self.original, self.winner = original, winner
        self.score = {"total": total}


def test_the_d1_case_keeps_only_the_higher_scoring_decision():
    kept = _dedupe([D("pymodbus", "modbus-tk", 0.702),
                    D("pymodbus", "minimalmodbus", 0.606)])
    assert [d.winner for d in kept] == ["modbus-tk"]


def test_order_does_not_matter():
    kept = _dedupe([D("pymodbus", "minimalmodbus", 0.606),
                    D("pymodbus", "modbus-tk", 0.702)])
    assert [d.winner for d in kept] == ["modbus-tk"]


def test_different_sources_are_both_kept():
    """Two packages replaced in one program is normal and must survive."""
    kept = _dedupe([D("requests", "httpx", 0.9), D("paho-mqtt", "aiomqtt", 0.8)])
    assert len(kept) == 2


def test_a_retained_original_is_not_treated_as_a_replacement():
    kept = _dedupe([D("pymodbus", "pymodbus", 1.0), D("requests", "httpx", 0.9)])
    assert [d.winner for d in kept] == ["pymodbus", "httpx"]


def test_a_tie_keeps_the_first_which_is_the_resolver_ranking():
    kept = _dedupe([D("pymodbus", "modbus-tk", 0.5),
                    D("pymodbus", "minimalmodbus", 0.5)])
    assert [d.winner for d in kept] == ["modbus-tk"]


def test_a_missing_score_does_not_crash():
    a, b = D("pymodbus", "modbus-tk", 0.7), D("pymodbus", "minimalmodbus", 0.6)
    b.score = None
    assert [d.winner for d in _dedupe([a, b])] == ["modbus-tk"]


# ------------------------------------------------------- wired into AXIOM ---

def test_axiom_applies_the_rule_before_computing_replacements():
    source = open(AXIOM, encoding="utf-8").read()
    assert "best_by_source" in source, "the rule must live in axiom_agent"
    assert "one replacement per source package" in source.lower()
    # It has to run BEFORE drop-in classification, or a superseded decision
    # still reaches the rewrite.
    assert source.index("best_by_source") < source.index("drop_in_replacements: dict")


def test_the_dropped_decision_is_announced_not_silent():
    source = open(AXIOM, encoding="utf-8").read()
    window = source[source.index("best_by_source"):]
    window = window[:window.index("drop_in_replacements: dict")]
    assert "dropping the lower-scoring decision" in window
