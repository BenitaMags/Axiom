"""Check 5: the patch must return what the original returned.

A2 migrates json -> ujson. ujson escapes forward slashes by default and the
stdlib does not, so a tag containing a slash serializes as ``"a\\/b"`` instead
of ``"a/b"``. The program's contract is byte-reproducible audit logs, so that
is a real defect -- and one flag away from correct
(``escape_forward_slashes=False``). All three A2 seeds failed on it, in every
arm, every run.

Nothing in the pipeline could see it. The suite passes: no test uses a slash.
Layer 6 constructs nothing, because A2 has no collaborator. Only the golden
vectors caught it, and the Verifier is blind to those by design -- so the
repair loop was never told what was wrong and could not fix it.

The original is the ground truth by definition, so comparing against it needs
no golden vector and keeps the scoring oracle's answer key out of the gate.

Measured before wiring: zero false positives across all twelve originals
compared with themselves, and it fires on two of thirty-six clones, both
already failing. It never fires on a passing row -- the property check 4
lacked, which is why check 4 is recorded rather than repaired.
"""
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "equivalence"))

import pytest  # noqa: E402

from differential import check, _plan  # noqa: E402
from smart_loader.agents.migration_completeness import behaviour_errors  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")


def test_identical_modules_never_diverge():
    src = "def f(x: str) -> str:\n    return x.upper()\n"
    assert check(src, src) == ("OK", "no behavioural divergence on generated inputs")


def test_a_changed_return_value_is_caught():
    before = "def f(x: str) -> str:\n    return x.upper()\n"
    after = "def f(x: str) -> str:\n    return x.lower()\n"
    verdict, detail = check(before, after)
    assert verdict == "DEFECT"
    assert "f(" in detail or "f[" in detail


def test_a_changed_exception_is_caught():
    before = "def f(x: str) -> str:\n    raise ValueError(x)\n"
    after = "def f(x: str) -> str:\n    raise TypeError(x)\n"
    assert check(before, after)[0] == "DEFECT"


def test_a_slash_escaping_difference_is_caught():
    """The A2 shape, without needing ujson installed."""
    before = textwrap.dedent("""
        def dump(tag: str) -> str:
            return '{"tag":"%s"}' % tag
    """)
    after = textwrap.dedent("""
        def dump(tag: str) -> str:
            return '{"tag":"%s"}' % tag.replace("/", "\\\\/")
    """)
    assert check(before, after)[0] == "DEFECT"


def test_private_functions_are_ignored():
    before = "def _helper(x: str) -> str:\n    return x\n"
    after = "def _helper(x: str) -> str:\n    return x.upper()\n"
    assert check(before, after)[0] == "OK", "private functions are not the contract"


def test_functions_taking_a_collaborator_are_skipped():
    """Calling these opens sockets; that is layer 6's job, with a real double."""
    src = "def publish(client, topic: str):\n    return client.publish(topic)\n"
    assert _plan(src) == []


def test_syntax_error_yields_no_plan():
    assert _plan("def f(\n") == []


def test_a_function_with_unusable_parameters_is_skipped():
    src = "def f(handler: SomeCustomProtocol):\n    return handler\n"
    assert _plan(src) == []


# ── The corpus ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_no_original_diverges_from_itself():
    """Zero false positives is the bar; a spurious DEFECT here reverts good code."""
    from smart_loader.eval.proteus_harness import discover_programs
    for program in discover_programs(CORPUS):
        for name in program.source_files:
            src = open(os.path.join(program.path, name), encoding="utf-8").read()
            verdict, detail = check(src, src)
            assert verdict == "OK", f"{program.name}/{name}: {detail}"


@pytest.mark.skipif(
    not os.path.isfile("clones_full_v2/proteus_full/A2_sensor_json_logger_run2/"
                       "sensor_json_logger.py"),
    reason="v2 clones not present")
def test_the_real_a2_clone_is_caught():
    original = open(os.path.join(CORPUS, "A2_sensor_json_logger",
                                 "sensor_json_logger.py"), encoding="utf-8").read()
    clone = open("clones_full_v2/proteus_full/A2_sensor_json_logger_run2/"
                 "sensor_json_logger.py", encoding="utf-8").read()
    errors = behaviour_errors(original, clone)
    assert errors, "the escaping difference must reach the Connector"
    assert "different from the original" in errors[0]["message"]
    assert behaviour_errors(original, original) == []
