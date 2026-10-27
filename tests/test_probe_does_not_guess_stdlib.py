"""A distribution that is not installed must not be reported as available.

``_TIMER_SCRIPT``'s resolver falls back to hyphen segments when metadata does
not answer. ``asyncio-mqtt`` is not installed on this machine at all, but its
first segment resolves to the STDLIB ``asyncio``, so the probe timed that
import and reported the distribution as available and measured. AXIOM selected
it; the Connector had to emit ``import asyncio_mqtt``, which does not exist; it
found that ``aiomqtt`` (the renamed successor) works, and patch integrity forced
it back to the non-existent name. B3 exhausted its repair budget and reverted.

The class is general: any distribution whose first hyphen segment collides with
an installed or stdlib module looks present when it is absent.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_loader.agents.profiler_agent import _probe_package  # noqa: E402


def test_uninstalled_distribution_is_not_available_via_its_first_segment():
    """asyncio-mqtt is absent; 'asyncio' must not stand in for it."""
    import importlib.util
    if importlib.util.find_spec("asyncio_mqtt") is not None:
        pytest.skip("asyncio_mqtt is installed here, so there is nothing to guess")
    assert _probe_package("asyncio-mqtt").available is False


@pytest.mark.parametrize("dist", ["aiomqtt", "paho-mqtt", "modbus-tk", "httpx", "ciso8601"])
def test_installed_distributions_still_resolve(dist):
    """The fix must not make real hyphenated packages look absent.

    paho-mqtt imports as ``paho`` and modbus-tk as ``modbus_tk``; resolving
    those is the whole reason the fallback exists.
    """
    import importlib.util
    import importlib.metadata as md
    try:
        md.distribution(dist)
    except md.PackageNotFoundError:
        pytest.skip(f"{dist} not installed in this environment")
    assert _probe_package(dist).available is True


@pytest.mark.parametrize("mod", ["datetime", "json", "zlib", "asyncio"])
def test_stdlib_modules_probed_directly_still_resolve(mod):
    """A stdlib candidate is resolved by find_spec on the first step.

    Only the hyphen-SEGMENT guess is restricted, not a direct stdlib probe --
    A2 and A3 migrate away from json and datetime, and those must still profile.
    """
    assert _probe_package(mod).available is True
