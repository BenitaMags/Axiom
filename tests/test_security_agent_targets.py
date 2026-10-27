"""The security scanner must query real distributions, and only dependencies.

Two defects, both visible on every B3 and D3 run:

1. It queried the IMPORT ROOT, not the distribution. ``paho.mqtt.client`` was
   looked up as ``paho``, which is not a name on PyPI, so every run printed
   "Security data not_found: PyPI metadata for paho: HTTP" and the package the
   migration was moving AWAY from was never checked for advisories at all.
   ``IMPORT_TO_DISTRIBUTION`` is hand-maintained and has no entry for paho,
   simple_pid or modbus_tk; ``packages_distributions()`` knows all three.

2. It scanned FIRST-PARTY modules. B3 imports ``telemetry`` and
   ``mqtt_config``, which are ``telemetry.py`` and ``mqtt_config.py`` in the
   same directory -- and ``telemetry`` is also a real, unrelated PyPI package.
   The scanner found version 2.0.8, reported "not updated in 2y 6mo" against
   the project's own file, and fed a staleness penalty into AXIOM's scoring.
   The Resolver then hunted for replacements for it.

   The second is worse than noise. Had a first-party module shared a name with
   a genuinely vulnerable package, the report would have asserted a CVE in
   code that has none.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.security_agent import (  # noqa: E402
    _distribution_for_import, _first_party_modules,
)

CORPUS = os.path.expanduser("~/Downloads/evaluation")


@pytest.mark.parametrize("module,distribution", [
    ("paho", "paho-mqtt"),
    ("simple_pid", "simple-pid"),
])
def test_import_root_resolves_to_its_real_distribution(module, distribution):
    import importlib.metadata as md
    if not md.packages_distributions().get(module):
        pytest.skip(f"{module} not installed here")
    assert _distribution_for_import(module) == distribution


def test_a_dotted_import_uses_its_root():
    import importlib.metadata as md
    if not md.packages_distributions().get("paho"):
        pytest.skip("paho-mqtt not installed here")
    assert _distribution_for_import("paho.mqtt.client") == "paho-mqtt"


def test_an_unknown_module_falls_back_to_its_own_name():
    """No metadata and no table entry: the name is the best answer available."""
    assert _distribution_for_import("definitely_not_installed_xyz") == \
        "definitely_not_installed_xyz"


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_sibling_modules_are_recognised_as_first_party():
    modules = _first_party_modules(
        os.path.join(CORPUS, "B3_mqtt_telemetry_bridge", "bridge.py"))
    assert "telemetry" in modules, "a real PyPI package name AND a local file"
    assert "mqtt_config" in modules


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="corpus not present")
def test_test_files_are_not_treated_as_first_party_modules():
    """Excluding test_* keeps the set to things the program actually imports."""
    modules = _first_party_modules(
        os.path.join(CORPUS, "B3_mqtt_telemetry_bridge", "bridge.py"))
    assert not any(m.startswith("test_") for m in modules)


def test_a_missing_source_file_yields_nothing():
    assert _first_party_modules("") == set()
    assert _first_party_modules("/no/such/dir/x.py") == set()


def test_the_agent_subtracts_first_party_from_the_scan_set():
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "smart_loader", "agents", "security_agent.py"),
                  encoding="utf-8").read()
    assert "scanned - first_party" in source
    assert "_first_party_modules(state.get" in source


def test_runtime_metadata_is_consulted_before_the_static_table():
    """The table is hand-maintained and was missing every name that mattered."""
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "smart_loader", "agents", "security_agent.py"),
                  encoding="utf-8").read()
    body = source[source.index("def _distribution_for_import"):]
    body = body[:body.index("def _first_party_modules")]
    assert body.index("packages_distributions") < body.index("IMPORT_TO_DISTRIBUTION")
