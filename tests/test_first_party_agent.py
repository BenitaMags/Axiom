"""The FRA path: reimplementation instead of library selection.

C1 (`simple-pid` -> in-house controller) and C2 (`transitions` -> hand-rolled
FSM) ask for the dependency to be removed and its behaviour written as
first-party code. The LSA selects among libraries and cannot express that, so
both subjects returned the file untouched on all six runs of both Proteus
configurations. These tests cover the routing and the acceptance contract; the
generation itself needs a provider and is exercised end-to-end separately.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.core.graph import build_graph
from smart_loader.core.migration_finalize import (
    FinalizeRequest, finalize_migration, remaining_source_imports)
from smart_loader.agents.first_party_agent import _top_level_names, _used_surface

ORIGINAL = '''\
"""controller -- PID pump controller."""
from io_map import speed_to_counts


def _make_pid(kp, ki, kd, setpoint):
    from simple_pid import PID  # lazy import for testability

    return PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))


class PumpController:
    def __init__(self, setpoint, pid=None):
        self.pid = pid if pid is not None else _make_pid(2.0, 0.5, 0.0, 0.0)
'''

REIMPLEMENTED = '''\
"""controller -- PID pump controller."""
from io_map import speed_to_counts


class PID:
    def __init__(self, kp, ki, kd, setpoint=0.0, output_limits=(None, None)):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.setpoint = setpoint
        self.output_limits = output_limits


def _make_pid(kp, ki, kd, setpoint):
    return PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))


class PumpController:
    def __init__(self, setpoint, pid=None):
        self.pid = pid if pid is not None else _make_pid(2.0, 0.5, 0.0, 0.0)
'''


# ── routing ──────────────────────────────────────────────────────────────────

def test_reimplement_mode_adds_the_fra_node():
    assert "fra" in build_graph(reimplement_mode=True).get_graph().nodes


def test_the_fra_reaches_the_lma_and_the_verifier():
    """Regression for a silently dangling node.

    The first live run had no `fra -> logic_substitution` edge. LangGraph
    compiles a node with no outgoing edge perfectly happily and terminates
    after it, so the run produced a patched file, printed no error and scored
    well -- while the Logic-Mutation Agent and the Verification Agent never
    executed. The only evidence was two missing banners in a long log.

    On this path the LMA matters more than anywhere else: the logic worth
    diversifying lived inside the third-party library and only becomes
    first-party code once the FRA has written it.
    """
    g = build_graph(reimplement_mode=True).get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("fra", "logic_substitution") in edges, (
        "the FRA terminates the graph; the LMA and VCA would never run")
    assert ("logic_substitution", "verifier") in edges


def test_every_reachable_node_leads_somewhere():
    """A reachable node with no outgoing edge silently ends the run.

    Scoped to REACHABLE nodes deliberately. In reimplement mode the
    library-substitution nodes (resolver, profiler, axiom, connector, ...) are
    still registered but nothing routes to them, which is intended and not a
    defect -- there is no library to choose. What must never happen is a node
    that the pipeline actually enters and then falls out of.
    """
    for mode in (False, True):
        g = build_graph(reimplement_mode=mode).get_graph()
        out: dict = {}
        for e in g.edges:
            out.setdefault(e.source, set()).add(e.target)

        reachable, frontier = set(), ["__start__"]
        while frontier:
            node = frontier.pop()
            if node in reachable:
                continue
            reachable.add(node)
            frontier.extend(out.get(node, ()))

        assert "fra" in reachable if mode else "fra" not in reachable
        for node in reachable:
            if node == "__end__":
                continue
            assert out.get(node), (
                f"reimplement_mode={mode}: {node!r} is reachable but has no "
                f"outgoing edge, so the run terminates there silently")


def test_the_normal_path_is_unchanged():
    """A library migration must not acquire an FRA node."""
    nodes = build_graph(reimplement_mode=False).get_graph().nodes
    assert "fra" not in nodes
    assert {"resolver", "profiler", "axiom", "connector"} <= set(nodes)


# ── acceptance contract ──────────────────────────────────────────────────────

def test_a_completed_reimplementation_is_accepted():
    r = finalize_migration(ORIGINAL, REIMPLEMENTED,
                           [FinalizeRequest(original="simple-pid")])
    assert r.complete, r.survivors


def test_a_surviving_lazy_import_is_rejected():
    """The import is inside a function -- the case that broke B3 and D3."""
    left = REIMPLEMENTED.replace(
        "def _make_pid(kp, ki, kd, setpoint):\n    return PID(",
        "def _make_pid(kp, ki, kd, setpoint):\n    from simple_pid import PID\n    return PID(")
    r = finalize_migration(ORIGINAL, left, [FinalizeRequest(original="simple-pid")])
    assert not r.complete
    assert "simple_pid" in r.survivors["simple-pid"][0]


def test_a_shadowed_module_level_import_is_rejected():
    """Defining PID inline while leaving the import still needs the package."""
    left = "from simple_pid import PID\n" + REIMPLEMENTED
    r = finalize_migration(ORIGINAL, left, [FinalizeRequest(original="simple-pid")])
    assert not r.complete


def test_an_inline_replacement_is_never_redirected_to_a_new_module():
    """With no winner_import there is no module to point at.

    Rewriting the import to `from pid_engine import PID` when nothing defines
    `pid_engine` would trade a missing dependency for an ImportError.
    """
    r = finalize_migration(ORIGINAL, REIMPLEMENTED,
                           [FinalizeRequest(original="simple-pid")])
    assert r.import_rewrites == {}
    assert "pid_engine" not in r.code


# ── guards on the rewrite ────────────────────────────────────────────────────

def test_deleting_a_public_definition_is_detectable():
    """A rewrite that drops PumpController is a different program, and the
    suite may not notice. The FRA's loop rejects on this."""
    gutted = REIMPLEMENTED.split("class PumpController")[0]
    assert "PumpController" in _top_level_names(ORIGINAL) - _top_level_names(gutted)


