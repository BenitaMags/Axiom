"""An unfixable fidelity finding must not revert a working migration.

The verifier's repair budget is global -- five cycles for the whole run, not
per file -- and when it is exhausted the file is reverted to its ORIGINAL
source. That is correct for code that does not work. It is actively harmful
for code that works but is less faithful about failure: the revert produces a
no-op, which is worse for a user than a working migration with a known wart,
and scores worse on every metric.

Measured against the archive: seven proteus_full runs currently succeed while
carrying an added swallowing handler. Routing those through the revert path
would turn seven working migrations into seven no-ops.

The second case here pins the bug that fix introduced. Syntax, smoke and
project-test failures were appended to the COMBINED list after the split, so
they never reached ``blocking_errors`` -- meaning a patched syntax failure
would have driven repair cycles and then been accepted once the budget ran
out.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VERIFIER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "smart_loader", "agents", "verifier_agent.py")


def _assignments_to(name: str) -> list[ast.AST]:
    tree = ast.parse(open(VERIFIER, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found.append(node.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "append"
                    and isinstance(func.value, ast.Name) and func.value.id == name):
                found.append(node)
    return found


def test_error_contract_never_drives_a_repair():
    """It is recorded, not acted on.

    Driving repair with it made things WORSE. D1's original has no try/except
    because pymodbus reports failure through ``rr.isError()``; the faithful
    adapter translates modbus-tk's exception into that status, which this check
    reads as new swallowing. The Connector was told to re-raise, ``isError()``
    became unreachable, both ``raise IOError(...)`` branches became dead code,
    and a blind judge rated the result MAJOR twice.
    """
    source = open(VERIFIER, encoding="utf-8").read()
    assert 'recorded_error_contract = [e for e in completeness_errors' in source

    # The invariant is about error_contract, not about advisory_errors being
    # empty. This used to assert the literal `advisory_errors: list[dict] = []`
    # as a proxy, which held only while NOTHING was advisory; it broke the day
    # a different check -- contract erosion, whose repairs only restore what
    # the original had -- was legitimately routed there, while saying nothing
    # about whether error_contract had leaked in. So check the real thing:
    # whatever advisory_errors is built from, it must not be error_contract.
    start = source.index("advisory_errors: list[dict] =")
    initialiser = source[start:source.index("\n", source.index("=", start))]
    assert "error_contract" not in initialiser, \
        "error_contract must not be fed into the repair-driving list"
    assert "completeness_errors" not in initialiser, \
        "the completeness checks are routed explicitly, not swept in wholesale"

    # ... and it must not be appended later either.
    for line in source.splitlines():
        if "advisory_errors.append" in line or "advisory_errors +=" in line:
            assert "error_contract" not in line, line
        if "blocking_errors.append" in line or "blocking_errors +=" in line:
            assert "recorded_error_contract" not in line, line

    block = source[source.index("blocking_errors = ("):source.index("deterministic_errors = blocking")]
    assert 'e.get("check") != "error_contract"' in block, \
        "the blocking list must still exclude it"
    assert "recorded_error_contract" not in block, \
        "the recorded list must never be added to the blocking list"


def test_error_contract_is_still_recorded():
    source = open(VERIFIER, encoding="utf-8").read()
    assert 'check_type="error_contract"' in source
    assert '"acted_on": False' in source, "the evidence must say it was not acted on"


def test_revert_consults_blocking_only():
    source = open(VERIFIER, encoding="utf-8").read()
    idx = source.index("reverting to the original source")
    guard = source.rindex("if ", 0, idx)
    assert source[guard:idx].startswith("if blocking_errors:"), \
        "an advisory finding must never revert a working migration to a no-op"


def test_stage1_clean_is_not_held_hostage_by_an_advisory_finding():
    source = open(VERIFIER, encoding="utf-8").read()
    block = source[source.index("stage1_clean = ("):source.index("if recorded_error_contract:")]
    assert "not blocking_errors" in block
    assert "not deterministic_errors" not in block


def test_hard_failures_reach_the_blocking_list():
    """Syntax, smoke and project tests must be appended to BLOCKING.

    Appending them to the combined list leaves them out of the revert guard
    and out of ``stage1_clean``, so broken code gets accepted.
    """
    source = open(VERIFIER, encoding="utf-8").read()
    region = source[source.index("# These three are appended to BLOCKING"):
                    source.index("deterministic_errors = blocking_errors + advisory_errors")]
    assert region.count("blocking_errors.append(") == 3, \
        "syntax / smoke / project-test failures must all be blocking"
    assert "deterministic_errors.append(" not in region


def test_deterministic_errors_is_built_after_the_appends():
    """Ordering bug: building the combined list first loses the three appends."""
    source = open(VERIFIER, encoding="utf-8").read()
    combined = source.index("deterministic_errors = blocking_errors + advisory_errors")
    last_append = source.rindex("blocking_errors.append(", 0, combined)
    assert last_append < combined, \
        "deterministic_errors must be assembled AFTER the hard failures are appended"



