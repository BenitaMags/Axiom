"""``--no-aci`` must actually bypass the multi-turn editor.

The ACI edit session is the most expensive component in the pipeline: measured
13k-138k input tokens per attempt against roughly 2k for one-shot wrapper
generation, and it frequently fails its own integrity check and falls back
anyway. ``single_shot_repair`` reaches the same 25/36 as ``proteus_full`` for
$1.25 against $13.57, with no ACI at all -- so whether the editor earns its
tokens is an open question.

It could not be asked before: the one-shot path was reachable only by ACI
failing first. This flag makes it a choice.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONNECTOR = os.path.join(ROOT, "smart_loader", "agents", "connector_agent.py")
GRAPH = os.path.join(ROOT, "smart_loader", "core", "graph.py")
RUNNER = os.path.join(ROOT, "run_axiom_arms.py")


def test_connector_skips_the_editor_when_disabled():
    source = open(CONNECTOR, encoding="utf-8").read()
    assert 'if not state.get("enable_aci", True):' in source
    block = source[source.index('if not state.get("enable_aci", True):'):]
    block = block[:block.index("else:")]
    assert "_run_aci_wrapper_injection" not in block, \
        "the disabled branch must not call the editor"
    assert "aci_ok = [], False" in block, "must route to the one-shot path"


def test_default_is_still_enabled():
    """Omitting the flag must not change existing behaviour."""
    source = open(CONNECTOR, encoding="utf-8").read()
    assert 'state.get("enable_aci", True)' in source, "default must be True"


def test_flag_is_plumbed_end_to_end():
    graph = open(GRAPH, encoding="utf-8").read()
    assert graph.count("enable_aci: bool = True,") == 2, "build_graph AND run_loader"
    assert '"enable_aci":         enable_aci,' in graph, "must reach the state dict"
    runner = open(RUNNER, encoding="utf-8").read()
    assert "enable_aci=not no_aci," in runner
    assert "no_aci=args.no_aci" in runner


def test_cli_exposes_the_flag_exactly_once():
    """It was briefly added twice, which makes argparse raise at startup."""
    runner = open(RUNNER, encoding="utf-8").read()
    assert runner.count('ap.add_argument("--no-aci"') == 1


def test_no_option_string_is_declared_twice():
    """A duplicate option string raises ArgumentError before any run starts.

    ``--no-aci`` was briefly added twice, which made every invocation --
    including ``--help`` -- die in argparse.
    """
    tree = ast.parse(open(RUNNER, encoding="utf-8").read())
    seen = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)):
            seen.append(node.args[0].value)
    dupes = {o for o in seen if seen.count(o) > 1}
    assert not dupes, f"duplicate CLI options: {dupes}"