def test_the_used_api_surface_is_scoped_to_this_file():
    """Only what the program actually uses, so 'preserve it exactly' is
    satisfiable rather than a request to reimplement a whole library."""
    class Imp:
        module = "simple_pid"
        names = ["PID"]
        api_calls = ["PID", "PID.setpoint"]
    surface = _used_surface({"imports": [Imp()]}, "simple-pid")
    assert "PID" in surface and "setpoint" in surface


def test_remaining_source_imports_ignores_unrelated_libraries():
    assert remaining_source_imports(REIMPLEMENTED, "io_map") != []
    assert remaining_source_imports(REIMPLEMENTED, "simple_pid") == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── the behavioural gate ──────────────────────────────────────────────────
#
# The cheap checks are blind to a behavioural regression. A C1 rewrite passed
# compile, surface-preservation and all five of the program's own tests while
# its PID constructor raised AttributeError for any output limit, because every
# test injects a FakePID and never constructs the real class.

BROKEN_PID = '''
class PID:
    def __init__(self, Kp=1.0, output_limits=(None, None)):
        self.Kp = Kp
        self._min_output, self._max_output = None, None
        self.output_limits = output_limits      # setter reads _integral ...
        self._integral = 0.0                   # ... which is defined here

    @property
    def output_limits(self):
        return self._min_output, self._max_output

    @output_limits.setter
    def output_limits(self, limits):
        self._min_output, self._max_output = limits
        self._integral = min(self._integral, 1.0)
'''


def test_the_broken_constructor_pattern_really_does_raise():
    """Pin the defect itself, so the gate is guarding something real."""
    ns = {}
    exec(compile(BROKEN_PID, "<broken>", "exec"), ns)
    try:
        ns["PID"](1.0, (0.0, 5.0))
    except AttributeError as exc:
        assert "_integral" in str(exc)
    else:
        raise AssertionError("expected AttributeError on _integral")


def test_the_gate_abstains_when_no_oracle_corpus_is_configured():
    from smart_loader.agents.first_party_agent import _behaviour_check
    ok, detail = _behaviour_check({}, "controller.py", "X = 1\n")
    assert ok is True and detail == ""


def test_the_gate_abstains_when_the_oracle_cannot_run():
    """An unusable oracle must not pass silently nor fail a good rewrite."""
    from smart_loader.agents.first_party_agent import _behaviour_check
    ok, _ = _behaviour_check(
        {"isomorph_program_dir": "/nonexistent/program/dir",
         "isomorph_program_name": "C1_pid_pump_controller"},
        "controller.py", "X = 1\n")
    assert ok is True


def test_the_gate_is_wired_into_the_repair_loop():
    """The verdict must gate the success path, not just be computed."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "smart_loader/agents/first_party_agent.py"),
               encoding="utf-8").read()
    assert "ok, detail = (_behaviour_check(state, filename, result.code)" in src
    assert "if result.complete and not ok:" in src
    assert "elif result.complete:" in src, (
        "the success path must be reachable only when the gate passes"
    )


def test_the_behavioural_rejection_is_not_overwritten_by_the_import_message():
    """The three branches must be mutually exclusive.

    The incomplete-migration message used to be a fall-through, so a COMPLETE
    rewrite rejected by the behavioural gate had its oracle diagnosis replaced
    by "`simple-pid` is still imported and in use ()" -- with an empty survivor
    list, because there were none. The model was told the wrong thing.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "smart_loader/agents/first_party_agent.py"),
               encoding="utf-8").read()
    head = src.index("if result.complete and not ok:")
    tail = src.index("is still imported and in use", head)
    between = src[head:tail]
    assert "elif result.complete:" in between
    assert "\n                    else:\n" in between, (
        "the incomplete-migration branch is a fall-through, so it overwrites "
        "the behavioural gate's feedback"
    )


# ── the reference implementation and the incremental repair loop ───────────

