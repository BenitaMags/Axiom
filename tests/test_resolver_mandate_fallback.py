"""A mandated substitution must not be abandoned when discovery finds nothing.

`run_axiom_arms` seeds `migration_source` and `migration_target`: the swap is a
REQUIREMENT, not something the resolver has to rediscover. But discovery is an
LLM call at temperature 0.7, and it intermittently returned no group for a source
module it had been handed -- `json`, `datetime` and `smtplib` are all standard
library, and the discovery prompt is built around third-party overlap. When that
happened `_after_resolver` routed straight to END: one LLM call, nothing changed,
row scored a failure.

Measured on `proteus_full`: A2 run1, C3 run1 and C3 run2 died that way, while the
same programs on other seeds made 6-11 calls and migrated successfully. A coin
flip decided whether the pipeline attempted the task at all.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smart_loader.core.state import ImportInfo  # noqa: E402


def _imports(module, api_calls=("dumps",)):
    return [ImportInfo(line=1, module=module, alias=None, names=[],
                       api_calls=list(api_calls))]


def test_a_mandated_substitution_produces_a_group():
    from smart_loader.agents.resolver_agent import _mandated_group
    groups = _mandated_group(
        {"migration_source": "json", "migration_target": "ujson",
         "source_code": "import json\n"}, _imports("json"))
    assert len(groups) == 1
    group = groups[0]
    assert group.candidates == ["json", "ujson"]
    assert group.__dict__["original"] == "json"
    assert group.__dict__["original_import"] == "json"
    assert group.used_apis == ["dumps"]


def test_the_target_import_root_is_resolved_not_guessed():
    """`paho-mqtt` imports as `paho`, not `paho_mqtt`."""
    from smart_loader.agents.resolver_agent import _mandated_group
    groups = _mandated_group(
        {"migration_source": "paho-mqtt", "migration_target": "aiomqtt",
         "source_code": "import paho.mqtt.client as mqtt\n"},
        _imports("paho.mqtt.client", api_calls=("Client",)))
    assert groups, "a hyphenated source distribution was not matched"
    assert groups[0].__dict__["original_import"] == "paho"


def test_no_mandate_means_no_group():
    """Free-choice runs must still rely on discovery."""
    from smart_loader.agents.resolver_agent import _mandated_group
    assert _mandated_group({"source_code": "import json\n"},
                           _imports("json")) == []


def test_a_source_without_a_target_means_no_group():
    """Under --free-choice the target is deliberately withheld; synthesising one
    would decide for the model what it was asked to choose."""
    from smart_loader.agents.resolver_agent import _mandated_group
    assert _mandated_group(
        {"migration_source": "json", "migration_target": "",
         "source_code": "import json\n"}, _imports("json")) == []


def test_a_mandate_for_an_unimported_module_is_declined():
    """Otherwise the rest of the pipeline hunts for a call site that is absent."""
    from smart_loader.agents.resolver_agent import _mandated_group
    assert _mandated_group(
        {"migration_source": "requests", "migration_target": "httpx",
         "source_code": "import json\n"}, _imports("json")) == []


def test_the_fallback_only_runs_when_discovery_found_nothing():
    """It is a fallback, not an override: a discovered group must win.

    Discovery sees the file and can find a better-scoped group than a synthesised
    one; replacing it would throw that away.
    """
    source = open(os.path.join(ROOT, "smart_loader/agents/resolver_agent.py"),
                  encoding="utf-8").read()
    assert "if not groups:" in source
    index = source.index("if not groups:")
    assert "_mandated_group(state, imports)" in source[index:index + 400]


def test_the_graph_no_longer_ends_on_an_empty_resolver_by_accident():
    """`_after_resolver` still ends on an empty result -- correctly, because with
    the fallback in place an empty result now means there is genuinely nothing to
    do. This pins the pairing so the fallback cannot be removed while leaving the
    early exit behind."""
    graph_source = open(os.path.join(ROOT, "smart_loader/core/graph.py"),
                        encoding="utf-8").read()
    assert 'if not state.get("equivalence_groups"):' in graph_source
    resolver_source = open(
        os.path.join(ROOT, "smart_loader/agents/resolver_agent.py"),
        encoding="utf-8").read()
    assert "_mandated_group" in resolver_source, (
        "the early exit is still there but the fallback that makes it safe is "
        "gone; a mandated substitution would be abandoned again")


def test_a_malformed_discovery_element_does_not_abort_the_run():
    """The model intermittently returns strings where objects were asked for.

    `g.get("candidates", [])` then raises `AttributeError: 'str' object has no
    attribute 'get'`, which `run_axiom_arms` records as "pipeline error" and
    scores as a no-op with one LLM call. Measured on A1 run3: same program, same
    prompt, the other two seeds migrated cleanly.

    Skipping the bad element leaves the group list empty in the worst case, and
    the mandate fallback then supplies the substitution the caller specified --
    so a shape surprise costs a group, not the run.
    """
    source = open(os.path.join(ROOT, "smart_loader/agents/resolver_agent.py"),
                  encoding="utf-8").read()
    loop = source.index("for g in groups_data:")
    # Anchored on the real statement, not a substring that also appears in the
    # explanatory comment above it.
    first_use = source.index("candidates = [c for c in g.get(", loop)
    body = source[loop:first_use]
    assert "if not isinstance(g, dict):" in body, (
        "the loop reaches .get() on the element without checking its type")
    assert "continue" in body


def test_the_guard_and_the_fallback_cover_the_same_failure():
    """Both defend one thing: an LLM shape surprise must not throw away a run
    whose answer the caller already supplied."""
    source = open(os.path.join(ROOT, "smart_loader/agents/resolver_agent.py"),
                  encoding="utf-8").read()
    assert source.index("if not isinstance(g, dict):") < source.index(
        "groups = _mandated_group(state, imports)")
