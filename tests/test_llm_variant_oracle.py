"""The equivalence gate must accept a variant that EXTRACTS a module.

A restructuring that splits a cohesive helper into its own file is one of the
things the Logic Substitution Agent is supposed to be able to do. Before this,
``oracle.check`` wrote only the one module it was given into its scratch copy,
so any such variant was rejected on ``ModuleNotFoundError`` no matter how
faithful it was.

Measured on a hand-written reference variant of D1 that extracts a saturation
element into ``saturation.py``: a blind LLM judge rated it CLEAN with zero
findings, and this gate failed it on ``No module named 'saturation'``.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "equivalence"))

from smart_loader.isomorph import llm_variant as LV  # noqa: E402

CORPUS = os.path.expanduser("~/Downloads/evaluation")


def test_parse_reply_separates_the_module_from_what_it_added():
    reply = ("### FILENAME: transport.py\n```python\nA = 1\n```\n\n"
             "### FILENAME: saturation.py\n```python\nB = 2\n```\n")
    main, extra = LV.parse_reply(reply, "transport")
    assert main.strip() == "A = 1"
    assert extra == {"saturation.py": "B = 2\n"}


def test_a_reply_without_the_module_itself_is_unusable():
    """Helpers with no module to serve must not be shipped as a variant."""
    reply = "### FILENAME: saturation.py\n```python\nB = 2\n```\n"
    main, extra = LV.parse_reply(reply, "transport")
    assert main == ""


def test_generate_returns_no_variant_when_the_oracle_rejects():
    class Reject:
        def check(self, module, source, extra_files=None):
            class R:
                equivalent = False
                detail = "golden mismatch in: crc32"
            return R()

    res = LV.generate(Reject(), "m", "x = 1\n", llm=lambda p: (
        "### FILENAME: m.py\n```python\nx = 2\n```\n"), attempts=2)
    assert res.oracle_passed is False
    assert res.variant_source == ""        # never ship a rejected candidate
    assert "golden mismatch" in res.oracle_detail
    assert res.candidates_generated == 2 and res.candidates_equivalent == 0


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_oracle_accepts_a_variant_that_extracts_a_module():
    from oracle import ProgramOracle  # noqa: PLC0415

    d = os.path.join(CORPUS, "D1_boiler_control_loop")
    oracle = ProgramOracle(d, "D1_boiler_control_loop")
    transport = open(os.path.join(d, "transport.py"), encoding="utf-8").read()

    # The variant depends on a module it extracted. Appended rather than
    # anchored on some pattern inside the file: an anchor that stops matching
    # makes the replace a no-op and the test passes while checking nothing,
    # which is how the first version of this test "passed".
    variant = transport + "\nfrom _helper import clamp as _unused_clamp\n"
    helper = "def clamp(value, low, high):\n    return max(low, min(high, value))\n"
    assert variant != transport

    without = oracle.check("transport", variant)
    assert not without.equivalent
    assert "_helper" in without.detail or "ModuleNotFoundError" in without.detail

    with_extra = oracle.check("transport", variant,
                              extra_files={"_helper.py": helper})
    assert with_extra.equivalent, with_extra.detail


# ── wiring ───────────────────────────────────────────────────────────────────

def test_agent_falls_back_to_the_search_when_the_llm_is_rejected():
    """The LLM proposes; it does not get to override the gate.

    A rejected LLM candidate must not end the run -- the transformation search
    still has a verified variant to offer, and a small verified variant beats
    none.
    """
    body = open(os.path.join(ROOT, "smart_loader/agents/logic_substitution_agent.py"),
                encoding="utf-8").read()
    assert 'generator = str(state.get("isomorph_generator", "llm")' in body
    assert "if llm_result.oracle_passed:" in body
    assert "if result is None:\n        result = search.run(" in body, (
        "the search must still run when the LLM candidate was not accepted")


def test_runner_writes_extracted_modules_only_with_the_variant():
    """A clone that imports an extracted module must contain it.

    And only when the variant is what ships: writing them beside a REVERTED
    patch leaves an orphan file in a clone that never referenced it.
    """
    body = open(os.path.join(ROOT, "run_axiom_arms.py"), encoding="utf-8").read()
    assert 'state.get("isomorph_extra_files")' in body
    assert "if shipped is variant:" in body


def test_agent_exports_the_extracted_modules():
    body = open(os.path.join(ROOT, "smart_loader/agents/logic_substitution_agent.py"),
                encoding="utf-8").read()
    assert '"isomorph_extra_files": dict(getattr(result, "extra_files", {})' in body


def test_extracted_modules_survive_the_graph_state():
    """Undeclared keys are dropped on merge, and the clone then cannot import.

    Measured: a D1 run produced a transport.py that imported
    ``modbus_tk_bridge``, the oracle passed it, and the saved clone scored 0/5
    on ``ModuleNotFoundError: No module named 'modbus_tk_bridge'`` -- the agent
    returned the file and LoaderState discarded it.
    """
    body = open(os.path.join(ROOT, "smart_loader/core/state.py"),
                encoding="utf-8").read()
    for key in ("isomorph_extra_files", "isomorph_generator",
                "isomorph_llm_attempts"):
        assert f"    {key}:" in body, f"{key} must be declared on LoaderState"


def test_the_remaining_module_pass_gets_the_same_llm():
    """Otherwise it defaults to ollama and every attempt is refused.

    Measured on the same run: transport.py got an LLM variant while control.py
    and supervisor.py fell back to the search after three ConnectError refusals,
    so one program was diversified two different ways in a single run.
    """
    body = open(os.path.join(ROOT, "run_axiom_arms.py"), encoding="utf-8").read()
    block = body.split("def _diversify_remaining")[1].split("\ndef ")[0]
    assert '"llm_provider": state.get("llm_provider"' in block
    assert '"model": state.get("model"' in block
    assert '"isomorph_extra_files"' in block, (
        "extracted modules must be written here too")


def test_runner_has_no_undefined_console():
    """``run_axiom_arms`` prints with print(); it has no rich console.

    Both references were introduced by fixes in this file's history, and the
    NameError they raised was swallowed by the pipeline's own except-handler
    into ``notes="pipeline error: NameError: name 'console' is not defined"``
    -- which silently skipped the per-module diversification pass for a whole
    run before anyone noticed.
    """
    body = open(os.path.join(ROOT, "run_axiom_arms.py"), encoding="utf-8").read()
    assert "console." not in body


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_scoring_oracle_reads_modules_the_clone_added(tmp_path):
    """``oracle_verdict`` scores every .py in the clone, not the pristine list.

    A variant that extracts a module imports a file absent from
    ``program.source_files``. Scoring only the listed names dropped it and
    failed the clone on ``ModuleNotFoundError`` for a module sitting in its own
    directory -- measured on a D1 clone whose transport.py was restructured
    into transport.py + modbus_tk_adapter.py.
    """
    import shutil
    import smart_loader.eval.proteus_harness as H

    program = {p.name: p for p in H.discover_programs(CORPUS)}["D1_boiler_control_loop"]
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in os.listdir(program.path):
        src = os.path.join(program.path, name)
        if os.path.isfile(src):
            shutil.copy(src, clone)

    # transport delegates to a module it extracted
    t = (clone / "transport.py")
    t.write_text(t.read_text() + "\nfrom _extracted import helper as _unused\n")
    (clone / "_extracted.py").write_text("def helper():\n    return None\n")

    verdict, detail = H.oracle_verdict(program, str(clone))
    assert verdict == "Y", detail


def test_an_unparseable_reply_is_diagnosed_not_just_labelled(tmp_path):
    """"no block for the module itself" is true and useless.

    Nine consecutive attempts on a D1 run reported exactly that string, which
    does not distinguish a model answering in prose, one naming the file
    differently, and one whose answer was cut off mid-fence -- three problems
    with three different fixes. The reply is now diagnosed and written to disk.
    """
    from smart_loader.isomorph.llm_variant import _diagnose

    assert "prose" in _diagnose("Sure, here you go.", "t")
    assert "cut off" in _diagnose("### FILENAME: t.py\n```python\ndef f():", "t")
    assert "other.py" in _diagnose(
        "### FILENAME: other.py\n```python\nA=1\n```", "t")
    assert "empty" in _diagnose("", "t")


def test_the_raw_reply_is_written_for_inspection(tmp_path):
    class Reject:
        def check(self, *a, **k):
            raise AssertionError("must not reach the oracle")

    out = tmp_path / "failures"
    res = LV.generate(Reject(), "t", "x = 1\n",
                      llm=lambda p: "I cannot do that.",
                      attempts=1, failure_dir=str(out))
    assert res.oracle_passed is False
    written = list(out.glob("*.txt"))
    assert written, "the reply must be on disk, not discarded"
    assert written[0].read_text() == "I cannot do that."


def test_a_header_written_inside_the_fence_is_recovered():
    """Measured on C2: two of three attempts opened the fence first.

    The strict pattern wants `### FILENAME:` above the fence. The model wrote
    it as the first line INSIDE the block, so nothing matched and the run fell
    back to the transformation search at ast_structural 0.07 -- a real variant
    thrown away over formatting.
    """
    reply = ("```python\n### FILENAME: fsm.py\nSTATES = []\n```\n\n"
             "```python\n### FILENAME: state_engine.py\nclass E: pass\n```\n")
    main, extra = LV.parse_reply(reply, "fsm")
    assert "STATES" in main
    assert sorted(extra) == ["state_engine.py"]


def test_the_strict_form_still_wins():
    """A reply in the documented shape must not be re-interpreted."""
    reply = ("### FILENAME: fsm.py\n```python\nCORRECT = 1\n```\n")
    main, extra = LV.parse_reply(reply, "fsm")
    assert "CORRECT" in main and extra == {}


def test_fences_are_never_matched_to_files_by_position():
    """Guessing the order writes valid code into the wrong module."""
    reply = "```python\nA = 1\n```\n\n```python\nB = 2\n```\n"
    main, extra = LV.parse_reply(reply, "fsm")
    assert main == "" and extra == {}