def test_the_reference_recovers_the_real_library_source():
    from smart_loader.agents.first_party_agent import _reference_implementation
    pytest = __import__("pytest")
    try:
        import simple_pid                                     # noqa: F401
    except ImportError:
        pytest.skip("simple-pid not installed")
    ref = _reference_implementation("simple-pid", "from simple_pid import PID")
    # The three behaviours three rounds of review found missing. If the budget
    # or the extraction regresses, these are what silently disappear.
    assert "def reset" in ref
    assert "self._last_time = self.time_fn()" in ref       # constructor clock init
    assert "differential_on_measurement" in ref            # derivative basis
    assert "self._last_output is not None" in ref          # the sample_time hold
    assert "(truncated)" not in ref


def test_the_reference_is_empty_when_the_library_is_absent():
    """A missing library must degrade to the old prompt, not crash."""
    from smart_loader.agents.first_party_agent import _reference_implementation
    assert _reference_implementation("no-such-distribution-xyz", "") == ""


def test_the_retry_prompt_carries_the_rejected_attempt():
    from smart_loader.agents.first_party_agent import RETRY
    text = RETRY.format(error="invariant violated: derivative on error",
                        attempt="class PID:\n    pass\n")
    assert "class PID" in text
    assert "derivative on error" in text
    assert "do not start over from scratch" in text


def test_the_repair_loop_is_incremental_not_a_restart():
    """The loop must send the rejected attempt, not only the original file."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "smart_loader/agents/first_party_agent.py"),
               encoding="utf-8").read()
    assert "last_attempt = candidate or last_attempt" in src
    assert "RETRY.format(error=last_error, attempt=last_attempt)" in src


def test_the_system_prompt_names_the_easily_missed_behaviours():
    from smart_loader.agents.first_party_agent import SYSTEM
    text = SYSTEM.format(original="x", surface="", reference="")
    for cue in ("caching or rate limiting", "very first call",
                "rate-of-change term is computed from",
                "initialise every internal attribute BEFORE assigning"):
        assert cue in text, cue


# ── the verbatim-copy guard ────────────────────────────────────────────────
#
# Supplying the library's own source as prompt context made the model vendor it:
# three C1 runs at temperature 0.7 returned byte-identical files and 73% of
# `simple_pid.PID`'s code lines appeared in runs of four or more. That passes
# every behavioural check by construction, because it IS the library.

def _pid_src():
    import inspect
    simple_pid = __import__("simple_pid")
    return inspect.getsource(simple_pid.PID)


def test_a_verbatim_copy_of_the_library_is_detected():
    pytest = __import__("pytest")
    try:
        import simple_pid                                     # noqa: F401
    except ImportError:
        pytest.skip("simple-pid not installed")
    from smart_loader.agents.first_party_agent import (
        MAX_VERBATIM_OVERLAP, _verbatim_overlap)
    overlap = _verbatim_overlap(_pid_src(), "simple-pid",
                                "from simple_pid import PID")
    assert overlap > MAX_VERBATIM_OVERLAP
    assert overlap > 0.9, f"a straight copy scored only {overlap:.0%}"


def test_unrelated_code_scores_zero():
    pytest = __import__("pytest")
    try:
        import simple_pid                                     # noqa: F401
    except ImportError:
        pytest.skip("simple-pid not installed")
    from smart_loader.agents.first_party_agent import _verbatim_overlap
    unrelated = "def add(a, b):\n    return a + b\n"
    assert _verbatim_overlap(unrelated, "simple-pid",
                             "from simple_pid import PID") == 0.0


def test_coincident_single_lines_do_not_count_as_copying():
    """`self.Kp = Kp` matching proves nothing; only runs count."""
    pytest = __import__("pytest")
    try:
        import simple_pid                                     # noqa: F401
    except ImportError:
        pytest.skip("simple-pid not installed")
    from smart_loader.agents.first_party_agent import _verbatim_overlap
    scattered = ("class PID:\n"
                 "    def __init__(self, Kp, Ki, Kd):\n"
                 "        self.Kp = Kp\n"
                 "        zzz_unique_marker_one = 1\n"
                 "        self.Ki = Ki\n"
                 "        zzz_unique_marker_two = 2\n"
                 "        self.Kd = Kd\n")
    assert _verbatim_overlap(scattered, "simple-pid",
                             "from simple_pid import PID") < 0.10


def test_an_unreadable_library_reports_no_evidence_rather_than_guessing():
    from smart_loader.agents.first_party_agent import _verbatim_overlap
    assert _verbatim_overlap("x = 1", "no-such-distribution-xyz", "") == 0.0


def test_the_guard_runs_before_the_behavioural_gate():
    """A vendored copy passes the behavioural gate by construction.

    Rejecting it on behaviour is impossible; rejecting it on provenance is the
    point, so the overlap branch must come first.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "smart_loader/agents/first_party_agent.py"),
               encoding="utf-8").read()
    overlap_branch = src.index("overlap > MAX_VERBATIM_OVERLAP")
    behaviour_branch = src.index("elif result.complete and not ok:")
    assert overlap_branch < behaviour_branch


def test_the_library_source_is_not_supplied_by_default():
    """The reported configuration must never include the reference source."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "smart_loader/agents/first_party_agent.py"),
               encoding="utf-8").read()
    assert 'state.get("fra_supply_library_source")' in src, (
        "the library source is injected unconditionally again"
    )
