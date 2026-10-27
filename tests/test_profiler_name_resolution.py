"""The host profiler must import a distribution by its MODULE name.

It ran ``importlib.import_module(pkg)`` on the raw distribution name. A
hyphenated distribution can never be a module name, so `modbus-tk`,
`paho-mqtt` and `simple-pid` raised ModuleNotFoundError however correctly they
were installed, and were recorded unavailable on every run. Their speed and
memory evidence was then omitted from AXIOM's scoring, leaving those candidates
judged on security and API-match alone.

The isolated profiling path already resolved this via
``importlib.metadata.packages_distributions()``; only the host path did not.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.profiler_agent import _TIMER_SCRIPT


def _probe(name: str) -> str:
    proc = subprocess.run([sys.executable, "-c", _TIMER_SCRIPT, name, "1"],
                          capture_output=True, text=True, timeout=120)
    return (proc.stdout or proc.stderr).strip()


def test_a_plain_module_name_still_works():
    assert _probe("json").startswith("OK:")


def test_a_hyphenated_distribution_resolves_to_its_module():
    """`modbus-tk` imports as `modbus_tk` — the underscore form."""
    if not _installed("modbus_tk"):
        return
    assert _probe("modbus-tk").startswith("OK:"), (
        "an installed hyphenated distribution was reported unavailable")


def test_a_distribution_whose_root_is_the_first_segment_resolves():
    """`paho-mqtt` imports as `paho` — NOT `paho_mqtt`."""
    if not _installed("paho"):
        return
    assert _probe("paho-mqtt").startswith("OK:")


def test_a_genuinely_absent_package_is_still_reported_absent():
    """The fix must resolve names, never invent availability."""
    assert _probe("definitely-not-a-real-package-xyz").startswith("ERROR:")


def _installed(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
